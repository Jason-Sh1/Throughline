"""Verification pass (CLAUDE.md rule 2): after synthesis, every sentence is checked
against the excerpts it cites, and anything not supported is removed.

Flow per card: `items()` numbers every cited sentence ("V1", "V2", ...) -> one
request per card asks the model for a verdict on each (supported / unsupported /
contradicted) -> `apply()` drops everything not `supported`. A card whose
headline (or one-liner) fails, or that loses all its brief bullets, is marked
`failed` and the edition falls back to the story's one-liner, or leaves it out.

We drop rather than rewrite: a rewrite would be new, unchecked text. The prompt
tells the model to choose `unsupported` when unsure, and a sentence missing
from the response counts as unsupported.

The core functions work on a card as a dict, so the same code verifies stored
cards (article ids, through the edition's batch) and eval cards (local ids).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

import anthropic
import psycopg
from pydantic import ValidationError

from llm import batch, prompts
from llm.cost import cost_usd
from llm.inputs import SourceDoc, docs_for_articles, render_sources
from llm.schemas import Strict

DEFAULT_MODEL = "claude-haiku-5-5"
# Verification is the accuracy step, so one notch above extraction/synthesis.
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 8000
MIN_TIMELINE_FOR_VIZ = 3  # same rule as citations.settle_viz_hint


def verify_model() -> str:
    return os.environ.get("VERIFY_MODEL", DEFAULT_MODEL)


def verify_effort() -> str:
    return os.environ.get("VERIFY_EFFORT", DEFAULT_EFFORT)


class Verdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class SentenceVerdict(Strict):
    id: str
    verdict: Verdict
    reason: str


class VerifyResult(Strict):
    verdicts: list[SentenceVerdict]


# --- Walking a card ----------------------------------------------------------------

LIST_FIELDS = ("brief", "why_it_matters", "key_facts", "background", "whats_next")
FRAMING_BUCKETS = ("widely_reported", "reported_by_some", "analysis_opinion")
# Losing one of these rejects the card.
REQUIRED = {"headline", "one_liner"}


@dataclass
class Item:
    key: str  # "V3"
    field: str  # "brief", "framing.reported_by_some", ...
    text: str  # what the verifier reads
    ref: dict  # the item inside the card, so apply() can remove it


def _walk(card: dict) -> list[tuple[str, dict, str]]:
    """(field, item, text to verify) for every cited item, in a fixed order."""
    out: list[tuple[str, dict, str]] = []
    for single in ("headline", "one_liner"):
        if card.get(single):
            out.append((single, card[single], card[single]["text"]))
    for f in LIST_FIELDS:
        out += [(f, it, it["text"]) for it in card.get(f, [])]
    out += [("glossary", it, f"{it['term']}: {it['definition']}")
            for it in card.get("glossary", [])]  # fmt: skip
    out += [("timeline", it, f"{it['date']}: {it['event']}") for it in card.get("timeline", [])]
    framing = card.get("framing") or {}
    for b in FRAMING_BUCKETS:
        out += [(f"framing.{b}", it, it["text"]) for it in framing.get(b, [])]
    viz = card.get("viz_data") or {}
    out += [("viz_data.places", it, f"Place involved in this story: {it['name']}")
            for it in viz.get("places", [])]  # fmt: skip
    chart = viz.get("chart")
    if chart:
        out += [("viz_data.chart", it, f"{chart['title']}: {it['label']}: {it['value']} "
                 f"{chart['unit']}") for it in chart["points"]]  # fmt: skip
    return out


def items(card: dict) -> list[Item]:
    return [Item(f"V{i}", f, text, ref) for i, (f, ref, text) in enumerate(_walk(card), 1)]


def render_sentences(its: list[Item], to_local: dict) -> str:
    """to_local maps a cited id as stored in the card (article id or "S3") to the
    local id the model sees in <sources>."""
    lines = []
    for it in its:
        cites = ", ".join(to_local[s] for s in it.ref["source_ids"] if s in to_local)
        lines.append(f"[{it.key}] cites: {cites or 'none'}\n{it.text}")
    return "\n\n".join(lines)


def user_message(docs: list[SourceDoc], its: list[Item], to_local: dict) -> str:
    return (
        f"<sources>\n{render_sources(docs)}\n</sources>\n\n"
        f"<sentences>\n{render_sentences(its, to_local)}\n</sentences>\n\n"
        "Return a verdict for every sentence."
    )


class CardFailed(Exception):
    def __init__(self, msg: str, report: dict):
        super().__init__(msg)
        self.report = report | {"error": msg}


def review(its: list[Item], result: VerifyResult) -> tuple[set[int], dict]:
    """Which items to drop (by identity), and a report of the verdicts."""
    verdicts = {v.id: v for v in result.verdicts}
    drop_ids, dropped, counts = set(), [], Counter()
    for it in its:
        v = verdicts.get(it.key)
        verdict = v.verdict.value if v else "missing"
        counts[verdict] += 1
        if verdict != Verdict.SUPPORTED:
            drop_ids.add(id(it.ref))
            dropped.append({"field": it.field, "text": it.text, "verdict": verdict,
                            "reason": v.reason if v else "no verdict returned"})  # fmt: skip
    return drop_ids, {"stats": dict(counts), "total": len(its), "dropped": dropped}


def apply(card: dict, its: list[Item], result: VerifyResult) -> tuple[dict, dict]:
    """Remove every item not judged `supported`, in place. Returns (card, report).
    Raises CardFailed (carrying the report) when the card can't stand without
    what was removed."""
    drop_ids, report = review(its, result)
    failed = [it.field for it in its if it.field in REQUIRED and id(it.ref) in drop_ids]
    if failed:
        raise CardFailed(f"{failed[0]} not supported", report)
    keep = lambda xs: [x for x in xs if id(x) not in drop_ids]  # noqa: E731
    for f in (*LIST_FIELDS, "glossary", "timeline"):
        if f in card:
            card[f] = keep(card[f])
    for b in FRAMING_BUCKETS:
        if b in (card.get("framing") or {}):
            card["framing"][b] = keep(card["framing"][b])
    viz = card.get("viz_data") or {}
    if "places" in viz:
        viz["places"] = keep(viz["places"])
    if viz.get("chart"):
        viz["chart"]["points"] = keep(viz["chart"]["points"])
        if len(viz["chart"]["points"]) < 2:
            viz["chart"] = None
    if "viz_hint" in card:
        card["viz_hint"] = settle_viz_hint(card)
    if "brief" in card and not card["brief"]:
        raise CardFailed("no brief bullet survived verification", report)
    card["verified"] = True
    return card, report


def settle_viz_hint(card: dict) -> str:
    hint, viz = card["viz_hint"], card.get("viz_data") or {}
    if hint == "timeline" and len(card.get("timeline", [])) < MIN_TIMELINE_FOR_VIZ:
        return "none"
    if hint == "map" and not viz.get("places"):
        return "none"
    if hint == "chart" and not viz.get("chart"):
        return "none"
    return hint


def parse_result(text: str, expected: int | None = None) -> VerifyResult:
    """ValueError (which triggers the one retry) on invalid JSON or when fewer
    than half the sentences got a verdict: that's a broken response, not a
    strict verifier."""
    try:
        res = VerifyResult.model_validate_json(text)
    except ValidationError as e:
        raise ValueError(f"invalid verification JSON: {e}") from e
    if expected and len({v.id for v in res.verdicts}) * 2 < expected:
        raise ValueError(f"only {len(res.verdicts)} verdicts for {expected} sentences")
    return res


def request_params(user: str, model: str, effort: str) -> dict:
    system, _ = prompts.load(prompts.VERIFY)
    schema = anthropic.transform_schema(VerifyResult)
    return batch.params(system, user, schema, model, effort, MAX_TOKENS)


# --- Stored cards, through the Batches API --------------------------------------------


@dataclass
class VerifyJob:
    card_id: int
    n_items: int
    user_message: str
    sentences_sha: str  # fingerprint of exactly what was graded (see store)

    @property
    def custom_id(self) -> str:
        return f"verify-{self.card_id}"


def _load(conn: psycopg.Connection, card_id: int) -> tuple[dict, list[SourceDoc], dict]:
    with conn.cursor() as cur:
        cur.execute("select card from cards where id = %s", (card_id,))
        card = cur.fetchone()[0]
    article_ids = [s["article_id"] for s in card["sources"]]
    docs = docs_for_articles(conn, article_ids)
    to_local = {d.article_id: d.local_id for d in docs}
    return card, docs, to_local


def cited_docs(docs: list[SourceDoc], its: list[Item]) -> list[SourceDoc]:
    """Only the sources some sentence cites, with their ids unchanged. The verifier
    never sees an article no sentence relies on, so text in such an article cannot
    influence any verdict (and the request is smaller)."""
    cited = {s for it in its for s in it.ref["source_ids"]}
    # Stored cards cite article ids; eval cards cite the local ids ("S3").
    return [d for d in docs if d.article_id in cited or d.local_id in cited]


def sentences_sha(its: list[Item], to_local: dict) -> str:
    """Fingerprint of the numbered sentences as the verifier reads them. Verdicts
    come back as "V3: unsupported"; they only mean something for this exact list."""
    return hashlib.sha256(render_sentences(its, to_local).encode()).hexdigest()


def plan(conn: psycopg.Connection, card_id: int) -> VerifyJob | None:
    card, docs, to_local = _load(conn, card_id)
    its = items(card)
    if not its or not docs:
        return None
    message = user_message(cited_docs(docs, its), its, to_local)
    return VerifyJob(card_id, len(its), message, sentences_sha(its, to_local))


def submit(conn, client, jobs: list[VerifyJob], model: str, effort: str, edition_id=None):
    requests = {
        j.custom_id: (request_params(j.user_message, model, effort),
                      {"card_id": j.card_id, "n_items": j.n_items,
                       "sentences_sha": j.sentences_sha})
        for j in jobs
    }  # fmt: skip
    return batch.submit(conn, client, "verify", requests, edition_id)


def store(conn: psycopg.Connection, meta: dict, result: VerifyResult, tin: int, tout: int,
          cost: float) -> str:  # fmt: skip
    """Apply the verdicts to the stored card. Returns the new status, or "stale"
    when the verdicts do not belong to the card as it is now.

    Verdicts are applied at most once, and only to the text they were given for:
    - the card must still be `draft` (a second set of results for a card that is
      already verified, failed or published is ignored), and
    - the numbered sentences must be the ones the request graded (same fingerprint).
    Without these checks, results collected twice would delete the wrong sentences
    from a card that had already been cut, or pull a published card back.
    """
    card, _, to_local = _load(conn, meta["card_id"])
    its = items(card)
    expected = meta.get("sentences_sha")  # absent on batches submitted before this check
    if expected and expected != sentences_sha(its, to_local):
        return "stale"
    try:
        card, report = apply(card, its, result)
        status = "verified"
    except CardFailed as e:
        report, status = e.report, "failed"
    _, version = prompts.load(prompts.VERIFY)
    report |= {"model": meta["params"]["model"], "prompt_version": version,
               "input_tokens": tin, "output_tokens": tout, "cost_usd": cost}  # fmt: skip
    with conn.cursor() as cur:
        cur.execute(
            """update cards set card = case when %s = 'verified' then %s::jsonb else card end,
                   status = %s, verified_at = now(),
                   cost_usd = coalesce(cost_usd, 0) + %s,
                   input_tokens = coalesce(input_tokens, 0) + %s,
                   output_tokens = coalesce(output_tokens, 0) + %s
               where id = %s and status = 'draft'""",
            (status, json.dumps(card), status, cost, tin, tout, meta["card_id"]),
        )  # fmt: skip
        if cur.rowcount == 0:
            conn.rollback()
            return "stale"
        # The report quotes the sentences that were removed, so it lives in a table
        # readers cannot query (card rows are public once published).
        cur.execute(
            """insert into card_verifications (card_id, report) values (%s, %s)
               on conflict (card_id) do update set report = excluded.report, created_at = now()""",
            (meta["card_id"], json.dumps(report)),
        )
    conn.commit()
    return status


def collect(conn: psycopg.Connection, client, batch_row_id: int) -> dict:
    statuses: Counter = Counter()

    def save(meta, result, tin, tout, cost):
        # Checked here (not in parse) because only meta knows the sentence count;
        # a ValueError from store triggers the same one retry.
        if len({v.id for v in result.verdicts}) * 2 < meta["n_items"]:
            raise ValueError(f"only {len(result.verdicts)} verdicts for {meta['n_items']}")
        statuses[store(conn, meta, result, tin, tout, cost)] += 1

    stats = batch.collect(conn, client, batch_row_id, parse_result, save)
    return stats | {"verified": statuses["verified"], "rejected": statuses["failed"],
                    "stale": statuses["stale"]}  # fmt: skip


def estimate_cost(jobs: list[VerifyJob], model: str) -> float:
    """~3.5 characters per token plus ~300 for the schema; ~1.5K output tokens."""
    system, _ = prompts.load(prompts.VERIFY)
    tin = sum((len(system) + len(j.user_message)) / 3.5 + 300 for j in jobs)
    return cost_usd(model, "batch", int(tin), 1500 * len(jobs))
