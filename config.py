"""
config.py — central configuration for the MANUAL-SIGNAL bot.

This is the sibling of Lee-on's autonomous paper-trading bot. The key
difference: this bot NEVER places an order. It screens, runs the Wyckoff+SMC
strategy, and pushes a Telegram alert telling YOU exactly what to buy and which
stop/take-profit orders to set in the Blink app. You place every order by hand
with real money.

All secrets come from environment variables (set in Railway → Variables). No
secret values live in this file.
"""

import os
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Identity / timezone
# ---------------------------------------------------------------------------
BOT_NAME = "manual-signal-bot"
TZ = ZoneInfo("Asia/Jerusalem")          # all schedule times are Israel local
US_MARKET_TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Universe to screen each morning
# ---------------------------------------------------------------------------
# The daily screener ranks this universe and picks the best ~5 to trade today.
# Keep it liquid and Blink-tradable. The original paper bot's 8 names are the
# core; the rest widen the pool so the daily pick is meaningful.
# Tuned for a small account day-trading up to ~$1000/trade: liquid, high
# intraday movement, mostly lower/mid share prices so $1000 buys a workable
# number of shares. NOT predictions — just names with the liquidity + volatility
# a day-trade screen needs. The daily screener still ranks these each morning.
UNIVERSE = [
    # liquid, high-beta movers (mostly lower/mid priced)
    "NVDA", "TSLA", "AAPL", "AMD", "PLTR", "SOFI", "F", "INTC",
    "NIO", "RIVN", "LCID", "HOOD", "COIN", "MARA", "RIOT", "SMCI",
    "MU", "UBER", "BABA", "AAL", "CCL", "NU", "T",
    # liquid, volatile ETFs
    "SPY", "QQQ", "IWM", "GLD", "SLV", "IBIT", "USO", "SOXL",
]

DAILY_PICK_COUNT = 5          # how many names to trade on a given day

# ---------------------------------------------------------------------------
# Strategy / risk parameters  (mirror the paper bot)
# ---------------------------------------------------------------------------
POSITION_SIZE = 0.10          # suggested 10% of capital per trade (advisory only)
TRADE_SIZE_USD = 1000         # approx $ you put into one trade (for share-count hint)
MAX_OPEN_SIGNALS = 3          # don't suggest more than 3 live "open" ideas at once
COOLDOWN_MINUTES = 60         # after a name's signal closes, wait before re-alerting
MIN_RR = 1.5                  # minimum reward:risk to emit a signal
RSI_OVERBOUGHT = 78           # block blow-off entries at/above this RSI

# ATR-based bracket sizing (same clamps as the paper bot)
SL_ATR_MULT = 1.2
SL_MIN_PCT = 0.005            # 0.5%
SL_MAX_PCT = 0.015           # 1.5%
TP_ATR_MULT = 2.0
TP_MIN_PCT = 0.008           # 0.8%
TP_MAX_PCT = 0.025           # 2.5%

# Blink exit is a single Sell Trailing Stop. The trail distance = the strategy's
# ATR stop, but never tighter than this floor (tight trails whipsaw on volatile
# names). You can widen it in the app if you see early exits.
TRAIL_MIN_PCT = 1.0

# (legacy advisory, unused)
TRAIL_BREAKEVEN_AT = 0.010
TRAIL_LOCK_AT = 0.015
TRAIL_LOCK_TO = 0.005

POLL_INTERVAL = 60           # seconds between bar polls during the session

# ---------------------------------------------------------------------------
# Daily schedule (Israel local time)
# ---------------------------------------------------------------------------
SCHED_HEARTBEAT = "09:00"        # "bot active" ping
SCHED_PREMARKET_SCREEN = "16:00" # run the daily 5-pick screen, send watchlist
SCHED_SESSION_START = "16:30"    # US open ~ start emitting live signals
SCHED_STOP_NEW = "22:00"         # (legacy, unused) stop emitting NEW buy ideas
SCHED_EOD_SUMMARY = "22:50"      # end-of-day P/L + recap
# Stop opening NEW ideas when fewer than this many minutes remain until the US
# close (driven by the real market clock, so it's DST/holiday-proof).
STOP_NEW_MIN_BEFORE_CLOSE = 60

# ---------------------------------------------------------------------------
# Alpaca market data (DATA ONLY — this bot has no trading endpoint calls)
# ---------------------------------------------------------------------------
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()
ALPACA_DATA_URL = "https://data.alpaca.markets"   # NOT paper-api (no data there)
ALPACA_FEED = "iex"

# ---------------------------------------------------------------------------
# News (optional, reuses the paper bot's approach)
# ---------------------------------------------------------------------------
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

# Real-time price accuracy: Finnhub /quote gives the consolidated price (matches
# Blink). If the IEX bar the strategy used differs from the real price by more
# than this %, the feed is stale → skip the signal instead of sending a bad one.
STALE_THRESHOLD_PCT = 1.5

# ---------------------------------------------------------------------------
# Notifications — NEW dedicated Telegram bot (separate from the paper bot)
# ---------------------------------------------------------------------------
# Create a brand-new bot via @BotFather and put its token here, so manual
# signals never blur with the paper bot's alerts.
TELEGRAM_TOKEN = os.getenv("MANUAL_TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("MANUAL_TELEGRAM_CHAT_ID", "").strip()

# Optional email mirror via Resend (Railway blocks SMTP)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev").strip()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
STATE_DIR = os.getenv("STATE_DIR", "memory")
STATE_FILE = os.path.join(STATE_DIR, "manual_state.json")

# A standing disclaimer appended to every actionable alert.
DISCLAIMER = (
    "⚠️ Signal only — not financial advice. Algorithmic idea, not a "
    "guaranteed trade. You place and own every order. Size to your own risk."
)
