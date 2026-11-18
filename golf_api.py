"""RapidAPI golf data: smart polling, key rotation, caching and DB fallback."""
import datetime
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import pytz
import requests
import streamlit as st

from db import (
    fetch_api_backup_from_db,
    fetch_field_backup_from_db,
    log_event,
    save_api_backup_to_db,
    save_field_backup_to_db,
)
from emails import _send_email_async
from scoring import mark_no_shows_withdrawn, resolve_current_round, safe_int

@st.cache_resource
def get_api_cache(): return {}

@st.cache_resource
def get_settings_cache(): return {}

def send_admin_api_alert(url, error_details):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    
    settings = get_settings_cache()
    last_alert = settings.get("last_api_alert_time")
    now = datetime.datetime.now()
    
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
                <p style="margin: 0 0 10px 0;"><b>Endpoint:</b><br>{url}</p>
                <p style="margin: 0;"><b>Last Error:</b><br>{error_details}</p>
            </div>
            
            <p style="font-size: 15px;">Please log in to your RapidAPI dashboard to check your usage and update your keys in the Streamlit secrets if necessary.</p>
            <p style="font-size: 12px; color: #7f8c8d; margin-top: 30px;"><i>Note: To prevent spam, this alert is limited to a maximum of 1 email per hour.</i></p>
          </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))
        
        threading.Thread(target=_send_email_async, args=(msg, sender, pwd)).start()
        
        settings["last_api_alert_time"] = now
        return True
    except Exception as e:
        print(f"Email Error (send_admin_api_alert): {e}")
        return False

def intelligent_api_call(url, trigger_reason="Unknown"):
    settings = get_settings_cache()
    all_keys = [k for k in st.secrets.keys() if "api" in k.lower() and "key" in k.lower()]
    if not all_keys: return None 
    current_active = settings.get("active_api_key", all_keys[0])
    
    last_error = "Unknown Error"
    
    for key_name in [current_active] + [k for k in all_keys if k != current_active]:
        try:
            resp = requests.get(url, headers={"X-RapidAPI-Key": st.secrets[key_name], "X-RapidAPI-Host": "golf-leaderboard-data.p.rapidapi.com"}, timeout=15)
            if resp.status_code == 200:
                settings["active_api_key"] = key_name
                quota = resp.headers.get("x-ratelimit-requests-remaining") or resp.headers.get("X-RateLimit-Requests-Remaining")
                if quota is not None:
                    settings[f"quota_{key_name}"] = str(quota)
                
                endpoint = url.split(".com/")[-1] 
                log_event("API CALL", f"Success ({endpoint}) | Reason: {trigger_reason} | Quota Left: {quota}")
                
                return resp.json()
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e: 
            last_error = str(e)
            continue
            
    log_event("API CRITICAL", f"All keys failed for {url}. Last Error: {last_error}")
    send_admin_api_alert(url, last_error)
    return None 

@st.cache_data(ttl=86400, show_spinner=False)
def get_fixture_list(tour_id, year):
    data = intelligent_api_call(f"https://golf-leaderboard-data.p.rapidapi.com/fixtures/{tour_id}/{year}", "Fixture Fetch")
    if data: return {f"{f.get('name')} ({f.get('start_date')[:10]})": {"id": f.get('id'), "start": f.get('start_date')} for f in data.get('results', [])}
    return {}

@st.cache_data(ttl=86400, show_spinner=False)
def get_top_20_players():
    data = intelligent_api_call("https://golf-leaderboard-data.p.rapidapi.com/world-rankings", "OWGR Fetch")
    if data:
        raw = data.get('results', [])
        rank_data = raw.get('rankings', []) or raw.get('world_rankings', []) if isinstance(raw, dict) else raw
        return {p.get('player_name', '').strip().lower(): i + 1 for i, p in enumerate(rank_data[:20])}
    return {}

def get_raw_entry_list(selected_t_id):
    cache = get_api_cache()
    cache_key = f"entry_list_{selected_t_id}"
    now = datetime.datetime.now()
    
    if cache_key in cache and now < cache[cache_key]['expire']: 
        return cache[cache_key]['data']
        
    data = intelligent_api_call(f"https://golf-leaderboard-data.p.rapidapi.com/entry-list/{selected_t_id}", "Entry List Fetch")
    
    if data and data.get('results'):
        clean_list = [f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() for p in data['results'].get('entry_list', [])]
        
        expire_time = now + datetime.timedelta(hours=1) if clean_list else now + datetime.timedelta(minutes=15)
        cache[cache_key] = {'data': clean_list, 'expire': expire_time}
        return clean_list
        
    if cache_key in cache: return cache[cache_key]['data']
    
    cache[cache_key] = {'data': [], 'expire': now + datetime.timedelta(minutes=5)}
    return []

