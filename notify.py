"""
notify.py — push manual-signal alerts to a DEDICATED Telegram bot.

Separate bot/token from the paper bot so manual signals never blur with the
paper bot's auto-trade alerts. Optional email mirror via Resend (Railway blocks
SMTP, so HTTPS only).
"""

from __future__ import annotations

import requests

import config

_UA = "Mozilla/5.0 (manual-signal-bot)"


def send_telegram(text: str) -> bool:
    """Send a plain-text message to the dedicated manual-signal Telegram bot."""
    if not (config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID):
        print("[notify] Telegram not configured; skipping.")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] telegram error: {exc}")
        return False


def send_email(subject: str, text: str) -> bool:
    """Optional mirror via Resend over HTTPS."""
    if not (config.RESEND_API_KEY and config.EMAIL_TO):
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.RESEND_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": _UA,  # avoids Cloudflare 1010
            },
            json={
                "from": f"Manual Signals <{config.EMAIL_FROM}>",
                "to": [config.EMAIL_TO],
                "subject": subject,
                "text": text,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] resend error: {exc}")
        return False


def notify(subject: str, text: str, email: bool = True) -> None:
    """Send to Telegram (primary) and optionally mirror to email."""
    send_telegram(text)
    if email:
        send_email(subject, text)


def resolve_chat_id() -> str | None:
    """
    Helper for /telegram-setup: read the most recent update and return the
    chat id of whoever last messaged the bot.
    """
    if not config.TELEGRAM_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("result", [])
        for upd in reversed(results):
            msg = upd.get("message") or upd.get("channel_post")
            if msg and msg.get("chat", {}).get("id"):
                return str(msg["chat"]["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] getUpdates error: {exc}")
    return None
