"""End-to-end check of the sweepstake leaderboard calculation, with the API and database faked."""
import datetime

import pandas as pd
import pytest

import leaderboard


def player(first, last, rounds, status='complete'):
    rds = [{'round_number': i + 1, 'strokes': 72 + s, 'total_to_par': s} for i, s in enumerate(rounds)]
    rds += [{'round_number': i + 1, 'strokes': 0, 'total_to_par': 0} for i in range(len(rounds), 4)]
    return {'first_name': first, 'last_name': last, 'status': status, 'rounds': rds, 'current_round': 4,
            'holes_played': 18 if status == 'complete' else 0, 'total_to_par': sum(rounds)}


@pytest.fixture
def finished_tournament(monkeypatch):
    lb = [
        player('Al', 'One',   [-3, -3, -3, -3]),   # -12, winner
        player('Bo', 'Two',   [-2, -2, -2, -2]),   # -8
        player('Cy', 'Three', [0, 0, 0, 0]),
        player('Di', 'Four',  [1, 1, 1, 1]),
        player('Ed', 'Five',  [2, 2, 2, 2]),
        player('Fi', 'Six',   [5, 5], status='cut'),
        player('Gu', 'Seven', [6, 6], status='cut'),
    ]
    full = {'results': {'leaderboard': lb, 'tournament': {'live_details': {'status': 'completed', 'current_round': 4}}}}
    now = datetime.datetime(2026, 4, 12, 20, 0)
    entries = pd.DataFrame([
        # name,   tie-break, picks
        ('Alice', -12, ['Al One', 'Bo Two', 'Cy Three', 'Di Four', 'Ed Five']),
        ('Bob',   -10, ['Al One', 'Bo Two', 'Cy Three', 'Di Four (Top 20 - #4)', 'Ed Five']),
        ('Carol', -12, ['Al One', 'Bo Two', 'Cy Three', 'Fi Six', 'Gu Seven']),   # 2 missed cut -> out
        ('Alice',  0,  ['Cy Three', 'Di Four', 'Ed Five', 'Fi Six', 'Bo Two']),   # Alice's 2nd team
    ], columns=['Name', 'Tie Breaker', 'Picks'])
    for i in range(5): entries[f'Pick {i+1}'] = entries['Picks'].str[i]
    entries['Sheet_Row'] = range(1, len(entries) + 1)
    entries = entries.drop(columns='Picks')

    monkeypatch.setattr(leaderboard, 'fetch_smart_leaderboard', lambda t: (lb, now, now, full, 'test'))
    monkeypatch.setattr(leaderboard, 'get_entries_df', lambda t: entries.copy())
    monkeypatch.setattr(leaderboard, 'fetch_aliases_from_db', lambda t: '')
    monkeypatch.setattr(leaderboard, 'get_api_cache', lambda: {})


def test_final_standings(finished_tournament):
    df = leaderboard.calculate_leaderboard('T1', 'Test Open', None, is_admin_download=True)
    standings = list(zip(df['Participant'], df['Total'], df['Elim'], df['DRank']))
    assert standings == [
        ('Alice (Team 1)', -16, False, '1'),   # best 4 = -3 -2 0 +1 a round; tie-break -12 is exact
        ('Bob', -16, False, '2'),              # same score, tie-break 2 away
        ('Alice (Team 2)', 4, False, '3'),     # one missed cut is fine: best 4 of the rest
        ('Carol', 188, True, '4'),             # two missed the cut: eliminated (99s in R3/R4)
    ]
    assert df.loc[0, 'RSc'] == {1: -4, 2: -4, 3: -4, 4: -4}


def test_followed_teams_are_pinned_and_highlighted(finished_tournament, monkeypatch):
    shown = []
    monkeypatch.setattr(leaderboard.st, 'markdown', lambda body, **k: shown.append(body))
    monkeypatch.setattr(leaderboard.st, 'balloons', lambda: None)
    monkeypatch.setattr(leaderboard.st, 'session_state', {})
    monkeypatch.setattr(leaderboard, 'get_settings_cache', lambda: {})
    leaderboard.calculate_leaderboard('T1', 'Test Open', None, is_admin_view=True, hide_title=True, follow_ids={1, 4})
    pinned = next(b for b in shown if '⭐ Your teams' in b)
    assert pinned.count('<details open') == 2 and 'Alice' in pinned and 'Bob' not in pinned
    full = next(b for b in shown if 'Score (Tie)' in b)
    assert full.count('⭐ ') == 2 and full.count('<details') == 4 and '<details open' not in full


def test_no_follow_means_no_pinned_section(finished_tournament, monkeypatch):
    shown = []
    monkeypatch.setattr(leaderboard.st, 'markdown', lambda body, **k: shown.append(body))
    leaderboard.calculate_leaderboard('T1', 'Test Open', None, is_admin_view=True, hide_title=True)
    assert not any('Your team' in b for b in shown) and any('Score (Tie)' in b for b in shown)
