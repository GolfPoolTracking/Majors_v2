"""Tests for the sweepstake rules in scoring.py.

Run with:  pytest
"""
import pandas as pd
import pytest

from scoring import (
    assess_round_progress, assign_ranks, build_player_info, clean_pick, effective_tournament_status,
    extract_tie_breaker, format_score, is_cut_made, is_team_eliminated, mark_no_shows_withdrawn,
    ranks_hidden, resolve_current_round, round_position_history, safe_int, score_team, split_prizes,
    visible_max_round, winning_score,
)


# ---------------------------------------------------------------- helpers

def info(status='active', rounds=None, total=0):
    """One entry of the p_info_lower dict that score_team works from."""
    return {'status': status, 'rounds': rounds or {}, 'total': total, 'holes_played': 0}


def api_player(first, last, status='complete', rounds=(), current_round=None, holes_played=0, total_to_par=None, par=72):
    """A player as the RapidAPI leaderboard returns them. `rounds` is a list of scores to par."""
    rds = [{'round_number': i + 1, 'strokes': par + s, 'total_to_par': s} for i, s in enumerate(rounds)]
    rds += [{'round_number': i + 1, 'strokes': 0, 'total_to_par': 0} for i in range(len(rounds), 4)]
    return {'first_name': first, 'last_name': last, 'status': status, 'rounds': rds,
            'current_round': current_round or max(len(rounds), 1), 'holes_played': holes_played,
            'total_to_par': sum(rounds) if total_to_par is None else total_to_par}


def teams_df(rows):
    """rows: (name, total, elim, diff)"""
    return pd.DataFrame([{'Participant': n, 'Total': t, 'Elim': e, 'Diff': d, 'Tie': -10} for n, t, e, d in rows])


# ---------------------------------------------------------------- small helpers

@pytest.mark.parametrize("val,expected", [("E", 0), ("-5", -5), ("+3", 3), ("", 0), ("-", 0), (None, 0), (4.0, 4), ("12 ", 12)])
def test_safe_int(val, expected):
    assert safe_int(val) == expected


@pytest.mark.parametrize("val,expected", [("-12", -12), ("I think -8", -8), (5, 5), ("E", 0), (None, 0)])
def test_extract_tie_breaker(val, expected):
    assert extract_tie_breaker(val) == expected


def test_format_score():
    assert [format_score(s) for s in (-3, 0, 4)] == ["-3", "E", "+4"]


def test_clean_pick_strips_top20_label():
    assert clean_pick("Scottie Scheffler (Top 20 - #1)") == "Scottie Scheffler"
    assert clean_pick("  Rory McIlroy ") == "Rory McIlroy"


# ---------------------------------------------------------------- best 4 of 5

def test_best_four_of_five_count_each_round():
    p = {'a': info(rounds={1: -3}), 'b': info(rounds={1: -1}), 'c': info(rounds={1: 0}),
         'd': info(rounds={1: 2}), 'e': info(rounds={1: 5})}
    total, counting, round_scores, cum = score_team(['A', 'B', 'C', 'D', 'E'], p, [1])
    assert total == -2                      # -3 -1 +0 +2, the +5 is dropped
    assert counting[1] == ['a', 'b', 'c', 'd']
    assert round_scores == {1: -2} and cum == {1: -2}


def test_dropped_player_can_change_each_round():
    p = {'a': info(rounds={1: -4, 2: 6}), 'b': info(rounds={1: 0, 2: 0}), 'c': info(rounds={1: 0, 2: 0}),
         'd': info(rounds={1: 0, 2: 0}), 'e': info(rounds={1: 3, 2: -1})}
    total, counting, round_scores, cum = score_team(['a', 'b', 'c', 'd', 'e'], p, [1, 2])
    assert 'e' not in counting[1] and 'a' not in counting[2]
    assert round_scores == {1: -4, 2: -1}
    assert cum == {1: -4, 2: -5} and total == -5


def test_pick_not_yet_started_counts_as_level_par():
    p = {'a': info(rounds={1: 2}), 'b': info(rounds={1: 2}), 'c': info(rounds={1: 2}), 'd': info(rounds={1: 2}), 'e': info()}
    total, counting, *_ = score_team(['a', 'b', 'c', 'd', 'e'], p, [1])
    assert 'e' in counting[1] and total == 6


def test_cut_withdrawn_or_unknown_pick_scores_99():
    p = {'a': info(rounds={3: 0}), 'b': info(rounds={3: 0}), 'c': info(rounds={3: 0}), 'd': info('cut')}
    total, counting, *_ = score_team(['a', 'b', 'c', 'd', 'Nobody'], p, [3])
    assert total == 99                       # three 0s + one 99; the second 99 is dropped
    for status in ('wd', 'dq'):
        p['d'] = info(status)
        assert score_team(['a', 'b', 'c', 'd', 'Nobody'], p, [3])[0] == 99


# ---------------------------------------------------------------- the cut

