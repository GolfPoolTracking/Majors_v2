import pandas as pd

from admin_mailing import payment_recipients, payout_summary_html, standings_recipients, top_rows

FOLLOW = lambda e: f"https://app/?me={e}"


def test_payment_recipients_group_unpaid_by_email_and_skip_other():
    entries = pd.DataFrame([
        {"Sheet_Row": 1, "Name": "Ann Archer", "Email": "Ann@x.com ", "Payment Method": "PayPal to me", "Paid": False},
        {"Sheet_Row": 2, "Name": "Ann Archer", "Email": "ann@x.com", "Payment Method": "Zelle to me", "Paid": False},
        {"Sheet_Row": 3, "Name": "Bob", "Email": "bob@x.com", "Payment Method": "PayPal to me", "Paid": True},
        {"Sheet_Row": 4, "Name": "Cy", "Email": "cy@x.com", "Payment Method": "Other", "Paid": False},
        {"Sheet_Row": 5, "Name": "Di", "Email": "", "Payment Method": "PayPal", "Paid": False},
    ])
    got = payment_recipients(entries, 30, FOLLOW)
    assert len(got) == 1
    ann = got[0]
    assert ann["email"] == "ann@x.com" and ann["first_name"] == "Ann" and ann["data"]["amount"] == 60
    assert [t["method"] for t in ann["data"]["teams"]] == ["PayPal to me", "Zelle to me"]
    assert ann["data"]["follow_url"].endswith("ann@x.com")


STANDINGS = pd.DataFrame([
    {"EntryId": 1, "Email": "ann@x.com", "Participant": "Ann (Team 1)", "Total": -16, "Elim": False, "DRank": "1"},
    {"EntryId": 3, "Email": "bob@x.com", "Participant": "Bob", "Total": -16, "Elim": False, "DRank": "2"},
    {"EntryId": 4, "Email": "ann@x.com", "Participant": "Ann (Team 2)", "Total": 4, "Elim": False, "DRank": "3"},
    {"EntryId": 2, "Email": "cy@x.com", "Participant": "Cy", "Total": 188, "Elim": True, "DRank": "4"},
])


def test_standings_recipients_one_email_per_person_with_payouts():
    got = {r["email"]: r for r in standings_recipients(STANDINGS, FOLLOW, {"Ann (Team 1)": 600})}
    assert set(got) == {"ann@x.com", "bob@x.com", "cy@x.com"}
    teams = got["ann@x.com"]["data"]["teams"]
    assert [(t["name"], t["rank"], t["payout"]) for t in teams] == [("Ann (Team 1)", "1", 600), ("Ann (Team 2)", "3", 0)]
    assert got["ann@x.com"]["first_name"] == "Ann" and got["cy@x.com"]["data"]["teams"][0]["elim"] is True


def test_top_rows_leave_out_eliminated_teams():
    assert [r["name"] for r in top_rows(STANDINGS, 10)] == ["Ann (Team 1)", "Bob", "Ann (Team 2)"]


def test_payout_summary_lists_who_to_pay_and_how_they_paid():
    entries = pd.DataFrame([{"Sheet_Row": 1, "Payment Method": "Revolut"}, {"Sheet_Row": 3, "Payment Method": "PayPal"}])
    podium = [{"name": "Ann (Team 1)", "payout": 600}, {"name": "Bob", "payout": 300}, {"name": "Ann (Team 2)", "payout": 0}]
    out = payout_summary_html(podium, STANDINGS, entries)
    assert "Ann (Team 1)</b>: $600" in out and "ann@x.com · paid in by Revolut" in out
    assert "Bob</b>: $300" in out and "Team 2" not in out
