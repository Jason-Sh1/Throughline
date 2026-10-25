# Throughline — Project Guide for Claude

Throughline is an AI news-understanding PWA. It clusters coverage of each news event across many outlets, then gives every story a short, **source-cited** briefing: 60-second brief, why it matters, key facts, background/glossary, timeline, how outlets frame it, and an optional "how this could affect you" card. It is published as finite daily editions (not an infinite feed).

This is a non-commercial portfolio project built by one student. Favor simple, explainable, cheap solutions over clever ones.

## Non-negotiable rules

1. **Every AI-generated sentence must carry `source_ids`** pointing to specific articles. No citation → the sentence is dropped.
2. **A verification pass runs after synthesis.** Sentences not supported by their cited excerpt are removed or rewritten.
3. **Never show AI-generated text under a publisher's name or logo.** Outlet headlines shown in the UI must be the outlet's real headline. Throughline's own headlines are labeled as Throughline's.
4. **Label all synthesized content** "AI-generated from N sources."
5. **Never display full article text.** Show our own synthesis + headline, outlet, timestamp, and a prominent link out. Raw extracted text is internal only and purged after 14 days.
6. **Never infer a user's political leaning.** Personalize story selection lightly; never personalize the framing panel or which outlets appear inside a story.
7. **Impact cards are informational only** — conditional language ("If you rent in…"), always state what's uncertain, never financial/legal/medical advice.
8. **Stay non-commercial.** No ads, no paid tier (required by Guardian dev key and AllSides CC BY-NC 4.0 license).
9. **Do not use NewsAPI.org in production** (dev-only, 24h delay).
10. **The LLM never writes chart code at runtime.** It emits a `viz_hint` + typed data; the frontend picks a deterministic component.

## Stack

- **Frontend:** Next.js (App Router, TypeScript), Tailwind, shadcn/ui, PWA (service worker, offline latest edition, Web Push).
- **DB/Auth:** Supabase Postgres + pgvector, Supabase Auth (magic link + Google), row-level security.
- **Worker:** Python 3.12 — feedparser, trafilatura (only where permitted), sentence-transformers (local embedding model), hdbscan, anthropic SDK, google-genai SDK.
- **Scheduling:** GitHub Actions cron (MVP) → Railway/Fly container later. Postgres `jobs` table for job state.
- **LLMs:** Claude Haiku 4.5 via Batch API for story synthesis/verification; a Gemini Flash-Lite-class model for extraction. Verify current model names/prices before coding against them.
- **Viz:** custom React timeline, Recharts, MapLibre GL.
- **Monitoring:** Sentry, `pipeline_runs` table, LLM tracing (Langfuse or similar).

## Repo layout

```
/web          Next.js app
/worker       Python ingestion + AI pipeline
  /ingest     RSS, GDELT, Guardian fetchers
  /cluster    dedupe, embeddings, clustering, burst detection
  /llm        prompts, schemas, extract/synthesize/verify
  /evals      gold set, metrics, eval runner
/supabase     migrations, seed data
/feeds        feeds.yaml (curated RSS list with spectrum labels)
/docs         architecture diagram, design decisions
ROADMAP.md    milestone checklist (keep updated)
```

## Data sources

- Publisher RSS feeds (primary) — curated in `/feeds/feeds.yaml`, spectrum-balanced, ≥25 feeds per category. Use conditional GET (ETag/Last-Modified). Respect robots.txt.
- GDELT 2.0 DOC API (15-min updates) — breaking-news radar, geo/entity tags.
- Guardian Open Platform (non-commercial key) — background/explainer full text. Read rate limits from response headers.
- AllSides bias ratings (CC BY-NC 4.0) — attribute everywhere they appear. Non-U.S. outlets are "Not rated."

## Story card schema (generated JSON)

Every text field is a list of `{ text, source_ids: [] }` sentences.

- `headline` (neutral, Throughline-labeled)
- `brief` (3 bullets)
- `why_it_matters`
- `key_facts`
- `background`
- `glossary` (≤5 terms: `{ term, definition, source_ids }`)
- `timeline` (5–8 nodes: `{ date, event, source_ids }`)
- `framing` (`widely_reported`, `reported_by_some`, `analysis_opinion`, representative headlines per bucket)
- `whats_next`
- `viz_hint` (`timeline | map | chart | none`) + typed `viz_data`
- `confidence` (outlet count, spectrum spread, `developing` flag)

## Prompting conventions

- Prompts live in `/worker/llm/prompts/` as versioned files (`synthesize_v3.md`). Never inline long prompts in code.
- Temperature 0–0.3. Strict JSON schema validation; retry once on invalid JSON, then fail the card.
- Rules in every synthesis prompt: attribute contested claims ("X said"), distinguish reported/alleged/confirmed, no speculation beyond sources, ~8th-grade reading level, no judgment adjectives.
- Use prompt caching for the fixed system prompt. Use the Batch API for all scheduled editions.
- Cache every generated card and personalization keyed by content hash; personalization is cached per (story, profile segment).

## Evals

- Gold set lives in `/worker/evals/gold/` — **hand-labeled by the human developer only. Do not generate gold labels with an LLM.**
- Metrics: sentence faithfulness, citation precision/recall, key-fact coverage, neutrality (rubric + perspective-swap delta), clustering pairwise F1 / B-cubed, breaking latency, cost per story, p95 generation time.
- Any change to prompts or models must run `make eval-quick` (30-story subset). Do not merge if faithfulness drops.

## UX rules

- Three depths only: Today screen → Story view → bottom-sheet overlays (never more than one overlay deep).
- Today shows 8–12 cards and a "You're caught up" end state. No infinite scroll, streaks, or dark patterns.
- ≤1 visual per story above the fold; no autoplay; no mid-story carousels.
- Notifications off by default except daily digest; breaking alerts capped at 2/day.
- WCAG 2.2 AA: keyboard + screen reader support for overlays, 4.5:1 contrast, never encode bias with color alone, text alternatives for charts, respect `prefers-reduced-motion`. Design at 375px first.
- Neutral palette; avoid red/blue as brand colors.

## Working conventions for Claude

- Work one milestone at a time from `ROADMAP.md`. Check off items and add notes when done.
- Explain non-obvious design choices in a short comment or in `/docs/decisions.md` — the developer needs to be able to explain everything in interviews.
- Write tests for pipeline logic (dedupe, clustering, schema validation, citation filtering). Run them before saying a task is done.
- Keep secrets in `.env` only; maintain `.env.example`.
- Prefer small, readable modules over frameworks. Ask before adding a new paid service or major dependency.
