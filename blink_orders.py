"""
blink_orders.py — turn a strategy decision into exact, copy-able Blink orders.

Blink allows only ONE open exit order per position, so we can't place a
stop + take-profit bracket. Instead each idea is a 2-order workflow:

  1) ENTRY — Market BUY (or Limit at the entry price)
  2) EXIT  — a single SELL Trailing Stop (trail %, "sell all", GTC)

The trailing stop both caps the downside AND rides the price up, so no separate
target order is needed. The bot's tracker simulates the same trailing exit.
"""

from __future__ import annotations

import config


def trail_pct_for(decision: dict) -> float:
    """Trailing distance: the ATR stop, floored so it doesn't whipsaw."""
    return round(max(decision.get("stop_pct", config.TRAIL_MIN_PCT), config.TRAIL_MIN_PCT), 2)


def _size_for_trade(entry: float) -> dict:
    """Concrete sizing for an ~TRADE_SIZE_USD position."""
    shares = max(int(config.TRADE_SIZE_USD / entry), 1)
    return {"shares": shares, "cost": shares * entry}


def format_alert(decision: dict, use_limit_entry: bool = False) -> str:
    """Clean, mobile-friendly Telegram/email message for one BUY idea (HTML)."""
    sym = decision["symbol"]
    entry = decision["entry"]
    trail = decision.get("trail_pct") or trail_pct_for(decision)
    s = _size_for_trade(entry)
    entry_kind = "Market BUY now" if not use_limit_entry else f"Limit BUY @ ${entry:.2f}"

    init_stop = round(entry * (1 - trail / 100), 2)
    dollar_risk = round(s["shares"] * (entry - init_stop), 2)

    lines = [
        f"🟢 <b>BUY · {sym}</b>   (score {decision['score']})",
        f"Wyckoff {decision['phase']} + {decision.get('trigger', 'SMC')}",
        "",
        "<b>Place in Blink — 2 orders:</b>",
        f"① <b>Entry</b>  {entry_kind}  (~${entry:.2f})",
        f"② <b>Exit</b>   Sell <b>Trailing Stop</b> · trail <b>{trail}%</b> · Sell all · GTC",
        "",
        f"💵 <b>Buy ~{s['shares']} shares</b> (≈${s['cost']:.0f})",
        f"Initial stop ≈ ${init_stop:.2f}  ·  risk ≈ <b>${dollar_risk:.2f}</b>",
        "<i>The trailing stop rides up automatically as price climbs — it's your "
        "whole exit, no separate target needed.</i>",
        "",
        "⚠️ <i>Signal only — not advice. You place and own every order.</i>",
    ]
    return "\n".join(lines)
