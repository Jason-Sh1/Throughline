"""Integration test: needs TEST_DATABASE_URL pointing at a database with the
migrations applied (CI provides one; locally `make db-test` sets it up)."""

import os

import pytest

from ingest.feeds_config import FeedsFile
from ingest.sync_sources import sync

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def feeds(rating="lean_left", urls=("https://a.example/rss", "https://a.example/tech")):
    return FeedsFile.model_validate(
        {
            "outlets": [
                {
                    "slug": "sync-test-outlet",
                    "name": "Sync Test Outlet",
                    "homepage": "https://a.example",
                    "region": "US",
                    "allsides_rating": rating,
                    "feeds": [{"url": u, "category": "us"} for u in urls],
                }
            ]
        }
    )


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(DB_URL) as c:
        yield c
        with c.cursor() as cur:
            cur.execute("delete from sources where slug = 'sync-test-outlet'")
        c.commit()


def test_sync_is_idempotent_and_deactivates_removed_feeds(conn):
    assert sync(conn, feeds())["feeds"] == 2
    sync(conn, feeds())  # second run must not duplicate
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from feeds f join sources s on s.id = f.source_id "
            "where s.slug = 'sync-test-outlet'"
        )
        assert cur.fetchone()[0] == 2

    stats = sync(conn, feeds(urls=("https://a.example/rss",)))
    assert stats["deactivated"] == 1
    with conn.cursor() as cur:
        cur.execute("select url from feeds where not active and url like 'https://a.example/%'")
        assert [r[0] for r in cur.fetchall()] == ["https://a.example/tech"]


def test_rating_change_updates_timestamp(conn):
    sync(conn, feeds())
    with conn.cursor() as cur:
        cur.execute("select rating_updated_at from sources where slug = 'sync-test-outlet'")
        first = cur.fetchone()[0]
    sync(conn, feeds())
    with conn.cursor() as cur:
        cur.execute("select rating_updated_at from sources where slug = 'sync-test-outlet'")
        assert cur.fetchone()[0] == first  # unchanged rating keeps timestamp
    sync(conn, feeds(rating="center"))
    with conn.cursor() as cur:
        cur.execute(
            "select allsides_rating, rating_updated_at from sources where slug = 'sync-test-outlet'"
        )
        rating, ts = cur.fetchone()
    assert rating == "center" and ts > first
