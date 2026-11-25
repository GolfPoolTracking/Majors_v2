"""Emails sent in bulk from the admin 📣 Mailing List tab: templates and sending.

No Streamlit imports: used by the admin page (preview and test email) and by send_campaign.py
(GitHub Actions), so the email previewed is exactly the email sent.

A campaign "job" is a plain dict saved in golf_app_settings under EMAIL_JOB_KEY. Everything except
each recipient's name, unsubscribe link and personal "data" is fixed when it's queued.

Two kinds of audience:
- Mailing-list kinds (entries_open, closing_soon) go to golf_subscribers.
- Entrant kinds (payment_reminder, round_recap, results) carry their own "recipients" list, worked
  out by the admin page from the entries and the leaderboard, each with personal "data".
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
    "closing_soon": "⏳ Closing soon",
    "payment_reminder": "💸 Payment reminder",
    "round_recap": "📊 Round recap",
    "results": "🏆 Final results",
}
MAILING_LIST_KINDS = {"entries_open", "closing_soon"}
# Entrant kinds that still skip people who unsubscribed from the mailing list. Payment reminders
# and results are about money someone owes or is owed, so they always go.
RESPECTS_UNSUBSCRIBE = MAILING_LIST_KINDS | {"round_recap"}

esc = html.escape


def campaign_id(t_id, kind, suffix=""):
    """One id per tournament and kind (and round, for recaps), so re-sending only reaches people
    not yet emailed."""
    return f"{t_id}:{kind}" + (f":{suffix}" if suffix else "")


def public_url(t_id):
    return f"{APP_URL}?view=public&tourney_id={t_id}"


def unsubscribe_url(token):
    return f"{APP_URL}?view=unsubscribe&token={token}" if token else ""


def signup_url():
    return f"{APP_URL}?view=signup"


def format_deadline(close_uk):
    """'12:00 AM ET Thu 3 Dec / 5:00 AM UK Thu 3 Dec'."""
    et = close_uk.astimezone(ET_TZ)
    fmt = lambda d, tz: f"{d.strftime('%I:%M %p').lstrip('0')} {tz} {d.strftime('%a')} {d.day} {d.strftime('%b')}"
    return f"{fmt(et, 'ET')} / {fmt(close_uk, 'UK')}"


def fmt_score(s):
    try:
        s = int(s)
    except (TypeError, ValueError):
        return str(s)
    return "E" if s == 0 else f"+{s}" if s > 0 else str(s)


def fmt_money(x):
    return f"${x:,.2f}".replace(".00", "")


def ordinal_rank(rank):
    """'T3' -> 'tied 3rd', '1' -> '1st'."""
    r = str(rank)
    tied, n = r.startswith("T"), r.lstrip("T")
    try:
        k = int(n)
        suffix = "th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")
        n = f"{k}{suffix}"
    except ValueError:
        pass
    return f"tied {n}" if tied else n


def default_subject(kind, t_name, close_uk=None, round_no=None):
    return {
        "entries_open": f"{t_name} Sweepstakes - Entries Open",
        "closing_soon": f"⏳ {t_name} Sweepstakes - Entries close {close_uk.astimezone(ET_TZ):%A}" if close_uk
                        else f"⏳ {t_name} Sweepstakes - Entries close soon",
        "payment_reminder": f"{t_name} Sweepstakes - Entry fee reminder",
        "round_recap": f"📊 {t_name} Sweepstakes - After Round {round_no}",
        "results": f"🏆 {t_name} Sweepstakes - Final Results",
    }[kind]


def default_intro(kind, t_name, round_no=None):
    return {
        "entries_open": f"The {t_name} Sweepstakes is open. Use the web app to submit your team, "
                        "edit your picks, and track the leaderboard.",
        "closing_soon": f"Last call! Entries for the {t_name} Sweepstakes close soon. "
                        "If you haven't picked your team yet, there's still time.",
        "payment_reminder": "Thanks for entering! Our records show the entry fee below hasn't come in yet. "
                            "If you've already paid, thank you, and please ignore this email.",
        "round_recap": f"Round {round_no} is in the books. Here's where things stand.",
        "results": f"The {t_name} is over. Thanks for playing! Here's how it finished.",
    }[kind]


def build_job(kind, t_id, t_name, close_uk, rules_md, logo_url="", entry_url="", subject="", intro="",
              recipients=None, extra=None, round_no=None):
    job = {
        "campaign_id": campaign_id(t_id, kind, f"R{round_no}" if kind == "round_recap" and round_no else ""),
        "kind": kind,
        "t_id": str(t_id),
        "t_name": t_name,
        "subject": (subject or "").strip() or default_subject(kind, t_name, close_uk, round_no),
        "intro": (intro or "").strip() or default_intro(kind, t_name, round_no),
        "logo_url": logo_url or "",
        "close_iso": close_uk.isoformat() if close_uk else "",
        "rules_md": rules_md or "",
        "entry_url": entry_url or public_url(t_id),
        "round_no": round_no,
    }
    if recipients is not None:
        job["recipients"] = recipients
    if extra:
        job.update(extra)
    return job


# --- Pieces shared by every email ---

def _button(url, label, color="#27ae60"):
    return (f"<div style='text-align:center;margin:26px 0;'><a href='{esc(url)}' "
            f"style='background-color:{color};color:#ffffff;padding:14px 28px;text-decoration:none;"
            f"border-radius:6px;display:inline-block;font-weight:bold;font-size:16px;'>{label}</a></div>")


def _table(rows, highlight=()):
    """rows: [(rank, name, score)]. Names in `highlight` are bolded with a gold background."""
    trs = []
    for rank, name, score in rows:
        hl = name in highlight
        style = "background:#fff8dc;font-weight:bold;" if hl else ""
        trs.append(f"<tr style='{style}'><td style='padding:6px 8px;border-bottom:1px solid #eee;width:48px;'>{esc(str(rank))}</td>"
                   f"<td style='padding:6px 8px;border-bottom:1px solid #eee;'>{'⭐ ' if hl else ''}{esc(name)}</td>"
                   f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;'>{esc(score)}</td></tr>")
    return f"<table style='width:100%;border-collapse:collapse;font-size:15px;margin:8px 0 18px 0;'>{''.join(trs)}</table>"


def _box(inner, color="#2ecc71", bg="#f8f9fa"):
    return (f"<div style='background-color:{bg};border-left:4px solid {color};padding:14px 18px;"
            f"border-radius:4px;margin:18px 0;'>{inner}</div>")


def _team_score(t):
    return "❌ Out (2 missed the cut)" if t.get("elim") else fmt_score(t.get("score"))


def _shell(job, first_name, body, unsub_url, footer_reason):
    greeting = f"Hello {esc(first_name.strip())}," if first_name and first_name.strip() else "Hello,"
    intro = "".join(f"<p>{esc(p.strip())}</p>" for p in job["intro"].split("\n\n") if p.strip())
    logo = (f"<div style='text-align:center;margin-bottom:20px;'><img src='{esc(job['logo_url'])}' "
            f"style='max-height:110px;max-width:100%;' alt=''></div>") if job.get("logo_url") else ""
    unsub = (f"<a href=\"{esc(unsub_url)}\" style=\"color:#7f8c8d;\">Unsubscribe</a> · " if unsub_url else "")
    return f"""<html><body style="margin:0;padding:0;background:#ffffff;">
