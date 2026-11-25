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
| `campaigns.py` | Mailing-list email templates ("entries open", "closing soon") and the Gmail sender |
| `admin_mailing.py` | The admin 📣 Mailing List tab: subscribers, preview, test send, queue a send |
| `send_campaign.py` | Sends a queued mailing-list email (GitHub Actions) |
| `field_watch.py` | Scheduled field check (GitHub Actions): emails the admin when the pinned tournament's field changes before entries close |

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
| `sql/golf_subscribers.sql` | Mailing list (`golf_subscribers`) and the per-email send log (`golf_email_sends`) |
| `sql/golf_app_settings.sql` | App-wide settings table. Holds the pinned active tournament the admin page opens on. |
| `sql/golf_tournament_standings_view.sql` | The view the app reads entries from. It must be a view over `golf_entries`, not a table copy, or new entries won't show. |

## Field watch

`.github/workflows/field-watch.yml` runs `field_watch.py` every 2 hours. Within 10 days of the
entry close it compares the pinned tournament's field with the Field tab's alert baseline and
emails the admin once per change. It never emails entrants; that's still the **🚀 Send Alert
Emails** button. Run it by hand from the Actions tab (**Field watch → Run workflow**). Tick
**Send me a test email** to check the API keys and email settings at any time: it ignores the
watch window and emails a status report. **WATCH_DAYS for this run only** overrides the window
for that one run.

It needs these repository secrets (Settings → Secrets and variables → Actions):
`SUPABASE_URL`, `SUPABASE_KEY`, `RAPIDAPI_KEYS` (comma-separated), `EMAIL_SENDER`,
`EMAIL_PASSWORD`, and optionally `ALERT_EMAIL` (defaults to `EMAIL_SENDER`).

## Mailing list

Replaces Mailchimp. Subscribers live in `golf_subscribers`; people join from the signup page
(`?view=signup`) or by leaving "Email me when entries open" ticked on the entry form, and leave
from the unsubscribe link in every email (`?view=unsubscribe&token=…`).

Send from the admin **📣 Mailing List** tab: pick "Entries open" or "Closing soon reminder", edit the
subject and opening message, preview, **Send a test to me**, then **Send to N subscribers**. That
queues the email and starts `.github/workflows/send-emails.yml`, which sends one email per person
(about 2 seconds apart), logs each one in `golf_email_sends` so nobody is emailed twice for the same
tournament and kind, and emails the admin a summary. "Closing soon" skips anyone who has entered.

To let the admin page start the workflow, add a `github_token` Streamlit secret: a fine-grained
GitHub token for this repository with **Actions: Read and write**. Without it, the email is queued
and you press **Actions → Send emails → Run workflow** yourself.