def get_formatted_field(selected_t_id, top_20_dict):
    raw_field = get_raw_entry_list(selected_t_id)
    
    if not raw_field and selected_t_id:
        backup_str = fetch_field_backup_from_db(selected_t_id)
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
        backup_str = fetch_field_backup_from_db(selected_t_id)
        current_str = ",".join(formatted_field)
        if backup_str != current_str: save_field_backup_to_db(selected_t_id, current_str)
        
    return formatted_field

def fetch_smart_leaderboard(selected_t_id):
    cache = get_api_cache()
    now = datetime.datetime.now()
    cache_key = f"lb_{selected_t_id}"
    
    if cache_key in cache and now < cache[cache_key]["next_fetch_allowed"]:
        return cache[cache_key]["data"], cache[cache_key]["next_fetch_allowed"], cache[cache_key]["last_fetch"], cache[cache_key]["full_data"], "⚡ Active Memory Cache"
        
    data = intelligent_api_call(f"https://golf-leaderboard-data.p.rapidapi.com/leaderboard/{selected_t_id}")
    if data and data.get('results'):
        lb_data = data.get('results', {}).get('leaderboard', [])
        
        raw_results = data.get('results') or {}
        tourney_data = raw_results.get('tournament') or {}
        live_details = tourney_data.get('live_details') or {}
        
        t_status = str(live_details.get('status', '')).lower()
        
        current_r = resolve_current_round(lb_data, live_details)
        
        players_in_curr_round = [p for p in lb_data if safe_int(p.get('current_round', 0)) == current_r and p.get('status', '').lower() not in ['cut', 'wd', 'dq', 'withdrawn', 'missed cut']]
        
        has_started_curr = any(safe_int(p.get('holes_played', 0)) > 0 or p.get('status', '').lower() in ['active', 'complete', 'completed'] for p in players_in_curr_round)
        
        if not has_started_curr and current_r > 1 and t_status not in ['completed', 'complete']:
            current_r -= 1
            t_status = 'endofday'
            live_details['current_round'] = current_r
            live_details['status'] = t_status
        else:
            live_details['current_round'] = current_r
        
        mark_no_shows_withdrawn(lb_data, current_r, t_status)
        
        mode = "⛳ Active Play"
        next_fetch = now + datetime.timedelta(minutes=60)
        
        try:
            tourney_tz_str = tourney_data.get('timezone') or 'America/New_York'
            tourney_tz = pytz.timezone(tourney_tz_str)
            now_utc = datetime.datetime.now(pytz.utc)
            now_tourney = now_utc.astimezone(tourney_tz)
            
            target_r = current_r if current_r > 0 else 1
            
            # Check if all players in target round are done, but API hasn't rolled status to endofday
            players_in_target = [p for p in lb_data if safe_int(p.get('current_round', 0)) == target_r and p.get('status', '').lower() not in ['cut', 'wd', 'dq', 'withdrawn', 'missed cut']]
            all_target_complete = len(players_in_target) > 0 and all(p.get('status', '').lower() in ['complete', 'completed'] for p in players_in_target)
            
            prev_min_tt_str = "23:59"
            min_tt_str, max_tt_str = "23:59", "00:00"
            
            for p in lb_data:
                for r in p.get('rounds', []):
                    r_num = safe_int(r.get('round_number', 0))
                    tt = r.get('tee_time_local', '')
                    if tt and ':' in tt:
                        if r_num == target_r:
                            if tt < min_tt_str: min_tt_str = tt
                            if tt > max_tt_str: max_tt_str = tt
                        elif r_num == target_r - 1:
                            if tt < prev_min_tt_str: prev_min_tt_str = tt
                            
            has_tee_times = (max_tt_str != "00:00" and min_tt_str != "23:59")
            
            if t_status in ['completed', 'complete']:
                next_fetch = now + datetime.timedelta(minutes=1440)
                mode = "🏁 Finished (24h)"
            elif t_status in ['suspended', 'endofday']:
                next_fetch = now + datetime.timedelta(minutes=60)
                mode = f"⏸️ {t_status.title()} (1h)"
            elif all_target_complete and t_status == 'inprogress':
                # 🚨 FAIL-SAFE 1: Round is done but API hasn't rolled over to next round yet
                next_fetch = now + datetime.timedelta(minutes=10)
                mode = f"⏳ API Lag: Waiting for R{target_r + 1} Rollover (10m)"
            elif not has_tee_times:
                # 🚨 FAIL-SAFE 2: No tee times yet, but it's the weekend
                if current_r >= 3 and t_status == 'inprogress':
                    start_polling_dt = None
                    if prev_min_tt_str != "23:59":
                        parts = prev_min_tt_str.split(':')
                        # Construct target time using the previous round's hour/minute
                        start_polling_dt = now_tourney.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
                        
                        # If that time is in the past, and it's > 6 hours ago, we are likely on the night before.
                        # Add a day so it sleeps until tomorrow morning.
                        if start_polling_dt < now_tourney:
                            if (now_tourney - start_polling_dt).total_seconds() > 21600: 
                                start_polling_dt += datetime.timedelta(days=1)
                                
                    if start_polling_dt and now_tourney < start_polling_dt:
                        # Sleep until the previous round's start time
                        wait_sec = (start_polling_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"😴 Sleep until R{target_r-1} start time ({prev_min_tt_str})"
                    else:
                        # We have crossed the threshold, aggressively poll for tee times
                        next_fetch = now + datetime.timedelta(minutes=10)
                        mode = f"⏳ API Lag: Awaiting R{target_r} Times (10m)"
                else:
                    next_fetch = now + datetime.timedelta(minutes=240)
                    mode = "😴 Waiting for Tee Times (4h)"
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
                    # 🚨 R1-3 LOGIC: Sleep until 4.5 hours after the LAST tee time
                    end_of_play_dt = last_tt_dt + datetime.timedelta(hours=4, minutes=30)
                    
                    if now_tourney < first_check_dt:
                        wait_sec = (first_check_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Wait for R{target_r} Start (+30m after 1st TT)"
                    elif now_tourney < end_of_play_dt:
                        wait_sec = (end_of_play_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"😴 Awaiting R{target_r} Finish (4.5h after last TT)"
                    else:
                        next_fetch = now + datetime.timedelta(minutes=30)
                        mode = f"⏳ Checking for R{target_r} Completion (30m)"
                else:
                    # 🚨 R4 LOGIC: Live Scoring (10 min after last tee time -> 5m loops)
                    live_scoring_start_dt = last_tt_dt + datetime.timedelta(minutes=10)
                    
                    if now_tourney < first_check_dt:
                        wait_sec = (first_check_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Wait for R{target_r} Start (+30m after 1st TT)"
                    elif now_tourney < live_scoring_start_dt:
                        wait_sec = (live_scoring_start_dt - now_tourney).total_seconds()
                        next_fetch = now + datetime.timedelta(seconds=max(60, wait_sec))
                        mode = f"⏳ Wait for R{target_r} Last TT (+10m)"
                    else:
                        next_fetch = now + datetime.timedelta(minutes=5)
                        mode = f"🔥 R{target_r} Live Scoring (5m)"

        except Exception as e:
            log_event("TZ/TT ERROR", str(e))
            next_fetch = now + datetime.timedelta(minutes=60)
            mode = "⛳ Active Play (Fallback 1h)"
        
        cache[cache_key] = {"data": lb_data, "last_fetch": now, "next_fetch_allowed": next_fetch, "mode": mode, "full_data": data}
        if selected_t_id: save_api_backup_to_db(selected_t_id, data)
        return lb_data, next_fetch, now, data, "📡 Live API"
        
    if selected_t_id:
        backup_data = fetch_api_backup_from_db(selected_t_id)
        if backup_data and backup_data.get('results'):
            lb_data = backup_data.get('results', {}).get('leaderboard', [])
            live_details = backup_data.get('results', {}).get('tournament', {}).get('live_details', {})
            t_status = str(live_details.get('status', '')).lower()
            current_r = live_details.get('current_round') or max([safe_int(p.get('current_round', 0)) for p in lb_data] + [0])
            
            players_in_curr_round = [p for p in lb_data if safe_int(p.get('current_round', 0)) == current_r and p.get('status', '').lower() not in ['cut', 'wd', 'dq', 'withdrawn', 'missed cut']]
            has_started_curr = any(safe_int(p.get('holes_played', 0)) > 0 or p.get('status', '').lower() == 'active' for p in players_in_curr_round)
            if not has_started_curr and current_r > 1 and t_status not in ['completed', 'complete']:
                current_r -= 1
                t_status = 'endofday'
                live_details['current_round'] = current_r
                live_details['status'] = t_status
            
            mark_no_shows_withdrawn(lb_data, current_r, t_status)
                        
            next_fetch = now + datetime.timedelta(minutes=5) 
            cache[cache_key] = {"data": lb_data, "last_fetch": now, "next_fetch_allowed": next_fetch, "mode": "backup", "full_data": backup_data}
            return lb_data, next_fetch, now, backup_data, "💾 Supabase DB Backup (API Offline)"
            
    next_fetch = now + datetime.timedelta(minutes=5)
    cache[cache_key] = {"data": [], "last_fetch": now, "next_fetch_allowed": next_fetch, "mode": "offline", "full_data": {}}
    return [], next_fetch, now, {}, "❌ No Data Available"
