# Throughline Roadmap

Claude: check items off as they're completed and add a one-line note (date, anything notable). Don't start a milestone until the previous one's tests pass.

## MVP

### M1 — Setup & feeds (Week 1)
- [x] Monorepo scaffold (`/web`, `/worker`, `/supabase`, `/feeds`, `/docs`) — 2026-10-25
- [x] `.env.example`, Makefile (`make dev`, `make test`, `make ingest`, `make eval-quick`) — 2026-10-25; `ingest`/`eval-quick` are stubs until M2/M4
- [x] GitHub Actions CI: lint + tests for web and worker — 2026-10-25; also applies migrations + RLS tests on pgvector/pg16
- [x] **Human:** create Supabase project and `supabase db push` — 2026-10-25; dev project created by Jason, migrations applied via the Management API
- [x] Initial migration: `sources`, `articles`, `clusters`, `cluster_articles`, `entities`, `cards`, `editions`, `pipeline_runs`, `jobs`, `users`, `profiles`, `evals` — 2026-10-25; added `feeds`, `article_entities`, `edition_cards` (see docs/schema.md)
- [x] Enable pgvector — 2026-10-25; `vector(384)` + HNSW
- [x] `feeds.yaml` schema (url, outlet, category, region, allsides_rating) + validator script — 2026-10-25; 24 starter feeds, `STRICT=1` enforces launch targets
- [x] ~~**Human:**~~ curate 150+ feeds, spectrum-balanced, ≥25 per category — 2026-10-25; delegated to Claude by Jason: 281 feeds drafted, all returning 200 in the smoke run, passes `STRICT=1`; labels checked by a reviewer agent, then replaced by the AllSides import
- [x] AllSides ratings import script with attribution — 2026-10-25
- [x] **Human:** download AllSides ratings CSV and run `make allsides CSV=... APPLY=1` — 2026-10-25; Jason exported the table, 88 changes applied (CSV kept in project files)

### M2 — Ingestion & clustering (Week 2)
- [x] RSS fetcher with conditional GET, timeouts, per-host rate limits — 2026-10-25; also honours robots.txt
- [x] GDELT DOC API fetcher — 2026-10-25; tracked outlets only
- [x] Guardian API fetcher (reads rate-limit headers) — 2026-10-25
- [x] URL canonicalization + title hashing + SimHash near-duplicate detection (track "republished by N outlets") — 2026-10-25; via `duplicate_of`
- [x] Local embedding model; store vectors in pgvector — 2026-10-25; all-MiniLM-L6-v2, `make embed-deps`
- [x] Online clustering (cosine threshold, 48–72h window) + entity-overlap tiebreak — 2026-10-25; heuristic entities until M3
- [x] Nightly HDBSCAN re-cluster (merge/split) — 2026-10-25; scheduling lands with the cron in M8
- [x] Cluster inspection tool (CLI or simple admin page) — 2026-10-25; `make clusters`
- [x] 14-day raw-text purge job — 2026-10-25; `make purge`
- [x] Tests for dedupe and clustering — 2026-10-25; plus fetchers and DB pipeline tests
- [x] ~~**Human:**~~ inspect 50 clusters, tune threshold, note results in `/docs/decisions.md` — 2026-10-25; delegated to Claude: threshold 0.62→0.66, gray margin 0.10→0.05 from a 12-config sweep (`cluster/sweep.py`) over a live 72h window

### M3 — LLM extraction & synthesis (Week 3)
- [x] Pydantic/JSON schemas for extraction output and story card — 2026-10-25; `worker/llm/schemas.py`
- [x] Extraction prompt v1 (entities, atomic claims w/ source_ids, dates, numbers, locations, viz_hint) — 2026-10-25; Claude Haiku 5.5 (Gemini free tier too small, see decisions.md)
- [x] Synthesis prompt v1 (full card schema, per-sentence source_ids) — 2026-10-25; Claude Haiku 5.5, effort low, structured outputs
- [x] Batch API submission + polling + result ingestion — 2026-10-25; `llm_batches` table, `make edition-collect` for late batches
- [x] Prompt caching for system prompt — 2026-10-25
- [x] Citation filter: drop any sentence without valid source_ids — 2026-10-25; also strips invented ids
- [x] Edition builder: rank clusters, select top 40–60, long tail gets one-liners — 2026-10-25; top 50 + 30 one-liners, spend guard
- [x] Render first cards as static JSON — 2026-10-26; `make edition-json`, samples + cost in docs/samples/

