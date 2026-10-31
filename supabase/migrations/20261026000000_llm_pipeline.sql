-- M3: LLM extraction + synthesis.
--
-- extractions: one row per (cluster inputs, prompt, model). Like cards, keyed by
--   a content hash of the inputs, so an unchanged cluster is never re-extracted.
-- llm_batches: Anthropic Message Batches we submitted and still need to collect.
--   A batch can take minutes to hours, so the edition builder records it here and
--   a later run (or the same run, polling) ingests the results.

create table extractions (
  id               bigint generated always as identity primary key,
  cluster_id       bigint not null references clusters (id) on delete cascade,
  content_hash     text not null unique,
  prompt_version   text not null,
  model            text not null,
  data             jsonb not null,               -- validated Extraction (worker/llm/schemas.py)
  source_map       jsonb not null,               -- local source id ("S1") -> article id
  input_tokens     int,
  output_tokens    int,
  cost_usd         numeric(10, 5),
  created_at       timestamptz not null default now()
);
create index extractions_cluster_idx on extractions (cluster_id);

create table llm_batches (
  id               bigint generated always as identity primary key,
  provider         text not null default 'anthropic',
  provider_batch_id text not null unique,
  kind             text not null,                 -- 'synthesize'
  edition_id       bigint references editions (id) on delete set null,
  status           text not null default 'submitted'
                   check (status in ('submitted', 'ended', 'ingested', 'failed')),
  -- custom_id -> {cluster_id, content_hash, extraction_id, source_map}, so results
  -- can be matched back without trusting their order.
  requests         jsonb not null,
  request_count    int not null,
  submitted_at     timestamptz not null default now(),
  ended_at         timestamptz,
  ingested_at      timestamptz
);
create index llm_batches_open_idx on llm_batches (submitted_at) where status in ('submitted', 'ended');

-- Internal pipeline tables: worker only (RLS on, no policies).
alter table extractions enable row level security;
alter table llm_batches enable row level security;
