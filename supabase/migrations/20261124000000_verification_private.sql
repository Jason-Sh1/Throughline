-- M4 review fix: keep the verification report out of public reach.
--
-- The report lists every sentence the verification pass removed, with the reason.
-- It was stored in cards.verification, but the row-level policy on `cards` exposes
-- whole rows once a card is published, so anyone with the anon key could read the
-- exact text verification exists to keep from readers.
--
-- The report now lives in its own table. Row-level security is on and there is no
-- policy, so only the worker (service role / direct connection) can read it.

create table card_verifications (
  card_id     bigint primary key references cards (id) on delete cascade,
  report      jsonb not null,
  created_at  timestamptz not null default now()
);

alter table card_verifications enable row level security;

insert into card_verifications (card_id, report)
  select id, verification from cards where verification is not null;

alter table cards drop column verification;
