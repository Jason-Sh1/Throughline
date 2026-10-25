-- Review fixes for the initial schema.

-- Keep updated_at honest without relying on every caller to set it.
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger jobs_set_updated_at before update on jobs
  for each row execute function set_updated_at();
create trigger profiles_set_updated_at before update on profiles
  for each row execute function set_updated_at();

-- Create the app-level users row when someone signs up through Supabase Auth,
-- so the client never has to (and RLS never has to allow arbitrary inserts).
-- security definer: runs as the function owner, because the auth service's
-- role can't write to public tables. search_path is pinned so a malicious
-- schema can't shadow `users`.
create or replace function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.users (id) values (new.id) on conflict (id) do nothing;
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function handle_new_user();
