"""
blink_orders.py — turn a strategy decision into exact, copy-able Blink orders.

The Blink app exposes these order types (from the app's "סוגי הוראות" screen):
  Market | Limit | Stop | Stop Limit | Trailing Stop

This bot does NOT place orders. It formats human instructions so Lee-on can
place them by hand. A long idea becomes a 3-part manual bracket:

  1) ENTRY      — buy (Market now, or Limit at the entry price)
  2) STOP-LOSS  — protective SELL Stop at the stop price
  3) TAKE-PROFIT— SELL Limit at the target price

Plus an optional Trailing-Stop alternative for the stop once in profit.
"""

from __future__ import annotations

import config


def _size_for_trade(entry: float, stop: float) -> dict:
    """Concrete sizing for an ~TRADE_SIZE_USD position."""
    risk_per_share = max(entry - stop, 0.01)
    shares = max(int(config.TRADE_SIZE_USD / entry), 1)
    cost = shares * entry
    dollar_risk = shares * risk_per_share
    return {
        "shares": shares,
        "cost": cost,
        "risk_per_share": risk_per_share,
        "dollar_risk": dollar_risk,
    }


def build_entry_orders(decision: dict, use_limit_entry: bool = False) -> dict:
    """
    Return a structured set of Blink orders for a BUY decision.
    `use_limit_entry=True` suggests a Limit buy at entry instead of Market.
    """
    sym = decision["symbol"]
    entry = decision["entry"]
    stop = decision["stop"]
    target = decision["target"]

    entry_order = (
        {"type": "Limit", "side": "BUY", "limit": entry,
         "note": f"Buy {sym} with a Limit order at ${entry:.2f} (fills at ${entry:.2f} or better)."}
        if use_limit_entry
        else
        {"type": "Market", "side": "BUY", "limit": None,
         "note": f"Buy {sym} with a Market order now (~${entry:.2f})."}
    )

    return {
        "symbol": sym,
        "entry": entry_order,
        "stop_loss": {
            "type": "Stop", "side": "SELL", "stop": stop,
            "note": f"Protective SELL Stop at ${stop:.2f} (−{decision['stop_pct']}%). "
                    f"Triggers a market sell if price falls to ${stop:.2f}.",
        },
        "take_profit": {
            "type": "Limit", "side": "SELL", "limit": target,
            "note": f"SELL Limit at ${target:.2f} (+{decision['target_pct']}%) to lock the profit.",
        },
        "trailing_alt": {
            "type": "Trailing Stop", "side": "SELL",
            "trail_pct": round(decision["stop_pct"], 2),
            "note": f"Alternative to the fixed stop: SELL Trailing Stop at "
                    f"{decision['stop_pct']}% — the stop follows the price up and "
                    f"locks gains automatically.",
        },
        "rr": decision["rr"],
        "sizing": _size_for_trade(entry, stop),
    }


def format_alert(decision: dict, use_limit_entry: bool = False) -> str:
    """Clean, mobile-friendly Telegram/email message for one BUY idea (HTML)."""
    o = build_entry_orders(decision, use_limit_entry)
    sym = o["symbol"]
    entry = decision["entry"]
    entry_kind = "Market BUY now" if not use_limit_entry else f"Limit BUY @ ${entry:.2f}"
    s = o["sizing"]

    lines = [
        f"🟢 <b>BUY · {sym}</b>   (score {decision['score']})",
        f"Wyckoff {decision['phase']} + {decision.get('trigger', 'SMC')}",
        "",
        "<b>Place in Blink:</b>",
        f"① <b>Entry</b>  {entry_kind}  (~${entry:.2f})",
        f"② <b>Stop</b>   Sell Stop @ ${decision['stop']:.2f}  (−{decision['stop_pct']}%)",
        f"③ <b>Target</b> Sell Limit @ ${decision['target']:.2f}  (+{decision['target_pct']}%)",
        "",
        f"💵 <b>Buy ~{s['shares']} shares</b> (≈${s['cost']:.0f})",
        f"Risk ≈ <b>${s['dollar_risk']:.2f}</b> if stopped  ·  R:R <b>{o['rr']}:1</b>",
        "",
        f"<i>Tip: a Trailing Stop at {decision['stop_pct']}% can replace the fixed stop "
        "to ride gains.</i>",
        "",
        "⚠️ <i>Signal only — not advice. You place and own every order.</i>",
    ]
    return "\n".join(lines)
