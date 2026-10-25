"""Cheap named-entity guesses for the clustering tiebreak.

Until LLM extraction lands in M3, we approximate entities as runs of
capitalized words ("Federal Reserve", "Jerome Powell", "Gaza"). It's crude,
but the tiebreak only needs "do these two stories mention the same names?",
and a false entity like "Breaking" is filtered by the stopword list. M3's
extraction step will replace these rows with real typed entities.
"""

from __future__ import annotations

import re

import psycopg

_STOP = {
    # sentence starters and headline filler that are capitalized but not names
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "at", "to", "of", "for",
    "by", "with", "from", "as", "is", "are", "was", "were", "be", "it", "its", "this",
    "that", "these", "those", "he", "she", "they", "we", "you", "i", "his", "her",
    "their", "our", "my", "after", "before", "over", "under", "new", "how", "why",
    "what", "who", "when", "where", "will", "can", "could", "would", "should", "may",
    "says", "said", "say", "breaking", "live", "update", "updates", "watch", "video",
    "opinion", "analysis", "exclusive", "report", "news", "here", "there", "just",
    "more", "first", "last", "top", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "today", "yesterday", "tomorrow", "mr", "ms", "mrs",
}  # fmt: skip

_TOKEN = re.compile(r"[A-Z][\w'’.-]*[\w]|[A-Z]")


def _flush(run: list[str], counts: dict[str, int]) -> None:
    if run:
        name = " ".join(run)
        if len(name) > 2 or name.isupper():  # keep acronyms like "EU", "UN"
            key = name.lower()
            counts[key] = counts.get(key, 0) + 1
        run.clear()


def extract(text: str, max_entities: int = 15) -> list[str]:
    """Return normalized candidate entity names, most frequent first."""
    counts: dict[str, int] = {}
    # Split into sentences (a period after 2+ lowercase letters, so "U.S." survives);
    # within one, collect runs of capitalized tokens.
    for sentence in re.split(r"(?<=[a-z0-9]{2})[.!?]\s+|[:;\n]+\s*", text):
        run: list[str] = []
        for word in sentence.split():
            word = word.strip("\"'“”‘’()[],")
            if re.fullmatch(r"(?:[A-Z]\.){2,}", word):
                word = word.replace(".", "")  # U.S. -> US
            word = word.rstrip(".")
            possessive = word.endswith(("'s", "’s"))
            if possessive:
                word = word[:-2]
            if word and _TOKEN.fullmatch(word) and word.lower() not in _STOP:
                run.append(word)
                if possessive:  # "Fed's Jerome Powell" is two names
                    _flush(run, counts)
            else:
                _flush(run, counts)
        _flush(run, counts)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:max_entities]]


def save_for_articles(conn: psycopg.Connection, article_ids: list[int]) -> int:
    """Extract and store entities for the given articles. Returns rows written."""
    if not article_ids:
        return 0
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "select id, title, coalesce(summary, '') from articles where id = any(%s)",
            (article_ids,),
        )
        for article_id, title, summary in cur.fetchall():
            for name in extract(f"{title}. {summary}"):
                cur.execute(
                    """
                    insert into entities (name, normalized_name, kind) values (%s, %s, 'other')
                    on conflict (normalized_name, kind) do update set name = entities.name
                    returning id
                    """,
                    (name, name),
                )
                entity_id = cur.fetchone()[0]
                cur.execute(
                    """
                    insert into article_entities (article_id, entity_id) values (%s, %s)
                    on conflict do nothing
                    """,
                    (article_id, entity_id),
                )
                written += cur.rowcount
    return written
