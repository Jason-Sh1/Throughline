"""Load feeds.yaml into the `sources` and `feeds` tables.

feeds.yaml is the source of truth. This upserts every outlet and feed, and
marks feeds that were removed from the file as inactive (never deletes them,
because articles reference them).

Usage: python -m ingest.sync_sources [--feeds path]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from common.db import connect
from ingest.feeds_config import DEFAULT_FEEDS_PATH, FeedsFile, load_raw, validate


def sync(conn: psycopg.Connection, feeds: FeedsFile) -> dict[str, int]:
    stats = {"sources": 0, "feeds": 0, "deactivated": 0}
    with conn.transaction(), conn.cursor() as cur:
        urls: list[str] = []
        for o in feeds.outlets:
            # rating_updated_at only moves when the rating actually changes.
            cur.execute(
                """
                insert into sources (slug, name, homepage_url, region, allsides_rating,
                                     allsides_url, rating_updated_at)
                values (%s, %s, %s, %s, %s, %s, now())
                on conflict (slug) do update set
                  name = excluded.name,
                  homepage_url = excluded.homepage_url,
                  region = excluded.region,
                  allsides_url = excluded.allsides_url,
                  rating_updated_at = case
                    when sources.allsides_rating is distinct from excluded.allsides_rating
                    then now() else sources.rating_updated_at end,
                  allsides_rating = excluded.allsides_rating
                returning id
                """,
                (o.slug, o.name, o.homepage, o.region, o.allsides_rating.value, o.allsides_url),
            )
            source_id = cur.fetchone()[0]
            stats["sources"] += 1
            for f in o.feeds:
                cur.execute(
                    """
                    insert into feeds (source_id, url, category, active)
                    values (%s, %s, %s, true)
                    on conflict (url) do update set
                      source_id = excluded.source_id,
                      category = excluded.category,
                      active = true
                    """,
                    (source_id, f.url, f.category.value),
                )
                urls.append(f.url)
                stats["feeds"] += 1
        cur.execute("update feeds set active = false where active and not (url = any(%s))", (urls,))
        stats["deactivated"] = cur.rowcount
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync feeds.yaml into Postgres")
    ap.add_argument("--feeds", type=Path, default=DEFAULT_FEEDS_PATH)
    args = ap.parse_args(argv)

    parsed, report = validate(load_raw(args.feeds))
    if parsed is None or not report.ok:
        print("feeds.yaml is invalid; run `make validate-feeds`.")
        return 1
    with connect() as conn:
        stats = sync(conn, parsed)
    print(
        f"Synced {stats['sources']} sources, {stats['feeds']} feeds, "
        f"deactivated {stats['deactivated']}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
