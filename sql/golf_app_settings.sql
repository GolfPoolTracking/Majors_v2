-- App-wide settings that don't belong to one tournament.
-- Currently holds one row, key = 'active_tournament', value = {"id": ..., "name": ..., "start": ...}:
-- the tournament the admin page opens on, the public page falls back to, and field_watch.py checks.
create table if not exists public.golf_app_settings (
    key        text primary key,
    value      jsonb,
    updated_at timestamptz not null default now()
);

-- Same as the other golf_ tables: RLS on with no policies, so only the service key can read or write it.
alter table public.golf_app_settings enable row level security;
