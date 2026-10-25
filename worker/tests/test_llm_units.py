"""Unit tests for the LLM pipeline pieces that don't need a database or an API."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import anthropic
import pytest

from llm import prompts
from llm.batch import message_text
from llm.citations import CardRejected, filter_card, filter_extraction
from llm.cost import cost_usd
from llm.edition import score
from llm.inputs import SourceDoc, clean_text, content_hash, is_opinion, pick_rows
from llm.schemas import CardDraft, Extraction, clamp_lengths
from llm.synthesize import parse_draft, request_params


def s(text, *ids):
    return {"text": text, "source_ids": list(ids)}


def draft_dict(**over):
    d = {
        "headline": s("Fed holds rates steady", "S1", "S2"),
        "brief": [s("The Fed held rates.", "S1"), s("Markets rose.", "S2"), s("More soon.", "S9")],
        "why_it_matters": [s("Borrowing costs stay high.", "S1")],
        "key_facts": [s("Rate is 4.5%.", "S1", "S99"), s("Uncited fact.")],
        "background": [],
        "glossary": [
            {"term": "FOMC", "definition": "The Fed's rate committee.", "source_ids": ["S2"]}
        ],  # fmt: skip
        "timeline": [
            {"date": "2026-10-25", "event": "Decision", "source_ids": ["S1"]},
            {"date": "2026-10-01", "event": "Made up", "source_ids": []},
        ],  # fmt: skip
        "framing": {
            "widely_reported": [s("Rates unchanged.", "S1", "S2")],
            "reported_by_some": [],
            "analysis_opinion": [s("A column argued for cuts.", "S3")],
            "representative": {
                "widely_reported": ["S1", "S7"],
                "reported_by_some": [],
                "analysis_opinion": ["S3"],
            },  # fmt: skip
        },
        "whats_next": [s("Next meeting in December.", "S2")],
        "viz_hint": "chart",
        "viz_data": {
            "places": [],
            "chart": {
                "title": "Rate",
                "unit": "%",
                "points": [
                    {"label": "Now", "value": 4.5, "source_ids": ["S1"]},
                    {"label": "Before", "value": 4.75, "source_ids": ["S8"]},
                ],
            },
        },  # fmt: skip
    }
    d.update(over)
    return d


VALID = {"S1", "S2", "S3"}


def test_citation_filter_drops_uncited_and_invalid():
    draft, stats = filter_card(CardDraft.model_validate(draft_dict()), VALID)
    assert [b.text for b in draft.brief] == ["The Fed held rates.", "Markets rose."]  # S9 gone
    assert [f.source_ids for f in draft.key_facts] == [["S1"]]  # S99 stripped, uncited dropped
    assert len(draft.timeline) == 1
    assert draft.framing.representative.widely_reported == ["S1"]
    assert draft.viz_data.chart is None  # one valid point left: not a chart
    assert stats["dropped"] == 4
    assert stats["invalid_ids"] == 4


def test_citation_filter_rejects_card_without_headline_or_brief():
    with pytest.raises(CardRejected):
        filter_card(CardDraft.model_validate(draft_dict(headline=s("H", "S42"))), VALID)
    with pytest.raises(CardRejected):
        filter_card(CardDraft.model_validate(draft_dict(brief=[s("x", "S5")])), VALID)


def test_citation_filter_drops_empty_text():
    draft, _ = filter_card(CardDraft.model_validate(draft_dict(background=[s("  ", "S1")])), VALID)
    assert draft.background == []


def test_extraction_filter():
    ex = Extraction.model_validate({
        "entities": [{"name": "Fed", "kind": "org"}],
        "claims": [
            {"text": "Held rates", "source_ids": ["S1"], "status": "confirmed",
             "attributed_to": ""},
            {"text": "Ghost claim", "source_ids": ["S40"], "status": "reported",
             "attributed_to": None},
        ],
        "dates": [], "numbers": [], "locations": [],
        "viz_hint": "none",
        "one_liner": s("Uncited one-liner", "S40"),
    })  # fmt: skip
    ex, stats = filter_extraction(ex, VALID)
    assert [c.text for c in ex.claims] == ["Held rates"]
    assert ex.one_liner.source_ids == []
    assert stats["dropped"] == 2


def test_clamp_lengths():
    d = draft_dict(brief=[s(f"b{i}", "S1") for i in range(5)])
    assert len(clamp_lengths(CardDraft.model_validate(d)).brief) == 3


def test_card_schema_is_accepted_by_structured_outputs():
    schema = anthropic.transform_schema(CardDraft)
    assert schema["additionalProperties"] is False
    assert "headline" in schema["required"]


def test_request_params_cache_system_prompt_and_no_sampling_params():
    p = request_params("hello", "claude-haiku-5-5", "low")
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert p["system"][0]["text"] == prompts.load(prompts.SYNTHESIZE)[0]
    assert "temperature" not in p  # rejected by current models
    assert p["output_config"]["effort"] == "low"
    assert p["messages"][-1]["role"] == "user"  # no prefill


def msg(text, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],  # fmt: skip
    )


def test_parse_draft():
    import json

    good = msg(json.dumps(draft_dict()))
    assert parse_draft(message_text(good)).headline.text == "Fed holds rates steady"
    for bad in (msg("{}"), msg("not json"), msg("", "refusal"), msg("{", "max_tokens")):
        with pytest.raises(ValueError):
            parse_draft(message_text(bad))


def test_clean_text_and_opinion():
    assert clean_text("<p>Fed &amp; banks</p>\n  rise") == "Fed & banks rise"
    assert is_opinion("https://www.nytimes.com/2026/10/25/opinion/fed.html")
    assert is_opinion("https://example.com/commentary/")
    assert not is_opinion("https://example.com/news/opinion-poll-shows-lead")


def row(i, source, rating, hours_ago=0):
    return {
        "id": i,
        "source_id": source,
        "allsides_rating": rating,
        "published_at": datetime(2026, 10, 25, tzinfo=UTC) - timedelta(hours=hours_ago),
    }


def test_pick_rows_one_per_outlet_and_rotates_ratings():
    rows = [row(i, i, "left", i) for i in range(8)] + [row(20, 20, "right", 30),
            row(21, 21, "center", 40), row(22, 0, "left", 1)]  # fmt: skip
    picked = pick_rows(rows, limit=4)
    assert {r["allsides_rating"] for r in picked} == {"left", "right", "center"}
    assert len({r["source_id"] for r in picked}) == 4
    assert 22 not in [r["id"] for r in pick_rows(rows, limit=11)][:10]  # 2nd article last


def doc(i, title="T"):
    return SourceDoc(f"S{i}", i, "O", "center", title, "u", None, "x", False)


def test_content_hash_changes_with_inputs():
    base = content_hash([doc(1), doc(2)], "synthesize_v1", "m")
    assert base == content_hash([doc(1), doc(2)], "synthesize_v1", "m")
    assert base != content_hash([doc(1), doc(2, "edited")], "synthesize_v1", "m")
    assert base != content_hash([doc(1), doc(2)], "synthesize_v2", "m")
    assert base != content_hash([doc(1), doc(2)], "synthesize_v1", "other-model")


def test_score_prefers_coverage_spread_and_recency():
    assert score(10, {"left"}, 1) > score(3, {"left"}, 1)
    assert score(5, {"left", "center", "right"}, 1) > score(5, {"left"}, 1)
    assert score(5, {"left"}, 1) > score(5, {"left"}, 30)
    assert score(5, {"not_rated", "mixed"}, 1) == score(5, set(), 1)


def test_cost():
    # 1M in + 1M out on the Haiku 5.5 batch rate card
    assert cost_usd("claude-haiku-5-5", "batch", 1_000_000, 1_000_000) == pytest.approx(0.30)
    assert cost_usd("unknown", "batch", 10, 10) == 0.0
