"""Verification pass (M4): unit tests on the card walk / apply logic, plus edition
runs against Postgres with a fake verifier."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from llm import batch, edition, verify
from llm.card import confidence
from llm.verify import CardFailed, SentenceVerdict, VerifyResult, apply, items, parse_result
from tests.test_llm_pipeline_db import FakeAnthropic, seed
from tests.test_llm_units import draft_dict


def result(verdicts: dict[str, str]) -> VerifyResult:
    return VerifyResult(
        verdicts=[SentenceVerdict(id=k, verdict=v, reason="r") for k, v in verdicts.items()]
    )


def all_supported(its) -> VerifyResult:
    return result({it.key: "supported" for it in its})


# --- Unit: walking and applying ------------------------------------------------------


def test_items_cover_every_cited_field_in_order():
    card = draft_dict()
    its = items(card)
    fields = [it.field for it in its]
    assert fields[0] == "headline" and its[0].key == "V1"
    assert fields.count("brief") == 3
    assert "glossary" in fields and "timeline" in fields and "viz_data.chart" in fields
    assert "framing.analysis_opinion" in fields
    gloss = next(it for it in its if it.field == "glossary")
    assert gloss.text == "FOMC: The Fed's rate committee."
    timeline = next(it for it in its if it.field == "timeline")
    assert timeline.text == "2026-10-25: Decision"


def test_apply_drops_unsupported_and_contradicted():
    card = draft_dict()
    its = items(card)
    by_text = {it.text: it.key for it in its}
    verdicts = {it.key: "supported" for it in its}
    verdicts[by_text["Markets rose."]] = "unsupported"
    verdicts[by_text["Borrowing costs stay high."]] = "contradicted"
    card, report = apply(card, its, result(verdicts))
    assert [b["text"] for b in card["brief"]] == ["The Fed held rates.", "More soon."]
    assert card["why_it_matters"] == []
    assert card["verified"] is True
    assert report["stats"] == {"supported": len(its) - 2, "unsupported": 1, "contradicted": 1}
    assert {d["text"] for d in report["dropped"]} == {"Markets rose.", "Borrowing costs stay high."}


def test_missing_verdict_counts_as_unsupported():
    card = draft_dict()
    its = items(card)
    verdicts = {it.key: "supported" for it in its if it.text != "Markets rose."}
    card, report = apply(card, its, result(verdicts))
    assert "Markets rose." not in [b["text"] for b in card["brief"]]
    assert report["stats"]["missing"] == 1


def test_unsupported_headline_fails_the_card_with_report():
    card = draft_dict()
    its = items(card)
    verdicts = {it.key: "supported" for it in its}
    verdicts["V1"] = "unsupported"
    with pytest.raises(CardFailed) as e:
        apply(card, its, result(verdicts))
    assert e.value.report["dropped"][0]["field"] == "headline"


def test_losing_every_bullet_fails_the_card():
    card = draft_dict()
    its = items(card)
    verdicts = {it.key: ("unsupported" if it.field == "brief" else "supported") for it in its}
    with pytest.raises(CardFailed, match="brief"):
        apply(card, its, result(verdicts))


def test_viz_hint_settles_after_drops():
    card = draft_dict()
    its = items(card)
    verdicts = {it.key: "supported" for it in its}
    chart_point = next(it for it in its if it.field == "viz_data.chart")
    verdicts[chart_point.key] = "unsupported"  # leaves 1 point: no chart
    card, _ = apply(card, its, result(verdicts))
    assert card["viz_data"]["chart"] is None and card["viz_hint"] == "none"


def test_one_liner_card_verifies_its_sentence():
    card = {"one_liner": {"text": "The Fed held rates.", "source_ids": [1]}}
    its = items(card)
    assert [it.field for it in its] == ["one_liner"]
    with pytest.raises(CardFailed):
        apply(card, its, result({"V1": "contradicted"}))


def test_render_sentences_maps_article_ids_to_local_ids():
    card = {"headline": {"text": "H", "source_ids": [42, 7]}}
    text = verify.render_sentences(items(card), {42: "S1", 7: "S2"})
    assert text == "[V1] cites: S1, S2\nH"


def test_parse_result_rejects_broken_responses():
    with pytest.raises(ValueError):
        parse_result("{not json")
    ok = json.dumps({"verdicts": [{"id": "V1", "verdict": "supported", "reason": ""}]})
    with pytest.raises(ValueError, match="only 1 verdicts"):
        parse_result(ok, expected=5)
    assert parse_result(ok, expected=2).verdicts[0].id == "V1"


# --- Unit: confidence ----------------------------------------------------------------


def test_confidence_counts_news_outlets_only():
    now = datetime.now(UTC)

    def row(source_id, rating, kind="news", hours=1):
        return {"source_id": source_id, "allsides_rating": rating, "kind": kind,
                "published_at": now - timedelta(hours=hours)}  # fmt: skip

    rows = [row(1, "left"), row(2, "lean_left"), row(3, "right", hours=10),
            row(4, "not_rated", "agency"), row(5, "not_rated", "research")]  # fmt: skip
    c = confidence(rows, now)
    assert c.outlet_count == 3 and c.other_sources == 2 and c.article_count == 5
    assert c.spectrum == {"left": 1, "lean_left": 1, "right": 1}
    assert c.spectrum_spread == 2  # left + right, no center
    assert c.single_source is False and c.developing is True
    solo = confidence([row(1, "center"), row(4, "not_rated", "agency")], now)
    assert solo.single_source is True and solo.spectrum_spread == 1


# --- Edition runs against Postgres -----------------------------------------------------


def _edition_rows(db, eid):
    with db.cursor() as cur:
        cur.execute(
            """select ec.tier, c.card, c.status, v.report from edition_cards ec
               join cards c on c.id = ec.card_id
               left join card_verifications v on v.card_id = c.id
               where edition_id = %s order by rank""",
            (eid,),
        )
        return cur.fetchall()


def _edition_status(db, eid):
    with db.cursor() as cur:
        cur.execute("select status from editions where id = %s", (eid,))
        return cur.fetchone()[0]


def test_verification_drops_sentence_and_publishes(db, monkeypatch):
    with db.cursor() as cur:
        seed(cur)
    db.commit()
    fake = FakeAnthropic(json.dumps(draft_dict()), reject=("Markets rose",))
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake)
    stats = {}
    eid = edition.build(db, "morning", n_full=1, n_tail=1, wait_s=0, stats=stats)
    rows = _edition_rows(db, eid)
    tier, card, status, report = rows[0]
    assert tier == "full" and status == "published" and card["verified"] is True
    assert "Markets rose." not in [b["text"] for b in card["brief"]]
    assert report["dropped"][0]["text"] == "Markets rose."
    assert report["prompt_version"] == "verify_v1"
    # The verifier saw the sources and numbered sentences, citing local ids.
    req = fake.requests("verify-")[0]["params"]["messages"][0]["content"]
    assert "<sources>" in req and "[V1] cites: S" in req
    with db.cursor() as cur:
        cur.execute("select status from editions where id = %s", (eid,))
        assert cur.fetchone()[0] == "published"


def test_failed_full_card_falls_back_to_verified_one_liner(db, monkeypatch):
    with db.cursor() as cur:
        seed(cur)
    db.commit()
    fake = FakeAnthropic(json.dumps(draft_dict()), reject=("Fed holds rates steady",))
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake)
    stats = {}
    eid = edition.build(db, "morning", n_full=1, n_tail=0, wait_s=0, stats=stats)
    assert stats["verification"]["rejected"] == 1
    rows = _edition_rows(db, eid)
    assert [(t, s) for t, _, s, _ in rows] == [("one_liner", "published")]
    with db.cursor() as cur:
        cur.execute(
            """select v.report->>'error' from cards c
               join card_verifications v on v.card_id = c.id where c.status = 'failed'"""
        )
        assert cur.fetchone()[0] == "headline not supported"


def test_unverified_story_is_left_out(db, monkeypatch):
    with db.cursor() as cur:
        seed(cur)
    db.commit()
    # Rejects the full card's headline and the one-liner ("The Fed held rates steady.").
    fake = FakeAnthropic(json.dumps(draft_dict()), reject=("Fed hold", "held rates steady"))
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake)
    stats = {}
    eid = edition.build(db, "morning", n_full=1, n_tail=0, wait_s=0, stats=stats)
    assert _edition_rows(db, eid) == []
    # An edition with no verified card is never published empty.
    assert stats["published"] is False and stats["empty"] is True
    assert _edition_status(db, eid) == "failed"


def test_pending_verification_keeps_edition_unpublished(db, monkeypatch):
    with db.cursor() as cur:
        seed(cur)
    db.commit()
    fake = FakeAnthropic(json.dumps(draft_dict()))
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake)
    calls = []

    def wait_for(client, provider_id, timeout_s, poll_s=30):
        calls.append(provider_id)
        return len(calls) < 3  # extraction + synthesis end; verification doesn't

    monkeypatch.setattr(batch, "wait_for", wait_for)
    stats = {}
    eid = edition.build(db, "morning", n_full=1, n_tail=0, wait_s=0, stats=stats)
    assert stats["published"] is False and "verification_batch_pending" in stats
    assert _edition_rows(db, eid) == []  # drafts never ship
    with db.cursor() as cur:
        cur.execute("select status from editions where id = %s", (eid,))
        assert cur.fetchone()[0] == "building"

    # Later: collect the batch, re-run the slot, and it publishes from the cache.
    monkeypatch.setattr(batch, "wait_for", lambda *a, **k: True)
    edition.collect_open(db, {})
    stats2 = {}
    eid2 = edition.build(db, "morning", n_full=1, n_tail=0, wait_s=0, stats=stats2)
    assert eid2 == eid and stats2["published"] is True and stats2["new_verifications"] == 0
    assert [t for t, *_ in _edition_rows(db, eid)] == ["full"]


def test_restoring_a_one_liner_keeps_its_verified_state(db, monkeypatch):
    with db.cursor() as cur:
        seed(cur)
    db.commit()
    fake = FakeAnthropic(json.dumps(draft_dict()))
    monkeypatch.setattr(edition, "anthropic_client", lambda: fake)
    edition.build(db, "morning", n_full=0, n_tail=1, wait_s=0, stats={})
    with db.cursor() as cur:
        cur.execute("select id, card from cards where content_hash like 'one-liner:%'")
        card_id, card = cur.fetchone()
        cur.execute("select id from extractions limit 1")
        ex_id = cur.fetchone()[0]
    assert card["verified"] is True
    cluster_id = card["cluster_id"]
    assert edition.store_one_liner(db, cluster_id, ex_id) == card_id
    with db.cursor() as cur:
        cur.execute("select card->>'verified' from cards where id = %s", (card_id,))
        assert cur.fetchone()[0] == "true"
