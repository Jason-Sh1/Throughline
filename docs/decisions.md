# Design decisions

Short notes on non-obvious choices, newest first.

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
