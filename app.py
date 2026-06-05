import streamlit as st, pandas as pd, requests, re, datetime, smtplib, pytz, json, html, urllib.parse, secrets
import altair as alt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from functools import lru_cache
from supabase import create_client, Client

# 🚨 GLOBAL VARIABLES 🚨
if "payment_options" in st.secrets:
    custom_pays = [p.strip() for p in st.secrets["payment_options"].split(",") if p.strip()]
    pay_opts = ["Select..."] + custom_pays + ["Other"]
else:
    pay_opts = ["Select...", "PayPal to ejmcburney@gmail.com", "Zelle to ejmcburney@gmail.com", "Revolut to 083 032 8196", "Other"]

st.set_page_config(page_title="Golf Sweepstakes", page_icon="⛳", layout="wide")

# 🚨 UPTIMEROBOT INTERCEPTOR (Protects API Quotas) 🚨
if st.query_params.get("view") == "ping":
    st.write("Server is awake and ready! 🟢")
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

if "api_log" not in st.session_state: st.session_state["api_log"] = []
if "editing_row" not in st.session_state: st.session_state["editing_row"] = None

is_public = st.query_params.get("view") == "public"
target_tourney_id = st.query_params.get("tourney_id")

# Intelligently locate the password whether it's at the root or nested
if "admin_password" in st.secrets:
    ADMIN_PASSWORD = st.secrets["admin_password"]
elif "supabase" in st.secrets and "admin_password" in st.secrets["supabase"]:
    ADMIN_PASSWORD = st.secrets["supabase"]["admin_password"]
else:
    if not is_public:
        st.error("🚨 Configuration Error: `admin_password` is missing. (Check that it isn't accidentally nested under a [section] in your secrets.toml!)")
        st.stop()
    ADMIN_PASSWORD = "" # Prevents NameError in public views

BASE_URL = "https://majors-test.streamlit.app/"
DEFAULT_RULES = """### 🏆 Tournament Rules\n\n⛳ **The Team:** Pick **5** players, max **2** from Top 20.\n\n🏌️‍♂️ **Scoring:** The **best 4 scores** each day count.\n\n✂️ **The Cut:** If **2 or more** picks miss the cut, team is out.\n\n⚖️ **Tie Break:** Predict Winner's total score to par.\n\n⏳ **Deadline:** Midnight before the tournament.\n\n💰 **Entry Fee:** **$30**."""

@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])

@st.cache_resource
def get_api_cache(): return {}

@st.cache_resource
def get_settings_cache(): return {}

def safe_url(url):
    if not url: return ""
    u = str(url).strip()
    if not u.lower().startswith(("http://", "https://")):
        return ""
    return html.escape(u)

def normalize_pga_status(stt):
    s = str(stt).strip().lower()
    if s in ['withdrawn', 'wd']: return 'wd'
    if s in ['disqualified', 'dq']: return 'dq'
    if s in ['missed cut', 'cut', 'mc']: return 'cut'
    if s in ['complete', 'completed']: return 'completed'
    if s in ['active', 'inprogress']: return 'active'
    if s in ['suspended']: return 'suspended'
    if s in ['endofday']: return 'endofday'
    if s in ['not started', 'notstarted', 'pre', '']: return 'pre'
    return s

def get_safe_api_results(api_response):
    if not isinstance(api_response, dict): return {}
    return api_response.get('results', {})

@lru_cache(maxsize=2048)
def _cached_safe_int_string(s_val):
    if s_val in ["E", "EVEN", "PAR", "-", ""]: return 0
    clean = re.sub(r"[^0-9.-]", "", s_val)
    if not clean or clean == '-': return 0
    try: return int(float(clean))
    except (ValueError, TypeError): return 0

def safe_int(val):
    if pd.isna(val): return 0
    if isinstance(val, int): return val
    if isinstance(val, float): return int(val)
    return _cached_safe_int_string(str(val).upper().strip())

def extract_tie_breaker(v): 
    m = re.search(r'-?\d+', str(v))
    return int(m.group()) if m and not pd.isna(v) else 0

def format_score(s): return "E" if s == 0 else f"+{s}" if s > 0 else str(s)

def get_corrected_past_total(p, current_r, par_override):
    past_total = 0
    for r in p.get('rounds', []):
        rn = safe_int(r.get('round_number', 0))
        if rn < current_r:
            s = safe_int(r.get('strokes', 0))
            if par_override > 0 and s > 50: past_total += (s - par_override)
            else: past_total += safe_int(r.get('total_to_par', 0))
    return past_total

def get_pga_round_score_num(p, r_idx, max_visible_round=4, par_override=0, is_live_scoring_active=False, curr_r=1):
    stt = normalize_pga_status(p.get('status', ''))
    if stt in ['wd', 'dq']: return 999
    target_r = r_idx + 1
    if target_r > max_visible_round: return 999
    hp = safe_int(p.get('holes_played', 0))
    api_total = safe_int(p.get('total_to_par', 0))
    api_past = get_corrected_past_total(p, curr_r, par_override)
    has_started = (hp > 0) or (api_total != api_past)
    if is_live_scoring_active and target_r == curr_r and has_started: return api_total - api_past
    rounds = p.get('rounds', [])
    if r_idx < len(rounds):
        s = safe_int(rounds[r_idx].get('strokes', 0))
        if s == 0: return 999 
        if par_override > 0 and s > 50 and (target_r < curr_r or stt != 'active'): return s - par_override
        return safe_int(rounds[r_idx].get('total_to_par', 0))
    return 999

def format_pga_round_score(p, r_idx, max_visible_round, par_override, is_live_scoring_active, curr_r):
    target_r = r_idx + 1
    score = get_pga_round_score_num(p=p, r_idx=r_idx, max_visible_round=max_visible_round, par_override=par_override, is_live_scoring_active=is_live_scoring_active, curr_r=curr_r)
    if score == 999: return "-"
    txt = "E" if score == 0 else f"+{score}" if score > 0 else str(score)
    stt = normalize_pga_status(p.get('status', ''))
    if is_live_scoring_active and stt == 'active' and target_r == curr_r:
        hp = safe_int(p.get('holes_played', 0))
        if 0 < hp < 18: txt = f"{txt} ({hp})"
    return txt

def get_pga_total_num(p, max_visible_round, par_override, is_live_scoring_active, curr_r):
    stt = normalize_pga_status(p.get('status', ''))
    if stt == 'pre': return 999 
    if stt in ['wd', 'cut', 'dq']: return safe_int(p.get('total_to_par', 0))
    api_total = safe_int(p.get('total_to_par', 0))
    hp = safe_int(p.get('holes_played', 0))
    api_past = get_corrected_past_total(p, curr_r, par_override)
    has_started = (hp > 0) or (api_total != api_past)
    is_playing_now = stt == 'active' and has_started
    if is_live_scoring_active and is_playing_now and max_visible_round == curr_r:
        if par_override > 0:
            past_score = 0
            for i in range(curr_r - 1):
                s = get_pga_round_score_num(p=p, r_idx=i, max_visible_round=curr_r - 1, par_override=par_override, is_live_scoring_active=is_live_scoring_active, curr_r=curr_r)
                if s != 999: past_score += s
            return past_score + (api_total - api_past)
        return api_total
    locked_score = 0
    has_completed_round = False
    for r_idx in range(max_visible_round):
        rs = get_pga_round_score_num(p=p, r_idx=r_idx, max_visible_round=max_visible_round, par_override=par_override, is_live_scoring_active=is_live_scoring_active, curr_r=curr_r)
        if rs != 999:
            locked_score += rs
            has_completed_round = True
    return locked_score if has_completed_round else 999

def format_tee_time(tt, tourney_tz_str, target_tz_str, round_date=None):
    if not tt or ':' not in tt or tt == "00:00": return tt
    if not round_date: return tt 
    try:
        dt_source = pytz.timezone(tourney_tz_str).localize(datetime.datetime.strptime(f"{round_date} {tt}", "%Y-%m-%d %H:%M"))
        return dt_source.astimezone(pytz.timezone(target_tz_str)).strftime('%H:%M')
    except (ValueError, TypeError): return tt

def get_pga_thru(p, curr_r, completed_rounds, is_live_scoring_active, tourney_tz_str, target_tz_str, hide_tt=False):
    stt = normalize_pga_status(p.get('status', ''))
    hp = safe_int(p.get('holes_played', 0))
    if stt in ['wd', 'cut', 'dq']: return stt.upper()
    if stt == 'completed' or hp == 72 or completed_rounds == 4: return 'F'
    if is_live_scoring_active and stt == 'active':
        if hp >= 18: return 'F'
        if hp > 0: return str(hp)
        
    target_r = curr_r if is_live_scoring_active else completed_rounds + 1
    if target_r > 4: return "-"
    
    if hide_tt: 
        return "Waiting..." if stt == 'active' else "-"
    
    for rd in p.get('rounds', []):
        if safe_int(rd.get('round_number', 0)) == target_r:
            tt = rd.get('tee_time_local', '')
            r_date = rd.get('date')
            return format_tee_time(tt, tourney_tz_str, target_tz_str, r_date)
            
    if stt == 'active': return "Waiting for tee times..."
    return "-"

def golf_fmt(x): return "-" if pd.isna(x) or x == 999 else "E" if x == 0 else f"+{int(x)}" if x > 0 else str(int(x))

def get_calculated_par(lb_data):
    if lb_data:
        for p in lb_data:
            for r in p.get('rounds', []):
                if safe_int(r.get('strokes', 0)) > 0: return str(safe_int(r.get('strokes', 0)) - safe_int(r.get('total_to_par', 0)))
    return "Waiting..."

def check_api_par_warning(lb_data):
    if not lb_data: return False
    current_r = max([safe_int(p.get('current_round', 0)) for p in lb_data] + [1])
    for p in lb_data:
        stt = normalize_pga_status(p.get('status', ''))
        if stt == 'completed' or (stt == 'pre' and current_r > 1):
            api_top_ttp = safe_int(p.get('total_to_par', 0))
            sum_rounds_ttp, valid_rounds = 0, 0
            for r in p.get('rounds', []):
                if safe_int(r.get('strokes', 0)) > 0:
                    sum_rounds_ttp += safe_int(r.get('total_to_par', 0))
                    valid_rounds += 1
            if valid_rounds > 0 and api_top_ttp != sum_rounds_ttp: return True
    return False

def get_dropdown_index(clean_name, fmt_list):
    if not clean_name or pd.isna(clean_name): return 0
    sn = str(clean_name).split(" (Top 20")[0].strip().lower()
    for i, item in enumerate(fmt_list):
        if item.split(" (Top 20")[0].strip().lower() == sn: return i + 1
    return 0

# --- STATE INFERENCE & DATA HELPERS ---
def derive_tournament_state(lb_data, live_details, is_r4_live_mode=False, is_admin_view=False):
    """Centralized tournament state and round calculation."""
    t_status = normalize_pga_status(live_details.get('status', ''))
    api_curr_r = safe_int(live_details.get('current_round', 0))
    player_curr_r = max([safe_int(p.get('current_round', 0)) for p in lb_data] + [0])
    current_r = max(api_curr_r, player_curr_r)
    
    active_field = [p for p in lb_data if normalize_pga_status(p.get('status', '')) == 'active']
    not_started_current = [p for p in lb_data if normalize_pga_status(p.get('status', '')) == 'pre' and safe_int(p.get('current_round', 0)) == current_r]
    total_holes_live = sum(safe_int(p.get('holes_played', 0)) for p in lb_data)
    
    is_round_finished_consensus = False
    if t_status in ['completed', 'endofday']:
        is_round_finished_consensus = True
    elif active_field and all(safe_int(p.get('holes_played', 0)) == 18 for p in active_field):
        is_round_finished_consensus = True
    elif t_status == 'active' and not active_field and not not_started_current:
        is_round_finished_consensus = True
    elif t_status == 'active' and total_holes_live == 0 and not active_field and not not_started_current:
        is_round_finished_consensus = True

    active_holes = [safe_int(p.get('holes_played', 0)) for p in active_field]
    last_group_teed_off = False
    last_group_htr = 0
    is_r4_fully_done = False
    
    if not active_holes:
        if any(normalize_pga_status(p.get('status', '')) == 'completed' for p in lb_data):
            last_group_teed_off = True
            last_group_htr = 18
            if current_r == 4: is_r4_fully_done = True
    else:
        min_hp = min(active_holes)
        if min_hp == 0: 
            last_group_teed_off = False
        else:
            last_group_htr = min_hp
            last_group_teed_off = True

    if is_admin_view:
        sweep_max_round = current_r
    else:
        if current_r == 4:
            if is_r4_live_mode or last_group_teed_off: sweep_max_round = 4
            else: sweep_max_round = 3
        elif is_round_finished_consensus: sweep_max_round = current_r
        else: sweep_max_round = current_r - 1
        
    return {
        'current_r': current_r,
        't_status': t_status,
        'is_round_finished_consensus': is_round_finished_consensus,
        'sweep_max_round': sweep_max_round,
        'last_group_teed_off': last_group_teed_off,
        'last_group_htr': last_group_htr,
        'is_r4_fully_done': is_r4_fully_done
    }

