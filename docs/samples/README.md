# Sample cards (M3)

From the first live edition, built 2026-10-26 03:17 UTC on GitHub Actions
(`edition-smoke.yml`, run 40262739797): 281 feeds ingested into a throwaway
database, top 10 stories ranked, 5 full cards + 5 one-liners. Claude Haiku 5.5,
effort `low`, Batch API, prompts `extract_v1` / `synthesize_v1`.

- `card-1.json` … `card-5.json`: the five full cards.
- `edition-2026-10-26-morning.json`: the whole edition as `make edition-json`
  exports it.

These are **drafts**: the M4 verification pass hasn't run on them. The
`viz_hint` in these files predates the rule that drops a timeline with fewer
than 3 dates (all five asked for a timeline; most have 2-3 nodes).

## Token usage and cost

| Step | Requests | Input tokens | Output tokens | Cost | Per story |
|---|---|---|---|---|---|
| Extraction (batch) | 10 | 46,429 | 14,822 | $0.0049 | $0.00049 |
| Synthesis (batch) | 5 | 36,933 | 7,560 | $0.0030 | $0.00061 |
| **Edition** | | 83,362 | 22,382 | **$0.0080** | |

A full card costs about **$0.0011** (extraction + synthesis), a one-liner
$0.0005. A default edition (50 full + 30 one-liners) with nothing cached would
cost about $0.07; three a day for 30 days is about $6.30, under the $20/month
cap, and unchanged stories reuse their cached cards. The pre-submit estimate was
$0.0128 against $0.0080 actual, so the spend guard errs high.

## What to look at (notes for M4)

- Citations and framing headlines come out right: every sentence cites
  articles, and representative headlines are the outlets' own.
- Card 5 (Brazil) has an off-topic `whats_next` sentence about a municipal
  transport secretary: one article in the cluster was about a different story.
  Verification should catch it; clustering should not have joined it.
- Timelines are short because RSS summaries carry few dates.
- The opinion flag (URL-based) missed Hot Air's post and a Free Press column;
  the model still put them in `analysis_opinion` from their tone.
