"""Pure scoring logic for the golf sweepstake.

Nothing in this module touches Streamlit, the database or the network, so every
rule here (best-4 scoring, the cut, tie-breaks, ranks, prize splits, PGA score
handling) can be tested directly. See tests/test_scoring.py.
"""
import re, datetime
from functools import lru_cache

import pandas as pd
import pytz

@lru_cache(maxsize=2048)
def _cached_safe_int_string(s_val):
    if s_val in ["E", "EVEN", "PAR", "-", ""]: return 0
    clean = re.sub(r"[^0-9.-]", "", s_val)
    if not clean or clean == '-': return 0
    try: return int(float(clean))
    except Exception: return 0

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
    stt = p.get('status', '').lower()
    if stt in ['withdrawn', 'wd', 'disqualified', 'dq']: return 999
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
    score = get_pga_round_score_num(p, r_idx, max_visible_round, par_override, is_live_scoring_active, curr_r)
    if score == 999: return "-"
    txt = "E" if score == 0 else f"+{score}" if score > 0 else str(score)
    stt = p.get('status', '').lower()
    if is_live_scoring_active and stt == 'active' and target_r == curr_r:
        hp = safe_int(p.get('holes_played', 0))
        if 0 < hp < 18: txt = f"{txt} ({hp})"
    return txt

def get_pga_total_num(p, max_visible_round, par_override, is_live_scoring_active, curr_r):
    stt = p.get('status', '').lower()
    if stt == 'pre': return 999 
    if stt in ['withdrawn', 'missed cut', 'disqualified', 'wd', 'cut', 'dq']: return safe_int(p.get('total_to_par', 0))
    api_total = safe_int(p.get('total_to_par', 0))
    hp = safe_int(p.get('holes_played', 0))
    api_past = get_corrected_past_total(p, curr_r, par_override)
    has_started = (hp > 0) or (api_total != api_past)
    is_playing_now = stt == 'active' and has_started
    if is_live_scoring_active and is_playing_now and max_visible_round == curr_r:
        if par_override > 0:
            past_score = sum([get_pga_round_score_num(p, i, curr_r - 1, par_override) for i in range(curr_r - 1) if get_pga_round_score_num(p, i, curr_r - 1, par_override) != 999])
            return past_score + (api_total - api_past)
        return api_total
    locked_score = 0
    has_completed_round = False
    for r_idx in range(max_visible_round):
        rs = get_pga_round_score_num(p, r_idx, max_visible_round, par_override, is_live_scoring_active, curr_r)
        if rs != 999:
            locked_score += rs
            has_completed_round = True
    return locked_score if has_completed_round else 999

def get_pga_thru(p, curr_r, completed_rounds, is_live_scoring_active, tourney_tz_str, target_tz_str, hide_tt=False):
    stt = p.get('status', '').lower()
    hp = safe_int(p.get('holes_played', 0))
    if stt in ['withdrawn', 'missed cut', 'disqualified', 'wd', 'cut', 'dq']: return {'withdrawn': 'WD', 'missed cut': 'CUT', 'disqualified': 'DQ'}.get(stt, stt.upper())
    if stt in ['completed', 'complete'] or hp == 72 or completed_rounds == 4: return 'F'
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
            if tt and ':' in tt and tt != "00:00":
                try:
                    dt_source = pytz.timezone(tourney_tz_str).localize(datetime.datetime.now().replace(hour=int(tt.split(':')[0]), minute=int(tt.split(':')[1]), second=0, microsecond=0))
                    return dt_source.astimezone(pytz.timezone(target_tz_str)).strftime('%H:%M')
                except Exception: return tt
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
        stt = str(p.get('status', '')).lower()
        if stt in ['complete', 'completed'] or (stt in ['not started', 'notstarted'] and current_r > 1):
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


# ---------------------------------------------------------------------------
# Live leaderboard clean-up
# ---------------------------------------------------------------------------

OUT_STATUSES = ['cut', 'wd', 'dq']


def resolve_current_round(lb_data, live_details):
    """The round in play: the higher of the tournament's round and any player's round."""
    api_curr_r = safe_int(live_details.get('current_round', 0))
    player_curr_r = max([safe_int(p.get('current_round', 0)) for p in lb_data] + [0])
    return max(api_curr_r, player_curr_r)


def mark_no_shows_withdrawn(lb_data, current_r, t_status):
    """Players still 'not started' with no strokes once the field has moved on are treated as withdrawn."""
    for p in lb_data:
        stt = str(p.get('status', '')).lower()
        if stt in ['not started', 'notstarted'] and safe_int(p.get('holes_played', 0)) == 0:
            total_strokes = sum(safe_int(r.get('strokes', 0)) for r in p.get('rounds', []))
            if total_strokes == 0 and (current_r > 1 or t_status in ['completed', 'endofday']):
                p['status'] = 'Withdrawn'


