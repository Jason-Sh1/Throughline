"""Print recent clusters with their headlines, for eyeballing cluster quality.

Usage:
    python -m cluster.inspect                      # biggest clusters from the last 24h
    python -m cluster.inspect --hours 48 --min-outlets 3 --limit 20
    python -m cluster.inspect --id 123             # one cluster in full

While tuning CLUSTER_THRESHOLD, look for two failure types and note counts in
docs/decisions.md:
    over-merge: one cluster mixes different events -> raise the threshold
    over-split: one event appears as several clusters -> lower it
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from common.db import connect

SPECTRUM_ORDER = ["left", "lean_left", "center", "lean_right", "right", "mixed", "not_rated"]
SPECTRUM_SHORT = {"left": "L", "lean_left": "LL", "center": "C", "lean_right": "LR",
                  "right": "R", "mixed": "M", "not_rated": "NR"}  # fmt: skip


def fetch_clusters(cur: psycopg.Cursor, hours: int, min_outlets: int, limit: int,
                   cluster_id: int | None) -> list[tuple]:  # fmt: skip
    if cluster_id is not None:
        cur.execute(
            "select id, article_count, outlet_count, category, first_seen_at from clusters "
            "where id = %s",
            (cluster_id,),
        )
    else:
        cur.execute(
            """
            select id, article_count, outlet_count, category, first_seen_at from clusters
            where status = 'active' and last_updated_at > now() - make_interval(hours => %s)
              and outlet_count >= %s
            order by outlet_count desc, article_count desc, id
            limit %s
            """,
            (hours, min_outlets, limit),
        )
    return cur.fetchall()


def fetch_articles(cur: psycopg.Cursor, cluster_id: int) -> list[tuple]:
    cur.execute(
        """
        select s.name, s.allsides_rating, a.title, a.published_at, ca.similarity,
               a.duplicate_of is not null as is_dup, a.url
        from cluster_articles ca
        join articles a on a.id = ca.article_id
        join sources s on s.id = a.source_id
        where ca.cluster_id = %s
        order by a.published_at nulls last, a.id
        """,
        (cluster_id,),
    )
    return cur.fetchall()


def format_cluster(row: tuple, articles: list[tuple], max_headlines: int, show_urls: bool) -> str:
    cid, n, outlets, category, first_seen = row
    spectrum: dict[str, int] = {}
    for _, rating, *_ in articles:
        spectrum[rating] = spectrum.get(rating, 0) + 1
    spec = " ".join(f"{SPECTRUM_SHORT[r]}:{spectrum[r]}" for r in SPECTRUM_ORDER if r in spectrum)
    lines = [
        f"#{cid}  {n} articles, {outlets} outlets  [{category or '-'}]  "
        f"first seen {first_seen:%Y-%m-%d %H:%M}  {spec}"
    ]
    for outlet, rating, title, published, sim, is_dup, url in articles[:max_headlines]:
        when = f"{published:%m-%d %H:%M}" if published else "  --  "
        sim_s = f"{sim:.2f}" if sim is not None else " -- "
        dup = " (dup)" if is_dup else ""
        lines.append(
            f"   {when}  {sim_s}  {outlet[:22]:<22} [{SPECTRUM_SHORT[rating]:>2}]  {title}{dup}"
        )
        if show_urls:
            lines.append(f"{'':>42}{url}")
    if len(articles) > max_headlines:
        lines.append(f"   ... {len(articles) - max_headlines} more")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inspect clusters")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--min-outlets", type=int, default=1)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--headlines", type=int, default=8)
    ap.add_argument("--id", type=int, dest="cluster_id")
    ap.add_argument("--urls", action="store_true")
    args = ap.parse_args(argv)

    with connect() as conn, conn.cursor() as cur:
        rows = fetch_clusters(cur, args.hours, args.min_outlets, args.limit, args.cluster_id)
        if not rows:
            print("No clusters match.")
        for row in rows:
            articles = fetch_articles(cur, row[0])
            max_h = len(articles) if args.cluster_id else args.headlines
            print(format_cluster(row, articles, max_h, args.urls))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
