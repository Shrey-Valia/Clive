"""Options-bot alerts: a row in Supabase `alerts` (bot_id 'options', migration 004), a log line,
and an ntfy push. Same contract as Stock Shorting Bot/shortingbot/alerts.py:

* Condition alerts (``raise_alert(..., dedupe=True)``) are deduped on (kind, key) while open and
  resolved with ``resolve_cleared(kind, still_true)`` once the condition is gone, so a one-off
  problem (assignment stock, an unpaired leg, a failed run) clears itself on the next good run.
  The open set lives in live/state/alerts_state.json, so dedupe works with Supabase off too.
* Event alerts (``dedupe=False``) are one row per event.

Push: NTFY_TOPIC / NTFY_SERVER via config.ntfy_settings() (process env, then Options Bot/.env, then
Stock Shorting Bot/.env). Nothing here raises, and nothing here is ever on the trading path's
critical section: a failure only logs.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import requests

from . import config as C
from . import db

log = logging.getLogger("optionslive")
LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.ERROR}
STATE_NAME = "alerts_state.json"

# kinds (all condition alerts unless noted):
#   run_failed:<kind>      a run raised (resolved by the next run of that kind that finishes)
#   refused:<kind>         config / safety refusal (resolved likewise)
#   lock_busy:<kind>       a run could not get the lock
#   assignment             stock in the options account (resolved when none is left)
#   unpaired_legs          option legs that do not form a spread / condor
#   exit_unfilled          an exit did not fill (per spread id; resolved when it is closed)
#   expiry_risk            a spread / condor expiring today or tomorrow is still open after its exit
#   condor_deadline        a condor still open on expiry day past CONDOR_EXPIRY_DAY_DEADLINE
#   leftover_orders        earlier bot orders would not cancel (trading refused)
#   account                options level < 3 / trading blocked
#   inflight_recovered     event: a dead run's orders were found and booked
#   exdiv_risk             the condor's short call is exposed to early exercise before an ex-date
#   resting_order          a resting GTC order could not be staged / canceled


def _path():
    return C.STATE_DIR / STATE_NAME


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str))
        os.replace(tmp, p)
    except OSError as e:
        log.warning("alerts: could not save state: %s", e)


def _key(kind: str, ticker: str | None) -> str:
    return f"{kind}|{ticker or '*'}"


def push(title: str, message: str, level: str) -> bool:
    if not C.ALERTS_PUSH:
        return False
    topic, server = C.ntfy_settings()
    if not topic:
        return False
    try:
        requests.post(f"{server}/{topic}", data=message.encode(), timeout=10, headers={
            "Title": title, "Priority": "urgent" if level == "critical" else "default", "Tags": "warning"})
        return True
    except Exception as e:                      # noqa: BLE001
        log.warning("ntfy push failed: %s", type(e).__name__)
        return False


def raise_alert(kind: str, message: str, ticker: str | None = None, level: str = "warning",
                dedupe: bool = True) -> bool:
    """Record an alert. With dedupe, a (kind, ticker) already open is left alone (its message is
    refreshed locally). Returns True when a new alert was raised."""
    if not C.ALERTS_ENABLED:
        return False
    try:
        state = _load() if dedupe else {}
        k = _key(kind, ticker)
        if dedupe and k in state:
            return False
        log.log(LEVELS.get(level, logging.WARNING), "ALERT %s%s: %s", kind, f" {ticker}" if ticker else "", message)
        row = db.insert("alerts", {"bot_id": db.BOT_ID, "level": level, "kind": kind, "ticker": ticker,
                                   "message": message[:1000], "at": dt.datetime.now(dt.timezone.utc)})
        if dedupe:
            state[k] = {"id": row[0].get("id") if row else None, "message": message[:300], "level": level,
                        "at": dt.datetime.now(dt.timezone.utc).isoformat()}
            _save(state)
        push(f"Options bot: {kind}{' ' + ticker if ticker else ''}", message[:1000], level)
        return True
    except Exception as e:                      # noqa: BLE001
        log.warning("alert %s failed: %s", kind, e)
        return False


def resolve_cleared(kind: str, still_true: set | None = None) -> list[str]:
    """Resolve every open ``kind`` alert whose ticker is not in ``still_true`` (None = resolve all).
    Returns the resolved keys."""
    if not C.ALERTS_ENABLED:
        return []
    still_true = set() if still_true is None else set(still_true)
    try:
        state, done = _load(), []
        for key in [k for k in state if k.split("|", 1)[0] == kind]:
            ticker = key.split("|", 1)[1]
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
    except Exception as e:                      # noqa: BLE001
        log.warning("resolving %s alerts failed: %s", kind, e)
        return []


def resolve_one(kind: str, ticker: str | None) -> bool:
    """Resolve the open (kind, ticker) alert, if any."""
    others = {(None if k.split("|", 1)[1] == "*" else k.split("|", 1)[1]) for k in _load()
              if k.split("|", 1)[0] == kind} - {ticker}
    return bool(resolve_cleared(kind, others))


def sync_condition(kind: str, current: dict, level: str = "warning") -> None:
    """Make the open ``kind`` alerts match ``current`` ({ticker or None: message}): raise the new
    ones, resolve the ones no longer present."""
    for t, msg in current.items():
        raise_alert(kind, msg, ticker=t, level=level, dedupe=True)
    resolve_cleared(kind, set(current))


def open_alerts() -> dict:
    return _load()
