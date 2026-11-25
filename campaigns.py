"""Mailing-list emails ("entries open", "closing soon"): templates and sending.

No Streamlit imports: used by the admin page (preview and test email) and by send_campaign.py
(GitHub Actions), so the email previewed is exactly the email sent.

A campaign "job" is a plain dict saved in golf_app_settings under EMAIL_JOB_KEY:
    {"campaign_id", "kind", "t_id", "t_name", "subject", "intro", "logo_url", "close_iso",
     "rules_md", "entry_url", "status", "requested_at", ...}
Everything except the recipient's first name and unsubscribe link is fixed when it's queued.
"""
import datetime
import html
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

import markdown as md
import pytz

APP_URL = "https://golfmajors-v2.streamlit.app/"
EMAIL_JOB_KEY = "email_job"
UK_TZ = pytz.timezone("Europe/London")
ET_TZ = pytz.timezone("America/New_York")

KINDS = {
    "entries_open": "📣 Entries open",
    "closing_soon": "⏳ Closing soon reminder",
}


def campaign_id(t_id, kind):
    """One id per tournament and kind, so re-sending only reaches people not yet emailed."""
    return f"{t_id}:{kind}"


def public_url(t_id):
    return f"{APP_URL}?view=public&tourney_id={t_id}"


def unsubscribe_url(token):
    return f"{APP_URL}?view=unsubscribe&token={token}"


def signup_url():
    return f"{APP_URL}?view=signup"


def format_deadline(close_uk):
    """'12:00 AM ET Thu 3 Dec / 5:00 AM UK Thu 3 Dec'."""
    et = close_uk.astimezone(ET_TZ)
    fmt = lambda d: f"{d.strftime('%I:%M %p').lstrip('0')} {{tz}} {d.strftime('%a')} {d.day} {d.strftime('%b')}"
    return f"{fmt(et).format(tz='ET')} / {fmt(close_uk).format(tz='UK')}"


def default_subject(kind, t_name, close_uk=None):
    if kind == "closing_soon":
        when = f" {close_uk.astimezone(ET_TZ):%A}" if close_uk else " soon"
        return f"⏳ {t_name} Sweepstakes - Entries close{when}"
    return f"{t_name} Sweepstakes - Entries Open"


def default_intro(kind, t_name):
    if kind == "closing_soon":
        return (f"Last call! Entries for the {t_name} Sweepstakes close soon. "
                "If you haven't picked your team yet, there's still time.")
    return (f"The {t_name} Sweepstakes is open. Use the web app to submit your team, "
            "edit your picks, and track the leaderboard.")


def build_job(kind, t_id, t_name, close_uk, rules_md, logo_url="", entry_url="", subject="", intro=""):
    return {
        "campaign_id": campaign_id(t_id, kind),
        "kind": kind,
        "t_id": str(t_id),
        "t_name": t_name,
        "subject": subject.strip() or default_subject(kind, t_name, close_uk),
        "intro": intro.strip() or default_intro(kind, t_name),
        "logo_url": logo_url or "",
        "close_iso": close_uk.isoformat() if close_uk else "",
        "rules_md": rules_md or "",
        "entry_url": entry_url or public_url(t_id),
    }


def render_html(job, first_name="", unsub_url="#"):
    """The email body for one recipient."""
    esc = html.escape
    close_uk = datetime.datetime.fromisoformat(job["close_iso"]) if job.get("close_iso") else None
    greeting = f"Hello {esc(first_name.strip())}," if first_name and first_name.strip() else "Hello,"
    intro = "".join(f"<p>{esc(p.strip())}</p>" for p in job["intro"].split("\n\n") if p.strip())
    rules = md.markdown(job.get("rules_md") or "", extensions=["sane_lists"])
    logo = (f"<div style='text-align:center;margin-bottom:20px;'><img src='{esc(job['logo_url'])}' "
            f"style='max-height:110px;max-width:100%;' alt=''></div>") if job.get("logo_url") else ""
    deadline = (f"<p style='font-size:16px;'><b>⏳ Deadline:</b> {esc(format_deadline(close_uk))}</p>"
                if close_uk else "")
    button = (f"<div style='text-align:center;margin:28px 0;'><a href='{esc(job['entry_url'])}' "
              "style='background-color:#27ae60;color:#ffffff;padding:14px 28px;text-decoration:none;"
              "border-radius:6px;display:inline-block;font-weight:bold;font-size:16px;'>"
              "🏌️ Enter your team</a></div>")
    using_app = """
        <p style='margin-bottom:4px;'><b>✨ Using the app</b></p>
        <ul style='margin-top:4px;'>
          <li><b>Entering:</b> submit your picks and payment method online. You'll get a confirmation email.</li>
          <li><b>Editing picks:</b> use the "Check Entry" tab for a secure edit link.</li>
          <li><b>Withdrawals:</b> you'll be emailed if a pick withdraws before the tournament so you can swap them out.</li>
          <li><b>Leaderboard:</b> standings update at the end of each day for Rounds 1-3. Round 4 has live scoring.</li>
        </ul>"""
    return f"""<html><body style="margin:0;padding:0;background:#ffffff;">
<div style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#2c3e50;line-height:1.6;max-width:600px;margin:0 auto;padding:20px;font-size:15px;">
  {logo}
  <h1 style="font-size:26px;margin:0 0 16px 0;">{esc(job['t_name'])}</h1>
  <p>{greeting}</p>
  {intro}
  {button}
  {deadline}
  <div>{rules}</div>
  {using_app}
  {button}
  <p>Reply to this email if you run into any issues or have feedback.</p>
  <p>Good luck,<br>Eddie</p>
  <hr style="border:none;border-top:1px solid #ddd;margin:30px 0 12px 0;">
  <p style="font-size:12px;color:#7f8c8d;">
    You're getting this because you signed up for golf sweepstakes emails.<br>
    <a href="{esc(unsub_url)}" style="color:#7f8c8d;">Unsubscribe</a> ·
    Know someone who'd like to join? Send them <a href="{esc(signup_url())}" style="color:#7f8c8d;">this signup link</a>.
  </p>
</div></body></html>"""


def build_message(job, sender, to, first_name="", unsub_url="#"):
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((f"{job['t_name']} Sweepstakes", sender))
    msg["To"] = to
    msg["Subject"] = job["subject"]
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    # Lets Gmail, Outlook etc. show their own "Unsubscribe" button next to the sender.
    msg["List-Unsubscribe"] = f"<{unsub_url}>, <mailto:{sender}?subject=unsubscribe>"
    msg.attach(MIMEText(render_html(job, first_name, unsub_url), "html"))
    return msg


class GmailSender:
    """Reuses one SMTP connection for a batch, reconnecting if Gmail drops it."""

    def __init__(self, sender, pwd):
        self.sender, self.pwd, self.server = sender, pwd, None

    def _connect(self):
        self.server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        self.server.starttls()
        self.server.login(self.sender, self.pwd)

    def send(self, msg):
        for attempt in range(2):
            try:
                if self.server is None:
                    self._connect()
                self.server.send_message(msg)
                return
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPSenderRefused, OSError):
                self.close()
                if attempt:
                    raise
                time.sleep(5)

    def close(self):
        if self.server is not None:
            try:
                self.server.quit()
            except Exception:
                pass
            self.server = None

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
