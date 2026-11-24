"""Eval suite: metrics, suite selection, the blind labeling helper, the runner's
cache and gate (with a fake Anthropic client), and snapshots against Postgres."""

import json
import re
from types import SimpleNamespace

import pytest

from evals import label, metrics, run, snapshot
from evals.stories import save_gold, select_suite, story_docs
from tests.test_llm_pipeline_db import EXTRACTION, reply, seed, verdicts_for
from tests.test_llm_units import draft_dict


def art(ref, in_cluster=True, used=True, title=None):
    return {"ref": ref, "outlet": f"Outlet {ref}", "allsides_rating": "center", "kind": "news",
            "title": title or f"Headline {ref}", "url": f"https://x.example/{ref}",
            "published_at": "2026-10-30T12:00:00+00:00", "summary": f"Summary {ref}",
            "is_opinion": False, "in_cluster": in_cluster, "used": used}  # fmt: skip


def story(sid="2026-10-31-c1"):
    return {"id": sid, "cluster_id": 1, "category": "business", "snapshot_at": "x",
            "articles": [art("S1"), art("S2"), art("S3"), art("C1", False, False)]}  # fmt: skip


# --- Metrics -------------------------------------------------------------------------


def test_pairwise_and_bcubed_on_a_known_case():
    # Predicted cluster {a, b, c}; truth: a, b same event, c different, d (outside) same.
    pred = {"a": "story", "b": "story", "c": "story", "d": "d"}
    gold = {"a": "story", "b": "story", "c": "c", "d": "story"}
    tp, pp, gp = metrics.pairwise(pred, gold)
    assert (tp, pp, gp) == (1, 3, 3)  # ab correct; ac, bc wrong; ad, bd missed
    p_sum, r_sum, n = metrics.bcubed(pred, gold)
    assert n == 4
    assert p_sum == pytest.approx(2 / 3 + 2 / 3 + 1 / 3 + 1)
    assert r_sum == pytest.approx(2 / 3 + 2 / 3 + 1 + 1 / 3)


