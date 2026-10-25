# Milestone Prompts for Claude Code

Paste one prompt per session. After each, run the app/tests yourself, review the diff, commit, then move on.

---

## Before M1 — kickoff

> Read CLAUDE.md and ROADMAP.md. Don't write code yet. Summarize the architecture back to me in 10 bullets, list any decisions you think are ambiguous or risky, and check the current names and prices of the LLM models and the free-tier limits of Supabase, Vercel, GDELT and the Guardian API. Propose any changes to CLAUDE.md before we start.

## M1 — Setup & feeds

> Implement milestone M1 from ROADMAP.md except the items marked **Human**. Scaffold the monorepo, Makefile, CI, Supabase migrations (with pgvector), the feeds.yaml schema and validator, and an AllSides ratings import script (with attribution). Add ~20 example feeds across the spectrum so I can test; I'll curate the full list myself. Write tests, run them, update ROADMAP.md, and explain the database schema to me in plain English at the end.

## M2 — Ingestion & clustering

> Implement milestone M2. Build the RSS, GDELT and Guardian fetchers, dedupe (canonical URLs + title hash + SimHash), local embeddings into pgvector, online clustering with a configurable cosine threshold and time window, entity-overlap tiebreak, a nightly HDBSCAN re-cluster, the raw-text purge job, and a CLI that prints clusters with their headlines so I can inspect them. Write tests for dedupe and clustering. Explain how the clustering works and what the threshold controls, then update ROADMAP.md.

## M3 — Extraction & synthesis

> Implement milestone M3. Define the extraction and story-card schemas from CLAUDE.md with Pydantic, write versioned prompts in /worker/llm/prompts/, implement Batch API submission/polling with prompt caching, the citation filter, and the edition builder. Run it on today's clusters and save 5 sample cards as JSON in /docs/samples/. Show me the token usage and estimated cost per card. Update ROADMAP.md.

## M4 — Verification & evals

> Implement milestone M4. Add the verification pass and confidence scoring, then build the eval runner and metrics described in CLAUDE.md, plus `make eval-quick` as a CI gate. Create the gold-set file format and a small labeling helper (CLI or simple web form) that makes it fast for ME to label stories. Do not create gold labels yourself. Update ROADMAP.md.

*(Then: label 50 stories yourself and run the evals.)*

> Run the full eval on my gold set, write baseline results to /docs/evals.md, and list the top 3 failure patterns with suggested prompt fixes.

## M5 — Core UI

> Implement milestone M5. Before coding, propose a minimal visual design (neutral palette, typography, spacing) and the component list. After I approve, build the Today screen, Story view, receipts bottom sheet, glossary overlays and timeline, mobile-first at 375px, reading the edition JSON. Follow the UX rules in CLAUDE.md strictly. Update ROADMAP.md.

## M6 — Auth & personalization

> Implement milestone M6. Add Supabase Auth, the 30-second onboarding, coarse profile segments, the ranking formula with the ≥50% shared top stories rule and MMR, "Why am I seeing this?", the cached personal impact card, and data export/delete. Document the ranking formula in /docs/decisions.md. Update ROADMAP.md.

## M7 — Bias, accessibility, PWA

> Implement milestone M7. Add the coverage spectrum bar with the "Not rated" bucket and AllSides attribution, do an accessibility audit against WCAG 2.2 AA and fix issues (document in /docs/a11y.md), make the app an installable PWA with offline latest edition, and add the privacy notice, "About our AI" page and "Report a problem" flow. Update ROADMAP.md.

## M8 — Deploy

> Implement milestone M8. Walk me through deploying to Vercel and Supabase step by step (I'll do the account/dashboard steps), set up the 3x-daily cron, Sentry, the admin dashboard with token spend, and the public eval metrics page. Write the README following the structure in /docs or CLAUDE.md, including a Mermaid architecture diagram and the eval results table. Update ROADMAP.md.

---

## Useful anytime

- **Explain:** "Explain how [module] works as if I'm being asked about it in an interview. What are the trade-offs and what would I do differently at scale?"
- **Debug:** "This card has a wrong fact: [paste]. Trace where it came from (source → extraction → synthesis → verification) and fix the weakest step. Add it to the eval set as a regression case."
- **Cost check:** "Summarize token spend and cost per story for the last 7 days and suggest the biggest saving."
- **Review:** "Review the last milestone's code for bugs, security issues, and anything that violates CLAUDE.md."
