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
from llm import batch, extract, prompts, synthesize, verify
from llm.batch import anthropic_client
from llm.card import SIDES, one_liner_card
from llm.extract import ExtractionFailed, ExtractJob, extract_model, gemini_caller
from llm.inputs import build_sources, docs_to_json, load_cluster_rows
from llm.synthesize import build_job, synth_effort, synth_model

WINDOW_HOURS = 36
MIN_OUTLETS = 2
HALF_LIFE_HOURS = 18
# 3 editions a day * 30 days * $0.20 = $18, under the $20/month cap. Most
# editions cost far less, because unchanged clusters reuse their cached card.
MAX_EDITION_COST_USD = float(os.environ.get("MAX_EDITION_COST_USD", "0.20"))


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
    """The edition row for this slot. Re-running a slot that is already published
    leaves it published, with its cards in place, until the new build is ready:
    `assemble` + `publish` swap the cards in one transaction. A `failed` edition
    goes back to `building`."""
    with conn.cursor() as cur:
        cur.execute(
            """insert into editions (edition_date, slot) values (%s, %s)
               on conflict (edition_date, slot) do update set status =
                   case when editions.status = 'published' then 'published' else 'building' end
               returning id""",
            (day, slot),
        )
        eid = cur.fetchone()[0]
    conn.commit()
    return eid