def test_clustering_uses_only_labeled_articles():
    s = story()
    gold = {s["id"]: {"membership": {"S1": "same", "S2": "same", "S3": "different",
                                     "C1": "same"}}}  # fmt: skip
    m = metrics.clustering([s], gold)
    assert m["cluster_stories"] == 1
    assert m["cluster_pairwise_precision"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["cluster_pairwise_recall"] == pytest.approx(1 / 3, abs=1e-3)
    assert metrics.clustering([s], {})["cluster_pairwise_f1"] is None


def grade(sid, verdict="supported", cited=(("S1", True),), uncited=()):
    return {"id": sid, "verdict": verdict, "reason": "",
            "cited_support": [{"source_id": s, "supports": ok} for s, ok in cited],
            "uncited_support": list(uncited)}  # fmt: skip


def test_card_metrics():
    run_ok = {"card": {}, "kept": ["V1", "V2", "V4"]}
    judge = {"result": {
        "sentences": [grade("V1"), grade("V2", "unsupported", (("S1", False),)),
                      grade("V3", "unsupported"), grade("V4", uncited=("S2",))],
        "key_facts": [{"id": "K1", "covered_by": ["V1"]}, {"id": "K2", "covered_by": ["V3"]}],
    }}  # fmt: skip
    rows = [{"run": run_ok, "judge": judge, "key_facts": ["f1", "f2"]},
            {"run": {"card": None, "kept": []}, "judge": None, "key_facts": []}]  # fmt: skip
    m = metrics.card_metrics(rows)
    assert m["faithfulness"] == pytest.approx(2 / 3, abs=1e-3)  # V1, V4 of V1, V2, V4
    assert m["faithfulness_pre_verify"] == 0.5
    assert m["verifier_catch_rate"] == 0.5  # V3 dropped, V2 slipped through
    assert m["verifier_false_drop_rate"] == 0.0
    assert m["citation_precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["citation_recall"] == pytest.approx(2 / 3, abs=1e-3)  # V4 missed S2
    assert m["key_fact_coverage"] == 0.5  # K2 only in a dropped sentence
    assert m["card_failure_rate"] == 0.5


def test_p95():
    assert metrics.p95([]) is None
    assert metrics.p95(list(range(1, 101))) == 95


# --- Stories and suites --------------------------------------------------------------


def test_select_suite_puts_labeled_first_and_caps_quick():
    stories = [story(f"s{i:02d}") for i in range(40)]
    gold = {"s39": {}, "s38": {}}
    quick = select_suite(stories, gold, "quick")
    assert len(quick) == 30 and [s["id"] for s in quick[:2]] == ["s38", "s39"]
    assert len(select_suite(stories, gold, "full")) == 40


def test_story_docs_only_used_articles():
    docs = story_docs(story())
    assert [d.local_id for d in docs] == ["S1", "S2", "S3"]
    assert docs[0].excerpt == "Summary S1"


# --- Labeling helper ------------------------------------------------------------------


def test_labeling_is_blind_and_round_trips():
    s = story()
    payload = label.story_payload(s, None)
    ids = [a["id"] for a in payload["articles"]]
    assert all(re.fullmatch(r"a\d+", i) for i in ids)  # no S/C refs leak
    assert all("in_cluster" not in a and "used" not in a for a in payload["articles"])
    assert payload["anchor"]["title"] == "Headline S1"
    body = {"id": s["id"], "membership": dict.fromkeys(ids, "different"),
            "key_facts": [" Fact one ", "", "Fact two"]}  # fmt: skip
    g = label.to_gold(s, body, "Jason")
    assert g["membership"] == {"S1": "same", "S2": "different", "S3": "different",
                               "C1": "different"}  # fmt: skip
    assert g["key_facts"] == ["Fact one", "Fact two"]
    assert label.is_complete(s, g)
    # Reloading shows the saved labels under the same opaque ids.
    again = label.story_payload(s, g)
    assert again["membership"] == dict.fromkeys(ids, "different")


def test_incomplete_without_facts():
    s = story()
    g = {"membership": {a["ref"]: "same" for a in s["articles"]}, "key_facts": []}
    assert not label.is_complete(s, g)


# --- Runner, with a fake client --------------------------------------------------------

SENT = re.compile(r"^\[(V\d+)\] cites: (.*)$", re.M)


def judge_reply(user: str) -> str:
    sentences = [{"id": vid, "verdict": "supported", "reason": "",
                  "cited_support": [{"source_id": c.strip(), "supports": True}
                                    for c in cites.split(",") if c.strip() != "none"],
                  "uncited_support": []}
                 for vid, cites in SENT.findall(user)]  # fmt: skip
    facts = [{"id": k, "covered_by": ["V1"]} for k in re.findall(r"^\[(K\d+)\]", user, re.M)]
    return json.dumps({"sentences": sentences, "key_facts": facts})


class FakeClient:
    def __init__(self, reject=()):
        self.calls = []

        def create(**params):
            system = params["system"][0]["text"]
            user = params["messages"][0]["content"]
            self.calls.append(system[:20])
            if system.startswith("You extract"):
                text = json.dumps(EXTRACTION)
            elif system.startswith("You write"):
                text = json.dumps(draft_dict())
            elif system.startswith("You check"):
                text = verdicts_for(user, reject)
            else:
                text = judge_reply(user)
            return reply(text)

        self.messages = SimpleNamespace(create=create)


@pytest.fixture
def evaldirs(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(run, "BASELINE", tmp_path / "baseline.json")
    return tmp_path


def test_evaluate_scores_and_caches(evaldirs):
    stories = [story("s1"), story("s2")]
    gold = {"s1": {"story_id": "s1", "membership": {"S1": "same", "S2": "same", "S3": "same",
                                                    "C1": "different"},
                   "key_facts": ["The Fed held rates."]}}  # fmt: skip
    fake = FakeClient(reject=("Markets rose",))
    m = run.evaluate(stories, gold, lambda: fake)
    assert len(fake.calls) == 8  # 2 stories x (extract, synthesize, verify, judge)
    assert m["stories"] == 2 and m["card_failure_rate"] == 0.0
    assert m["faithfulness"] == 1.0  # the fake judge supports everything kept
    assert m["verifier_false_drop_rate"] > 0  # "Markets rose." dropped but judged supported
    assert m["key_fact_coverage"] == 1.0 and m["key_fact_stories"] == 1
    assert m["cluster_pairwise_f1"] == 1.0
    assert m["cost_per_story_usd"] > 0

    # Unchanged inputs: everything comes from the cache, no client needed.
    m2 = run.evaluate(stories, gold, lambda: pytest.fail("should not call the API"))
    assert m2 == m

    # New gold facts only re-run the judge.
    gold["s1"]["key_facts"].append("Markets rose.")
    fake2 = FakeClient()
    run.evaluate(stories, gold, lambda: fake2)
    assert len(fake2.calls) == 1 and fake2.calls[0].startswith("You grade")


def test_prompt_text_change_invalidates_the_cache(evaldirs, monkeypatch):
    stories = [story("s1")]
    run.evaluate(stories, {}, lambda: FakeClient())
    real = run._prompt_text
    monkeypatch.setattr(run, "_prompt_text", lambda n: real(n) + ("x" if "synth" in n else ""))
    fake = FakeClient()
    run.evaluate(stories, {}, lambda: fake)
    # The pipeline re-runs. The fake returns the same draft, and a grade is keyed on
    # the draft it graded, so the judge is not paid again for identical text.
    assert fake.calls == ["You extract facts fr", "You write story brie", "You check AI-written"]


def test_gate(evaldirs):
    result = {"stories": 3, "faithfulness": 0.90}
    assert run.check(result, "quick", run.BASELINE) == []  # no baseline: report only
    run.BASELINE.write_text(json.dumps({"quick": {"metrics": {"faithfulness": 0.95}}}))
    assert run.check({"stories": 3, "faithfulness": 0.94}, "quick", run.BASELINE) == []
    problems = run.check(result, "quick", run.BASELINE)
    assert problems and "0.950 -> 0.900" in problems[0]
    assert run.check({"stories": 3, "faithfulness": None}, "quick", run.BASELINE)


def test_spend_guard(evaldirs, monkeypatch):
    monkeypatch.setattr(run, "MAX_EVAL_COST_USD", 0.01)
    with pytest.raises(SystemExit, match="MAX_EVAL_COST_USD"):
        run.evaluate([story("s1")], {}, lambda: FakeClient())


def test_save_gold_writes_one_file_per_story(tmp_path):
    p = save_gold({"story_id": "s1", "membership": {}, "key_facts": []}, tmp_path)
    assert p.name == "s1.json" and json.loads(p.read_text())["story_id"] == "s1"


# --- Snapshot against Postgres ----------------------------------------------------------


def test_snapshot_freezes_sources_without_raw_text(db):
    with db.cursor() as cur:
        c1, c2 = seed(cur)
        # Give everything an embedding so near-misses can be found.
        cur.execute("update articles set embedding = array_fill(0.1, array[384])::vector")
    db.commit()
    picked = snapshot.pick_clusters(db, n=5, hours=72, min_outlets=2)
    assert [p[0] for p in picked][:2] == [c1, c2]  # 3 news outlets before 2
    s = snapshot.snapshot_cluster(db, c1, "business", hours=72, k=5)
    used = [a for a in s["articles"] if a["used"]]
    near = [a for a in s["articles"] if not a["in_cluster"]]
    assert [a["ref"] for a in used] == ["S1", "S2", "S3"]  # wire copy skipped
    assert near and all(a["ref"].startswith("C") for a in near)
    assert {a["title"] for a in near} == {"Storm hits coast", "Coastal storm"}
    text = json.dumps(s)
    assert "SECRET FULL TEXT" not in text and "Summary 1" in text
