-- Throughline initial schema (M1).
-- Design notes live in /docs/decisions.md and /docs/schema.md.
--
-- Access model: every table has row-level security ON. Tables with no policy
-- are reachable only by the service role (the Python worker). The browser
-- (anon / authenticated roles) can read published editions and manage its own
-- user + profile rows, nothing else. In particular `articles` is never exposed,
-- because it holds raw extracted text (CLAUDE.md rule 5).

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

-- AllSides buckets, plus "not_rated" for outlets AllSides doesn't cover
-- (all non-U.S. outlets). "mixed" is AllSides' own "Mixed" rating.
create type bias_rating as enum (
  'left', 'lean_left', 'center', 'lean_right', 'right', 'mixed', 'not_rated'
);

create type news_category as enum (
  'us', 'world', 'politics', 'business', 'tech', 'science', 'health', 'climate', 'sports', 'culture'
);

create type article_origin as enum ('rss', 'gdelt', 'guardian');

create type job_status as enum ('queued', 'running', 'succeeded', 'failed');

-- ---------------------------------------------------------------------------
-- Sources & feeds
-- ---------------------------------------------------------------------------

-- One row per outlet (publisher). Ratings come from AllSides (CC BY-NC 4.0)
-- and must be attributed wherever they're shown; allsides_url is the
-- per-outlet page we link to.
create table sources (
  id               bigint generated always as identity primary key,
  slug             text not null unique,
  name             text not null,
  homepage_url     text,
  region           text not null,                 -- ISO-3166 alpha-2, e.g. 'US', 'GB'
  allsides_rating  bias_rating not null default 'not_rated',
  allsides_url     text,
  rating_updated_at timestamptz,
  created_at       timestamptz not null default now()
);

-- One row per RSS feed. An outlet usually has several (one per section).
-- etag / last_modified support conditional GET so we don't re-download
-- unchanged feeds.
create table feeds (
  id               bigint generated always as identity primary key,
  source_id        bigint not null references sources (id) on delete cascade,
  url              text not null unique,
  category         news_category not null,
  active           boolean not null default true,
  etag             text,
  last_modified    text,
  last_fetched_at  timestamptz,
  last_status      int,
  created_at       timestamptz not null default now()
);
create index feeds_source_idx on feeds (source_id);

-- ---------------------------------------------------------------------------
-- Articles & entities
-- ---------------------------------------------------------------------------

-- 384 dims matches small local sentence-transformers models
-- (all-MiniLM-L6-v2, bge-small-en-v1.5). Change with a migration if the
-- embedding model changes.
create table articles (
  id               bigint generated always as identity primary key,
  source_id        bigint not null references sources (id),
  feed_id          bigint references feeds (id) on delete set null,
  origin           article_origin not null,
  url              text not null,
  canonical_url    text not null unique,
  title            text not null,                 -- the outlet's real headline, never rewritten
  title_hash       text not null,                 -- hash of normalized title, exact-dup check
  simhash          bigint,                        -- 64-bit SimHash for near-dup check
  summary          text,                          -- RSS description as published
  raw_text         text,                          -- internal only; purged after 14 days
  raw_text_purged_at timestamptz,
  language         text,
  published_at     timestamptz,
  fetched_at       timestamptz not null default now(),
  duplicate_of     bigint references articles (id) on delete set null,
  embedding        vector(384)
);
create index articles_source_idx on articles (source_id);
create index articles_published_idx on articles (published_at desc);
create index articles_title_hash_idx on articles (title_hash);
create index articles_fetched_idx on articles (fetched_at) where raw_text is not null;
create index articles_embedding_idx on articles using hnsw (embedding vector_cosine_ops);

create table entities (
  id               bigint generated always as identity primary key,
  name             text not null,
  normalized_name  text not null,
  kind             text not null check (kind in ('person', 'org', 'place', 'event', 'other')),
  unique (normalized_name, kind)
);

-- Needed for the entity-overlap tiebreak in clustering (M2).
create table article_entities (
  article_id       bigint not null references articles (id) on delete cascade,
  entity_id        bigint not null references entities (id) on delete cascade,
  mentions         int not null default 1,
  primary key (article_id, entity_id)
);
create index article_entities_entity_idx on article_entities (entity_id);

-- ---------------------------------------------------------------------------
-- Clusters (one cluster = one news event)
-- ---------------------------------------------------------------------------

create table clusters (
  id               bigint generated always as identity primary key,
  status           text not null default 'active' check (status in ('active', 'merged', 'archived')),
  merged_into      bigint references clusters (id),
  label            text,                          -- internal debugging label, never shown to users
  category         news_category,
  centroid         vector(384),
  article_count    int not null default 0,
  outlet_count     int not null default 0,
  burst_score      real,
  first_seen_at    timestamptz not null default now(),
  last_updated_at  timestamptz not null default now()
);
create index clusters_active_idx on clusters (last_updated_at desc) where status = 'active';

-- Primary key on article_id: an article belongs to at most one cluster at a
-- time. The nightly HDBSCAN re-cluster moves rows rather than duplicating.
create table cluster_articles (
  article_id       bigint primary key references articles (id) on delete cascade,
  cluster_id       bigint not null references clusters (id) on delete cascade,
  similarity       real,
  added_at         timestamptz not null default now()
);
create index cluster_articles_cluster_idx on cluster_articles (cluster_id);

