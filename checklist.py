"""The admin sidebar's setup checklist for a tournament. Pure logic; app.py gathers the inputs."""
import json


def build_checklist(cfg, *, pinned, field_count, n_entries, n_unpaid, entries_open_sent, started, closed):
    """Returns [(ok, label, hint)]. ok is True (done), False (to do) or None (just information)."""
    cfg = cfg or {}
    try:
        payout = cfg.get("payout_config") or {}
        payout = json.loads(payout) if isinstance(payout, str) else payout
    except Exception:
        payout = {}
    items = [
        (pinned, "Pinned as the active tournament", "Sidebar → 📌 Pin as active tournament, so the admin page and field watch use it."),
        (bool(cfg.get("close_time")), "Entry close time saved", "Not saved, so it defaults to 5:00 AM UK on the start date. Save it under ⏳ Entries Close Time."),
        (bool(cfg.get("logo_url")), "Logo added", "Add a Logo URL under 🖼️ Branding. It shows on the page and in emails."),
        (bool((cfg.get("rules") or "").strip()), "Rules saved for this tournament", "Using the default rules. Check the entry fee and deadline wording are right."),
        (field_count > 0, f"Field loaded ({field_count} players)" if field_count else "Field loaded", "The API hasn't returned the field yet. Check back closer to the event."),
    ]
    if not started:
        items.append((bool(cfg.get("alerted_field")), "Field alert starting list set",
                      "🏌️ Field tab → Set Initial Baseline, so the field watch can spot withdrawals."))
    items.append((entries_open_sent > 0, f"'Entries open' email sent ({entries_open_sent})" if entries_open_sent else "'Entries open' email sent",
                  "📣 Mailing List tab → Entries open."))
    if n_entries:
        items.append((n_unpaid == 0, "All entries paid" if n_unpaid == 0 else f"{n_unpaid} unpaid {'entry' if n_unpaid == 1 else 'entries'}",
                      "📣 Mailing List tab → Payment reminder, or tick Paid in 📝 Entries."))
    if closed or started:
        items.append((bool(payout.get("payout_confirmed")), "Payouts confirmed and shown publicly",
                      "💵 Financials → Payout Calculator → confirm and save."))
    return items
