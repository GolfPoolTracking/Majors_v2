"""Sweepstake leaderboard and PGA leaderboard rendering."""
import datetime
import html

import pandas as pd
import streamlit as st

from db import fetch_aliases_from_db, get_entries_df
from golf_api import fetch_smart_leaderboard, get_api_cache, get_settings_cache
from scoring import (
    assess_round_progress,
    assign_ranks,
    build_player_info,
    clean_pick,
    effective_tournament_status,
    extract_tie_breaker,
    format_pga_round_score,
    format_score,
    get_pga_thru,
    get_pga_total_num,
    golf_fmt,
    is_team_eliminated,
    ranks_hidden,
    resolve_current_round,
    round_position_history,
    safe_int,
    score_team,
    split_prizes,
    visible_max_round,
    winning_score,
)

def render_roster_table(picks, p_info_lower, rounds_active, counting_map, is_live_active, round_scores, round_ranks, is_elim, current_r, hide_rank=False):
    rows = []
    for p in picks:
        p_key = str(p).lower()
        safe_p = html.escape(str(p))
        if p_key not in p_info_lower:
            rows.append(f"<tr><td style='padding-bottom: 5px;'>⚠️ {safe_p}</td><td colspan='{len(rounds_active)+1}' style='color:red;'>Not in Field</td></tr>")
            continue
        d = p_info_lower[p_key]; stt = d['status']
        row_html = f"<tr><td style='padding-right:15px; min-width:140px; padding-bottom: 5px;'>{safe_p}</td>"
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
        df = get_entries_df(t_id)
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
    except Exception as e: return pd.DataFrame(), 0

