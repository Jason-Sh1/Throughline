"""Freeze recent clusters into eval stories: `make eval-snapshot N=60`.

Picks clusters with 3+ news outlets whose newest article is within --hours,
rotating across categories so the set isn't all politics. For each one it saves
the sources the model would see (same pick as production) plus up to
--candidates near-miss articles: the closest articles *outside* the cluster by
embedding. Those let the clustering labels catch wrongly split stories.

Existing story files are never overwritten (gold labels point at them).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from common.db import connect
from evals.stories import STORIES_DIR, SUMMARY_CHARS
from llm.inputs import build_sources, clean_text, is_opinion, load_cluster_rows


def pick_clusters(conn: psycopg.Connection, n: int, hours: int, min_outlets: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """select c.id, coalesce(c.category::text, 'none'), count(distinct a.source_id) n
               from clusters c
               join cluster_articles ca on ca.cluster_id = c.id
               join articles a on a.id = ca.article_id
               join sources s on s.id = a.source_id and s.kind = 'news'
               where c.status = 'active'
               group by c.id
               having count(distinct a.source_id) >= %s
                  and max(coalesce(a.published_at, a.fetched_at))
                      > now() - make_interval(hours => %s)
               order by n desc, c.id""",
            (min_outlets, hours),
        )
        rows = cur.fetchall()
    by_cat: dict[str, list[tuple]] = {}
    for cid, cat, outlets in rows:
        by_cat.setdefault(cat, []).append((cid, cat, outlets))
    picked: list[tuple] = []
    while len(picked) < n and any(by_cat.values()):
        for cat in sorted(by_cat):
            if by_cat[cat] and len(picked) < n:
                picked.append(by_cat[cat].pop(0))
    return picked


def near_misses(conn: psycopg.Connection, cluster_id: int, hours: int, k: int) -> list[dict]:
    """The k articles closest to the cluster's mean embedding that aren't in it
    (originals only, one per outlet+title)."""
    if k == 0:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """with centre as (
                   select avg(a.embedding) v from cluster_articles ca
                   join articles a on a.id = ca.article_id
                   where ca.cluster_id = %s and a.embedding is not null)
               select a.id, a.source_id, s.name, s.allsides_rating::text, s.kind, a.title, a.url,
                      a.published_at, a.summary
               from articles a join sources s on s.id = a.source_id, centre
               where centre.v is not null and a.embedding is not null and a.duplicate_of is null
                 and not exists (select 1 from cluster_articles x
                                 where x.article_id = a.id and x.cluster_id = %s)
                 and coalesce(a.published_at, a.fetched_at) > now() - make_interval(hours => %s)
               order by a.embedding <=> centre.v
               limit %s""",
            (cluster_id, cluster_id, hours, k),
        )
        cols = ["id", "source_id", "outlet", "allsides_rating", "kind", "title", "url",
                "published_at", "summary"]  # fmt: skip
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _article(ref: str, r: dict, in_cluster: bool, used: bool) -> dict:
    return {
        "ref": ref,
        "outlet": r["outlet"],
        "allsides_rating": r["allsides_rating"],
        "kind": r.get("kind", "news"),
        "title": clean_text(r["title"]),
        "url": r["url"],
        "published_at": r["published_at"].isoformat() if r["published_at"] else None,
        "summary": clean_text(r["summary"])[:SUMMARY_CHARS],  # never raw_text (public repo)
        "is_opinion": is_opinion(r["url"]),
        "in_cluster": in_cluster,
        "used": used,
    }


def snapshot_cluster(conn, cluster_id: int, category: str, hours: int, k: int) -> dict:
    rows = load_cluster_rows(conn, cluster_id)
    # Same source pick as production, but built without raw_text.
    docs = build_sources([r | {"raw_text": None} for r in rows])
    by_id = {r["id"]: r for r in rows}
    articles = [_article(d.local_id, by_id[d.article_id], True, True) for d in docs]
    articles += [_article(f"C{i}", r, False, False)
                 for i, r in enumerate(near_misses(conn, cluster_id, hours, k), 1)]  # fmt: skip
    now = datetime.now(UTC)
    return {
        "id": f"{now:%Y-%m-%d}-c{cluster_id}",
        "cluster_id": cluster_id,
        "category": category,
        "snapshot_at": now.isoformat(timespec="seconds"),
        "articles": articles,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--n", type=int, default=60)
    p.add_argument("--hours", type=int, default=72)
    p.add_argument("--min-outlets", type=int, default=3)
    p.add_argument("--candidates", type=int, default=5)
    p.add_argument("--out", type=Path, default=STORIES_DIR)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    with connect() as conn:
        for cid, cat, outlets in pick_clusters(conn, args.n, args.hours, args.min_outlets):
            story = snapshot_cluster(conn, cid, cat, args.hours, args.candidates)
            path = args.out / f"{story['id']}.json"
            if path.exists() or len([a for a in story["articles"] if a["used"]]) < 2:
                continue
            path.write_text(json.dumps(story, indent=2, ensure_ascii=False) + "\n", "utf-8")
            written += 1
            print(f"{story['id']}  [{cat}] {outlets} outlets  {story['articles'][0]['title']}")
    print(f"wrote {written} stories to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
