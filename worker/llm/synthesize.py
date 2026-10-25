"""Synthesis: Claude Haiku writes the story card, through the Message Batches API.

Flow: `build_job` prepares one request per cluster (or finds a cached card) ->
`submit` sends them as one batch -> `collect` validates each result, runs the
citation filter and stores the card.

The system prompt is identical for every request and marked for prompt caching.
Card JSON is enforced with structured outputs; Pydantic validates it again here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import anthropic
import psycopg
from pydantic import ValidationError

from llm import batch, prompts
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
        return f"card-{self.cluster_id}"

    def meta(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "content_hash": self.content_hash,
            "extraction_id": self.extraction_id,
            "docs": docs_to_json(self.docs),
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


def request_params(user_message: str, model: str, effort: str) -> dict:
    system, _ = prompts.load(prompts.SYNTHESIZE)
    schema = anthropic.transform_schema(CardDraft)
    return batch.params(system, user_message, schema, model, effort, MAX_TOKENS)


def estimate_cost(jobs: list[CardJob], model: str) -> float:
    """Rough pre-submit estimate (no API call): ~3.5 characters per token for
    input plus ~1.5K for the schema, ~3K output tokens per card incl. thinking."""
    system, _ = prompts.load(prompts.SYNTHESIZE)
    tin = sum((len(system) + len(j.user_message)) / 3.5 + 1500 for j in jobs)
    return cost_usd(model, "batch", int(tin), 3000 * len(jobs))


def submit(conn, client, jobs: list[CardJob], model: str, effort: str, edition_id=None):
    requests = {j.custom_id: (request_params(j.user_message, model, effort), j.meta())
                for j in jobs}  # fmt: skip
    return batch.submit(conn, client, "synthesize", requests, edition_id)


def parse_draft(text: str) -> CardDraft:
    try:
        return CardDraft.model_validate_json(text)
    except ValidationError as e:
        raise ValueError(f"invalid card JSON: {e}") from e


def store_card(
    conn: psycopg.Connection, meta: dict, draft: CardDraft, tokens_in: int, tokens_out: int,
    cost: float,
) -> int:  # fmt: skip
    docs = meta["docs"]
    try:
        draft, _ = filter_card(clamp_lengths(draft), {d["local_id"] for d in docs})
    except CardRejected as e:
        raise ValueError(f"card rejected: {e}") from e
    card = assemble_card(conn, meta["cluster_id"], draft, docs)
    _, version = prompts.load(prompts.SYNTHESIZE)
    with conn.cursor() as cur:
        cur.execute(
            """insert into cards (cluster_id, content_hash, prompt_version, model, status, card,
                   source_count, input_tokens, output_tokens, cost_usd)
               values (%s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s)
               on conflict (content_hash) do update set card = excluded.card
               returning id""",
            (meta["cluster_id"], meta["content_hash"], version, meta["params"]["model"],
             card.model_dump_json(), card.confidence.outlet_count, tokens_in, tokens_out, cost),
        )  # fmt: skip
        card_id = cur.fetchone()[0]
    conn.commit()
    return card_id


def collect(conn: psycopg.Connection, client, batch_row_id: int) -> dict:
    def store(meta, draft, tin, tout, cost):
        return store_card(conn, meta, draft, tin, tout, cost)

    return batch.collect(conn, client, batch_row_id, parse_draft, store)
