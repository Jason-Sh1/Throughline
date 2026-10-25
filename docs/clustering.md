# How ingestion and clustering work

Code: `worker/ingest/` and `worker/cluster/`. Run it with `make ingest`
(fetch + embed + cluster), `make recluster` nightly, `make clusters` to look.

## 1. Fetch

- **RSS** (`ingest/rss.py`): every active feed in the `feeds` table. Each request
  sends the `ETag`/`Last-Modified` from last time, so unchanged feeds answer
  `304 Not Modified` with no body. 15 s timeout, at most one request every 2 s
  per publisher host, robots.txt checked once per host per run.
- **GDELT** (`ingest/gdelt.py`): asks GDELT's DOC API for the last hour of
  articles from the outlets we track (8 domains per query, 5 s apart as GDELT
  asks). Catches stories before slow feeds update. Articles from outlets we
  don't track are skipped, so everything stored has a known spectrum label.
- **Guardian** (`ingest/guardian.py`): last 6 hours from the Content API, with
  full text kept as internal `raw_text`. Reads the remaining quota from the
  response headers and stops early instead of hitting the limit.

## 2. Dedupe (`cluster/dedupe.py`, `ingest/store.py`)

1. **Canonical URL**: strip tracking params (`utm_*`, `fbclid`, ...), `www.`,
   AMP paths, fragments, trailing slashes. Same canonical URL = same row.
2. **Title hash**: same normalized headline within 72 h = a republished copy
   (usually a wire story). Marked `duplicate_of` the first copy.
3. **SimHash**: a 64-bit fingerprint of title + summary; near-identical text
   (≤ 10 of 64 bits different) = a lightly edited copy. Also `duplicate_of`.

Duplicates aren't thrown away. They still count as coverage ("republished by N
outlets"), and they join the same cluster as their original.

## 3. Embed (`cluster/embed.py`)

Each original article's "title. summary" goes through a small local model
(all-MiniLM-L6-v2) that turns text into 384 numbers. Articles about the same
thing get vectors pointing in nearly the same direction, measured by **cosine
similarity**: 1.0 is identical meaning, ~0 is unrelated.

## 4. Online clustering (`cluster/online.py`)

New articles are handled in publish order. For each one we compare it to the
**centroid** (average vector) of every cluster that got new articles in the
last 72 hours:

| Situation | Result |
|---|---|
| best similarity ≥ `threshold` (0.66) | join that cluster |
| two or more clusters within 0.03 of the best | join the one sharing the most names |
| best is in the gray zone (0.61–0.66) and shares ≥ 2 names | join it anyway |
| otherwise | start a new cluster |

"Names" are capitalized phrases from the headline and summary (`cluster/entities.py`),
a stand-in until M3's LLM extraction produces real entities.

### What the threshold controls

It's how alike two articles must be to count as the same event.

- **Too high** → **over-splitting**: one event shows up as several stories
  ("Fed holds rates" and "Powell says cuts may come later" end up apart).
- **Too low** → **over-merging**: different events get lumped together
  (two unrelated Fed stories in the same week become one card).

Over-merging is worse for Throughline, because it puts facts from one event
into another event's briefing. So when in doubt, err high, and let the nightly
pass and the entity rule repair the splits.

The 72-hour window keeps a new "Fed holds rates" story from joining last
month's Fed cluster.

## 5. Nightly re-cluster (`cluster/recluster.py`)

Online clustering is greedy: it decides once and never looks back. Every night
we run **HDBSCAN** over all articles from the last 72 hours at once. It finds
dense groups without being told how many to expect, and leaves outliers as
"noise". We then:

- **merge** online clusters that HDBSCAN sees as one group (the cluster with the
  most articles in the group survives; the others are marked `merged_into` it),
- **split** off articles that HDBSCAN places in a different group,
- leave "noise" articles where they were, so a quiet night doesn't shuffle
  stable stories.

HDBSCAN finds dense regions, which isn't the same as "one event". On the first
live run it lumped all the political-ad stories together, and all the
Middle East stories together. Two guards stop that:

- **Coherence floor:** an article whose similarity to the rest of its group is
  below the online threshold goes back to "noise". So the nightly pass can only
  make merges that online clustering would also have accepted.
- **Whole-cluster merges only:** a cluster is merged away only if *all* of its
  articles landed in the new group. Otherwise only the matching articles move.

Cluster ids stay stable where possible, so links to a story survive.

## Tuning (ROADMAP M2 human step)

```bash
make clusters ARGS="--hours 24 --min-outlets 2 --limit 50"
make clusters ARGS="--id 123 --urls"
```

Go through 50 clusters and count over-merges and over-splits. Then set
`CLUSTER_THRESHOLD` in `.env`, re-run, and record the numbers in
`docs/decisions.md`. Reasonable range for this model: 0.55–0.75.
