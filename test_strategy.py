"""
test_strategy.py — offline sanity checks (no network).

Builds synthetic bars and confirms:
  • a clean accumulation->breakout makes the engine emit a BUY with a valid bracket
  • a downtrend yields NO_TRADE
  • the screener scores a volatile, trending name above a flat one
Run: python test_strategy.py
"""

import numpy as np
import pandas as pd

import smc_wyckoff as sw
import screener


def _frame(prices, vol=1_000_000):
    idx = pd.date_range("2026-06-29 09:30", periods=len(prices), freq="min")
    p = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": p,
            "high": p * 1.001,
            "low": p * 0.999,
            "close": p,
            "volume": np.full(len(p), vol, dtype=float),
        },
        index=idx,
    )
    return df


def test_buy_setup():
    # 5-min: zig-zag uptrend -> Wyckoff MARKUP (HH/HL + up-slope)
    seg = []
    lvl = 100.0
    for _ in range(12):
        seg += list(np.linspace(lvl, lvl + 1.2, 4)) + list(np.linspace(lvl + 1.2, lvl + 0.6, 2))
        lvl += 0.8
    df5 = _frame(seg)
    # 1-min (>=30 bars) ending in a break of structure (BOS up)
    seq = list(np.linspace(112, 112.8, 30)) + [112.9, 112.7, 112.5, 112.6, 113.2]
    df1 = _frame(seq)
    d = sw.decide("TEST", df5, df1)
    print("BUY setup ->", d["action"], d.get("phase"), d.get("trigger"),
          "score", d.get("score"), "rr", d.get("rr"))
    assert d["action"] == "BUY", f"expected BUY, got {d}"
    assert d["stop"] < d["entry"] < d["target"]
    assert d["rr"] >= sw.config.MIN_RR
    return d


def test_downtrend_no_trade():
    df5 = _frame(list(np.linspace(110, 100, 60)))      # markdown
    df1 = _frame(list(np.linspace(101, 100, 40)))
    d = sw.decide("DOWN", df5, df1)
    print("Downtrend ->", d["action"], d.get("phase"))
    assert d["action"] == "NO_TRADE"
    return d


def test_screener_ranks():
    daily_vol = _daily(np.linspace(50, 62, 30), vol=5_000_000)
    daily_flat = _daily(np.full(30, 30.0), vol=200_000)
    a = screener._score_symbol(daily_vol)
    b = screener._score_symbol(daily_flat)
    print("Screener volatile score", a["score"], "vs flat", b["score"])
    assert a["score"] > b["score"]


def _daily(prices, vol):
    idx = pd.date_range("2026-05-01", periods=len(prices), freq="D")
    p = np.array(prices, dtype=float)
    return pd.DataFrame(
        {"open": p, "high": p * 1.02, "low": p * 0.98, "close": p,
         "volume": np.full(len(p), vol, dtype=float)},
        index=idx,
    )


if __name__ == "__main__":
    test_buy_setup()
    test_downtrend_no_trade()
    test_screener_ranks()
    print("\nAll offline checks passed.")
