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


# --- main() end to end, with Supabase, the API and SMTP faked ---

import sys
import types

import pytest

import field_watch


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op = db, table, {}, ("select", None)

    def select(self, *_): return self
    def eq(self, col, val): self.filters[col] = val; return self
    def upsert(self, row): self.op = ("upsert", row); return self
    def insert(self, row): self.op = ("insert", row); return self

    def execute(self):
        kind, row = self.op
        rows = self.db.setdefault(self.table, [])
        if kind == "select":
            data = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in self.filters.items())]
        else:
            if kind == "upsert":
                rows[:] = [r for r in rows if r.get("key") != row.get("key")]
            rows.append(row)
            data = [row]
        return types.SimpleNamespace(data=data)


@pytest.fixture
def run(monkeypatch):
    def _run(db, field, **env):
        sent = []
        fake_sb = types.SimpleNamespace(table=lambda name: _Query(db, name))
        monkeypatch.setitem(sys.modules, "supabase", types.SimpleNamespace(create_client=lambda *a: fake_sb))
        monkeypatch.setattr(field_watch, "fetch_entry_list", lambda t_id, keys: field)
        monkeypatch.setattr(field_watch, "send_email", lambda s, p, to, subj, body: sent.append((to, subj, body)))
        for k, v in {"SUPABASE_URL": "u", "SUPABASE_KEY": "k", "RAPIDAPI_KEYS": "a,b",
                     "EMAIL_SENDER": "me@x", "EMAIL_PASSWORD": "pw", "WATCH_DAYS": "4", **env}.items():
            monkeypatch.setenv(k, v)
        field_watch.main()
        return sent
    return _run


def _db(close_in_days, baseline="A,B,C"):
    close = (datetime.datetime.now(UK_TZ) + datetime.timedelta(days=close_in_days)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "golf_app_settings": [{"key": "active_tournament", "value": {"id": "907", "name": "Hero", "start": None}}],
        "golf_tournament_configs": [{"tournament_id": "907", "tournament_name": "Hero", "close_time": close,
                                     "alerted_field": baseline}],
        "golf_tournament_standings": [{"tournament_id": "907", "participant_name": "Ann", "picks": ["A", "B"]}],
    }


def test_outside_window_sends_nothing(run):
    assert run(_db(8), ["B", "C", "D"]) == []


def test_change_is_emailed_once(run):
    db = _db(2)
    sent = run(db, ["B", "C", "D"])
    assert len(sent) == 1 and "+1 / −1, 1 entries affected" in sent[0][1]
    assert run(db, ["B", "C", "D"]) == []          # same change: not re-sent
    assert len(run(db, ["C", "D"])) == 1           # a further change: sent


def test_test_email_outside_window_reports_status(run):
    db = _db(8)
    sent = run(db, ["A", "B", "C"], TEST_EMAIL="true")
    assert len(sent) == 1 and sent[0][1].startswith("🧪")
    assert "stable" in sent[0][2] and "closed" in sent[0][2]


def test_test_email_shows_pending_change_without_marking_it_alerted(run):
    db = _db(8)
    sent = run(db, ["B", "C", "D"], TEST_EMAIL="true")
    assert "would send now" in sent[0][2] and "Removed" in sent[0][2]
    assert not any(r["key"].startswith("field_watch_last_alert") for r in db["golf_app_settings"])
    assert len(run(db, ["B", "C", "D"], WATCH_DAYS="10")) == 1   # the real alert still goes out


def test_test_email_without_baseline(run):
    sent = run(_db(8, baseline=""), ["A", "B"], TEST_EMAIL="true")
    assert "Set Initial Baseline" in sent[0][2]
