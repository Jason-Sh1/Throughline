"""Anthropic Message Batches plumbing shared by extraction and synthesis.

A batch is half the price of normal requests and usually finishes in minutes
(at most 24 h). We record every batch in `llm_batches` with the metadata needed
to store each result (keyed by custom_id, because results come back in any
order), so if a run stops waiting, the next run can still collect it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import psycopg

from llm.cost import cost_usd


def params(system: str, user: str, schema: dict, model: str, effort: str, max_tokens: int):
    """One request: cached system prompt, JSON enforced by structured outputs.
    No temperature (current Claude models reject it) and no prefill."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }


def submit(
    conn: psycopg.Connection, client, kind: str, requests: dict[str, tuple[dict, dict]],
    edition_id: int | None = None,
) -> tuple[int, str]:  # fmt: skip
    """requests: custom_id -> (params, meta). Returns (llm_batches id, provider id)."""
    batch = client.messages.batches.create(
        requests=[{"custom_id": cid, "params": p} for cid, (p, _) in requests.items()]
    )
    meta = {cid: m | {"params": p} for cid, (p, m) in requests.items()}
    with conn.cursor() as cur:
        cur.execute(
            """insert into llm_batches
                   (provider_batch_id, kind, edition_id, requests, request_count)
               values (%s, %s, %s, %s, %s) returning id""",
            (batch.id, kind, edition_id, json.dumps(meta), len(requests)),
        )
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id, batch.id


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


def message_text(message) -> str:
    """The JSON text of a response; ValueError if the model refused or ran out."""
    if message.stop_reason == "refusal":
        raise ValueError("model refused")
    if message.stop_reason == "max_tokens":
        raise ValueError("hit max_tokens")
    return next((b.text for b in message.content if b.type == "text"), "")


def usage_cost(message, model: str, mode: str) -> tuple[int, int, float]:
    u = message.usage
    cr = u.cache_read_input_tokens or 0
    cw = u.cache_creation_input_tokens or 0
    tin = u.input_tokens + cr + cw
    return tin, u.output_tokens, cost_usd(model, mode, u.input_tokens, u.output_tokens, cr, cw)


def collect[T](
    conn: psycopg.Connection, client, row_id: int,
    parse: Callable[[str], T], store: Callable[[dict, T, int, int, float], object],
) -> dict:  # fmt: skip
    """Ingest a finished batch. For each succeeded result: parse the JSON, and on
    a ValueError retry once as a normal request with the same params (CLAUDE.md:
    retry once, then fail). `store(meta, parsed, tokens_in, tokens_out, cost)`
    saves it and may raise ValueError to reject it. Returns stats + spend."""
    with conn.cursor() as cur:
        cur.execute("select provider_batch_id, requests from llm_batches where id = %s", (row_id,))
        provider_id, requests = cur.fetchone()
    stats: dict = {"ok": 0, "invalid": 0, "failed": 0}
    spend = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for res in client.messages.batches.results(provider_id):
        meta = requests.get(res.custom_id)
        if meta is None:
            continue
        if res.result.type != "succeeded":  # errored / expired / canceled
            stats["failed"] += 1
            continue
        msg, mode = res.result.message, "batch"
        for attempt in range(2):
            tin, tout, cost = usage_cost(msg, meta["params"]["model"], mode)
            spend["input_tokens"] += tin
            spend["output_tokens"] += tout
            spend["cost_usd"] += cost
            try:
                store(meta, parse(message_text(msg)), tin, tout, cost)
                stats["ok"] += 1
                break
            except ValueError as e:
                stats["invalid"] += 1
                print(f"  {res.custom_id}: {e}")
                if attempt == 0 and "refused" not in str(e):
                    msg, mode = client.messages.create(**meta["params"]), "standard"
                else:
                    stats["failed"] += 1
                    break
    with conn.cursor() as cur:
        cur.execute(
            """update llm_batches set status = 'ingested', ended_at = coalesce(ended_at, now()),
                   ingested_at = now() where id = %s""",
            (row_id,),
        )
    conn.commit()
    return stats | spend


def open_batches(conn: psycopg.Connection) -> list[tuple[int, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """select id, provider_batch_id, kind from llm_batches
               where status in ('submitted', 'ended') order by id"""
        )
        return cur.fetchall()


def anthropic_client():
    import anthropic

    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