### M4 — Verification & evals (Week 4)
- [ ] Verification pass (sentence vs. cited excerpt → supported / unsupported / contradicted)
- [ ] Confidence computation (outlet count, spectrum spread, single-source flag)
- [ ] Eval runner + metrics (faithfulness, citation precision/recall, key-fact coverage, clustering F1)
- [ ] `make eval-quick` (30-story subset) wired into CI as a gate
- [ ] **Human:** hand-label 50-story gold set in `/worker/evals/gold/`
- [ ] Record baseline numbers in `/docs/evals.md`

### M5 — Core UI (Week 5)
- [ ] Today screen: 8–12 cards, spectrum bar, topic chip, updated/developing tag, "You're caught up"
- [ ] Story view: collapsible sections in schema order
- [ ] Receipts: tap a sentence → bottom sheet with source excerpt + outlet + link
- [ ] Glossary overlays (≤5 underlined terms)
- [ ] Timeline component
- [ ] "AI-generated from N sources" labels
- [ ] Source list with prominent link-outs

### M6 — Auth, onboarding, personalization (Week 6)
- [ ] Supabase Auth (magic link + Google)
- [ ] 30-second onboarding: topics, optional location, optional "about you" chips
- [ ] Profiles stored as coarse segments
- [ ] Ranking: importance × recency + topic boost; ≥50% shared top stories; MMR diversity
- [ ] "Why am I seeing this?" on each card
- [ ] Personal impact card, cached per (story, segment)
- [ ] Interests page with edit/delete; data export/delete

### M7 — Bias display, accessibility, PWA (Week 7)
- [ ] Coverage spectrum bar with "Not rated" bucket + AllSides attribution
- [ ] Accessibility pass (WCAG 2.2 AA checklist in `/docs/a11y.md`)
- [ ] PWA: installable, offline latest edition
- [ ] Privacy notice + "About our AI" page
- [ ] "Report a problem" button on every card → feeds review queue

### M8 — Deploy & launch (Week 8)
- [ ] Deploy web to Vercel, custom domain
- [ ] Scheduled editions (3/day) via GitHub Actions cron
- [ ] Sentry + `pipeline_runs` admin dashboard + token-spend tracking
- [ ] Public eval metrics page
- [ ] README: pitch, GIF, live link, architecture diagram, eval table, design decisions, legal notes, setup
- [ ] **Human:** record 90-second demo video
- [ ] **Human:** soft-launch to 20–50 people, collect feedback in `/docs/feedback.md`

## v2 (Weeks 9–14)
- [ ] Breaking lane: 5–10 min polling, burst score, "Developing" cards, capped Web Push (≤2/day)
- [ ] Facts-vs-framing panel + three representative headlines + Blindspot flags
- [ ] Story Q&A with citations + refusal when sources don't cover it; per-user rate limit
- [ ] "Explain simpler" toggle
- [ ] Maps (MapLibre) and auto-charts (Recharts) from `viz_hint`
- [ ] Reusable background explainer cards
- [ ] Gold set → 150 stories; neutrality perspective-swap eval; cost dashboard
- [ ] Move worker to always-on container if needed

## Stretch
- [ ] Daily audio briefing (TTS)
- [ ] Weekly scrollytelling deep-dive
- [ ] Entity graph; follow people/orgs; "since you last read" diffs
- [ ] Loaded-language highlighter
- [ ] Google Fact Check Tools API integration
- [ ] Multilingual sources + translation
- [ ] Browser extension: explain the article you're reading
- [ ] Expo wrapper for app stores

## Notes
<!-- Claude appends dated notes here -->
- 2026-10-25 (M1): Starter feed URLs could not be fetched from the dev sandbox (news sites blocked), so treat them as unverified until the M2 fetcher runs. Ratings in feeds.yaml are placeholders until the AllSides import. 29 worker tests + RLS smoke test passing.
- 2026-10-25 (M2): 90 worker tests passing locally against Postgres + pgvector. Fetchers are tested with mocked HTTP only, because the dev sandbox can't reach news sites, GDELT, the Guardian or Hugging Face. `ingest-smoke.yml` runs the real pipeline on GitHub Actions. Clustering explained in docs/clustering.md.
- 2026-10-25 (M2 live run on Actions): 577 articles from 27/28 feeds. Plain HDBSCAN over-merged related stories (137 moves, 125 merges); with the coherence guards, 25 moves and 15 merges, and the top clusters are single events. Open issues: WaPo business feed returns 502; GDELT answered 429 from GitHub's shared runners, so GDELT is unproven until it runs from our own scheduler.
- 2026-10-26 (M3): First live edition on Actions: 10 stories, 5 full cards, $0.008 total (~$0.0011 per full card with Claude Haiku 5.5 batch for both steps). Gemini's free tier (20 requests/day) was too small for extraction. Cards stay `draft` until M4 verification. 123 worker tests passing (also run locally against Postgres + pgvector on 2026-10-31).
