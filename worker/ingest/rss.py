"""RSS/Atom fetcher with conditional GET.

We send the ETag / Last-Modified values from the previous fetch; an unchanged
feed answers 304 with no body, which is cheap for both sides.
"""

from __future__ import annotations

import calendar
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

from ingest.http import PoliteClient
from ingest.models import ArticleIn

# Keep the RSS description short: it's only used for embeddings/dedupe and as
# context for the LLM, never displayed in full.
MAX_SUMMARY_CHARS = 1000


@dataclass
class FeedRow:
    id: int
    source_id: int
    url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class FeedResult:
    feed_id: int
    status: int | None
    etag: str | None
    last_modified: str | None
    articles: list[ArticleIn] = field(default_factory=list)
    error: str | None = None


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:MAX_SUMMARY_CHARS] or None


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            # feedparser gives a UTC struct_time; timegm interprets it as UTC.
            return datetime.fromtimestamp(calendar.timegm(t), tz=UTC)
    return None


def parse_feed(body: bytes, feed: FeedRow) -> list[ArticleIn]:
    parsed = feedparser.parse(body)
    out = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = _clean(entry.get("title"))
        if not link or not title:
            continue
        out.append(
            ArticleIn(
                source_id=feed.source_id,
                feed_id=feed.id,
                origin="rss",
                url=link,
                title=title,
                summary=_clean(entry.get("summary")),
                published_at=_entry_time(entry),
                language=parsed.feed.get("language"),
            )
        )
    return out


def fetch_feed(client: PoliteClient, feed: FeedRow) -> FeedResult:
    if not client.allowed(feed.url):
        return FeedResult(
            feed.id, None, feed.etag, feed.last_modified, error="robots.txt disallows"
        )

    headers = {}
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified
    try:
        resp = client.get(feed.url, headers=headers)
    except httpx.HTTPError as exc:
        return FeedResult(feed.id, None, feed.etag, feed.last_modified, error=repr(exc))

    if resp.status_code == 304:
        return FeedResult(feed.id, 304, feed.etag, feed.last_modified)
    if resp.status_code != 200:
        return FeedResult(
            feed.id,
            resp.status_code,
            feed.etag,
            feed.last_modified,
            error=f"HTTP {resp.status_code}",
        )
    return FeedResult(
        feed_id=feed.id,
        status=200,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
        articles=parse_feed(resp.content, feed),
    )
