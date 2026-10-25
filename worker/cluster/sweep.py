"""Threshold sweep: replay online clustering over the current window with
several configs, without writing anything, and report how each one splits
the articles.

    python -m cluster.sweep                       # default grid
    python -m cluster.sweep --thresholds 0.66 0.70 --gray 0 0.05 --show 0.70:0.05

Why: tuning by eyeballing one run at a time is slow, and the live data is
only reachable from CI. The sweep prints, per config, how many multi-outlet
stories it finds, how big the biggest one gets, and a cohesion number (share
of members whose leave-one-out similarity to their own cluster is below
LOOSE_MEMBER_SIM, a proxy for "this article doesn't belong here"). Then it
prints the biggest clusters for the configs named with --show, so a human
can read the headlines and judge over-merging.

The replay is a vectorized copy of `online.choose_cluster` (a test checks
they agree), because the pure-Python version is too slow to run 12 times
over ~10k articles.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from cluster.embed import to_array
from cluster.online import ClusterConfig
from common.db import connect

LOOSE_MEMBER_SIM = 0.45


@dataclass
class Item:
    id: int
    vec: np.ndarray  # unit length
    entities: set[str]
    source: str
    title: str


def replay(items: list[Item], cfg: ClusterConfig) -> list[int]:
    """Cluster label per item (in order), mirroring online.choose_cluster."""
    dim = items[0].vec.shape[0] if items else 0
    cents = np.zeros((len(items), dim))  # preallocated; rows [0, k) are live clusters
    counts: list[int] = []
    ents: list[set[str]] = []
    labels: list[int] = []

    def pick(idx: np.ndarray, sims: np.ndarray, ent: set[str], min_overlap: int = 0):
        # Most shared names, then highest similarity; ties go to the older cluster.
        best = None
        for i in idx:
            key = (len(ent & ents[i]), sims[i])
            if key[0] >= min_overlap and (best is None or key > best[0]):
                best = (key, int(i))
        return None if best is None else best[1]

    for it in items:
        k = len(counts)
        chosen = None
        if k:
            sims = cents[:k] @ it.vec
            top = float(sims.max())
            if top >= cfg.threshold:
                idx = np.nonzero((sims >= top - cfg.tie_margin) & (sims >= cfg.threshold))[0]
                chosen = pick(idx, sims, it.entities)
            else:
                idx = np.nonzero(sims >= cfg.threshold - cfg.gray_margin)[0]
                chosen = pick(idx, sims, it.entities, cfg.min_entity_overlap)
        if chosen is None:
            cents[k] = it.vec
            counts.append(1)
            ents.append(set(it.entities))
            labels.append(k)
        else:
            # Same running mean as online.Candidate.add.
            n = counts[chosen]
            mean = (cents[chosen] * n + it.vec) / (n + 1)
            cents[chosen] = mean / (np.linalg.norm(mean) or 1.0)
            counts[chosen] = n + 1
            ents[chosen] |= it.entities
            labels.append(chosen)
    return labels


def summarize(items: list[Item], labels: list[int]) -> dict:
    groups: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        groups.setdefault(lab, []).append(idx)
    multi = [g for g in groups.values() if len({items[i].source for i in g}) >= 2]
    big = [g for g in multi if len({items[i].source for i in g}) >= 5]
    loose = members = 0
    for g in multi:
        total = np.sum([items[i].vec for i in g], axis=0)
        for i in g:
            rest = total - items[i].vec
            sim = float(items[i].vec @ rest / (np.linalg.norm(rest) or 1.0))
            loose += sim < LOOSE_MEMBER_SIM
            members += 1
    return {
        "clusters": len(groups),
        "multi_outlet": len(multi),
        "5plus_outlets": len(big),
        "largest": max((len(g) for g in groups.values()), default=0),
        "clustered_share": members / max(len(items), 1),
        "loose_share": loose / max(members, 1),
        "groups": sorted(multi, key=len, reverse=True),
    }


def load(conn: psycopg.Connection, window_hours: int) -> list[Item]:
    register_vector(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            select a.id, a.embedding, s.name, a.title,
                   coalesce(array_agg(e.normalized_name) filter (where e.id is not null), '{}')
            from articles a
            join sources s on s.id = a.source_id
            left join article_entities ae on ae.article_id = a.id
            left join entities e on e.id = ae.entity_id
            where a.embedding is not null and a.duplicate_of is null
              and coalesce(a.published_at, a.fetched_at) > now() - make_interval(hours => %s)
            group by a.id, s.name
            order by coalesce(a.published_at, a.fetched_at), a.id
            """,
            (window_hours,),
        )
        out = []
        for aid, emb, src, title, names in cur:
            v = to_array(emb)
            out.append(Item(aid, v / (np.linalg.norm(v) or 1.0), set(names), src, title))
        return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--thresholds", type=float, nargs="+", default=[0.62, 0.66, 0.70, 0.74])
    p.add_argument("--gray", type=float, nargs="+", default=[0.0, 0.05, 0.10])
    p.add_argument("--show", nargs="*", default=["0.66:0.05", "0.70:0.05"],
                   help="threshold:gray configs whose biggest clusters to print")  # fmt: skip
    p.add_argument("--top", type=int, default=6)
    args = p.parse_args()

    base = ClusterConfig.from_env()
    with connect() as conn:
        items = load(conn, base.window_hours)
    print(f"{len(items)} articles in the {base.window_hours}h window\n")
    print("thresh gray | clusters multi 5+outlets largest clustered% loose%")
    results = {}
    for t in args.thresholds:
        for g in args.gray:
            s = summarize(items, replay(items, replace(base, threshold=t, gray_margin=g)))
            results[f"{t:.2f}:{g:.2f}"] = s
            print(f" {t:.2f}  {g:.2f} | {s['clusters']:8} {s['multi_outlet']:5} "
                  f"{s['5plus_outlets']:9} {s['largest']:7} {s['clustered_share']:9.0%} "
                  f"{s['loose_share']:6.0%}")  # fmt: skip

    for key in args.show:
        t, g = (float(x) for x in key.split(":"))
        s = results.get(f"{t:.2f}:{g:.2f}") or summarize(
            items, replay(items, replace(base, threshold=t, gray_margin=g))
        )
        print(f"\n== threshold {t:.2f}, gray {g:.2f}: {args.top} largest multi-outlet clusters ==")
        for grp in s["groups"][: args.top]:
            outlets = len({items[i].source for i in grp})
            print(f"-- {len(grp)} articles, {outlets} outlets")
            for i in grp[:8]:
                print(f"   {items[i].source[:20]:20}  {items[i].title[:90]}")
            if len(grp) > 8:
                print(f"   ... {len(grp) - 8} more")


if __name__ == "__main__":
    main()
