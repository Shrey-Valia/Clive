"""Alerts: a row in Supabase `alerts`, a log line, and an optional ntfy.sh push.

Condition alerts (monitor.py: position_loss, book_loss) are deduped on (kind, ticker)
while open and resolved when the condition clears. The open set lives in
logs/alerts_state.json, so dedupe works with Supabase off too. Event alerts from
daily.py (pdt_block, cover_failed, ...) are one row per event.

Push: set NTFY_TOPIC in .env (and NTFY_SERVER for a self-hosted server). Off by default.
Nothing here raises.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import requests

from . import config, db

log = logging.getLogger("shortingbot")
STATE = os.path.join(config.LOG_DIR, "alerts_state.json")
LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.ERROR}


def _load() -> dict:
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as fh:
            json.dump(state, fh, indent=1)
    except OSError as e:
        log.warning("alerts: could not save state: %s", e)


def _key(kind: str, ticker: str | None) -> str:
    return f"{kind}:{ticker or '*'}"


def push(title: str, message: str, level: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    try:
        requests.post(f"{server}/{topic}", data=message.encode(), timeout=10, headers={
            "Title": title, "Priority": "urgent" if level == "critical" else "default", "Tags": "warning"})
    except Exception as e:
        log.warning("ntfy push failed: %s", e)


def raise_alert(kind: str, message: str, ticker: str | None = None, level: str = "warning",
                dedupe: bool = False) -> bool:
    """Record an alert. With dedupe, a (kind, ticker) already open is left alone.
    Returns True when a new alert was raised."""
    try:
        state = _load() if dedupe else {}
        if dedupe and _key(kind, ticker) in state:
            return False
        log.log(LEVELS.get(level, logging.WARNING), "ALERT %s%s: %s", kind,
                f" {ticker}" if ticker else "", message, extra={"step": "alert"})
        row = db.insert("alerts", {"bot_id": db.BOT_ID, "level": level, "kind": kind, "ticker": ticker,
                                   "message": message[:1000], "at": dt.datetime.now(dt.timezone.utc)})
        if dedupe:
            state[_key(kind, ticker)] = {"id": row[0]["id"] if row else None, "message": message,
                                        "at": dt.datetime.now(dt.timezone.utc).isoformat()}
            _save(state)
        push(f"Shorting bot: {kind}{' ' + ticker if ticker else ''}", message, level)
        return True
    except Exception as e:
        log.warning("alert %s failed: %s", kind, e)
        return False


def resolve_cleared(kind: str, still_true: set[str | None]) -> list[str]:
    """Resolve every open `kind` alert whose ticker is not in still_true. Returns resolved keys."""
    try:
        state, done = _load(), []
        for key in [k for k in state if k.split(":", 1)[0] == kind]:
            ticker = key.split(":", 1)[1]
            if (None if ticker == "*" else ticker) in still_true:
                continue
            if state[key].get("id"):
                db.update("alerts", {"id": state[key]["id"]}, {"resolved_at": dt.datetime.now(dt.timezone.utc)})
            log.info("alert cleared: %s", key)
            del state[key]
            done.append(key)
        if done:
            _save(state)
        return done
    except Exception as e:
        log.warning("resolving %s alerts failed: %s", kind, e)
        return []


def open_alerts() -> dict:
    return _load()
