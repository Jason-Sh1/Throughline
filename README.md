# Throughline

An AI news-understanding PWA. Throughline clusters coverage of each news event
across many outlets and gives every story a short, source-cited briefing,
published as finite daily editions.

Non-commercial portfolio project. Status: building the MVP, see [ROADMAP.md](ROADMAP.md).
Project rules for contributors (human or AI): [CLAUDE.md](CLAUDE.md).

## Layout

```
web/        Next.js app (App Router, TypeScript, Tailwind)
worker/     Python 3.12 ingestion + AI pipeline (uv)
supabase/   migrations and DB tests
feeds/      feeds.yaml, the curated RSS list
docs/       schema notes, design decisions
```

## Setup

Requires Node 22, [uv](https://docs.astral.sh/uv/), and `psql` for DB tests.

```bash
cp .env.example .env      # fill in as milestones need them
make setup
make test                 # worker lint + tests, web lint + typecheck
make validate-feeds
make dev                  # web app on http://localhost:3000
```

Database: create a Supabase project, then `supabase link` and `supabase db push`
to apply `supabase/migrations/`. Set `DATABASE_URL` and run `make sync-sources`.

To test migrations locally against any Postgres 16 with pgvector:
`make db-test PG_TEST_URL=postgresql://...` (use a throwaway database).

## Attribution

Media bias ratings from [AllSides](https://www.allsides.com/media-bias), licensed
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
