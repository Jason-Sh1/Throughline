import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ingest import gdelt, guardian
from ingest.http import HostRateLimiter, PoliteClient
from ingest.rss import FeedRow, fetch_feed, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"
FEED_XML = (FIXTURES / "sample_feed.xml").read_bytes()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def client_for(handler, robots=True) -> PoliteClient:
    fake = FakeClock()
    return PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "test"}),
        limiter=HostRateLimiter(0.0, clock=fake.clock, sleep=fake.sleep),
        respect_robots=robots,
    )


# --- rate limiter -------------------------------------------------------------


def test_rate_limiter_spaces_requests_per_host():
    fake = FakeClock()
    rl = HostRateLimiter(2.0, clock=fake.clock, sleep=fake.sleep)
    rl.wait("a.example")
    rl.wait("b.example")  # different host: no wait
    rl.wait("a.example")
    assert fake.slept == [2.0]
    fake.now += 5
    rl.wait("a.example")  # enough time passed
    assert fake.slept == [2.0]


def test_rate_limiter_per_call_override():
    fake = FakeClock()
    rl = HostRateLimiter(1.0, clock=fake.clock, sleep=fake.sleep)
    rl.wait("gdelt", 5.0)
    rl.wait("gdelt", 5.0)
    assert fake.slept == [5.0]


# --- robots.txt ---------------------------------------------------------------


def test_robots_disallow_blocks_feed():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, content=FEED_XML)

    c = client_for(handler)
    assert not c.allowed("https://news.example/private/rss")
    assert c.allowed("https://news.example/rss")
    r = fetch_feed(c, FeedRow(1, 1, "https://news.example/private/rss"))
    assert r.error == "robots.txt disallows" and not r.articles


@pytest.mark.parametrize("status,allowed", [(404, True), (403, False), (500, True)])
def test_robots_status_handling(status, allowed):
    c = client_for(lambda req: httpx.Response(status))
    assert c.allowed("https://news.example/rss") is allowed


def test_robots_fetched_once_per_host():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(404)

    c = client_for(handler)
    c.allowed("https://news.example/a")
    c.allowed("https://news.example/b")
    assert calls.count("/robots.txt") == 1


# --- RSS ----------------------------------------------------------------------


def test_parse_feed():
    items = parse_feed(FEED_XML, FeedRow(id=7, source_id=3, url="https://news.example/rss"))
    assert [a.title for a in items] == [
        "Senate passes & sends funding bill to president",
        "Governor signs housing law",
    ]
    first, second = items
    assert first.summary == "The Senate voted 68-31 on Tuesday to pass a stopgap funding bill."
    assert first.published_at == datetime(2026, 10, 21, 22, 15, tzinfo=UTC)
    assert second.published_at == datetime(2026, 10, 21, 22, 0, tzinfo=UTC)  # -0400 -> UTC
    assert first.feed_id == 7 and first.source_id == 3 and first.origin == "rss"
    assert first.language == "en-us"


def test_conditional_get_sends_validators_and_handles_304():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(304)

    feed = FeedRow(1, 1, "https://news.example/rss", etag='"abc"', last_modified="Tue, 21 Oct")
    r = fetch_feed(client_for(handler, robots=False), feed)
    assert seen["if-none-match"] == '"abc"' and seen["if-modified-since"] == "Tue, 21 Oct"
    assert r.status == 304 and r.articles == [] and r.etag == '"abc"' and r.error is None


def test_200_stores_new_validators():
    def handler(req):
        return httpx.Response(
            200, content=FEED_XML, headers={"ETag": '"v2"', "Last-Modified": "Wed, 22 Oct"}
        )

    r = fetch_feed(client_for(handler, robots=False), FeedRow(1, 1, "https://news.example/rss"))
    assert r.status == 200 and r.etag == '"v2"' and r.last_modified == "Wed, 22 Oct"
    assert len(r.articles) == 2


def test_http_error_and_timeout_are_reported_not_raised():
    r = fetch_feed(
        client_for(lambda req: httpx.Response(503), robots=False), FeedRow(1, 1, "https://x.test/")
    )
    assert r.status == 503 and r.error == "HTTP 503"

    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    r = fetch_feed(client_for(boom, robots=False), FeedRow(1, 1, "https://x.test/"))
    assert r.status is None and "ReadTimeout" in r.error


# --- GDELT --------------------------------------------------------------------

GDELT_BODY = json.dumps(
    {
        "articles": [
            {"url": "https://www.npr.org/2026/10/21/x", "title": "Story A ", "domain": "npr.org",
             "seendate": "20261021T221500Z", "language": "English"},
            {"url": "https://unknown.example/y", "title": "Story B", "domain": "unknown.example",
             "seendate": "20261021T221500Z", "language": "English"},
        ]
    }
)  # fmt: skip


