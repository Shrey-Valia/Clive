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
        with open(STATE + ".tmp", "w") as fh:          # tmp + rename: never half a file
            json.dump(state, fh, indent=1)
        os.replace(STATE + ".tmp", STATE)
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


# ---------------------------------------------------------------- event alert hygiene
# Event alerts (one row per event, never deduped) have no condition to clear, so before
# config.AUTO_RESOLVE_EVENT_ALERTS they stayed open on the dashboard forever. Two rules:
#   * a state kind resolves when a later check confirms the state is clean again:
#     SHORT_STATE_KINDS once no short from an earlier session is open (daily.py's leftover check,
#     sync.py when the book holds no short after the close), LONG_STATE_KINDS once no sleeve
#     long from an earlier session is open (sync.py);
#   * every other event kind resolves at the 16:15 sync once its session is over (raised before
#     today, ET). The run_events log keeps the history.
# Condition alerts monitor.py tracks in alerts_state.json (dedupe=True) are never swept here:
# their own resolve_cleared() owns them, and sweeping their row would leave the state stale.
SHORT_STATE_KINDS = frozenset({"cover_failed", "cover_blocked", "close_missed", "overnight_leftover",
                               "leftover_covered", "moc_cover_failed"})
LONG_STATE_KINDS = frozenset({"long_exit_failed", "long_exit_late"})
CONDITION_KINDS = frozenset({"position_loss", "book_loss", "long_loss", "long_outside_window"})


def _tracked_ids() -> list[str]:
    return sorted(str(v["id"]) for v in _load().values() if isinstance(v, dict) and v.get("id") is not None)


def resolve_events(kinds, before: dt.datetime | None = None, note: str = "",
                   exclude_kinds=frozenset()) -> int:
    """Resolve open event alerts of `kinds` (None = every kind but `exclude_kinds` and
    CONDITION_KINDS) raised before `before` (default now). Off unless
    config.AUTO_RESOLVE_EVENT_ALERTS. Returns rows resolved. Never raises."""
    try:
        if not config.AUTO_RESOLVE_EVENT_ALERTS or not db.enabled():
            return 0
        before = before or dt.datetime.now(dt.timezone.utc)
        params = {"bot_id": f"eq.{db.BOT_ID}", "resolved_at": "is.null", "at": f"lt.{before.isoformat()}"}
        if kinds is None:
            skip = sorted(set(exclude_kinds) | CONDITION_KINDS)
            params["kind"] = f"not.in.({','.join(skip)})"
        else:
            kinds = sorted(set(kinds))
            if not kinds:
                return 0
            params["kind"] = f"in.({','.join(kinds)})"
        tracked = _tracked_ids()
        if tracked:
            params["id"] = f"not.in.({','.join(tracked)})"
        rows = db.update_where("alerts", params, {"resolved_at": dt.datetime.now(dt.timezone.utc),
                                                  "resolution": note[:300] or None})
        if rows:
            log.info("alerts auto-resolved: %d (%s) - %s", len(rows),
                     ", ".join(sorted({r.get("kind") or "?" for r in rows})), note or "-")
        return len(rows)
    except Exception as e:
        log.warning("auto-resolving alerts failed: %s", e)
        return 0