<div style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#2c3e50;line-height:1.6;max-width:600px;margin:0 auto;padding:20px;font-size:15px;">
  {logo}
  <h1 style="font-size:26px;margin:0 0 16px 0;">{esc(job['t_name'])}</h1>
  <p>{greeting}</p>
  {intro}
  {body}
  <p>Reply to this email if you have any questions.</p>
  <p>Thanks,<br>Eddie</p>
  <hr style="border:none;border-top:1px solid #ddd;margin:30px 0 12px 0;">
  <p style="font-size:12px;color:#7f8c8d;">
    {esc(footer_reason)}<br>
    {unsub}Know someone who'd like to join? Send them <a href="{esc(signup_url())}" style="color:#7f8c8d;">this signup link</a>.
  </p>
</div></body></html>"""


# --- One body per kind ---

def _body_invite(job, data):
    close_uk = datetime.datetime.fromisoformat(job["close_iso"]) if job.get("close_iso") else None
    deadline = (f"<p style='font-size:16px;'><b>⏳ Deadline:</b> {esc(format_deadline(close_uk))}</p>" if close_uk else "")
    rules = md.markdown(job.get("rules_md") or "", extensions=["sane_lists"])
    button = _button(job["entry_url"], "🏌️ Enter your team")
    using_app = """
        <p style='margin-bottom:4px;'><b>✨ Using the app</b></p>
        <ul style='margin-top:4px;'>
          <li><b>Entering:</b> submit your picks and payment method online. You'll get a confirmation email.</li>
          <li><b>Editing picks:</b> use the "Check Entry" tab for a secure edit link.</li>
          <li><b>Withdrawals:</b> you'll be emailed if a pick withdraws before the tournament so you can swap them out.</li>
          <li><b>Leaderboard:</b> standings update at the end of each day for Rounds 1-3. Round 4 has live scoring.</li>
        </ul>"""
    return f"{button}{deadline}<div>{rules}</div>{using_app}{button}<p>Good luck!</p>"


def _body_payment(job, data):
    teams = data.get("teams", [])
    rows = "".join(f"<li><b>{esc(t['name'])}</b>: {esc(t.get('method') or '')}</li>" for t in teams)
    amount = data.get("amount")
    owed = f"<p style='font-size:17px;margin:10px 0 0 0;'><b>Amount due: {fmt_money(amount)}</b></p>" if amount else ""
    return _box(f"<p style='margin:0 0 6px 0;'><b>💸 Unpaid {'entry' if len(teams) == 1 else 'entries'}</b> "
                f"(team: payment method you chose)</p><ul style='margin:0;'>{rows}</ul>{owed}", "#e67e22", "#fff3e0") + \
        "<p>Please send it using the payment method above. Prizes are paid from the entry fees, so thanks for sorting it.</p>" + \
        _button(data.get("follow_url") or job["entry_url"], "⭐ View your team", "#34495e")


def _body_recap(job, data):
    teams = data.get("teams", [])
    n = job.get("n_teams", 0)
    mine = "".join(
        f"<p style='font-size:17px;margin:4px 0;'><b>{esc(t['name'])}</b>: "
        + ("❌ out, 2 picks missed the cut" if t.get("elim") else
           f"{esc(ordinal_rank(t['rank']))} of {n} at <b>{esc(fmt_score(t['score']))}</b>") + "</p>" for t in teams)
    top = job.get("top", [])
    names = {t["name"] for t in teams}
    return (_box(f"<p style='margin:0 0 4px 0;'><b>⭐ Your {'team' if len(teams) == 1 else 'teams'}</b></p>{mine}") +
            f"<p style='margin-bottom:0;'><b>🏆 Top of the leaderboard</b></p>" +
            _table([(r["rank"], r["name"], fmt_score(r["score"])) for r in top], names) +
            _button(data.get("follow_url") or job["entry_url"], "⭐ Follow your team live", "#34495e"))


def _body_results(job, data):
    teams = data.get("teams", [])
    n = job.get("n_teams", 0)
    won = [t for t in teams if t.get("payout")]
    mine = "".join(
        f"<p style='font-size:17px;margin:4px 0;'><b>{esc(t['name'])}</b>: "
        + ("❌ out, 2 picks missed the cut" if t.get("elim") else
           f"finished {esc(ordinal_rank(t['rank']))} of {n} at <b>{esc(fmt_score(t['score']))}</b>")
        + (f" 🎉 <b>You won {fmt_money(t['payout'])}!</b>" if t.get("payout") else "") + "</p>" for t in teams)
    podium = "".join(
        f"<p style='margin:4px 0;font-size:16px;'>{'🥇' if p['rank'] == 1 else '🥈' if p['rank'] == 2 else '🥉'} "
        f"<b>{esc(p['name'])}</b> ({esc(fmt_score(p['score']))})"
        + (f": {fmt_money(p['payout'])}" if job.get("payouts_confirmed") and p.get("payout") else "") + "</p>"
        for p in job.get("podium", []))
    names = {t["name"] for t in teams}
    win = f"<p>Winning score on tour: <b>{esc(fmt_score(job['winning_score']))}</b> (the tie-breaker target).</p>" \
        if job.get("winning_score") is not None else ""
    paid_note = "<p>I'll be in touch about your winnings.</p>" if won else ""
    return (_box(f"<p style='margin:0 0 4px 0;'><b>⭐ Your {'team' if len(teams) == 1 else 'teams'}</b></p>{mine}") +
            paid_note +
            f"<p style='margin-bottom:0;'><b>🏆 The podium</b></p>{podium}{win}" +
            "<p style='margin-bottom:0;'><b>Final top 10</b></p>" +
            _table([(r["rank"], r["name"], fmt_score(r["score"])) for r in job.get("top", [])], names) +
            _button(data.get("follow_url") or job["entry_url"], "📋 See the full leaderboard", "#34495e") +
            "<p>See you at the next major!</p>")


BODIES = {"entries_open": _body_invite, "closing_soon": _body_invite, "payment_reminder": _body_payment,
          "round_recap": _body_recap, "results": _body_results}
FOOTER_REASON = {
    "entries_open": "You're getting this because you signed up for golf sweepstakes emails.",
    "closing_soon": "You're getting this because you signed up for golf sweepstakes emails.",
    "payment_reminder": "You're getting this because you entered this sweepstakes.",
    "round_recap": "You're getting this because you entered this sweepstakes.",
    "results": "You're getting this because you entered this sweepstakes.",
}


def render_html(job, first_name="", unsub_url="", data=None):
    """The email body for one recipient."""
    body = BODIES[job["kind"]](job, data or {})
    return _shell(job, first_name, body, unsub_url, FOOTER_REASON[job["kind"]])


def sample_data(job):
    """Personal data for previews and test emails: the first recipient's, if there are any."""
    recips = job.get("recipients") or []
    return recips[0].get("data", {}) if recips else {}


def build_message(job, sender, to, first_name="", unsub_url="", data=None):
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((f"{job['t_name']} Sweepstakes", sender))
    msg["To"] = to
    msg["Subject"] = job["subject"]
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    if unsub_url:
        # Lets Gmail, Outlook etc. show their own "Unsubscribe" button next to the sender.
        msg["List-Unsubscribe"] = f"<{unsub_url}>, <mailto:{sender}?subject=unsubscribe>"
    msg.attach(MIMEText(render_html(job, first_name, unsub_url, data), "html"))
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
