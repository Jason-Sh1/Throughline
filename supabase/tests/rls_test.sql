-- RLS smoke test. Run after local_auth_shim.sql + migrations (see `make db-test`).
-- Mimics Supabase's default grants, then checks what anon/authenticated can see.
\set ON_ERROR_STOP 1
begin;  -- everything below is rolled back so the test leaves no data behind
grant usage on schema public to anon, authenticated;
grant select, insert, update, delete on all tables in schema public to anon, authenticated;

insert into sources (slug, name, region) values ('test-outlet', 'Test Outlet', 'US');
insert into articles (source_id, origin, url, canonical_url, title, title_hash, raw_text)
  select id, 'rss', 'https://x.test/a', 'https://x.test/a', 'Headline', 'h', 'secret full text' from sources;
insert into clusters default values;
insert into cards (cluster_id, content_hash, prompt_version, model, status, card, source_count)
  select id, 'draft-hash', 'v1', 'm', 'draft', '{}', 1 from clusters;
insert into cards (cluster_id, content_hash, prompt_version, model, status, card, source_count)
  select id, 'pub-hash', 'v1', 'm', 'published', '{}', 1 from clusters;
insert into auth.users values ('00000000-0000-0000-0000-00000000000a'), ('00000000-0000-0000-0000-00000000000b');
insert into users (id) values ('00000000-0000-0000-0000-00000000000a'), ('00000000-0000-0000-0000-00000000000b');
insert into profiles (user_id, topics) values ('00000000-0000-0000-0000-00000000000a', '{tech}'), ('00000000-0000-0000-0000-00000000000b', '{science}');

do $$
declare n int;
begin
  set local role anon;
  select count(*) into n from articles;  assert n = 0, 'anon must not read articles';
  select count(*) into n from sources;   assert n = 1, 'anon can read sources';
  select count(*) into n from cards;     assert n = 1, 'anon sees only published cards';
  select count(*) into n from profiles;  assert n = 0, 'anon must not read profiles';
  select count(*) into n from jobs;      assert n = 0, 'anon must not read jobs';
  reset role;

  perform set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-00000000000a', true);
  set local role authenticated;
  select count(*) into n from profiles;  assert n = 1, 'user sees only own profile';
  select count(*) into n from users;     assert n = 1, 'user sees only own user row';
  update profiles set topics = '{climate}' where user_id = '00000000-0000-0000-0000-00000000000b';
  get diagnostics n = row_count;         assert n = 0, 'user cannot edit another profile';
  reset role;
end $$;

rollback;
\echo 'RLS tests passed'