-- ---------------------------------------------------------------------------
-- Generated cards & editions
-- ---------------------------------------------------------------------------

-- content_hash = hash(cluster article set + prompt version + model), so an
-- unchanged cluster never pays for a second generation.
create table cards (
  id               bigint generated always as identity primary key,
  cluster_id       bigint not null references clusters (id) on delete cascade,
  content_hash     text not null unique,
  prompt_version   text not null,
  model            text not null,
  status           text not null default 'draft'
                   check (status in ('draft', 'verified', 'failed', 'published')),
  card             jsonb not null,
  source_count     int not null,
  input_tokens     int,
  output_tokens    int,
  cost_usd         numeric(10, 5),
  created_at       timestamptz not null default now()
);
create index cards_cluster_idx on cards (cluster_id);

-- Finite daily editions: up to 3 per day.
create table editions (
  id               bigint generated always as identity primary key,
  edition_date     date not null,
  slot             text not null check (slot in ('morning', 'midday', 'evening')),
  status           text not null default 'building' check (status in ('building', 'published', 'failed')),
  published_at     timestamptz,
  created_at       timestamptz not null default now(),
  unique (edition_date, slot)
);

create table edition_cards (
  edition_id       bigint not null references editions (id) on delete cascade,
  card_id          bigint not null references cards (id) on delete cascade,
  rank             int not null,
  tier             text not null check (tier in ('full', 'one_liner')),
  primary key (edition_id, card_id)
);

-- ---------------------------------------------------------------------------
-- Operations
-- ---------------------------------------------------------------------------

create table pipeline_runs (
  id               bigint generated always as identity primary key,
  kind             text not null,                 -- 'ingest', 'cluster', 'edition', 'purge', ...
  status           text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  stats            jsonb not null default '{}'::jsonb,
  input_tokens     int not null default 0,
  output_tokens    int not null default 0,
  cost_usd         numeric(10, 5) not null default 0,
  error            text
);
create index pipeline_runs_started_idx on pipeline_runs (started_at desc);

-- Simple Postgres job queue. Workers claim with
--   update jobs set status='running', locked_at=now(), locked_by=$1
--   where id = (select id from jobs where status='queued' and run_after<=now()
--               order by run_after for update skip locked limit 1)
--   returning *;
create table jobs (
  id               bigint generated always as identity primary key,
  kind             text not null,
  payload          jsonb not null default '{}'::jsonb,
  status           job_status not null default 'queued',
  attempts         int not null default 0,
  max_attempts     int not null default 3,
  run_after        timestamptz not null default now(),
  locked_at        timestamptz,
  locked_by        text,
  last_error       text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index jobs_queue_idx on jobs (run_after) where status = 'queued';

create table evals (
  id               bigint generated always as identity primary key,
  run_at           timestamptz not null default now(),
  suite            text not null check (suite in ('quick', 'full')),
  git_sha          text,
  prompt_version   text,
  model            text,
  story_count      int not null,
  metrics          jsonb not null
);

-- ---------------------------------------------------------------------------
-- Users & profiles
-- ---------------------------------------------------------------------------

-- App-level user settings, 1:1 with Supabase's auth.users.
create table users (
  id               uuid primary key references auth.users (id) on delete cascade,
  digest_enabled   boolean not null default true,  -- daily digest is the only default-on notification
  breaking_alerts  boolean not null default false,
  created_at       timestamptz not null default now()
);

-- Coarse profile only: topics, an optional region and optional "about you"
-- chips. Deliberately no field for political leaning (CLAUDE.md rule 6).
-- `segment` is a derived key (e.g. 'tech+climate|US-MD|renter') used to cache
-- personalized impact cards per (story, segment) instead of per user.
create table profiles (
  user_id          uuid primary key references users (id) on delete cascade,
  topics           news_category[] not null default '{}',
  region           text,
  about_chips      text[] not null default '{}',
  segment          text,
  updated_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

alter table sources          enable row level security;
alter table feeds            enable row level security;
alter table articles         enable row level security;
alter table entities         enable row level security;
alter table article_entities enable row level security;
alter table clusters         enable row level security;
alter table cluster_articles enable row level security;
alter table cards            enable row level security;
alter table editions         enable row level security;
alter table edition_cards    enable row level security;
alter table pipeline_runs    enable row level security;
alter table jobs             enable row level security;
alter table evals            enable row level security;
alter table users            enable row level security;
alter table profiles         enable row level security;

-- Public, read-only: outlet names/ratings, published editions and their cards,
-- and eval results (for the public metrics page).
create policy "sources are public" on sources for select using (true);
create policy "published editions are public" on editions
  for select using (status = 'published');
create policy "cards in published editions are public" on edition_cards
  for select using (exists (
    select 1 from editions e where e.id = edition_id and e.status = 'published'));
create policy "published cards are public" on cards
  for select using (status = 'published');
create policy "evals are public" on evals for select using (true);

-- Owner-only: users and profiles. Delete is allowed so "delete my data" works
-- without a server round trip.
create policy "own user row" on users
  for all using (auth.uid() = id) with check (auth.uid() = id);
create policy "own profile" on profiles
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
