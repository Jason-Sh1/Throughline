"""Edition pipeline against a real Postgres (TEST_DATABASE_URL), with fake LLMs.

The fakes stand in for Gemini and the Anthropic Batches API so the test checks
our plumbing: caching by content hash, the citation filter, real headlines in
the stored card, one-liner fallback, spend accounting and the static export.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from llm import edition
from llm.extract import LLMResult, extract_cluster
from tests.test_llm_units import draft_dict, msg

EXTRACTION = {
    "entities": [{"name": "Federal Reserve", "kind": "org"}],
    "claims": [{"text": "The Fed held rates.", "source_ids": ["S1", "S2"], "status": "confirmed",
                "attributed_to": "the Fed"}],  # fmt: skip
    "dates": [], "numbers": [], "locations": [], "viz_hint": "none",
    "one_liner": {"text": "The Fed held rates steady.", "source_ids": ["S1"]},
}  # fmt: skip


class FakeGemini:
    def __init__(self):
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        assert '<source id="S1"' in user
        return LLMResult(json.dumps(EXTRACTION), 1000, 500)


class FakeAnthropic:
    """Just enough of client.messages(.batches) for synthesize.py."""

    def __init__(self, card_json):
        self.submitted = []
        usage = SimpleNamespace(
            input_tokens=2000,
            output_tokens=1500,
            cache_read_input_tokens=1000,
            cache_creation_input_tokens=0,
        )
        self._msg = msg(card_json)
        self._msg.usage = usage
        outer = self

        class Batches:
            def create(self, requests):
                outer.submitted = list(requests)
                return SimpleNamespace(id="msgbatch_test")

            def retrieve(self, _id):
                return SimpleNamespace(processing_status="ended")

            def results(self, _id):
                return [SimpleNamespace(custom_id=r["custom_id"],
                        result=SimpleNamespace(type="succeeded", message=outer._msg))
                        for r in outer.submitted]  # fmt: skip

        self.messages = SimpleNamespace(batches=Batches(), create=lambda **kw: outer._msg)


def seed(cur):
    """Two clusters: a 3-outlet story and a 2-outlet story, plus a wire copy."""
    now = datetime.now(UTC)
    src = []
    for i, rating in enumerate(["left", "center", "right"]):
        cur.execute(
            "insert into sources (slug, name, region, allsides_rating) values (%s, %s, 'US', %s) "
            "returning id", (f"o{i}", f"Outlet {i}", rating))  # fmt: skip
        src.append(cur.fetchone()[0])
    clusters = []
    for _ in range(2):
        cur.execute("insert into clusters (category) values ('business') returning id")
        clusters.append(cur.fetchone()[0])

    def article(i, source, cluster, title, dup=None, url=None):
        cur.execute(
            """insert into articles (source_id, origin, url, canonical_url, title, title_hash,
                   summary, raw_text, published_at, duplicate_of)
               values (%s, 'rss', %s, %s, %s, %s, %s, 'SECRET FULL TEXT', %s, %s) returning id""",
            (source, url or f"https://x.example/{i}", f"https://x.example/{i}", title, f"h{i}",
             f"<p>Summary {i}</p>", now - timedelta(hours=i), dup),
        )  # fmt: skip
        aid = cur.fetchone()[0]
        cur.execute("insert into cluster_articles (article_id, cluster_id) values (%s, %s)",
                    (aid, cluster))  # fmt: skip
        return aid

    a1 = article(1, src[0], clusters[0], "Fed holds rates, Outlet 0 says")
    article(2, src[1], clusters[0], "Fed pauses again")
    article(3, src[2], clusters[0], "Fed copy", dup=a1)
    article(4, src[2], clusters[0], "Why the Fed is wrong", url="https://x.example/opinion/4")
    article(5, src[0], clusters[1], "Storm hits coast")
    article(6, src[1], clusters[1], "Coastal storm")
    return clusters


def test_extraction_is_cached_by_content_hash(db):
    with db.cursor() as cur:
        c1, _ = seed(cur)
    db.commit()
    fake = FakeGemini()
    ex1, spend1 = extract_cluster(db, c1, fake, "gemini-2.5-flash-lite")
    ex2, spend2 = extract_cluster(db, c1, fake, "gemini-2.5-flash-lite")
    assert ex1 == ex2 and fake.calls == 1
    assert spend1["input_tokens"] == 1000 and spend2["input_tokens"] == 0


def test_edition_build_end_to_end(db, monkeypatch, tmp_path):
    with db.cursor() as cur:
        c1, c2 = seed(cur)
    db.commit()
    card_json = json.dumps(draft_dict())
    fake_claude = FakeAnthropic(card_json)
    monkeypatch.setattr(edition, "gemini_caller", lambda model: FakeGemini())
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake_claude)

    stats = {}
    eid = edition.build(db, "morning", n_full=1, n_tail=5, wait_s=0, stats=stats)
    assert stats["new_cards"] == 1 and stats["synthesis"]["cards"] == 1
    assert stats["cost_usd"] > 0
    req = fake_claude.submitted[0]["params"]
    assert "SECRET FULL TEXT" in req["messages"][0]["content"]  # model sees text...

    with db.cursor() as cur:
        cur.execute(
            """select ec.tier, c.card from edition_cards ec join cards c on c.id = ec.card_id
               where edition_id = %s order by rank""",
            (eid,),
        )
        rows = cur.fetchall()
    assert [t for t, _ in rows] == ["full", "one_liner"]
    card = rows[0][1]
    assert card["cluster_id"] == c1
    # Local ids became article ids; uncited / invalid citations are gone.
    assert all(isinstance(i, int) for b in card["brief"] for i in b["source_ids"])
    assert len(card["brief"]) == 2
    # Representative headlines are the outlets' real ones, from the database.
    rep = card["framing"]["representative"]["widely_reported"][0]
    with db.cursor() as cur:
        cur.execute("select title from articles where id = %s", (rep["article_id"],))
        assert rep["headline"] == cur.fetchone()[0]
    assert card["confidence"]["outlet_count"] == 3  # the wire copy's outlet counts
    assert card["ai_label"] == "AI-generated from 3 sources"
    assert any(s["is_opinion"] for s in card["sources"])
    assert rows[1][1]["one_liner"]["text"] == "The Fed held rates steady."

    # Second run: nothing changed, so no new extraction or synthesis calls.
    stats2 = {}
    edition.build(db, "morning", n_full=1, n_tail=5, wait_s=0, stats=stats2)
    assert stats2["new_cards"] == 0 and stats2["cost_usd"] == 0

    out = tmp_path / "edition.json"
    doc = edition.export(db, out, eid)
    assert len(doc["cards"]) == 2
    assert "SECRET FULL TEXT" not in out.read_text()  # ...but it never ships (rule 5)
    assert "Summary 1" not in out.read_text()


def test_invalid_card_is_retried_once_then_falls_back(db, monkeypatch):
    with db.cursor() as cur:
        seed(cur)
    db.commit()
    fake = FakeAnthropic("{not json")
    retries = []

    def create(**kw):
        retries.append(kw)
        return fake._msg  # still invalid

    fake.messages.create = create
    monkeypatch.setattr(edition, "gemini_caller", lambda model: FakeGemini())
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake)
    stats = {}
    eid = edition.build(db, "evening", n_full=1, n_tail=0, wait_s=0, stats=stats)
    assert len(retries) == 1  # exactly one retry, with the same user message
    assert retries[0]["messages"] == fake.submitted[0]["params"]["messages"]
    assert stats["synthesis"]["failed"] == 1
    with db.cursor() as cur:
        cur.execute("select tier from edition_cards where edition_id = %s", (eid,))
        assert [r[0] for r in cur.fetchall()] == ["one_liner"]
