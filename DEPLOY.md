# Deploying Throughline

A checklist for the human steps. Everything in the repo is ready for them; nothing here
has been done yet. Order matters: database, then the pipeline, then the website.

**What gets created:** nothing new. It uses the Supabase project that already exists
(`throughline-dev`), the GitHub repository's Actions, and a Vercel project on the free
`*.vercel.app` domain.

**What it costs:** Supabase and Vercel free tiers. The pipeline calls the Anthropic
API: the one measured data point is $0.008 for a 10-story edition with 5 full
briefings on 2026-10-26, before the verification pass existed. Verification adds a
second pass per card, so expect a few cents per edition at the default size (10
briefings, 6 short items), three times a day. This estimate is **not measured yet**;
check the first few runs. `MAX_EDITION_COST_USD` (default $0.20) refuses to submit an
edition estimated above the cap.

## 1. Database (Supabase)

1. The project `throughline-dev` is **paused** (Supabase pauses free projects after a
   week without activity). Open it in the Supabase dashboard and click **Restore**.
   A paused project keeps its data.
2. Apply the migrations in `supabase/migrations/` that are not applied yet. From the
   repo root, with the Supabase CLI linked to the project:
   `supabase db push`
   The two newest are `20261031000000_verification.sql` and
   `20261124000000_verification_private.sql`.
3. In the dashboard, copy three values (Project Settings):
   - **Project URL** and the **anon / publishable key** (API settings). These are
     safe to expose: row-level security only lets them read published editions.
   - The **connection string** for the database (Database settings, "Session pooler"
     works from GitHub Actions). This one is a secret: it bypasses row-level security.

Why it matters: the website reads through the anon key and sees only published
editions; the pipeline writes through the connection string.

## 2. Pipeline (GitHub Actions)

1. In the repository on GitHub: Settings > Secrets and variables > Actions.
   - Secrets `ANTHROPIC_API_KEY` and `GUARDIAN_API_KEY` already exist.
   - Add secret **`DATABASE_URL`** = the connection string from step 1.3.
2. Run it once by hand: Actions > **Editions** > Run workflow > slot `morning`.
   It ingests the feeds, clusters, writes and verifies the briefings, and publishes.
   The run's summary shows an "Edition" notice with the card count and cost.
   - If it says "Edition not published", a batch was still running: run the workflow
     again with the same slot (and the same date, if midnight UTC has passed).
3. When a manual run looks right, switch the schedule on: same settings page,
   **Variables** tab, add **`EDITIONS_ENABLED`** = `true`. Editions then build at
   10:30, 16:30 and 22:30 UTC. Set the variable to anything else to pause.
   - Optional variables `EDITION_FULL` and `EDITION_TAIL` change the edition size.
4. Failures email you (GitHub's default for scheduled workflows). The evening run
   also re-clusters and deletes article text older than 14 days, which the Privacy
   page promises.

## 3. Website (Vercel)

1. In Vercel: Add New > Project > import `Jason-Sh1/Throughline`.
   Set **Root Directory** to `web`. Framework preset: Next.js. No build settings to change.
2. Environment variables (Production and Preview):
   - `NEXT_PUBLIC_SUPABASE_URL` = the Project URL
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY` = the anon / publishable key
   Do **not** add `DATABASE_URL` or the service role key to Vercel. The website never
   needs them.
3. Deploy. Without the two variables the site still works, but shows the bundled
   sample edition with a "Sample edition" note.
4. Check on a phone: the Today screen shows today's edition, a story opens, tapping
   a sentence shows its sources, and "Add to Home Screen" installs it. Turn on
   airplane mode and reopen: the edition should still be readable.

The site caches an edition for 5 minutes, so a new edition can take that long to appear.

## 4. After the first deploy

- Put the live link in the README.
- Label the gold set and record the eval baseline (`docs/evals.md`), so the CI gate
  compares against something.
- Do the screen-reader and real-phone checks listed in `docs/a11y.md`.

## Turning things off

- Pause editions: set the `EDITIONS_ENABLED` variable to `false`. Nothing else runs
  on a schedule.
- Take the site down: delete or pause the Vercel project. The database is untouched.
- Supabase pauses the project by itself after a week with no requests; a running
  schedule counts as activity.

## Not part of this checklist

- Sign-in (M6). It needs Supabase Auth settings (magic link, site URL) and is not built yet.
- Sentry or other monitoring: skipped by decision. Use the `pipeline_runs` table and
  the Actions failure emails.
- A custom domain: skipped by decision (free `*.vercel.app` domain).
