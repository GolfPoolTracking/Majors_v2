"""Scheduled field check: emails the admin when the active tournament's field changes.

Runs outside Streamlit (GitHub Actions, see .github/workflows/field-watch.yml). It does what the
admin Field tab's "Smart Field Alerts" does, without anyone having the page open:

1. Reads the pinned active tournament (golf_app_settings) and its close time and alert baseline
   (golf_tournament_configs.alerted_field).
2. Stops if entries have closed, the close is more than WATCH_DAYS away, or there's no baseline.
3. Fetches the entry list and compares it with the baseline.
4. If it differs, emails the admin once per distinct change. Entrants are NOT emailed: the admin
   reviews it and presses "Send Alert Emails" in the Field tab, which also moves the baseline.

Environment variables:
    SUPABASE_URL, SUPABASE_KEY      same values as the app's [supabase] secrets
    RAPIDAPI_KEYS                   one or more RapidAPI keys, comma-separated
    EMAIL_SENDER, EMAIL_PASSWORD    the Gmail account the app sends from
    ALERT_EMAIL                     optional; where to send the alert (default: EMAIL_SENDER)
    TOURNAMENT_ID                   optional; check this tournament instead of the pinned one
    WATCH_DAYS                      optional; only check within this many days of close (default 10)
    TEST_EMAIL                      optional; "true" ignores the watch window, checks the API and always
                                    emails the admin a test report (without marking a change as alerted)
"""
import datetime
import html
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import pytz
import requests

UK_TZ = pytz.timezone("Europe/London")
ET_TZ = pytz.timezone("America/New_York")
APP_URL = "https://golfmajors-v2.streamlit.app/"
API_HOST = "golf-leaderboard-data.p.rapidapi.com"
TIME_FMT = "%Y-%m-%d %H:%M:%S"


# --- Pure logic (tested in tests/test_field_watch.py) ---

def parse_field(field_str):
    return [p.strip() for p in (field_str or "").split(",") if p.strip()]


def diff_field(baseline, current):
    """Players added to and removed from the field, each sorted."""
    return sorted(set(current) - set(baseline)), sorted(set(baseline) - set(current))


def resolve_close_time(close_str, start_str):
    """Close time as an aware UK datetime, using the app's rule: the saved close_time if set,
    otherwise 05:00 UK on the start date. None if neither is known."""
    for value, default_hour in ((close_str, None), (start_str, 5)):
        if not value:
            continue
        try:
            dt = datetime.datetime.strptime(str(value)[:19], TIME_FMT)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
            except ValueError:
                continue
        if default_hour is not None:
            dt = dt.replace(hour=default_hour, minute=0, second=0)
        return UK_TZ.localize(dt)
    return None


def should_check(now_uk, close_uk, watch_days):
    """(ok, reason). Only check while entries are open and the close is near."""
    if close_uk is None:
        return False, "no close time known"
    if now_uk >= close_uk:
        return False, f"entries closed at {close_uk:%Y-%m-%d %H:%M} UK"
    if close_uk - now_uk > datetime.timedelta(days=watch_days):
        return False, f"close ({close_uk:%Y-%m-%d %H:%M} UK) is more than {watch_days} days away"
    return True, ""


def change_signature(added, removed):
    """A stable id for a change, so the same change is only emailed once."""
    return "+" + ",".join(added) + "|-" + ",".join(removed)


def affected_entries(entries, removed):
    """[(name, [removed picks])] for entries that picked a removed player."""
    out = []
    removed_set = set(removed)
    for e in entries:
        picks = [str(p).split(" (Top 20")[0].strip() for p in (e.get("picks") or []) if p]
        hit = [p for p in picks if p in removed_set]
        if hit:
            out.append((e.get("participant_name") or e.get("email") or "Entrant", hit))
    return out


def format_close(close_uk):
    return f"{close_uk:%a, %b %d at %I:%M %p} UK / {close_uk.astimezone(ET_TZ):%a, %b %d at %I:%M %p} ET"


