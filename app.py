import datetime, hashlib, hmac, html, json, re, time

import altair as alt
import pandas as pd
import pytz
import requests
import streamlit as st

from auth import make_magic_link_hash, secure_equals
from db import (
    insert_entry,
    fetch_active_tournament,
    fetch_alerted_field_from_db,
    fetch_aliases_from_db,
    fetch_close_time_from_db,
    fetch_dns_from_db,
    fetch_field_backup_from_db,
    fetch_fin_balances_from_db,
    fetch_hide_tt_from_db,
    fetch_logo_from_db,
    fetch_par_from_db,
    fetch_payout_config_from_db,
    fetch_reveal_time_from_db,
    fetch_short_url_from_db,
    get_config,
    get_entries_df,
    get_supabase,
    log_event,
    save_active_tournament,
    save_alerted_field_to_db,
    save_aliases_to_db,
    save_dns_to_db,
    save_fin_balances_to_db,
    save_hide_tt_to_db,
    save_logo_to_db,
    save_par_to_db,
    save_payout_config_to_db,
    save_short_url_to_db,
    update_config,
    update_paid_status,
    update_entry_field,
    update_specific_entry,
)
from emails import BASE_URL, send_confirmation_email, send_field_change_email, send_magic_link_email
from golf_api import (
    fetch_smart_leaderboard,
    get_api_cache,
    get_fixture_list,
    get_formatted_field,
    get_raw_entry_list,
    get_settings_cache,
    get_top_20_players,
)
from leaderboard import calculate_leaderboard, get_clean_entries, render_pga_leaderboard
from scoring import check_api_par_warning, get_calculated_par, get_dropdown_index, safe_int

# 🚨 GLOBAL VARIABLES 🚨
if "payment_options" in st.secrets:
    custom_pays = [p.strip() for p in st.secrets["payment_options"].split(",") if p.strip()]
    pay_opts = ["Select..."] + custom_pays + ["Other"]
else:
    # Set `payment_options` in Streamlit secrets (comma-separated) to show your payment methods.
    pay_opts = ["Select...", "Other"]

st.set_page_config(page_title="Golf Sweepstakes", page_icon="⛳", layout="wide")

# --- Error reporting (Sentry) ---
from sentry_setup import init_sentry  # noqa: E402
init_sentry()


# 🚨 UPTIMEROBOT INTERCEPTOR (Protects API Quotas & Keeps Supabase Awake) 🚨
if st.query_params.get("view") == "ping":
    st.write("Server is awake and ready! 🟢")
    try:
        # Lightweight query to keep Supabase from pausing after 7 days
        get_supabase().table('golf_tournament_configs').select('tournament_id').limit(1).execute()
        st.write("Supabase is awake! 🐘🟢")
    except Exception as e:
        st.write(f"Supabase ping error: {e}")
    st.stop()

# 🚨 DYNAMIC THEME CSS (FOR DARK MODE SUPPORT) 🚨
st.markdown("""
<style>
    .compact-container { background-color: var(--secondary-background-color); color: var(--text-color); border: 1px solid rgba(130, 130, 130, 0.4); border-radius: 6px; padding: 10px; margin-bottom: 15px; }
    details { border: 1px solid rgba(130, 130, 130, 0.4) !important; border-radius: 8px !important; background-color: var(--background-color) !important; margin-bottom: 8px !important; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    summary { background-color: var(--background-color) !important; color: var(--text-color) !important; display: flex !important; align-items: center; padding: 10px 12px !important; cursor: pointer; list-style: none !important; overflow-x: auto !important; -webkit-overflow-scrolling: touch; scrollbar-width: none; -ms-overflow-style: none; }
    summary::-webkit-scrollbar { display: none; }
    summary::before { content: '▶'; font-size: 0.8em; margin-right: 12px; color: var(--text-color); opacity: 0.6; transition: transform 0.2s ease-in-out; flex-shrink: 0; }
    details[open] > summary::before { transform: rotate(90deg); }
    summary > div { display: inline-grid !important; vertical-align: middle; margin-left: 2px; }
    .expanded-content { padding: 10px; background-color: var(--secondary-background-color) !important; border-top: 1px solid rgba(130, 130, 130, 0.4) !important; }
    table { color: var(--text-color); }
    small { color: var(--text-color); opacity: 0.7; }
</style>
""", unsafe_allow_html=True)

if "editing_row" not in st.session_state: st.session_state["editing_row"] = None

is_public = st.query_params.get("view") == "public"
target_tourney_id = st.query_params.get("tourney_id")

ADMIN_PASSWORD = st.secrets.get("admin_password")
if not ADMIN_PASSWORD and not is_public:
    st.error("🚨 Configuration Error: `admin_password` is missing from Streamlit Secrets.")
    st.stop()

# Edit links are signed with `magic_link_secret`. If it isn't set, a separate key is derived
# from the admin password so the password itself is never used as the signing key.
if st.secrets.get("magic_link_secret"):
    MAGIC_LINK_SECRET = str(st.secrets["magic_link_secret"])
else:
    MAGIC_LINK_SECRET = hmac.new(str(ADMIN_PASSWORD or "").encode(), b"golf-magic-link-v2", hashlib.sha256).hexdigest()


DEFAULT_RULES = """### 🏆 Tournament Rules\n\n⛳ **The Team:** Pick **5** players, max **2** from Top 20.\n\n🏌️‍♂️ **Scoring:** The **best 4 scores** each day count.\n\n✂️ **The Cut:** If **2 or more** picks miss the cut, team is out.\n\n⚖️ **Tie Break:** Predict Winner's total score to par.\n\n⏳ **Deadline:** Midnight before the tournament.\n\n💰 **Entry Fee:** **$30**."""


def generate_short_link(long_url):
    try:
        import urllib.parse
        resp = requests.get(f"https://is.gd/create.php?format=simple&url={urllib.parse.quote(long_url)}", timeout=5)
        if resp.status_code == 200 and "is.gd" in resp.text: return resp.text.strip()
    except Exception as e: 
        print(f"URL Shortener Error: {e}")
    return None


        


