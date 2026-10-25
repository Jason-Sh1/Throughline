"""Extraction: one LLM call per cluster -> entities + cited claims.

Two backends, picked by EXTRACT_MODEL:
- `claude-*` (default): requests go through the Message Batches API like
  synthesis, in their own batch that finishes before the synthesis batch.
- `gemini-*`: synchronous calls. Kept because CLAUDE.md first named Gemini, but
  the free tier allows only 20 requests a day, too few for an edition.

Results are cached in `extractions` by content hash, so a cluster that hasn't
changed since the last edition costs nothing.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import anthropic
import psycopg
from pydantic import ValidationError

from llm import batch, prompts
from llm.citations import filter_extraction
from llm.cost import cost_usd
from llm.inputs import (
    SourceDoc,
    build_sources,
    content_hash,
    load_cluster_rows,
    render_sources,
    source_map,
)
from llm.schemas import Extraction

DEFAULT_MODEL = "claude-haiku-5-5"
EFFORT = "low"
MAX_TOKENS = 8000


def extract_model() -> str:
    return os.environ.get("EXTRACT_MODEL", DEFAULT_MODEL)


class ExtractionFailed(Exception):
    pass


@dataclass
class ExtractJob:
    cluster_id: int
    content_hash: str
    docs: list[SourceDoc]
    user_message: str

    @property
    def custom_id(self) -> str:
        return f"extract-{self.cluster_id}"

    def meta(self) -> dict:
        return {"cluster_id": self.cluster_id, "content_hash": self.content_hash,
                "source_map": source_map(self.docs)}  # fmt: skip


def plan(conn: psycopg.Connection, cluster_id: int, model: str) -> int | ExtractJob | None:
    """The cached extraction id, a job to run, or None if no usable sources."""
    docs = build_sources(load_cluster_rows(conn, cluster_id))
    if not docs:
        return None
    _, version = prompts.load(prompts.EXTRACT)
    chash = content_hash(docs, version, model)
    with conn.cursor() as cur:
        cur.execute("select id from extractions where content_hash = %s", (chash,))
        if row := cur.fetchone():
            return row[0]
    return ExtractJob(cluster_id, chash, docs, f"<sources>\n{render_sources(docs)}\n</sources>")


def parse_extraction(text: str) -> Extraction:
    try:
        return Extraction.model_validate_json(text)
    except ValidationError as e:
        raise ValueError(f"invalid extraction JSON: {e}") from e


def store_extraction(
    conn: psycopg.Connection, meta: dict, ex: Extraction, model: str, tin: int, tout: int,
    cost: float,
) -> int:  # fmt: skip
    ex, stats = filter_extraction(ex, set(meta["source_map"]))
    _, version = prompts.load(prompts.EXTRACT)
    with conn.cursor() as cur:
        cur.execute(
            """insert into extractions (cluster_id, content_hash, prompt_version, model, data,
                   source_map, input_tokens, output_tokens, cost_usd)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (content_hash) do update set data = excluded.data
               returning id""",
            (meta["cluster_id"], meta["content_hash"], version, model, ex.model_dump_json(),
             json.dumps(meta["source_map"]), tin, tout, cost),
        )  # fmt: skip
        ex_id = cur.fetchone()[0]
    conn.commit()
    if stats["dropped"]:
        print(f"  cluster {meta['cluster_id']}: citation filter dropped {stats['dropped']} items")
    return ex_id


# --- Claude (batch) -------------------------------------------------------------


def request_params(user_message: str, model: str) -> dict:
    system, _ = prompts.load(prompts.EXTRACT)
    schema = anthropic.transform_schema(Extraction)
    return batch.params(system, user_message, schema, model, EFFORT, MAX_TOKENS)


def submit(conn, client, jobs: list[ExtractJob], model: str, edition_id=None):
    requests = {j.custom_id: (request_params(j.user_message, model), j.meta()) for j in jobs}
    return batch.submit(conn, client, "extract", requests, edition_id)


def collect(conn: psycopg.Connection, client, batch_row_id: int) -> dict:
    def store(meta, ex, tin, tout, cost):
        return store_extraction(conn, meta, ex, meta["params"]["model"], tin, tout, cost)

    return batch.collect(conn, client, batch_row_id, parse_extraction, store)


def estimate_cost(jobs: list[ExtractJob], model: str) -> float:
    """~3.5 characters per token plus ~1K for the schema; ~2.5K output tokens."""
    system, _ = prompts.load(prompts.EXTRACT)
    tin = sum((len(system) + len(j.user_message)) / 3.5 + 1000 for j in jobs)
    return cost_usd(model, "batch", int(tin), 2500 * len(jobs))


# --- Gemini (synchronous) ---------------------------------------------------------


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int


# (system prompt, user message) -> raw JSON text + usage. Tests pass a fake.
CallFn = Callable[[str, str], LLMResult]


def gemini_caller(model: str) -> CallFn:
    from google import genai
    from google.genai import errors, types

    client = genai.Client()  # reads GEMINI_API_KEY

    def call(system: str, user: str) -> LLMResult:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=Extraction.model_json_schema(),
            temperature=0.2,
        )
        for attempt in range(4):
            try:
                r = client.models.generate_content(model=model, contents=user, config=config)
                break
            except errors.ServerError:  # 503 "high demand" is common; back off
                if attempt == 3:
                    raise
                time.sleep(5 * 2**attempt)
        u = r.usage_metadata
        out = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        return LLMResult(r.text or "", u.prompt_token_count or 0, out)

    return call


def extract_sync(
    conn: psycopg.Connection, cluster_id: int, call: CallFn, model: str
) -> tuple[int | None, dict]:
    """Returns (extraction id, spend); spend is zero on a cache hit. Strict
    validation with one retry on invalid JSON, then ExtractionFailed."""
    spend = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    job = plan(conn, cluster_id, model)
    if not isinstance(job, ExtractJob):
        return job, spend
    system, _ = prompts.load(prompts.EXTRACT)
    for _ in range(2):
        res = call(system, job.user_message)
        spend["input_tokens"] += res.input_tokens
        spend["output_tokens"] += res.output_tokens
        try:
            ex = parse_extraction(res.text)
            break
        except ValueError as e:
            err = e
    else:
        raise ExtractionFailed(f"after retry: {err}")
    spend["cost_usd"] = cost_usd(model, "standard", spend["input_tokens"], spend["output_tokens"])
    ex_id = store_extraction(conn, job.meta(), ex, model, spend["input_tokens"],
                             spend["output_tokens"], spend["cost_usd"])  # fmt: skip
    return ex_id, spend