def build_email_html(t_name, added, removed, affected, close_uk, baseline_size, current_size):
    def ul(items):
        return "<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>"

    parts = [f"<h2 style='margin:0 0 10px 0;'>⛳ Field change: {html.escape(t_name)}</h2>",
             f"<p>Entries close <b>{format_close(close_uk)}</b>.</p>"]
    if removed:
        parts.append(f"<h3 style='color:#c0392b;'>❌ Removed ({len(removed)})</h3>{ul(removed)}")
    if added:
        parts.append(f"<h3 style='color:#16a085;'>➕ Added ({len(added)})</h3>{ul(added)}")
    if affected:
        rows = [f"{name}: {', '.join(picks)}" for name, picks in affected]
        parts.append(f"<h3>🚨 Entries with a removed pick ({len(affected)})</h3>{ul(rows)}")
    elif removed:
        parts.append("<p>No entries picked a removed player.</p>")
    if baseline_size and len(removed) > baseline_size / 2:
        parts.append(f"<p style='color:#d35400;'><b>⚠️ More than half the field ({len(removed)} of {baseline_size}) "
                     f"was removed, now {current_size}. This may be an API glitch; check before emailing entrants.</b></p>")
    parts.append(f"<p>Nothing has been sent to entrants. Open the admin page's <b>🏌️ Field</b> tab to review "
                 f"and press <b>🚀 Send Alert Emails</b>.</p><p><a href='{APP_URL}'>{APP_URL}</a></p>")
    return ("<html><body style=\"font-family:Helvetica,Arial,sans-serif;color:#2c3e50;line-height:1.5;"
            "max-width:600px;margin:0 auto;padding:16px;\">" + "".join(parts) + "</body></html>")


# --- I/O ---

def env(name, required=True, default=None):
    val = os.environ.get(name, "").strip()
    if not val and required:
        sys.exit(f"Missing environment variable {name}")
    return val or default


