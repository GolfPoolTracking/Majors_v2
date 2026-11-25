"""Sends the queued mailing-list email (GitHub Actions, see .github/workflows/send-emails.yml).

The admin page's 📣 Mailing List tab queues a job in golf_app_settings and starts this workflow.
It emails every subscribed address one at a time, records each send in golf_email_sends, and
skips anyone that campaign has already reached, so a re-run or a later re-send never doubles up.
"Closing soon" also skips anyone who has already entered the tournament. Entrant emails (payment
reminders, round recaps, results) carry their own recipient list with personal data. A job with
"test_to" goes only to that address, as a full dry run of the send. When it finishes, it emails the
admin a summary.

Environment variables: SUPABASE_URL, SUPABASE_KEY, EMAIL_SENDER, EMAIL_PASSWORD,
ALERT_EMAIL (optional; where the summary goes, default EMAIL_SENDER),
SEND_PAUSE_SECONDS (optional; pause between emails, default 1.5).
"""
import datetime
import os
import sys
import time
from email.mime.text import MIMEText

from campaigns import (EMAIL_JOB_KEY, RESPECTS_UNSUBSCRIBE, GmailSender, build_message, sample_data,
                       unsubscribe_url)

MAX_JOB_AGE = datetime.timedelta(hours=12)


def env(name, default=None):
    val = os.environ.get(name, "").strip()
    if not val and default is None:
        sys.exit(f"Missing environment variable {name}")
    return val or default


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def select_recipients(job, subscribers, already_sent, entered):
    """[{email, first_name, data, unsub_token}] still to email, in a stable order."""
    kind = job["kind"]
    subs = {str(s["email"]).strip().lower(): s for s in subscribers}

    if job.get("test_to"):
        email = job["test_to"].strip().lower()
        me = subs.get(email, {})
        return [{"email": email, "first_name": me.get("first_name") or "", "data": sample_data(job),
                 "unsub_token": me.get("unsub_token")}]

    if job.get("recipients") is not None:
        base = [{"email": str(r["email"]).strip().lower(), "first_name": r.get("first_name") or "",
                 "data": r.get("data") or {}} for r in job["recipients"]]
    else:
        base = [{"email": e, "first_name": s.get("first_name") or "", "data": {}}
                for e, s in subs.items() if not s.get("unsubscribed_at")]
        if kind == "closing_soon":
            base = [r for r in base if r["email"] not in entered]

    out, seen = [], set()
    for r in sorted(base, key=lambda r: r["email"]):
        sub = subs.get(r["email"])
        if r["email"] in already_sent or r["email"] in seen:
            continue
        if kind in RESPECTS_UNSUBSCRIBE and sub and sub.get("unsubscribed_at"):
            continue
        seen.add(r["email"])
        # Only mailing-list style emails carry an unsubscribe link; payment and results emails don't.
        r["unsub_token"] = sub.get("unsub_token") if (sub and kind in RESPECTS_UNSUBSCRIBE) else None
        out.append(r)
    return out


def main(sb=None, sender_cls=GmailSender, sleep=time.sleep):
    if sb is None:
        from supabase import create_client
        sb = create_client(env("SUPABASE_URL"), env("SUPABASE_KEY"))
    sender, pwd = env("EMAIL_SENDER"), env("EMAIL_PASSWORD")
    admin_to = env("ALERT_EMAIL", sender)
    pause = float(env("SEND_PAUSE_SECONDS", "1.5"))

    def save_job(job):
        sb.table("golf_app_settings").upsert({"key": EMAIL_JOB_KEY, "value": job,
                                              "updated_at": now_utc().isoformat()}).execute()

    res = sb.table("golf_app_settings").select("value").eq("key", EMAIL_JOB_KEY).execute()
    job = res.data[0]["value"] if res.data else None
    if not job or job.get("status") not in ("queued", "sending"):
        print("No email queued. Queue one from the admin page's 📣 Mailing List tab.")
        return

    requested = datetime.datetime.fromisoformat(job["requested_at"])
    if now_utc() - requested > MAX_JOB_AGE:
        job.update(status="expired", finished_at=now_utc().isoformat())
        save_job(job)
        print(f"The queued email is from {job['requested_at']}, over 12 hours ago. Not sending; queue it again.")
        return

    cid, kind, t_id = job["campaign_id"], job["kind"], job["t_id"]
    subscribers = sb.table("golf_subscribers").select("email, first_name, unsubscribed_at, unsub_token").execute().data or []
    sent_rows = sb.table("golf_email_sends").select("email").eq("campaign_id", cid).eq("status", "sent").execute().data or []
    already_sent = {r["email"] for r in sent_rows}
    entered = set()
    if kind == "closing_soon":
        rows = sb.table("golf_entries").select("email").eq("tournament_id", t_id).execute().data or []
        entered = {str(r.get("email") or "").strip().lower() for r in rows}

    recipients = select_recipients(job, subscribers, already_sent, entered)
    job.update(status="sending", started_at=now_utc().isoformat(), to_send=len(recipients))
    save_job(job)
    print(f"Sending '{job['subject']}' to {len(recipients)} subscribers "
          f"({len(already_sent)} already sent{f', {len(entered)} already entered' if kind == 'closing_soon' else ''}).")

    sent, failed = 0, []
    with sender_cls(sender, pwd) as mailer:
        for i, s in enumerate(recipients):
            email = s["email"].strip().lower()
            try:
                mailer.send(build_message(job, sender, email, s.get("first_name") or "",
                                          unsubscribe_url(s.get("unsub_token")), s.get("data")))
                status = "sent"
                sent += 1
            except Exception as e:
                status = f"failed: {str(e)[:200]}"
                failed.append((email, str(e)[:120]))
            sb.table("golf_email_sends").upsert({"campaign_id": cid, "email": email, "status": status,
                                                 "sent_at": now_utc().isoformat()},
                                                on_conflict="campaign_id,email").execute()
            if i < len(recipients) - 1:
                sleep(pause)

    job.update(status="done", finished_at=now_utc().isoformat(), sent=sent, failed=len(failed))
    save_job(job)
    sb.table("golf_app_logs").insert({"tournament_id": t_id, "event_type": "MAILING LIST",
                                      "message": f"{job['subject']}: sent {sent}, failed {len(failed)}"}).execute()
    print(f"Done: sent {sent}, failed {len(failed)}.")
    for email, err in failed:
        print(f"  failed: {email}: {err}")

    summary = (f"<p><b>{job['subject']}</b></p><p>Sent: {sent}<br>Failed: {len(failed)}<br>"
               f"Skipped (already sent): {len(already_sent)}"
               + (f"<br>Skipped (already entered): {len(entered & {s['email'] for s in subscribers})}" if kind == "closing_soon" else "")
               + "</p>")
    if job.get("admin_note_html"):
        summary += job["admin_note_html"]
    if failed:
        summary += "<p>Failed addresses:</p><ul>" + "".join(f"<li>{e}: {err}</li>" for e, err in failed) + "</ul>"
    msg = MIMEText(summary, "html")
    msg["From"], msg["To"] = sender, admin_to
    msg["Subject"] = f"📣 Mailing list: {sent} sent" + (f", {len(failed)} failed" if failed else "")
    with sender_cls(sender, pwd) as mailer:
        mailer.send(msg)


if __name__ == "__main__":
    main()
