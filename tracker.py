"""
tracker.py — signal performance tracking + daily state.

This bot places no orders, so it can't read your real Blink fills. Instead it
tracks the HYPOTHETICAL outcome of every signal it sends, using live market
data: from the suggested entry, did price hit the stop (loss), the target
(win), or finish the day open (marked at last price)?

This gives an honest scoreboard of how the signals would have performed, so you
can compare it against what you actually did in Blink. You can also log your
real fills via /log to compare.

State persists to memory/manual_state.json.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import config


def _today() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


def _empty_state() -> dict:
    return {
        "today": _today(),
        "watchlist": [],
        "open_signals": [],      # signals emitted, not yet resolved
        "closed_signals": [],    # resolved today (win/loss/eod)
        "wins": 0,
        "losses": 0,
        "signal_pnl_pct": 0.0,   # sum of pct outcomes (per 1-unit position)
        "manual_log": [],        # your real fills, optional
    }


def load() -> dict:
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        state = _empty_state()
    if state.get("today") != _today():
        state = _empty_state()
        save(state)
    return state


def save(state: dict) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(config.STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def set_watchlist(picks: list[dict]) -> dict:
    state = load()
    state["watchlist"] = [p["symbol"] for p in picks]
    save(state)
    return state


def already_open(symbol: str) -> bool:
    state = load()
    return any(s["symbol"] == symbol for s in state["open_signals"])


def open_count() -> int:
    return len(load()["open_signals"])


def record_signal(decision: dict) -> dict:
    """Store an emitted BUY signal as an open position to monitor."""
    state = load()
    state["open_signals"].append(
        {
            "symbol": decision["symbol"],
            "entry": decision["entry"],
            "stop": decision["stop"],
            "target": decision["target"],
            "time": datetime.now(config.TZ).strftime("%H:%M"),
            "trigger": decision.get("trigger"),
        }
    )
    save(state)
    return state


def update_open(prices: dict[str, float]) -> list[dict]:
    """
    Check open signals against live prices. Resolve any that hit stop/target.
    Returns the list of newly-closed signals (for notification).
    """
    state = load()
    closed_now = []
    still_open = []
    for sig in state["open_signals"]:
        px = prices.get(sig["symbol"])
        if px is None:
            still_open.append(sig)
            continue
        outcome = None
        if px <= sig["stop"]:
            outcome = ("STOP", sig["stop"])
        elif px >= sig["target"]:
            outcome = ("TARGET", sig["target"])
        if outcome:
            label, exit_px = outcome
            pnl_pct = (exit_px - sig["entry"]) / sig["entry"] * 100.0
            rec = {**sig, "exit": round(exit_px, 2), "result": label,
                   "pnl_pct": round(pnl_pct, 2)}
            closed_now.append(rec)
            state["closed_signals"].append(rec)
            if pnl_pct >= 0:
                state["wins"] += 1
            else:
                state["losses"] += 1
            state["signal_pnl_pct"] = round(state["signal_pnl_pct"] + pnl_pct, 2)
        else:
            still_open.append(sig)
    state["open_signals"] = still_open
    save(state)
    return closed_now


def close_all_eod(prices: dict[str, float]) -> dict:
    """At end of day, mark any still-open signal at last price (no overnight)."""
    state = load()
    for sig in state["open_signals"]:
        px = prices.get(sig["symbol"], sig["entry"])
        pnl_pct = (px - sig["entry"]) / sig["entry"] * 100.0
        rec = {**sig, "exit": round(px, 2), "result": "EOD",
               "pnl_pct": round(pnl_pct, 2)}
        state["closed_signals"].append(rec)
        if pnl_pct >= 0:
            state["wins"] += 1
        else:
            state["losses"] += 1
        state["signal_pnl_pct"] = round(state["signal_pnl_pct"] + pnl_pct, 2)
    state["open_signals"] = []
    save(state)
    return state


def eod_summary_text() -> str:
    state = load()
    closed = state["closed_signals"]
    n = len(closed)
    lines = [f"📊 End-of-day signal recap · {state['today']}", ""]
    if n == 0:
        lines.append("No signals fired today.")
    else:
        for c in closed:
            sign = "✅" if c["pnl_pct"] >= 0 else "🔻"
            lines.append(
                f"{sign} {c['symbol']}  {c['result']}  "
                f"entry ${c['entry']} → exit ${c['exit']}  ({c['pnl_pct']:+.2f}%)"
            )
        wins, losses = state["wins"], state["losses"]
        wr = (wins / n * 100) if n else 0
        lines += [
            "",
            f"Record: {wins}W / {losses}L  ({wr:.0f}% win rate)",
            f"Net signal performance: {state['signal_pnl_pct']:+.2f}% "
            f"(sum of per-trade %, 1 unit each)",
        ]
    lines += [
        "",
        "Note: this is the SIGNALS' hypothetical result from suggested entries — "
        "not your real Blink P/L. Reply /log to record what you actually did.",
        "",
        config.DISCLAIMER,
    ]
    return "\n".join(lines)


def log_manual_fill(text: str) -> None:
    state = load()
    state["manual_log"].append(
        {"time": datetime.now(config.TZ).strftime("%H:%M"), "note": text}
    )
    save(state)
