"""Admin 📣 Mailing List tab: subscribers, and every bulk email (preview, test, send).

Mailing-list emails (entries open, closing soon) go to subscribers. Entrant emails (payment
reminder, round recap, final results) are worked out here from the entries and the leaderboard,
and queued with a personal recipient list. GitHub Actions (send_campaign.py) does the sending.
"""
import datetime
import html

import pandas as pd
import requests
import streamlit as st

from campaigns import (EMAIL_JOB_KEY, KINDS, MAILING_LIST_KINDS, RESPECTS_UNSUBSCRIBE, GmailSender, build_job,
                       build_message, fmt_money, render_html, sample_data, signup_url, unsubscribe_url)
from db import (add_subscriber, get_app_setting, get_campaign_sent_emails, get_config, get_entered_emails,
                get_entries_df, get_subscriber, get_subscribers_df, log_event, set_app_setting, set_subscribed)
from field_watch import format_close, resolve_close_time
from scoring import split_prizes

DEFAULT_REPO = "GolfPoolTracking/Majors_v2"
WORKFLOW_FILE = "send-emails.yml"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _job_active(job):
    if not job or job.get("status") not in ("queued", "sending"):
        return False
    try:
        return _now() - datetime.datetime.fromisoformat(job["requested_at"]) < datetime.timedelta(hours=12)
    except Exception:
        return False


def _start_workflow():
    """Starts the Send emails workflow. Returns (ok, message)."""
    token = st.secrets.get("github_token")
    if not token:
        return False, "no `github_token` secret"
    repo = st.secrets.get("github_repo", DEFAULT_REPO)
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"ref": "main"}, timeout=15)
        if resp.status_code == 204:
            return True, ""
        return False, f"GitHub said HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def _job_status(job):
    if not job:
        return
    status = job.get("status")
    label = KINDS.get(job.get("kind"), job.get("kind"))
    who = f" (test run to {job['test_to']})" if job.get("test_to") else ""
    if status == "queued":
        st.info(f"⏳ **Queued:** {label} for {job['t_name']}{who}. Waiting for GitHub Actions to start…")
    elif status == "sending":
        sent = len(get_campaign_sent_emails(job["campaign_id"]))
        st.info(f"📤 **Sending:** {label} for {job['t_name']}{who}: {sent} sent so far (of about {job.get('to_send', '?')} this run).")
    elif status == "done":
        st.success(f"✅ **Last send:** {label} for {job['t_name']}{who}: {job.get('sent', 0)} sent, "
                   f"{job.get('failed', 0)} failed ({job.get('finished_at', '')[:16].replace('T', ' ')} UTC).")
    elif status == "expired":
        st.warning(f"⌛ The queued {label} for {job['t_name']} was never sent (over 12 hours old).")
    if status in ("queued", "sending") and st.button("🔄 Refresh status"):
        st.rerun()


# --- Working out who gets the entrant emails (pure functions, tested) ---

def _first_name(name):
    base = str(name or "").split(" (Team")[0].strip()
    return base.split(" ")[0] if base else ""


def payment_recipients(entries, fee, follow_url_fn):
    """One reminder per email address, listing each unpaid team. Entries paid by 'Other' are free."""
    if entries.empty:
        return []
    df = entries.copy()
    df["_paid"] = df["Paid"].fillna(False).astype(bool) if "Paid" in df.columns else False
    df["_method"] = (df["Payment Method"] if "Payment Method" in df.columns else pd.Series([""] * len(df), index=df.index)).fillna("").astype(str)
    df = df[~df["_paid"] & ~df["_method"].str.startswith("Other")]
    out = {}
    for _, r in df.sort_values("Sheet_Row").iterrows():
        email = str(r.get("Email") or "").strip().lower()
        if "@" not in email:
            continue
        rec = out.setdefault(email, {"email": email, "first_name": _first_name(r.get("Name")),
                                     "data": {"teams": [], "amount": 0, "follow_url": follow_url_fn(email)}})
        rec["data"]["teams"].append({"name": str(r.get("Name") or "Entrant"), "method": r["_method"]})
        rec["data"]["amount"] += fee
    return list(out.values())


