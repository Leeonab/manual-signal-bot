"""
screener.py — daily pre-market screen.

Each morning, rank the UNIVERSE and pick the best ~5 names to FOCUS on today.
The strategy engine (smc_wyckoff) then runs intraday only on these picks.

This is the new step the paper bot didn't have (it always traded a fixed 8).
Ranking favors names that are good for long intraday day-trading:

  • Volatility   — ATR% over the last ~14 daily bars (need movement to profit)
  • Rel. volume  — last session volume vs its 20-day average (liquidity/interest)
  • Trend        — close above its 20-day EMA (aligns with long-only Wyckoff bias)
  • Gap          — modest up-gap is fine; huge gaps are penalized (chasey)

All inputs are daily bars from Alpaca data. Pure ranking — no orders.
"""

from __future__ import annotations

import pandas as pd

import config
import data
from smc_wyckoff import atr, ema


def _score_symbol(df_daily: pd.DataFrame) -> dict | None:
    if df_daily is None or len(df_daily) < 20:
        return None

    close = df_daily["close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    atr14 = float(atr(df_daily, 14).iloc[-1])
    atr_pct = atr14 / last * 100.0

    avg_vol = float(df_daily["volume"].iloc[-20:].mean())
    rel_vol = float(df_daily["volume"].iloc[-1]) / avg_vol if avg_vol else 0.0

    ema20 = float(ema(close, 20).iloc[-1])
    above_trend = last > ema20

    gap_pct = (last - prev) / prev * 100.0 if prev else 0.0

    # Composite score (display 0-100-ish). Tunable weights.
    score = 0.0
    score += min(atr_pct, 6.0) * 8.0          # volatility, capped
    score += min(rel_vol, 3.0) * 12.0         # relative volume, capped
    score += 15.0 if above_trend else 0.0     # trend alignment (long bias)
    score -= max(abs(gap_pct) - 3.0, 0.0) * 4.0  # penalize big gaps only

    return {
        "atr_pct": round(atr_pct, 2),
        "rel_vol": round(rel_vol, 2),
        "above_trend": above_trend,
        "gap_pct": round(gap_pct, 2),
        "last": round(last, 2),
        "score": round(score, 1),
    }


def run_screen(universe: list[str] | None = None, top_n: int | None = None) -> list[dict]:
    """
    Rank the universe and return the top picks (list of dicts, best first).
    """
    universe = universe or config.UNIVERSE
    top_n = top_n or config.DAILY_PICK_COUNT

    ranked = []
    for sym in universe:
        df_daily = data.get_bars(sym, timeframe="1Day", limit=40)
        metrics = _score_symbol(df_daily)
        if metrics is None:
            continue
        metrics["symbol"] = sym
        ranked.append(metrics)

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:top_n]


def format_watchlist(picks: list[dict]) -> str:
    """Pre-market watchlist message."""
    if not picks:
        return "Pre-market screen: no candidates passed today."
    lines = ["📅 Today's watchlist (top picks to day-trade):", ""]
    for i, p in enumerate(picks, 1):
        trend = "↑trend" if p["above_trend"] else "↓below-trend"
        lines.append(
            f"{i}. {p['symbol']}  ${p['last']}  "
            f"[ATR {p['atr_pct']}% · RVOL {p['rel_vol']}x · {trend} · gap {p['gap_pct']}%]  "
            f"score {p['score']}"
        )
    lines += [
        "",
        "I'll watch these intraday and ping you the moment a Wyckoff+SMC "
        "long setup triggers, with the exact Blink orders.",
        "",
        config.DISCLAIMER,
    ]
    return "\n".join(lines)
