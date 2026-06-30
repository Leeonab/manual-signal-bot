"""
data.py — Alpaca IEX market-data access (DATA ONLY).

This bot never touches a trading endpoint. It only reads bars from
https://data.alpaca.markets (the paper-api host has NO market-data endpoints —
that was the bug that kept the paper bot's bars at 0).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

import config

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
        # browser UA avoids occasional Cloudflare 1010 blocks
        "User-Agent": "Mozilla/5.0 (manual-signal-bot)",
    }
)


def _bars_to_df(bars: list) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(bars)
    df = df.rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "t": "t"}
    )
    df["t"] = pd.to_datetime(df["t"])
    df = df.set_index("t").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def get_bars(symbol: str, timeframe: str = "1Min", limit: int = 120) -> pd.DataFrame:
    """
    Fetch recent bars. timeframe e.g. '1Min', '5Min'.
    Returns a DataFrame indexed by time with OHLCV columns (may be empty).
    """
    url = f"{config.ALPACA_DATA_URL}/v2/stocks/{symbol}/bars"
    end = dt.datetime.utcnow()
    # Daily bars need a long window to gather enough history (≈40 trading
    # days ≈ 8 weeks, plus holidays). Intraday bars only need a few days.
    lookback_days = 200 if "Day" in timeframe or "Week" in timeframe else 5
    start = end - dt.timedelta(days=lookback_days)
    params = {
        "timeframe": timeframe,
        "limit": limit,
        "feed": config.ALPACA_FEED,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjustment": "raw",
    }
    try:
        resp = _SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return _bars_to_df(resp.json().get("bars", []))
    except Exception as exc:  # noqa: BLE001
        print(f"[data] bars error {symbol} {timeframe}: {exc}")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def get_latest_price(symbol: str) -> float | None:
    """Latest trade/quote price via the latest-bar snapshot, with fallback."""
    url = f"{config.ALPACA_DATA_URL}/v2/stocks/{symbol}/bars/latest"
    try:
        resp = _SESSION.get(url, params={"feed": config.ALPACA_FEED}, timeout=10)
        resp.raise_for_status()
        bar = resp.json().get("bar")
        if bar:
            return float(bar["c"])
    except Exception as exc:  # noqa: BLE001
        print(f"[data] latest error {symbol}: {exc}")
    return None


def get_realtime_price(symbol: str) -> float | None:
    """
    Real-time consolidated price via Finnhub /quote (matches what Blink shows).
    Returns the current price, or None if Finnhub isn't configured / call fails.
    """
    if not config.FINNHUB_KEY:
        return None
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": config.FINNHUB_KEY},
            headers={"User-Agent": "manual-signal-bot"},
            timeout=10,
        )
        resp.raise_for_status()
        c = resp.json().get("c")
        return float(c) if c else None
    except Exception as exc:  # noqa: BLE001
        print(f"[data] finnhub quote error {symbol}: {exc}")
        return None


def market_is_open() -> bool:
    """Check Alpaca's clock. Uses the trading host's /v2/clock (read-only)."""
    url = "https://paper-api.alpaca.markets/v2/clock"
    try:
        resp = _SESSION.get(url, timeout=10)
        resp.raise_for_status()
        return bool(resp.json().get("is_open", False))
    except Exception as exc:  # noqa: BLE001
        print(f"[data] clock error: {exc}")
        return False
