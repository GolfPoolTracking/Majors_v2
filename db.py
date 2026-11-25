"""Supabase data access: tournament config, entries and app logs."""
import datetime
import json

import pandas as pd
import streamlit as st
from supabase import Client, create_client

@st.cache_resource
def get_supabase() -> Client:
    """Authenticates and returns a persistent Supabase client."""
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])

def get_config(t_id, column, default=""):
    try:
        res = get_supabase().table('golf_tournament_configs').select(column).eq('tournament_id', str(t_id)).execute()
        if res.data and len(res.data) > 0:
            return res.data[0].get(column) or default
        return default
    except Exception as e:
        print(f"DB Read Error ({column}): {e}")
        return default

def update_config(t_id, column, value, t_name=None):
    try:
        sb = get_supabase()
        existing = sb.table('golf_tournament_configs').select('tournament_id').eq('tournament_id', str(t_id)).execute()
        
        update_data = {column: value}
        if t_name and str(t_name).strip(): 
            update_data['tournament_name'] = str(t_name).strip()
            
        if existing.data:
            sb.table('golf_tournament_configs').update(update_data).eq('tournament_id', str(t_id)).execute()
        else:
            update_data['tournament_id'] = str(t_id)
            sb.table('golf_tournament_configs').insert(update_data).execute()
        return True
    except Exception as e:
        print(f"DB Write Error ({column}): {e}")
        return False

def get_app_setting(key, default=None):
    try:
        res = get_supabase().table('golf_app_settings').select('value').eq('key', key).execute()
        if res.data: return res.data[0].get('value', default)
        return default
    except Exception as e:
        print(f"DB Read Error (app setting {key}): {e}")
        return default