# --- CONFIG COMPATIBILITY ---
@st.cache_data(ttl=300, show_spinner=False)
def get_all_configs_cached(t_id):
    try:
        res = get_supabase().table('tournament_configs').select('*').eq('tournament_id', str(t_id)).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        st.session_state.setdefault("api_log", []).append(f"DB Read Error in get_all_configs_cached: {type(e).__name__}")
        return {}

def get_config(t_id, column, default=""):
    cfg = get_all_configs_cached(t_id)
    val = cfg.get(column)
    return val if val is not None and val != "" else default

def update_config(t_id, column, value, t_name=None):
    try:
        update_data = {'tournament_id': str(t_id), column: value}
        if t_name and str(t_name).strip(): 
            update_data['tournament_name'] = str(t_name).strip()
            
        res = get_supabase().table('tournament_configs').upsert(update_data, on_conflict='tournament_id').execute()
        if not res.data: raise ValueError("Upsert returned empty data")
        
        get_all_configs_cached.clear() 
        return True
    except Exception as e:
        st.session_state.setdefault("api_log", []).append(f"DB Write Error in update_config ({column}): {type(e).__name__} - {e}")
        return False

def log_to_sheet(event_type, message):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    st.session_state["api_log"].insert(0, f"[{ts}] {event_type}: {message}")
    try:
        t_id = st.session_state.get('current_t_id', 'General')
        get_supabase().table('app_logs').insert({
            'tournament_id': str(t_id),
            'event_type': event_type,
            'message': message
        }).execute()
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"DB Error in log_to_sheet: {type(e).__name__}")

def save_api_backup_to_sheet(t_id, full_data):
    t_name = ""
    try: t_name = full_data.get('results', {}).get('tournament', {}).get('name', '')
    except (KeyError, TypeError, AttributeError): pass
    return update_config(t_id, 'api_backup', full_data, t_name=t_name)

def fetch_api_backup_from_sheet(t_id):
    data = get_config(t_id, 'api_backup', {})
    return data if data else None

def fetch_close_time_from_db(t_id): return get_config(t_id, 'close_time', "")
def fetch_reveal_time_from_db(t_id): return get_config(t_id, 'reveal_time', "")
def fetch_field_backup_from_sheet(t_id): return get_config(t_id, 'field_backup', "")

def save_field_backup_to_sheet(t_id, field_string):
    return update_config(t_id, 'field_backup', field_string)

def fetch_dns_from_sheet(t_id): return get_config(t_id, 'dns_list', "")
def save_dns_to_sheet(t_id, dns_string): return update_config(t_id, 'dns_list', dns_string)

def fetch_short_url_from_sheet(t_id): return get_config(t_id, 'short_url', "")
def save_short_url_to_sheet(t_id, short_url): return update_config(t_id, 'short_url', short_url)

def fetch_fin_balances_from_sheet(t_id):
    val = get_config(t_id, 'fin_balances', {})
    return json.dumps(val) if isinstance(val, dict) else val

def save_fin_balances_to_sheet(t_id, json_str):
    try:
        val = json.loads(json_str)
        return update_config(t_id, 'fin_balances', val)
    except json.JSONDecodeError: return False

def fetch_alerted_field_from_sheet(t_id): return get_config(t_id, 'alerted_field', "")
def save_alerted_field_to_sheet(t_id, field_string): return update_config(t_id, 'alerted_field', field_string)

def fetch_par_from_sheet(t_id):
    try: return int(get_config(t_id, 'manual_par', 0))
    except (ValueError, TypeError): return 0

def save_par_to_sheet(t_id, par_value): return update_config(t_id, 'manual_par', par_value)

def fetch_logo_from_sheet(t_id): return get_config(t_id, 'logo_url', "")
def save_logo_to_sheet(t_id, logo_url): return update_config(t_id, 'logo_url', logo_url)

def fetch_aliases_from_sheet(t_id): return get_config(t_id, 'name_aliases', "")
def save_aliases_to_sheet(t_id, alias_string): return update_config(t_id, 'name_aliases', alias_string)

def fetch_hide_tt_from_sheet(t_id): return get_config(t_id, 'hide_tee_times', False)
def save_hide_tt_to_sheet(t_id, hide_tt_bool): return update_config(t_id, 'hide_tee_times', hide_tt_bool)

def fetch_payout_config_from_sheet(t_id):
    val = get_config(t_id, 'payout_config', {})
    if isinstance(val, dict): return json.dumps(val)
    if isinstance(val, str) and val.strip(): return val
    return "{}"

def save_payout_config_to_sheet(t_id, json_str):
    try:
        val = json.loads(json_str)
        return update_config(t_id, 'payout_config', val)
    except json.JSONDecodeError: return False

@st.cache_data(ttl=120, show_spinner=False)
def get_raw_sheet_data(t_id):
    if not t_id: return pd.DataFrame()
    try:
        res = get_supabase().table('entries').select('*').eq('tournament_id', str(t_id)).order('id').execute()
        if not res.data: return pd.DataFrame()
        
        df = pd.DataFrame(res.data)
        
        df = df.rename(columns={
            'id': 'Sheet_Row',
            'name': 'Name',
            'email': 'Email',                   
            'payment_method': 'Payment Method', 
            'tie_breaker': 'Tie Breaker',
            'paid': 'Paid'
        })
        
        for i in range(1, 6):
            col_name = f'pick_{i}'
            if col_name in df.columns:
                df[f'Pick {i}'] = df[col_name]
            else:
                df[f'Pick {i}'] = ""
            
        df = df.drop(columns=[f'pick_{i}' for i in range(1, 6)] + ['tournament_id', 'created_at', 'updated_at'], errors='ignore')
        return df.copy()
    except Exception as e:
        st.session_state.setdefault("api_log", []).append(f"DB Read Error in get_raw_sheet_data: {type(e).__name__} - {e}")
        return pd.DataFrame()

def build_html_email_template(subject_title, blocks, safe_name, public_link, logo_url):
    logo_html = f'<div style="text-align: center; margin-bottom: 20px;"><img src="{safe_url(logo_url)}" style="max-height: 85px; max-width: 100%;" alt="{html.escape(subject_title)} Logo"></div>' if logo_url else f'<div style="text-align: center; border-bottom: 2px solid #2ecc71; padding-bottom: 10px; margin-bottom: 20px;"><h2 style="color: #27ae60; margin: 0;">{html.escape(subject_title)}</h2></div>'
    body = f"""
    <html>
      <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #2c3e50; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
        {logo_html}
        <p style="font-size: 16px;">Hi <b>{safe_name}</b>,</p>
        {"".join(blocks)}
        <div style="text-align: center; margin-top: 35px; margin-bottom: 20px;">
            <a href="{safe_url(public_link)}" target="_blank" style="background-color: #34495e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold; font-size: 15px;">Go to Tournament Dashboard</a>
        </div>
      </body>
    </html>
    """
    return body

def send_field_change_email(user_email, name, removed_picks, added_to_field, t_name, t_id, close_time_str, logo_url):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    try:
        safe_name = html.escape(str(name))
        safe_tname = html.escape(str(t_name))
        
        msg = MIMEMultipart()
        msg['From'] = formataddr((safe_tname, sender))
        msg['To'] = user_email
        
        blocks = []
        subject = f"{safe_tname} - ⚠️ Tournament Field Update"
        
        if removed_picks:
            subject = f"{safe_tname} - 🚨 URGENT: Action Required for your Team"
            p_html = "".join([f"<li style='padding: 4px 0;'><b>{html.escape(p)}</b></li>" for p in removed_picks])
            blocks.append(f"""
            <div style="background-color: #fff3e0; border-left: 4px solid #e67e22; padding: 15px 20px; border-radius: 4px; margin: 20px 0;">
                <h3 style="margin-top:0; color: #d35400; font-size: 18px;">🚨 Player(s) Withdrawn</h3>
                <p style="font-size: 15px;">One or more players you selected are no longer in the tournament field:</p>
                <ul style="font-size: 15px; color: #c0392b;">{p_html}</ul>
                <p style="font-size: 15px; font-weight: bold;">Please go to the Tournament Dashboard to update your entry before the deadline: <br><span style="color:#d35400;">{html.escape(str(close_time_str))}</span>.</p>
            </div>
            """)
            
        if added_to_field:
            p_html = "".join([f"<li>{html.escape(p)}</li>" for p in added_to_field])
            blocks.append(f"""
            <div style="background-color: #e8f8f5; border-left: 4px solid #1abc9c; padding: 15px 20px; border-radius: 4px; margin: 20px 0;">
                <h3 style="margin-top:0; color: #16a085; font-size: 18px;">⛳ New Players Added</h3>
                <p style="font-size: 15px;">The following players have just been added to the tournament field. You may edit your team to include them if you wish:</p>
                <ul style="font-size: 15px; color: #2c3e50;">{p_html}</ul>
            </div>
            """)
            
        msg['Subject'] = subject
        body_html = build_html_email_template(t_name, blocks, safe_name, f"{BASE_URL}?view=public&tourney_id={t_id}", logo_url)
        msg.attach(MIMEText(body_html, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        try:
            server.starttls()
            server.login(sender, pwd)
            server.send_message(msg)
        finally:
            server.quit()
        return True
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"Email Error in send_field_change_email: {type(e).__name__} - {e}")
        return False

def generate_short_link(long_url):
    try:
        resp = requests.get(f"https://is.gd/create.php?format=simple&url={urllib.parse.quote(long_url)}", timeout=10)
        if resp.status_code == 200 and "is.gd" in resp.text: return resp.text.strip()
    except requests.RequestException as e: 
        st.session_state.setdefault("api_log", []).append(f"URL Shortener Error in generate_short_link: {type(e).__name__} - {e}")
    return None

def send_confirmation_email(user_email, name, picks, tie_breaker, payment, t_name, t_id, is_edit=False, close_time_str="", logo_url=""):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    try:
        safe_name = html.escape(str(name))
        safe_tname = html.escape(str(t_name))
        safe_tie = html.escape(str(tie_breaker))
        safe_pay = html.escape(str(payment))
        
        msg = MIMEMultipart()
        msg['From'] = formataddr((safe_tname, sender))
        msg['To'] = user_email
        status_word = "UPDATED" if is_edit else "LOCKED IN"
        msg['Subject'] = f"{safe_tname} - ⛳ Your Team is {status_word}"
        
        p_html = "".join([f"<li style='padding: 4px 0;'><b>{html.escape(p)}</b></li>" for p in picks])
        public_link = f"{BASE_URL}?view=public&tourney_id={t_id}"
        
        blocks = []
        blocks.append(f"""
        <p style="font-size: 16px;">Your entry has been securely <b>{status_word.lower()}</b>! Here are your official team picks for the tournament:</p>
        <div style="background-color: #f8f9fa; border-left: 4px solid #2ecc71; padding: 15px 20px; border-radius: 4px; margin: 20px 0;">
            <h3 style="margin-top:0; color: #2c3e50; font-size: 18px;">🏌️ Your Starting 5</h3>
            <ul style="font-size: 16px; list-style-type: none; padding-left: 0;">
              {p_html}
            </ul>
            <hr style="border: 0; border-top: 1px solid #dee2e6; margin: 15px 0;">
            <p style="margin: 5px 0; font-size: 15px;"><b>Tie Breaker Score:</b> <span style="color: #2980b9; font-weight: bold;">{safe_tie}</span></p>
            <p style="margin: 5px 0; font-size: 15px;"><b>Payment Method:</b> {safe_pay}</p>
        </div>
        """)
        
        if close_time_str:
            blocks.append(f"<p style='color: #e67e22; font-size: 14px; background-color: #fff3e0; padding: 10px; border-radius: 4px; border-left: 3px solid #e67e22;'>✏️ <b>Need to make a change?</b> You can edit your team anytime before <b>{html.escape(str(close_time_str))}</b>. Just go to the <a href='{safe_url(public_link)}' target='_blank'>Tournament Dashboard</a>, click the <b>🔍 Check Entry</b> tab, and enter your email address to securely unlock your entry!</p>")
            
        body_html = build_html_email_template(t_name, blocks, safe_name, public_link, logo_url)
        msg.attach(MIMEText(body_html, 'html'))
        msg['Bcc'] = sender 
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        try:
            server.starttls()
            server.login(sender, pwd)
            server.send_message(msg)
        finally:
            server.quit()
        return True
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"Email Error in send_confirmation_email: {type(e).__name__} - {e}")
        return False