def edition_status(conn: psycopg.Connection, edition_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute("select status from editions where id = %s", (edition_id,))
        return cur.fetchone()[0]


def mark_failed(conn: psycopg.Connection, edition_id: int) -> None:
    """A build that cannot finish. Never downgrades an edition readers already see."""
    with conn.cursor() as cur:
        cur.execute(
            "update editions set status = 'failed' where id = %s and status = 'building'",
            (edition_id,),
        )
    conn.commit()


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
               on conflict (content_hash) do nothing returning id""",
            (cluster_id, f"one-liner:{ex_hash}", version, model, json.dumps(card),
             card["confidence"]["outlet_count"]),
        )  # fmt: skip
        row = cur.fetchone()
        if row is None:  # already stored (and maybe verified): keep it as is
            cur.execute("select id from cards where content_hash = %s", (f"one-liner:{ex_hash}",))
            row = cur.fetchone()
        cid = row[0]
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


SPEND_KEYS = ("input_tokens", "output_tokens", "cost_usd")


def _add(total: dict, part: dict) -> None:
    for k in SPEND_KEYS:
        total[k] = total.get(k, 0) + part.get(k, 0)


class OverBudget(RuntimeError):
    pass


def _check_budget(stats: dict, step: str, estimate: float) -> None:
    """Refuse to submit if this edition's estimated LLM spend would pass the cap."""
    stats[f"{step}_estimate_usd"] = round(estimate, 4)
    so_far = sum(v for k, v in stats.items() if k.endswith("_estimate_usd"))
    if so_far > MAX_EDITION_COST_USD:
        raise OverBudget(
            f"estimated edition cost ${so_far:.3f} exceeds MAX_EDITION_COST_USD "
            f"${MAX_EDITION_COST_USD:.2f}; not submitting {step}"
        )


def _run_batch(conn, client, submit, collect, wait_s: int, stats: dict, step: str) -> bool:
    """Submit, wait, collect. False if the batch is still running at the deadline
    (the next run's collect_open picks it up, and its results land in the cache)."""
    row_id, provider_id = submit()
    print(f"submitted {step} batch {provider_id}")
    if not batch.wait_for(client, provider_id, wait_s):
        stats[f"{step}_batch_pending"] = provider_id
        return False
    stats[step] = collect(conn, client, row_id)
    _add(stats, stats[step])
    return True


def run_extraction(
    conn, ranked: list[int], edition_id: int, wait_s: int, stats: dict
) -> tuple[dict, bool]:
    """(cluster id -> extraction id for every ranked cluster that has one, pending).
    `pending` is True when an extraction batch is still running, in which case the
    edition is incomplete and must not be published."""
    model = extract_model()
    ids: dict[int, int] = {}
    pending = False
    if model.startswith("gemini"):
        call = gemini_caller(model)
        for cid in ranked:
            try:
                ex_id, spend = extract.extract_sync(conn, cid, call, model)
            except ExtractionFailed as e:
                stats["extraction_failed"] = stats.get("extraction_failed", 0) + 1
                print(f"  cluster {cid}: {e}")
                continue
            _add(stats, spend)
            if ex_id:
                ids[cid] = ex_id
        return ids, pending

    jobs: list[ExtractJob] = []
    for cid in ranked:
        planned = extract.plan(conn, cid, model)
        if isinstance(planned, ExtractJob):
            jobs.append(planned)
        elif planned:
            ids[cid] = planned
    # Work an earlier run already submitted is still on its way: wait for it
    # instead of paying for it twice.
    flying = {m["content_hash"] for m in batch.in_flight(conn, "extract")}
    waiting = [j for j in jobs if j.content_hash in flying]
    jobs = [j for j in jobs if j.content_hash not in flying]
    pending |= bool(waiting)
    stats.update(new_extractions=len(jobs), cached_extractions=len(ids),
                 extractions_in_flight=len(waiting))  # fmt: skip
    if jobs:
        _check_budget(stats, "extraction", extract.estimate_cost(jobs, model))
        client = anthropic_client()
        pending |= not _run_batch(
            conn, client, lambda: extract.submit(conn, client, jobs, model, edition_id),
            extract.collect, wait_s, stats, "extraction")  # fmt: skip
        with conn.cursor() as cur:
            cur.execute(
                "select content_hash, id from extractions where content_hash = any(%s)",
                ([j.content_hash for j in jobs],),
            )
            by_hash = dict(cur.fetchall())
        ids |= {j.cluster_id: by_hash[j.content_hash] for j in jobs if j.content_hash in by_hash}
    return ids, pending


def build(
    conn, slot: str, n_full: int, n_tail: int, wait_s: int, stats: dict, day: date | None = None
) -> int:
    """Returns the edition id. Spend lands in stats, which pipeline_run records.

    Steps: collect batches an earlier run left open -> rank -> extract (batch) ->
    synthesize full cards (batch) -> verify every candidate card (batch) -> swap in
    the verified cards and publish, in one transaction.

    If any batch outlives --wait, nothing is published by this run: a new edition
    stays `building`, and an edition that was already published keeps the cards
    readers see now. Re-running the same slot (pass --date after midnight UTC)
    collects the finished batches and completes it. An edition with no verified
    card is marked `failed`, never published empty.
    """
    edition_id = get_edition(conn, slot, day or datetime.now(UTC).date())
    if not _lock_edition(conn, edition_id):
        raise SystemExit(f"another build of edition {edition_id} is running; not starting a second")
    try:
        return _build_locked(conn, edition_id, n_full, n_tail, wait_s, stats)
    finally:
        _unlock_edition(conn, edition_id)


# Session-level advisory lock: held across the many commits of a build, released in
# `finally`, and released by Postgres itself if the process dies.
LOCK_NAMESPACE = 7461  # arbitrary; keeps edition locks apart from any other advisory locks


def _lock_edition(conn, edition_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(%s, %s)", (LOCK_NAMESPACE, edition_id))
        got = cur.fetchone()[0]
    conn.commit()
    return got


def _unlock_edition(conn, edition_id: int) -> None:
    conn.rollback()  # leave no failed transaction behind before unlocking
    with conn.cursor() as cur:
        cur.execute("select pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, edition_id))
    conn.commit()


def _build_locked(conn, edition_id: int, n_full: int, n_tail: int, wait_s: int, stats: dict) -> int:
    was_published = edition_status(conn, edition_id) == "published"
    stats.update(edition_id=edition_id)
    for k in SPEND_KEYS:
        stats.setdefault(k, 0)
    try:
        collect_open(conn, stats)
        ranked = rank_clusters(conn)[: n_full + n_tail]
        stats.update(ranked=len(ranked))

        ex_ids, pending = run_extraction(conn, ranked, edition_id, wait_s, stats)
        full = [c for c in ranked[:n_full] if c in ex_ids]
        tail = [c for c in ranked[n_full:] if c in ex_ids]

        model, effort = synth_model(), synth_effort()
        jobs = [j for c in full if (j := build_job(conn, c, ex_ids[c], model))]
        flying = {m["content_hash"] for m in batch.in_flight(conn, "synthesize")}
        waiting = [j for j in jobs if j.content_hash in flying]
        jobs = [j for j in jobs if j.content_hash not in flying]
        pending |= bool(waiting)
        stats.update(full=len(full), tail=len(tail), new_cards=len(jobs),
                     cards_in_flight=len(waiting),
                     cached_cards=len(full) - len(jobs) - len(waiting))  # fmt: skip
        if jobs:
            _check_budget(stats, "synthesis", synthesize.estimate_cost(jobs, model))
            client = anthropic_client()
            pending |= not _run_batch(
                conn, client,
                lambda: synthesize.submit(conn, client, jobs, model, effort, edition_id),
                synthesize.collect, wait_s, stats, "synthesis")  # fmt: skip

        candidates = candidate_cards(conn, full, tail, ex_ids)
        pending |= not run_verification(conn, candidates, edition_id, wait_s, stats)
    except Exception:
        conn.rollback()
        mark_failed(conn, edition_id)
        raise

    if pending:
        if not was_published:  # keep a draft line-up visible to `--export`
            assemble(conn, edition_id, candidates)
            conn.commit()
        stats["published"] = False
        return edition_id
    n_cards = assemble(conn, edition_id, candidates)
    if n_cards == 0:
        conn.rollback()  # keep whatever line-up was there before
        mark_failed(conn, edition_id)
        stats.update(published=False, empty=True)
        print("no verified card: edition not published")
        return edition_id
    publish(conn, edition_id)  # commits the new line-up and the status together
    stats.update(published=True, cards=n_cards)
    return edition_id


def candidate_cards(conn, full: list[int], tail: list[int], ex_ids: dict) -> list[tuple]:
    """(cluster id, full card id or None, one-liner card id or None) in rank order.
    Full-tier stories also get a one-liner, verified in the same batch, so a full
    card that fails verification can fall back without a second round."""
    out = []
    for cid in full + tail:
        card_id = latest_card(conn, cid) if cid in full else None
        out.append((cid, card_id, store_one_liner(conn, cid, ex_ids[cid])))
    return out


def run_verification(conn, candidates: list[tuple], edition_id: int, wait_s: int,
                     stats: dict) -> bool:  # fmt: skip
    """Verify every candidate card still in `draft`. False if any verification is
    still pending, whether submitted by this run or by an earlier one."""
    ids = [i for _, full_id, one_id in candidates for i in (full_id, one_id) if i]
    with conn.cursor() as cur:
        cur.execute("select id from cards where id = any(%s) and status = 'draft'", (ids,))
        drafts = sorted(r[0] for r in cur.fetchall())
    # A card already sitting in an open verify batch is not sent again: a second
    # set of verdicts would be applied to a card the first set had already cut.
    flying = {m["card_id"] for m in batch.in_flight(conn, "verify")}
    waiting = [i for i in drafts if i in flying]
    jobs = [j for i in drafts if i not in flying and (j := verify.plan(conn, i))]
    stats.update(new_verifications=len(jobs), verifications_in_flight=len(waiting))
    done = not waiting
    if jobs:
        model, effort = verify.verify_model(), verify.verify_effort()
        _check_budget(stats, "verification", verify.estimate_cost(jobs, model))
        client = anthropic_client()
        done &= _run_batch(conn, client,
                           lambda: verify.submit(conn, client, jobs, model, effort, edition_id),
                           verify.collect, wait_s, stats, "verification")  # fmt: skip
    return done


def card_status(conn, card_id: int | None) -> str | None:
    if card_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute("select status from cards where id = %s", (card_id,))
        row = cur.fetchone()
    return row[0] if row else None


PUBLISHABLE = ("verified", "published")


def assemble(conn, edition_id: int, candidates: list[tuple]) -> int:
    """Replace the edition's line-up; returns how many cards it now has. Only
    verified cards make it in (rule 2): the full card if it passed, else the
    story's one-liner if that passed, else the story is left out.

    Does not commit: the caller commits, so replacing the cards of a published
    edition and publishing the new ones is a single transaction."""
    rank = 0
    with conn.cursor() as cur:
        cur.execute("delete from edition_cards where edition_id = %s", (edition_id,))
    for _, full_id, one_id in candidates:
        if card_status(conn, full_id) in PUBLISHABLE:
            tier, card_id = "full", full_id
        elif card_status(conn, one_id) in PUBLISHABLE:
            tier, card_id = "one_liner", one_id
        else:
            continue
        rank += 1
        with conn.cursor() as cur:
            cur.execute(
                """insert into edition_cards (edition_id, card_id, rank, tier)
                   values (%s, %s, %s, %s) on conflict do nothing""",
                (edition_id, card_id, rank, tier),
            )
    return rank


def publish(conn, edition_id: int) -> None:
    """Make the edition and its cards readable by the web app (RLS policies
    expose only `published` rows)."""
    with conn.cursor() as cur:
        cur.execute(
            """update cards set status = 'published' where id in
                   (select card_id from edition_cards where edition_id = %s)""",
            (edition_id,),
        )
        cur.execute(
            "update editions set status = 'published', published_at = now() where id = %s",
            (edition_id,),
        )
    conn.commit()


def collect_open(conn, stats: dict) -> None:
    """Ingest finished batches that an earlier run stopped waiting for. Their
    results go into the extraction / card caches, so re-running the edition
    then uses them without paying again."""
    open_rows = batch.open_batches(conn)
    if not open_rows:
        return
    client = anthropic_client()
    collectors = {
        "extract": extract.collect,
        "synthesize": synthesize.collect,
        "verify": verify.collect,
    }
    for row_id, provider_id, kind in open_rows:
        # Each batch on its own: one that cannot be read (deleted, expired, made
        # under another key) must not stop the others, or every later build.
        try:
            if batch.wait_for(client, provider_id, 0):
                stats[provider_id] = collectors[kind](conn, client, row_id)
                _add(stats, stats[provider_id])
        except Exception as e:  # noqa: BLE001 - any failure is handled the same way
            conn.rollback()
            gone = batch.give_up(conn, row_id, permanent=batch.is_permanent(e))
            stats[provider_id] = {"error": f"{type(e).__name__}: {e}"[:200], "abandoned": gone}
            print(f"  batch {provider_id}: {e}" + (" (abandoned)" if gone else " (will retry)"))


def export(conn, out: Path, edition_id: int | None = None) -> dict:
    """Static JSON for the web app (M5 reads it). Only our synthesis plus real
    headlines, outlets, timestamps and links; never article text (rule 5)."""
    with conn.cursor() as cur:
        if edition_id is None:
            cur.execute("select id from editions order by created_at desc limit 1")
            row = cur.fetchone()
            if row is None:
                raise SystemExit("no edition to export yet; run `make edition` first")
            edition_id = row[0]
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
    p.add_argument("--date", type=date.fromisoformat, default=None,
                   help="edition date, YYYY-MM-DD (default: today, UTC); pass it to finish "
                        "an edition after midnight UTC")  # fmt: skip
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
                build(conn, args.slot, args.full, args.tail, args.wait, stats, args.date)
                print(json.dumps(stats, indent=2, default=str))
        if args.export:
            doc = export(conn, args.export)
            print(f"wrote {args.export} ({len(doc['cards'])} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
