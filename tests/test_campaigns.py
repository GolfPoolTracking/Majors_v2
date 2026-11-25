import datetime
import types

import campaigns
import send_campaign
from campaigns import UK_TZ, build_job, build_message, format_deadline, render_html

CLOSE = UK_TZ.localize(datetime.datetime(2026, 12, 3, 5, 0))


def test_deadline_shows_et_and_uk():
    assert format_deadline(CLOSE) == "12:00 AM ET Thu 3 Dec / 5:00 AM UK Thu 3 Dec"


def test_job_defaults_and_ids():
    job = build_job("closing_soon", 907, "Hero World Challenge", CLOSE, "**Rules**")
    assert job["campaign_id"] == "907:closing_soon"
    assert job["subject"] == "⏳ Hero World Challenge Sweepstakes - Entries close Thursday"
    assert job["entry_url"].endswith("tourney_id=907")
    assert build_job("entries_open", 907, "Hero", CLOSE, "", subject="Custom ").get("subject") == "Custom"


def test_render_personalises_escapes_and_renders_rules():
    job = build_job("entries_open", 907, "Hero", CLOSE, "### 🏆 Rules\n\n**Pick 5**", intro="Line one\n\nLine <two>")
    body = render_html(job, "Orla", "https://x/unsub?token=abc")
    assert "Hello Orla," in body and "<p>Line &lt;two&gt;</p>" in body
    assert "<strong>Pick 5</strong>" in body and "<h3>" in body
    assert "https://x/unsub?token=abc" in body and "view=signup" in body
    assert "Hello," in render_html(job, "", "#")


def test_message_has_unsubscribe_header():
    job = build_job("entries_open", 907, "Hero", CLOSE, "")
    msg = build_message(job, "me@gmail.com", "you@x.com", "You", "https://u/1")
    assert msg["List-Unsubscribe"] == "<https://u/1>, <mailto:me@gmail.com?subject=unsubscribe>"
    assert msg["Subject"] == "Hero Sweepstakes - Entries Open"


def test_pick_recipients():
    subs = [{"email": "b@x"}, {"email": "a@x"}, {"email": "c@x", "unsubscribed_at": "2026-01-01"}, {"email": "d@x"}]
    got = send_campaign.pick_recipients(subs, {"d@x"}, {"b@x"}, "closing_soon")
    assert [s["email"] for s in got] == ["a@x"]
    assert [s["email"] for s in send_campaign.pick_recipients(subs, set(), {"b@x"}, "entries_open")] == ["a@x", "b@x", "d@x"]


# --- send_campaign.main with a fake Supabase and a fake mailer ---

class _Q:
    def __init__(self, db, t): self.db, self.t, self.f, self.op = db, t, {}, ("select", None, None)
    def select(self, *_): return self
    def eq(self, c, v): self.f[c] = v; return self
    def upsert(self, row, on_conflict=None): self.op = ("upsert", row, on_conflict); return self
    def insert(self, row): self.op = ("insert", row, None); return self

    def execute(self):
        kind, row, oc = self.op
        rows = self.db.setdefault(self.t, [])
        if kind == "select":
            return types.SimpleNamespace(data=[r for r in rows if all(str(r.get(k)) == str(v) for k, v in self.f.items())])
        keys = (oc or "key").split(",")
        rows[:] = [r for r in rows if kind == "insert" or any(r.get(k) != row.get(k) for k in keys)]
        rows.append(dict(row))
        return types.SimpleNamespace(data=[row])


class _Mailer:
    sent = []
    fail = set()
    def __init__(self, *a): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def send(self, msg):
        if msg["To"] in self.fail: raise RuntimeError("bounced")
        _Mailer.sent.append((msg["To"], msg["Subject"]))


def _setup(monkeypatch, kind="closing_soon", requested_hours_ago=0):
    for k, v in {"EMAIL_SENDER": "me@gmail.com", "EMAIL_PASSWORD": "pw"}.items():
        monkeypatch.setenv(k, v)
    job = build_job(kind, "907", "Hero", CLOSE, "")
    job.update(status="queued", requested_at=(datetime.datetime.now(datetime.timezone.utc)
                                              - datetime.timedelta(hours=requested_hours_ago)).isoformat())
    db = {
        "golf_app_settings": [{"key": campaigns.EMAIL_JOB_KEY, "value": job}],
        "golf_subscribers": [
            {"email": "a@x", "first_name": "A", "unsub_token": "t1"},
            {"email": "b@x", "first_name": "B", "unsub_token": "t2"},
            {"email": "c@x", "first_name": "C", "unsub_token": "t3", "unsubscribed_at": "2026-01-01"},
            {"email": "d@x", "first_name": "D", "unsub_token": "t4"},
        ],
        "golf_entries": [{"tournament_id": "907", "email": "B@x "}],
        "golf_email_sends": [],
    }
    _Mailer.sent, _Mailer.fail = [], set()
    return db, types.SimpleNamespace(table=lambda t: _Q(db, t))


def _job(db):
    return next(r["value"] for r in db["golf_app_settings"] if r["key"] == campaigns.EMAIL_JOB_KEY)


def test_send_skips_unsubscribed_and_entered_then_summarises(monkeypatch):
    db, sb = _setup(monkeypatch)
    _Mailer.fail = {"d@x"}
    send_campaign.main(sb, _Mailer, sleep=lambda s: None)
    assert [to for to, _ in _Mailer.sent] == ["a@x", "me@gmail.com"]      # a sent, then the admin summary
    assert "1 sent, 1 failed" in _Mailer.sent[-1][1]
    assert {r["email"]: r["status"][:6] for r in db["golf_email_sends"]} == {"a@x": "sent", "d@x": "failed"}
    assert _job(db)["status"] == "done"


def test_rerun_only_retries_unsent(monkeypatch):
    db, sb = _setup(monkeypatch)
    _Mailer.fail = {"d@x"}
    send_campaign.main(sb, _Mailer, sleep=lambda s: None)
    _job(db)["status"] = "queued"
    _Mailer.sent, _Mailer.fail = [], set()
    send_campaign.main(sb, _Mailer, sleep=lambda s: None)
    assert [to for to, _ in _Mailer.sent] == ["d@x", "me@gmail.com"]
    assert {r["status"] for r in db["golf_email_sends"]} == {"sent"}


def test_nothing_queued_or_stale_job_sends_nothing(monkeypatch):
    db, sb = _setup(monkeypatch, requested_hours_ago=13)
    send_campaign.main(sb, _Mailer, sleep=lambda s: None)
    assert _Mailer.sent == [] and _job(db)["status"] == "expired"
    send_campaign.main(sb, _Mailer, sleep=lambda s: None)
    assert _Mailer.sent == []
