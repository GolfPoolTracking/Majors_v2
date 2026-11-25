"""Admin 📣 Mailing List tab: subscribers, email preview, test send, and queueing a send."""
import datetime

import requests
import streamlit as st

from campaigns import (EMAIL_JOB_KEY, KINDS, GmailSender, build_job, build_message, render_html,
                       signup_url, unsubscribe_url)
from db import (add_subscriber, get_app_setting, get_campaign_sent_emails, get_config, get_entered_emails,
                get_subscriber, get_subscribers_df, log_event, set_app_setting, set_subscribed)
from field_watch import format_close, resolve_close_time

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
    sent = len(get_campaign_sent_emails(job["campaign_id"]))
    status = job.get("status")
    label = KINDS.get(job.get("kind"), job.get("kind"))
    if status == "queued":
        st.info(f"⏳ **Queued:** {label} for {job['t_name']}. Waiting for GitHub Actions to start…")
    elif status == "sending":
        st.info(f"📤 **Sending:** {label} for {job['t_name']}: {sent} sent so far (of about {job.get('to_send', '?')} this run).")
    elif status == "done":
        st.success(f"✅ **Last send:** {label} for {job['t_name']}: {job.get('sent', 0)} sent, "
                   f"{job.get('failed', 0)} failed ({job.get('finished_at', '')[:16].replace('T', ' ')} UTC).")
    elif status == "expired":
        st.warning(f"⌛ The queued {label} for {job['t_name']} was never sent (over 12 hours old).")
    if status in ("queued", "sending") and st.button("🔄 Refresh status"):
        st.rerun()


def render_mailing_tab(t_id, t_name, t_start, logo_url, entry_url):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
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
    kind = st.radio("Email", list(KINDS), format_func=KINDS.get, horizontal=True, key="mail_kind")
    close_uk = resolve_close_time(get_config(t_id, 'close_time', ""), t_start)
    if close_uk:
        st.caption(f"⏳ Deadline in the email: **{format_close(close_uk)}** (from the Entries Close Time setting).")
    else:
        st.warning("No close time is known for this tournament, so the email won't show a deadline.")

    defaults = build_job(kind, t_id, t_name, close_uk, "")
    subject = st.text_input("Subject", value=defaults["subject"], key=f"mail_subject_{kind}_{t_id}")
    intro = st.text_area("Opening message (blank line = new paragraph)", value=defaults["intro"], height=110,
                         key=f"mail_intro_{kind}_{t_id}")
    rules_md = get_config(t_id, 'rules', "") or ""
    job_draft = build_job(kind, t_id, t_name, close_uk, rules_md, logo_url, entry_url, subject, intro)

    already = get_campaign_sent_emails(job_draft["campaign_id"])
    emails = set(active["email"]) if not active.empty else set()
    to_send = emails - already - (entered if kind == "closing_soon" else set())
    notes = []
    if already: notes.append(f"{len(already)} already got this email for this tournament")
    if kind == "closing_soon":
        n = len(emails & entered)
        notes.append(f"{n} {'subscriber has' if n == 1 else 'subscribers have'} already entered")
    st.markdown(f"**Will go to {len(to_send)} subscribers**" + (f" ({'; '.join(notes)}, so they're skipped)." if notes else "."))

    with st.expander("👀 Preview", expanded=False):
        first = (get_subscriber(sender) or {}).get("first_name") if sender else ""
        st.html(render_html(job_draft, first or "Eddie", "#"))

    b1, b2 = st.columns(2)
    with b1:
        if st.button("🧪 Send a test to me", use_container_width=True, disabled=not (sender and pwd)):
            me = get_subscriber(sender) or {}
            test_job = dict(job_draft, subject=f"[Test] {job_draft['subject']}")
            unsub = unsubscribe_url(me["unsub_token"]) if me.get("unsub_token") else "#"
            try:
                with GmailSender(sender, pwd) as mailer:
                    mailer.send(build_message(test_job, sender, sender, me.get("first_name") or "", unsub))
                st.success(f"✅ Test sent to {sender}.")
            except Exception as e:
                st.error(f"🚨 Test email failed: {e}")

    with b2:
        confirm = st.checkbox(f"I've checked the test. Send to {len(to_send)} subscribers.", key=f"mail_confirm_{kind}")
        busy = _job_active(job)
        if st.button(f"🚀 Send to {len(to_send)} subscribers", type="primary", use_container_width=True,
                     disabled=busy or not confirm or not to_send):
            job_draft.update(status="queued", requested_at=_now().isoformat())
            if set_app_setting(EMAIL_JOB_KEY, job_draft):
                log_event("ADMIN ACTION", f"Queued mailing list email: {job_draft['subject']} ({len(to_send)} recipients)")
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

    # --- Subscribers ---
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
