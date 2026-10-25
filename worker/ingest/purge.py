"""Purge raw article text older than 14 days (CLAUDE.md rule 5).

Only `raw_text` is cleared; the headline, link, summary and embedding stay so
old clusters keep working. `raw_text_purged_at` records that it happened and
stops a later re-fetch from restoring the text.

Usage: python -m ingest.purge [--days 14]
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from common.db import connect
from common.runs import pipeline_run

RETENTION_DAYS = 14


def purge(conn: psycopg.Connection, days: int = RETENTION_DAYS) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update articles set raw_text = null, raw_text_purged_at = now()
            where raw_text is not null and fetched_at < now() - make_interval(days => %s)
            """,
            (days,),
        )
        return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Purge old raw article text")
    ap.add_argument("--days", type=int, default=RETENTION_DAYS)
    args = ap.parse_args(argv)
    with connect() as conn, pipeline_run(conn, "purge") as stats:
        stats["purged"] = purge(conn, args.days)
        print(f"Purged raw text from {stats['purged']} articles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