def standings_recipients(standings, follow_url_fn, payouts=None):
    """One email per address with that person's teams and positions."""
    payouts = payouts or {}
    out = {}
    for _, r in standings.iterrows():
        email = str(r.get("Email") or "").strip().lower()
        if "@" not in email:
            continue
        rec = out.setdefault(email, {"email": email, "first_name": _first_name(r["Participant"]),
                                     "data": {"teams": [], "follow_url": follow_url_fn(email)}})
        rec["data"]["teams"].append({"name": r["Participant"], "rank": str(r["DRank"]), "score": int(r["Total"]),
                                     "elim": bool(r["Elim"]), "payout": payouts.get(r["Participant"], 0)})
    return list(out.values())


def top_rows(standings, n):
    alive = standings[~standings["Elim"].astype(bool)]
    return [{"rank": str(r["DRank"]), "name": r["Participant"], "score": int(r["Total"])} for _, r in alive.head(n).iterrows()]


def payout_summary_html(podium, standings, entries):
    """For the admin: who to pay, how much, and the payment method they entered with."""
    method = {}
    if not entries.empty and "Sheet_Row" in entries.columns:
        method = {str(r["Sheet_Row"]): str(r.get("Payment Method") or "") for _, r in entries.iterrows()}
    rows = []
    for p in podium:
        if not p.get("payout"):
            continue
        hit = standings[standings["Participant"] == p["name"]]
        email = hit["Email"].iloc[0] if not hit.empty and "Email" in hit.columns else ""
        how = method.get(str(hit["EntryId"].iloc[0]), "") if not hit.empty and "EntryId" in hit.columns else ""
        rows.append(f"<li><b>{html.escape(p['name'])}</b>: {fmt_money(p['payout'])} "
                    f"<span style='color:#7f8c8d;'>({html.escape(email)} · paid in by {html.escape(how)})</span></li>")
    return "<p><b>💰 Payouts to send</b></p><ul>" + "".join(rows) + "</ul>" if rows else ""


# --- The tab ---

