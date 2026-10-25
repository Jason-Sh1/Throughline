"""End-to-end pipeline tests against a real Postgres + pgvector (TEST_DATABASE_URL).

A fake embedder stands in for sentence-transformers: it maps each article to a
fixed direction per topic word, plus a little deterministic noise, so we can
control which articles "mean the same thing".
"""

import hashlib
from datetime import UTC, datetime, timedelta

import numpy as np

from cluster import entities
from cluster.embed import embed_pending
from cluster.online import ClusterConfig, run_online
from cluster.recluster import run as recluster
from ingest.models import ArticleIn
from ingest.purge import purge
from ingest.store import store_articles

TOPICS = ["fed", "storm", "election", "quake"]


class FakeEmbedder:
    def encode(self, texts):
        out = []
        for t in texts:
            v = np.zeros(384, dtype=np.float32)
            for i, topic in enumerate(TOPICS):
                if topic in t.lower():
                    v[i] = 1.0
            seed = int.from_bytes(hashlib.sha1(t.encode()).digest()[:4], "big")
            v += np.random.default_rng(seed).normal(0, 0.01, 384).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.vstack(out)


def make_sources(cur, n=4):
    ids = []
    for i in range(n):
        cur.execute(
            "insert into sources (slug, name, homepage_url, region, allsides_rating) "
            "values (%s, %s, %s, 'US', %s) returning id",
            (f"outlet-{i}", f"Outlet {i}", f"https://outlet{i}.example",
             ["left", "center", "right", "not_rated"][i % 4]),
        )  # fmt: skip
        ids.append(cur.fetchone()[0])
    return ids


def art(source_id, slug, title, summary=None, **kw):
    return ArticleIn(source_id=source_id, origin="rss", url=f"https://outlet.example/{slug}",
                     title=title, summary=summary,
                     published_at=kw.get("published_at", datetime.now(UTC)))  # fmt: skip


WIRE = (
    "The Federal Reserve left its benchmark interest rate unchanged on Wednesday, saying "
    "inflation remains elevated even as hiring has slowed across most industries."
)


def test_store_dedupes_url_title_and_simhash(db):
    with db.cursor() as cur:
        s = make_sources(cur)
    db.commit()
    stats = store_articles(db, [
        art(s[0], "fed-1", "Fed holds rates steady", WIRE),
        art(s[0], "fed-1?utm_source=x", "Fed holds rates steady", WIRE),          # same URL
        art(s[1], "fed-copy", "Fed Holds Rates Steady - Outlet 1", "Different text."),  # title
        art(s[2], "fed-wire", "Fed holds rates as prices rise", WIRE + " Up."),  # simhash
        art(s[3], "storm", "Storm hits plains", "Heavy snow closed highways."),
    ])  # fmt: skip
    assert stats.as_dict() == {"inserted": 4, "url_dupes": 1, "title_dupes": 1, "near_dupes": 1}
    with db.cursor() as cur:
        cur.execute("select count(*) from articles where duplicate_of = 1")
        assert cur.fetchone()[0] == 2


def test_store_near_dupe_needs_similar_headline(db):
    # Same boilerplate-heavy summary, unrelated headlines: not a duplicate.
    with db.cursor() as cur:
        s = make_sources(cur, 2)
    db.commit()
    stats = store_articles(db, [
        art(s[0], "a", "Fed holds rates steady", WIRE),
        art(s[1], "b", "Storm closes highways across the plains", WIRE),
    ])  # fmt: skip
    assert stats.near_dupes == 0


def test_store_backfills_raw_text_but_not_after_purge(db):
    with db.cursor() as cur:
        s = make_sources(cur, 1)
    db.commit()
    store_articles(db, [art(s[0], "a", "Headline")])
    full = art(s[0], "a", "Headline")
    full.raw_text = "body"
    store_articles(db, [full])
    with db.cursor() as cur:
        cur.execute("select raw_text from articles")
        assert cur.fetchone()[0] == "body"
        cur.execute("update articles set fetched_at = now() - interval '15 days'")
    db.commit()
    assert purge(db) == 1
    store_articles(db, [full])  # re-fetch must not restore purged text
    with db.cursor() as cur:
        cur.execute("select raw_text, raw_text_purged_at is not null from articles")
        assert cur.fetchone() == (None, True)


def test_purge_keeps_recent_text(db):
    with db.cursor() as cur:
        s = make_sources(cur, 1)
    db.commit()
    a = art(s[0], "new", "Fresh")
    a.raw_text = "body"
    store_articles(db, [a])
    assert purge(db) == 0


def load_clusters(db):
    with db.cursor() as cur:
        cur.execute(
            """select ca.cluster_id, array_agg(a.title order by a.id) from cluster_articles ca
               join articles a on a.id = ca.article_id
               join clusters c on c.id = ca.cluster_id where c.status = 'active'
               group by 1 order by 1"""
        )
        return [sorted(titles) for _, titles in cur.fetchall()]