# --- 5. MAIN UI ---
settings = get_settings_cache()
if is_public:
    tid = target_tourney_id
    if not tid:
        pinned = fetch_active_tournament()
        if pinned: tid = pinned["id"]
    if not tid and "tournaments" in st.secrets: tid = next(iter(st.secrets["tournaments"]), None)
    if not tid: st.error("No tournament ID."); st.stop()
    st.session_state['current_t_id'] = tid
    
    current_par_override = fetch_par_from_db(tid)
    current_hide_tt = fetch_hide_tt_from_db(tid)

    try:
        pconf = json.loads(fetch_payout_config_from_db(tid))
        if "payout_confirmed" in pconf:
            settings[f"payout_confirmed_{tid}"] = pconf.get("payout_confirmed", False)
            settings[f"payout_1_{tid}"] = pconf.get("p1", 0)
            settings[f"payout_2_{tid}"] = pconf.get("p2", 0)
            settings[f"payout_3_{tid}"] = pconf.get("p3", 0)
            settings[f"payout_pot_{tid}"] = pconf.get("pot", 0)
    except Exception: pass
    
    lb_data, _, _, full_data, data_source = fetch_smart_leaderboard(tid)
    
    raw_results = full_data.get('results') or {}
    tourney_data = raw_results.get('tournament') or {}
    live_details = tourney_data.get('live_details') or {}
    t_status = str(live_details.get('status', '')).lower()
    is_tournament_started = t_status not in ['pre', '']
    
    tnm = tourney_data.get('name', 'Tournament')
    t_start = tourney_data.get('start_date')
    
    cache = get_api_cache()
    lb_key = f"lb_{tid}"
    is_live_now = (lb_key in cache and "R4 Live Scoring" in cache[lb_key].get("mode", ""))
    refresh_script = """setInterval(function(){ window.location.reload(); }, 300000);""" if is_live_now else ""
    
    st.html(f"""
    <script>
        document.title = "{tnm.replace('"', '')} Sweepstakes";
        if (!document.querySelector('meta[name="apple-mobile-web-app-capable"]')) {{
            const meta1 = document.createElement('meta'); meta1.name = "apple-mobile-web-app-capable"; meta1.content = "yes"; document.head.appendChild(meta1);
            const meta2 = document.createElement('meta'); meta2.name = "apple-mobile-web-app-status-bar-style"; meta2.content = "black-translucent"; document.head.appendChild(meta2);
            const meta3 = document.createElement('meta'); meta3.name = "mobile-web-app-capable"; meta3.content = "yes"; document.head.appendChild(meta3);
        }}
        if (!document.getElementById('umami-tracker')) {{
            const script = document.createElement('script');
            script.id = 'umami-tracker';
            script.defer = true;
            script.dataset.websiteId = "6b529d5f-180e-452a-b6bf-ca2a0525186b"; 
            script.src = "https://cloud.umami.is/script.js"; 
            document.head.appendChild(script);
        }}
        {refresh_script}
    </script>
    """)
    
    t_start_dt = datetime.datetime.strptime(str(t_start), "%Y-%m-%d %H:%M:%S") if t_start else None
    
    # Set default times to 5:00 AM
    default_close = t_start_dt.replace(hour=5, minute=0, second=0) if t_start_dt else None
    default_reveal = t_start_dt.replace(hour=5, minute=0, second=0) if t_start_dt else None
    
    # Fetch from Supabase
    db_close = fetch_close_time_from_db(tid)
    db_reveal = fetch_reveal_time_from_db(tid)
    
    close_time = datetime.datetime.strptime(db_close, "%Y-%m-%d %H:%M:%S") if db_close else default_close
    reveal_time = datetime.datetime.strptime(db_reveal, "%Y-%m-%d %H:%M:%S") if db_reveal else default_reveal
    
    try:
        uk_tz = pytz.timezone('Europe/London')
        et_tz = pytz.timezone('America/New_York')
        if close_time:
            ct_uk = uk_tz.localize(close_time)
            ct_et = ct_uk.astimezone(et_tz)
            dual_close = f"{ct_uk.strftime('%a, %b %d at %I:%M %p')} UK / {ct_et.strftime('%a, %b %d at %I:%M %p')} ET"
            close_time_str_email = dual_close
            close_time_str_ui = dual_close
        if reveal_time:
            rt_uk = uk_tz.localize(reveal_time)
            rt_et = rt_uk.astimezone(et_tz)
            reveal_time_str_ui = f"{rt_uk.strftime('%a, %b %d at %I:%M %p')} UK / {rt_et.strftime('%a, %b %d at %I:%M %p')} ET"
    except Exception:
        if close_time: 
            close_time_str_email = close_time.strftime('%a, %b %d at %I:%M %p')
            close_time_str_ui = close_time_str_email
        if reveal_time:
            reveal_time_str_ui = reveal_time.strftime('%a, %b %d at %I:%M %p')
            
    now_utc = datetime.datetime.now(pytz.utc)
    now_uk = now_utc.astimezone(pytz.timezone('Europe/London'))
    now_naive = now_uk.replace(tzinfo=None)
    
    is_accepting_entries = close_time and now_naive < close_time 
    is_pre_reveal = reveal_time and now_naive < reveal_time
    show_real = True 
    
    raw_rules = get_config(tid, 'rules', DEFAULT_RULES)
    rules_text = raw_rules if raw_rules and raw_rules.strip() else DEFAULT_RULES
    public_dns = fetch_dns_from_db(tid)
    
    public_logo = fetch_logo_from_db(tid)
    logo_rendered = False
    
    if public_logo:
        try:
            st.markdown(f"<div style='text-align: center; padding-bottom: 10px;'><img src='{html.escape(public_logo)}' style='max-width: 250px; max-height: 150px; width: 100%; object-fit: contain;'></div>", unsafe_allow_html=True)
            logo_rendered = True
        except Exception: pass 
            
    if not logo_rendered:
        st.markdown(f"<h1 style='text-align: center; padding-bottom: 10px;'>🏆 {html.escape(tnm.split('(')[0])}</h1>", unsafe_allow_html=True)
    
    tab_names = ["🏆 Leaderboard"]
    if is_accepting_entries: tab_names.append("📝 Enter Team")
    if is_accepting_entries or is_pre_reveal: tab_names.append("🔍 Check Entry")
    tab_names.append("💰 Prize Pool") 
    if not is_pre_reveal: 
        tab_names.append("📝 Full Entries")
        tab_names.append("📊 Tournament Insights")
    tab_names.append("📜 Rules") 
    if show_real: tab_names.append("⛳ Official PGA Scores")
    
    sel = st.tabs(tab_names)
    
    if (is_accepting_entries or is_pre_reveal) and not is_tournament_started:
        top20_cached = get_top_20_players()
        formatted_field = get_formatted_field(tid, top20_cached)
    else:
        backup_str = fetch_field_backup_from_db(tid)
        formatted_field = [x.strip() for x in backup_str.split(',')] if backup_str else []
        
    valid_p = [p.split(" (Top 20")[0] for p in formatted_field]
    full_df, total_count = get_clean_entries(tid, True, valid_players=valid_p, dns_players=public_dns.split(","))
    
    is_authenticated = False
    raw_email = st.query_params.get("edit_email")
    auth_email = raw_email[0] if isinstance(raw_email, list) else raw_email
    raw_hash = st.query_params.get("auth")
    auth_hash = raw_hash[0] if isinstance(raw_hash, list) else raw_hash
    raw_ts = st.query_params.get("ts")
    auth_ts = raw_ts[0] if isinstance(raw_ts, list) else raw_ts
    auth_email_dec = ""
    
    if auth_email and auth_hash and auth_ts:
        try:
            import urllib.parse
            auth_email_dec = urllib.parse.unquote(str(auth_email))
            clean_email = auth_email_dec.strip().lower()
            ts_int = int(auth_ts)
            
            if 0 <= int(time.time()) - ts_int < 3600: 
                expected_hash = make_magic_link_hash(clean_email, tid, ts_int, MAGIC_LINK_SECRET)
                if secure_equals(auth_hash, expected_hash):
                    is_authenticated = True
                    st.html("""
                        <script>
                        let attempts = 0;
                        let tabPoller = setInterval(function() {
                            const tabs = document.querySelectorAll("button[role=tab]");
                            if (tabs.length > 0) {
                                for (let i = 0; i < tabs.length; i++) {
                                    if (tabs[i].innerText.includes("Check Entry")) {
                                        tabs[i].click();
                                        clearInterval(tabPoller);
                                        break;
                                    }
                                }
                            }
                            attempts++;
                            if (attempts > 60) clearInterval(tabPoller);
                        }, 50);
                        </script>
                    """)
            else:
                st.error("🚨 This secure edit link has expired (valid for 1 hour). Please request a new one.")
        except Exception as e:
            pass

    current_tab = 0
    
    with sel[current_tab]: 
        if is_pre_reveal:
            calculate_leaderboard(tid, tnm, t_start, par_override=current_par_override, header_only=True, hide_title=True, search_query="")
            st.info(f"🔒 **Tournament Picks are Hidden**\n\nThe public leaderboard and all player entries will be revealed on **{reveal_time_str_ui}**.")
            
            if close_time:
                uk_tz = pytz.timezone('Europe/London')
                close_ts = int(uk_tz.localize(close_time).timestamp() * 1000)
                timer_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                <style>
                    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; background: transparent; overflow: hidden; }}
                    .box {{ text-align: center; font-size: 20px; font-weight: bold; padding: 15px; border-radius: 8px; border: 2px solid #3498db; width: 100%; max-width: 400px; box-sizing: border-box; background-color: #f8f9fa; color: #2c3e50; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
                    @media (prefers-color-scheme: dark) {{
                        .box {{ background-color: #262730; color: #FAFAFA; border-color: #2980b9; }}
                    }}
                </style>
                </head>
                <body>
                    <div class="box">
                        ⏳ Entries Close In:<br>
                        <span id="countdown" style="color: #e74c3c; font-size: 24px; display: inline-block; margin-top: 8px;">Loading...</span>
                    </div>
                    <script>
                        var countDownDate = {close_ts};
                        function tick() {{
                            var el = document.getElementById("countdown");
                            if (!el) return;
                            var distance = countDownDate - new Date().getTime();
                            if (distance < 0) {{
                                el.innerHTML = "🔒 Entries are now Closed!";
                                return;
                            }}
                            var days = Math.floor(distance / 86400000);
                            var hours = Math.floor((distance % 86400000) / 3600000);
                            var mins = Math.floor((distance % 3600000) / 60000);
                            var secs = Math.floor((distance % 60000) / 1000);
                            
                            el.innerHTML = (days > 0 ? days + "d " : "") + hours + "h " + mins + "m " + secs + "s";
                            setTimeout(tick, 1000);
                        }}
                        tick();
                    </script>
                </body>
                </html>
                """
                st.iframe(timer_html, height=120)

            st.markdown(f"""
                <div style="text-align: center; margin: -5px auto 20px auto; max-width: 400px; color: var(--text-color);">
                    <div style="font-size: 14px; opacity: 0.8; margin-bottom: 2px;">🎟️ Total Entries Received</div>
                    <div style="font-size: 32px; font-weight: bold;">{total_count}</div>
                </div>
            """, unsafe_allow_html=True)
        else:
            search_val = st.text_input("🔍 Find a Player...", placeholder="Type a name to filter the leaderboard...")
            calculate_leaderboard(tid, tnm, t_start, par_override=current_par_override, dns_input=public_dns, valid_players=valid_p, is_admin_view=False, hide_title=True, search_query=search_val)
    current_tab += 1
    
    if is_accepting_entries:
        with sel[current_tab]:
            if st.session_state.get(f"success_{tid}"):
                success_html = f"""
                <div style='text-align: center; padding: 40px 20px; background-color: var(--secondary-background-color); border-radius: 10px; border: 2px solid #2ecc71; margin-bottom: 20px;'>
                    <h2 style='color: #27ae60; margin-top: 0;'>🎉 Success! Your team is locked in.</h2>
                    <p style='font-size: 16px;'>We've securely saved your entry and sent a confirmation email to <b>{html.escape(st.session_state.get(f'success_email_{tid}'))}</b>.</p>
                    <p style='font-size: 15px; opacity: 0.8;'>You can review or edit your picks at any time before the deadline using the <b>🔍 Check Entry</b> tab.</p>
                </div>
                """
                st.markdown(success_html, unsafe_allow_html=True)
                
                if st.button("⬅️ Submit Another Team"):
                    st.session_state[f"success_{tid}"] = False
                    st.rerun()
            else:
                st.markdown("### 🏌️ Submit Your Entry")
                if close_time: st.info(f"⏳ **Deadline:** The entry form will automatically close on **{close_time_str_ui}**.")
                st.caption("Fill out the form below to lock in your team. You will receive an email confirmation.")
                
                with st.form("entry_form"):
                    col1, col2 = st.columns(2)
                    with col1: form_name = st.text_input("Full Name *")
                    with col2: form_email = st.text_input("Email Address *")
                    
                    st.markdown("**Select your 5 Golfers (Maximum of 2 Top 20 players):**")
                    p1, p2, p3, p4, p5 = [st.selectbox(f"Pick {i}", [""] + formatted_field) for i in range(1, 6)]
                    
                    col3, col4 = st.columns(2)
                    with col3: 
                        form_tie = st.number_input("Tie Breaker: Winner's total score to par (e.g., -12) *", value=None, step=1)
                        confirm_pos = st.checkbox("If Tie-Breaker is above par, check this box to confirm")
                    with col4: 
                        form_payment = st.selectbox("Payment Method *", pay_opts)
                    
                    submitted = st.form_submit_button("Submit Team", type="primary")
                    
                    if submitted:
                        picks = [p1, p2, p3, p4, p5]; clean_picks = [p for p in picks if p != ""]
                        has_error = True 
                        
                        if not form_name or not form_email: st.error("🚨 Name and Email are required.")
                        elif not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", form_email.strip()): st.error("🚨 Please enter a valid email address.")
                        elif form_payment == "Select...": st.error("🚨 Please select a Payment Method.")
                        elif form_tie is None: st.error("🚨 Please enter a Tie Breaker score.")
                        elif form_tie > 0 and not confirm_pos: st.error("🚨 You entered a positive tie-breaker score. If this is correct, please check the confirmation box.")
                        elif len(clean_picks) != 5: st.error("🚨 You must select exactly 5 players.")
                        elif len(set(clean_picks)) != 5: st.error("🚨 Duplicate players detected! You must pick 5 unique golfers.")
                        elif sum(1 for p in clean_picks if "(Top 20" in p) > 2: st.error("🚨 Too many Top 20 players! You selected more than 2.")
                        else:
                            has_error = False 
                            
                        if not has_error:
                            sub_sig = f"{form_email}_{p1}_{p2}_{p3}_{p4}_{p5}"
                            if st.session_state.get(f"last_sub_{tid}") == sub_sig:
                                st.session_state[f"success_{tid}"] = True
                                st.rerun()
                            
                            st.session_state[f"last_sub_{tid}"] = sub_sig
                            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            final_payment = form_payment
                            
                            with st.spinner("Locking in your team..."):
                                if insert_entry(tid, [ts, form_name, form_email, final_payment, form_tie, p1, p2, p3, p4, p5]):
                                    log_event("NEW ENTRY", f"Team submitted by {form_name}")
                                    send_confirmation_email(form_email, form_name, clean_picks, form_tie, final_payment, tnm, tid, is_edit=False, close_time_str=close_time_str_email, logo_url=public_logo)
                                    st.session_state[f"success_{tid}"] = True
                                    st.session_state[f"success_email_{tid}"] = form_email
                                    st.rerun()
                                else: 
                                    st.error("🚨 Could not save entry. Please contact the administrator.")
                                    st.session_state[f"last_sub_{tid}"] = None 
        current_tab += 1

    if is_accepting_entries or is_pre_reveal: 
        with sel[current_tab]: 
            if st.session_state.get("editing_row"):
                row_data = st.session_state["editing_row"]
                st.markdown("### ✏️ Edit Your Team")
                
                with st.form("edit_form"):
                    col1, col2 = st.columns(2)
                    with col1: edit_name = st.text_input("Name", value=row_data['Name'])
                    with col2: edit_email = st.text_input("Email", value=row_data['Email'], disabled=True)
                    
                    p_vals = row_data['Picks'] + [""] * 5
                    ep1, ep2, ep3, ep4, ep5 = [st.selectbox(f"Pick {i+1}", [""] + formatted_field, index=get_dropdown_index(p_vals[i], formatted_field)) for i in range(5)]
                    
                    col3, col4 = st.columns(2)
                    with col3:
                        edit_tie = st.number_input("Tie Breaker", value=int(safe_int(row_data['Tie'])), step=1)
                        edit_confirm_pos = st.checkbox("Confirm score is > 0 (above par)", value=(int(safe_int(row_data['Tie'])) > 0))
                    
                    with col4: 
                        safe_idx = pay_opts.index(row_data['Payment']) if row_data['Payment'] in pay_opts else 0
                        edit_payment = st.selectbox("Payment Method *", pay_opts, index=safe_idx)
                    
                    c_submit, c_cancel = st.columns([1, 4])
                    with c_submit: update_submitted = st.form_submit_button("Update Team", type="primary")
                    
                    if update_submitted:
                        new_picks = [ep1, ep2, ep3, ep4, ep5]; clean_picks = [p for p in new_picks if p != ""]
                        has_error = True 
                        
                        if not edit_name: st.error("🚨 Name is required.")
                        elif edit_payment == "Select...": st.error("🚨 Please select a Payment Method.")
                        elif edit_tie > 0 and not edit_confirm_pos: st.error("🚨 You entered a positive tie-breaker score.")
                        elif len(clean_picks) != 5: st.error("🚨 You must select exactly 5 players.")
                        elif len(set(clean_picks)) != 5: st.error("🚨 Duplicate players detected!")
                        elif sum(1 for p in clean_picks if "(Top 20" in p) > 2: st.error("🚨 Too many Top 20 players! You selected more than 2.")
                        else:
                            has_error = False 
                            
                        if not has_error:
                            sub_sig = f"{edit_email}_{ep1}_{ep2}_{ep3}_{ep4}_{ep5}_{edit_tie}_{edit_payment}"
                            if st.session_state.get(f"last_upd_{tid}") == sub_sig:
                                st.session_state["editing_row"] = None
                                st.session_state[f"edit_success_{tid}"] = True
                                st.rerun()
                                
                            st.session_state[f"last_upd_{tid}"] = sub_sig
                            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            final_edit_payment = edit_payment
                            
                            with st.spinner("Updating your team..."):
                                if update_specific_entry(tid, row_data['Sheet_Row'], [ts, edit_name, edit_email, final_edit_payment, edit_tie, ep1, ep2, ep3, ep4, ep5]):
                                    log_event("ENTRY UPDATED", f"Team updated by {edit_name}")
                                    send_confirmation_email(edit_email, edit_name, clean_picks, edit_tie, final_edit_payment, tnm, tid, is_edit=True, close_time_str=close_time_str_email, logo_url=public_logo)
                                    st.session_state["editing_row"] = None
                                    st.session_state[f"edit_success_{tid}"] = True
                                    st.session_state[f"edit_success_email_{tid}"] = edit_email
                                    st.rerun()
                                else: 
                                    st.error("🚨 Could not update entry.")
                                    st.session_state[f"last_upd_{tid}"] = None
                                    
                if st.button("Cancel Edit"): st.session_state["editing_row"] = None; st.rerun()
            else:
                email_input = ""
                if st.session_state.get(f"edit_success_{tid}"):
                    edit_success_html = f"""
                    <div style='text-align: center; padding: 40px 20px; background-color: var(--secondary-background-color); border-radius: 10px; border: 2px solid #2ecc71; margin-bottom: 20px;'>
                        <h2 style='color: #27ae60; margin-top: 0;'>✅ Team Updated Successfully!</h2>
                        <p style='font-size: 16px;'>We've securely saved your changes and sent a new confirmation email to <b>{html.escape(st.session_state.get(f'edit_success_email_{tid}'))}</b>.</p>
                    </div>
                    """
                    st.markdown(edit_success_html, unsafe_allow_html=True)
                    if st.button("⬅️ Back to Check Entry"):
                        st.session_state[f"edit_success_{tid}"] = False
                        st.rerun()
                else:
                    st.markdown("### 🔍 Check Your Entry")
                    if is_authenticated:
                        st.success("✅ **Secure link verified!** You may now edit your team directly.")
                        email_input = auth_email_dec
                    else:
                        email_input = st.text_input("Email Address:", placeholder="e.g., name@example.com")
                    
                if email_input:
                    user_df, _ = get_clean_entries(tid, True, valid_players=valid_p, dns_players=public_dns.split(","), email_filter=email_input)
                    if not user_df.empty:
                        st.success(f"Found {len(user_df)} team(s) linked to this email.")
                        
                        if not is_authenticated and is_accepting_entries:
                            if st.button("📧 Send Secure Edit Link for All Entries", type="primary", width="stretch"):
                                with st.spinner("Sending secure link to your email..."):
                                    if send_magic_link_email(email_input, tnm, tid, MAGIC_LINK_SECRET, public_logo):
                                        st.success("✅ Secure edit link sent! Please check your email inbox to unlock your entries.")
                                    else:
                                        st.error("🚨 Failed to send email. Please check configuration.")
                                        
                        for idx, r in user_df.iterrows():
                            with st.container():
                                st.markdown("---")
                                name_col_val = next((c for c in user_df.columns if c not in ['Sheet_Row', 'Email', 'Payment Method', 'Paid', 'Tie', 'Picks', 'Field Status', 'SheetWarnings', 'Tie Breaker'] and 'pick' not in c.lower()), user_df.columns[2])
                                p_list = [str(r[c]) for c in ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5'] if c in user_df.columns and pd.notna(r[c]) and str(r[c]).strip() != ""]
                                tie_val = int(safe_int(r.get('Tie Breaker', r.get('Tie', 0))))
                                c1, c2 = st.columns([3, 1])
                                with c1:
                                    safe_team_name = html.escape(str(r[name_col_val]))
                                    safe_picks = html.escape(', '.join(p_list))
                                    if is_authenticated:
                                        st.markdown(f"**Team Name:** {safe_team_name}  |  **Tie Breaker:** `{tie_val}`\n\n**Picks:** {safe_picks}")
                                        if not str(r.get("Field Status", "✅ All Valid")).startswith("✅"): st.error(f"🚨 Roster Issue: {r.get('Field Status')}")
                                    else:
                                        st.markdown(f"**Team Name:** {safe_team_name}\n\n🔒 *Picks and Tie Breaker are hidden. Click the button above to send a secure link to your email to view or edit this entry.*")
                                        if not str(r.get("Field Status", "✅ All Valid")).startswith("✅"): st.error(f"🚨 Roster Issue Detected! Please use the secure link to fix your team.")
                                        
                                with c2:
                                    if is_accepting_entries:
                                        if is_authenticated and str(r['Email']).lower() == auth_email_dec.lower():
                                            if st.button("✏️ Edit Entry", key=f"edit_{r['Sheet_Row']}", type="primary"):
                                                st.session_state["editing_row"] = {'Name': r.get('_Original_Name', r[name_col_val]), 'Email': r['Email'], 'Payment': r.get('Payment Method', 'Select...'), 'Tie': tie_val, 'Picks': p_list, 'Sheet_Row': r['Sheet_Row']}
                                                st.rerun()
                    else: st.error("No entries found for that email.")
        current_tab += 1

    with sel[current_tab]:
        st.markdown("### 💰 Tournament Prize Pool")
        if settings.get(f"payout_confirmed_{tid}", False):
            c1, c2, c3 = st.columns(3)
            with c1: st.markdown(f"<div style='text-align: center; padding: 20px; border: 1px solid var(--border-color); border-radius: 8px;'><p style='margin:0; font-size:18px;'>🥇 1st Place</p><p style='margin:0; font-size:32px; font-weight:bold;'>${settings.get(f'payout_1_{tid}', 0)}</p></div>", unsafe_allow_html=True)
            with c2: st.markdown(f"<div style='text-align: center; padding: 20px; border: 1px solid var(--border-color); border-radius: 8px;'><p style='margin:0; font-size:18px;'>🥈 2nd Place</p><p style='margin:0; font-size:32px; font-weight:bold;'>${settings.get(f'payout_2_{tid}', 0)}</p></div>", unsafe_allow_html=True)
            with c3: st.markdown(f"<div style='text-align: center; padding: 20px; border: 1px solid var(--border-color); border-radius: 8px;'><p style='margin:0; font-size:18px;'>🥉 3rd Place</p><p style='margin:0; font-size:32px; font-weight:bold;'>${settings.get(f'payout_3_{tid}', 0)}</p></div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; margin-top:20px; margin-bottom:15px; font-size: 18px;'>Total Prize Pot: <b>${settings.get(f'payout_pot_{tid}', 0)}</b></div>", unsafe_allow_html=True)
            
            if not full_df.empty and 'Paid' in full_df.columns:
                unpaid_count = len(full_df[full_df['Paid'] == False])
                if unpaid_count > 0:
                    payment_text = "entry payment is" if unpaid_count == 1 else "entry payments are"
                    st.warning(f"⚠️ **Note:** The final prize pool is subject to change as **{unpaid_count}** {payment_text} still pending verification.")
        else: st.info("Payout totals will be displayed here once all entries are closed and entry fees have been fully reconciled.")
    current_tab += 1

    if not is_pre_reveal:
        with sel[current_tab]: 
            st.markdown("### 📝 Full Entry List")
            st.metric("🎟️ Total Entries", total_count)
            if not full_df.empty: 
                cols_to_drop = ['Sheet_Row', 'Email', 'Payment Method', 'Paid', '_Original_Name', 'tournament_id', 'picks', 'id', 'created_at']
                st.dataframe(full_df.drop(columns=cols_to_drop, errors='ignore'), width="stretch", hide_index=True)
        current_tab += 1
        
        with sel[current_tab]:
            st.markdown("### 📊 The Hive Mind: Pick Analytics")
            st.caption("See who the crowd is backing! This data is generated from all locked-in teams.")
            
            if total_count > 0 and not full_df.empty:
                pick_cols = [c for c in ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5'] if c in full_df.columns]
                all_picks = []
                for c in pick_cols:
                    all_picks.extend(full_df[c].dropna().astype(str).tolist())
                
                clean_picks = [p.split(" (Top 20")[0].strip() for p in all_picks if p.strip() and p.lower() != 'nan']
                
                if clean_picks:
                    pick_counts = pd.Series(clean_picks).value_counts()
                    pick_pct = (pick_counts / total_count) * 100
                    
                    chart_df = pd.DataFrame({
                        'Player': pick_counts.index,
                        'Picks': pick_counts.values,
                        '% of Field': pick_pct.values
                    }).set_index('Player')
                    
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        st.subheader("📈 Top 20 Most Picked Players")
                        top_20_df = chart_df.head(20).reset_index()
                        chart = alt.Chart(top_20_df).mark_bar(color='#3498db', cornerRadiusEnd=4).encode(
                            x=alt.X('Picks:Q', title='Number of Picks', axis=alt.Axis(tickMinStep=1)),
                            y=alt.Y('Player:N', sort='-x', title=None, axis=alt.Axis(labelLimit=250)),
                            tooltip=[alt.Tooltip('Player:N', title='Golfer'), alt.Tooltip('Picks:Q'), alt.Tooltip('% of Field:Q', format='.1f')]
                        ).properties(height=alt.Step(25))
                        st.altair_chart(chart, width="stretch")
                    
                    with col2:
                        st.subheader("💡 Quick Stats")
                        st.metric("Total Valid Teams", total_count)
                        top_player = chart_df.index[0]
                        top_picks = int(chart_df.iloc[0]['Picks'])
                        top_pct = chart_df.iloc[0]['% of Field']
                        st.metric("Most Popular Pick", f"{top_player}")
                        st.caption(f"{top_picks} picks ({top_pct:.1f}%)")
                        unique_picks = chart_df[chart_df['Picks'] == 1]
                        st.metric("Lone Wolf Picks", len(unique_picks))
                        st.caption("Players picked by exactly 1 team")
                        
                    with st.expander("📋 View Full Pick Breakdown (All Players)"):
                        st.dataframe(chart_df.reset_index().style.format({'% of Field': '{:.1f}%'}), width="stretch", hide_index=True)
            else:
                st.info("Not enough data to generate insights yet.")
        current_tab += 1
        
    with sel[current_tab]: st.markdown(rules_text)
    current_tab += 1
    
    if show_real:
        with sel[current_tab]: render_pga_leaderboard(lb_data, full_data, tid, "pub", par_override=current_par_override, hide_tt=current_hide_tt)

else:
    with st.sidebar:
        st.title("⚙️ Admin")
        
        url_token = st.query_params.get("token", "")
        
        # Only the dedicated `admin_token` secret is accepted in the URL, never the admin password,
        # so the password doesn't end up in browser history, shared links or server logs.
        admin_token = str(st.secrets.get("admin_token", "") or "")
        is_token_valid = bool(url_token and admin_token) and secure_equals(str(url_token).strip(), admin_token)
        
        if is_token_valid:
            st.session_state["admin_auth"] = True
            
        if not st.session_state.get("admin_auth", False):
            if url_token and not is_token_valid:
                st.warning("⚠️ Invalid token provided in URL.")
                
            pwd_input = st.text_input("Password", type="password")
            if not pwd_input or not secure_equals(pwd_input, ADMIN_PASSWORD):
                if pwd_input: st.error("❌ Incorrect password.")
                st.stop()
            else:
                st.session_state["admin_auth"] = True
                st.rerun()
        else:
            st.success("✅ Logged in securely")
            if st.button("🚪 Logout"):
                st.session_state["admin_auth"] = False
                if "token" in st.query_params:
                    del st.query_params["token"]
                st.rerun()
        
        ak = [k for k in st.secrets.keys() if "api" in k.lower() and "key" in k.lower()]
        if not ak: st.error("No API keys found in secrets!"); st.stop()
            
        active = st.selectbox("API Key", ak, index=ak.index(settings.get("active_api_key", ak[0])) if settings.get("active_api_key") in ak else 0)
        st.caption(f"📊 **Quota:** {settings.get(f'quota_{active}', 'Checking (Will update on next API pull)...')}")
        
        # The admin page opens on the pinned "active" tournament, so it doesn't have to be picked
        # again every visit. "Switch tournament" shows the pickers; "Pin" makes the choice stick.
        pinned = fetch_active_tournament()
        picking = st.session_state.get("picking_tournament", False) or not pinned
        
        if not picking:
            t_id, t_key, t_start = pinned["id"], pinned.get("name") or f"Tournament {pinned['id']}", pinned.get("start")
            st.info(f"📌 **Active tournament:** {t_key}")
            if st.button("🔄 Switch tournament", use_container_width=True):
                st.session_state["picking_tournament"] = True
                st.rerun()
        else:
            use_manual = st.checkbox("☑️ Manually enter Tournament ID")
            if use_manual:
                t_id, t_key, t_start = st.text_input("Tournament ID", value="832"), st.text_input("Tournament Name", value="The Masters"), None
            else:
                c_tour, c_year = st.columns(2)
                with c_tour: tour_val = st.selectbox("Tour", ["2 - PGA Tour", "1 - DP World Tour"])
                with c_year:
                    this_year = datetime.date.today().year
                    year_val = st.selectbox("Season", [this_year - 1, this_year, this_year + 1], index=1)
            
                cache_key_fix = f"fixtures_{tour_val}_{year_val}"
            
                if st.button("📥 Load Tournament List", use_container_width=True):
                    with st.spinner("Fetching schedule from API..."):
                        settings[cache_key_fix] = get_fixture_list(int(tour_val.split(" - ")[0]), year_val)
                    st.rerun()
                
                fixtures = settings.get(cache_key_fix, {})
            
                if not fixtures:
                    st.info("👆 Click to fetch the schedule, or switch to Manual Entry.")
                    t_id, t_key, t_start = None, None, None
                else:
                    fixture_names = list(fixtures.keys())
                    pinned_idx = next((i for i, k in enumerate(fixture_names) if pinned and str(fixtures[k]['id']) == str(pinned['id'])), 0)
                    t_key = st.selectbox("Tournament", fixture_names, index=pinned_idx)
                    t_id, t_start = fixtures[t_key]['id'], fixtures[t_key]['start']
            
            
            if t_id:
                if pinned and str(pinned["id"]) == str(t_id):
                    st.caption("📌 This is the active tournament.")
                elif st.button("📌 Pin as active tournament", type="primary", use_container_width=True):
                    if save_active_tournament(t_id, t_key, t_start):
                        log_event("ADMIN ACTION", f"Pinned active tournament: {t_key} ({t_id})")
                        st.session_state["picking_tournament"] = False
                        st.rerun()
                    else:
                        st.error("🚨 Failed to save the active tournament. Has sql/golf_app_settings.sql been run?")
            if pinned and st.button(f"↩️ Back to {pinned.get('name') or pinned['id']}", use_container_width=True):
                st.session_state["picking_tournament"] = False
                st.rerun()
            
        if t_id:
            st.session_state['current_t_id'] = t_id

            st.subheader("🌍 OWGR")
            if st.button("📥 Force Refresh Top 20", type="primary"): 
                get_top_20_players.clear()  
                get_top_20_players()        
                log_event("ADMIN ACTION", "Forced a manual refresh of the OWGR Top 20")
                st.success("✅ Fresh OWGR Top 20 pulled and cached!")
                time.sleep(1)
                st.rerun()                  
            
            st.subheader("📋 Supabase Connection")
            st.success("✅ Connected to Supabase!")
            
            current_par_override = fetch_par_from_db(t_id)
            current_hide_tt = fetch_hide_tt_from_db(t_id)

            try:
                pconf = json.loads(fetch_payout_config_from_db(t_id))
                if "payout_confirmed" in pconf:
                    settings[f"payout_confirmed_{t_id}"] = pconf.get("payout_confirmed", False)
                    settings[f"payout_1_{t_id}"] = pconf.get("p1", 0)
                    settings[f"payout_2_{t_id}"] = pconf.get("p2", 0)
                    settings[f"payout_3_{t_id}"] = pconf.get("p3", 0)
                    settings[f"payout_pot_{t_id}"] = pconf.get("pot", 0)
            except Exception: pass
            
            st.subheader("🖼️ Branding")
            current_logo = fetch_logo_from_db(t_id)
            new_logo = st.text_input("Logo URL", value=current_logo)
            
            if new_logo != current_logo:
                if save_logo_to_db(t_id, new_logo):
                    st.success("✅ Logo permanently saved to Supabase!")
                    st.rerun()
                else:
                    st.error("🚨 Failed to save logo.")
                    
            if new_logo: st.image(new_logo, width="stretch")
            
            st.subheader("🚑 Smart DNS Manager")
            current_dns = fetch_dns_from_db(t_id)
            st.info(f"**Current DNS:** {current_dns if current_dns else 'None'}")
            dns_input = st.text_input("Player Name", placeholder="Jake Knapp or -Jake Knapp")
            if st.button("🔄 Update DNS List", width="stretch") and dns_input:
                dns_list = [p.strip() for p in current_dns.split(",")] if current_dns else []
                action_name = dns_input.strip()
                if action_name.startswith("-"):
                    dns_list = [p for p in dns_list if p.lower() != action_name[1:].strip().lower()]
                    st.success(f"Removed {action_name[1:]}")
                else:
                    if not any(p.lower() == action_name.lower() for p in dns_list): dns_list.append(action_name); st.success(f"Added {action_name}")
                if save_dns_to_db(t_id, ", ".join(dns_list)): st.rerun()
                else: st.error("Failed to save.")
            settings[f"dns_{t_id}"] = current_dns
            
            st.markdown("---")
            lb_check, _, _, _, _ = fetch_smart_leaderboard(t_id)
            
            if check_api_par_warning(lb_check) and current_par_override == 0:
                st.error("🚨 **API Math Error Detected!**\nThe provider's overall scores do not match their individual round scores. Please enter the correct **Manual Par** below.")
            
            st.caption(f"🔍 Calc. Course Par (R1): **{get_calculated_par(lb_check)}**")
            
            new_par = st.number_input("Manual Par Override (e.g. 70, 71, 72)", value=current_par_override)
            if new_par != current_par_override:
                if save_par_to_db(t_id, new_par):
                    st.success(f"✅ Par permanently saved as {new_par}!")
                    st.rerun()
                else:
                    st.error("🚨 Failed to save Par.")

            st.markdown("---")
            st.caption("🕒 **Tee Time Visibility**")
            new_hide_tt = st.checkbox("🚫 Hide API Tee Times (Force 'Waiting...')", value=current_hide_tt)
            if new_hide_tt != current_hide_tt:
                if save_hide_tt_to_db(t_id, new_hide_tt):
                    st.success("✅ Tee Time visibility updated!")
                    st.rerun()
                else:
                    st.error("🚨 Failed to save setting.")
            
            st.markdown("---")
            st.write("📜 **Tournament Rules**")
            raw_admin_rules = get_config(t_id, 'rules', DEFAULT_RULES)
            new_rules = st.text_area("Enter Rules for Public Display", value=(raw_admin_rules if raw_admin_rules and raw_admin_rules.strip() else DEFAULT_RULES), height=250)
            if new_rules != raw_admin_rules:
                update_config(t_id, 'rules', new_rules)
            
            st.markdown("---")
            st.write("⏳ **Entries Close Time**")
            default_close_dt = datetime.datetime.strptime(str(t_start), "%Y-%m-%d %H:%M:%S").replace(hour=5, minute=0, second=0) if t_start else datetime.datetime.now()
            db_close_admin = fetch_close_time_from_db(t_id)
            current_close_dt = datetime.datetime.strptime(db_close_admin, "%Y-%m-%d %H:%M:%S") if db_close_admin else default_close_dt
            
            c_d2, c_t2 = st.columns(2)
            with c_d2: close_d = st.date_input("Close Date", value=current_close_dt.date())
            with c_t2: close_t = st.time_input("Close Time", value=current_close_dt.time())
            
            st.write("🔒 **Public Reveal Time**")
            default_reveal_dt = datetime.datetime.strptime(str(t_start), "%Y-%m-%d %H:%M:%S").replace(hour=5, minute=0, second=0) if t_start else datetime.datetime.now()
            db_reveal_admin = fetch_reveal_time_from_db(t_id)
            current_reveal_dt = datetime.datetime.strptime(db_reveal_admin, "%Y-%m-%d %H:%M:%S") if db_reveal_admin else default_reveal_dt
            
            c_d, c_t = st.columns(2)
            with c_d: reveal_d = st.date_input("Reveal Date", value=current_reveal_dt.date())
            with c_t: reveal_t = st.time_input("Reveal Time", value=current_reveal_dt.time())
            
            selected_close_dt = datetime.datetime.combine(close_d, close_t)
            try:
                uk_tz = pytz.timezone('Europe/London')
                et_tz = pytz.timezone('America/New_York')
                preview_uk = uk_tz.localize(selected_close_dt)
                preview_et = preview_uk.astimezone(et_tz)
                
                is_midnight_et = (preview_et.hour == 0 and preview_et.minute == 0)
                
                if is_midnight_et:
                    alert_html = f"""
                    <div style="background-color: rgba(46, 204, 113, 0.15); padding: 12px; border-radius: 6px; border-left: 4px solid #2ecc71; margin-top: 15px; margin-bottom: 15px;">
                        <div style="color: #27ae60; font-weight: bold; margin-bottom: 5px;">✅ Standard Deadline Confirmed</div>
                        <div style="font-size: 0.95em;">🇬🇧 UK Time: <b>{preview_uk.strftime('%I:%M %p')}</b> on {preview_uk.strftime('%b %d')}</div>
                        <div style="font-size: 0.95em;">🇺🇸 US ET: <b>{preview_et.strftime('%I:%M %p')}</b> on {preview_et.strftime('%b %d')}</div>
                    </div>
                    """
                else:
                    alert_html = f"""
                    <div style="background-color: rgba(230, 126, 34, 0.15); padding: 12px; border-radius: 6px; border-left: 4px solid #e67e22; margin-top: 15px; margin-bottom: 15px;">
                        <div style="color: #d35400; font-weight: bold; margin-bottom: 5px;">⚠️ Warning: Non-Standard Deadline</div>
                        <div style="font-size: 0.9em; margin-bottom: 8px;">Entries are <b>not</b> set to close at Midnight ET. Are you sure you want to use this time?</div>
                        <div style="font-size: 0.95em;">🇬🇧 UK Time: <b>{preview_uk.strftime('%I:%M %p')}</b> on {preview_uk.strftime('%b %d')}</div>
                        <div style="font-size: 0.95em;">🇺🇸 US ET: <b>{preview_et.strftime('%I:%M %p')}</b> on {preview_et.strftime('%b %d')}</div>
                    </div>
                    """
                st.markdown(alert_html, unsafe_allow_html=True)
            except Exception: pass
            
            if st.button("💾 Save Tournament Dates", type="primary", use_container_width=True):
                close_str = datetime.datetime.combine(close_d, close_t).strftime("%Y-%m-%d %H:%M:%S")
                reveal_str = datetime.datetime.combine(reveal_d, reveal_t).strftime("%Y-%m-%d %H:%M:%S")
                
                with st.spinner("Saving to database..."):
                    update_config(t_id, 'close_time', close_str)
                    update_config(t_id, 'reveal_time', reveal_str)
                    fetch_close_time_from_db.clear()
                    fetch_reveal_time_from_db.clear()
                    
                st.success("✅ Dates saved securely! They will not change unless you click this again.")
            
            p_url = f"{BASE_URL}?view=public&tourney_id={t_id}"
                
            st.markdown("---")
            st.write("🔗 **Public Sharing Link**")
            st.code(p_url)
            
            if st.button("👀 Preview Public Leaderboard", type="primary", use_container_width=True):
                st.query_params.clear()
                st.query_params["view"] = "public"
                st.query_params["tourney_id"] = str(t_id)
                st.rerun()
                
            saved_short_url = fetch_short_url_from_db(t_id)
            
            if saved_short_url:
                st.success("✨ Anonymous Short Link Ready!")
                st.code(saved_short_url)
                if st.button("🗑️ Reset Short Link"): 
                    save_short_url_to_db(t_id, "")
                    st.rerun()
            else:
                if st.button("✨ Generate Anonymous Short Link"):
                    with st.spinner("Generating short link..."):
                        short_link = generate_short_link(p_url)
                        if short_link: 
                            save_short_url_to_db(t_id, short_link)
                            st.success("Created!")
                            st.rerun()
                        else: 
                            st.error("Failed to generate link.")
                            
            if st.button("🔄 Clear Cache"): get_api_cache().clear(); st.cache_data.clear(); get_entries_df.clear(); st.rerun()

    if t_id:
        admin_logo = fetch_logo_from_db(t_id)
        t_name_display = t_key.split('(')[0].strip() if t_key else "Tournament"
        
        # 💥 Updates the browser tab for the Admin view (Safari-proof)
        st.html(f"""
        <script>
            setTimeout(function() {{
                window.parent.document.title = "{t_name_display.replace('"', '')} Admin";
            }}, 100);
        </script>
        """)
        
        logo_rendered = False
        
        if admin_logo:
            try:
                st.markdown(f"<div style='text-align: center; padding-bottom: 10px;'><img src='{html.escape(admin_logo)}' style='max-width: 250px; max-height: 150px; width: 100%; object-fit: contain;'></div>", unsafe_allow_html=True)
                logo_rendered = True
            except Exception: pass 

        if not logo_rendered:
            st.markdown(f"<h1 style='text-align: center; padding-bottom: 10px;'>🏆 {html.escape(t_name_display)}</h1>", unsafe_allow_html=True)

        t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11 = st.tabs(["🏆 Leaderboard", "📝 Entries", "💵 Financials", "⛳ Official PGA Scores", "💾 Export", "📡 API Status", "💻 Raw JSON", "📜 Rules", "🏌️ Field", "📈 Analytics", "🔀 Aliases"])
        
        lb_data, next_f, last_f, full_data, data_source = fetch_smart_leaderboard(t_id)
        
        raw_results = full_data.get('results') or {}
        tourney_data = raw_results.get('tournament') or {}
        live_details = tourney_data.get('live_details') or {}
        t_status = str(live_details.get('status', '')).lower()
        is_tournament_started = t_status not in ['pre', '']
        
        top20_cached_admin = get_top_20_players()
        
        if not is_tournament_started:
            formatted_field_admin = get_formatted_field(t_id, top20_cached_admin)
        else:
            backup_str = fetch_field_backup_from_db(t_id)
            formatted_field_admin = [x.strip() for x in backup_str.split(',')] if backup_str else []
            
        valid_p = [p.split(" (Top 20")[0] for p in formatted_field_admin]
        
        with t1: 
            if "Memory" in data_source: st.info(f"**Data Source:** {data_source}")
            elif "Live" in data_source: st.success(f"**Data Source:** {data_source}")
            else: st.warning(f"**Data Source:** {data_source}")
            
            admin_lb_tab, pub_lb_tab = st.tabs(["🕵️‍♂️ Admin View (Live Scoring)", "🌍 Public View (Delayed)"])
            with admin_lb_tab:
                calculate_leaderboard(t_id, t_key, t_start, par_override=current_par_override, dns_input=current_dns, valid_players=valid_p, is_admin_view=True, hide_title=True)        
            with pub_lb_tab:
                calculate_leaderboard(t_id, t_key, t_start, par_override=current_par_override, dns_input=current_dns, valid_players=valid_p, is_admin_view=False, hide_title=True)        
        
        admin_df, admin_count = get_clean_entries(t_id, False, valid_p, current_dns.split(","))
        edited_df = pd.DataFrame()
        
        with t2: 
            st.metric("🎟️ Total Entries", admin_count)
            if not admin_df.empty:
                st.caption("Check the 'Paid' box for users and hit Save to securely write to Supabase.")
                top_button_container = st.container()
                disabled_cols = [c for c in admin_df.columns if c != 'Paid']
                
                edited_df = st.data_editor(admin_df, width="stretch", hide_index=True, disabled=disabled_cols, column_config={"Sheet_Row": None, "_Original_Name": None, "Paid": st.column_config.CheckboxColumn("Paid?", default=False)})
                
                with top_button_container:
                    if st.button("💾 Save Payment Statuses", type="primary", width="stretch"):
                        with st.spinner("Writing to Database..."):
                            diffs = edited_df[edited_df['Paid'] != admin_df['Paid']]
                            if not diffs.empty:
                                success = True
                                for idx, row in diffs.iterrows():
                                    if not update_paid_status(t_id, row['Sheet_Row'], row['Paid']): success = False
                                    else: log_event("ADMIN ACTION", f"Marked {'Paid' if row['Paid'] else 'Unpaid'} for {row.get('_Original_Name', 'Unknown')}")
                                if success: st.success("✅ Payment statuses successfully saved to DB! Your financials are locked in.")
                                else: st.error("🚨 Encountered an error updating some rows.")
                            else: st.info("No changes to save.")
                        
                st.markdown("---")
                st.markdown("### 🛠️ Quick Edits (Payment & Picks)")
                name_col = next((c for c in admin_df.columns if c not in ['Sheet_Row', 'Email', 'Payment Method', 'Paid', 'Tie', 'Picks', 'Field Status', 'SheetWarnings', 'Tie Breaker'] and 'pick' not in c.lower()), admin_df.columns[2])
                team_opts = [""] + [f"{r[name_col]} (Current: {r.get('Payment Method', 'Unknown')})" for i, r in admin_df.iterrows()]
                
                target_team_str = st.selectbox("Select Team to Update:", team_opts)
                
                if target_team_str:
                    target_name = target_team_str.split(" (Current:")[0]
                    target_row = admin_df[admin_df[name_col] == target_name].iloc[0]
                    st.markdown(f"**Editing:** `{target_name}`")
                    
                    c1, c2 = st.columns([2, 1])
                    with c1: 
                        admin_clean_opts = [p for p in pay_opts if p != "Select..."]
                        curr_pay = target_row.get('Payment Method')
                        new_pay = st.selectbox("Payment Method:", admin_clean_opts, index=admin_clean_opts.index(curr_pay) if curr_pay in admin_clean_opts else 0)
                    with c2:
                        st.markdown("<br>", unsafe_allow_html=True) 
                        if st.button("💾 Update Method", type="primary", width="stretch"):
                            with st.spinner("Updating DB..."):
                                if update_entry_field(t_id, target_row['Sheet_Row'], "Payment Method", new_pay):
                                    log_event("ADMIN ACTION", f"Changed payment method to '{new_pay}' for {target_name}")
                                    st.toast("✅ Payment updated successfully!")
                                    time.sleep(1); st.rerun() 
                                else: st.error("🚨 Failed to update.")
                                
                    st.markdown("**Edit Players:**")
                    pick_cols_target = [c for c in ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5'] if c in admin_df.columns and pd.notna(target_row[c])]
                    current_picks = [str(target_row[c]).split(" (Top 20")[0].strip() for c in pick_cols_target][:5]
                    current_picks += [""] * (5 - len(current_picks)) 
                    
                    p_cols = st.columns(5)
                    new_ep = []
                    for i in range(5):
                        with p_cols[i]:
                            new_ep.append(st.selectbox(f"Pick {i+1}", [""] + formatted_field_admin, index=get_dropdown_index(current_picks[i], formatted_field_admin), key=f"adm_p{i}"))
                    
                    if st.button("💾 Update Picks", type="primary"):
                        clean_new_picks = [p for p in new_ep if p != ""]
                        if len(clean_new_picks) != 5: st.error("🚨 Must select exactly 5 players.")
                        elif len(set(clean_new_picks)) != 5: st.error("🚨 Duplicate players detected!")
                        elif sum(1 for p in clean_new_picks if "(Top 20" in p) > 2: st.error("🚨 Too many Top 20 players!")
                        else:
                            with st.spinner("Writing new picks to Database..."):
                                success = True
                                actual_pick_headers = ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5']
                                if len(actual_pick_headers) == 5:
                                    for i in range(5):
                                        if not update_entry_field(t_id, target_row['Sheet_Row'], actual_pick_headers[i], clean_new_picks[i]): success = False
                                    if success:
                                        log_event("ADMIN ACTION", f"Manually swapped picks for {target_name}")
                                        st.success("✅ Picks updated successfully!")
                                        time.sleep(1); st.rerun()
                                    else: st.error("🚨 Failed to update some picks.")
                                else: st.error("🚨 Could not identify the exactly 5 pick columns.")

        with t3:
            st.header("💵 Financial Reconciliation & Payouts")
            fee = st.number_input("Standard Entry Fee ($)", value=30, step=5)
            st.divider(); st.subheader("📊 Collection Status by Payment Method")
            
            current_fin_df = edited_df if not edited_df.empty else admin_df
            
            if not current_fin_df.empty and 'Payment Method' in current_fin_df.columns:
                df_calc = current_fin_df.copy()
                df_calc['Fee_Mult'] = df_calc['Payment Method'].apply(lambda x: 0 if str(x).startswith('Other') else fee)
                df_calc['Is_Paid'] = df_calc['Paid'].apply(lambda x: 1 if str(x).lower() in ['true', 'yes', '1', 'y'] or x is True else 0)
                
                df_fin = df_calc.groupby('Payment Method').agg(Total_Entries=('Payment Method', 'size'), Paid_Entries=('Is_Paid', 'sum'), Fee_Per_Entry=('Fee_Mult', 'max')).reset_index()
                
                df_fin['Expected ($)'] = df_fin['Total_Entries'] * df_fin['Fee_Per_Entry']
                df_fin['Collected ($)'] = df_fin['Paid_Entries'] * df_fin['Fee_Per_Entry']
                df_fin['Outstanding ($)'] = df_fin['Expected ($)'] - df_fin['Collected ($)']
                
                display_fin = df_fin[['Payment Method', 'Total_Entries', 'Paid_Entries', 'Expected ($)', 'Collected ($)', 'Outstanding ($)']].rename(columns={'Total_Entries': 'Total Entries', 'Paid_Entries': 'Paid Entries'})
                st.dataframe(display_fin, width="stretch", hide_index=True)
                
                total_expected, total_collected, total_outstanding = display_fin['Expected ($)'].sum(), display_fin['Collected ($)'].sum(), display_fin['Outstanding ($)'].sum()
                
                st.markdown("### 🏦 Bank Account Reconciliation (Actuals)")
                st.caption("Compare your real bank balances against the 'Collected' amounts tracked via the checkboxes above.")
                
                z_coll = df_fin[df_fin['Payment Method'].str.contains('Zelle', case=False, na=False)]['Collected ($)'].sum()
                p_coll = df_fin[df_fin['Payment Method'].str.contains('PayPal', case=False, na=False)]['Collected ($)'].sum()
                r_coll = df_fin[df_fin['Payment Method'].str.contains('Revolut', case=False, na=False)]['Collected ($)'].sum()
                
                saved_bals_str = fetch_fin_balances_from_db(t_id)
                try: saved_bals = json.loads(saved_bals_str)
                except Exception: saved_bals = {}

                col_z, col_p, col_r = st.columns(3)
                with col_z:
                    st.markdown("**Zelle / Bank**"); st.caption(f"App Collected: **${z_coll}**")
                    z_actual = st.number_input("Actual Zelle Balance", value=float(saved_bals.get("z_act", 0.0)), step=10.0)
                    z_personal = st.number_input("Subtract Personal Money", value=float(saved_bals.get("z_per", 0.0)), step=10.0, key="z_pers")
                    z_net = z_actual - z_personal; z_diff = z_net - z_coll
                    st.metric("Net Zelle Revenue", f"${z_net:,.2f}", delta=f"-${abs(z_diff):,.2f} vs App" if z_diff < 0 else f"+${z_diff:,.2f} vs App" if z_diff > 0 else "Matches App ✅", delta_color="inverse" if z_diff > 0 else "normal" if z_diff < 0 else "off")
                with col_p:
                    st.markdown("**PayPal**"); st.caption(f"App Collected: **${p_coll}**")
                    p_actual = st.number_input("Actual PayPal Balance", value=float(saved_bals.get("p_act", 0.0)), step=10.0)
                    p_personal = st.number_input("Subtract Personal Money", value=float(saved_bals.get("p_per", 0.0)), step=10.0, key="p_pers")
                    p_net = p_actual - p_personal; p_diff = p_net - p_coll
                    st.metric("Net PayPal Revenue", f"${p_net:,.2f}", delta=f"-${abs(p_diff):,.2f} vs App" if p_diff < 0 else f"+${p_diff:,.2f} vs App" if p_diff > 0 else "Matches App ✅", delta_color="inverse" if p_diff > 0 else "normal" if p_diff < 0 else "off")
                with col_r:
                    st.markdown("**Revolut / Cash / Other**"); st.caption(f"App Collected: **${r_coll}**")
                    r_actual = st.number_input("Actual Revolut/Cash", value=float(saved_bals.get("r_act", 0.0)), step=10.0)
                    r_personal = st.number_input("Subtract Personal Money", value=float(saved_bals.get("r_per", 0.0)), step=10.0, key="r_pers")
                    r_net = r_actual - r_personal; r_diff = r_net - r_coll
                    st.metric("Net Revolut/Cash", f"${r_net:,.2f}", delta=f"-${abs(r_diff):,.2f} vs App" if r_diff < 0 else f"+${r_diff:,.2f} vs App" if r_diff > 0 else "Matches App ✅", delta_color="inverse" if r_diff > 0 else "normal" if r_diff < 0 else "off")
                
                total_actual_bank = z_net + p_net + r_net; diff_bank_vs_app = total_actual_bank - total_collected
                
                if st.button("💾 Save Bank Balances to DB", type="secondary", width="stretch"):
                    new_bals = json.dumps({"z_act": z_actual, "z_per": z_personal, "p_act": p_actual, "p_per": p_personal, "r_act": r_actual, "r_per": r_personal})
                    with st.spinner("Writing balances to DB..."):
                        if save_fin_balances_to_db(t_id, new_bals): 
                            log_event("ADMIN ACTION", "Saved Bank Account Reconciliation Balances")
                            st.success("✅ Balances securely saved! They will permanently survive a refresh.")
                        else: st.error("🚨 Failed to save balances.")

                st.markdown("---"); st.markdown("### 🧮 Overall Reconciliation")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Expected Pot", f"${total_expected:,.2f}"); c2.metric("Outstanding (Unpaid)", f"${total_outstanding:,.2f}")
                c3.metric("Total Collected (App)", f"${total_collected:,.2f}")
                c4.metric("Actual Bank Total", f"${total_actual_bank:,.2f}", delta=f"-${abs(diff_bank_vs_app):,.2f} Bank vs App" if diff_bank_vs_app < 0 else f"+${diff_bank_vs_app:,.2f} Bank vs App" if diff_bank_vs_app > 0 else "Perfect Match ✅", delta_color="inverse" if diff_bank_vs_app > 0 else "normal" if diff_bank_vs_app < 0 else "off")
                
                if total_expected > 0:
                    pct = min(total_collected / total_expected, 1.0)
                    st.progress(pct, text=f"💰 Collection Progress: ${total_collected:,.2f} / ${total_expected:,.2f} ({pct*100:.1f}%)")
                    st.markdown("<br>", unsafe_allow_html=True)
                
                if diff_bank_vs_app != 0: st.error(f"🚨 **Discrepancy Detected:** Your actual bank balances are off by **${abs(diff_bank_vs_app):,.2f}** compared to the App. Please review payments!")
                else: st.success("✅ **Reconciled:** Your actual bank balances perfectly match the tracked payments!")
                
                st.divider(); st.subheader("🏆 Payout Calculator"); st.caption("Calculations are based on your **Total Expected Pot**.")
                pct_1, pct_2, pct_3 = st.columns(3)
                with pct_1: p1_pct = st.number_input("1st Place %", value=60, step=5)
                with pct_2: p2_pct = st.number_input("2nd Place %", value=30, step=5)
                with pct_3: p3_pct = st.number_input("3rd Place %", value=10, step=5)
                
                st.markdown("**Final Payout Overrides (These are shown to the public):**")
                fin_1, fin_2, fin_3 = st.columns(3)
                with fin_1: final_1 = st.number_input("1st Place Amount ($)", value=int((p1_pct / 100) * total_expected), step=5)
                with fin_2: final_2 = st.number_input("2nd Place Amount ($)", value=int((p2_pct / 100) * total_expected), step=5)
                with fin_3: final_3 = st.number_input("3rd Place Amount ($)", value=int((p3_pct / 100) * total_expected), step=5)
                
                total_pot = final_1 + final_2 + final_3
                expected_diff = total_expected - total_pot
                bank_diff = total_actual_bank - total_pot
                
                if expected_diff == 0 and bank_diff >= 0:
                    st.success(f"✅ **Perfect Match!** Total Prize Pot (\\${total_pot:,.2f}) perfectly matches the Expected Revenue, and your Bank has enough to cover it."); allow_save = True
                elif expected_diff == 0 and bank_diff < 0:
                    st.warning(f"⚠️ **Pot Matches Expected, but Bank is Short:** Your Prize Pot (\\${total_pot:,.2f}) matches the Total Expected Pot, BUT you are short \\${abs(bank_diff):,.2f} in actual bank collections! You can publish, but chase down those payments."); allow_save = True
                elif expected_diff > 0:
                    st.error(f"🚨 **Under Budget:** Your Prize Pot (\\${total_pot:,.2f}) is **\\${expected_diff:,.2f} LESS** than the Total Expected Pot (\\${total_expected:,.2f})."); allow_save = False
                else:
                    st.error(f"🚨 **Over Budget:** Your Prize Pot (\\${total_pot:,.2f}) is **\\${abs(expected_diff):,.2f} MORE** than the Total Expected Pot (\\${total_expected:,.2f})."); allow_save = False
                
                publish_payouts = st.checkbox("✅ Check here to confirm and reveal Payouts on the Public Tab", value=settings.get(f"payout_confirmed_{t_id}", False), disabled=not allow_save)
                
                if st.button("💾 Save Payout Configuration", disabled=not allow_save, type="primary"):
                    settings[f"payout_1_{t_id}"], settings[f"payout_2_{t_id}"], settings[f"payout_3_{t_id}"], settings[f"payout_pot_{t_id}"], settings[f"payout_confirmed_{t_id}"] = final_1, final_2, final_3, total_pot, publish_payouts
                    
                    payout_json = json.dumps({"payout_confirmed": publish_payouts, "p1": final_1, "p2": final_2, "p3": final_3, "pot": total_pot})
                    with st.spinner("Locking payouts to database..."):
                        if save_payout_config_to_db(t_id, payout_json):
                            log_event("ADMIN ACTION", f"Updated Payouts (Total Pot: ${total_pot:,.2f})")
                            st.success("Payout configurations safely locked and updated!")
                        else:
                            st.error("🚨 Failed to save payouts.")
            else: st.warning("No entries or payment data found yet.")

        with t4: 
            if "Memory" in data_source: st.info(f"**Data Source:** {data_source}")
            elif "Live" in data_source: st.success(f"**Data Source:** {data_source}")
            else: st.warning(f"**Data Source:** {data_source}")
            
            admin_pga_tab, pub_pga_tab = st.tabs(["🕵️‍♂️ Admin View (Live Scoring)", "🌍 Public View (Delayed)"])
            with admin_pga_tab:
                render_pga_leaderboard(lb_data, full_data, t_id, "admin", par_override=current_par_override, hide_tt=current_hide_tt)
            with pub_pga_tab:
                render_pga_leaderboard(lb_data, full_data, t_id, "pub", par_override=current_par_override, hide_tt=current_hide_tt)
        
        with t5:
            st.subheader("💾 Export Data")
            st.caption("Download the high-level tournament standings (Rank, Name, Total Score, Tie-Breaker).")
            df_export = calculate_leaderboard(t_id, t_key, t_start, par_override=current_par_override, dns_input=current_dns, valid_players=valid_p, is_admin_download=True, is_admin_view=True)            
            if df_export is not None and not df_export.empty:
                export_final = pd.DataFrame()
                export_final['Rank'] = df_export['DRank']
                export_final['Player Name'] = df_export['Participant']
                export_final['Total Score'] = df_export.apply(lambda x: "CUT" if x['Elim'] else x['Total'], axis=1)
                export_final['Tie Breaker'] = df_export['Tie']
                st.download_button("⬇️ Download Top-Level Results (CSV)", export_final.to_csv(index=False).encode('utf-8'), f"{t_key.replace(' ', '_').split('(')[0]}_Leaderboard.csv", "text/csv")
            else: st.warning("No data to export.")
                
        with t6:
            st.subheader("📡 API Monitor")
            cache = get_api_cache()
            lb_key = f"lb_{t_id}"
            
            c1, c2 = st.columns(2)
            with c1: st.metric("Last Fetch", cache[lb_key]['last_fetch'].strftime("%H:%M:%S") if lb_key in cache else "Never")
            with c2: st.metric("Next Allowed", cache[lb_key]['next_fetch_allowed'].strftime("%H:%M:%S") if lb_key in cache else "Now")
            
            st.info(f"**Current Polling Strategy:** {cache[lb_key].get('mode', 'Unknown') if lb_key in cache else 'Unknown'}")
            
            st.divider()
            st.write("📜 **Session Activity Log**")
            
            if st.button("📥 Load Remote Logs"):
                try:
                    res = get_supabase().table('golf_app_logs').select('*').eq('tournament_id', str(t_id)).order('created_at', desc=True).limit(50).execute()
                    if res.data: st.dataframe(pd.DataFrame(res.data), width="stretch", hide_index=True)
                    else: st.info("Log table is empty.")
                except Exception as e: st.error(f"Could not load logs: {e}")
                    
        with t7:
            st.subheader("💻 Raw API JSON")
            st.caption("Inspect the exact payload returned by the API for this tournament.")
            if full_data: st.json(full_data)
            else: st.info("No leaderboard data available from the API yet.")
            
        with t8:
            raw_admin_rules = get_config(t_id, 'rules', DEFAULT_RULES)
            st.markdown(raw_admin_rules if raw_admin_rules and raw_admin_rules.strip() else DEFAULT_RULES)
            
        with t9:
            st.subheader("🏌️ Tournament Field & Top 20")
            st.caption("This is the active field returned by the API. It updates automatically.")
            
            st.markdown("---")
            
            if is_tournament_started:
                st.info("⛳ The tournament has started! The entry list is locked.")
                raw_field = [p.split(" (Top 20")[0] for p in formatted_field_admin]
                
                if st.button("🛠️ Force Rebuild Frozen Field (Fix Top 20 Flags)", type="primary"):
                    with st.spinner("Rebuilding Top 20 database..."): 
                        get_formatted_field(t_id, get_top_20_players())
                        log_event("ADMIN ACTION", "Forced rebuild of frozen field and Top 20 flags")
                    st.success("✅ Database rebuilt and permanently frozen! Please click the 'Clear Cache' button in the sidebar.")
            else:
                st.subheader("🚨 Smart Field Alerts")
                st.caption("Detect if players have been added or removed since your last check.")
                raw_field = get_raw_entry_list(t_id)
                
                baseline_str = fetch_alerted_field_from_db(t_id)
                baseline_field = [p.strip() for p in baseline_str.split(",")] if baseline_str else []
                
                if not baseline_str and raw_field:
                    if st.button("Set Initial Baseline", type="primary"):
                        save_alerted_field_to_db(t_id, ",".join(raw_field))
                        st.rerun()
                elif raw_field and baseline_field:
                    added_players = list(set(raw_field) - set(baseline_field))
                    removed_players = list(set(baseline_field) - set(raw_field))
                    
                    if not added_players and not removed_players: st.success("✅ The field is currently stable. No changes detected.")
                    else:
                        if added_players: st.write(f"**➕ Added:** {', '.join(added_players)}")
                        if removed_players: st.write(f"**❌ Removed:** {', '.join(removed_players)}")
                        
                        if st.button("🚀 Send Alert Emails", type="primary"):
                            with st.spinner("Sending emails..."):
                                current_close_dt = datetime.datetime.strptime(settings.get(f"close_time_{t_id}"), "%Y-%m-%d %H:%M:%S") if settings.get(f"close_time_{t_id}") else datetime.datetime.now()
                                try:
                                    uk_tz = pytz.timezone('Europe/London'); et_tz = pytz.timezone('America/New_York')
                                    ct_uk = uk_tz.localize(current_close_dt); ct_et = ct_uk.astimezone(et_tz)
                                    close_time_str_admin = f"{ct_uk.strftime('%a, %b %d at %I:%M %p')} UK / {ct_et.strftime('%a, %b %d at %I:%M %p')} ET"
                                except Exception: close_time_str_admin = current_close_dt.strftime('%a, %b %d at %I:%M %p')
                                
                                emails_sent = 0
                                alerted_emails = set()
                                
                                if not admin_df.empty:
                                    name_col = next((c for c in admin_df.columns if c not in ['Sheet_Row', 'Email', 'Payment Method', 'Paid', 'Field Status', '_Original_Name'] and 'pick' not in c.lower()), admin_df.columns[2])
                                    for _, row in admin_df.iterrows():
                                        user_email = str(row.get('Email', '')).strip().lower()
                                        user_name = str(row.get('_Original_Name', row.get(name_col, 'Entrant')))
                                        if not user_email or '@' not in user_email: continue
                                        if user_email in alerted_emails: continue 
                                        
                                        user_picks = [str(row[c]).split(" (Top 20")[0].strip() for c in ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5'] if c in admin_df.columns and pd.notna(row[c])]
                                        affected_removals = [p for p in removed_players if p in user_picks]
                                        
                                        if affected_removals or added_players:
                                            if send_field_change_email(user_email, user_name, affected_removals, added_players, t_key.split('(')[0], t_id, close_time_str_admin, admin_logo):
                                                emails_sent += 1
                                                alerted_emails.add(user_email)
                                                time.sleep(1.5)
                                                
                                save_alerted_field_to_db(t_id, ",".join(raw_field))
                                st.success(f"✅ Sent {emails_sent} alerts!"); st.rerun()

            st.markdown("---")
            if formatted_field_admin:
                display_df = pd.DataFrame([{"Player": p.split(" (Top 20")[0], "Top 20 Restriction?": "✅ Yes" if "(Top 20" in p else ""} for p in formatted_field_admin])
                st.dataframe(display_df, width="stretch", hide_index=True)
            else: 
                st.info("Field has not been populated by the API yet.")
        
        with t10:
            st.subheader("📈 Live Traffic Analytics")
            st.caption("Live visitor tracking powered by Umami.")
            umami_share_url = "https://cloud.umami.is/share/eSEE741Q3wuxQygs"
            st.html(f'<iframe src="{umami_share_url}" width="100%" height="800px" style="border:none;" scrolling="yes"></iframe>')

        with t11:
            st.subheader("🔀 Name Alias Manager")
            st.caption("Link a player's drafted name to their official API leaderboard name to fix 'Not in Field' errors.")
            
            current_aliases = fetch_aliases_from_db(t_id)
            alias_list = [a.strip() for a in current_aliases.split(",") if a.strip()]
            
            st.markdown("### Current Aliases")
            if alias_list:
                for idx, a in enumerate(alias_list):
                    if ':' in a:
                        w, c = a.split(':')
                        colA, colB = st.columns([4, 1])
                        with colA: st.markdown(f"**{w.strip().title()}** ➡️ mapped to API name **{c.strip().title()}**")
                        with colB:
                            if st.button("❌ Remove", key=f"rm_alias_{idx}"):
                                alias_list.pop(idx)
                                save_aliases_to_db(t_id, ",".join(alias_list))
                                st.rerun()
            else:
                st.info("No aliases currently set.")
                
            st.markdown("---")
            st.markdown("### Add New Alias")
            c_wrong, c_right = st.columns(2)
            with c_wrong: new_wrong = st.text_input("Drafted Name (e.g. Nico Echavarria)", key="new_w")
            with c_right: new_right = st.text_input("API Name (e.g. Nicolas Echavarria)", key="new_r")
            
            if st.button("➕ Add Alias", type="primary"):
                if new_wrong and new_right:
                    new_pair = f"{new_wrong.strip()}:{new_right.strip()}"
                    alias_list.append(new_pair)
                    with st.spinner("Saving to DB..."):
                        if save_aliases_to_db(t_id, ",".join(alias_list)):
                            st.success("Alias added successfully!")
                            st.rerun()
                        else: st.error("🚨 Failed to save alias.")
                else:
                    st.error("🚨 Please fill out both names.")