def set_app_setting(key, value):
    try:
        get_supabase().table('golf_app_settings').upsert({'key': key, 'value': value, 'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}).execute()
        return True
    except Exception as e:
        print(f"DB Write Error (app setting {key}): {e}")
        return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_active_tournament():
    """The pinned tournament as {"id", "name", "start"}, or None if none is pinned."""
    val = get_app_setting('active_tournament')
    return val if isinstance(val, dict) and val.get('id') else None

def save_active_tournament(t_id, t_name, t_start):
    if set_app_setting('active_tournament', {'id': str(t_id), 'name': t_name, 'start': t_start}):
        fetch_active_tournament.clear()
        return True
    return False

def log_event(event_type, message):
    try:
        t_id = st.session_state.get('current_t_id', 'General')
        get_supabase().table('golf_app_logs').insert({
            'tournament_id': str(t_id),
            'event_type': event_type,
            'message': message
        }).execute()
    except Exception as e: 
        print(f"DB Error (log_to_db): {e}")

def save_api_backup_to_db(t_id, full_data):
    t_name = ""
    try:
        t_name = full_data.get('results', {}).get('tournament', {}).get('name', '')
    except Exception:
        pass
    return update_config(t_id, 'api_backup', full_data, t_name=t_name)

def fetch_api_backup_from_db(t_id):
    data = get_config(t_id, 'api_backup', {})
    return data if data else None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_close_time_from_db(t_id):
    return get_config(t_id, 'close_time', "")

@st.cache_data(ttl=300, show_spinner=False)
def fetch_reveal_time_from_db(t_id):
    return get_config(t_id, 'reveal_time', "")

@st.cache_data(ttl=300, show_spinner=False)
def fetch_field_backup_from_db(t_id):
    return get_config(t_id, 'field_backup', "")

def save_field_backup_to_db(t_id, field_string):
    if update_config(t_id, 'field_backup', field_string):
        fetch_field_backup_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_dns_from_db(t_id):
    return get_config(t_id, 'dns_list', "")

def save_dns_to_db(t_id, dns_string):
    if update_config(t_id, 'dns_list', dns_string):
        fetch_dns_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_short_url_from_db(t_id):
    return get_config(t_id, 'short_url', "")

def save_short_url_to_db(t_id, short_url):
    if update_config(t_id, 'short_url', short_url):
        fetch_short_url_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_fin_balances_from_db(t_id):
    val = get_config(t_id, 'fin_balances', {})
    return json.dumps(val) if isinstance(val, dict) else val

def save_fin_balances_to_db(t_id, json_str):
    try:
        val = json.loads(json_str)
        if update_config(t_id, 'fin_balances', val):
            fetch_fin_balances_from_db.clear()
            return True
        return False
    except Exception: return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_alerted_field_from_db(t_id):
    return get_config(t_id, 'alerted_field', "")

def save_alerted_field_to_db(t_id, field_string):
    if update_config(t_id, 'alerted_field', field_string):
        fetch_alerted_field_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_par_from_db(t_id):
    try: return int(get_config(t_id, 'manual_par', 0))
    except Exception: return 0

def save_par_to_db(t_id, par_value):
    if update_config(t_id, 'manual_par', par_value):
        fetch_par_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_logo_from_db(t_id):
    return get_config(t_id, 'logo_url', "")

def save_logo_to_db(t_id, logo_url):
    if update_config(t_id, 'logo_url', logo_url):
        fetch_logo_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_aliases_from_db(t_id):
    return get_config(t_id, 'name_aliases', "")

def save_aliases_to_db(t_id, alias_string):
    if update_config(t_id, 'name_aliases', alias_string):
        fetch_aliases_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_hide_tt_from_db(t_id):
    return get_config(t_id, 'hide_tee_times', False)

def save_hide_tt_to_db(t_id, hide_tt_bool):
    if update_config(t_id, 'hide_tee_times', hide_tt_bool):
        fetch_hide_tt_from_db.clear()
        return True
    return False

@st.cache_data(ttl=300, show_spinner=False)
def fetch_payout_config_from_db(t_id):
    val = get_config(t_id, 'payout_config', {})
    return json.dumps(val) if isinstance(val, dict) else "{}"

def save_payout_config_to_db(t_id, json_str):
    try:
        val = json.loads(json_str)
        if update_config(t_id, 'payout_config', val):
            fetch_payout_config_from_db.clear()
            return True
        return False
    except Exception: return False

@st.cache_data(ttl=120, show_spinner=False)
def get_entries_df(t_id):
    if not t_id: return pd.DataFrame()
    try:
        res = get_supabase().table('golf_tournament_standings').select('*').eq('tournament_id', str(t_id)).order('entry_id').execute()
        if not res.data: return pd.DataFrame()
        
        df = pd.DataFrame(res.data)
        
        df = df.rename(columns={
            'entry_id': 'Sheet_Row',
            'participant_name': 'Name',
            'email': 'Email',
            'payment_method': 'Payment Method',
            'tie_breaker': 'Tie Breaker',
            'paid': 'Paid'
        })
        
        if 'picks' in df.columns:
            for i in range(5):
                df[f'Pick {i+1}'] = df['picks'].apply(lambda x: x[i] if isinstance(x, list) and len(x) > i else "")
            
        return df.copy()
    except Exception as e:
        print(f"DB Read Error (tournament_standings view): {e}")
        return pd.DataFrame()

def insert_entry(t_id, entry_row):
    try:
        data = {
            'tournament_id': str(t_id),
            'name': entry_row[1],
            'email': entry_row[2],
            'payment_method': entry_row[3],
            'tie_breaker': int(entry_row[4]),
            'pick_1': entry_row[5],
            'pick_2': entry_row[6],
            'pick_3': entry_row[7],
            'pick_4': entry_row[8],
            'pick_5': entry_row[9],
            'paid': False
        }
        get_supabase().table('golf_entries').insert(data).execute()
        get_entries_df.clear() # Instantly update cache
        return True
    except Exception as e: 
        print(f"DB Error (append_entry): {e}")
        return False

def update_entry_field(t_id, row_number, header_name, new_value):
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
            get_supabase().table('golf_entries').update({col: new_value}).eq('id', row_number).execute()
            get_entries_df.clear()
            return True
        return False
    except Exception as e:
        print(f"DB Error (update_single_cell): {e}")
        return False

def update_specific_entry(t_id, row_index, entry_row):
    try:
        data = {
            'name': entry_row[1],
            'email': entry_row[2],
            'payment_method': entry_row[3],
            'tie_breaker': int(entry_row[4]),
            'pick_1': entry_row[5],
            'pick_2': entry_row[6],
            'pick_3': entry_row[7],
            'pick_4': entry_row[8],
            'pick_5': entry_row[9]
        }
        get_supabase().table('golf_entries').update(data).eq('id', row_index).execute()
        get_entries_df.clear()
        return True
    except Exception as e: 
        print(f"DB Error (update_specific_entry): {e}")
        return False

def update_paid_status(t_id, row_index, is_paid):
    try:
        get_supabase().table('golf_entries').update({'paid': is_paid}).eq('id', row_index).execute()
        get_entries_df.clear()
        return True
    except Exception as e: 
        print(f"DB Error (update_paid_status): {e}")
        return False