def assess_round_progress(lb_data, t_status, current_r):
    """Work out whether the current round is finished and how far the last group has got.

    Returns a dict with: finished, last_group_teed_off, last_group_htr, r4_fully_done.
    """
    active_field = [p for p in lb_data if p.get('status', '').lower() == 'active']
    not_started_current = [p for p in lb_data if p.get('status', '').lower() in ['not started', 'notstarted'] and safe_int(p.get('current_round', 0)) == current_r]
    total_holes_live = sum(safe_int(p.get('holes_played', 0)) for p in lb_data)

    if t_status in ['completed', 'complete', 'endofday']:
        finished = True
    elif active_field and all(safe_int(p.get('holes_played', 0)) == 18 for p in active_field):
        finished = True
    elif t_status == 'inprogress' and not active_field and not not_started_current:
        finished = True
    elif t_status == 'inprogress' and total_holes_live == 0:
        finished = True
    else:
        finished = False

    active_holes = [safe_int(p.get('holes_played', 0)) for p in active_field]
    last_group_teed_off = False
    last_group_htr = 0
    r4_fully_done = False

    if not active_holes:
        if any(p.get('status', '').lower() in ['complete', 'completed'] for p in lb_data):
            last_group_teed_off = True
            last_group_htr = 18
            if current_r == 4: r4_fully_done = True
    else:
        min_hp = min(active_holes)
        if min_hp > 0:
            last_group_htr = min_hp
            last_group_teed_off = True

    return {'finished': finished, 'last_group_teed_off': last_group_teed_off,
            'last_group_htr': last_group_htr, 'r4_fully_done': r4_fully_done}


def visible_max_round(current_r, is_admin_view, is_r4_live_mode, last_group_teed_off, round_finished):
    """Highest round whose scores count. The public view hides a round until it's finished,
    except R4, which goes live once the last group has teed off."""
    if is_admin_view:
        return current_r
    if current_r == 4:
        return 4 if (is_r4_live_mode or last_group_teed_off) else 3
    if round_finished:
        return current_r
    return current_r - 1


def build_player_info(lb_data, sweep_max_round, current_r, par_override=0, dns_input="", alias_str="", valid_players=None, t_status=""):
    """Per-player round scores, total and status, keyed by lower-case player name.

    Applies the admin's DNS list, name aliases ("wrong:correct" pairs) and, before the
    tournament starts, adds every valid field player with a zero score.
    """
    p_info = {}
    status_map = {'withdrawn': 'wd', 'disqualified': 'dq', 'missed cut': 'cut', 'mc': 'cut'}
    dns_list = [n.strip().lower() for n in str(dns_input).split(",")] if dns_input else []

    for p in lb_data:
        name = f"{p['first_name']} {p['last_name']}".strip()
        name_lower = name.lower()
        raw_status = p.get('status', '').lower()
        norm_status = status_map.get(raw_status, raw_status)
        final_status = 'wd' if name_lower in dns_list else norm_status

        try: strokes = [safe_int(rd.get('strokes', 0)) for rd in p.get('rounds', [])]
        except Exception: strokes = [0]*4

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
        elif final_status in ['missed cut', 'cut']: final_total = safe_int(p.get('total_to_par', 0))
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

    if alias_str:
        for pair in alias_str.split(','):
            if ':' in pair:
                wrong, correct = pair.split(':')
                wrong, correct = wrong.strip().lower(), correct.strip().lower()
                if correct in p_info_lower:
                    p_info_lower[wrong] = p_info_lower[correct]

    if current_r == 0 or t_status == 'pre' or t_status == '':
        for vp in (valid_players or []):
            if vp.lower() not in p_info_lower: p_info_lower[vp.lower()] = {'status': 'active', 'rounds': {}, 'total': 0, 'holes_played': 0}

    return p_info_lower


def winning_score(lb_data):
    """Leader's score to par from the live leaderboard (withdrawn/DQ players ignored). Used for the tie-break."""
    live_win_scores = [safe_int(p.get('total_to_par', 0)) for p in lb_data if str(p.get('status', '')).lower() not in ['withdrawn', 'wd', 'disqualified', 'dq']]
    return min(live_win_scores) if live_win_scores else 0


def effective_tournament_status(t_status, round_finished, current_r):
    """Promote 'inprogress' to 'endofday' (or 'completed' after R4) once the round is finished."""
    if t_status not in ['completed', 'endofday', 'pre', '']:
        if round_finished: return 'completed' if current_r == 4 else 'endofday'
    return t_status