@pytest.mark.parametrize("current_r,finished,expected", [(1, True, False), (2, False, False), (2, True, True), (3, False, True), (4, True, True)])
def test_cut_is_applied_once_round_two_is_finished(current_r, finished, expected):
    assert is_cut_made(current_r, finished) is expected


def test_one_pick_missing_cut_survives_two_is_eliminated():
    p = {'a': info(), 'b': info(), 'c': info(), 'd': info('cut'), 'e': info('cut')}
    assert not is_team_eliminated(['a', 'b', 'c', 'd', 'x_ok'], {**p, 'x_ok': info()}, 3, False)
    assert is_team_eliminated(['a', 'b', 'c', 'd', 'e'], p, 3, False)


def test_withdrawals_and_invalid_picks_count_towards_elimination():
    p = {'a': info(), 'b': info(), 'c': info(), 'd': info('wd')}
    assert is_team_eliminated(['a', 'b', 'c', 'd', 'Not In Field'], p, 3, False)


def test_no_elimination_before_the_cut():
    p = {'a': info(), 'b': info(), 'c': info(), 'd': info('wd'), 'e': info('wd')}
    assert not is_team_eliminated(['a', 'b', 'c', 'd', 'e'], p, 2, False)


# ---------------------------------------------------------------- ranking & tie-breaks

def test_ranking_order_eliminated_last_then_score_then_tiebreak():
    df = assign_ranks(teams_df([('Cut Team', -20, True, 0), ('Bob', -5, False, 3), ('Alice', -5, False, 1), ('Carol', -8, False, 9)]))
    assert list(df['Participant']) == ['Carol', 'Alice', 'Bob', 'Cut Team']
    assert list(df['DRank']) == ['1', '2', '3', '4']


def test_exact_ties_share_a_rank():
    df = assign_ranks(teams_df([('Ann', -5, False, 2), ('Ben', -5, False, 2), ('Cat', -5, False, 2), ('Dan', -1, False, 0)]))
    assert list(df['DRank']) == ['T1', 'T1', 'T1', '4']


def test_tie_after_first_place():
    df = assign_ranks(teams_df([('Ann', -9, False, 0), ('Ben', -5, False, 2), ('Cat', -5, False, 2)]))
    assert list(df['DRank']) == ['1', 'T2', 'T2']


def test_round_position_history():
    df = pd.DataFrame({'Participant': ['A', 'B', 'C'], 'Cum': [{1: -2, 2: -6}, {1: -2, 2: -3}, {1: 0, 2: -7}]})
    h = round_position_history(df, [1, 2])
    assert [h[i][1] for i in range(3)] == ['T1', 'T1', '3']
    assert [h[i][2] for i in range(3)] == ['2', '3', '1']


@pytest.mark.parametrize("lb,expected", [
    ([api_player('A', 'A', rounds=[-5]), api_player('B', 'B', rounds=[-2])], -5),
    ([api_player('A', 'A', 'withdrawn', rounds=[-9]), api_player('B', 'B', rounds=[-2])], -2),
    ([api_player('A', 'A', 'dq', rounds=[-9]), api_player('B', 'B', rounds=[3])], 3),
    ([], 0),
])
def test_winning_score_ignores_withdrawn_and_dq(lb, expected):
    assert winning_score(lb) == expected


# ---------------------------------------------------------------- prize money

POOL = [100, 50, 25]

def podium(rows):
    return {p['name']: p['payout'] for p in split_prizes(assign_ranks(teams_df(rows)), POOL)}


def test_prizes_no_ties():
    assert podium([('A', -9, False, 0), ('B', -8, False, 0), ('C', -7, False, 0), ('D', -6, False, 0)]) == {'A': 100, 'B': 50, 'C': 25}


def test_two_way_tie_for_first_shares_first_and_second():
    assert podium([('A', -9, False, 0), ('B', -9, False, 0), ('C', -7, False, 0), ('D', -6, False, 0)]) == {'A': 75, 'B': 75, 'C': 25}


def test_three_way_tie_for_second_shares_second_and_third():
    out = podium([('A', -9, False, 0), ('B', -5, False, 1), ('C', -5, False, 1), ('D', -5, False, 1), ('E', 0, False, 0)])
    assert out == {'A': 100, 'B': 25, 'C': 25, 'D': 25}


def test_four_way_tie_for_first_shares_whole_pot():
    out = podium([(n, -3, False, 1) for n in 'ABCD'])
    assert out == {n: pytest.approx(175 / 4) for n in 'ABCD'}


def test_hidden_ranks_pay_nothing():
    df = teams_df([('A', -9, False, 0)]); df['DRank'] = '-'
    assert split_prizes(df, POOL) == []


# ---------------------------------------------------------------- reading the live leaderboard

def test_current_round_takes_the_higher_of_tournament_and_players():
    assert resolve_current_round([{'current_round': 3}], {'current_round': 2}) == 3
    assert resolve_current_round([], {'current_round': '2'}) == 2


