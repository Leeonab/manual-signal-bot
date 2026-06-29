"""
server.py — always-on Flask app + scheduler for the MANUAL-SIGNAL bot.

Threads:
  _poll_loop  — every POLL_INTERVAL while the market is open: scan watchlist for
                fresh BUY setups and monitor open signals for stop/target hits.
  _scheduler  — Israel-time daily triggers: heartbeat, pre-market screen, stop
                new ideas, end-of-day recap. DST-proof via zoneinfo.

Nothing here places an order. It only reads data and sends Telegram/email.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request

import config
import data
import manual_bot
import tracker
import notify

app = Flask(__name__)

_STATE = {
    "started": datetime.now(config.TZ).isoformat(),
    "last_poll": None,
    "poll_count": 0,
    "last_error": None,
    "market_open": False,
    "fired_today": set(),
    "fired_day": None,
}


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(config.TZ)


def _hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def _reset_fired_if_new_day(t: datetime):
    day = t.strftime("%Y-%m-%d")
    if _STATE["fired_day"] != day:
        _STATE["fired_day"] = day
        _STATE["fired_today"] = set()


def _fire_once(key: str, fn):
    """Run a scheduled action at most once per day."""
    if key in _STATE["fired_today"]:
        return
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        _STATE["last_error"] = f"{key}: {exc}"
        print(f"[sched] {key} error: {exc}")
    _STATE["fired_today"].add(key)


def _scheduler():
    while True:
        try:
            t = _now()
            _reset_fired_if_new_day(t)
            hm = _hm(t)
            weekday = t.weekday() < 5  # Mon-Fri

            if hm == config.SCHED_HEARTBEAT:
                _fire_once(
                    "heartbeat",
                    lambda: notify.send_telegram(
                        f"💓 manual-signal-bot active · {t:%Y-%m-%d}\n" + config.DISCLAIMER
                    ),
                )
            if weekday and hm == config.SCHED_PREMARKET_SCREEN:
                _fire_once("screen", manual_bot.run_premarket_screen)
            if weekday and hm == config.SCHED_EOD_SUMMARY:
                _fire_once("eod", manual_bot.run_eod)
        except Exception as exc:  # noqa: BLE001
            _STATE["last_error"] = f"scheduler: {exc}"
            print(f"[sched] error: {exc}")
        time.sleep(20)


def _within_session(t: datetime) -> bool:
    return config.SCHED_SESSION_START <= _hm(t) <= config.SCHED_EOD_SUMMARY


def _poll_loop():
    while True:
        try:
            t = _now()
            _STATE["market_open"] = data.market_is_open()
            if _STATE["market_open"] and t.weekday() < 5 and _within_session(t):
                allow_new = _hm(t) < config.SCHED_STOP_NEW
                manual_bot.scan_for_signals(allow_new=allow_new)
                manual_bot.monitor_open()
            _STATE["last_poll"] = t.isoformat()
            _STATE["poll_count"] += 1
        except Exception as exc:  # noqa: BLE001
            _STATE["last_error"] = f"poll: {exc}"
            print(f"[poll] error: {exc}")
        time.sleep(config.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "bot": config.BOT_NAME,
            "started": _STATE["started"],
            "last_poll": _STATE["last_poll"],
            "poll_count": _STATE["poll_count"],
            "market_open": _STATE["market_open"],
            "last_error": _STATE["last_error"],
        }
    )


@app.get("/status")
def status():
    st = tracker.load()
    return jsonify(
        {
            "today": st["today"],
            "watchlist": st["watchlist"],
            "open_signals": st["open_signals"],
            "closed_signals": st["closed_signals"],
            "wins": st["wins"],
            "losses": st["losses"],
            "signal_pnl_pct": st["signal_pnl_pct"],
        }
    )


@app.get("/screen")
def screen_now():
    picks = manual_bot.run_premarket_screen()
    return jsonify({"picks": picks})


@app.get("/scan")
def scan_now():
    fired = manual_bot.scan_for_signals(allow_new=True)
    return jsonify({"fired": [f["symbol"] for f in fired]})


@app.get("/eod")
def eod_now():
    return jsonify({"summary": manual_bot.run_eod()})


@app.get("/send-status")
def send_status():
    st = tracker.load()
    txt = (
        f"Status · {st['today']}\n"
        f"Watchlist: {', '.join(st['watchlist']) or '—'}\n"
        f"Open: {len(st['open_signals'])} · Closed: {len(st['closed_signals'])}\n"
        f"Record: {st['wins']}W/{st['losses']}L · "
        f"signal perf {st['signal_pnl_pct']:+.2f}%"
    )
    notify.send_telegram(txt)
    return jsonify({"sent": True})


@app.get("/telegram-setup")
def telegram_setup():
    chat_id = notify.resolve_chat_id()
    return jsonify({"chat_id": chat_id, "hint": "Set MANUAL_TELEGRAM_CHAT_ID to this."})


@app.route("/log", methods=["GET", "POST"])
def log_fill():
    note = request.values.get("note", "")
    if note:
        tracker.log_manual_fill(note)
    return jsonify({"logged": bool(note)})


def _start_threads():
    threading.Thread(target=_scheduler, daemon=True).start()
    threading.Thread(target=_poll_loop, daemon=True).start()


_start_threads()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
