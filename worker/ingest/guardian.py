"""Guardian Open Platform fetcher.

The Guardian's developer key allows non-commercial use and returns full article
text (`bodyText`). We store it as internal `raw_text` for background/explainer
generation; it's never shown to users and is purged after 14 days.

Rate limits are read from the response headers rather than hard-coded, since
they depend on the key tier:
    X-RateLimit-Remaining-minute, X-RateLimit-Remaining-day
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from ingest.http import PoliteClient
from ingest.models import ArticleIn

API = "https://content.guardianapis.com/search"
PAGE_SIZE = 50
MAX_PAGES = 5
# Leave headroom in the daily quota for manual debugging and background lookups.
DAILY_RESERVE = 50


def build_url(api_key: str, since: datetime, page: int) -> str:
    params = {
        "api-key": api_key,
        "from-date": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "order-by": "newest",
        "page-size": str(PAGE_SIZE),
        "page": str(page),
        "show-fields": "trailText,bodyText",
    }
    return f"{API}?{urlencode(params)}"


def _int_header(resp: httpx.Response, name: str) -> int | None:
    try:
        return int(resp.headers[name])
    except (KeyError, ValueError):
        return None


def parse_response(data: dict, source_id: int) -> tuple[list[ArticleIn], int]:
    """Returns (articles, total pages)."""
    body = data.get("response", {})
    out = []
    for item in body.get("results", []):
        fields = item.get("fields", {})
        published = item.get("webPublicationDate")
        out.append(
            ArticleIn(
                source_id=source_id,
                origin="guardian",
                url=item["webUrl"],
                title=item["webTitle"],
                summary=fields.get("trailText"),
                raw_text=fields.get("bodyText") or None,
                published_at=(
                    datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None
                ),
                language="en",
            )
        )
    return out, int(body.get("pages", 1))


def fetch(
    client: PoliteClient,
    source_id: int,
    since: datetime | None = None,
    api_key: str | None = None,
) -> tuple[list[ArticleIn], list[str]]:
    api_key = api_key or os.environ.get("GUARDIAN_API_KEY")
    if not api_key:
        return [], ["guardian: GUARDIAN_API_KEY not set, skipped"]
    since = since or datetime.now(UTC) - timedelta(hours=6)

    articles: list[ArticleIn] = []
    errors: list[str] = []
    page = 1
    while page <= MAX_PAGES:
        try:
            resp = client.get(build_url(api_key, since, page))
        except httpx.HTTPError as exc:
            # Type name only: some httpx errors include the request URL, which
            # carries the API key, and these errors end up in CI logs.
            errors.append(f"guardian page {page}: {type(exc).__name__}")
            break
        if resp.status_code == 429:
            errors.append("guardian: rate limited (429)")
            break
        if resp.status_code != 200:
            errors.append(f"guardian page {page}: HTTP {resp.status_code}")
            break

        found, pages = parse_response(resp.json(), source_id)
        articles.extend(found)

        remaining_day = _int_header(resp, "X-RateLimit-Remaining-day")
        remaining_min = _int_header(resp, "X-RateLimit-Remaining-minute")
        if remaining_day is not None and remaining_day <= DAILY_RESERVE:
            errors.append(f"guardian: stopping, only {remaining_day} requests left today")
            break
        if remaining_min is not None and remaining_min <= 1:
            # Back off for the rest of the minute rather than risk a 429.
            client.limiter.wait("content.guardianapis.com", 60.0)
        if page >= pages:
            break
        page += 1
    return articles, errors
