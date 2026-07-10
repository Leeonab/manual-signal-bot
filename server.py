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


def _poll_loop():
    while True:
        try:
            t = _now()
            # Drive purely off the real market clock (DST/holiday-proof) rather
            # than a hardcoded Israel-time window.
            clock = data.get_clock()
            _STATE["market_open"] = clock["is_open"]
            if clock["is_open"]:
                # Restart-safety: if the service restarted mid-session and has no
                # watchlist for today, run the screen now instead of falling back.
                if not tracker.load().get("watchlist"):
                    manual_bot.run_premarket_screen()
                mtc = clock["minutes_to_close"]
                allow_new = (mtc is None) or (mtc > config.STOP_NEW_MIN_BEFORE_CLOSE)
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


@app.get("/history.json")
def history_json():
    return jsonify(tracker.load_history())


@app.get("/dashboard")
def dashboard():
    from collections import defaultdict

    hist = tracker.load_history()
    n = len(hist)
    wins = sum(1 for h in hist if h.get("pnl_pct", 0) >= 0)
    losses = n - wins
    win_rate = (wins / n * 100) if n else 0.0
    cum = sum(h.get("pnl_pct", 0) for h in hist)
    avg = (cum / n) if n else 0.0
    delays = [h["signal_age_sec"] for h in hist if h.get("signal_age_sec") is not None]
    avg_delay = (sum(delays) / len(delays)) if delays else None
    max_delay = max(delays) if delays else None

    days = defaultdict(lambda: {"n": 0, "w": 0, "net": 0.0})
    for h in hist:
        d = days[h.get("date", "?")]
        d["n"] += 1
        d["w"] += 1 if h.get("pnl_pct", 0) >= 0 else 0
        d["net"] += h.get("pnl_pct", 0)
    day_rows = sorted(days.items())

    def card(label, value, sub=""):
        return (f'<div class="card"><div class="lbl">{label}</div>'
                f'<div class="val">{value}</div><div class="sub">{sub}</div></div>')

    delay_txt = f"{avg_delay:.0f}s" if avg_delay is not None else "—"
    delay_sub = f"max {max_delay:.0f}s" if max_delay is not None else "no data yet"
    cards = "".join([
        card("Trades", n, f"{wins}W / {losses}L"),
        card("Win rate", f"{win_rate:.0f}%"),
        card("Cumulative", f"{cum:+.2f}%", "sum of per-trade %"),
        card("Avg / trade", f"{avg:+.2f}%"),
        card("Avg delay", delay_txt, delay_sub),
    ])

    labels = [d for d, _ in day_rows]
    net_data = [round(v["net"], 2) for _, v in day_rows]
    day_table = "".join(
        f"<tr><td>{d}</td><td>{v['n']}</td><td>{v['w']}/{v['n'] - v['w']}</td>"
        f"<td class='{'pos' if v['net'] >= 0 else 'neg'}'>{v['net']:+.2f}%</td></tr>"
        for d, v in day_rows
    ) or "<tr><td colspan=4>No trades logged yet.</td></tr>"

    recent = list(reversed(hist))[:25]
    recent_table = "".join(
        f"<tr><td>{h.get('date','')}</td><td>{h.get('time','')}</td><td><b>{h.get('symbol','')}</b></td>"
        f"<td>${h.get('entry','')}</td><td>${h.get('exit','')}</td><td>{h.get('result','')}</td>"
        f"<td class='{'pos' if h.get('pnl_pct',0) >= 0 else 'neg'}'>{h.get('pnl_pct',0):+.2f}%</td>"
        f"<td>{'' if h.get('signal_age_sec') is None else str(h.get('signal_age_sec')) + 's'}</td></tr>"
        for h in recent
    ) or "<tr><td colspan=8>No trades logged yet.</td></tr>"

    html = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Manual Signal Bot — Performance</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
 body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:20px}}
 h1{{font-size:20px;margin:0 0 4px}} .muted{{color:#8b949e;font-size:13px;margin-bottom:18px}}
 .cards{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;min-width:120px}}
 .lbl{{color:#8b949e;font-size:12px}} .val{{font-size:24px;font-weight:700;margin:4px 0}} .sub{{color:#8b949e;font-size:11px}}
 table{{width:100%;border-collapse:collapse;margin:10px 0 26px;font-size:13px}}
 th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #21262d}} th{{color:#8b949e;font-weight:600}}
 .pos{{color:#3fb950}} .neg{{color:#f85149}} h2{{font-size:15px;margin:18px 0 6px}}
 canvas{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px;max-height:260px}}
 .note{{color:#8b949e;font-size:12px;margin-top:8px}}
</style></head><body>
<h1>📊 Manual Signal Bot — Performance</h1>
<div class=muted>All-time signal history · simulated on suggested entries (not your real Blink fills)</div>
<div class=cards>{cards}</div>
<h2>Daily net %</h2>
<canvas id=chart></canvas>
<h2>By day</h2>
<table><tr><th>Date</th><th>Trades</th><th>W/L</th><th>Net %</th></tr>{day_table}</table>
<h2>Recent trades</h2>
<table><tr><th>Date</th><th>Time</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>Result</th><th>P&L</th><th>Delay</th></tr>{recent_table}</table>
<div class=note>Delay = how old the market data was when the signal fired. Refresh to update.</div>
<script>
new Chart(document.getElementById('chart'),{{type:'bar',
 data:{{labels:{labels},datasets:[{{label:'net %',data:{net_data},
 backgroundColor:{net_data}.map(v=>v>=0?'#238636':'#da3633')}}]}},
 options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#8b949e'}}}},y:{{ticks:{{color:'#8b949e'}}}}}}}}}});
</script></body></html>"""
    return html


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
