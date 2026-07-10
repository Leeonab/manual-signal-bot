"""
manual_bot.py — orchestration. Screens, runs the strategy, emits MANUAL alerts.

No broker order is ever placed. The flow:
  1. Pre-market: run_premarket_screen() -> pick ~5, send the watchlist.
  2. Session:   scan_for_signals() each poll -> on a fresh BUY, send Blink orders.
                monitor_open() each poll -> resolve stop/target, notify closes.
  3. EOD:       run_eod() -> mark remaining open at last price, send recap.
"""

from __future__ import annotations

import time

import config
import data
import tracker
from blink_orders import format_alert, trail_pct_for
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
            # Verify against the real-time consolidated price (matches Blink).
            real = data.get_realtime_price(sym)
            if real:
                bar_price = decision["entry"]
                drift = abs(real - bar_price) / real * 100
                if drift > config.STALE_THRESHOLD_PCT:
                    print(f"[scan] {sym} skipped: stale feed "
                          f"(bar ${bar_price} vs real ${real}, {drift:.1f}% off)")
                    continue
                decision = _reanchor(decision, real)
            decision["trail_pct"] = trail_pct_for(decision)
            # Delay metric: how stale was the data when the signal fired?
            try:
                decision["signal_age_sec"] = int(max(time.time() - df1.index[-1].timestamp(), 0))
            except Exception:  # noqa: BLE001
                decision["signal_age_sec"] = None
            tracker.record_signal(decision)
            notify(f"BUY signal {sym}", format_alert(decision))
            fired.append(decision)

    return fired


def _reanchor(decision: dict, real_price: float) -> dict:
    """Re-anchor the bracket to the real-time price, keeping the % distances."""
    sp, tp = decision["stop_pct"], decision["target_pct"]
    decision["entry"] = round(real_price, 2)
    decision["stop"] = round(real_price * (1 - sp / 100), 2)
    decision["target"] = round(real_price * (1 + tp / 100), 2)
    return decision


def monitor_open() -> list[dict]:
    """Resolve open signals against live prices; notify any close."""
    from notify import notify

    state = tracker.load()
    symbols = [s["symbol"] for s in state["open_signals"]]
    if not symbols:
        return []

    # Prefer the real-time consolidated price; fall back to the IEX bar.
    prices = {s: (data.get_realtime_price(s) or data.get_latest_price(s)) for s in symbols}
    closed = tracker.update_open(prices)
    for c in closed:
        emoji = "🎯" if c["pnl_pct"] >= 0 else "🛑"
        msg = (
            f"{emoji} <b>{c['symbol']}</b> trailing stop hit @ ${c['exit']}   "
            f"<b>{c['pnl_pct']:+.2f}%</b>\n\n"
            "<i>If you're in this on Blink, your Trailing Stop sold automatically.</i>"
        )
        notify(f"{c['symbol']} exited", msg)
    return closed


def run_eod() -> str:
    """Mark remaining open signals at last price and send the recap."""
    from notify import notify

    state = tracker.load()
    symbols = [s["symbol"] for s in state["open_signals"]]
    prices = {s: (data.get_realtime_price(s) or data.get_latest_price(s)) for s in symbols}
    tracker.close_all_eod(prices)
    summary = tracker.eod_summary_text()
    notify("EOD recap", summary)
    return summary
