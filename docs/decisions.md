# Design decisions

Short notes on non-obvious choices, newest first.

## 2026-10-25 — M2

- **SimHash on words, with a threshold of 10 bits rather than the textbook 3.** The
  classic setting (3-grams, 3 bits) is calibrated for whole web pages; we only
  have a title plus an RSS summary. On test pairs, lightly edited wire copies
  were 5–8 bits apart and independent write-ups of the same event 19–28. See
  `cluster/dedupe.py`.
- **Duplicates are kept, linked with `duplicate_of`.** A wire story that 6
  outlets run is 6 outlets' worth of coverage for the spectrum bar, but only
  one text for embeddings and the LLM.
- **Cosine threshold 0.62, 72 h window, entity tiebreak.** Starting values for
  all-MiniLM-L6-v2 on title + summary; to be tuned on real clusters (human step).
  The entity rule only acts near the threshold, so a shared name like "Trump"
  alone can't glue unrelated stories together (it needs ≥ 2 shared names and
  similarity within 0.10 of the threshold).
- **Heuristic entities until M3.** Capitalized phrases from title + summary. Cheap
  and good enough for "do these stories mention the same people?". M3's
  extraction step will produce typed entities.
- **scikit-learn's HDBSCAN** instead of the `hdbscan` package: same algorithm,
  no extra compiled dependency. Noise points keep their online assignment.
- **Guards on the nightly re-cluster.** The first live run (529 articles, 24
  feeds) showed plain HDBSCAN over-merging: one 22-article "cluster" mixed three
  different election-ad stories, and another mixed IMF, Iran and Houthi stories.
  Two guards were added. A leave-one-out coherence floor at the online threshold
  turns weak members back into noise; it measures each member against the
  *other* members, because including the member itself is far too lenient for
  small groups. And a cluster is merged only when all of its articles moved.
- **GDELT only for outlets we already track.** Untracked domains have no
  spectrum label, so storing them would skew the coverage bar.
- **sentence-transformers is installed by `make embed-deps`, not locked.** The
  default Linux PyTorch wheel bundles CUDA (~5 GB); we pull the CPU build from
  PyTorch's index. CI unit tests use a fake embedder and don't need it.
- **Live smoke test as a separate, non-required workflow.** The dev sandbox can't
  reach news sites, so `ingest-smoke.yml` runs the real fetch → embed → cluster
  on GitHub Actions for PRs touching ingestion. It's not a required check
  because a publisher's feed being down shouldn't block merges.

## 2026-10-25 — M1

- **feeds.yaml is grouped by outlet**, not one row per feed. Region and AllSides
  rating belong to the outlet, so writing them once means two feeds from the
  same outlet can't disagree.
- **Non-U.S. outlets are always `not_rated`**, enforced by the validator, even
  when AllSides has a page for them (it does for BBC and The Guardian). AllSides
  calibrates ratings to U.S. politics, so applying them abroad would mislead.
- **AllSides import writes into feeds.yaml** (round-trip YAML via `ruamel.yaml`,
  so comments survive) rather than straight into the database. That keeps one
  source of truth that's reviewable in a diff; `make sync-sources` then loads it.
  The import reads a CSV downloaded by hand; we don't scrape AllSides. When
  AllSides lists separate news and opinion ratings, the news rating wins because
  we cluster news coverage.
- **Embeddings are `vector(384)`**, sized for small local sentence-transformers
  models (all-MiniLM-L6-v2, bge-small-en-v1.5). Changing models means a migration.
  HNSW index for cosine similarity: no training step, good recall at our scale.
- **One cluster per article** (`cluster_articles.article_id` is the primary key).
  Overlapping clusters would make "how many outlets covered this" ambiguous.
- **Job queue in Postgres** (`jobs` + `SKIP LOCKED`) instead of Redis/a queue
  service: one less service to run and pay for; volume is tiny.
- **`users` + `profiles` split**: `users` is account-level settings, `profiles`
  is the coarse personalization data that "delete my data" can wipe on its own.
- **CI tests migrations against plain Postgres + pgvector** with a small shim
  (`supabase/tests/local_auth_shim.sql`) for `auth.users`, `auth.uid()` and the
  `anon`/`authenticated` roles, instead of booting the full Supabase stack.

