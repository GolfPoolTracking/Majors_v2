import datetime

from field_watch import (
    UK_TZ, affected_entries, build_email_html, change_signature, diff_field,
    parse_field, resolve_close_time, should_check,
)


def test_diff_field():
    assert diff_field(["A", "B", "C"], ["B", "C", "D"]) == (["D"], ["A"])
    assert diff_field(parse_field("A, B,"), ["A", "B"]) == ([], [])


def test_close_time_prefers_saved_then_start_at_5am():
    assert resolve_close_time("2026-04-09 00:00:00", "2026-04-09 00:00:00").hour == 0
    assert resolve_close_time("", "2026-04-09 00:00:00") == UK_TZ.localize(datetime.datetime(2026, 4, 9, 5))
    assert resolve_close_time(None, "2026-04-09T00:00:00+00:00").hour == 5
    assert resolve_close_time(None, None) is None


def test_should_check_window():
    close = UK_TZ.localize(datetime.datetime(2026, 4, 9, 5))
    assert should_check(close - datetime.timedelta(days=2), close, 10)[0]
    assert not should_check(close, close, 10)[0]
    assert not should_check(close - datetime.timedelta(days=11), close, 10)[0]
    assert not should_check(close, None, 10)[0]


def test_signature_changes_with_the_change():
    assert change_signature(["D"], ["A"]) != change_signature(["D"], ["A", "B"])


def test_affected_entries_strips_top20_suffix():
    entries = [
        {"participant_name": "Ann", "picks": ["Scottie Scheffler (Top 20 - #1)", "Jake Knapp"]},
        {"participant_name": "Bob", "picks": ["Rory McIlroy"]},
    ]
    assert affected_entries(entries, ["Scottie Scheffler"]) == [("Ann", ["Scottie Scheffler"])]


def test_email_flags_mass_removal():
    close = UK_TZ.localize(datetime.datetime(2026, 4, 9, 5))
    body = build_email_html("Masters", [], [f"P{i}" for i in range(60)], [], close, 100, 40)
    assert "API glitch" in body
    assert "API glitch" not in build_email_html("Masters", ["X"], ["Y"], [("Ann", ["Y"])], close, 100, 100)
