"""Plain data passed between fetchers and the article store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ArticleIn:
    source_id: int
    origin: str  # 'rss' | 'gdelt' | 'guardian'
    url: str
    title: str
    published_at: datetime | None = None
    summary: str | None = None
    raw_text: str | None = None
    language: str | None = None
    feed_id: int | None = None