### M2 follow-up: fixes from the first full live run (2026-10-25)

The first smoke run over the full feed list (~7.9k articles) showed three problems:

- **False near-duplicates.** Unrelated articles were marked as SimHash duplicates. With only
  title + RSS summary to fingerprint, stopwords ("the", "said", "that") and newsletter
  boilerplate dominated the features. Fix: SimHash now ignores stopwords, and a near-duplicate
  must *also* share at least half of its headline content words (`TITLE_MIN_JACCARD = 0.5`).
  Real wire copies keep the headline mostly intact; boilerplate collisions don't. With
  stopwords gone each word weighs more, so lightly edited copies measured 5-11 bits apart;
  the threshold moved from 10 to 12 (independent coverage still sits ~29 bits away).
- **Old articles clustered.** Some feeds re-serve items from months ago. The clustering window
  used `fetched_at`, so a March article fetched today counted as fresh. Online clustering and
  the nightly re-cluster now window on `coalesce(published_at, fetched_at)`.
- **Embedding cap.** The first run hit the 5,000-article embed limit. Raised to 20,000
  (all-MiniLM-L6-v2 on CPU does that in a few minutes).

Feeds: 52 dead feeds (404, robots.txt disallow, bot blocks) were dropped and replaced, mostly
with extra sections from outlets whose other feeds already worked. The list is 268 feeds and
passes `make validate-feeds STRICT=1` (every category ≥25, left/right ratio 78/53 = 1.47).

Label review (agent-assisted, from memory because allsides.com is blocked from the sandbox):
Washington Examiner moved to Lean Right. RealClearPolitics/RealClearEnergy were dropped,
because they're aggregators that re-link other outlets and would double-count in clusters.
Fox's opinion feed was dropped (opinion shouldn't enter as news). Country fixes: Phys.org and
Medical Xpress (Isle of Man), New Atlas (AU), Universe Today (CA). The remaining ratings stay
placeholders until the AllSides CSV import. **Open for M3:** agencies (NASA, CDC, WHO) and
journals (Nature, Lancet) shouldn't count toward a story's outlet count or spectrum spread in
`confidence`; opinion-heavy outlets need to land in the framing panel's analysis/opinion bucket.

### M2 follow-up: first cluster threshold tuning (2026-10-25)

Delegated to Claude by Jason. Method: `cluster/sweep.py` replays online clustering over one live
72h window (3,482 articles from 281 feeds, smoke run on PR #2) for 12 threshold × gray-margin
combinations and prints the biggest clusters for three of them. Read-only, so it costs one CI run.

| threshold / gray | multi-outlet stories | 5+ outlets | largest |
|---|---|---|---|
| 0.62 / 0.10 (old) | 290 | 57 | 39 |
| **0.66 / 0.05 (new)** | 277 | 47 | 35 |
| 0.70 / 0.05 | 256 | 44 | 30 |

Over-merges at the old setting, all gone at 0.66 / 0.05:
- Brazil election cluster absorbed a Guardian column on Brazilian football.
- France protests cluster was seeded by Foreign Policy's generic "What in the World?" newsletter.
- Starlink/Ukraine cluster pulled in a separate story on GOP oversight of SpaceX.
- A 29-article "Premier League weekend" cluster (3 outlets) merged unrelated match reports.

At 0.70 the big events (Alaska DOJ suit, Machado's return, Brazil, France) stay intact but
~20 fewer multi-outlet stories form, i.e. we start splitting smaller events. Over-merging is the
worse failure for us (a briefing that mixes two events is wrong; a split event is just a
smaller card), so we take the stricter of the settings that don't visibly split, 0.66.
The gray margin shrank because most bad joins came through the entity rule, whose entities are
still capitalized-phrase heuristics; revisit both after M3 replaces them with LLM extraction.

**Known remaining issues:** a few Daily Beast pieces with generic headlines land in the
Starlink cluster (their summaries are about it, so arguably correct); sports match reports
from one league still group loosely. Re-run the sweep after any embedding or entity change.
