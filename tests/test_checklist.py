from checklist import build_checklist

BASE = dict(pinned=True, field_count=20, n_entries=0, n_unpaid=0, entries_open_sent=0, started=False, closed=False)


def labels(items):
    return {label.split(" (")[0]: ok for ok, label, _ in items}


def test_new_tournament_shows_what_is_missing():
    got = labels(build_checklist({}, **BASE))
    assert got == {"Pinned as the active tournament": True, "Entry close time saved": False, "Logo added": False,
                   "Rules saved for this tournament": False, "Field loaded": True,
                   "Field alert starting list set": False, "'Entries open' email sent": False}


def test_after_close_tracks_payments_and_payouts_not_baseline():
    cfg = {"close_time": "x", "logo_url": "x", "rules": "r", "payout_config": '{"payout_confirmed": true}'}
    got = labels(build_checklist(cfg, **dict(BASE, n_entries=10, n_unpaid=3, entries_open_sent=140, started=True, closed=True)))
    assert "Field alert starting list set" not in got
    assert got["3 unpaid entries"] is False and got["Payouts confirmed and shown publicly"] is True
    assert got["'Entries open' email sent"] is True