def ranks_hidden(t_status, current_r):
    """Ranks stay hidden before the tournament and until the end of round 1."""
    return (t_status == 'pre' or current_r == 0 or t_status == '') or (current_r == 1 and t_status not in ['endofday', 'completed'])


# ---------------------------------------------------------------------------
# Team scoring
# ---------------------------------------------------------------------------

MISSING_SCORE = 99  # a pick that is out (cut/WD/DQ) or not in the field


def clean_pick(name):
    """Strip the ' (Top 20 - #n)' label the entry form adds to player names."""
    return str(name).split(" (Top 20")[0].strip()


def score_team(picks, p_info_lower, rounds):
    """Best 4 of 5 picks count each round.

    A pick with no score yet counts as 0; a pick who is out or not in the field counts as 99.
    Returns (total, counting_map, round_scores, cumulative_scores).
    """
    total = 0; c_map = {}; r_sc = {}; cum_sc = {}; running = 0
    for r in rounds:
        p_scores = []
        for p in picks:
            p_key = p.lower()
            if p_key in p_info_lower:
                if r in p_info_lower[p_key]['rounds']: score = p_info_lower[p_key]['rounds'][r]
                elif p_info_lower[p_key]['status'] in OUT_STATUSES: score = MISSING_SCORE
                else: score = 0
            else: score = MISSING_SCORE
            p_scores.append((p_key, score))

        p_scores.sort(key=lambda x: int(x[1]))
        top_4 = p_scores[:4]; c_map[r] = [x[0] for x in top_4]
        r_sum = sum(int(x[1]) for x in top_4)
        r_sc[r] = r_sum; running += r_sum; cum_sc[r] = running; total += r_sum
    return total, c_map, r_sc, cum_sc


def is_cut_made(current_r, round_finished):
    """The cut is applied once round 2 is finished."""
    return (current_r > 2) or (current_r == 2 and round_finished)


def is_team_eliminated(picks, p_info_lower, current_r, round_finished):
    """After the cut, a team needs at least 4 picks still playing (2+ out = eliminated)."""
    cut_made = is_cut_made(current_r, round_finished)
    return (cut_made and sum(1 for p in picks if p.lower() in p_info_lower and p_info_lower[p.lower()]['status'] not in OUT_STATUSES) < 4)


def assign_ranks(df):
    """Sort teams (eliminated last, then total, then tie-break distance, then name) and give
    each a display rank, with 'T' for ties. Returns the sorted DataFrame with a DRank column."""
    df = df.sort_values(by=["Elim", "Total", "Diff", "Participant"], ascending=[True, True, True, True]).reset_index(drop=True)
    dr = []; cr = 1
    for i in range(len(df)):
        if i > 0 and df.iloc[i]['Elim'] == df.iloc[i-1]['Elim'] and df.iloc[i]['Total'] == df.iloc[i-1]['Total'] and df.iloc[i]['Diff'] == df.iloc[i-1]['Diff']:
            dr.append(f"T{cr}")
            if not str(dr[i-1]).startswith("T"): dr[i-1] = f"T{cr}"
        else: cr = i + 1; dr.append(str(cr))
    df['DRank'] = dr
    return df


def round_position_history(df, rounds):
    """Each team's position after each round, by cumulative score. Returns {df_index: {round: 'T3'}}."""
    history_ranks = {idx: {} for idx in df.index}
    for r in rounds:
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
    return history_ranks


def split_prizes(ranked_df, prize_pool):
    """Share the 1st/2nd/3rd prizes. Tied teams pool the prizes for the places they cover
    and split them evenly (e.g. two teams tied 2nd share 2nd + 3rd).

    Returns a list of {name, rank, payout, score, tie} for every team on the podium.
    Names are returned as-is; escape them before putting them in HTML.
    """
    podium_players = []
    current_prizes_used = 0
    grouped_ranks = {}

    for _, row in ranked_df.iterrows():
        r_str = str(row.get('DRank', '-'))
        if r_str == '-': continue
        if r_str not in grouped_ranks: grouped_ranks[r_str] = []
        grouped_ranks[r_str].append({"name": str(row['Participant']), "score": format_score(row['Total']), "tie": row['Tie']})

    for r_str, players in grouped_ranks.items():
        match = re.search(r'\d+', r_str)
        if not match: continue
        base_r = int(match.group())

        if current_prizes_used >= 3: break

        n_players = len(players)
        prizes_to_take = min(n_players, 3 - current_prizes_used)

        total_cash = 0
        for _ in range(prizes_to_take):
            total_cash += prize_pool[current_prizes_used]
            current_prizes_used += 1

        payout = total_cash / n_players if n_players > 0 else 0

        for p_data in players:
            podium_players.append({"name": p_data['name'], "rank": base_r, "payout": payout, "score": p_data['score'], "tie": p_data['tie']})
    return podium_players
