"""
smc_wyckoff.py — Wyckoff (5-min bias) + SMC (1-min entry) strategy engine.

CLEAN-ROOM rebuild from the paper-bot handoff spec (v5.0). Pure functions:
they take pandas DataFrames and return plain dicts, so they are unit-testable
and back-testable with no network or broker dependency.

Decision logic (mirrors the handoff):
  Step 1 — Wyckoff on the 5-min frame -> bias: LONG_OK | NO_TRADE
           LONG_OK only in Accumulation or Markup.
  Step 2 — SMC on the 1-min frame (only when LONG_OK) -> needs a PRIMARY trigger:
             (a) liquidity sweep of a recent swing low + reclaim, OR
             (b) CHoCH / BOS up (break of structure).
           Confluence boosters: bullish FVG mitigation, bullish order-block
           retest, price above VWAP.
  A BUY fires only when (bias == LONG_OK) AND (a primary SMC trigger present).
  RSI overbought guard (>= RSI_OVERBOUGHT) blocks blow-off entries.
  Risk: ATR-based bracket. SL = clamp(ATR*1.2, 0.5%, 1.5%),
        TP = clamp(ATR*2.0, 0.8%, 2.5%), enforced min reward:risk = 1.5.

Expected DataFrame columns (lower-case): open, high, low, close, volume.
Index should be time-ordered (oldest first).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session-naive rolling VWAP over the provided window."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (typical * df["volume"]).cumsum()
    vol = df["volume"].cumsum().replace(0, np.nan)
    return pv / vol


def swing_points(df: pd.DataFrame, left: int = 2, right: int = 2):
    """Return boolean Series marking fractal swing highs / lows."""
    highs, lows = df["high"], df["low"]
    n = len(df)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window_h = highs.iloc[i - left : i + right + 1]
        window_l = lows.iloc[i - left : i + right + 1]
        if highs.iloc[i] == window_h.max() and (window_h.idxmax() == highs.index[i]):
            is_high[i] = True
        if lows.iloc[i] == window_l.min() and (window_l.idxmin() == lows.index[i]):
            is_low[i] = True
    return pd.Series(is_high, index=df.index), pd.Series(is_low, index=df.index)


# ---------------------------------------------------------------------------
# Step 1 — Wyckoff bias on the higher timeframe (5-min)
# ---------------------------------------------------------------------------
def wyckoff_bias(df5: pd.DataFrame) -> dict:
    """
    Classify the Wyckoff phase and produce a long bias.
    Returns {phase, bias, reasons[]}.
    """
    if df5 is None or len(df5) < 40:
        return {"phase": "UNKNOWN", "bias": "NO_TRADE", "reasons": ["not enough 5m bars"]}

    close = df5["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50) if len(df5) >= 50 else ema(close, 20)

    # Trading range over the recent window
    lookback = min(40, len(df5))
    window = df5.iloc[-lookback:]
    range_hi = window["high"].max()
    range_lo = window["low"].min()
    rng = max(range_hi - range_lo, 1e-9)
    pos_in_range = (close.iloc[-1] - range_lo) / rng   # 0 (bottom) .. 1 (top)

    # EMA slope (normalized)
    slope = (ema20.iloc[-1] - ema20.iloc[-5]) / max(abs(ema20.iloc[-5]), 1e-9)

    # Structure: higher highs / higher lows over the window
    sh, sl = swing_points(window, 2, 2)
    swing_highs = window["high"][sh].tolist()
    swing_lows = window["low"][sl].tolist()
    hh = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
    hl = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]

    # Spring detection: dipped below range low then closed back inside
    recent = df5.iloc[-6:]
    spring = (recent["low"].min() < range_lo * 1.001) and (close.iloc[-1] > range_lo)

    above_emas = close.iloc[-1] > ema20.iloc[-1] >= ema50.iloc[-1] * 0.999

    reasons = []
    phase = "RANGE"

    # Markup: trending up, above EMAs, making HH/HL
    if slope > 0.0008 and above_emas and (hh or hl):
        phase = "MARKUP"
        reasons.append("up-slope EMA + above EMAs + HH/HL")
    # Accumulation: near range lows, flattening/turning up, spring present
    elif pos_in_range < 0.45 and slope > -0.0005 and (spring or hl):
        phase = "ACCUMULATION"
        reasons.append("lower range + turning up / spring")
    # Distribution: near highs, slope rolling over
    elif pos_in_range > 0.7 and slope < 0:
        phase = "DISTRIBUTION"
        reasons.append("upper range + down-slope")
    # Markdown: trending down below EMAs
    elif slope < -0.0008 and close.iloc[-1] < ema20.iloc[-1]:
        phase = "MARKDOWN"
        reasons.append("down-slope + below EMA")

    bias = "LONG_OK" if phase in ("ACCUMULATION", "MARKUP") else "NO_TRADE"
    return {
        "phase": phase,
        "bias": bias,
        "pos_in_range": round(float(pos_in_range), 3),
        "slope": round(float(slope), 5),
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Step 2 — SMC entry on the lower timeframe (1-min)
# ---------------------------------------------------------------------------
def smc_entry(df1: pd.DataFrame) -> dict:
    """
    Look for a primary bullish SMC trigger on the 1-min frame, plus confluence.
    Returns {trigger, primary(bool), boosters[], rsi, vwap}.
    """
    if df1 is None or len(df1) < 30:
        return {"trigger": None, "primary": False, "boosters": [], "rsi": 50.0}

    close = df1["close"]
    last = close.iloc[-1]
    r = float(rsi(close).iloc[-1])
    vw = float(vwap(df1).iloc[-1])

    sh, sl = swing_points(df1.iloc[-30:], 2, 2)
    swing_lows_idx = df1.iloc[-30:]["low"][sl]
    swing_highs_idx = df1.iloc[-30:]["high"][sh]

    primary = None

    # (a) Liquidity sweep of a recent swing low + reclaim
    if len(swing_lows_idx) >= 1:
        recent_low = float(swing_lows_idx.iloc[-1])
        bar_low_5 = df1["low"].iloc[-5:].min()
        if bar_low_5 < recent_low and last > recent_low:
            primary = "LIQUIDITY_SWEEP_RECLAIM"

    # (b) CHoCH / BOS up — close breaks the most recent swing high
    if primary is None and len(swing_highs_idx) >= 1:
        recent_high = float(swing_highs_idx.iloc[-1])
        if last > recent_high and close.iloc[-2] <= recent_high:
            primary = "BOS_UP"

    # Confluence boosters
    boosters = []
    # bullish FVG: gap between candle i-2 high and candle i low (3-candle), still open
    h = df1["high"]
    low = df1["low"]
    if len(df1) >= 3 and low.iloc[-1] > h.iloc[-3] and last > low.iloc[-1]:
        boosters.append("BULLISH_FVG")
    # order-block retest: down candle followed by strong up move, price retests its open
    o = df1["open"]
    if len(df1) >= 4:
        ob_down = o.iloc[-4] > close.iloc[-4]
        strong_up = close.iloc[-3] > o.iloc[-4]
        retest = abs(last - o.iloc[-4]) / max(o.iloc[-4], 1e-9) < 0.002
        if ob_down and strong_up and retest:
            boosters.append("ORDER_BLOCK_RETEST")
    if last > vw:
        boosters.append("ABOVE_VWAP")

    return {
        "trigger": primary,
        "primary": primary is not None,
        "boosters": boosters,
        "rsi": round(r, 1),
        "vwap": round(vw, 4),
        "last": float(last),
    }


# ---------------------------------------------------------------------------
# Risk bracket
# ---------------------------------------------------------------------------
def risk_bracket(entry: float, atr_val: float) -> dict:
    """ATR-based SL/TP with clamps and a min reward:risk enforcement."""
    sl_dist = atr_val * config.SL_ATR_MULT
    sl_dist = min(max(sl_dist, entry * config.SL_MIN_PCT), entry * config.SL_MAX_PCT)
    tp_dist = atr_val * config.TP_ATR_MULT
    tp_dist = min(max(tp_dist, entry * config.TP_MIN_PCT), entry * config.TP_MAX_PCT)

    # enforce minimum reward:risk
    if tp_dist < sl_dist * config.MIN_RR:
        tp_dist = sl_dist * config.MIN_RR

    stop = round(entry - sl_dist, 2)
    target = round(entry + tp_dist, 2)
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    return {
        "entry": round(entry, 2),
        "stop": stop,
        "target": target,
        "stop_pct": round(sl_dist / entry * 100, 2),
        "target_pct": round(tp_dist / entry * 100, 2),
        "rr": rr,
    }


# ---------------------------------------------------------------------------
# Top-level decision
# ---------------------------------------------------------------------------
def decide(symbol: str, df5: pd.DataFrame, df1: pd.DataFrame) -> dict:
    """
    Combine Wyckoff bias + SMC entry into a single decision dict.
    action is 'BUY' or 'NO_TRADE'. When BUY, includes the risk bracket.
    The 0-100 score is for display only (>=70 => BUY), as in the paper bot.
    """
    bias = wyckoff_bias(df5)
    result = {
        "symbol": symbol,
        "action": "NO_TRADE",
        "phase": bias["phase"],
        "bias": bias["bias"],
        "score": 0,
        "reasons": list(bias.get("reasons", [])),
    }

    if bias["bias"] != "LONG_OK":
        result["reasons"].append(f"bias={bias['bias']}")
        return result

    entry_sig = smc_entry(df1)
    result.update(
        {
            "trigger": entry_sig["trigger"],
            "boosters": entry_sig["boosters"],
            "rsi": entry_sig["rsi"],
        }
    )

    if entry_sig["rsi"] >= config.RSI_OVERBOUGHT:
        result["reasons"].append(f"RSI guard {entry_sig['rsi']} >= {config.RSI_OVERBOUGHT}")
        return result

    if not entry_sig["primary"]:
        result["reasons"].append("no primary SMC trigger")
        return result

    # Score: 70 base for a valid setup + 10 per booster (display only)
    score = 70 + 10 * len(entry_sig["boosters"])
    score = min(score, 100)

    entry = entry_sig["last"]
    atr_val = float(atr(df1).iloc[-1])
    bracket = risk_bracket(entry, atr_val)

    if bracket["rr"] < config.MIN_RR:
        result["reasons"].append(f"RR {bracket['rr']} < {config.MIN_RR}")
        return result

    result.update(
        {
            "action": "BUY",
            "score": score,
            "reasons": result["reasons"]
            + [f"trigger={entry_sig['trigger']}", *entry_sig["boosters"]],
            **bracket,
        }
    )
    return result
