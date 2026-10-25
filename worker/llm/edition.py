"""Edition builder: rank today's clusters, extract, synthesize, assemble.

Usage:
  python -m llm.edition --slot morning [--full 50] [--tail 30] [--wait 3600]
  python -m llm.edition --collect          # ingest batches a previous run left open
  python -m llm.edition --export out.json  # write the latest edition as static JSON

Ranking (deliberately simple, see docs/decisions.md):
  score = log2(1 + outlets) * (1 + 0.25 * sides) * 0.5 ** (age_hours / 18)
where `sides` counts how many of {left, center, right} covered it (0-3) and
age is hours since the cluster's newest article. The top --full clusters get full
cards; the next --tail get a one-line summary from the extraction step.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from common.db import connect
from common.runs import pipeline_run
from llm import prompts
from llm.card import one_liner_card
from llm.extract import ExtractionFailed, extract_cluster, extract_model, gemini_caller
from llm.inputs import build_sources, docs_to_json, load_cluster_rows
from llm.synthesize import (
    anthropic_client,
    build_job,
    collect,
    estimate_cost,
    submit,
    synth_effort,
    synth_model,
    wait_for,
)  # fmt: skip

WINDOW_HOURS = 36
MIN_OUTLETS = 2
HALF_LIFE_HOURS = 18
# 3 editions a day * 30 days * $0.20 = $18, under the $20/month cap. Most
# editions cost far less, because unchanged clusters reuse their cached card.
MAX_EDITION_COST_USD = float(os.environ.get("MAX_EDITION_COST_USD", "0.20"))

SIDES = {"left": "left", "lean_left": "left", "center": "center",
         "lean_right": "right", "right": "right"}  # fmt: skip


def score(outlets: int, ratings: set[str], age_hours: float) -> float:
    sides = len({SIDES[r] for r in ratings if r in SIDES})
    return math.log2(1 + outlets) * (1 + 0.25 * sides) * 0.5 ** (age_hours / HALF_LIFE_HOURS)


def rank_clusters(conn: psycopg.Connection, now: datetime | None = None) -> list[int]:
    now = now or datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(
            """select ca.cluster_id, count(distinct a.source_id),
                      array_agg(distinct s.allsides_rating::text),
                      max(coalesce(a.published_at, a.fetched_at))
               from cluster_articles ca
               join clusters c on c.id = ca.cluster_id and c.status = 'active'
               join articles a on a.id = ca.article_id
               join sources s on s.id = a.source_id
               group by ca.cluster_id
               having count(distinct a.source_id) >= %s
                  and max(coalesce(a.published_at, a.fetched_at))
                      > %s - make_interval(hours => %s)""",
            (MIN_OUTLETS, now, WINDOW_HOURS),
        )
        rows = cur.fetchall()
    scored = [
        (score(n, set(ratings), max(0.0, (now - newest).total_seconds() / 3600)), cid)
        for cid, n, ratings, newest in rows
    ]
    return [cid for _, cid in sorted(scored, key=lambda t: (-t[0], t[1]))]


def get_edition(conn: psycopg.Connection, slot: str, day: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """insert into editions (edition_date, slot) values (%s, %s)
               on conflict (edition_date, slot) do update set status = 'building'
               returning id""",
            (day, slot),
        )
        eid = cur.fetchone()[0]
        cur.execute("delete from edition_cards where edition_id = %s", (eid,))
    conn.commit()
    return eid


def store_one_liner(conn: psycopg.Connection, cluster_id: int, extraction_id: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "select data, content_hash, model, prompt_version from extractions where id = %s",
            (extraction_id,),
        )
        data, ex_hash, model, version = cur.fetchone()
    if not data["one_liner"]["source_ids"]:
        return None
    docs = docs_to_json(build_sources(load_cluster_rows(conn, cluster_id)))
    card = one_liner_card(conn, cluster_id, data["one_liner"], docs)
    with conn.cursor() as cur:
        cur.execute(
            """insert into cards (cluster_id, content_hash, prompt_version, model, card,
                   source_count, input_tokens, output_tokens, cost_usd)
               values (%s, %s, %s, %s, %s, %s, 0, 0, 0)
               on conflict (content_hash) do update set card = excluded.card returning id""",
            (cluster_id, f"one-liner:{ex_hash}", version, model, json.dumps(card),
             card["confidence"]["outlet_count"]),
        )  # fmt: skip
        cid = cur.fetchone()[0]
    conn.commit()
    return cid


def latest_card(conn: psycopg.Connection, cluster_id: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            """select id from cards where cluster_id = %s and content_hash not like 'one-liner:%%'
               and status <> 'failed' order by created_at desc limit 1""",
            (cluster_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def build(conn, slot: str, n_full: int, n_tail: int, wait_s: int, stats: dict) -> int:
    edition_id = get_edition(conn, slot, datetime.now(UTC).date())
    ranked = rank_clusters(conn)[: n_full + n_tail]
    stats.update(ranked=len(ranked), edition_id=edition_id)

    call, ex_model = gemini_caller(extract_model()), extract_model()
    extraction_ids: dict[int, int] = {}
    spend = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for cid in ranked:
        try:
            ex_id, s = extract_cluster(conn, cid, call, ex_model)
            for k in spend:
                spend[k] += s[k]
            if ex_id:
                extraction_ids[cid] = ex_id
        except ExtractionFailed as e:
            stats["extraction_failed"] = stats.get("extraction_failed", 0) + 1
            print(f"  cluster {cid}: {e}")
    stats["extraction"] = dict(spend)
    full = [c for c in ranked[:n_full] if c in extraction_ids]
    tail = [c for c in ranked[n_full:] if c in extraction_ids]

    model, effort = synth_model(), synth_effort()
    jobs = [j for c in full if (j := build_job(conn, c, extraction_ids[c], model))]
    stats.update(full=len(full), tail=len(tail), new_cards=len(jobs),
                 cached_cards=len(full) - len(jobs))  # fmt: skip
    if jobs:
        est = estimate_cost(jobs, model)
        stats["estimated_cost_usd"] = round(est, 4)
        if est > MAX_EDITION_COST_USD:
            raise RuntimeError(
                f"estimated synthesis cost ${est:.3f} exceeds MAX_EDITION_COST_USD "
                f"${MAX_EDITION_COST_USD:.2f}; not submitting"
            )
        client = anthropic_client()
        batch_row = submit(conn, client, jobs, model, effort, edition_id)
        with conn.cursor() as cur:
            cur.execute("select provider_batch_id from llm_batches where id = %s", (batch_row,))
            provider_id = cur.fetchone()[0]
        print(f"submitted batch {provider_id} with {len(jobs)} requests (est ${est:.3f})")
        if wait_for(client, provider_id, wait_s):
            stats["synthesis"] = syn = collect(conn, client, batch_row, effort)
            for k in spend:
                spend[k] += syn[k]
        else:
            stats["batch_pending"] = provider_id
            print("batch still running; run `make edition-collect` later")

    assemble(conn, edition_id, full, tail, extraction_ids)
    stats.update(spend)  # pipeline_run copies these onto the run row
    return edition_id


def assemble(conn, edition_id: int, full: list[int], tail: list[int], ex_ids: dict) -> None:
    rank = 0
    with conn.cursor() as cur:
        cur.execute("delete from edition_cards where edition_id = %s", (edition_id,))
    for cid in full + tail:
        tier = "full" if cid in full else "one_liner"
        card_id = latest_card(conn, cid) if tier == "full" else None
        if card_id is None:  # no full card (failed or pending): fall back to a one-liner
            tier, card_id = "one_liner", store_one_liner(conn, cid, ex_ids[cid])
        if card_id is None:
            continue
        rank += 1
        with conn.cursor() as cur:
            cur.execute(
                """insert into edition_cards (edition_id, card_id, rank, tier)
                   values (%s, %s, %s, %s) on conflict do nothing""",
                (edition_id, card_id, rank, tier),
            )
    conn.commit()


def collect_open(conn, stats: dict) -> None:
    client = anthropic_client()
    with conn.cursor() as cur:
        cur.execute(
            """select id, provider_batch_id, edition_id from llm_batches
               where status in ('submitted', 'ended') order by id"""
        )
        open_batches = cur.fetchall()
    for row_id, provider_id, _edition_id in open_batches:
        if wait_for(client, provider_id, 0):
            stats[provider_id] = syn = collect(conn, client, row_id, synth_effort())
            for k in ("input_tokens", "output_tokens", "cost_usd"):
                stats[k] = stats.get(k, 0) + syn[k]


def export(conn, out: Path, edition_id: int | None = None) -> dict:
    """Static JSON for the web app (M5 reads it). Only our synthesis plus real
    headlines, outlets, timestamps and links; never article text (rule 5)."""
    with conn.cursor() as cur:
        if edition_id is None:
            cur.execute("select id from editions order by created_at desc limit 1")
            edition_id = cur.fetchone()[0]
        cur.execute("select edition_date, slot, status from editions where id = %s", (edition_id,))
        day, slot, status = cur.fetchone()
        cur.execute(
            """select ec.rank, ec.tier, c.card, c.status from edition_cards ec
               join cards c on c.id = ec.card_id where ec.edition_id = %s order by ec.rank""",
            (edition_id,),
        )
        cards = [card | {"rank": rank, "tier": tier, "status": cstatus}
                 for rank, tier, card, cstatus in cur.fetchall()]  # fmt: skip
    doc = {
        "edition_id": edition_id,
        "date": day.isoformat(),
        "slot": slot,
        "status": status,
        "prompt_version": prompts.SYNTHESIZE,
        "generated_at": datetime.now(UTC).isoformat(),
        "cards": cards,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return doc


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--slot", choices=["morning", "midday", "evening"])
    p.add_argument("--full", type=int, default=50, help="clusters that get a full card")
    p.add_argument("--tail", type=int, default=30, help="clusters that get a one-liner")
    p.add_argument("--wait", type=int, default=3600, help="seconds to wait for the batch")
    p.add_argument("--collect", action="store_true", help="ingest open batches and exit")
    p.add_argument("--export", type=Path, help="write the latest edition JSON here")
    args = p.parse_args()

    with connect() as conn:
        if args.collect:
            with pipeline_run(conn, "edition-collect") as stats:
                collect_open(conn, stats)
                print(stats)
        if args.slot:
            with pipeline_run(conn, "edition") as stats:
                build(conn, args.slot, args.full, args.tail, args.wait, stats)
                print(json.dumps(stats, indent=2, default=str))
        if args.export:
            doc = export(conn, args.export)
            print(f"wrote {args.export} ({len(doc['cards'])} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
