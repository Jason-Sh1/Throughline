"""Fetch every source once and store new articles.

Usage: python -m ingest.run [--no-rss] [--no-gdelt] [--no-guardian]
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

import psycopg

from cluster import entities
from common.db import connect
from common.runs import pipeline_run
from ingest import gdelt, guardian
from ingest.http import PoliteClient
from ingest.rss import FeedResult, FeedRow, fetch_feed
from ingest.store import store_articles

# Feeds are fetched in parallel across hosts; the per-host rate limiter still
# keeps us to one request at a time per publisher.
MAX_WORKERS = 8


def load_feeds(cur: psycopg.Cursor) -> list[FeedRow]:
    cur.execute(
        "select id, source_id, url, etag, last_modified from feeds where active order by id"
    )
    return [FeedRow(*row) for row in cur.fetchall()]


def save_feed_state(cur: psycopg.Cursor, r: FeedResult) -> None:
    cur.execute(
        """
        update feeds set etag = %s, last_modified = %s, last_status = %s, last_fetched_at = now()
        where id = %s
        """,
        (r.etag, r.last_modified, r.status, r.feed_id),
    )


def source_domains(cur: psycopg.Cursor) -> dict[str, int]:
    cur.execute("select id, homepage_url from sources where homepage_url is not null")
    return {gdelt.domain_of(url): sid for sid, url in cur.fetchall()}


def run(conn: psycopg.Connection, client: PoliteClient, rss=True, use_gdelt=True,
        use_guardian=True) -> dict:  # fmt: skip
    stats: dict = {"feeds_ok": 0, "feeds_304": 0, "feeds_failed": 0, "errors": []}
    articles = []
    with conn.cursor() as cur:
        if rss:
            feeds = load_feeds(cur)
            with ThreadPoolExecutor(MAX_WORKERS) as pool:
                results = list(pool.map(lambda f: fetch_feed(client, f), feeds))
            for r in results:
                save_feed_state(cur, r)
                if r.error:
                    stats["feeds_failed"] += 1
                    stats["errors"].append(f"feed {r.feed_id}: {r.error}")
                elif r.status == 304:
                    stats["feeds_304"] += 1
                else:
                    stats["feeds_ok"] += 1
                articles.extend(r.articles)
            conn.commit()
        if use_gdelt:
            found, errors = gdelt.fetch(client, source_domains(cur))
            stats["gdelt_fetched"] = len(found)
            articles.extend(found)
            stats["errors"].extend(errors)
        if use_guardian:
            cur.execute("select id from sources where slug = 'the-guardian'")
            row = cur.fetchone()
            if row:
                found, errors = guardian.fetch(client, row[0])
                # Guardian API items often share a URL with the Guardian RSS copy, so they
                # show up as url_dupes (backfilling raw_text) rather than new rows.
                stats["guardian_fetched"] = len(found)
                articles.extend(found)
                stats["errors"].extend(errors)

    stored = store_articles(conn, articles)
    stats.update(stored.as_dict())
    stats["fetched"] = len(articles)
    stats["entity_links"] = entities.save_for_articles(conn, stored.new_ids)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch sources and store articles")
    ap.add_argument("--no-rss", action="store_true")
    ap.add_argument("--no-gdelt", action="store_true")
    ap.add_argument("--no-guardian", action="store_true")
    args = ap.parse_args(argv)
    client = PoliteClient()
    try:
        with connect() as conn, pipeline_run(conn, "ingest") as stats:
            stats.update(
                run(conn, client, not args.no_rss, not args.no_gdelt, not args.no_guardian)
            )
            errors = stats.get("errors", [])
            print({k: v for k, v in stats.items() if k != "errors"})
            for e in errors[:50]:
                print("  !", e)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
