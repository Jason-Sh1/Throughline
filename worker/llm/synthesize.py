"""Synthesis: Claude Haiku writes the story card, through the Message Batches API.

Flow: `build_job` prepares one request per cluster -> `submit` sends them as one
batch and records it in `llm_batches` -> `collect` (same run or a later one)
polls the batch, validates each result, runs the citation filter and stores the
card. Batches are half price and an edition isn't latency-sensitive.

The system prompt is identical for every request and marked for prompt caching,
so after the first request in a batch it's billed at the cache-read rate. Card
JSON is enforced with structured outputs (`output_config.format`); Pydantic
validates it again on our side.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass

import psycopg
from pydantic import ValidationError

from llm import prompts
from llm.card import assemble_card
from llm.citations import CardRejected, filter_card
from llm.cost import cost_usd
from llm.inputs import (
    SourceDoc,
    build_sources,
    content_hash,
    docs_to_json,
    load_cluster_rows,
    render_sources,
)
from llm.schemas import CardDraft, Extraction, clamp_lengths

DEFAULT_MODEL = "claude-haiku-5-5"
# Haiku 5.5 thinks adaptively by default; effort sets how much. `low` keeps
# thinking tokens (billed as output) down. Change only with an eval run.
DEFAULT_EFFORT = "low"
MAX_TOKENS = 12000  # thinking + card JSON; a card is ~2-3K tokens


def synth_model() -> str:
    return os.environ.get("SYNTH_MODEL", DEFAULT_MODEL)


def synth_effort() -> str:
    return os.environ.get("SYNTH_EFFORT", DEFAULT_EFFORT)


@dataclass
class CardJob:
    """One cluster to synthesize; everything needed to store the result later."""

    cluster_id: int
    content_hash: str
    extraction_id: int
    docs: list[SourceDoc]
    user_message: str

    @property
    def custom_id(self) -> str:
        return f"cluster-{self.cluster_id}"

    def meta(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "content_hash": self.content_hash,
            "extraction_id": self.extraction_id,
            "docs": docs_to_json(self.docs),
            "user_message": self.user_message,  # for the one retry, same inputs
        }


def render_claims(ex: Extraction) -> str:
    claims = [c.model_dump(mode="json") for c in ex.claims]
    dates = [d.model_dump(mode="json") for d in ex.dates]
    return json.dumps({"claims": claims, "dates": dates}, ensure_ascii=False)


def build_job(
    conn: psycopg.Connection, cluster_id: int, extraction_id: int, model: str
) -> CardJob | None:
    """None when a card for exactly these inputs already exists (cache hit)."""
    docs = build_sources(load_cluster_rows(conn, cluster_id))
    _, version = prompts.load(prompts.SYNTHESIZE)
    with conn.cursor() as cur:
        cur.execute("select data, content_hash from extractions where id = %s", (extraction_id,))
        data, ex_hash = cur.fetchone()
        chash = content_hash(docs, version, model, ex_hash)
        cur.execute("select 1 from cards where content_hash = %s", (chash,))
        if cur.fetchone():
            return None
    ex = Extraction.model_validate(data)
    published = [d.published_at for d in docs if d.published_at]
    today = max(published).strftime("%Y-%m-%d") if published else "unknown"
    user = (
        f"<sources>\n{render_sources(docs)}\n</sources>\n\n"
        f"<claims>\n{render_claims(ex)}\n</claims>\n\n"
        f"The newest source was published on {today}. Write the briefing JSON."
    )
    return CardJob(cluster_id, chash, extraction_id, docs, user)


def request_params(job_user_message: str, model: str, effort: str) -> dict:
    import anthropic

    system, _ = prompts.load(prompts.SYNTHESIZE)
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": job_user_message}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(CardDraft)},
        },
    }


def estimate_cost(jobs: list[CardJob], model: str) -> float:
    """Rough pre-submit estimate (no API call): ~3.5 characters per token for
    input, ~3K output tokens per card including thinking."""
    system, _ = prompts.load(prompts.SYNTHESIZE)
    tin = sum((len(system) + len(j.user_message)) / 3.5 + 1500 for j in jobs)  # +schema
    return cost_usd(model, "batch", int(tin), 3000 * len(jobs))


def submit(
    conn: psycopg.Connection, client, jobs: list[CardJob], model: str, effort: str,
    edition_id: int | None = None,
) -> int:  # fmt: skip
    """Submit one batch; returns the llm_batches row id."""
    batch = client.messages.batches.create(
        requests=[
            {"custom_id": j.custom_id, "params": request_params(j.user_message, model, effort)}
            for j in jobs
        ]
    )
    meta = {j.custom_id: j.meta() | {"model": model} for j in jobs}
    with conn.cursor() as cur:
        cur.execute(
            """insert into llm_batches
                   (provider_batch_id, kind, edition_id, requests, request_count)
               values (%s, 'synthesize', %s, %s, %s) returning id""",
            (batch.id, edition_id, json.dumps(meta), len(jobs)),
        )
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def wait_for(client, provider_batch_id: str, timeout_s: int, poll_s: int = 30) -> bool:
    """Poll until the batch ends. False on timeout (a later run can collect it)."""
    deadline = time.monotonic() + timeout_s
    while True:
        b = client.messages.batches.retrieve(provider_batch_id)
        if b.processing_status == "ended":
            return True
        if time.monotonic() > deadline:
            return False
        print(f"  batch {provider_batch_id}: {b.request_counts.processing} processing")
        time.sleep(poll_s)


def parse_draft(message) -> CardDraft:
    """Raises ValueError for anything that isn't a valid card."""
    if message.stop_reason == "refusal":
        raise ValueError("model refused")
    if message.stop_reason == "max_tokens":
        raise ValueError("hit max_tokens")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return CardDraft.model_validate_json(text)
    except ValidationError as e:
        raise ValueError(f"invalid card JSON: {e}") from e


