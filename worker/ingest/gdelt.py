"""GDELT 2.0 DOC API fetcher (breaking-news radar).

GDELT indexes worldwide news every 15 minutes. We ask it for articles from
outlets we already track, which catches stories before a slow RSS feed
updates. Articles from untracked outlets are skipped so every article in the
database has a known outlet and spectrum label.

API docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
GDELT asks clients to wait at least 5 seconds between requests.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

import httpx

from ingest.http import PoliteClient
from ingest.models import ArticleIn

API = "https://api.gdeltproject.org/api/v2/doc/doc"
HOST = "api.gdeltproject.org"
MIN_INTERVAL_SECONDS = 5.0
# Long OR-queries get rejected, so we ask about a few outlets per request.
DOMAINS_PER_QUERY = 8
# GDELT's TLS handshake is often slow (it timed out at our 5 s default in the
# first live run), so give it longer and retry transport errors once.
TIMEOUT = httpx.Timeout(30.0, connect=20.0)
ATTEMPTS = 2


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def build_query(domains: Iterable[str]) -> str:
    ors = " OR ".join(f"domainis:{d}" for d in domains)
    return f"({ors}) sourcelang:english"


def build_url(domains: Iterable[str], timespan: str = "60min") -> str:
    params = {
        "query": build_query(domains),
        "mode": "ArtList",
        "format": "json",
        "maxrecords": "250",
        "timespan": timespan,
        "sort": "DateDesc",
    }
    return f"{API}?{urlencode(params)}"


def parse_seendate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_response(body: str, source_by_domain: dict[str, int]) -> tuple[list[ArticleIn], int]:
    """Returns (articles from tracked outlets, count skipped as untracked).

    GDELT answers some bad queries with a plain-text message instead of JSON;
    that raises ValueError so the caller can log it.
    """
    try:
        data = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"GDELT returned non-JSON: {body[:200]!r}") from exc

    articles, skipped = [], 0
    for item in data.get("articles", []):
        source_id = source_by_domain.get((item.get("domain") or "").lower().removeprefix("www."))
        if source_id is None or not item.get("url") or not item.get("title"):
            skipped += 1
            continue
        articles.append(
            ArticleIn(
                source_id=source_id,
                origin="gdelt",
                url=item["url"],
                title=item["title"].strip(),
                published_at=parse_seendate(item.get("seendate")),
                language=(item.get("language") or "").lower()[:2] or None,
            )
        )
    return articles, skipped


def fetch(
    client: PoliteClient, source_by_domain: dict[str, int], timespan: str = "60min"
) -> tuple[list[ArticleIn], list[str]]:
    """Query GDELT for every tracked domain. Returns (articles, errors)."""
    client.host_intervals[HOST] = MIN_INTERVAL_SECONDS
    domains = sorted(source_by_domain)
    articles: list[ArticleIn] = []
    errors: list[str] = []
    for i in range(0, len(domains), DOMAINS_PER_QUERY):
        chunk = domains[i : i + DOMAINS_PER_QUERY]
        for attempt in range(1, ATTEMPTS + 1):
            try:
                resp = client.get(build_url(chunk, timespan), timeout=TIMEOUT)
                resp.raise_for_status()
                found, _ = parse_response(resp.text, source_by_domain)
                articles.extend(found)
                break
            except httpx.TransportError as exc:
                if attempt == ATTEMPTS:
                    errors.append(f"gdelt {chunk[0]}..: {exc!r}")
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"gdelt {chunk[0]}..: {exc}")
                break
    return articles, errors