def render_mailing_tab(t_id, t_name, t_start, logo_url, entry_url, *, get_standings=None, follow_url_fn=None,
                       current_round=0, t_status="", payout_settings=None):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    follow_url_fn = follow_url_fn or (lambda e: entry_url)
    subs = get_subscribers_df()
    active = subs[subs["unsubscribed_at"].isna()] if not subs.empty else subs

    c1, c2, c3 = st.columns(3)
    c1.metric("Subscribed", len(active))
    c2.metric("Unsubscribed", len(subs) - len(active))
    entered = get_entered_emails(t_id)
    c3.metric("Entered this tournament", len(entered))

    job = get_app_setting(EMAIL_JOB_KEY)
    _job_status(job)

    # --- Compose ---
    st.subheader("✉️ Send an email")
    kind = st.selectbox("Email", list(KINDS), format_func=KINDS.get, key="mail_kind")
    close_uk = resolve_close_time(get_config(t_id, 'close_time', ""), t_start)
    rules_md = get_config(t_id, 'rules', "") or ""
    recipients, extra, round_no, warnings = None, {}, None, []

    if kind in MAILING_LIST_KINDS:
        if close_uk:
            st.caption(f"⏳ Deadline in the email: **{format_close(close_uk)}** (from the Entries Close Time setting).")
        else:
            warnings.append("No close time is known for this tournament, so the email won't show a deadline.")

    elif kind == "payment_reminder":
        fee = st.number_input("Entry fee per team ($)", value=30, step=5, key="mail_fee")
        recipients = payment_recipients(get_entries_df(t_id), fee, follow_url_fn)
        st.caption("Goes to everyone with an unpaid entry (not 'Other'), one email per person listing their unpaid teams. "
                   "Tick Paid in 📝 Entries first so people who've paid aren't chased.")

    else:
        standings = get_standings() if get_standings else pd.DataFrame()
        if standings is None or standings.empty or "DRank" not in standings.columns or (standings["DRank"] == "-").all():
            st.warning("No standings yet. This email can be sent once scores are in.")
            return _subscribers_section(subs)
        extra = {"n_teams": len(standings), "top": top_rows(standings, 5 if kind == "round_recap" else 10)}
        payouts = {}
        if kind == "round_recap":
            finished = t_status in ("endofday", "completed", "complete")
            done = current_round if finished else current_round - 1
            round_no = int(st.number_input("After round", min_value=1, max_value=4, value=min(max(done, 1), 4), key="mail_round"))
            if not finished and round_no >= current_round:
                warnings.append(f"Round {current_round} is still in play, so these positions aren't final for the round.")
        else:
            if t_status not in ("completed", "complete"):
                warnings.append("The tournament isn't marked finished yet. Wait for the final result before sending.")
            ps = payout_settings or {}
            podium = split_prizes(standings, ps.get("prizes", [0, 0, 0]))
            payouts = {p["name"]: p["payout"] for p in podium}
            totals = dict(zip(standings["Participant"], standings["Total"]))
            extra.update(podium=[{"rank": p["rank"], "name": p["name"], "score": int(totals.get(p["name"], 0)),
                                  "payout": p["payout"]} for p in podium],
                         payouts_confirmed=bool(ps.get("confirmed")), winning_score=ps.get("winning_score"),
                         admin_note_html=payout_summary_html(podium, standings, get_entries_df(t_id)))
            if not ps.get("confirmed"):
                warnings.append("Payouts aren't confirmed in 💵 Financials, so the email won't show prize amounts.")
        recipients = standings_recipients(standings, follow_url_fn, payouts)

    for w in warnings:
        st.warning(w)

    defaults = build_job(kind, t_id, t_name, close_uk, "", round_no=round_no)
    subject = st.text_input("Subject", value=defaults["subject"], key=f"mail_subject_{kind}_{t_id}_{round_no}")
    intro = st.text_area("Opening message (blank line = new paragraph)", value=defaults["intro"], height=110,
                         key=f"mail_intro_{kind}_{t_id}_{round_no}")
    job_draft = build_job(kind, t_id, t_name, close_uk, rules_md, logo_url, entry_url, subject, intro,
                          recipients=recipients, extra=extra, round_no=round_no)

    # Who it will go to
    already = get_campaign_sent_emails(job_draft["campaign_id"])
    unsubscribed = set(subs.loc[subs["unsubscribed_at"].notna(), "email"]) if not subs.empty else set()
    if recipients is None:
        emails = set(active["email"]) if not active.empty else set()
        to_send = emails - already - (entered if kind == "closing_soon" else set())
    else:
        to_send = {r["email"] for r in recipients} - already
        if kind in RESPECTS_UNSUBSCRIBE:
            to_send -= unsubscribed
    notes = []
    if already: notes.append(f"{len(already)} already got this email")
    if kind == "closing_soon":
        n = len((set(active["email"]) if not active.empty else set()) & entered)
        notes.append(f"{n} {'subscriber has' if n == 1 else 'subscribers have'} already entered")
    people = lambda n: f"{n} {'person' if n == 1 else 'people'}"
    st.markdown(f"**Will go to {people(len(to_send))}**" + (f" ({'; '.join(notes)}, so they're skipped)." if notes else "."))
    if recipients:
        with st.expander("👥 Recipients"):
            st.dataframe(pd.DataFrame([{"Email": r["email"], "Name": r["first_name"],
                                        "Teams": ", ".join(t["name"] for t in r["data"].get("teams", []))}
                                       for r in recipients if r["email"] in to_send]), width="stretch", hide_index=True)

    me = (get_subscriber(sender) or {}) if sender else {}
    with st.expander("👀 Preview", expanded=False):
        if recipients:
            st.caption(f"Shown with {recipients[0]['email']}'s details.")
        st.html(render_html(job_draft, me.get("first_name") or "Eddie", "#" if kind in RESPECTS_UNSUBSCRIBE else "",
                            sample_data(job_draft)))

    audience = st.radio("Send to", ["everyone", "me"], horizontal=True, key=f"mail_audience_{kind}",
                        format_func=lambda a: f"Everyone ({len(to_send)})" if a == "everyone"
                        else "🧪 Just me: a full test run through GitHub Actions")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("🧪 Send a quick test to me", use_container_width=True, disabled=not (sender and pwd),
                     help="Sends one copy straight from here, without GitHub Actions."):
            test_job = dict(job_draft, subject=f"[Test] {job_draft['subject']}")
            unsub = unsubscribe_url(me.get("unsub_token")) if kind in RESPECTS_UNSUBSCRIBE else ""
            try:
                with GmailSender(sender, pwd) as mailer:
                    mailer.send(build_message(test_job, sender, sender, me.get("first_name") or "", unsub, sample_data(job_draft)))
                st.success(f"✅ Test sent to {sender}.")
            except Exception as e:
                st.error(f"🚨 Test email failed: {e}")

    with b2:
        busy = _job_active(job)
        if audience == "me":
            go = st.button("🚀 Run the test send", type="primary", use_container_width=True, disabled=busy or not sender)
        else:
            confirm = st.checkbox(f"I've checked the test. Send to {people(len(to_send))}.", key=f"mail_confirm_{kind}")
            go = st.button(f"🚀 Send to {people(len(to_send))}", type="primary", use_container_width=True,
                           disabled=busy or not confirm or not to_send)
        if go:
            queued = dict(job_draft, status="queued", requested_at=_now().isoformat())
            if audience == "me":
                queued.update(test_to=sender, subject=f"[Test] {job_draft['subject']}",
                              campaign_id=f"{job_draft['campaign_id']}:test-{int(_now().timestamp())}")
            if set_app_setting(EMAIL_JOB_KEY, queued):
                log_event("ADMIN ACTION", f"Queued {'test of ' if audience == 'me' else ''}{KINDS[kind]} email: "
                                          f"{queued['subject']} ({1 if audience == 'me' else len(to_send)} recipients)")
                ok, why = _start_workflow()
                if ok:
                    st.success("🚀 Queued and started. Sending takes about 2 seconds per email; you'll get a summary email when it's done.")
                else:
                    st.warning(f"Queued, but couldn't start the sender automatically ({why}). "
                               "Open GitHub → Actions → **Send emails** → **Run workflow** to send it.")
            else:
                st.error("🚨 Couldn't queue the email.")
        if busy:
            st.caption("A send is already queued or in progress.")

    _subscribers_section(subs)


