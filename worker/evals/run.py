"""Eval runner: `make eval-quick` (30 stories, the CI gate) or `make eval` (all).

For each story: run the pipeline (evals/pipeline.py), have the judge grade the
card (evals/judge.py), then compute the metrics (evals/metrics.py) and compare
faithfulness with the committed baseline.

Every pipeline run and judge grade is cached on disk, keyed by a hash of
everything that could change it (story, prompt *texts*, models, efforts, gold
key facts, and a fingerprint of the pipeline code). An unchanged pipeline costs
nothing to re-evaluate, so CI can run the gate on every PR and only pays when
something that can alter the output changed.

Options:
  --check            fail if faithfulness dropped more than TOLERANCE vs the suite's
                     baseline, or the card failure rate rose, or far fewer sentences
                     survive, or the judge failed on a story (see check())
  --update-baseline  write these numbers to evals/baseline.json
  --record           also insert the results into the `evals` table (DATABASE_URL)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from evals import judge, metrics, pipeline
from evals.stories import EVALS_DIR, load_gold, load_stories, select_suite
from llm import prompts

CACHE_DIR = EVALS_DIR / ".cache"
RESULTS_DIR = EVALS_DIR / "results"
BASELINE = EVALS_DIR / "baseline.json"
# LLM outputs vary a little run to run even with identical prompts; a drop
# smaller than this isn't treated as a regression.
TOLERANCE = 0.02
FAILURE_TOLERANCE = 0.10  # card failure rate may not rise by more than this
KEPT_FLOOR = 0.80  # sentences kept per story may not fall below this share of baseline
# Rough per-story cost of an uncached run, for the spend guard (standard prices):
# Haiku pipeline ~$0.006, Sonnet judge ~$0.04.
EST_RUN_USD, EST_JUDGE_USD = 0.006, 0.04
MAX_EVAL_COST_USD = float(os.environ.get("MAX_EVAL_COST_USD", "3.00"))


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]


def _prompt_text(name: str) -> str:
    return prompts.load(name)[0]


# Code that shapes a request or post-processes a response: schemas and their field
# descriptions, token limits, source rendering, the citation filter, how verdicts are
# applied, how a story becomes sources. A change to any of it can change the output
# without touching a prompt file, so it is part of the cache key. Listed by name:
# the edition builder and the price table cannot change an eval result, and an edit
# there should not cost a re-run (~$1.40). Add a module here when the eval starts
# importing it.
LLM_DIR = EVALS_DIR.parent / "llm"
CODE_FILES = [
    LLM_DIR / "batch.py",  # request parameters
    LLM_DIR / "citations.py",
    LLM_DIR / "extract.py",
    LLM_DIR / "inputs.py",
    LLM_DIR / "schemas.py",
    LLM_DIR / "synthesize.py",
    LLM_DIR / "verify.py",
    EVALS_DIR / "judge.py",
    EVALS_DIR / "pipeline.py",
    EVALS_DIR / "stories.py",
]


def code_fingerprint() -> str:
    h = hashlib.sha256()
    for path in CODE_FILES:
        h.update(path.name.encode())
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))  # same hash on Windows and CI
    return h.hexdigest()[:16]


def run_key(story: dict) -> str:
    texts = [_prompt_text(n) for n in (prompts.EXTRACT, prompts.SYNTHESIZE, prompts.VERIFY)]
    return _hash("run", story, texts, pipeline.models(), code_fingerprint())


def judge_key(story: dict, pre_card: dict, facts: list[str]) -> str:
    """Keyed on what the judge reads: the story's sources and the draft that was
    actually graded (not the run key: if a run is redone, an old grade must not be
    attached to a different draft)."""
    return _hash("judge", story, pre_card, _prompt_text(prompts.JUDGE), judge.judge_model(),
                 judge.judge_effort(), facts, code_fingerprint())  # fmt: skip


def _cached(kind: str, key: str) -> dict | None:
    p = CACHE_DIR / kind / f"{key}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _store(kind: str, key: str, data: dict) -> None:
    p = CACHE_DIR / kind / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def evaluate(stories: list[dict], gold: dict, client_factory, workers: int = 4) -> dict:
    plan = []
    need_run = need_judge = 0
    for s in stories:
        facts = (gold.get(s["id"]) or {}).get("key_facts", [])
        rk = run_key(s)
        plan.append((s, facts, rk))
        cached_run = _cached("run", rk)
        if cached_run is None:
            need_run += 1
            need_judge += 1
        elif cached_run["pre_card"] is not None:  # a story with no draft has nothing to judge
            need_judge += _cached("judge", judge_key(s, cached_run["pre_card"], facts)) is None
    estimate = need_run * EST_RUN_USD + need_judge * EST_JUDGE_USD
    print(f"{len(plan)} stories: {need_run} pipeline runs and {need_judge} judge calls "
          f"not cached (est. ${estimate:.2f})")  # fmt: skip
    if estimate > MAX_EVAL_COST_USD:
        raise SystemExit(f"estimated ${estimate:.2f} > MAX_EVAL_COST_USD ${MAX_EVAL_COST_USD:.2f}")
    client = client_factory() if (need_run or need_judge) else None
    spent = {"cost_usd": 0.0}

    def one(item):
        story, facts, rk = item
        run = _cached("run", rk)
        if run is None:
            run = pipeline.run_story(client, story)
            spent["cost_usd"] += run["spend"]["cost_usd"]
            # A run that died on a transient error (invalid JSON after the retry,
            # max_tokens) is not cached: it would count as a failed story forever.
            if not run.get("transient"):
                _store("run", rk, run)
        graded = None
        if run["pre_card"] is not None:
            jk = judge_key(story, run["pre_card"], facts)
            graded = _cached("judge", jk)
            if graded is None:
                try:
                    graded = judge.judge(client, story, run["pre_card"], facts)
                    spent["cost_usd"] += graded["spend"]["cost_usd"]
                    _store("judge", jk, graded)
                except ValueError as e:
                    print(f"  {story['id']}: judge failed: {e}")
        if run["error"]:
            print(f"  {story['id']}: {run['error']}")
        return {"run": run, "judge": graded, "key_facts": facts}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, plan))
    result = (
        metrics.card_metrics(rows)
        | metrics.clustering(stories, gold)
        | metrics.ops_metrics([r["run"] for r in rows])
    )
    result["judge_failures"] = sum(1 for r in rows if r["run"]["pre_card"] and not r["judge"])
    result["labeled_stories"] = sum(1 for s in stories if s["id"] in gold)
    print(f"spent this run: ${spent['cost_usd']:.4f}")
    return result


def config() -> dict:
    return {
        "prompts": [prompts.EXTRACT, prompts.SYNTHESIZE, prompts.VERIFY],
        "judge_prompt": prompts.JUDGE,
        "models": pipeline.models(),
        "judge_model": judge.judge_model(),
        "judge_effort": judge.judge_effort(),
    }


def check(result: dict, suite: str, baseline_path: Path = BASELINE) -> list[str]:
    """Problems that should fail the gate (empty list = pass)."""
    problems = []
    if result["stories"] and result["faithfulness"] is None:
        problems.append("no card survived to be judged")
    if result.get("judge_failures"):
        problems.append(
            f"the judge failed on {result['judge_failures']} stories, so they were not "
            "scored; re-run the eval"
        )
    base = json.loads(baseline_path.read_text()).get(suite) if baseline_path.exists() else None
    if base is None:
        if result["stories"]:
            print(f"::warning title=Eval gate not armed::no '{suite}' baseline in "
                  f"{baseline_path.name}: nothing is compared. Set one with "
                  f"`make eval-quick ARGS=--update-baseline` and commit the file.")  # fmt: skip
        return problems
    old, new = base["metrics"]["faithfulness"], result["faithfulness"]
    if old is not None and new is not None and new < old - TOLERANCE:
        problems.append(f"faithfulness dropped {old:.3f} -> {new:.3f} (tolerance {TOLERANCE})")
    # Faithfulness is measured over the sentences that survive, so it can be raised
    # by failing more stories or deleting more sentences. Those are gated too.
    old, new = base["metrics"].get("card_failure_rate"), result.get("card_failure_rate")
    if old is not None and new is not None and new > old + FAILURE_TOLERANCE:
        problems.append(
            f"card failure rate rose {old:.3f} -> {new:.3f} (tolerance {FAILURE_TOLERANCE})"
        )
    old = base["metrics"].get("sentences_kept_per_story")
    new = result.get("sentences_kept_per_story")
    if old and new is not None and new < old * KEPT_FLOOR:
        problems.append(
            f"sentences kept per story fell {old:.1f} -> {new:.1f} (floor {KEPT_FLOOR:.0%})"
        )
    return problems


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def print_table(result: dict) -> None:
    width = max(len(k) for k in result)
    for k, v in result.items():
        print(f"  {k:<{width}}  {'-' if v is None else v}")


def record(result: dict, suite: str) -> None:
    from common.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into evals (suite, git_sha, prompt_version, model, story_count, metrics)
               values (%s, %s, %s, %s, %s, %s)""",
            (suite, _git_sha(), prompts.SYNTHESIZE, pipeline.models()["synthesize"],
             result["stories"], json.dumps(result | {"config": config()})),
        )  # fmt: skip
        conn.commit()


def anthropic_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is needed: some stories aren't in the eval cache")
    import anthropic

    return anthropic.Anthropic()


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--suite", choices=["quick", "full"], default="quick")
    p.add_argument("--check", action="store_true")
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--record", action="store_true")
    args = p.parse_args()

    gold = load_gold()
    stories = select_suite(load_stories(), gold, args.suite)
    if not stories:
        print("no eval stories yet (make eval-snapshot): passing")
        return 0
    result = evaluate(stories, gold, anthropic_client)
    print(f"\n{args.suite} suite:")
    print_table(result)
    RESULTS_DIR.mkdir(exist_ok=True)
    doc = {"suite": args.suite, "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "git_sha": _git_sha(), "config": config(), "metrics": result}  # fmt: skip
    (RESULTS_DIR / f"{args.suite}.json").write_text(json.dumps(doc, indent=2) + "\n", "utf-8")
    if args.record:
        record(result, args.suite)
    if args.update_baseline:
        base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
        base[args.suite] = doc
        BASELINE.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
        print(f"baseline updated: {BASELINE}")
    if args.check:
        problems = check(result, args.suite)
        for msg in problems:
            print(f"::error title=Eval gate::{msg}")
        if problems:
            return 1
        print("eval gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
