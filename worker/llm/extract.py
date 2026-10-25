"""Extraction: one Gemini Flash-Lite call per cluster -> entities + cited claims.

Synchronous calls rather than a batch: extraction is cheap, the free tier has no
batch mode for this model, and the synthesis batch needs the claims first.
Results are cached in `extractions` by content hash, so a cluster that hasn't
changed since the last edition costs nothing.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from pydantic import ValidationError

from llm import prompts
from llm.citations import filter_extraction
from llm.cost import cost_usd
from llm.inputs import build_sources, content_hash, load_cluster_rows, render_sources, source_map
from llm.schemas import Extraction

DEFAULT_MODEL = "gemini-2.5-flash-lite"


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int


# (system prompt, user message) -> raw JSON text + usage. Tests pass a fake.
CallFn = Callable[[str, str], LLMResult]


class ExtractionFailed(Exception):
    pass


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


def parse_with_retry(call: CallFn, system: str, user: str) -> tuple[Extraction, int, int]:
    """Strict validation; one retry on invalid JSON, then fail (CLAUDE.md)."""
    tokens_in = tokens_out = 0
    for _ in range(2):
        res = call(system, user)
        tokens_in += res.input_tokens
        tokens_out += res.output_tokens
        try:
            return Extraction.model_validate_json(res.text), tokens_in, tokens_out
        except ValidationError as e:
            err = e
    raise ExtractionFailed(f"invalid extraction JSON after retry: {err}")


def extract_cluster(
    conn: psycopg.Connection, cluster_id: int, call: CallFn, model: str
) -> tuple[int | None, dict]:
    """Returns (extraction id, spend). The id is None if the cluster has no usable
    sources; spend is zero on a cache hit."""
    spend = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    docs = build_sources(load_cluster_rows(conn, cluster_id))
    if not docs:
        return None, spend
    system, version = prompts.load(prompts.EXTRACT)
    chash = content_hash(docs, version, model)
    with conn.cursor() as cur:
        cur.execute("select id from extractions where content_hash = %s", (chash,))
        if row := cur.fetchone():
            return row[0], spend
    ex, tin, tout = parse_with_retry(call, system, f"<sources>\n{render_sources(docs)}\n</sources>")
    ex, stats = filter_extraction(ex, set(source_map(docs)))
    spend = {"input_tokens": tin, "output_tokens": tout,
             "cost_usd": cost_usd(model, "standard", tin, tout)}  # fmt: skip
    with conn.cursor() as cur:
        cur.execute(
            """insert into extractions (cluster_id, content_hash, prompt_version, model, data,
                   source_map, input_tokens, output_tokens, cost_usd)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (cluster_id, chash, version, model, ex.model_dump_json(),
             json.dumps(source_map(docs)), tin, tout, spend["cost_usd"]),
        )  # fmt: skip
        ex_id = cur.fetchone()[0]
    conn.commit()
    if stats["dropped"]:
        print(f"  cluster {cluster_id}: citation filter dropped {stats['dropped']} items")
    return ex_id, spend


def extract_model() -> str:
    return os.environ.get("EXTRACT_MODEL", DEFAULT_MODEL)