def send_magic_link_email(user_email, t_name, t_id, logo_url=""):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    try:
        safe_tname = html.escape(str(t_name))
        clean_email = str(user_email).strip().lower()
        token = secrets.token_urlsafe(32)
        
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        expires = (now_utc + datetime.timedelta(hours=1)).isoformat()
        
        res = get_supabase().table('magic_links').insert({
            'token': token,
            'email': clean_email,
            'tournament_id': str(t_id),
            'expires_at': expires
        }).execute()
        if not res.data: raise ValueError("Failed to insert magic link")
        
        link = f"{BASE_URL}?view=public&tourney_id={t_id}&magic_token={token}"
        msg = MIMEMultipart()
        msg['From'] = formataddr((safe_tname, sender))
        msg['To'] = user_email
        msg['Subject'] = f"{safe_tname} - 🔐 Secure Edit Link"
        
        blocks = [
            f"<p style='font-size: 16px;'>You requested a secure link to edit your team for the <b>{safe_tname} Sweepstakes</b>.</p>",
            f"<p style='font-size: 14px; color: #7f8c8d;'>This link is uniquely tied to your email address and grants direct access to your entry form. For security, this link will automatically expire in 1 hour.</p>"
        ]
        body_html = build_html_email_template(t_name, blocks, "Golfer", link, logo_url)
        msg.attach(MIMEText(body_html, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        try:
            server.starttls()
            server.login(sender, pwd)
            server.send_message(msg)
        finally:
            server.quit()
        return True
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"Email Error in send_magic_link_email: {type(e).__name__} - {e}")
        return False

def safe_entry_payload(t_id, payload_dict):
    """Enforces constraints before writing an entry to Supabase."""
    picks = payload_dict.get('picks', [])
    if not isinstance(picks, list) or len(picks) != 5:
        raise ValueError("Invalid picks format or length. Expected list of 5.")
    clean_picks = [str(p).strip() for p in picks]
    if any(p == "" for p in clean_picks):
        raise ValueError("One or more picks are blank.")
    if len(set(p.lower() for p in clean_picks)) != 5:
        raise ValueError("Duplicate picks detected in payload.")
    picks = clean_picks
    
    return {
        'tournament_id': str(t_id),
        'name': str(payload_dict.get('name', '')),
        'email': str(payload_dict.get('email', '')).strip().lower(),
        'payment_method': str(payload_dict.get('payment_method', '')),
        'tie_breaker': int(safe_int(payload_dict.get('tie_breaker', 0))),
        'pick_1': str(picks[0]),
        'pick_2': str(picks[1]),
        'pick_3': str(picks[2]),
        'pick_4': str(picks[3]),
        'pick_5': str(picks[4])
    }

def append_entry_to_sheet(t_id, payload):
    try:
        clean_email = str(payload.get('email', '')).strip().lower()
        
        # Defensive duplicate check: Exact match intercepts silently
        existing = get_supabase().table('entries').select('*').eq('tournament_id', str(t_id)).eq('email', clean_email).execute()
        if existing.data:
            new_picks = set(payload.get('picks', []))
            for r in existing.data:
                old_picks = set([str(r.get(f'pick_{i}', '')) for i in range(1,6)])
                # INTENTIONAL: only block identical pick sets from the same email.
                # Multiple teams per email with different picks are allowed by design
                # (see (Team 2) rendering logic in get_clean_entries).
                if new_picks == old_picks:
                    st.session_state.setdefault("api_log", []).append(f"Duplicate block hit for {clean_email}")
                    return "DUPLICATE"
                    
        data = safe_entry_payload(t_id, payload)
        data['paid'] = False
        
        res = get_supabase().table('entries').insert(data).execute()
        if not res.data: raise ValueError("Insert returned no data")
        
        get_raw_sheet_data.clear() 
        return True
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"DB Error in append_entry_to_sheet ({t_id}): {type(e).__name__} - {e}")
        return False

def update_single_cell_in_sheet(t_id, row_number, header_name, new_value):
    try:
        mapping = {
            "Payment Method": "payment_method",
            "Pick 1": "pick_1",
            "Pick 2": "pick_2",
            "Pick 3": "pick_3",
            "Pick 4": "pick_4",
            "Pick 5": "pick_5"
        }
        col = mapping.get(header_name)
        if col:
            res = get_supabase().table('entries').update({col: str(new_value)}).eq('id', row_number).eq('tournament_id', str(t_id)).execute()
            if not res.data: raise ValueError("Update returned no rows")
            get_raw_sheet_data.clear()
            return True
        return False
    except Exception as e:
        st.session_state.setdefault("api_log", []).append(f"DB Error in update_single_cell_in_sheet (row {row_number}): {type(e).__name__} - {e}")
        return False

def update_specific_entry(t_id, row_index, payload):
    try:
        data = safe_entry_payload(t_id, payload)
        res = get_supabase().table('entries').update(data).eq('id', row_index).eq('tournament_id', str(t_id)).execute()
        if not res.data: raise ValueError("Update returned no rows")
        get_raw_sheet_data.clear()
        return True
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"DB Error in update_specific_entry (row {row_index}): {type(e).__name__} - {e}")
        return False
        
def update_paid_status(t_id, row_index, is_paid):
    try:
        res = get_supabase().table('entries').update({'paid': bool(is_paid)}).eq('id', row_index).eq('tournament_id', str(t_id)).execute()
        if not res.data: raise ValueError("Update returned no rows")
        get_raw_sheet_data.clear()
        return True
    except Exception as e: 
        st.session_state.setdefault("api_log", []).append(f"DB Error in update_paid_status (row {row_index}): {type(e).__name__} - {e}")
        return False