def test_no_shows_become_withdrawn_once_field_moves_on():
    lb = [api_player('A', 'A', 'not started', rounds=[]), api_player('B', 'B', 'not started', rounds=[])]
    mark_no_shows_withdrawn(lb[:1], current_r=1, t_status='inprogress')
    assert lb[0]['status'] == 'not started'
    mark_no_shows_withdrawn(lb, current_r=2, t_status='inprogress')
    assert [p['status'] for p in lb] == ['Withdrawn', 'Withdrawn']


def test_round_progress_mid_round():
    lb = [api_player('A', 'A', 'active', rounds=[-1], current_round=2, holes_played=7),
          api_player('B', 'B', 'active', rounds=[0], current_round=2, holes_played=12)]
    prog = assess_round_progress(lb, 'inprogress', 2)
    assert prog == {'finished': False, 'last_group_teed_off': True, 'last_group_htr': 7, 'r4_fully_done': False}


def test_round_progress_everyone_finished():
    lb = [api_player('A', 'A', 'complete', rounds=[-1, 2, 0, -3])]
    prog = assess_round_progress(lb, 'inprogress', 4)
    assert prog['finished'] and prog['r4_fully_done'] and prog['last_group_htr'] == 18


@pytest.mark.parametrize("current_r,admin,r4_live,teed_off,finished,expected", [
    (2, True, False, False, False, 2),   # admin always sees the live round
    (2, False, False, False, False, 1),  # public: round in progress is hidden
    (2, False, False, False, True, 2),   # ... until it's finished
    (4, False, False, False, False, 3),  # R4 hidden until the last group tees off
    (4, False, False, True, False, 4),
    (4, False, True, False, False, 4),
])
def test_public_view_hides_unfinished_round(current_r, admin, r4_live, teed_off, finished, expected):
    assert visible_max_round(current_r, admin, r4_live, teed_off, finished) == expected


def test_status_promoted_when_round_finishes():
    assert effective_tournament_status('inprogress', True, 2) == 'endofday'
    assert effective_tournament_status('inprogress', True, 4) == 'completed'
    assert effective_tournament_status('inprogress', False, 4) == 'inprogress'
    assert effective_tournament_status('pre', True, 0) == 'pre'


def test_ranks_hidden_until_end_of_round_one():
    assert ranks_hidden('pre', 0)
    assert ranks_hidden('inprogress', 1)
    assert not ranks_hidden('endofday', 1)
    assert not ranks_hidden('inprogress', 2)


# ---------------------------------------------------------------- building player scores

def test_player_info_rounds_and_totals():
    lb = [api_player('Rory', 'McIlroy', rounds=[-3, -2, 1, -4]), api_player('Jon', 'Rahm', 'cut', rounds=[2, 3])]
    p = build_player_info(lb, sweep_max_round=4, current_r=4)
    assert p['rory mcilroy']['rounds'] == {1: -3, 2: -2, 3: 1, 4: -4} and p['rory mcilroy']['total'] == -8
    assert p['jon rahm']['status'] == 'cut' and p['jon rahm']['total'] == 5


def test_player_info_only_includes_visible_rounds():
    lb = [api_player('Rory', 'McIlroy', rounds=[-3, -2, 1])]
    p = build_player_info(lb, sweep_max_round=2, current_r=3)
    assert p['rory mcilroy']['rounds'] == {1: -3, 2: -2} and p['rory mcilroy']['total'] == -5


def test_player_info_live_round_uses_running_total():
    lb = [api_player('Rory', 'McIlroy', 'active', rounds=[-3, -2], current_round=3, holes_played=9, total_to_par=-8)]
    p = build_player_info(lb, sweep_max_round=3, current_r=3)
    assert p['rory mcilroy']['rounds'][3] == -3 and p['rory mcilroy']['total'] == -8


def test_par_override_corrects_bad_api_to_par():
    lb = [api_player('Rory', 'McIlroy', rounds=[-3, -2], par=72)]
    lb[0]['rounds'][0]['total_to_par'] = 0          # API got the to-par wrong; strokes are right
    p = build_player_info(lb, sweep_max_round=2, current_r=2, par_override=72)
    assert p['rory mcilroy']['rounds'] == {1: -3, 2: -2}


def test_dns_list_marks_players_withdrawn():
    lb = [api_player('Rory', 'McIlroy', rounds=[-3])]
    p = build_player_info(lb, 1, 1, dns_input="Rory McIlroy, Tiger Woods")
    assert p['rory mcilroy']['status'] == 'wd' and p['rory mcilroy']['total'] == 999
    assert p['tiger woods']['status'] == 'wd'


def test_aliases_map_misspelt_picks_to_real_players():
    lb = [api_player('Ludvig', 'Åberg', rounds=[-4])]
    p = build_player_info(lb, 1, 1, alias_str="Ludvig Aberg:Ludvig Åberg, Junk:Nobody")
    assert p['ludvig aberg'] is p['ludvig åberg']
    assert 'junk' not in p


def test_pre_tournament_adds_whole_field_at_level_par():
    p = build_player_info([], 0, 0, valid_players=['Rory McIlroy'], t_status='pre')
    assert p['rory mcilroy'] == {'status': 'active', 'rounds': {}, 'total': 0, 'holes_played': 0}