def ingest(db, articles):
    stored = store_articles(db, articles)
    entities.save_for_articles(db, stored.new_ids)
    embed_pending(db, FakeEmbedder())
    return run_online(db, ClusterConfig(threshold=0.8))


def test_online_clustering_groups_by_topic_and_attaches_duplicates(db):
    with db.cursor() as cur:
        s = make_sources(cur)
    db.commit()
    t0 = datetime.now(UTC) - timedelta(hours=2)
    stats = ingest(db, [
        art(s[0], "f1", "Fed decision on rates", published_at=t0),
        art(s[1], "f2", "What the fed did today", published_at=t0 + timedelta(minutes=5)),
        art(s[2], "s1", "Storm closes highways", published_at=t0 + timedelta(minutes=6)),
        art(s[3], "f1-copy", "Fed Decision on Rates", published_at=t0 + timedelta(minutes=7)),
        art(s[3], "s2", "Big storm warning", published_at=t0 + timedelta(minutes=8)),
    ])  # fmt: skip
    assert stats["new_clusters"] == 2 and stats["assigned"] == 4 and stats["duplicates"] == 1
    assert load_clusters(db) == [
        ["Fed Decision on Rates", "Fed decision on rates", "What the fed did today"],
        ["Big storm warning", "Storm closes highways"],
    ]
    with db.cursor() as cur:
        cur.execute("select article_count, outlet_count from clusters order by id")
        assert cur.fetchall() == [(3, 3), (2, 2)]

    # A later run adds to existing clusters rather than starting over.
    stats = ingest(db, [art(s[2], "f3", "Fed chair speaks")])
    assert stats["new_clusters"] == 0
    assert len(load_clusters(db)[0]) == 4


def test_recluster_merges_split_event(db):
    with db.cursor() as cur:
        s = make_sources(cur)
    db.commit()
    # A strict threshold makes online clustering split the fed story in two...
    store = store_articles(db, [
        art(s[0], "f1", "Fed decision"), art(s[1], "f2", "Fed rates"),
        art(s[2], "f3", "Fed outlook"), art(s[3], "q1", "Quake strikes"),
        art(s[0], "q2", "Quake damage"),
    ])  # fmt: skip
    entities.save_for_articles(db, store.new_ids)
    embed_pending(db, FakeEmbedder())
    run_online(db, ClusterConfig(threshold=0.999, gray_margin=0.0))
    assert len(load_clusters(db)) == 5

    # ...and the nightly HDBSCAN pass puts each event back together.
    result = recluster(db)
    assert result["merged"] >= 1
    assert load_clusters(db) == [
        ["Fed decision", "Fed outlook", "Fed rates"],
        ["Quake damage", "Quake strikes"],
    ]
    with db.cursor() as cur:
        cur.execute(
            "select count(*) from clusters where status = 'merged' and merged_into is not null"
        )
        assert cur.fetchone()[0] == 3
        cur.execute("select count(*) from clusters where status = 'active' and centroid is null")
        assert cur.fetchone()[0] == 0


def test_ingest_run_end_to_end_with_mock_feeds(db):
    from pathlib import Path

    import httpx

    from ingest.http import HostRateLimiter, PoliteClient
    from ingest.run import run as ingest_run

    feed_xml = (Path(__file__).parent / "fixtures" / "sample_feed.xml").read_bytes()
    with db.cursor() as cur:
        (sid,) = make_sources(cur, 1)
        cur.execute(
            "insert into feeds (source_id, url, category) values (%s, %s, 'politics'), "
            "(%s, %s, 'us')",
            (sid, "https://outlet0.example/rss", sid, "https://outlet0.example/gone"),
        )
    db.commit()

    def handler(req):
        if req.url.path == "/gone":
            return httpx.Response(404)
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=feed_xml, headers={"ETag": '"e1"'})

    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        limiter=HostRateLimiter(0.0),
    )
    stats = ingest_run(db, client, use_gdelt=False, use_guardian=False)
    assert stats["feeds_ok"] == 1 and stats["feeds_failed"] == 1
    assert stats["inserted"] == 2 and stats["entity_links"] > 0
    with db.cursor() as cur:
        cur.execute("select url, etag, last_status from feeds order by id")
        assert cur.fetchall() == [
            ("https://outlet0.example/rss", '"e1"', 200),
            ("https://outlet0.example/gone", None, 404),
        ]
        cur.execute("select canonical_url from articles order by id limit 1")
        assert cur.fetchone()[0] == "https://news.example/politics/funding-bill"


def test_pipeline_run_records_success_and_failure(db):
    import pytest

    from common.runs import pipeline_run

    with pipeline_run(db, "unit") as stats:
        stats["n"] = 3
    with pytest.raises(RuntimeError), pipeline_run(db, "unit"):
        raise RuntimeError("boom")
    with db.cursor() as cur:
        cur.execute("select status, stats->>'n', error is not null from pipeline_runs order by id")
        assert cur.fetchall() == [("succeeded", "3", False), ("failed", None, True)]
