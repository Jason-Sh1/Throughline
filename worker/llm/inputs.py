"""Gather the articles of one cluster into the source list the LLMs see.

Each source gets a short local id ("S1".."S12"). Wire copies (rows with
`duplicate_of`) are skipped: they add an outlet to the coverage count but no new
text. We keep at most MAX_SOURCES articles, taking one per outlet first and
rotating across AllSides ratings, so a story covered by 20 left-leaning and 3
right-leaning outlets still shows the model both sides' framing.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import psycopg

MAX_SOURCES = 12
EXCERPT_CHARS = 1200

# URL path segments that mark analysis/opinion rather than straight news.
_OPINION_PATH = re.compile(
    r"/(opinion|opinions|commentary|editorials?|columns?|columnists?|op-ed|analysis|views?|"
    r"perspectives?|ideas|blogs?)(/|$)",
    re.I,
)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class SourceDoc:
    local_id: str
    article_id: int
    outlet: str
    allsides_rating: str
    title: str
    url: str
    published_at: datetime | None
    excerpt: str
    is_opinion: bool


def clean_text(text: str | None) -> str:
    """RSS summaries often carry HTML and entities; the model wants plain text.
    Entities are decoded *before* tags are stripped, so an escaped tag in a feed
    (`&lt;/sources&gt;`) is removed like a real one instead of becoming one."""
    if not text:
        return ""
    return _SPACE.sub(" ", _TAG.sub(" ", html.unescape(text))).strip()


def is_opinion(url: str) -> bool:
    return bool(_OPINION_PATH.search(url))


# Sort key for articles without a publish time (feeds sometimes omit it).
# Must be timezone-aware: timestamptz rows come back aware.
NEVER = datetime.min.replace(tzinfo=UTC)


def pick_rows(rows: list[dict], limit: int = MAX_SOURCES) -> list[dict]:
    """One article per outlet (newest first), rotating across ratings so no side
    of the spectrum crowds out the others, then fill up with second articles."""
    rows = sorted(rows, key=lambda r: r["published_at"] or NEVER, reverse=True)
    first, rest, seen = [], [], set()
    for r in rows:
        (rest if r["source_id"] in seen else first).append(r)
        seen.add(r["source_id"])
    by_rating: dict[str, list[dict]] = {}
    for r in first:
        by_rating.setdefault(r["allsides_rating"], []).append(r)
    picked: list[dict] = []
    while len(picked) < limit and any(by_rating.values()):
        for rating in sorted(by_rating):
            if by_rating[rating] and len(picked) < limit:
                picked.append(by_rating[rating].pop(0))
    picked += rest[: limit - len(picked)]
    return picked


def load_cluster_rows(conn: psycopg.Connection, cluster_id: int) -> list[dict]:
    """All articles in the cluster, wire copies included (for coverage counts)."""
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, a.source_id, s.name, s.allsides_rating::text, a.title, a.url,
                      a.published_at, a.summary, a.raw_text, a.duplicate_of, s.kind
               from cluster_articles ca
               join articles a on a.id = ca.article_id
               join sources s on s.id = a.source_id
               where ca.cluster_id = %s
               order by a.id""",
            (cluster_id,),
        )
        cols = ["id", "source_id", "outlet", "allsides_rating", "title", "url",
                "published_at", "summary", "raw_text", "duplicate_of", "kind"]  # fmt: skip
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _excerpt(r: dict) -> str:
    excerpt = clean_text(r["summary"])
    body = clean_text(r["raw_text"])
    if body and body[:200] not in excerpt:
        excerpt = f"{excerpt}\n{body}" if excerpt else body
    return excerpt[:EXCERPT_CHARS]


def _doc(i: int, r: dict) -> SourceDoc:
    return SourceDoc(
        local_id=f"S{i}",
        article_id=r["id"],
        outlet=r["outlet"],
        allsides_rating=r["allsides_rating"],
        title=clean_text(r["title"]),
        url=r["url"],
        published_at=r["published_at"],
        excerpt=_excerpt(r),
        is_opinion=is_opinion(r["url"]),
    )


def build_sources(rows: list[dict], limit: int = MAX_SOURCES) -> list[SourceDoc]:
    originals = [r for r in rows if r["duplicate_of"] is None]
    return [_doc(i, r) for i, r in enumerate(pick_rows(originals, limit), start=1)]


def docs_for_articles(conn: psycopg.Connection, article_ids: list[int]) -> list[SourceDoc]:
    """Rebuild the exact source list a stored card was written from (same order,
    so local ids match: the card's sources[i] was "S{i+1}"). Used by verification."""
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, s.name, s.allsides_rating::text, a.title, a.url, a.published_at,
                      a.summary, a.raw_text
               from articles a join sources s on s.id = a.source_id where a.id = any(%s)""",
            (article_ids,),
        )
        cols = ["id", "outlet", "allsides_rating", "title", "url", "published_at", "summary",
                "raw_text"]  # fmt: skip
        by_id = {row[0]: dict(zip(cols, row, strict=True)) for row in cur.fetchall()}
    return [_doc(i, by_id[a]) for i, a in enumerate(article_ids, start=1) if a in by_id]


def _esc(text: str) -> str:
    """Article text is untrusted. Escaping angle brackets means nothing inside a
    headline or excerpt can close the <source> tag or open a new block."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_sources(docs: list[SourceDoc]) -> str:
    """The source list as the models see it."""
    parts = []
    for d in docs:
        when = d.published_at.strftime("%Y-%m-%d %H:%M UTC") if d.published_at else "unknown"
        kind = "opinion/analysis" if d.is_opinion else "news"
        outlet = _esc(d.outlet).replace('"', "&quot;")
        parts.append(
            f'<source id="{d.local_id}" outlet="{outlet}" published="{when}" type="{kind}">\n'
            f"Headline: {_esc(d.title)}\n{_esc(d.excerpt)}\n</source>"
        )
    return "\n\n".join(parts)


def content_hash(docs: list[SourceDoc], *parts: str) -> str:
    """Fingerprint of the inputs plus prompt version and model: if none of them
    changed, the cached extraction or card is reused instead of paying again."""
    payload = json.dumps(
        [[d.article_id, d.title, d.excerpt] for d in docs] + list(parts), sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def source_map(docs: list[SourceDoc]) -> dict[str, int]:
    return {d.local_id: d.article_id for d in docs}


def docs_to_json(docs: list[SourceDoc]) -> list[dict]:
    return [asdict(d) | {"published_at": d.published_at and d.published_at.isoformat()}
            for d in docs]  # fmt: skip
