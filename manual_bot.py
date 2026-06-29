"""
manual_bot.py — orchestration. Screens, runs the strategy, emits MANUAL alerts.

No broker order is ever placed. The flow:
  1. Pre-market: run_premarket_screen() -> pick ~5, send the watchlist.
  2. Session:   scan_for_signals() each poll -> on a fresh BUY, send Blink orders.
                monitor_open() each poll -> resolve stop/target, notify closes.
  3. EOD:       run_eod() -> mark remaining open at last price, send recap.
"""

from __future__ import annotations

import config
import data
import tracker
from blink_orders import format_alert
from screener import run_screen, format_watchlist
from smc_wyckoff import decide


def run_premarket_screen() -> list[dict]:
    picks = run_screen()
    tracker.set_watchlist(picks)
    from notify import notify
    notify("Today's watchlist", format_watchlist(picks))
    return picks


def _symbol_done_today(symbol: str) -> bool:
    """One signal per symbol per day (simple cooldown / anti-spam)."""
    state = tracker.load()
    if any(s["symbol"] == symbol for s in state["closed_signals"]):
        return True
    return tracker.already_open(symbol)


def scan_for_signals(allow_new: bool = True) -> list[dict]:
    """Run the strategy on the day's watchlist; alert on fresh BUYs."""
    from notify import notify

    state = tracker.load()
    watchlist = state.get("watchlist") or config.UNIVERSE[:config.DAILY_PICK_COUNT]
    fired = []

    for sym in watchlist:
        if not allow_new:
            break
        if tracker.open_count() >= config.MAX_OPEN_SIGNALS:
            break
        if _symbol_done_today(sym):
            continue

        df5 = data.get_bars(sym, "5Min", limit=120)
        df1 = data.get_bars(sym, "1Min", limit=120)
        if df5.empty or df1.empty:
            continue

        decision = decide(sym, df5, df1)
        if decision["action"] == "BUY":
            tracker.record_signal(decision)
            notify(f"BUY signal {sym}", format_alert(decision))
            fired.append(decision)

    return fired


def monitor_open() -> list[dict]:
    """Resolve open signals against live prices; notify any close."""
    from notify import notify

    state = tracker.load()
    symbols = [s["symbol"] for s in state["open_signals"]]
    if not symbols:
        return []

    prices = {s: data.get_latest_price(s) for s in symbols}
    closed = tracker.update_open(prices)
    for c in closed:
        emoji = "🎯" if c["result"] == "TARGET" else "🛑"
        msg = (
            f"{emoji} {c['symbol']} closed @ ${c['exit']} ({c['result']}) "
            f"→ {c['pnl_pct']:+.2f}%\n\n"
            "If you took this in Blink, your stop/target should have handled it.\n\n"
            + config.DISCLAIMER
        )
        notify(f"{c['symbol']} closed", msg)
    return closed


def run_eod() -> str:
    """Mark remaining open signals at last price and send the recap."""
    from notify import notify

    state = tracker.load()
    symbols = [s["symbol"] for s in state["open_signals"]]
    prices = {s: data.get_latest_price(s) for s in symbols}
    tracker.close_all_eod(prices)
    summary = tracker.eod_summary_text()
    notify("EOD recap", summary)
    return summary
