"""Eval metrics. Pure functions over pipeline outputs, judge grades and gold labels.

Definitions (also in docs/evals.md):

- faithfulness: share of the sentences readers see (kept by verification) that
  the judge rates `supported` by their cited sources. The merge gate.
- faithfulness_pre_verify: the same over every sentence before verification.
- verifier_catch_rate: of the sentences the judge rates not supported, the
  share verification removed. verifier_false_drop_rate: of the sentences the judge
  rates supported, the share verification removed anyway.
- citation_precision: of all (kept sentence, cited source) pairs, the share
  where that source supports the sentence.
- citation_recall: of all sources that support a kept sentence (cited or not),
  the share that were cited.
- key_fact_coverage: share of the human's key facts stated by a kept sentence
  (labeled stories only).
- cluster_pairwise_f1 / cluster_bcubed_f1: clustering vs the human's "same
  event?" labels, over the sources and near-miss articles in each snapshot.
- card_failure_rate, cost_per_story_usd (batch prices), p95_seconds.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or p + r == 0:
        return None
    return round(2 * p * r / (p + r), 4)


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


# --- Clustering --------------------------------------------------------------------


def pairwise(pred: dict[str, str], gold: dict[str, str]) -> tuple[int, int, int]:
    """(true-positive pairs, predicted pairs, gold pairs) for one story. Items
    share a cluster when their labels are equal."""
    tp = pp = gp = 0
    for a, b in combinations(sorted(pred), 2):
        same_p, same_g = pred[a] == pred[b], gold[a] == gold[b]
        pp += same_p
        gp += same_g
        tp += same_p and same_g
    return tp, pp, gp


def bcubed(pred: dict[str, str], gold: dict[str, str]) -> tuple[float, float, int]:
    """(sum of per-item precision, sum of per-item recall, items) for one story."""
    p_sum = r_sum = 0.0
    for item in pred:
        p_group = {x for x in pred if pred[x] == pred[item]}
        g_group = {x for x in gold if gold[x] == gold[item]}
        both = len(p_group & g_group)
        p_sum += both / len(p_group)
        r_sum += both / len(g_group)
    return p_sum, r_sum, len(pred)


def cluster_labels(story: dict, gold: dict) -> tuple[dict, dict]:
    """Predicted and gold labels over the articles the human labeled. Articles in
    the cluster share the label "story"; every other article is its own group."""
    pred, true = {}, {}
    for a in story["articles"]:
        label = gold["membership"].get(a["ref"])
        if label not in ("same", "different"):
            continue
        pred[a["ref"]] = "story" if a["in_cluster"] else a["ref"]
        true[a["ref"]] = "story" if label == "same" else a["ref"]
    return pred, true


def clustering(stories: list[dict], gold: dict[str, dict]) -> dict:
    tp = pp = gp = 0
    p_sum = r_sum = n = 0.0
    labeled = 0
    for s in stories:
        g = gold.get(s["id"])
        if not g or not g.get("membership"):
            continue
        labeled += 1
        pred, true = cluster_labels(s, g)
        a, b, c = pairwise(pred, true)
        tp, pp, gp = tp + a, pp + b, gp + c
        x, y, z = bcubed(pred, true)
        p_sum, r_sum, n = p_sum + x, r_sum + y, n + z
    pw_p, pw_r = _ratio(tp, pp), _ratio(tp, gp)
    b_p, b_r = _ratio(p_sum, n), _ratio(r_sum, n)
    return {
        "cluster_stories": labeled,
        "cluster_pairwise_precision": pw_p,
        "cluster_pairwise_recall": pw_r,
        "cluster_pairwise_f1": _f1(pw_p, pw_r),
        "cluster_bcubed_f1": _f1(b_p, b_r),
    }


# --- Card quality ---------------------------------------------------------------------


def card_metrics(rows: list[dict]) -> dict:
    """rows: {run, judge (or None), key_facts} per story."""
    c: Counter = Counter()
    for row in rows:
        run, j = row["run"], row["judge"]
        c["stories"] += 1
        if run["card"] is None:
            c["failed"] += 1
        if not j:
            continue
        kept = set(run["kept"])
        for g in j["result"]["sentences"]:
            ok = g["verdict"] == "supported"
            c["pre_total"] += 1
            c["pre_supported"] += ok
            if g["id"] in kept:
                c["kept"] += 1
                c["kept_supported"] += ok
                cited_ok = sum(cs["supports"] for cs in g["cited_support"])
                c["cited_pairs"] += len(g["cited_support"])
                c["cited_supporting"] += cited_ok
                c["all_supporting"] += cited_ok + len(g["uncited_support"])
            else:
                c["dropped_bad" if not ok else "dropped_good"] += 1
            c["bad" if not ok else "good"] += 1
        facts = row["key_facts"]
        if facts:
            c["fact_stories"] += 1
            c["facts"] += len(facts)
            c["facts_covered"] += sum(
                1 for f in j["result"]["key_facts"] if set(f["covered_by"]) & kept
            )
    return {
        "stories": c["stories"],
        "faithfulness": _ratio(c["kept_supported"], c["kept"]),
        "faithfulness_pre_verify": _ratio(c["pre_supported"], c["pre_total"]),
        "verifier_catch_rate": _ratio(c["dropped_bad"], c["bad"]),
        "verifier_false_drop_rate": _ratio(c["dropped_good"], c["good"]),
        "citation_precision": _ratio(c["cited_supporting"], c["cited_pairs"]),
        "citation_recall": _ratio(c["cited_supporting"], c["all_supporting"]),
        "key_fact_coverage": _ratio(c["facts_covered"], c["facts"]),
        "key_fact_stories": c["fact_stories"],
        "sentences_judged": c["pre_total"],
        "card_failure_rate": _ratio(c["failed"], c["stories"]),
    }


def ops_metrics(runs: list[dict]) -> dict:
    costs = [r["spend"]["cost_usd_batch"] for r in runs]
    return {
        "cost_per_story_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "p95_seconds": p95([r["seconds"] for r in runs]),
    }
