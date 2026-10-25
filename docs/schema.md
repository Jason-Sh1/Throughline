# Database schema, in plain English

Migration: `supabase/migrations/20261025000000_initial_schema.sql`.

Think of the data as a pipeline that flows top to bottom:

**Outlets and feeds.** `sources` has one row per news outlet (NPR, Fox News, BBC):
its name, country, and AllSides bias rating plus a link to its AllSides page for
attribution. `feeds` has one row per RSS feed, since an outlet usually has several
(politics, tech, ...). Feeds also remember the `etag`/`last_modified` headers from
the last fetch, so the next fetch can ask "anything new?" and get a cheap
"no" instead of re-downloading. Both tables are generated from `feeds/feeds.yaml`
by `make sync-sources`; the YAML file is the source of truth.

**Articles.** `articles` has one row per story an outlet published. It keeps the
outlet's real headline (never rewritten), the link, a canonical URL (unique, so
the same story fetched twice is one row), two fingerprints for duplicate
detection (`title_hash` for exact matches, `simhash` for near-copies like wire
stories republished by many outlets), and an `embedding`: a list of 384 numbers
that represents what the article is about, used to find similar articles.
`raw_text` is the extracted body text; it's internal only and wiped after 14 days.
`entities` (people, organizations, places) and `article_entities` record who and
what each article mentions; clustering uses them as a tiebreaker.

**Clusters.** A `cluster` is one news event, for example "Fed raises rates".
`cluster_articles` says which cluster each article belongs to. An article is in
at most one cluster at a time; the nightly re-cluster can move it or merge two
clusters (the old one is marked `merged` and points at the survivor).

**Cards and editions.** A `card` is the AI-generated briefing for one cluster,
stored as JSON in the story card schema from CLAUDE.md. `content_hash` is a
fingerprint of the inputs (which articles, which prompt version, which model),
so if nothing changed we reuse the card instead of paying to regenerate it.
Token counts and cost are recorded per card. An `edition` is one published
issue (up to 3 a day: morning, midday, evening), and `edition_cards` lists which
cards are in it, in what order, and whether each is a full card or a one-liner.

**Operations.** `pipeline_runs` logs each pipeline run (when, how long, what
it did, tokens and cost, errors) for the admin dashboard. `jobs` is a simple
work queue stored in Postgres: workers grab the next queued job with
`FOR UPDATE SKIP LOCKED`, which lets several workers run without taking the
same job. `evals` stores the metrics from each eval run.

**Users.** Supabase manages sign-in in its own `auth.users` table. Our `users`
table adds app settings (daily digest on by default, breaking alerts off).
`profiles` stores only coarse preferences: topics, an optional region, and
optional "about you" chips like "renter". There is intentionally no field for
political leaning. `segment` is a short key built from those preferences, so
a personalized impact card is generated once per (story, segment) and shared by
everyone in that segment instead of once per person.

**Who can see what (row-level security).** Every table has RLS turned on.
With RLS on and no policy, only the worker (which uses the service-role key)
can touch a table. On top of that:
- Anyone can read outlets, published editions, published cards, and eval results.
- A signed-in user can read and change only their own `users` and `profiles` rows.
- Nobody outside the worker can read `articles`, which is how raw article text
  stays internal.

`supabase/tests/rls_test.sql` checks these rules (`make db-test`).
