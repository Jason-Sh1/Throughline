"""Eval runner: `make eval-quick` (30 stories, the CI gate) or `make eval` (all).

For each story: run the pipeline (evals/pipeline.py), have the judge grade the
card (evals/judge.py), then compute the metrics (evals/metrics.py) and compare
faithfulness with the committed baseline.

Every pipeline run and judge grade is cached on disk, keyed by a hash of
everything that could change it (story, prompt *texts*, models, efforts, gold
key facts). An unchanged pipeline costs nothing to re-evaluate, so CI can run the
gate on every PR and only pays when a prompt or model actually changed.

Options:
  --check            fail if faithfulness dropped more than TOLERANCE vs baseline
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
# Rough per-story cost of an uncached run, for the spend guard (standard prices):
# Haiku pipeline ~$0.006, Sonnet judge ~$0.04.
EST_RUN_USD, EST_JUDGE_USD = 0.006, 0.04
MAX_EVAL_COST_USD = float(os.environ.get("MAX_EVAL_COST_USD", "3.00"))


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]


def _prompt_text(name: str) -> str:
    return prompts.load(name)[0]


def run_key(story: dict) -> str:
    texts = [_prompt_text(n) for n in (prompts.EXTRACT, prompts.SYNTHESIZE, prompts.VERIFY)]
    return _hash("run", story, texts, pipeline.models())


def judge_key(rkey: str, facts: list[str]) -> str:
    return _hash("judge", rkey, _prompt_text(prompts.JUDGE), judge.judge_model(),
                 judge.judge_effort(), facts)  # fmt: skip


def _cached(kind: str, key: str) -> dict | None:
    p = CACHE_DIR / kind / f"{key}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _store(kind: str, key: str, data: dict) -> None:
    p = CACHE_DIR / kind / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def evaluate(stories: list[dict], gold: dict, client_factory, workers: int = 4) -> dict:
    plan = []
    for s in stories:
        facts = (gold.get(s["id"]) or {}).get("key_facts", [])
        rk = run_key(s)
        plan.append((s, facts, rk, judge_key(rk, facts)))
    need_run = sum(_cached("run", rk) is None for _, _, rk, _ in plan)
    need_judge = sum(_cached("judge", jk) is None for *_, jk in plan)
    estimate = need_run * EST_RUN_USD + need_judge * EST_JUDGE_USD
    print(f"{len(plan)} stories: {need_run} pipeline runs and {need_judge} judge calls "
          f"not cached (est. ${estimate:.2f})")  # fmt: skip
    if estimate > MAX_EVAL_COST_USD:
        raise SystemExit(f"estimated ${estimate:.2f} > MAX_EVAL_COST_USD ${MAX_EVAL_COST_USD:.2f}")
    client = client_factory() if (need_run or need_judge) else None
    spent = {"cost_usd": 0.0}

    def one(item):
        story, facts, rk, jk = item
        run = _cached("run", rk)
        if run is None:
            run = pipeline.run_story(client, story)
            spent["cost_usd"] += run["spend"]["cost_usd"]
            _store("run", rk, run)
        graded = None
        if run["pre_card"] is not None:
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
    base = json.loads(baseline_path.read_text()).get(suite) if baseline_path.exists() else None
    if base is None:
        print(f"no {suite} baseline yet: reporting only (run with --update-baseline to set one)")
        return problems
    old, new = base["metrics"]["faithfulness"], result["faithfulness"]
    if old is not None and new is not None and new < old - TOLERANCE:
        problems.append(f"faithfulness dropped {old:.3f} -> {new:.3f} (tolerance {TOLERANCE})")
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
