"""A polite HTTP client shared by all fetchers.

- Identifies itself with a User-Agent that includes a contact address.
- Hard timeouts, so one slow publisher can't stall a run.
- Per-host rate limit: at most one request per `min_interval` seconds to the
  same host, even when that host has many feeds.
- robots.txt is fetched once per host per run and honoured.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def user_agent() -> str:
    contact = os.environ.get("CRAWLER_CONTACT", "").strip()
    suffix = f"; +{contact}" if contact else ""
    return f"ThroughlineBot/0.1 (non-commercial news research{suffix})"


class HostRateLimiter:
    """Blocks until `min_interval` seconds have passed since the last request to a host."""

    def __init__(
        self,
        min_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, min_interval: float | None = None) -> None:
        interval = self.min_interval if min_interval is None else min_interval
        with self._lock:
            now = self._clock()
            ready_at = self._last.get(host, float("-inf")) + interval
            delay = max(0.0, ready_at - now)
            # Reserve the slot before sleeping so concurrent callers queue up.
            self._last[host] = now + delay
        if delay:
            self._sleep(delay)


class PoliteClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        limiter: HostRateLimiter | None = None,
        respect_robots: bool = True,
    ):
        self.client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": user_agent()},
        )
        self.limiter = limiter or HostRateLimiter()
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}
        # Per-host interval overrides (e.g. GDELT asks for >= 5 s between calls).
        self.host_intervals: dict[str, float] = {}

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = None
            try:
                self.limiter.wait(parts.netloc, self.host_intervals.get(parts.netloc))
                resp = self.client.get(f"{origin}/robots.txt")
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    # Standard crawler convention: an access-denied robots.txt means
                    # "don't crawl". A 404 means no rules, so everything is allowed.
                    parser = RobotFileParser()
                    parser.disallow_all = True
            except httpx.HTTPError:
                parser = None  # unreachable robots.txt: treat as no rules
            self._robots[origin] = parser
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        return parser is None or parser.can_fetch(self.client.headers["User-Agent"], url)

    def get(self, url: str, headers: dict[str, str] | None = None, **kw) -> httpx.Response:
        host = urlsplit(url).netloc
        self.limiter.wait(host, self.host_intervals.get(host))
        return self.client.get(url, headers=headers, **kw)

    def close(self) -> None:
        self.client.close()