def calculate_leaderboard(t_id, t_name, t_start, par_override=0, dns_input="", valid_players=None, logo_url=None, is_admin_download=False, header_only=False, is_admin_view=False, hide_title=False, search_query="", follow_ids=None):
    try:
        lb_data, next_f, last_f, full_data, data_source = fetch_smart_leaderboard(t_id)
        
        raw_results = full_data.get('results') or {}
        tourney_data = raw_results.get('tournament') or {}
        live_details = tourney_data.get('live_details') or {}

        t_status = str(live_details.get('status', '')).lower()
        current_r = resolve_current_round(lb_data, live_details)

        progress = assess_round_progress(lb_data, t_status, current_r)
        is_round_finished_consensus = progress['finished']
        last_group_teed_off = progress['last_group_teed_off']
        last_group_htr = progress['last_group_htr']
        is_r4_fully_done = progress['r4_fully_done']

        cache = get_api_cache()
        lb_key = f"lb_{t_id}"
        cache_mode = cache[lb_key].get("mode", "") if lb_key in cache else ""
        is_r4_live_mode = "R4 Live Scoring" in cache_mode

        sweep_max_round = visible_max_round(current_r, is_admin_view, is_r4_live_mode, last_group_teed_off, is_round_finished_consensus)

        p_info_lower = build_player_info(lb_data, sweep_max_round, current_r, par_override, dns_input,
                                         fetch_aliases_from_db(t_id), valid_players, t_status)

        # Tie-break target: the winning score on the LIVE leaderboard
        win_score = winning_score(lb_data)

        t_status = effective_tournament_status(t_status, is_round_finished_consensus, current_r)

        hide_rank = ranks_hidden(t_status, current_r)
        
        if t_status == 'completed' or (current_r == 4 and (is_r4_fully_done or last_group_htr >= 18)):
            if t_status == 'completed':
                status_txt = f"Tournament Finished | Winning Score: {format_score(win_score)}"
            else:
                status_txt = "All players finished: Final Result Being Checked!"
        elif t_status in ['pre', '']:
            status_txt = f"Waiting for R1 to start... ⏳ ({datetime.datetime.strptime(str(t_start), '%Y-%m-%d %H:%M:%S').strftime('%d %b %y')})" if t_start else "Waiting for R1 to start... ⏳"
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
                try:
                    st.markdown(f"<div style='text-align: center; padding-bottom: 10px;'><img src='{html.escape(logo_url)}' style='max-width: 250px; max-height: 150px; width: 100%; object-fit: contain;'></div>", unsafe_allow_html=True)
                except Exception: pass
            elif not hide_title:
                st.header(f"🏆 {html.escape(t_name.split('(')[0])}")

            if t_status not in ['pre', ''] and current_r > 0:
                if t_status == 'completed':
                    status_bar_html = f"<div class='compact-container' style='display: flex; justify-content: center; align-items: center; padding: 12px;'><div style='font-weight: bold; font-size: 1.15em; color: #2ecc71;'>🏆 {html.escape(status_txt)}</div></div>"
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
                            <div class="title">⛳ {html.escape(status_txt)}</div>
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
            
        df_resp = get_entries_df(t_id)
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
            picks = [clean_pick(row[c]) for c in pick_cols if c in df_resp.columns][:5]
            sheet_warns = []
            total, c_map, r_sc, cum_sc = score_team(picks, p_info_lower, r_range)
            elim = is_team_eliminated(picks, p_info_lower, current_r, is_round_finished_consensus)
            t_val = extract_tie_breaker(row['Tie Breaker']) if 'Tie Breaker' in row else 0
            results.append({"EntryId": row.get('Sheet_Row'), "Email": str(row.get('Email') or '').strip().lower(), "Participant": str(row[name_col]), "DisplayName": str(row['_Display_Name']), "Total": total, "Elim": elim, "Picks": picks, "CMap": c_map, "RSc": r_sc, "Cum": cum_sc, "Diff": abs(t_val - win_score), "Tie": t_val, "SheetWarnings": sheet_warns})

        if not results:
            if is_admin_download: return pd.DataFrame()
            st.info("No entries have been submitted yet."); return None

        df = pd.DataFrame(results)
        if hide_rank:
            df = df.sort_values(by="Participant", key=lambda x: x.astype(str).str.lower()).reset_index(drop=True)
            df['DRank'] = "-"
        else:
            df = assign_ranks(df)

        history_ranks = round_position_history(df, r_range)

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
                podium_players = split_prizes(df, prize_pool)
                for p in podium_players: p['name'] = html.escape(p['name'])

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
                        
                    score_str = f"<div style='font-size: 0.9em; margin-top: 4px;'><b style='color: #2ecc71;'>{p['score']}</b> <span style='color: #475569; opacity: 0.8;'>({p['tie']})</span></div>"
                        
                    block = f"<div style='flex: 1; max-width: {s['mw']}; background: {s['bg']}; border-radius: 8px 8px 0 0; padding: {s['padding']}; margin: 0 4px; border: 1px solid {s['border']}; border-bottom: none; position: relative; z-index: {s['z']}; {s['shadow']}'><div style='font-size: {s['sz']}; margin-bottom: 5px;'>{s['medal']}</div><div style='font-size: 0.9em; font-weight: bold; color: #475569; word-wrap: break-word;'>{p['name']}</div>{score_str}{pz_str}</div>"
                    html_blocks.append(block)
                
                final_html = "<div style='display: flex; justify-content: center; align-items: flex-end; margin: 20px 0 0px 0; text-align: center; font-family: sans-serif;'>" + "".join(html_blocks) + "</div><div style='height: 4px; background-color: #2c3e50; margin-bottom: 30px; border-radius: 2px;'></div>"
                
                st.markdown(final_html, unsafe_allow_html=True)
        
        follow_ids = {str(x) for x in (follow_ids or [])}
        followed = df[df['EntryId'].astype(str).isin(follow_ids)] if follow_ids else df.iloc[0:0]

        if search_query:
            df = df[df['Participant'].str.contains(search_query, case=False, na=False)]
            if df.empty:
                st.warning("🔍 No entries found matching your search.")
        
        is_live_active_now = (sweep_max_round == current_r and t_status in ['inprogress', 'active'])

        all_names = list(df['Participant']) + list(followed['Participant'])
        max_name_len = max([len(str(n)) for n in all_names]) if all_names else 15
        name_min_width = max(max_name_len * 8, 110) 

        def render_row(i, row, is_open=False, is_followed=False):
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
                elif p_key not in p_info_lower: warn_list.append(f"Invalid Pick: {html.escape(p)}")
                elif vp_lower and p_key not in vp_lower:
                    if p_info_lower[p_key]['status'] not in ['cut', 'wd', 'dq']: warn_list.append(f"Possible WD/DNS: {html.escape(p)}")
            
            for w in row['SheetWarnings']: warn_list.append(html.escape(str(w)))
            warn_list = list(dict.fromkeys(warn_list))
            
            if row['Elim']: 
                score_block = "<div style='color: #e74c3c; font-weight: bold; white-space: nowrap;'>❌ CUT</div>"
            else: 
                score_color = '#2ecc71' 
                score_block = f"<div style='white-space: nowrap;'><b style='color: {score_color};'>{score_txt}</b> <span style='color: var(--text-color); opacity: 0.6; font-size: 0.9em;'>({row['Tie']})</span></div>"
                
            warn_html = f"<div style='color: #e67e22; font-size: 0.85em; white-space: nowrap;'>⚠️ {' | '.join(warn_list)}</div>" if warn_list else "<div></div>"
            
            table_html = render_roster_table(row['Picks'], p_info_lower, r_range, row['CMap'], is_live_active_now, row['RSc'], history_ranks[i], row['Elim'], current_r, hide_rank)
            
            star = "⭐ " if is_followed else ""
            summary_html = f"<div style='display: grid; grid-template-columns: 40px {name_min_width}px 80px auto; gap: 8px; align-items: center; width: max-content;'><div style='font-weight: bold;'>{lbl_val}</div><div style='font-weight: bold; white-space: nowrap;'>{star}{row['DisplayName']}</div>{score_block}{warn_html}</div>"
            highlight = " style='border: 2px solid #f1c40f !important;'" if is_followed else ""
            
            return f"<details{' open' if is_open else ''}{highlight}><summary>{summary_html}</summary><div class='expanded-content'>{table_html}</div></details>"

        if not followed.empty:
            pinned_html = "<div style='display: flex; flex-direction: column; gap: 4px; overflow-x: auto; margin-bottom: 14px;'>"
            pinned_html += f"<div style='font-weight: bold; margin-bottom: 2px;'>⭐ {'Your team' if len(followed) == 1 else 'Your teams'}</div>"
            for i, row in followed.iterrows():
                pinned_html += render_row(i, row, is_open=True, is_followed=True)
            st.markdown(pinned_html + "</div>", unsafe_allow_html=True)

        leaderboard_html = "<div style='display: flex; flex-direction: column; gap: 4px; overflow-x: auto;'>"

        if not hide_rank:
            legend_html = f"<div style='display: grid; grid-template-columns: 34px 40px {name_min_width}px 80px auto; gap: 8px; width: max-content; font-size: 0.85em; color: var(--text-color); opacity: 0.7; margin-bottom: 2px;'><div></div><div></div><div></div><div style='white-space: nowrap;'>🏌️‍♂️ Score (Tie)</div><div></div></div>"
            leaderboard_html += legend_html
        
        for i, row in df.iterrows():
            leaderboard_html += render_row(i, row, is_followed=str(row['EntryId']) in follow_ids)
            
        leaderboard_html += "</div>"
        
        st.markdown(leaderboard_html, unsafe_allow_html=True)
        
    except Exception as e: 
        st.error(f"Error calculating leaderboard: {e}")
        return pd.DataFrame() if is_admin_download else None

