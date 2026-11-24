# Evals

How we measure whether Throughline's briefings are accurate, and the current
numbers. Code: `worker/evals/`. Design notes: `docs/decisions.md` (M4).

## How it works

1. **Stories** (`worker/evals/stories/`): frozen snapshots of real clusters
   (`make eval-snapshot`). Each holds the sources the model sees (headline, outlet,
   link, time, RSS summary; never body text, since the repo is public) plus a few
   near-miss articles from outside the cluster.
2. **Gold labels** (`worker/evals/gold/`): written by the developer only, with
   `make label` (blind: articles are shuffled under opaque ids). Per story: which
   articles are the same event, and the key facts a good briefing must state.
3. **Run** (`make eval-quick` = 30 stories, `make eval` = all): the production
   pipeline (extract → synthesize → citation filter → verify) runs on each story;
   then a judge model (Claude Sonnet 5.5, stronger than the Haiku 5.5 being graded)
   grades every sentence against the source text. Runs and grades are cached by
   content hash, so only a prompt or model change costs money (~$1 for 30 stories).
4. **Gate**: CI runs `make eval-quick` on every PR and fails if faithfulness drops
   more than 0.02 below `worker/evals/baseline.json`.

## Metrics

| Metric | Meaning | Needs gold? |
|---|---|---|
| **faithfulness** | Share of sentences readers see that the judge rates supported by their cited sources. **The merge gate.** | no |
| faithfulness_pre_verify | Same, before the verification pass removed anything | no |
| verifier_catch_rate | Of sentences the judge rates unsupported, share verification removed | no |
| verifier_false_drop_rate | Of sentences the judge rates supported, share verification removed anyway | no |
| citation_precision | Of (sentence, cited source) pairs, share where the source supports the sentence | no |
| citation_recall | Of sources that support a sentence, share the sentence cites | no |
| key_fact_coverage | Share of the human's key facts stated in the final card | yes |
| cluster_pairwise_f1, cluster_bcubed_f1 | Clustering vs the human's same-event labels | yes |
| card_failure_rate | Stories that ended with no full card (citation filter or verification) | no |
| cost_per_story_usd | Extraction + synthesis + verification at Batch API prices | no |
| p95_seconds | 95th-percentile pipeline time per story (synchronous calls in the eval) | no |

Not yet measured: neutrality (rubric + perspective swap) and breaking latency,
both planned for v2.

**Caveat on the judge:** an LLM judge can be wrong. Its agreement with human
judgment hasn't been measured yet; a sample of human sentence labels would do that
(v2, with the gold set's growth to 150 stories).

## Baseline

_Filled in by the first CI run of the eval on the snapshot set; see below._