def _subscribers_section(subs):
    st.markdown("---")
    st.subheader("👥 Subscribers")
    st.write("Signup link to share with new people:")
    st.code(signup_url())

    with st.expander(f"View all ({len(subs)})"):
        if not subs.empty:
            view = subs.copy()
            view["Status"] = view["unsubscribed_at"].apply(lambda x: "✅ Subscribed" if x is None or x != x else "🚫 Unsubscribed")
            st.dataframe(view[["email", "first_name", "last_name", "Status", "source", "referred_by", "subscribed_at"]],
                         width="stretch", hide_index=True)

    with st.expander("➕ Add or change a subscriber"):
        with st.form("add_sub_form", clear_on_submit=True):
            a1, a2 = st.columns(2)
            with a1: f_first = st.text_input("First name")
            with a2: f_last = st.text_input("Last name")
            f_email = st.text_input("Email *")
            if st.form_submit_button("Add / resubscribe", type="primary"):
                if "@" not in f_email:
                    st.error("🚨 Enter an email address.")
                else:
                    result = add_subscriber(f_email, f_first, f_last, source="Admin", resubscribe=True)
                    st.success({"added": "✅ Added.", "resubscribed": "✅ Resubscribed.",
                                "exists": "Already subscribed."}.get(result, "🚨 Failed to save."))
        u_email = st.text_input("Unsubscribe an address", placeholder="name@example.com", key="unsub_admin")
        if st.button("🚫 Unsubscribe") and u_email:
            if get_subscriber(u_email) and set_subscribed(u_email, False):
                log_event("ADMIN ACTION", f"Unsubscribed {u_email.strip().lower()} from the mailing list")
                st.success("Unsubscribed.")
            else:
                st.error("No subscriber with that email.")
