# Golf Majors Sweepstake

Streamlit app for running a golf sweepstake: entrants pick 5 players, the best 4
scores count each round, and the leaderboard updates from live tournament data.

## Code layout

| File | What's in it |
|---|---|
| `app.py` | Page setup, login, and the public and admin pages |
| `scoring.py` | All the sweepstake rules: best 4 of 5, the cut, tie-breaks, ranks, prize splits. No Streamlit, database or network code. |
| `leaderboard.py` | Builds and draws the sweepstake and PGA leaderboards |
| `golf_api.py` | RapidAPI calls, API key rotation, smart polling schedule, DB fallback |
| `db.py` | Supabase reads and writes (tournament settings, entries, logs) |
| `emails.py` | Confirmation, edit-link and field-change emails |
| `auth.py` | Signing and checking the secure edit links |

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

`tests/test_scoring.py` covers each rule; `tests/test_leaderboard.py` runs a whole
finished tournament through the leaderboard with the API and database faked.

## Database

`sql/` holds the database objects that aren't plain tables. Run a file in the
Supabase SQL editor to recreate that object.

| File | What it is |
|---|---|
| `sql/golf_tournament_standings_view.sql` | The view the app reads entries from. It must be a view over `golf_entries`, not a table copy, or new entries won't show. |
