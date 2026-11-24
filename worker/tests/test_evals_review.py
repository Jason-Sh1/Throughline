"""Regression tests for the M4 review of the eval runner: the cache key covers the code
that shapes requests, a grade is tied to the draft it graded, transient failures are
not cached, and the gate cannot be passed by failing or deleting more."""

import json
from types import SimpleNamespace

import pytest

from evals import run
from tests.test_evals import FakeClient, judge_reply, story
from tests.test_llm_pipeline_db import EXTRACTION, reply, verdicts_for
from tests.test_llm_units import draft_dict


@pytest.fixture
def evaldirs(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(run, "BASELINE", tmp_path / "baseline.json")
    return tmp_path


def test_code_change_invalidates_the_cache(evaldirs, monkeypatch):
    stories = [story("s1")]
    run.evaluate(stories, {}, lambda: FakeClient())
    run.evaluate(stories, {}, lambda: pytest.fail("unchanged code must use the cache"))
    monkeypatch.setattr(run, "code_fingerprint", lambda: "different")
    fake = FakeClient()
    run.evaluate(stories, {}, lambda: fake)
    assert len(fake.calls) == 4  # extract, synthesize, verify and judge all re-run


def test_code_fingerprint_covers_the_pipeline_and_ignores_line_endings(tmp_path, monkeypatch):
    names = {p.name for p in run.CODE_FILES}
    assert {"verify.py", "schemas.py", "inputs.py", "pipeline.py", "judge.py"} <= names
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(b"x = 1\ny = 2\n")
    monkeypatch.setattr(run, "CODE_FILES", [a])
    lf = run.code_fingerprint()
    a.write_bytes(b"x = 1\r\ny = 2\r\n")
    assert run.code_fingerprint() == lf
    a.write_bytes(b"x = 1\ny = 3\n")
    assert run.code_fingerprint() != lf
    b.write_bytes(b"")
    monkeypatch.setattr(run, "CODE_FILES", [a, b])
    assert run.code_fingerprint() != lf


def test_judge_grade_is_tied_to_the_draft_it_graded():
    card = draft_dict()
    other = draft_dict()
    other["brief"][0]["text"] = "A different sentence."
    assert run.judge_key(card, []) != run.judge_key(other, [])
    assert run.judge_key(card, []) == run.judge_key(draft_dict(), [])
    assert run.judge_key(card, []) != run.judge_key(card, ["A key fact."])


class FlakyClient:
    """Synthesis returns invalid JSON until `heal()` is called."""

    def __init__(self):
        self.calls = []
        self.broken = True

        def create(**params):
            system = params["system"][0]["text"]
            user = params["messages"][0]["content"]
            self.calls.append(system[:20])
            if system.startswith("You extract"):
                text = json.dumps(EXTRACTION)
            elif system.startswith("You write"):
                text = "{not json" if self.broken else json.dumps(draft_dict())
            elif system.startswith("You check"):
                text = verdicts_for(user, ())
            else:
                text = judge_reply(user)
            return reply(text)

        self.messages = SimpleNamespace(create=create)


def test_transient_failure_is_not_cached(evaldirs):
    stories = [story("s1")]
    flaky = FlakyClient()
    m = run.evaluate(stories, {}, lambda: flaky)
    assert m["card_failure_rate"] == 1.0
    assert run._cached("run", run.run_key(stories[0])) is None  # not remembered as a failure
    flaky.broken = False
    m2 = run.evaluate(stories, {}, lambda: flaky)
    assert m2["card_failure_rate"] == 0.0 and m2["faithfulness"] == 1.0
    assert run._cached("run", run.run_key(stories[0])) is not None


def test_fully_cached_suite_needs_no_client_even_after_a_verification_failure(evaldirs):
    stories = [story("s1")]
    # The verifier rejects the headline: the card fails, which is a real result.
    fake = FakeClient(reject=("Fed holds rates steady",))
    m = run.evaluate(stories, {}, lambda: fake)
    assert m["card_failure_rate"] == 1.0
    assert run.evaluate(stories, {}, lambda: pytest.fail("should not call the API")) == m


def test_gate_cannot_be_passed_by_failing_or_deleting_more(evaldirs):
    base = {"faithfulness": 0.95, "card_failure_rate": 0.05, "sentences_kept_per_story": 20.0}
    run.BASELINE.write_text(json.dumps({"quick": {"metrics": base}}))

    def check(**changes):
        result = {"stories": 30, "judge_failures": 0} | base | changes
        return run.check(result, "quick", run.BASELINE)

    assert check() == []
    assert check(card_failure_rate=0.14, sentences_kept_per_story=16.5) == []  # within tolerance
    assert "card failure rate rose" in check(faithfulness=0.99, card_failure_rate=0.30)[0]
    assert "sentences kept per story fell" in check(faithfulness=1.0,
                                                    sentences_kept_per_story=10.0)[0]  # fmt: skip
    assert "judge failed on 2 stories" in check(judge_failures=2)[0]


def test_gate_tolerates_a_baseline_from_before_the_new_metrics(evaldirs):
    run.BASELINE.write_text(json.dumps({"quick": {"metrics": {"faithfulness": 0.95}}}))
    result = {"stories": 3, "faithfulness": 0.95, "card_failure_rate": 0.5,
              "sentences_kept_per_story": 1.0, "judge_failures": 0}  # fmt: skip
    assert run.check(result, "quick", run.BASELINE) == []


def test_kept_sentences_per_story_is_reported(evaldirs):
    m = run.evaluate([story("s1")], {}, lambda: FakeClient(reject=("Markets rose",)))
    assert m["sentences_kept_per_story"] == m["sentences_judged"] - 1