def usage_cost(message, model: str, mode: str) -> tuple[int, int, float]:
    u = message.usage
    cr = u.cache_read_input_tokens or 0
    cw = u.cache_creation_input_tokens or 0
    tin = u.input_tokens + cr + cw
    return tin, u.output_tokens, cost_usd(model, mode, u.input_tokens, u.output_tokens, cr, cw)


def store_card(
    conn: psycopg.Connection, meta: dict, draft: CardDraft, model: str,
    tokens_in: int, tokens_out: int, cost: float,
) -> tuple[int, Counter]:  # fmt: skip
    docs = meta["docs"]
    draft, stats = filter_card(clamp_lengths(draft), {d["local_id"] for d in docs})
    card = assemble_card(conn, meta["cluster_id"], draft, docs)
    _, version = prompts.load(prompts.SYNTHESIZE)
    with conn.cursor() as cur:
        cur.execute(
            """insert into cards (cluster_id, content_hash, prompt_version, model, status, card,
                   source_count, input_tokens, output_tokens, cost_usd)
               values (%s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s)
               on conflict (content_hash) do update set card = excluded.card
               returning id""",
            (meta["cluster_id"], meta["content_hash"], version, model, card.model_dump_json(),
             card.confidence.outlet_count, tokens_in, tokens_out, cost),
        )  # fmt: skip
        card_id = cur.fetchone()[0]
    conn.commit()
    return card_id, stats


def retry_direct(client, meta: dict, model: str, effort: str):
    """The one retry for an invalid card, as a normal (non-batch) request with
    exactly the same inputs, so the source ids still match `meta["docs"]`."""
    return client.messages.create(**request_params(meta["user_message"], model, effort))


def collect(conn: psycopg.Connection, client, batch_row_id: int, effort: str) -> dict:
    """Ingest a finished batch. Returns stats incl. tokens and cost."""
    with conn.cursor() as cur:
        cur.execute(
            "select provider_batch_id, requests from llm_batches where id = %s", (batch_row_id,)
        )
        provider_id, requests = cur.fetchone()
    stats: Counter = Counter()
    totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    card_ids: dict[int, int] = {}
    for res in client.messages.batches.results(provider_id):
        meta = requests.get(res.custom_id)
        if meta is None:
            continue
        model = meta["model"]
        if res.result.type != "succeeded":
            stats[f"batch_{res.result.type}"] += 1
            continue
        msg, mode = res.result.message, "batch"
        for attempt in range(2):
            tin, tout, cost = usage_cost(msg, model, mode)
            totals["input_tokens"] += tin
            totals["output_tokens"] += tout
            totals["cost_usd"] += cost
            try:
                draft = parse_draft(msg)
                card_id, fstats = store_card(conn, meta, draft, model, tin, tout, cost)
                card_ids[meta["cluster_id"]] = card_id
                stats["cards"] += 1
                stats.update({f"citations_{k}": v for k, v in fstats.items()})
                break
            except (ValueError, CardRejected) as e:
                stats["invalid"] += 1
                print(f"  {res.custom_id}: {e}")
                if attempt == 0 and isinstance(e, ValueError) and "refused" not in str(e):
                    msg, mode = retry_direct(client, meta, model, effort), "standard"
                else:
                    stats["failed"] += 1
                    break
    with conn.cursor() as cur:
        cur.execute(
            """update llm_batches set status = 'ingested', ended_at = coalesce(ended_at, now()),
                   ingested_at = now() where id = %s""",
            (batch_row_id,),
        )
    conn.commit()
    return dict(stats) | totals | {"card_ids": card_ids}


def anthropic_client():
    import anthropic

    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