def test_gdelt_query_and_url():
    assert (
        gdelt.build_query(["a.com", "b.org"])
        == "(domainis:a.com OR domainis:b.org) sourcelang:english"
    )
    url = gdelt.build_url(["a.com"], "15min")
    assert "mode=ArtList" in url and "format=json" in url and "timespan=15min" in url


def test_gdelt_parse_keeps_only_tracked_outlets():
    articles, skipped = gdelt.parse_response(GDELT_BODY, {"npr.org": 5})
    assert skipped == 1
    (a,) = articles
    assert a.source_id == 5 and a.origin == "gdelt" and a.title == "Story A"
    assert a.published_at == datetime(2026, 10, 21, 22, 15, tzinfo=UTC) and a.language == "en"


def test_gdelt_non_json_raises():
    with pytest.raises(ValueError):
        gdelt.parse_response("Your search contained a phrase that was too short.", {})


def test_gdelt_fetch_chunks_domains_and_collects_errors():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if len(calls) == 2:
            return httpx.Response(200, text="Invalid query")
        return httpx.Response(200, text=GDELT_BODY)

    domains = {f"site{i}.com": i for i in range(gdelt.DOMAINS_PER_QUERY + 1)} | {"npr.org": 99}
    c = client_for(handler, robots=False)
    articles, errors = gdelt.fetch(c, domains)
    assert len(calls) == 2
    assert len(errors) == 1 and "non-JSON" in errors[0]
    assert c.host_intervals[gdelt.HOST] == gdelt.MIN_INTERVAL_SECONDS


def test_gdelt_retries_transport_errors_once():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("handshake timed out", request=req)
        return httpx.Response(200, text=GDELT_BODY)

    articles, errors = gdelt.fetch(client_for(handler, robots=False), {"npr.org": 5})
    assert len(calls) == 2 and len(articles) == 1 and errors == []


def test_gdelt_gives_up_after_second_transport_error():
    def handler(req):
        raise httpx.ConnectTimeout("handshake timed out", request=req)

    articles, errors = gdelt.fetch(client_for(handler, robots=False), {"npr.org": 5})
    assert articles == [] and "ConnectTimeout" in errors[0]


def test_gdelt_stops_after_rate_limit():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, text="Too Many Requests")

    domains = {f"site{i}.com": i for i in range(gdelt.DOMAINS_PER_QUERY * 3)}
    articles, errors = gdelt.fetch(client_for(handler, robots=False), domains)
    assert len(calls) == 1
    assert errors == [
        "gdelt site0.com..: HTTP 429",
        "gdelt: rate limited, skipped remaining queries",
    ]


# --- Guardian -----------------------------------------------------------------


def guardian_page(page, pages, headers=None):
    body = {
        "response": {
            "status": "ok",
            "pages": pages,
            "results": [
                {
                    "webUrl": f"https://www.theguardian.com/world/p{page}",
                    "webTitle": f"Guardian headline {page}",
                    "webPublicationDate": "2026-10-21T20:00:00Z",
                    "fields": {"trailText": "Trail", "bodyText": "Full body text"},
                }
            ],
        }
    }
    return httpx.Response(200, json=body, headers=headers or {})


def test_guardian_skips_without_key(monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    articles, errors = guardian.fetch(client_for(lambda r: httpx.Response(500)), source_id=1)
    assert articles == [] and "not set" in errors[0]


def test_guardian_pages_and_parses():
    def handler(req):
        page = int(req.url.params["page"])
        assert req.url.params["show-fields"] == "trailText,bodyText"
        return guardian_page(page, pages=2)

    articles, errors = guardian.fetch(client_for(handler, robots=False), 4, api_key="k")
    assert [a.title for a in articles] == ["Guardian headline 1", "Guardian headline 2"]
    assert articles[0].raw_text == "Full body text" and articles[0].origin == "guardian"
    assert articles[0].published_at == datetime(2026, 10, 21, 20, tzinfo=UTC)
    assert errors == []


def test_guardian_stops_near_daily_quota():
    def handler(req):
        return guardian_page(1, pages=5, headers={"X-RateLimit-Remaining-day": "10"})

    articles, errors = guardian.fetch(client_for(handler, robots=False), 4, api_key="k")
    assert len(articles) == 1 and "only 10 requests left" in errors[0]


def test_guardian_backs_off_when_minute_quota_low():
    fake = FakeClock()
    pages = iter([1, 2])

    def handler(req):
        return guardian_page(next(pages), 2, headers={"X-RateLimit-Remaining-minute": "1"})

    c = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        limiter=HostRateLimiter(0.0, clock=fake.clock, sleep=fake.sleep),
        respect_robots=False,
    )
    guardian.fetch(c, 4, api_key="k")
    assert any(s >= 59 for s in fake.slept)
