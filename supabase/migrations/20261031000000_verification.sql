-- M4: verification pass, outlet kinds for confidence.
--
-- cards.verification: per-sentence verdicts from the verification pass
--   ({stats, dropped: [{field, text, verdict, reason}], model, prompt_version}).
--   Kept so a dropped sentence can be traced and the eval can report drop rates.
-- sources.kind: agencies (NASA, CDC, WHO) and research sites (Nature, Phys.org)
--   are shown as sources but don't count toward a story's outlet count or
--   spectrum spread. Set from feeds.yaml by `make sync-sources`.

alter table cards add column verification jsonb;
alter table cards add column verified_at timestamptz;

alter table sources add column kind text not null default 'news'
  check (kind in ('news', 'agency', 'research'));
