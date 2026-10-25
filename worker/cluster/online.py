"""Online clustering: assign each new article to an event as it arrives.

For each new article we compare its embedding to the centroid (average
embedding) of every cluster active in the last `window_hours`:

- similarity >= threshold                  -> join the most similar cluster
- several clusters within `tie_margin`     -> prefer the one sharing more names
- just below threshold (the "gray zone"),
  but sharing >= min_entity_overlap names  -> join anyway
- otherwise                                -> start a new cluster

`threshold` is the main dial: higher means stricter (more, smaller clusters,
risk of splitting one event), lower means looser (fewer, bigger clusters, risk
of merging different events). The entity rule rescues articles that describe
the same event in different words. Tune with `make clusters` (see
docs/decisions.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from cluster.embed import to_array


@dataclass(frozen=True)
class ClusterConfig:
    threshold: float = 0.62
    tie_margin: float = 0.03
    gray_margin: float = 0.10
    min_entity_overlap: int = 2
    window_hours: int = 72

    @classmethod
    def from_env(cls) -> ClusterConfig:
        d = cls()
        return cls(
            threshold=float(os.environ.get("CLUSTER_THRESHOLD", d.threshold)),
            tie_margin=float(os.environ.get("CLUSTER_TIE_MARGIN", d.tie_margin)),
            gray_margin=float(os.environ.get("CLUSTER_GRAY_MARGIN", d.gray_margin)),
            min_entity_overlap=int(
                os.environ.get("CLUSTER_MIN_ENTITY_OVERLAP", d.min_entity_overlap)
            ),
            window_hours=int(os.environ.get("CLUSTER_WINDOW_HOURS", d.window_hours)),
        )


@dataclass
class Candidate:
    id: int
    centroid: np.ndarray  # unit length
    n: int  # members that contributed to the centroid (duplicates excluded)
    entities: set[str] = field(default_factory=set)

    def add(self, vec: np.ndarray, entities: set[str]) -> None:
        """Running mean of member vectors, re-normalized to unit length."""
        mean = (self.centroid * self.n + vec) / (self.n + 1)
        self.centroid = mean / (np.linalg.norm(mean) or 1.0)
        self.n += 1
        self.entities |= entities


@dataclass(frozen=True)
class Decision:
    cluster_id: int | None  # None = start a new cluster
    similarity: float
    reason: str  # 'similarity' | 'tiebreak' | 'entity_overlap' | 'new'


def _unit(v) -> np.ndarray:
    v = to_array(v)
    return v / (np.linalg.norm(v) or 1.0)


def choose_cluster(
    vec: np.ndarray, entities: set[str], candidates: list[Candidate], cfg: ClusterConfig
) -> Decision:
    if not candidates:
        return Decision(None, 0.0, "new")
    vec = _unit(vec)
    scored = sorted(
        ((float(np.dot(vec, c.centroid)), len(entities & c.entities), c) for c in candidates),
        key=lambda t: (-t[0], t[2].id),
    )

    best_sim = scored[0][0]
    if best_sim >= cfg.threshold:
        contenders = [
            t for t in scored if best_sim - t[0] <= cfg.tie_margin and t[0] >= cfg.threshold
        ]
        sim, overlap, cand = max(contenders, key=lambda t: (t[1], t[0]))
        reason = "tiebreak" if cand is not scored[0][2] else "similarity"
        return Decision(cand.id, sim, reason)

    gray = [
        t
        for t in scored
        if t[0] >= cfg.threshold - cfg.gray_margin and t[1] >= cfg.min_entity_overlap
    ]
    if gray:
        sim, _, cand = max(gray, key=lambda t: (t[1], t[0]))
        return Decision(cand.id, sim, "entity_overlap")
    return Decision(None, best_sim, "new")


# ---------------------------------------------------------------------------
# Database runner
# ---------------------------------------------------------------------------


def _load_candidates(cur: psycopg.Cursor, window_hours: int) -> dict[int, Candidate]:
    cur.execute(
        """
        select c.id, c.centroid,
               (select count(*) from cluster_articles ca join articles a on a.id = ca.article_id
                where ca.cluster_id = c.id and a.duplicate_of is null)
        from clusters c
        where c.status = 'active' and c.centroid is not null
          and c.last_updated_at > now() - make_interval(hours => %s)
        """,
        (window_hours,),
    )
    cands = {cid: Candidate(cid, _unit(centroid), max(int(n), 1)) for cid, centroid, n in cur}
    if cands:
        cur.execute(
            """
            select ca.cluster_id, e.normalized_name
            from cluster_articles ca
            join article_entities ae on ae.article_id = ca.article_id
            join entities e on e.id = ae.entity_id
            where ca.cluster_id = any(%s)
            """,
            (list(cands),),
        )
        for cid, name in cur:
            cands[cid].entities.add(name)
    return cands


def _entities_for(cur: psycopg.Cursor, article_ids: list[int]) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {i: set() for i in article_ids}
    cur.execute(
        """
        select ae.article_id, e.normalized_name from article_entities ae
        join entities e on e.id = ae.entity_id where ae.article_id = any(%s)
        """,
        (article_ids,),
    )
    for aid, name in cur:
        out[aid].add(name)
    return out


def refresh_cluster_stats(cur: psycopg.Cursor, cluster_ids: list[int]) -> None:
    if not cluster_ids:
        return
    cur.execute(
        """
        update clusters c set
          article_count = s.n,
          outlet_count = s.outlets,
          category = coalesce(s.category, c.category),
          last_updated_at = greatest(c.last_updated_at, s.latest)
        from (
          select ca.cluster_id, count(*) as n, count(distinct a.source_id) as outlets,
                 max(a.fetched_at) as latest,
                 mode() within group (order by f.category) as category
          from cluster_articles ca
          join articles a on a.id = ca.article_id
          left join feeds f on f.id = a.feed_id
          where ca.cluster_id = any(%s)
          group by ca.cluster_id
        ) s
        where c.id = s.cluster_id
        """,
        (cluster_ids,),
    )


def attach_duplicates(cur: psycopg.Cursor) -> list[int]:
    """Duplicates (wire copies) join their original's cluster.

    Returns one cluster id per attached article."""
    cur.execute(
        """
        insert into cluster_articles (article_id, cluster_id, similarity)
        select a.id, ca.cluster_id, 1.0
        from articles a
        join cluster_articles ca on ca.article_id = a.duplicate_of
        where a.duplicate_of is not null
          and not exists (select 1 from cluster_articles x where x.article_id = a.id)
        returning cluster_id
        """
    )
    return [r[0] for r in cur.fetchall()]


def run_online(conn: psycopg.Connection, cfg: ClusterConfig | None = None) -> dict[str, int]:
    cfg = cfg or ClusterConfig.from_env()
    register_vector(conn)
    stats = {"assigned": 0, "new_clusters": 0, "tiebreaks": 0, "entity_joins": 0, "duplicates": 0}
    touched: set[int] = set()
    with conn.transaction(), conn.cursor() as cur:
        cands = _load_candidates(cur, cfg.window_hours)
        cur.execute(
            """
            select a.id, a.embedding, a.title from articles a
            where a.embedding is not null and a.duplicate_of is null
              and not exists (select 1 from cluster_articles ca where ca.article_id = a.id)
              -- Window on publish time: feeds re-serve months-old items we've only just fetched.
              and coalesce(a.published_at, a.fetched_at) > now() - make_interval(hours => %s)
            order by coalesce(a.published_at, a.fetched_at), a.id
            """,
            (cfg.window_hours,),
        )
        pending = cur.fetchall()
        ents = _entities_for(cur, [p[0] for p in pending]) if pending else {}

        for article_id, embedding, title in pending:
            vec = _unit(embedding)
            decision = choose_cluster(vec, ents[article_id], list(cands.values()), cfg)
            if decision.cluster_id is None:
                cur.execute(
                    "insert into clusters (label, centroid) values (%s, %s) returning id",
                    (title[:200], vec),
                )
                cid = cur.fetchone()[0]
                cands[cid] = Candidate(cid, vec, 1, set(ents[article_id]))
                stats["new_clusters"] += 1
                sim = 1.0
            else:
                cid = decision.cluster_id
                cands[cid].add(vec, ents[article_id])
                sim = decision.similarity
                stats["tiebreaks"] += decision.reason == "tiebreak"
                stats["entity_joins"] += decision.reason == "entity_overlap"
            cur.execute(
                "insert into cluster_articles (article_id, cluster_id, similarity) "
                "values (%s, %s, %s)",
                (article_id, cid, sim),
            )
            touched.add(cid)
            stats["assigned"] += 1

        for cid in touched:
            cur.execute(
                "update clusters set centroid = %s where id = %s", (cands[cid].centroid, cid)
            )
        dup_clusters = attach_duplicates(cur)
        stats["duplicates"] = len(dup_clusters)
        touched.update(dup_clusters)  # set: repeats collapse
        refresh_cluster_stats(cur, sorted(touched))
    return stats