def send_admin_api_alert(url, error_details):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    
    settings = get_settings_cache()
    last_alert = settings.get("last_api_alert_time")
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if last_alert and (now - last_alert).total_seconds() < 3600:
        return False
        
    try:
        msg = MIMEMultipart()
        msg['From'] = formataddr(("Golf App Alert", sender))
        msg['To'] = sender  
        msg['Subject'] = "🚨 URGENT: API Calls Failing / Quota Exhausted"
        
        body = f"""
        <html>
          <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #2c3e50; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; border-bottom: 2px solid #e74c3c; padding-bottom: 10px; margin-bottom: 20px;">
                <h2 style="color: #c0392b; margin: 0;">🚨 Critical API Failure</h2>
            </div>
            <p style="font-size: 16px;">The application attempted to fetch live data but <b>all available API keys failed</b>.</p>
            <p style="font-size: 16px; color: #d35400;"><b>This likely means you have exhausted your API quotas across all registered keys!</b></p>
            
            <div style="background-color: #f8f9fa; border-left: 4px solid #e74c3c; padding: 15px; border-radius: 4px; margin: 20px 0; font-family: monospace; font-size: 14px;">
                <p style="margin: 0 0 10px 0;"><b>Endpoint:</b><br>{html.escape(url)}</p>
                <p style="margin: 0;"><b>Last Error:</b><br>{html.escape(error_details)}</p>
            </div>
            
            <p style="font-size: 15px;">Please log in to your RapidAPI dashboard to check your usage and update your keys in the Streamlit secrets if necessary.</p>
            <p style="font-size: 12px; color: #7f8c8d; margin-top: 30px;"><i>Note: To prevent spam, this alert is limited to a maximum of 1 email per hour.</i></p>
          </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        try:
            server.starttls()
            server.login(sender, pwd)
            server.send_message(msg)
        finally:
            server.quit()
        
        settings["last_api_alert_time"] = now
        return True
    except Exception as e:
        st.session_state.setdefault("api_log", []).append(f"Email Error in send_admin_api_alert: {type(e).__name__} - {e}")
        return False

def intelligent_api_call(url, trigger_reason="Unknown"):
    settings = get_settings_cache()
    # Explicit Canonical Config Priority (No broad scanning)
    all_keys = st.secrets.get("api_keys")
    if not all_keys or not isinstance(all_keys, list):
        log_to_sheet("API CRITICAL", "api_keys list missing from secrets.toml")
        return None 
        
    current_active = settings.get("active_api_key", all_keys[0])
    last_error = "Unknown Error"
    
    for key_name in [current_active] + [k for k in all_keys if k != current_active]:
        try:
            val = st.secrets.get(key_name) if key_name in st.secrets else key_name
            resp = requests.get(url, headers={"X-RapidAPI-Key": val, "X-RapidAPI-Host": "golf-leaderboard-data.p.rapidapi.com"}, timeout=10)
            if resp.status_code == 200:
                settings["active_api_key"] = key_name
                quota = resp.headers.get("x-ratelimit-requests-remaining") or resp.headers.get("X-RateLimit-Requests-Remaining")
                if quota is not None:
                    settings[f"quota_{key_name}"] = str(quota)
                
                endpoint = url.split(".com/")[-1] 
                log_to_sheet("API CALL", f"Success ({endpoint}) | Reason: {trigger_reason} | Quota Left: {quota}")
                
                return resp.json()
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text}"
        except requests.RequestException as e: 
            last_error = f"{type(e).__name__}: {str(e)}"
            continue
            
    log_to_sheet("API CRITICAL", f"All keys failed for {url}. Last Error: {last_error}")
    send_admin_api_alert(url, last_error)
    return None 

@st.cache_data(ttl=86400, show_spinner=False)
def get_fixture_list(tour_id, year):
    data = intelligent_api_call(f"https://golf-leaderboard-data.p.rapidapi.com/fixtures/{tour_id}/{year}", "Fixture Fetch")
    if data:
        result = {}
        for f in get_safe_api_results(data):
            start = f.get('start_date') or ''
            label = f"{f.get('name', 'Unknown')} ({start[:10]})" if start else str(f.get('name', 'Unknown'))
            result[label] = {"id": f.get('id'), "start": f.get('start_date')}
        return result
    return {}

@st.cache_data(ttl=86400, show_spinner=False)
def get_top_20_players():
    data = intelligent_api_call("https://golf-leaderboard-data.p.rapidapi.com/world-rankings", "OWGR Fetch")
    if data:
        raw = get_safe_api_results(data)
        rank_data = raw.get('rankings', []) or raw.get('world_rankings', []) if isinstance(raw, dict) else raw
        return {p.get('player_name', '').strip().lower(): i + 1 for i, p in enumerate(rank_data[:20])}
    return {}

def get_raw_entry_list(selected_t_id):
    cache = get_api_cache()
    cache_key = f"entry_list_{selected_t_id}"
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if cache_key in cache:
        try:
            if now < cache[cache_key]['expire']: 
                return cache[cache_key]['data']
        except TypeError:
            pass # Ignore naive datetime mismatch
        
    data = intelligent_api_call(f"https://golf-leaderboard-data.p.rapidapi.com/entry-list/{selected_t_id}", "Entry List Fetch")
    
    if data:
        res = get_safe_api_results(data)
        clean_list = [f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() for p in res.get('entry_list', [])] if isinstance(res, dict) else []
        
        expire_time = now + datetime.timedelta(hours=1) if clean_list else now + datetime.timedelta(minutes=15)
        cache[cache_key] = {'data': clean_list, 'expire': expire_time}
        return clean_list
        
    if cache_key in cache: return cache[cache_key]['data']
    
    cache[cache_key] = {'data': [], 'expire': now + datetime.timedelta(minutes=5)}
    return []

def get_formatted_field(selected_t_id, top_20_dict):
    raw_field = get_raw_entry_list(selected_t_id)
    
    if not raw_field and selected_t_id:
        backup_str = fetch_field_backup_from_sheet(selected_t_id)
        if backup_str: return [x.strip() for x in backup_str.split(',') if x.strip()]
        return []
    
    top_20_items = []
    regular_list = []
    for p in raw_field:
        p_lower = p.lower()
        if p_lower in top_20_dict:
            rank = top_20_dict[p_lower]
            top_20_items.append((rank, f"{p} (Top 20 - #{rank})"))
        else:
            regular_list.append(p)
            
    top_20_sorted = [item[1] for item in sorted(top_20_items, key=lambda x: x[0])]
    formatted_field = top_20_sorted + sorted(regular_list)
    
    if formatted_field and selected_t_id:
        backup_str = fetch_field_backup_from_sheet(selected_t_id)
        current_str = ",".join(formatted_field)
        if backup_str != current_str: save_field_backup_to_sheet(selected_t_id, current_str)
        
    return formatted_field

def fetch_smart_leaderboard(selected_t_id):
    cache = get_api_cache()
    now = datetime.datetime.now(datetime.timezone.utc)
    cache_key = f"lb_{selected_t_id}"
    
    if cache_key in cache:
        try:
            if now < cache[cache_key]["next_fetch_allowed"]:
                return cache[cache_key]["data"], cache[cache_key]["next_fetch_allowed"], cache[cache_key]["last_fetch"], cache[cache_key]["full_data"], "⚡ Active Memory Cache"
        except TypeError:
            pass # Cache has old naive datetime; ignore and fetch fresh data
        
    data = intelligent_api_call(f"https://golf-leaderboard-data.p.rapidapi.com/leaderboard/{selected_t_id}")
    if data and get_safe_api_results(data):
        raw_results = get_safe_api_results(data)
        lb_data = raw_results.get('leaderboard', [])
        tourney_data = raw_results.get('tournament', {})
        live_details = tourney_data.get('live_details', {})
        
        state = derive_tournament_state(lb_data, live_details)
        current_r = state['current_r']
        t_status = state['t_status']
        
        if live_details.get('status'):
            live_details['current_round'] = current_r
            live_details['status'] = t_status
        
        for p in lb_data:
            stt = normalize_pga_status(p.get('status', ''))
            if stt == 'pre' and safe_int(p.get('holes_played', 0)) == 0:
                total_strokes = sum(safe_int(r.get('strokes', 0)) for r in p.get('rounds', []))
                if total_strokes == 0 and (current_r > 1 or t_status in ['completed', 'endofday']):
                    p['status'] = 'Withdrawn'
        
        mode = "⛳ Active Play"
        next_fetch = now + datetime.timedelta(minutes=60)
        
        try:
            tourney_tz_str = tourney_data.get('timezone') or 'America/New_York'
            tourney_tz = pytz.timezone(tourney_tz_str)
            now_tourney = now.astimezone(tourney_tz)
            
            target_r = current_r if current_r > 0 else 1
            min_tt_str, max_tt_str = "23:59", "00:00"
            min_tt_date, max_tt_date = None, None
            
            for p in lb_data:
                for r in p.get('rounds', []):
                    if safe_int(r.get('round_number', 0)) == target_r:
                        tt = r.get('tee_time_local', '')
                        r_date = r.get('date', '')
                        if tt and ':' in tt:
                            if tt < min_tt_str: 
                                min_tt_str = tt
                                min_tt_date = r_date
                            if tt > max_tt_str: 
                                max_tt_str = tt
                                max_tt_date = r_date
                            
            has_tee_times = (max_tt_str != "00:00" and min_tt_str != "23:59")
            
            if t_status == 'completed':
                next_fetch = now + datetime.timedelta(minutes=1440)
                mode = "🏁 Finished (24h)"
            elif t_status in ['suspended', 'endofday']:
                next_fetch = now + datetime.timedelta(minutes=60)
                mode = f"⏸️ {t_status.title()} (1h)"
            elif not has_tee_times:
                next_fetch = now + datetime.timedelta(minutes=240)
                mode = "😴 Waiting for Tee Times (4h)"
            else:
                if min_tt_date and max_tt_date:
                    try:
                        first_tt_dt = tourney_tz.localize(datetime.datetime.strptime(f"{min_tt_date} {min_tt_str}", "%Y-%m-%d %H:%M"))
                        last_tt_dt = tourney_tz.localize(datetime.datetime.strptime(f"{max_tt_date} {max_tt_str}", "%Y-%m-%d %H:%M"))
                    except (ValueError, TypeError):
                        min_tt_parts = min_tt_str.split(':')
                        max_tt_parts = max_tt_str.split(':')
                        first_tt_dt = now_tourney.replace(hour=int(min_tt_parts[0]), minute=int(min_tt_parts[1]), second=0, microsecond=0)
                        last_tt_dt = now_tourney.replace(hour=int(max_tt_parts[0]), minute=int(max_tt_parts[1]), second=0, microsecond=0)
                else:
                    min_tt_parts = min_tt_str.split(':')
                    max_tt_parts = max_tt_str.split(':')
                    first_tt_dt = now_tourney.replace(hour=int(min_tt_parts[0]), minute=int(min_tt_parts[1]), second=0, microsecond=0)
                    last_tt_dt = now_tourney.replace(hour=int(max_tt_parts[0]), minute=int(max_tt_parts[1]), second=0, microsecond=0)
                    if t_status == 'pre' and first_tt_dt < now_tourney:
                        first_tt_dt += datetime.timedelta(days=1)
                        last_tt_dt += datetime.timedelta(days=1)

                first_check_dt = first_tt_dt + datetime.timedelta(minutes=30)
                
                if target_r < 4: 
                    target_finish = last_tt_dt + datetime.timedelta(hours=4, minutes=30)
                    if now_tourney < first_check_dt:
                        wait_sec = (first_check_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Wait for R{target_r} Start (+30m after 1st TT)"
                    elif now_tourney < target_finish:
                        wait_sec = (target_finish - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Sleep till R{target_r} Finish (+4.5h after last TT)"
                    else:
                        next_fetch = now + datetime.timedelta(minutes=60)
                        mode = f"⛳ R{target_r} Wrap-up (1h)"
                else: 
                    r4_live_dt = last_tt_dt + datetime.timedelta(minutes=10)
                    if now_tourney < first_check_dt:
                        wait_sec = (first_check_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Wait for R4 Start (+30m after 1st TT)"
                    elif now_tourney < r4_live_dt:
                        wait_sec = (r4_live_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Wait for R4 Last TT (+10m)"
                    else:
                        next_fetch = now + datetime.timedelta(minutes=5)
                        mode = "🔥 R4 Live Scoring (5m)"

        except Exception as e:
            log_to_sheet("TZ/TT ERROR", f"{type(e).__name__} - {str(e)}")
            next_fetch = now + datetime.timedelta(minutes=60)
            mode = "⛳ Active Play (Fallback 1h)"
        
        cache[cache_key] = {"data": lb_data, "last_fetch": now, "next_fetch_allowed": next_fetch, "mode": mode, "full_data": data}
        if selected_t_id: save_api_backup_to_sheet(selected_t_id, data)
        return lb_data, next_fetch, now, data, "📡 Live API"
        
    if selected_t_id:
        backup_data = fetch_api_backup_from_sheet(selected_t_id)
        if backup_data and backup_data.get('results'):
            lb_data = backup_data.get('results', {}).get('leaderboard', [])
            live_details = backup_data.get('results', {}).get('tournament', {}).get('live_details', {})
            
            state = derive_tournament_state(lb_data, live_details)
            current_r = state['current_r']
            t_status = state['t_status']
            
            for p in lb_data:
                stt = normalize_pga_status(p.get('status', ''))
                if stt == 'pre' and safe_int(p.get('holes_played', 0)) == 0:
                    total_strokes = sum(safe_int(r.get('strokes', 0)) for r in p.get('rounds', []))
                    if total_strokes == 0 and (current_r > 1 or t_status in ['completed', 'endofday']):
                        p['status'] = 'Withdrawn'
                        
            next_fetch = now + datetime.timedelta(minutes=5) 
            cache[cache_key] = {"data": lb_data, "last_fetch": now, "next_fetch_allowed": next_fetch, "mode": "backup", "full_data": backup_data}
            return lb_data, next_fetch, now, backup_data, "💾 Supabase DB Backup (API Offline)"
            
    next_fetch = now + datetime.timedelta(minutes=5)
    cache[cache_key] = {"data": [], "last_fetch": now, "next_fetch_allowed": next_fetch, "mode": "offline", "full_data": {}}
    return [], next_fetch, now, {}, "❌ No Data Available"

def render_roster_table(picks, p_info_lower, rounds_active, counting_map, is_live_active, round_scores, round_ranks, is_elim, current_r, hide_rank=False):
    rows = []
    for p in picks:
        p_key = str(p).lower()
        p_safe = html.escape(str(p))
        if p_key not in p_info_lower:
            rows.append(f"<tr><td style='padding-bottom: 5px;'>⚠️ {p_safe}</td><td colspan='{len(rounds_active)+1}' style='color:red;'>Not in Field</td></tr>")
            continue
        d = p_info_lower[p_key]; stt = d['status']
        row_html = f"<tr><td style='padding-right:15px; min-width:140px; padding-bottom: 5px;'>{p_safe}</td>"
        for r in rounds_active:
            s = d['rounds'].get(r, None); is_cnt = p_key in counting_map.get(r, [])
            if s is None and stt in ['cut', 'wd', 'dq']: val = f"<span style='color:#e74c3c;'>{stt.upper()}</span>"
            elif s is None: val = "<span style='color:#7f8c8d;'>-</span>"
            else: 
                txt = format_score(s)
                if is_live_active and r == current_r and stt == 'active':
                    hp = d.get('holes_played', 0)
                    if 0 < hp < 18:
                        txt = f"{txt} <small>({hp})</small>"
                        
                if s < 0: 
                    score_color = "#e74c3c"
                    bg_color = "rgba(231, 76, 60, 0.15)"
                elif s == 0: 
                    score_color = "#2ecc71"
                    bg_color = "rgba(46, 204, 113, 0.15)"
                else: 
                    score_color = "var(--text-color)"
                    bg_color = "rgba(130, 130, 130, 0.15)"
                
                if is_cnt:
                    val = f"<span style='color: {score_color}; background-color: {bg_color}; padding: 2px 6px; border-radius: 6px; font-weight: bold;'>{txt}</span>"
                else:
                    val = f"<span style='color: {score_color}; opacity: 0.45;'>{txt}</span>"
                    
            row_html += f"<td style='padding: 0 10px; padding-bottom: 5px;'>R{r}: {val}</td>"
        
        if stt not in ['cut', 'wd', 'dq']:
            if d['total'] < 0: tot_col = "#e74c3c"
            elif d['total'] == 0: tot_col = "#2ecc71"
            else: tot_col = "var(--text-color)"
            row_html += f"<td style='padding: 0 10px; padding-bottom: 5px; white-space: nowrap;'><b>TOT: <span style='color:{tot_col if current_r > 0 else '#7f8c8d'};'>{format_score(d['total']) if current_r > 0 else '-'}</span></b></td>"
        else: 
            row_html += f"<td style='padding: 0 10px; padding-bottom: 5px; white-space: nowrap;'><b>TOT: <span style='color:#e74c3c;'>{stt.upper()}</span></b></td>"
            
        row_html += "</tr>"; rows.append(row_html)
        
    tot_html = "<tr><td style='padding-right:15px; border-top: 1px solid var(--border-color); padding-top: 8px;'><b>TOTAL (POS)</b></td>"
    for r in rounds_active:
        if is_elim and r >= 3: tot_html += f"<td style='padding: 0 10px; border-top: 1px solid var(--border-color); padding-top: 8px;'><b style='color:red;'>CUT</b></td>"
        else:
            r_score = round_scores.get(r, 0)
            if r_score < 0: r_col = "#e74c3c"
            elif r_score == 0: r_col = "#2ecc71"
            else: r_col = "var(--text-color)"
            
            s_txt = f"<span style='color: {r_col};'>{format_score(r_score)}</span>" if current_r > 0 else "-"
            rank_display = round_ranks.get(r, '-') if (current_r > 0 and not hide_rank) else '-'
            tot_html += f"<td style='padding: 0 10px; border-top: 1px solid var(--border-color); padding-top: 8px;'><b>{s_txt}</b> <small>({rank_display})</small></td>"
            
    tot_html += "<td></td></tr>"; rows.append(tot_html)
    return f"<div style='overflow-x: auto;'><table style='width:100%; border-collapse:collapse; font-family:monospace; font-size: 0.95em;'>{''.join(rows)}</table></div>"

def get_clean_entries(t_id, public_mode, valid_players=[], dns_players=[], email_filter=None):
    if not t_id: return pd.DataFrame(), 0
    try:
        df = get_raw_sheet_data(t_id)
        if df.empty or len(df.columns) < 2: return pd.DataFrame(), 0
        total_entries = len(df)
        
        if email_filter:
            if 'Email' in df.columns: df = df[df['Email'].astype(str).str.lower().str.strip() == email_filter.lower().strip()].copy()
            else: return pd.DataFrame(), total_entries

        name_col = 'Name'
        df['_Original_Name'] = df[name_col]
        df['_name_lower'] = df[name_col].astype(str).str.lower().str.strip()
        df = df.sort_values('Sheet_Row')
        df['_total_entries'] = df.groupby('_name_lower')['_name_lower'].transform('count')
        df['_entry_num'] = df.groupby('_name_lower').cumcount() + 1
        
        def format_admin_name(r):
            base = str(r['_Original_Name']).strip()
            return f"{base} (Team {r['_entry_num']})" if r['_total_entries'] > 1 else base
            
        df[name_col] = df.apply(format_admin_name, axis=1)
        df = df.drop(columns=['_name_lower', '_entry_num', '_total_entries'])

        if valid_players or dns_players:
            stt = []; v_low = [p.strip().lower() for p in valid_players]; d_low = [p.strip().lower() for p in dns_players]
            pick_cols = ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5']

            for _, row in df.iterrows():
                inv = []; wrn = []
                for col in pick_cols:
                    if col in df.columns:
                        p = str(row[col]).split(" (Top 20")[0].strip()
                        if p and p.lower() != 'nan':
                            l = p.lower()
                            if l in d_low: wrn.append(p)
                            elif l not in v_low: inv.append(p)
                msgs = []
                if inv: msgs.append(f"❌ {', '.join(inv)} not in field")
                if wrn: msgs.append(f"⚠️ {', '.join(wrn)} DNS")
                stt.append("✅ All Valid" if not msgs else " | ".join(msgs))
            df.insert(df.columns.get_loc(name_col)+1, "Field Status", stt)
        
        desired_order = [name_col, 'Email', 'Payment Method', 'Paid']
        if "Field Status" in df.columns: desired_order.append("Field Status")
        desired_order.append('Tie Breaker')
        desired_order.extend(['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5'])
        
        remaining_cols = [c for c in df.columns if c not in desired_order]
        df = df[desired_order + remaining_cols]
        
        df['_sort_name'] = df[name_col].astype(str).str.lower()
        if "Field Status" in df.columns:
            df['_has_error'] = df['Field Status'].apply(lambda x: 0 if str(x).strip().startswith("✅") else 1)
            df = df.sort_values(by=['_has_error', '_sort_name'], ascending=[False, True]).drop(columns=['_has_error', '_sort_name'])
        else:
            df = df.sort_values(by=['_sort_name'], ascending=[True]).drop(columns=['_sort_name'])
        df.index = range(1, len(df) + 1)
        return df, total_entries
    except Exception as e:
        st.session_state.setdefault("api_log", []).append(f"Error building UI entry grid: {type(e).__name__} - {e}")
        return pd.DataFrame(), 0


def _parse_t_start(t_start):
    """Safely parse a tournament start date that may be a datetime, a full
    datetime string, or a date-only string, returning a datetime or None."""
    if not t_start:
        return None
    if isinstance(t_start, datetime.datetime):
        return t_start
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(t_start), fmt)
        except ValueError:
            pass
    return None

def calculate_leaderboard(t_id, t_name, t_start, par_override=0, dns_input="", valid_players=None, logo_url=None, is_admin_download=False, header_only=False, is_admin_view=False, hide_title=False, search_query=""):
    try:
        lb_data, next_f, last_f, full_data, data_source = fetch_smart_leaderboard(t_id)
        
        raw_results = full_data.get('results') or {}
        tourney_data = raw_results.get('tournament') or {}
        live_details = tourney_data.get('live_details') or {}
        
        state = derive_tournament_state(lb_data, live_details, is_r4_live_mode=False, is_admin_view=is_admin_view)
        t_status = state['t_status']
        current_r = state['current_r']
        sweep_max_round = state['sweep_max_round']
        is_round_finished_consensus = state['is_round_finished_consensus']
        last_group_teed_off = state['last_group_teed_off']
        last_group_htr = state['last_group_htr']
        is_r4_fully_done = state['is_r4_fully_done']
            
        p_info = {}
        dns_list = [n.strip().lower() for n in str(dns_input).split(",")] if dns_input else []

        for p in lb_data:
            name = f"{p['first_name']} {p['last_name']}".strip()
            name_lower = name.lower()
            norm_status = normalize_pga_status(p.get('status', ''))
            final_status = 'wd' if name_lower in dns_list else norm_status
            
            try: strokes = [safe_int(rd.get('strokes', 0)) for rd in p.get('rounds', [])]
            except (ValueError, TypeError): strokes = [0]*4
                
            rds = {}
            if final_status not in ['wd', 'dq']:
                for i, rd in enumerate(p.get('rounds', [])):
                    r_num = rd.get('round_number')
                    if not r_num or r_num > sweep_max_round: continue 
                    s = strokes[i]
                    if s > 0: rds[r_num] = (s - par_override) if (par_override > 0 and s > 40) else safe_int(rd.get('total_to_par', 0))
                
                if final_status == 'active' and sweep_max_round == current_r:
                    hp = safe_int(p.get('holes_played', 0))
                    api_total = safe_int(p.get('total_to_par', 0))
                    api_past_total = get_corrected_past_total(p, current_r, par_override)
                    
                    has_started = (hp > 0) or (api_total != api_past_total)
                    if has_started:
                        rds[current_r] = api_total - api_past_total
                        
            if final_status in ['wd', 'dq']: final_total = 999
            elif final_status == 'cut': final_total = safe_int(p.get('total_to_par', 0))
            else:
                if sweep_max_round == current_r and final_status == 'active':
                    api_total = safe_int(p.get('total_to_par', 0))
                    if par_override > 0:
                        api_past_total = get_corrected_past_total(p, current_r, par_override)
                        final_total = sum([rds.get(x, 0) for x in range(1, current_r)]) + (api_total - api_past_total)
                    else: final_total = api_total
                else: final_total = sum(rds.values())
                
            p_info[name] = {'status': final_status, 'rounds': rds, 'total': final_total, 'holes_played': safe_int(p.get('holes_played', 0))}
            
        if dns_input:
            for dns_name in str(dns_input).split(","):
                clean_dns = dns_name.strip()
                if not clean_dns: continue
                if clean_dns not in p_info: p_info[clean_dns] = {'status': 'wd', 'rounds': {}, 'total': 999, 'holes_played': 0}
        
        p_info_lower = {k.lower(): v for k, v in p_info.items()}
        
        alias_str = fetch_aliases_from_sheet(t_id)
        if alias_str:
            for pair in alias_str.split(','):
                if ':' in pair:
                    wrong, correct = pair.split(':')
                    wrong, correct = wrong.strip().lower(), correct.strip().lower()
                    if correct in p_info_lower:
                        p_info_lower[wrong] = p_info_lower[correct]
            
        if current_r == 0 or t_status == 'pre':
            for vp in (valid_players or []):
                if vp.lower() not in p_info_lower: p_info_lower[vp.lower()] = {'status': 'active', 'rounds': {}, 'total': 0, 'holes_played': 0}
        
        win_score = 0
        if sweep_max_round > 0:
            active_scores = [v['total'] for v in p_info_lower.values() if v['status'] in ['active', 'completed', 'cut', 'endofday', 'pre'] and v['total'] != 999]
            if active_scores: win_score = min(active_scores)
            
        hide_rank = (t_status == 'pre' or current_r == 0) or (current_r == 1 and t_status not in ['endofday', 'completed'])
        
        if t_status == 'completed' or (current_r == 4 and (is_r4_fully_done or last_group_htr >= 18)):
            if t_status == 'completed':
                status_txt = f"Tournament Finished | Winning Score: {format_score(win_score)}"
            else:
                status_txt = "All players finished: Final Result Being Checked!"
        elif t_status == 'pre':
            _dt = _parse_t_start(t_start)
            status_txt = f"Waiting for R1 to start... ⏳ ({_dt.strftime('%d %b %y')})" if _dt else "Waiting for R1 to start... ⏳"
        elif t_status == 'suspended':
            status_txt = f"Play Suspended (R{current_r})"
        elif t_status == 'endofday':
            status_txt = f"R{current_r} Completed"
        else: 
            if current_r == 4:
                if not last_group_teed_off: status_txt = "R4 In-Play | Live scoring will begin once all players tee off"
                else: status_txt = f"R4 In-Play | Leader: {format_score(win_score)} | Last Group Thru: {last_group_htr}"
            else: status_txt = f"R{current_r} In-Play"
            
        if not is_admin_download:
            if logo_url:
                st.markdown(f"<div style='text-align: center; padding-bottom: 10px;'><img src='{safe_url(logo_url)}' style='max-width: 250px; max-height: 150px; width: 100%; object-fit: contain;'></div>", unsafe_allow_html=True)
            elif not hide_title:
                st.header(f"🏆 {html.escape(str(t_name).split('(')[0])}")

            if t_status != 'pre' and current_r > 0:
                if t_status == 'completed':
                    status_bar_html = f"<div class='compact-container' style='display: flex; justify-content: center; align-items: center; padding: 12px;'><div style='font-weight: bold; font-size: 1.15em; color: #2ecc71;'>🏆 {html.escape(str(status_txt))}</div></div>"
                    st.markdown(status_bar_html, unsafe_allow_html=True)
                else:
                    last_ts = int(last_f.timestamp() * 1000) if last_f else 0
                    next_ts = int(next_f.timestamp() * 1000) if next_f else 0

                    status_html = f"""
                    <!DOCTYPE html>
                    <html>
                    <head>
                    <style>
                        body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: transparent; padding: 2px; }}
                        .box {{ text-align: center; border-radius: 6px; padding: 10px; border: 1px solid rgba(130, 130, 130, 0.4); background-color: #f8f9fa; color: #2c3e50; }}
                        @media (prefers-color-scheme: dark) {{
                            .box {{ background-color: #262730; color: #FAFAFA; border-color: rgba(255, 255, 255, 0.2); }}
                        }}
                        .title {{ font-weight: bold; font-size: 1.05em; }}
                        .row {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 15px; margin-top: 6px; font-size: 0.95em; }}
                    </style>
                    </head>
                    <body>
                        <div class="box">
                            <div class="title">⛳ {html.escape(str(status_txt))}</div>
                            <div class="row">
                                <span>🕒 <b>Last Check:</b> <span id="t-last">...</span></span>
                                <span>⏳ <b>Next Check:</b> <span id="t-next">...</span></span>
                            </div>
                        </div>
                        <script>
                            function fmt(ts) {{
                                if (!ts) return "Unknown";
                                return new Date(ts).toLocaleString([], {{ weekday: "short", hour: "numeric", minute: "2-digit", hour12: true, timeZoneName: "short" }});
                            }}
                            document.getElementById("t-last").innerText = fmt({last_ts});
                            document.getElementById("t-next").innerText = fmt({next_ts});
                        </script>
                    </body>
                    </html>
                    """
                    st.iframe(status_html, height=85)
                
        if header_only: return

        if not t_id: return pd.DataFrame() if is_admin_download else None
            
        df_resp = get_raw_sheet_data(t_id)
        if df_resp.empty or len(df_resp.columns) < 2: 
            if is_admin_download: return pd.DataFrame()
            st.info("No entries have been submitted yet."); return None
        
        name_col = 'Name'
        df_resp['_name_lower'] = df_resp[name_col].astype(str).str.lower().str.strip()
        df_resp['_total_entries'] = df_resp.groupby('_name_lower')['_name_lower'].transform('count')
        df_resp['_entry_num'] = df_resp.groupby('_name_lower').cumcount() + 1
        
        def make_display_name(r):
            base = html.escape(str(r[name_col]).strip())
            if r['_total_entries'] > 1:
                return f"{base} <span style='font-size: 0.75em; background-color: rgba(130, 130, 130, 0.25); padding: 2px 6px; border-radius: 10px; margin-left: 6px; font-weight: normal; vertical-align: middle;'>Team {r['_entry_num']}</span>"
            return base

        def make_export_name(r):
            base = str(r[name_col]).strip()
            return f"{base} (Team {r['_entry_num']})" if r['_total_entries'] > 1 else base
            
        df_resp['_Display_Name'] = df_resp.apply(make_display_name, axis=1)
        df_resp[name_col] = df_resp.apply(make_export_name, axis=1)
        
        pick_cols = ['Pick 1', 'Pick 2', 'Pick 3', 'Pick 4', 'Pick 5']
        r_range = list(range(1, sweep_max_round + 1)) if sweep_max_round > 0 else [1]
        
        results = []
        for _, row in df_resp.iterrows():
            picks = [str(row[c]).split(" (Top 20")[0].strip() for c in pick_cols if c in df_resp.columns][:5]
            sheet_warns = []
            
            total = 0; c_map = {}; r_sc = {}; cum_sc = {}; running = 0
            for r in r_range:
                p_scores = []
                for p in picks:
                    p_key = p.lower()
                    if p_key in p_info_lower:
                        if r in p_info_lower[p_key]['rounds']: score = p_info_lower[p_key]['rounds'][r]
                        elif p_info_lower[p_key]['status'] in ['cut', 'wd', 'dq']: score = 99
                        else: score = 0 
                    else: score = 99
                    p_scores.append((p_key, score))
                
                p_scores.sort(key=lambda x: int(x[1]))
                top_4 = p_scores[:4]; c_map[r] = [x[0] for x in top_4]
                r_sum = sum(int(x[1]) for x in top_4)
                r_sc[r] = r_sum; running += r_sum; cum_sc[r] = running; total += r_sum
                
            cut_made = (current_r > 2) or (current_r == 2 and is_round_finished_consensus)
            elim = (cut_made and sum(1 for p in picks if p.lower() in p_info_lower and p_info_lower[p.lower()]['status'] not in ['cut','wd','dq']) < 4)
            
            t_val = extract_tie_breaker(row['Tie Breaker']) if 'Tie Breaker' in row else 0
            
            results.append({"Participant": str(row[name_col]), "DisplayName": str(row['_Display_Name']), "Total": total, "Elim": elim, "Picks": picks, "CMap": c_map, "RSc": r_sc, "Cum": cum_sc, "Diff": abs(t_val - win_score), "Tie": t_val, "SheetWarnings": sheet_warns})

        if not results:
            if is_admin_download: return pd.DataFrame()
            st.info("No entries have been submitted yet."); return None

        df = pd.DataFrame(results)
        if hide_rank:
            df = df.sort_values(by="Participant", key=lambda x: x.astype(str).str.lower()).reset_index(drop=True)
            df['DRank'] = "-"
        else:
            df = df.sort_values(by=["Elim", "Total", "Diff", "Participant"], ascending=[True, True, True, True]).reset_index(drop=True)
            dr = []; cr = 1
            for i in range(len(df)):
                if i > 0 and df.iloc[i]['Elim'] == df.iloc[i-1]['Elim'] and df.iloc[i]['Total'] == df.iloc[i-1]['Total'] and df.iloc[i]['Diff'] == df.iloc[i-1]['Diff']:
                    dr.append(f"T{cr}")
                    if not str(dr[i-1]).startswith("T"): dr[i-1] = f"T{cr}"
                else: cr = i + 1; dr.append(str(cr))
            df['DRank'] = dr

        history_ranks = {idx: {} for idx in df.index}
        for r in r_range:
            sorted_indices = sorted(df.index, key=lambda x: df.loc[x, 'Cum'].get(r, 9999))
            rank_start = 1
            for k, idx in enumerate(sorted_indices):
                score = df.loc[idx, 'Cum'].get(r, 9999)
                if k > 0 and score == df.loc[sorted_indices[k-1], 'Cum'].get(r, 9999):
                    r_str = f"T{rank_start}"
                    prev_idx = sorted_indices[k-1]
                    if not history_ranks[prev_idx].get(r, "").startswith("T"): history_ranks[prev_idx][r] = f"T{rank_start}"
                else: rank_start = k + 1; r_str = str(rank_start)
                history_ranks[idx][r] = r_str

        if is_admin_download: return df 
        
        if t_status == 'completed' and not is_admin_view and not header_only:
            if not st.session_state.get(f"balloons_{t_id}"):
                st.balloons(); st.session_state[f"balloons_{t_id}"] = True
                
            settings_cache = get_settings_cache()
            conf = settings_cache.get(f"payout_confirmed_{t_id}", False)
            
            prize_pool = [
                float(settings_cache.get(f"payout_1_{t_id}") or 0),
                float(settings_cache.get(f"payout_2_{t_id}") or 0),
                float(settings_cache.get(f"payout_3_{t_id}") or 0)
            ]
            
            if not df.empty:
                podium_players = []
                current_prizes_used = 0
                grouped_ranks = {}
                
                for _, row in df.iterrows():
                    r_str = str(row.get('DRank', '-'))
                    if r_str == '-': continue
                    if r_str not in grouped_ranks: grouped_ranks[r_str] = []
                    grouped_ranks[r_str].append(html.escape(str(row['Participant'])))
                
                for r_str, names in grouped_ranks.items():
                    match = re.search(r'\d+', r_str)
                    if not match: continue
                    base_r = int(match.group())
                    
                    if current_prizes_used >= 3: break 
                        
                    n_players = len(names)
                    prizes_to_take = min(n_players, 3 - current_prizes_used)
                    
                    total_cash = 0
                    for _ in range(prizes_to_take):
                        total_cash += prize_pool[current_prizes_used]
                        current_prizes_used += 1
                        
                    payout = total_cash / n_players if n_players > 0 else 0
                    
                    for name in names:
                        podium_players.append({"name": name, "rank": base_r, "payout": payout})
                
                r1_players = [p for p in podium_players if p['rank'] == 1]
                r2_players = [p for p in podium_players if p['rank'] == 2]
                r3_players = [p for p in podium_players if p['rank'] == 3]
                
                display_order = r2_players + r1_players + r3_players
                    
                podium_styles = {
                    1: {"medal": "🥇", "bg": "linear-gradient(145deg, #fffbeb, #fef08a)", "border": "#fde047", "padding": "25px 10px 15px", "sz": "2.8em", "z": "3", "shadow": "box-shadow: 0 -4px 10px rgba(0,0,0,0.1);", "mw": "160px"},
                    2: {"medal": "🥈", "bg": "linear-gradient(145deg, #f8f9fa, #e2e8f0)", "border": "#cbd5e1", "padding": "15px 10px", "sz": "2.2em", "z": "2", "shadow": "", "mw": "140px"},
                    3: {"medal": "🥉", "bg": "linear-gradient(145deg, #fff7ed, #ffedd5)", "border": "#fed7aa", "padding": "10px", "sz": "1.8em", "z": "1", "shadow": "", "mw": "140px"}
                }
                
                html_blocks = []
                for p in display_order:
                    s = podium_styles.get(p['rank'], podium_styles[3]) 
                    pz_str = ""
                    if conf and p['payout'] > 0:
                        cash_str = f"{p['payout']:,.2f}".replace(".00", "") 
                        pz_str = f"<br><span style='color:#27ae60; font-size:1.1em;'><b>${cash_str}</b></span>"
                        
                    block = f"<div style='flex: 1; max-width: {s['mw']}; background: {s['bg']}; border-radius: 8px 8px 0 0; padding: {s['padding']}; margin: 0 4px; border: 1px solid {s['border']}; border-bottom: none; position: relative; z-index: {s['z']}; {s['shadow']}'><div style='font-size: {s['sz']}; margin-bottom: 5px;'>{s['medal']}</div><div style='font-size: 0.9em; font-weight: bold; color: #475569; word-wrap: break-word;'>{p['name']}</div>{pz_str}</div>"
                    html_blocks.append(block)
                
                final_html = "<div style='display: flex; justify-content: center; align-items: flex-end; margin: 20px 0 0px 0; text-align: center; font-family: sans-serif;'>" + "".join(html_blocks) + "</div><div style='height: 4px; background-color: #2c3e50; margin-bottom: 30px; border-radius: 2px;'></div>"
                
                st.markdown(final_html, unsafe_allow_html=True)
        
        if search_query:
            df = df[df['Participant'].str.contains(search_query, case=False, na=False)]
            if df.empty:
                st.warning("🔍 No entries found matching your search.")
        
        is_live_active_now = (sweep_max_round == current_r and t_status == 'active')

        max_name_len = max([len(str(n)) for n in df['Participant']]) if not df.empty else 15
        name_min_width = max(max_name_len * 8, 110) 

        leaderboard_html = "<div style='display: flex; flex-direction: column; gap: 4px; overflow-x: auto;'>"

        if not hide_rank:
            legend_html = f"<div style='display: grid; grid-template-columns: 34px 40px {name_min_width}px 80px auto; gap: 8px; width: max-content; font-size: 0.85em; color: var(--text-color); opacity: 0.7; margin-bottom: 2px;'><div></div><div></div><div></div><div style='white-space: nowrap;'>🏌️‍♂️ Score (Tie)</div><div></div></div>"
            leaderboard_html += legend_html
        
        for i, row in df.iterrows():
            d = row['DRank']
            
            if hide_rank: lbl_val = "-"
            elif t_status == 'completed': lbl_val = "🥇" if d in ["1","T1"] else "🥈" if d in ["2","T2"] else "🥉" if d in ["3","T3"] else f"#{d}"
            else: lbl_val = f"#{d}"
            
            score_txt = format_score(row['Total']) if not hide_rank else "-"
            warn_list = []
            vp_lower = [v.lower() for v in valid_players] if valid_players else []
            api_data_loaded = len(p_info_lower) > 0
            
            for p in row['Picks']:
                p_key = p.lower()
                if not api_data_loaded: pass 
                elif p_key not in p_info_lower: warn_list.append(html.escape(f"Invalid Pick: {p}"))
                elif vp_lower and p_key not in vp_lower:
                    if p_info_lower[p_key]['status'] not in ['cut', 'wd', 'dq']: warn_list.append(html.escape(f"Possible WD/DNS: {p}"))
            
            for w in row['SheetWarnings']: warn_list.append(html.escape(str(w)))
            warn_list = list(dict.fromkeys(warn_list))
            
            if row['Elim']: 
                score_block = "<div style='color: #e74c3c; font-weight: bold; white-space: nowrap;'>❌ CUT</div>"
            else: 
                score_color = '#2ecc71' 
                score_block = f"<div style='white-space: nowrap;'><b style='color: {score_color};'>{score_txt}</b> <span style='color: var(--text-color); opacity: 0.6; font-size: 0.9em;'>({html.escape(str(row['Tie']))})</span></div>"
                
            warn_html = f"<div style='color: #e67e22; font-size: 0.85em; white-space: nowrap;'>⚠️ {' | '.join(warn_list)}</div>" if warn_list else "<div></div>"
            
            table_html = render_roster_table(row['Picks'], p_info_lower, r_range, row['CMap'], is_live_active_now, row['RSc'], history_ranks[i], row['Elim'], current_r, hide_rank)
            
            summary_html = f"<div style='display: grid; grid-template-columns: 40px {name_min_width}px 80px auto; gap: 8px; align-items: center; width: max-content;'><div style='font-weight: bold;'>{lbl_val}</div><div style='font-weight: bold; white-space: nowrap;'>{row['DisplayName']}</div>{score_block}{warn_html}</div>"
            
            leaderboard_html += f"<details><summary>{summary_html}</summary><div class='expanded-content'>{table_html}</div></details>"
            
        leaderboard_html += "</div>"
        
        st.markdown(leaderboard_html, unsafe_allow_html=True)
        
    except Exception as e: 
        st.error(f"Error calculating leaderboard: {type(e).__name__} - {e}")
        return pd.DataFrame() if is_admin_download else None

def render_pga_leaderboard(lb_data, full_data, tourney_id, view_mode, par_override=0, hide_tt=False):
    if not lb_data: st.info("No tournament data available yet."); return

    raw_results = get_safe_api_results(full_data) if full_data else {}
    tourney_data = raw_results.get('tournament') or {}
    live_details = tourney_data.get('live_details') or {}
    tourney_tz_str = tourney_data.get('timezone') or 'America/New_York'
    
    state = derive_tournament_state(lb_data, live_details)
    curr_r = state['current_r']
    global_status = state['t_status']
    
    tz_choice = st.radio("Display Tee Times in:", ["🇬🇧 UK Time", "🇺🇸 US Eastern (ET)"], horizontal=True, key=f"tz_{tourney_id}_{view_mode}")
    target_tz = 'Europe/London' if "UK" in tz_choice else 'America/New_York'

    if global_status == 'completed': completed_rounds = 4
    elif global_status == 'endofday': completed_rounds = curr_r
    else:
        completed_rounds = curr_r if state['is_round_finished_consensus'] else curr_r - 1
            
    cache = get_api_cache()
    lb_key = f"lb_{tourney_id}"
    is_r4_live = (lb_key in cache and "R4 Live" in cache[lb_key].get("mode", ""))
    
    if view_mode == "admin":
        max_visible_round = curr_r
        is_live_scoring_active = (global_status == 'active')
    else:
        if is_r4_live and curr_r == 4:
            max_visible_round = 4
            is_live_scoring_active = True
        else:
            max_visible_round = completed_rounds
            is_live_scoring_active = False

    if completed_rounds == 4 or is_live_scoring_active: thru_header = "Thru"
    else: thru_header = f"R{completed_rounds + 1} Tee Times"

    real_df = pd.DataFrame([{
        'Pos': str(p.get('position', '-')), 
        'Status': normalize_pga_status(p.get('status', '')),
        'Player': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        'Total': get_pga_total_num(p, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'Thru': get_pga_thru(p, curr_r, completed_rounds, is_live_scoring_active, tourney_tz_str, target_tz, hide_tt),
        'R1': format_pga_round_score(p, 0, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'R2': format_pga_round_score(p, 1, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'R3': format_pga_round_score(p, 2, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'R4': format_pga_round_score(p, 3, max_visible_round, par_override, is_live_scoring_active, curr_r)
    } for p in lb_data])
    
    if not is_live_scoring_active:
        real_df['Sort_Status'] = real_df['Status'].apply(lambda x: 1 if x in ['wd', 'cut', 'dq'] else 0)
        real_df = real_df.sort_values(by=['Sort_Status', 'Total', 'Player']).reset_index(drop=True)
        ranks = []; crank = 1
        for i in range(len(real_df)):
            score, status = real_df.loc[i, 'Total'], real_df.loc[i, 'Status']
            if status == 'wd': ranks.append("WD")
            elif status == 'cut': ranks.append("CUT")
            elif status == 'dq': ranks.append("DQ")
            elif score == 999: ranks.append("-")
            else:
                if i > 0 and real_df.loc[i, 'Total'] == real_df.loc[i-1, 'Total'] and real_df.loc[i, 'Sort_Status'] == 0:
                    ranks.append(f"T{crank}"); ranks[i-1] = f"T{crank}" if not str(ranks[i-1]).startswith("T") else ranks[i-1]
                else: crank = i + 1; ranks.append(str(crank))
        real_df['Pos'] = ranks
    
    real_df = real_df.drop(columns=['Status', 'Sort_Status'], errors='ignore')
    st.dataframe(real_df.style.format({'Total': golf_fmt}), width="stretch", hide_index=True, column_config={
        "Pos": st.column_config.TextColumn("Pos", width="small", pinned=True),
        "Player": st.column_config.TextColumn("Golfer", width="medium"),
        "Total": st.column_config.Column("Total", width="small"),
        "Thru": st.column_config.TextColumn(thru_header, width="medium"),
        "R1": st.column_config.TextColumn("R1", width="small"),
        "R2": st.column_config.TextColumn("R2", width="small"),
        "R3": st.column_config.TextColumn("R3", width="small"),
        "R4": st.column_config.TextColumn("R4", width="small")
    })

# --- 5. MAIN UI ---
settings = get_settings_cache()
if is_public:
    tid = target_tourney_id
    if not tid and "tournaments" in st.secrets: tid = next(iter(st.secrets["tournaments"]), None)
    if not tid: st.error("No tournament ID."); st.stop()
    st.session_state['current_t_id'] = tid
    
    current_par_override = fetch_par_from_sheet(tid)
    current_hide_tt = fetch_hide_tt_from_sheet(tid)

    try:
        pconf = json.loads(fetch_payout_config_from_sheet(tid))
        if "payout_confirmed" in pconf:
            settings[f"payout_confirmed_{tid}"] = pconf.get("payout_confirmed", False)
            settings[f"payout_1_{tid}"] = pconf.get("p1", 0)
            settings[f"payout_2_{tid}"] = pconf.get("p2", 0)
            settings[f"payout_3_{tid}"] = pconf.get("p3", 0)
            settings[f"payout_pot_{tid}"] = pconf.get("pot", 0)
    except json.JSONDecodeError: pass
    
    lb_data, _, _, full_data, data_source = fetch_smart_leaderboard(tid)
    
    raw_results = get_safe_api_results(full_data)
    tourney_data = raw_results.get('tournament') or {}
    live_details = tourney_data.get('live_details') or {}
    t_status = normalize_pga_status(live_details.get('status', ''))
    is_tournament_started = t_status != 'pre'
    
    tnm = tourney_data.get('name', 'Tournament')
    t_start = tourney_data.get('start_date')
    
    cache = get_api_cache()
    lb_key = f"lb_{tid}"
    is_live_now = (lb_key in cache and "R4 Live Scoring" in cache[lb_key].get("mode", ""))
    refresh_script = """setInterval(function(){ window.location.reload(); }, 300000);""" if is_live_now else ""
    
    st.html(f"""
    <script>
        document.title = '{html.escape(str(tnm).replace('"', ''))} Sweepstakes';
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
    
    try: t_start_dt = datetime.datetime.strptime(str(t_start), "%Y-%m-%d %H:%M:%S") if t_start else None
    except (ValueError, TypeError): t_start_dt = None
    
    default_close = t_start_dt.replace(hour=5, minute=0, second=0) if t_start_dt else None
    default_reveal = t_start_dt.replace(hour=5, minute=0, second=0) if t_start_dt else None
    
    db_close = fetch_close_time_from_db(tid)
    db_reveal = fetch_reveal_time_from_db(tid)
    
    try: close_time = datetime.datetime.strptime(db_close, "%Y-%m-%d %H:%M:%S") if db_close else default_close
    except (ValueError, TypeError): close_time = default_close
    
    try: reveal_time = datetime.datetime.strptime(db_reveal, "%Y-%m-%d %H:%M:%S") if db_reveal else default_reveal
    except (ValueError, TypeError): reveal_time = default_reveal
    
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
            
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # Use UTC-aware comparison so the deadline is correct regardless of server timezone.
    # close_time and reveal_time are naive (stored without tz) — treat them as UTC
    # to match the UTC-aware now_utc used everywhere else in the app.
    _now_for_check = datetime.datetime.now(datetime.timezone.utc)
    def _naive_to_utc(dt):
        if dt is None: return None
        if dt.tzinfo is None: return dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    is_accepting_entries = close_time and _now_for_check < _naive_to_utc(close_time)
    is_pre_reveal = reveal_time and _now_for_check < _naive_to_utc(reveal_time)
    show_real = True 
    
    raw_rules = get_config(tid, 'rules', DEFAULT_RULES)
    rules_text = raw_rules if raw_rules and raw_rules.strip() else DEFAULT_RULES
    public_dns = fetch_dns_from_sheet(tid)
    
    public_logo = fetch_logo_from_sheet(tid)
    logo_rendered = False
    
    if public_logo:
        try:
            st.markdown(f"<div style='text-align: center; padding-bottom: 10px;'><img src='{safe_url(public_logo)}' style='max-width: 250px; max-height: 150px; width: 100%; object-fit: contain;'></div>", unsafe_allow_html=True)
            logo_rendered = True
        except Exception: pass 
            
    if not logo_rendered:
        st.markdown(f"<h1 style='text-align: center; padding-bottom: 10px;'>🏆 {html.escape(str(tnm).split('(')[0])}</h1>", unsafe_allow_html=True)
    
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
        backup_str = fetch_field_backup_from_sheet(tid)
        formatted_field = [x.strip() for x in backup_str.split(',')] if backup_str else []
        
    valid_p = [p.split(" (Top 20")[0] for p in formatted_field]
    full_df, total_count = get_clean_entries(tid, True, valid_players=valid_p, dns_players=public_dns.split(","))
    
    raw_token = st.query_params.get("magic_token")
    magic_token = raw_token[0] if isinstance(raw_token, list) else raw_token
    
    if magic_token and not st.session_state.get(f"auth_email_{tid}"):
        try:
            sb = get_supabase()
            now_str = now_utc.isoformat()
            
            res = sb.table('magic_links') \
                .update({'used_at': now_str}) \
                .eq('token', str(magic_token)) \
                .eq('tournament_id', str(tid)) \
                .is_('used_at', 'null') \
                .gte('expires_at', now_str) \
                .execute()
            
            if res.data and len(res.data) > 0:
                st.session_state[f"auth_email_{tid}"] = res.data[0].get('email', '')
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
                st.error("🚨 This secure edit link has expired, been used, or is invalid. Please request a new one.")
        except Exception as e:
            st.session_state.setdefault("api_log", []).append(f"Magic Link Error UI: {type(e).__name__} - {e}")

    auth_email_dec = st.session_state.get(f"auth_email_{tid}", "")
    is_authenticated = bool(auth_email_dec)

    current_tab = 0
    
    with sel[current_tab]: 
        if is_pre_reveal:
            calculate_leaderboard(tid, tnm, t_start, par_override=current_par_override, header_only=True, hide_title=True, search_query="")
            st.info(f"🔒 **Tournament Picks are Hidden**\n\nThe public leaderboard and all player entries will be revealed on **{reveal_time_str_ui}**.")
            
            if close_time:
                close_ts = int(close_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
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
                    <p style='font-size: 16px;'>We've securely saved your entry and sent a confirmation email to <b>{html.escape(st.session_state.get(f'success_email_{tid}', ''))}</b>.</p>
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
                            final_payment = form_payment
                            
                            with st.spinner("Locking in your team..."):
                                payload = {
                                    'name': form_name, 'email': form_email, 'payment_method': final_payment,
                                    'tie_breaker': form_tie, 'picks': clean_picks
                                }
                                status = append_entry_to_sheet(tid, payload)
                                
                                if status == "DUPLICATE":
                                    st.warning("⚠️ You have already submitted this exact team. We won't record a duplicate.")
                                    st.session_state[f"last_sub_{tid}"] = None
                                elif status is True:
                                    log_to_sheet("NEW ENTRY", f"Team submitted by {form_name}")
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
                    
                    update_submitted = st.form_submit_button("Update Team", type="primary")
                    
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
                            final_edit_payment = edit_payment
                            
                            with st.spinner("Updating your team..."):
                                payload = {
                                    'name': edit_name, 'email': edit_email, 'payment_method': final_edit_payment,
                                    'tie_breaker': edit_tie, 'picks': clean_picks
                                }
                                if update_specific_entry(tid, row_data['Sheet_Row'], payload):
                                    log_to_sheet("ENTRY UPDATED", f"Team updated by {edit_name}")
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
                        <p style='font-size: 16px;'>We've securely saved your changes and sent a new confirmation email to <b>{html.escape(st.session_state.get(f'edit_success_email_{tid}', ''))}</b>.</p>
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
                        if st.button("🔒 Logout of secure session"):
                            st.session_state[f"auth_email_{tid}"] = ""
                            st.rerun()
                    else:
                        email_input = st.text_input("Email Address:", placeholder="e.g., name@example.com")
                    
                if email_input:
                    user_df, _ = get_clean_entries(tid, True, valid_players=valid_p, dns_players=public_dns.split(","), email_filter=email_input)
                    if not user_df.empty:
                        st.success(f"Found {len(user_df)} team(s) linked to this email.")
                        
                        if not is_authenticated and is_accepting_entries:
                            if st.button("📧 Send Secure Edit Link for All Entries", type="primary", width="stretch"):
                                with st.spinner("Sending secure link to your email..."):
                                    if send_magic_link_email(email_input, tnm, tid, public_logo):
                                        st.success("✅ Secure edit link sent! Please check your email inbox to unlock your entries.")
                                    else:
                                        st.error("🚨 Failed to send email. Please check configuration.")
                                        
                        for idx, r in user_df.iterrows():
                            with st.container():
                                st.markdown("---")
                                name_col_val = next((c for c in user_df.columns if c not in ['Sheet_Row', 'Email', 'Payment Method', 'Paid', 'Tie', 'Picks', 'Field Status', 'SheetWarnings', 'Tie Breaker'] and 'pick' not in c.lower()), user_df.columns[2])
                                p_list = [str(r[c]) for c in user_df.columns if 'pick' in str(c).lower() and pd.notna(r[c]) and str(r[c]).strip() != ""]
                                tie_val = int(safe_int(r.get('Tie Breaker', r.get('Tie', 0))))
                                c1, c2 = st.columns([3, 1])
                                with c1:
                                    if is_authenticated:
                                        st.markdown(f"**Team Name:** {html.escape(str(r[name_col_val]))}  |  **Tie Breaker:** `{tie_val}`\n\n**Picks:** {html.escape(', '.join(p_list))}")
                                        if not str(r.get("Field Status", "✅ All Valid")).startswith("✅"): st.error(f"🚨 Roster Issue: {html.escape(str(r.get('Field Status')))}")
                                    else:
                                        st.markdown(f"**Team Name:** {html.escape(str(r[name_col_val]))}\n\n🔒 *Picks and Tie Breaker are hidden. Click the button above to send a secure link to your email to view or edit this entry.*")
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
                st.dataframe(full_df.drop(columns=['Sheet_Row', 'Email', 'Payment Method', 'Paid', '_Original_Name'], errors='ignore'), width="stretch", hide_index=True)
        current_tab += 1
        
        with sel[current_tab]:
            st.markdown("### 📊 The Hive Mind: Pick Analytics")
            st.caption("See who the crowd is backing! This data is generated from all locked-in teams.")
            
            if total_count > 0 and not full_df.empty:
                pick_cols = [c for c in full_df.columns if 'pick' in c.lower()]
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
        
        valid_tokens = [str(ADMIN_PASSWORD)]
        if "admin_token" in st.secrets:
            valid_tokens.append(str(st.secrets["admin_token"]))
            
        is_token_valid = str(url_token).strip() in valid_tokens if url_token else False
        
        if is_token_valid:
            st.session_state["admin_auth"] = True
            
        if not st.session_state.get("admin_auth", False):
            if url_token and not is_token_valid:
                st.warning("⚠️ Invalid token provided in URL.")
                
            pwd_input = st.text_input("Password", type="password")
            if pwd_input != ADMIN_PASSWORD:
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
        
        ak = st.secrets.get("api_keys")
        if not ak or not isinstance(ak, list): 
            st.error("No explicit `api_keys` array found in secrets!")
            st.stop()
            
        active = st.selectbox("API Key", ak, index=ak.index(settings.get("active_api_key", ak[0])) if settings.get("active_api_key") in ak else 0)
        st.caption(f"📊 **Quota:** {settings.get(f'quota_{active}', 'Checking (Will update on next API pull)...')}")
        
        use_manual = st.checkbox("☑️ Manually enter Tournament ID")
        if use_manual:
            t_id, t_key, t_start = st.text_input("Tournament ID", value="832"), st.text_input("Tournament Name", value="The Masters"), None
        else:
            c_tour, c_year = st.columns(2)
            with c_tour: tour_val = st.selectbox("Tour", ["2 - PGA Tour", "1 - DP World Tour"])
            with c_year: year_val = st.selectbox("Season", [2025, 2026, 2027], index=1)
            
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
                t_key = st.selectbox("Tournament", list(fixtures.keys()))
                t_id, t_start = fixtures[t_key]['id'], fixtures[t_key]['start']
            
        if t_id:
            st.session_state['current_t_id'] = t_id

            st.subheader("🌍 OWGR")
            if st.button("📥 Force Refresh Top 20", type="primary"): 
                get_top_20_players.clear()  
                get_top_20_players()        
                log_to_sheet("ADMIN ACTION", "Forced a manual refresh of the OWGR Top 20")
                st.success("✅ Fresh OWGR Top 20 pulled and cached!")
                time.sleep(1)
                st.rerun()                  
            
            st.subheader("📋 Supabase Connection")
            st.success("✅ Connected to Supabase!")
            
            current_par_override = fetch_par_from_sheet(t_id)
            current_hide_tt = fetch_hide_tt_from_sheet(t_id)

            try:
                pconf = json.loads(fetch_payout_config_from_sheet(t_id))
                if "payout_confirmed" in pconf:
                    settings[f"payout_confirmed_{t_id}"] = pconf.get("payout_confirmed", False)
                    settings[f"payout_1_{t_id}"] = pconf.get("p1", 0)
                    settings[f"payout_2_{t_id}"] = pconf.get("p2", 0)
                    settings[f"payout_3_{t_id}"] = pconf.get("p3", 0)
                    settings[f"payout_pot_{t_id}"] = pconf.get("pot", 0)
            except json.JSONDecodeError: pass
            
            st.subheader("🖼️ Branding")
            current_logo = fetch_logo_from_sheet(t_id)
            new_logo = st.text_input("Logo URL", value=current_logo)
            
            if new_logo != current_logo:
                if save_logo_to_sheet(t_id, new_logo):
                    st.success("✅ Logo permanently saved to Supabase!")
                    st.rerun()
                else:
                    st.error("🚨 Failed to save logo.")
                    
            if new_logo: st.image(new_logo, width="stretch")
            
            st.subheader("🚑 Smart DNS Manager")
            current_dns = fetch_dns_from_sheet(t_id)
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
                if save_dns_to_sheet(t_id, ", ".join(dns_list)): st.rerun()
                else: st.error("Failed to save.")
            settings[f"dns_{t_id}"] = current_dns
            
            st.markdown("---")
            lb_check, _, _, _, _ = fetch_smart_leaderboard(t_id)
            
            if check_api_par_warning(lb_check) and current_par_override == 0:
                st.error("🚨 **API Math Error Detected!**\nThe provider's overall scores do not match their individual round scores. Please enter the correct **Manual Par** below.")
            
            st.caption(f"🔍 Calc. Course Par (R1): **{get_calculated_par(lb_check)}**")
            
            new_par = st.number_input("Manual Par Override (e.g. 70, 71, 72)", value=current_par_override)
            if new_par != current_par_override:
                if save_par_to_sheet(t_id, new_par):
                    st.success(f"✅ Par permanently saved as {new_par}!")
                    st.rerun()
                else:
                    st.error("🚨 Failed to save Par.")

            st.markdown("---")
            st.caption("🕒 **Tee Time Visibility**")
            new_hide_tt = st.checkbox("🚫 Hide API Tee Times (Force 'Waiting...')", value=current_hide_tt)
            if new_hide_tt != current_hide_tt:
                if save_hide_tt_to_sheet(t_id, new_hide_tt):
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
            try: default_close_dt = (_parse_t_start(t_start).replace(hour=5, minute=0, second=0) if _parse_t_start(t_start) else datetime.datetime.now(datetime.timezone.utc))
            except (ValueError, TypeError): default_close_dt = datetime.datetime.now(datetime.timezone.utc)
            
            db_close_admin = fetch_close_time_from_db(t_id)
            try: current_close_dt = datetime.datetime.strptime(db_close_admin, "%Y-%m-%d %H:%M:%S") if db_close_admin else default_close_dt
            except (ValueError, TypeError): current_close_dt = default_close_dt
            
            c_d2, c_t2 = st.columns(2)
            with c_d2: close_d = st.date_input("Close Date", value=current_close_dt.date())
            with c_t2: close_t = st.time_input("Close Time", value=current_close_dt.time())
            
            st.write("🔒 **Public Reveal Time**")
            try: default_reveal_dt = (_parse_t_start(t_start).replace(hour=5, minute=0, second=0) if _parse_t_start(t_start) else datetime.datetime.now(datetime.timezone.utc))
            except (ValueError, TypeError): default_reveal_dt = datetime.datetime.now(datetime.timezone.utc)
            
            db_reveal_admin = fetch_reveal_time_from_db(t_id)
            try: current_reveal_dt = datetime.datetime.strptime(db_reveal_admin, "%Y-%m-%d %H:%M:%S") if db_reveal_admin else default_reveal_dt
            except (ValueError, TypeError): current_reveal_dt = default_reveal_dt
            
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
                
            saved_short_url = fetch_short_url_from_sheet(t_id)
            
            if saved_short_url:
                st.success("✨ Anonymous Short Link Ready!")
                st.code(saved_short_url)
                if st.button("🗑️ Reset Short Link"): 
                    save_short_url_to_sheet(t_id, "")
                    st.rerun()
            else:
                if st.button("✨ Generate Anonymous Short Link"):
                    with st.spinner("Generating short link..."):
                        short_link = generate_short_link(p_url)
                        if short_link: 
                            save_short_url_to_sheet(t_id, short_link)
                            st.success("Created!")
                            st.rerun()
                        else: 
                            st.error("Failed to generate link.")
                            
            if st.button("🔄 Clear Cache"): get_api_cache().clear(); st.cache_data.clear(); get_raw_sheet_data.clear(); st.rerun()

    if t_id:
        admin_logo = fetch_logo_from_sheet(t_id)
        t_name_display = t_key.split('(')[0].strip() if t_key else "Tournament"
        logo_rendered = False
        
        if admin_logo:
            try:
                st.markdown(f"<div style='text-align: center; padding-bottom: 10px;'><img src='{safe_url(admin_logo)}' style='max-width: 250px; max-height: 150px; width: 100%; object-fit: contain;'></div>", unsafe_allow_html=True)
                logo_rendered = True
            except Exception: pass 

        if not logo_rendered:
            st.markdown(f"<h1 style='text-align: center; padding-bottom: 10px;'>🏆 {html.escape(str(t_name_display))}</h1>", unsafe_allow_html=True)

        t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11 = st.tabs(["🏆 Leaderboard", "📝 Entries", "💵 Financials", "⛳ Official PGA Scores", "💾 Export", "📡 API Status", "💻 Raw JSON", "📜 Rules", "🏌️ Field", "📈 Analytics", "🔀 Aliases"])
        
        lb_data, next_f, last_f, full_data, data_source = fetch_smart_leaderboard(t_id)
        
        raw_results = get_safe_api_results(full_data)
        tourney_data = raw_results.get('tournament') or {}
        live_details = tourney_data.get('live_details') or {}
        t_status = normalize_pga_status(live_details.get('status', ''))
        is_tournament_started = t_status != 'pre'
        
        top20_cached_admin = get_top_20_players()
        
        if not is_tournament_started:
            formatted_field_admin = get_formatted_field(t_id, top20_cached_admin)
        else:
            backup_str = fetch_field_backup_from_sheet(t_id)
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
                                    else: log_to_sheet("ADMIN ACTION", f"Marked {'Paid' if row['Paid'] else 'Unpaid'} for {row.get('_Original_Name', 'Unknown')}")
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
                    st.markdown(f"**Editing:** `{html.escape(str(target_name))}`")
                    
                    c1, c2 = st.columns([2, 1])
                    with c1: 
                        admin_clean_opts = [p for p in pay_opts if p != "Select..."]
                        curr_pay = target_row.get('Payment Method')
                        new_pay = st.selectbox("Payment Method:", admin_clean_opts, index=admin_clean_opts.index(curr_pay) if curr_pay in admin_clean_opts else 0)
                    with c2:
                        st.markdown("<br>", unsafe_allow_html=True) 
                        if st.button("💾 Update Method", type="primary", width="stretch"):
                            with st.spinner("Updating DB..."):
                                if update_single_cell_in_sheet(t_id, target_row['Sheet_Row'], "Payment Method", new_pay):
                                    log_to_sheet("ADMIN ACTION", f"Changed payment method to '{new_pay}' for {target_name}")
                                    st.toast("✅ Payment updated successfully!")
                                    time.sleep(1); st.rerun() 
                                else: st.error("🚨 Failed to update.")
                                
                    st.markdown("**Edit Players:**")
                    pick_cols_target = [c for c in admin_df.columns if 'pick' in c.lower() and pd.notna(target_row[c])]
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
                                actual_pick_headers = [c for c in admin_df.columns if c.strip().lower().startswith("pick") or "player pick" in c.lower()][:5]
                                if len(actual_pick_headers) == 5:
                                    for i in range(5):
                                        if not update_single_cell_in_sheet(t_id, target_row['Sheet_Row'], actual_pick_headers[i], clean_new_picks[i]): success = False
                                    if success:
                                        log_to_sheet("ADMIN ACTION", f"Manually swapped picks for {target_name}")
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
                
                saved_bals_str = fetch_fin_balances_from_sheet(t_id)
                try: saved_bals = json.loads(saved_bals_str)
                except json.JSONDecodeError: saved_bals = {}

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
                        if save_fin_balances_to_sheet(t_id, new_bals): 
                            log_to_sheet("ADMIN ACTION", "Saved Bank Account Reconciliation Balances")
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
                        if save_payout_config_to_sheet(t_id, payout_json):
                            log_to_sheet("ADMIN ACTION", f"Updated Payouts (Total Pot: ${total_pot:,.2f})")
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
                st.download_button("⬇️ Download Top-Level Results (CSV)", export_final.to_csv(index=False).encode('utf-8'), f"{html.escape(str(t_key)).replace(' ', '_').split('(')[0]}_Leaderboard.csv", "text/csv")
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
                    res = get_supabase().table('app_logs').select('*').eq('tournament_id', str(t_id)).order('created_at', desc=True).limit(50).execute()
                    if res.data: st.dataframe(pd.DataFrame(res.data), width="stretch", hide_index=True)
                    else: st.info("Log table is empty.")
                except Exception as e: st.error(f"Could not load logs: {type(e).__name__} - {e}")
                    
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
                        log_to_sheet("ADMIN ACTION", "Forced rebuild of frozen field and Top 20 flags")
                    st.success("✅ Database rebuilt and permanently frozen! Please click the 'Clear Cache' button in the sidebar.")
            else:
                st.subheader("🚨 Smart Field Alerts")
                st.caption("Detect if players have been added or removed since your last check.")
                raw_field = get_raw_entry_list(t_id)
                
                baseline_str = fetch_alerted_field_from_sheet(t_id)
                baseline_field = [p.strip() for p in baseline_str.split(",")] if baseline_str else []
                
                if not baseline_str and raw_field:
                    if st.button("Set Initial Baseline", type="primary"):
                        save_alerted_field_to_sheet(t_id, ",".join(raw_field))
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
                                try: current_close_dt = datetime.datetime.strptime(settings.get(f"close_time_{t_id}"), "%Y-%m-%d %H:%M:%S") if settings.get(f"close_time_{t_id}") else datetime.datetime.now(datetime.timezone.utc)
                                except (ValueError, TypeError): current_close_dt = datetime.datetime.now(datetime.timezone.utc)
                                
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
                                        
                                        user_picks = [str(row[c]).split(" (Top 20")[0].strip() for c in admin_df.columns if 'pick' in c.lower() and pd.notna(row[c])]
                                        affected_removals = [p for p in removed_players if p in user_picks]
                                        
                                        if affected_removals or added_players:
                                            if send_field_change_email(user_email, user_name, affected_removals, added_players, t_key.split('(')[0], t_id, close_time_str_admin, admin_logo):
                                                emails_sent += 1
                                                alerted_emails.add(user_email)
                                                time.sleep(1.5)
                                                
                                save_alerted_field_to_sheet(t_id, ",".join(raw_field))
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
            
            current_aliases = fetch_aliases_from_sheet(t_id)
            alias_list = [a.strip() for a in current_aliases.split(",") if a.strip()]
            
            st.markdown("### Current Aliases")
            if alias_list:
                for idx, a in enumerate(alias_list):
                    if ':' in a:
                        w, c = a.split(':')
                        colA, colB = st.columns([4, 1])
                        with colA: st.markdown(f"**{html.escape(w.strip().title())}** ➡️ mapped to API name **{html.escape(c.strip().title())}**")
                        with colB:
                            if st.button("❌ Remove", key=f"rm_alias_{idx}"):
                                alias_list.pop(idx)
                                save_aliases_to_sheet(t_id, ",".join(alias_list))
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
                        if save_aliases_to_sheet(t_id, ",".join(alias_list)):
                            st.success("Alias added successfully!")
                            st.rerun()
                        else: st.error("🚨 Failed to save alias.")
                else:
                    st.error("🚨 Please fill out both names.")