def fetch_entry_list(t_id, keys):
    last_error = "no keys"
    for key in keys:
        try:
            resp = requests.get(f"https://{API_HOST}/entry-list/{t_id}",
                                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": API_HOST}, timeout=20)
            if resp.status_code == 200:
                results = (resp.json() or {}).get("results") or {}
                return [f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                        for p in results.get("entry_list", []) or []]
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:  # try the next key
            last_error = str(e)
    raise RuntimeError(f"All RapidAPI keys failed: {last_error}")


def send_email(sender, pwd, to, subject, body_html):
    msg = MIMEMultipart()
    msg["From"] = formataddr(("Golf Field Watch", sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, pwd)
        server.send_message(msg)


def build_test_email_html(t_name, t_id, status_lines, change_html):
    items = "".join(f"<li>{html.escape(line)}</li>" for line in status_lines)
    return ("<html><body style=\"font-family:Helvetica,Arial,sans-serif;color:#2c3e50;line-height:1.5;"
            "max-width:600px;margin:0 auto;padding:16px;\">"
            f"<h2 style='margin:0 0 10px 0;'>🧪 Field watch test: {html.escape(t_name)} ({html.escape(t_id)})</h2>"
            "<p>This is a test run from the Actions tab. If you're reading this, the email settings work.</p>"
            f"<ul>{items}</ul>"
            + (f"<hr><p><b>This is the alert a scheduled run would send now:</b></p>{change_html}" if change_html else "")
            + "<p>Test runs ignore the watch window and don't mark a change as already alerted.</p>"
            "</body></html>")


def main():
    from supabase import create_client

    sb = create_client(env("SUPABASE_URL"), env("SUPABASE_KEY"))
    watch_days = int(env("WATCH_DAYS", required=False, default="10"))
    test_mode = env("TEST_EMAIL", required=False, default="").lower() in ("1", "true", "yes")
    if test_mode:
        print("🧪 Test run: ignoring the watch window and sending a test email.")

    def app_setting(key):
        res = sb.table("golf_app_settings").select("value").eq("key", key).execute()
        return res.data[0]["value"] if res.data else None

    def set_app_setting(key, value):
        sb.table("golf_app_settings").upsert({
            "key": key, "value": value,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }).execute()

    pinned = app_setting("active_tournament") or {}
    t_id = env("TOURNAMENT_ID", required=False) or pinned.get("id")
    if not t_id:
        msg = "No active tournament pinned (admin page → 📌 Pin as active tournament). Nothing to do."
        if test_mode: sys.exit(msg)  # fail the test run so it shows up red
        print(msg)
        return
    t_id = str(t_id)

    res = sb.table("golf_tournament_configs").select(
        "tournament_name, close_time, alerted_field").eq("tournament_id", t_id).execute()
    cfg = res.data[0] if res.data else {}
    t_name = cfg.get("tournament_name") or pinned.get("name") or f"Tournament {t_id}"
    start = pinned.get("start") if str(pinned.get("id")) == t_id else None

    close_uk = resolve_close_time(cfg.get("close_time"), start)
    now_uk = datetime.datetime.now(UK_TZ)
    ok, reason = should_check(now_uk, close_uk, watch_days)
    status = [f"Entries close: {format_close(close_uk)}" if close_uk else "Entries close: unknown",
              f"Watch window ({watch_days} days): " + ("open, scheduled runs are checking" if ok else f"closed, {reason}")]
    if not ok and not test_mode:
        print(f"{t_name} ({t_id}): skipping, {reason}.")
        return

    sender, pwd = env("EMAIL_SENDER"), env("EMAIL_PASSWORD")
    to = env("ALERT_EMAIL", required=False, default=sender)

    def finish_test(extra, change_html=""):
        status.extend(extra)
        send_email(sender, pwd, to, f"🧪 Field watch test: {t_name}",
                   build_test_email_html(t_name, t_id, status, change_html))
        print("\n".join(status))
        print(f"Test email sent to {to}.")

    baseline = parse_field(cfg.get("alerted_field"))
    if not baseline and not test_mode:
        print(f"{t_name} ({t_id}): no alert baseline yet. Set it in the admin Field tab first.")
        return

    current = fetch_entry_list(t_id, [k.strip() for k in env("RAPIDAPI_KEYS").split(",") if k.strip()])
    if not current:
        if test_mode: return finish_test(["RapidAPI: reached, but the field came back empty"])
        print(f"{t_name} ({t_id}): API returned an empty field; treating as a glitch and skipping.")
        return
    if not baseline:
        return finish_test([f"RapidAPI: OK, {len(current)} players in the field",
                            "Alert baseline: not set yet. Press 'Set Initial Baseline' in the admin Field tab."])

    added, removed = diff_field(baseline, current)
    if not added and not removed:
        if test_mode: return finish_test([f"RapidAPI: OK, {len(current)} players in the field",
                                          "Field: stable, matches your baseline"])
        print(f"{t_name} ({t_id}): field stable ({len(current)} players).")
        return

    sig = change_signature(added, removed)
    state_key = f"field_watch_last_alert_{t_id}"
    if not test_mode and app_setting(state_key) == sig:
        print(f"{t_name} ({t_id}): unchanged since last alert (+{len(added)}/-{len(removed)}); not re-sending.")
        return

    entries = sb.table("golf_tournament_standings").select(
        "participant_name, email, picks").eq("tournament_id", t_id).execute().data or []
    affected = affected_entries(entries, removed)
    alert_html = build_email_html(t_name, added, removed, affected, close_uk, len(baseline), len(current))

    if test_mode:
        return finish_test([f"RapidAPI: OK, {len(current)} players in the field",
                            f"Field: changed since your baseline (+{len(added)} / −{len(removed)})"], alert_html)

    subject = f"⛳ Field change: {t_name} (+{len(added)} / −{len(removed)}"
    subject += f", {len(affected)} entries affected)" if affected else ")"
    send_email(sender, pwd, to, subject, alert_html)
    set_app_setting(state_key, sig)
    sb.table("golf_app_logs").insert({
        "tournament_id": t_id, "event_type": "FIELD WATCH",
        "message": f"Emailed admin: +{added} -{removed}",
    }).execute()
    print(f"{t_name} ({t_id}): change found, admin emailed. Added {added}; removed {removed}.")


if __name__ == "__main__":
    main()