def render_pga_leaderboard(lb_data, full_data, tourney_id, view_mode, par_override=0, hide_tt=False):
    if not lb_data: st.info("No tournament data available yet."); return

    raw_results = full_data.get('results') or {} if full_data else {}
    tourney_data = raw_results.get('tournament') or {}
    live_details = tourney_data.get('live_details') or {}
    tourney_tz_str = tourney_data.get('timezone') or 'America/New_York'
    
    api_curr_r = safe_int(live_details.get('current_round', 0))
    player_curr_r = max([safe_int(p.get('current_round', 0)) for p in lb_data] + [0])
    curr_r = max(api_curr_r, player_curr_r)
    global_status = live_details.get('status', '').lower()
    
    tz_choice = st.radio("Display Tee Times in:", ["🇬🇧 UK Time", "🇺🇸 US Eastern (ET)"], horizontal=True, key=f"tz_{tourney_id}_{view_mode}")
    target_tz = 'Europe/London' if "UK" in tz_choice else 'America/New_York'

    active_field = [p for p in lb_data if p.get('status','').lower() == 'active']
    not_started_current = [p for p in lb_data if p.get('status','').lower() in ['not started', 'notstarted'] and safe_int(p.get('current_round', 0)) == curr_r]
    total_holes_live = sum(safe_int(p.get('holes_played', 0)) for p in lb_data)
    
    if global_status in ['completed', 'complete']: completed_rounds = 4
    elif global_status == 'endofday': completed_rounds = curr_r
    else:
        if active_field and all(safe_int(p.get('holes_played',0)) == 18 for p in active_field): 
            completed_rounds = curr_r
        elif global_status == 'inprogress' and not active_field and not not_started_current:
            completed_rounds = curr_r
        elif global_status == 'inprogress' and total_holes_live == 0:
            completed_rounds = curr_r
        else: 
            completed_rounds = curr_r - 1
            
    cache = get_api_cache()
    lb_key = f"lb_{tourney_id}"
    is_r4_live = (lb_key in cache and "R4 Live" in cache[lb_key].get("mode", ""))
    
    if view_mode == "admin":
        max_visible_round = curr_r
        is_live_scoring_active = (global_status in ['inprogress', 'active'])
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
        'Status': str(p.get('status', '')).lower(),
        'Player': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        'Total': get_pga_total_num(p, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'Thru': get_pga_thru(p, curr_r, completed_rounds, is_live_scoring_active, tourney_tz_str, target_tz, hide_tt),
        'R1': format_pga_round_score(p, 0, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'R2': format_pga_round_score(p, 1, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'R3': format_pga_round_score(p, 2, max_visible_round, par_override, is_live_scoring_active, curr_r),
        'R4': format_pga_round_score(p, 3, max_visible_round, par_override, is_live_scoring_active, curr_r)
    } for p in lb_data])
    
    if not is_live_scoring_active:
        real_df['Sort_Status'] = real_df['Status'].apply(lambda x: 1 if x in ['withdrawn', 'missed cut', 'disqualified', 'wd', 'cut', 'dq'] else 0)
        real_df = real_df.sort_values(by=['Sort_Status', 'Total', 'Player']).reset_index(drop=True)
        ranks = []; crank = 1
        for i in range(len(real_df)):
            score, status = real_df.loc[i, 'Total'], real_df.loc[i, 'Status']
            if status in ['withdrawn', 'wd']: ranks.append("WD")
            elif status in ['missed cut', 'cut']: ranks.append("CUT")
            elif status in ['disqualified', 'dq']: ranks.append("DQ")
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
