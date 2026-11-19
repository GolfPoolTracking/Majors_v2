-- golf_tournament_standings
--
-- The app reads entries from this VIEW (db.py: get_entries_df) and writes new
-- entries to the golf_entries TABLE. The view must stay a view, not a table:
-- a table copy is a frozen snapshot and new entries will never appear in the app.
--
-- To rebuild it, run this whole file in the Supabase SQL editor.
-- (If a TABLE called golf_tournament_standings exists, rename or drop it first.)

create or replace view golf_tournament_standings as
select
  e.tournament_id,
  e.id             as entry_id,
  e.name           as participant_name,
  e.email,
  e.payment_method,
  e.tie_breaker,
  e.paid,
  jsonb_build_array(e.pick_1, e.pick_2, e.pick_3, e.pick_4, e.pick_5) as picks,
  e.created_at
from golf_entries e;

grant select on golf_tournament_standings to anon, authenticated;
