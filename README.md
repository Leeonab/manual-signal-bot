# manual-signal-bot

A sibling to Lee-on's autonomous paper-trading bot. **Same Wyckoff + SMC
strategy — but it never places an order.** Instead, each trading day it:

1. **Screens** a wider universe pre-market and picks the best ~5 names to focus on.
2. **Watches** those names intraday and, the moment a long setup triggers, sends
   a **Telegram alert with the exact Blink orders** to place by hand.
3. **Tracks** how every signal would have done (stop vs target) and sends an
   **end-of-day recap** so you can compare against your real Blink fills.

You place every order yourself, with real money, in the Blink app.

> ⚠️ **Not financial advice.** These are algorithmic signals, not guaranteed
> trades. You own every decision and every order. Size to your own risk.

---

## How it differs from the paper bot

| | Paper bot (`trading-bot`) | This bot (`manual-signal-bot`) |
|---|---|---|
| Places orders | Yes — Alpaca bracket orders, auto | **No — alerts only** |
| Account | Alpaca paper | **Your real Blink account (manual)** |
| Universe | Fixed 8 assets | **Daily ~5-pick screen** from a wider list |
| Money | Paper | **Real** |
| Notifications | Existing Telegram bot | **New dedicated Telegram bot** |
| P/L source | Alpaca `equity−last_equity` | **Signal-simulated** (stop/target hit) + your manual log |
| EOD | Force-closes positions flat | Marks open signals at last price (you manage real exits) |

The strategy engine `smc_wyckoff.py` is a clean-room rebuild from the paper
bot's handoff spec. If you drop in the original `smc_wyckoff.py`, it's a direct
swap — the rest of the code calls `decide(symbol, df5, df1)` and the same config
names.

---

## Files

```
config.py        — universe, params, schedule, env-var names, disclaimer
data.py          — Alpaca IEX bars (DATA ONLY; no trading endpoints)
screener.py      — daily pre-market ~5-pick screen + watchlist message
smc_wyckoff.py   — Wyckoff (5m bias) + SMC (1m entry) engine, ATR bracket
blink_orders.py  — formats entry/stop/target into Blink order instructions
notify.py        — dedicated Telegram bot + optional Resend email mirror
tracker.py       — signal performance + daily state (memory/manual_state.json)
manual_bot.py    — orchestration (screen / scan / monitor / EOD)
server.py        — Flask app + poll loop + Israel-time scheduler
test_strategy.py — offline sanity checks (no network)
```

---

## The Blink order each alert tells you to place

A long idea is a 3-part manual bracket (matching Blink's order types):

1. **ENTRY** — `Market` BUY now (or `Limit` BUY at the entry price).
2. **STOP-LOSS** — `Stop` SELL at the stop price (protects the downside).
3. **TAKE-PROFIT** — `Limit` SELL at the target price (locks the gain).

Optional: use a `Trailing Stop` SELL instead of the fixed stop to let the stop
follow the price up once you're in profit.

Risk sizing is ATR-based and identical to the paper bot:
`SL = clamp(ATR×1.2, 0.5%, 1.5%)`, `TP = clamp(ATR×2.0, 0.8%, 2.5%)`,
minimum reward:risk = 1.5.

---

## Daily schedule (Israel time)

| Time | Action |
|------|--------|
| 09:00 | Heartbeat ("bot active") |
| 16:00 | Pre-market screen → today's ~5 watchlist (Telegram) |
| 16:30–22:00 | Live: alert on fresh BUY setups, with Blink orders |
| 22:00 | Stop sending NEW ideas (still monitors open ones) |
| 22:50 | End-of-day signal recap |

---

## Setup

### 1. New Telegram bot (keeps it separate from the paper bot)
1. In Telegram, message **@BotFather** → `/newbot` → get a **token**.
2. Message your new bot once (say "hi").
3. After deploy, open `…/telegram-setup` to get your **chat id**.
4. Set `MANUAL_TELEGRAM_TOKEN` and `MANUAL_TELEGRAM_CHAT_ID`.

### 2. Environment variables (Railway → Variables)
Required:
```
ALPACA_API_KEY, ALPACA_SECRET_KEY        # data only
MANUAL_TELEGRAM_TOKEN, MANUAL_TELEGRAM_CHAT_ID
```
Optional:
```
RESEND_API_KEY, EMAIL_TO, EMAIL_FROM     # email mirror
FINNHUB_KEY, GROQ_API_KEY                # (reserved for a news digest)
STATE_DIR=memory
```
You can reuse the paper bot's Alpaca keys (read-only data use) or generate a
fresh pair. Do **not** reuse the paper bot's Telegram token — use a new bot.

### 3. Deploy on Railway (a SECOND service, beside the paper bot)
1. New GitHub repo, e.g. `manual-signal-bot`, push this folder.
2. Railway → **New Project** → Deploy from that repo (a separate service from
   `overflowing-warmth`/`web`).
3. Add the env vars above. `Procfile` already runs `python server.py`.
4. Railway → Settings → **Watch Paths**:
   ```
   **
   !memory/**
   ```
   so the bot's own state commits don't trigger redeploys.

### 4. Run locally
```bash
pip install -r requirements.txt
cp .env.example .env   # fill it in, then export the vars
python server.py       # http://localhost:8080/health
python test_strategy.py
```

---

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | alive, last poll, market open, errors |
| `GET /status` | watchlist, open/closed signals, W/L, signal P/L |
| `GET /screen` | run the screen now and send the watchlist |
| `GET /scan` | run the strategy now; alert on any fresh BUY |
| `GET /eod` | run the end-of-day recap now |
| `GET /send-status` | push a short status to Telegram |
| `GET /telegram-setup` | returns your chat id after you message the bot |
| `GET/POST /log?note=...` | log what you actually did in Blink |

---

## Important notes & limits

- **Signal P/L ≠ your real P/L.** The bot can't see Blink. It simulates each
  signal from the suggested entry to stop/target on live data. Treat it as a
  scoreboard for the *signals*, then compare with your actual fills via `/log`.
- **One signal per symbol per day** (simple anti-spam / cooldown). Tunable in
  `manual_bot._symbol_done_today`.
- **Max 3 open ideas at once** (`MAX_OPEN_SIGNALS`).
- **Data host matters:** bars come from `data.alpaca.markets`, never
  `paper-api` (no market data there) — the bug that once kept bars at 0.
- The clean-room `smc_wyckoff.py` aims to match the documented rules but is not
  byte-identical to your original. Swap in the real file to make signals match
  exactly.
