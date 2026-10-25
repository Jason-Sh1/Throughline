"""Write fetched articles to Postgres, applying the three dedupe layers
from cluster/dedupe.py."""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from cluster.dedupe import (
    SIMHASH_MAX_DISTANCE,
    canonicalize_url,
    hamming,
    simhash,
    simhash_text,
    title_hash,
)
from ingest.models import ArticleIn

DEDUPE_WINDOW_HOURS = 72


@dataclass
class StoreStats:
    inserted: int = 0
    url_dupes: int = 0
    title_dupes: int = 0
    near_dupes: int = 0
    new_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "url_dupes": self.url_dupes,
            "title_dupes": self.title_dupes,
            "near_dupes": self.near_dupes,
        }


@dataclass
class _Recent:
    id: int
    title_hash: str
    simhash: int | None


def _load_recent(cur: psycopg.Cursor, hours: int) -> list[_Recent]:
    cur.execute(
        """
        select id, title_hash, simhash from articles
        where duplicate_of is null and fetched_at > now() - make_interval(hours => %s)
        order by id
        """,
        (hours,),
    )
    return [_Recent(*row) for row in cur.fetchall()]


def find_original(recent: list[_Recent], th: str, sh: int | None) -> tuple[int | None, str | None]:
    """Earliest recent article this one duplicates, and which layer matched."""
    for r in recent:
        if r.title_hash == th:
            return r.id, "title"
    if sh is not None:
        for r in recent:
            if r.simhash is not None and hamming(r.simhash, sh) <= SIMHASH_MAX_DISTANCE:
                return r.id, "simhash"
    return None, None


def store_articles(
    conn: psycopg.Connection, articles: list[ArticleIn], window_hours: int = DEDUPE_WINDOW_HOURS
) -> StoreStats:
    stats = StoreStats()
    with conn.transaction(), conn.cursor() as cur:
        recent = _load_recent(cur, window_hours)
        for a in articles:
            canon = canonicalize_url(a.url)
            th = title_hash(a.title)
            text = simhash_text(a.title, a.summary)
            sh = simhash(text) if text else None

            # The same URL can arrive from RSS and later from the Guardian API with
            # full text; keep the first row and fill in anything it was missing.
            cur.execute(
                """
                insert into articles (source_id, feed_id, origin, url, canonical_url, title,
                                      title_hash, simhash, summary, raw_text, language,
                                      published_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (canonical_url) do update set
                  summary = coalesce(articles.summary, excluded.summary),
                  raw_text = case when articles.raw_text_purged_at is null
                                  then coalesce(articles.raw_text, excluded.raw_text) end,
                  published_at = coalesce(articles.published_at, excluded.published_at)
                returning id, (xmax = 0) as inserted
                """,
                (a.source_id, a.feed_id, a.origin, a.url, canon, a.title, th, sh, a.summary,
                 a.raw_text, a.language, a.published_at),
            )  # fmt: skip
            article_id, inserted = cur.fetchone()
            if not inserted:
                stats.url_dupes += 1
                continue

            stats.inserted += 1
            stats.new_ids.append(article_id)
            original, layer = find_original(recent, th, sh)
            if original is not None:
                cur.execute(
                    "update articles set duplicate_of = %s where id = %s", (original, article_id)
                )
                if layer == "title":
                    stats.title_dupes += 1
                else:
                    stats.near_dupes += 1
            else:
                recent.append(_Recent(article_id, th, sh))
    return stats
