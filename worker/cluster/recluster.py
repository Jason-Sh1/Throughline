"""Nightly re-cluster with HDBSCAN.

Online clustering is greedy: it decides once, in arrival order, and never looks
back. Once a night we re-cluster the whole window at once with HDBSCAN and
fix its mistakes:

- merge: two online clusters that HDBSCAN sees as one event
- split: one online cluster whose articles HDBSCAN puts in separate events

HDBSCAN suits news because it doesn't need the number of clusters up front and
it labels outliers as "noise" instead of forcing them into a group. We keep
noise articles where online clustering put them, so a quiet night doesn't
shuffle stable clusters. We use scikit-learn's HDBSCAN rather than the separate
`hdbscan` package: same algorithm, one less compiled dependency.

Cluster ids are kept stable where possible (each new group reuses the existing
cluster it overlaps most), so links to a story survive a re-cluster.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from cluster.embed import to_array
from cluster.online import attach_duplicates, refresh_cluster_stats

NOISE = -1


@dataclass
class Plan:
    # article_id -> target; an int is an existing cluster id, a str "new:<label>" a new cluster
    moves: dict[int, int | str] = field(default_factory=dict)
    merges: dict[int, int] = field(default_factory=dict)  # merged cluster -> survivor
    new_groups: dict[str, list[int]] = field(default_factory=dict)


def plan_recluster(current: dict[int, int | None], labels: dict[int, int]) -> Plan:
    """Turn HDBSCAN labels into moves/merges against the current assignment.

    current: article_id -> cluster id (None if unclustered)
    labels:  article_id -> HDBSCAN label (-1 = noise)
    """
    members: dict[int, list[int]] = defaultdict(list)
    for aid, label in labels.items():
        if label != NOISE:
            members[label].append(aid)

    # overlap[label][cluster] = how many of the label's articles sit in that cluster
    overlap: dict[int, Counter] = {
        label: Counter(current[a] for a in aids if current.get(a) is not None)
        for label, aids in members.items()
    }

    # Each existing cluster "belongs" to the label holding most of its articles.
    home: dict[int, int] = {}
    best: dict[int, tuple[int, int]] = {}
    for label, counts in overlap.items():
        for cid, n in counts.items():
            # Ties go to the lower label so the result is deterministic.
            if cid not in best or (n, -label) > (best[cid][0], -best[cid][1]):
                best[cid] = (n, label)
    for cid, (_, label) in best.items():
        home[cid] = label

    plan = Plan()
    target_of: dict[int, int | str] = {}
    for label in sorted(members):
        homed = [cid for cid, lbl in home.items() if lbl == label]
        if not homed:
            key = f"new:{label}"
            target_of[label] = key
            plan.new_groups[key] = sorted(members[label])
            continue
        # Survivor: the homed cluster with the most articles in this group, then the oldest id.
        survivor = max(homed, key=lambda cid: (overlap[label][cid], -cid))
        target_of[label] = survivor
        for cid in homed:
            if cid != survivor:
                plan.merges[cid] = survivor

    for label, aids in members.items():
        target = target_of[label]
        for aid in aids:
            if current.get(aid) != target:
                plan.moves[aid] = target

    # Noise articles stay put, unless their cluster was merged away.
    for aid, label in labels.items():
        cid = current.get(aid)
        if label == NOISE and cid in plan.merges:
            plan.moves[aid] = plan.merges[cid]
    return plan


def run_hdbscan(vectors: np.ndarray, min_cluster_size: int = 2, epsilon: float = 0.0) -> np.ndarray:
    from sklearn.cluster import HDBSCAN

    if len(vectors) < max(2, min_cluster_size):
        return np.full(len(vectors), NOISE)
    # On unit vectors, Euclidean distance is a monotonic function of cosine
    # distance (d^2 = 2 - 2cos), so this is cosine clustering in effect.
    model = HDBSCAN(
        min_cluster_size=min_cluster_size,
        cluster_selection_epsilon=epsilon,
        metric="euclidean",
        copy=True,
    )
    return model.fit_predict(vectors)


def apply_plan(cur: psycopg.Cursor, plan: Plan) -> dict:
    new_ids: dict[str, int] = {}
    for key in plan.new_groups:
        cur.execute("insert into clusters (label) values (null) returning id")
        new_ids[key] = cur.fetchone()[0]

    touched: set[int] = set()
    for aid, target in plan.moves.items():
        cid = new_ids[target] if isinstance(target, str) else target
        cur.execute(
            """
            insert into cluster_articles (article_id, cluster_id) values (%s, %s)
            on conflict (article_id) do update set cluster_id = excluded.cluster_id,
                                                   added_at = now()
            """,
            (aid, cid),
        )
        touched.add(cid)

    for old, survivor in plan.merges.items():
        # Anything still in the merged cluster (e.g. its wire duplicates) follows.
        cur.execute(
            "update cluster_articles set cluster_id = %s where cluster_id = %s", (survivor, old)
        )
        cur.execute(
            "update clusters set status = 'merged', merged_into = %s where id = %s", (survivor, old)
        )
        touched.add(survivor)

    # Duplicates follow their original wherever it moved.
    cur.execute(
        """
        update cluster_articles d set cluster_id = o.cluster_id
        from articles a join cluster_articles o on o.article_id = a.duplicate_of
        where d.article_id = a.id and d.cluster_id <> o.cluster_id
        returning d.cluster_id
        """
    )
    touched.update(r[0] for r in cur.fetchall())
    touched.update(attach_duplicates(cur))

    # Recompute centroids and labels from members.
    ids = sorted(touched)
    if ids:
        cur.execute(
            """
            update clusters c set
              centroid = s.centroid,
              label = coalesce(c.label, s.label)
            from (
              select ca.cluster_id, avg(a.embedding) as centroid,
                     (array_agg(a.title order by a.published_at nulls last))[1] as label
              from cluster_articles ca join articles a on a.id = ca.article_id
              where ca.cluster_id = any(%s) and a.embedding is not null
              group by ca.cluster_id
            ) s where c.id = s.cluster_id
            """,
            (ids,),
        )
        refresh_cluster_stats(cur, ids)
    # Clusters left with no articles after their articles moved are archived.
    cur.execute(
        """
        update clusters c set status = 'archived'
        where c.status = 'active'
          and not exists (select 1 from cluster_articles ca where ca.cluster_id = c.id)
        """
    )
    return {
        "moved": len(plan.moves),
        "merged": len(plan.merges),
        "new_clusters": len(plan.new_groups),
    }


def run(conn: psycopg.Connection, window_hours: int = 72, min_cluster_size: int = 2,
        epsilon: float = 0.0) -> dict:  # fmt: skip
    register_vector(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            select a.id, a.embedding, ca.cluster_id
            from articles a left join cluster_articles ca on ca.article_id = a.id
            where a.embedding is not null and a.duplicate_of is null
              and a.fetched_at > now() - make_interval(hours => %s)
            order by a.id
            """,
            (window_hours,),
        )
        rows = cur.fetchall()
        if not rows:
            return {"articles": 0, "moved": 0, "merged": 0, "new_clusters": 0}
        ids = [r[0] for r in rows]
        vectors = np.vstack([to_array(r[1]) for r in rows])
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-9)
        labels = run_hdbscan(vectors, min_cluster_size, epsilon)
        plan = plan_recluster(
            {r[0]: r[2] for r in rows}, dict(zip(ids, labels.tolist(), strict=True))
        )
        result = apply_plan(cur, plan)
    return {"articles": len(rows), **result}


def main(argv: list[str] | None = None) -> int:
    from common.db import connect
    from common.runs import pipeline_run

    ap = argparse.ArgumentParser(description="Nightly HDBSCAN re-cluster")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--min-cluster-size", type=int, default=2)
    ap.add_argument("--epsilon", type=float, default=0.0,
                    help="merge clusters closer than this (euclidean on unit vectors)")  # fmt: skip
    args = ap.parse_args(argv)
    with connect() as conn, pipeline_run(conn, "recluster") as stats:
        stats.update(run(conn, args.hours, args.min_cluster_size, args.epsilon))
        print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
