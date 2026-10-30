"""Supabase writes over PostgREST, same pattern as Stock Shorting Bot/shortingbot/db.py.
No-op unless SUPABASE_URL and SUPABASE_SERVICE_KEY are set (Options Bot/.env or env), or after
``disable()`` (``run.py --dry-run``). Failures are logged and never stop trading.

A column the database does not have yet (a migration not applied) is dropped from the row and
the write retried, so a new field never costs the whole row.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

import requests

from . import config as C

BOT_ID = C.BOT_ID
log = logging.getLogger("optionslive")
_MISSING_COL = re.compile(r"Could not find the '([^']+)' column")
_warned: set[str] = set()
_disabled = False
RANK = {"idle": 0, "ok": 1, "running": 2, "warning": 3, "error": 4}


def disable() -> None:
    """Turn every Supabase call into a no-op for this process (--dry-run)."""
    global _disabled
    _disabled = True


def _env() -> dict:
    return C.options_env()


def enabled() -> bool:
    if _disabled:
        return False
    e = _env()
    return bool(e.get("SUPABASE_URL") and e.get("SUPABASE_SERVICE_KEY"))


def _headers(prefer: str) -> dict:
    key = _env()["SUPABASE_SERVICE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": prefer}


def _url(table: str) -> str:
    return f"{_env()['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"


def _clean(v):
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _send(method: str, table: str, rows, params: dict, prefer: str):
    """One request, retried without any column PostgREST reports missing. Response or None."""
    for _ in range(12):
        try:
            r = requests.request(method, _url(table), params=params, headers=_headers(prefer),
                                 data=json.dumps(rows, default=str) if rows is not None else None,
                                 timeout=C.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            log.error("supabase %s %s failed: %s", table, method.lower(), e)
            return None
        if r.status_code < 300:
            return r
        m = _MISSING_COL.search(r.text or "")
        if m and rows is not None:
            col = m.group(1)
            if f"{table}.{col}" not in _warned:
                _warned.add(f"{table}.{col}")
                log.warning("supabase %s has no column %s; writing without it (apply the migration)", table, col)
            if isinstance(rows, list):
                rows = [{k: v for k, v in row.items() if k != col} for row in rows]
            else:
                rows = {k: v for k, v in rows.items() if k != col}
            continue
        log.error("supabase %s %s failed: %s %s", table, method.lower(), r.status_code, (r.text or "")[:300])
        return None
    return None


def insert(table: str, rows, upsert_on: str | None = None) -> list[dict]:
    if not enabled():
        return []
    rows = rows if isinstance(rows, list) else [rows]
    if not rows:
        return []
    rows = [{k: _clean(v) for k, v in r.items()} for r in rows]
    prefer = "return=representation"
    params = {}
    if upsert_on:
        prefer += ",resolution=merge-duplicates"
        params["on_conflict"] = upsert_on
    r = _send("POST", table, rows, params, prefer)
    if r is None:
        return []
    try:
        return r.json()
    except ValueError:
        return []


def update(table: str, match: dict, values: dict) -> None:
    if not enabled():
        return
    _send("PATCH", table, {k: _clean(v) for k, v in values.items()},
          {k: f"eq.{v}" for k, v in match.items()}, "return=minimal")


def select(table: str, params: dict) -> list[dict] | None:
    """GET rows (None on any failure)."""
    if not enabled():
        return None
    try:
        r = requests.get(_url(table), params=params, headers=_headers("return=representation"),
                         timeout=C.REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log.error("supabase %s select failed: %s", table, e)
        return None
    if r.status_code >= 300:
        log.warning("supabase %s select failed: %s %s", table, r.status_code, (r.text or "")[:200])
        return None
    try:
        return r.json()
    except ValueError:
        return None


def delete(table: str, match: dict) -> None:
    if not enabled():
        return
    try:
        requests.delete(_url(table), params={k: f"eq.{v}" for k, v in match.items()},
                        headers=_headers("return=minimal"), timeout=C.REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log.error("supabase %s delete failed: %s", table, e)


def worst(*statuses) -> str:
    st = [s for s in statuses if s]
    return max(st, key=lambda s: RANK.get(s, 1)) if st else "ok"


def heartbeat(status: str, message: str, source: str = "run") -> None:
    """bots row for 'options'. Same split as the shorting bot (migration 004): the trading runs
    write last_run_status/at, sync.py writes last_sync_status/at, and run_state is the worse of
    the two, so the 16:15 sync's "ok" can no longer paint over a 15:55 "EXPIRY EXIT NOT FILLED".
    Before migration 004 (columns missing) a sync only overwrites run_state with an error."""
    if not enabled():
        return
    other = "sync" if source == "run" else "run"
    now = dt.datetime.now(dt.timezone.utc)
    values = {"last_message": message[:500], "last_heartbeat": now,
              f"last_{source}_status": status, f"last_{source}_at": now}
    row = select("bots", {"select": f"last_{other}_status", "id": f"eq.{BOT_ID}"})
    if row and f"last_{other}_status" in row[0]:
        values["run_state"] = worst(status, row[0][f"last_{other}_status"])
    elif source == "run" or status == "error":
        values["run_state"] = status
    update("bots", {"id": BOT_ID}, values)


def liveness() -> None:
    """Record that a run happened (last_run_status/at) without touching run_state or the message,
    for the quiet intraday checks that have nothing to report: it proves the host is alive without
    clearing an earlier error."""
    if not enabled():
        return
    now = dt.datetime.now(dt.timezone.utc)
    update("bots", {"id": BOT_ID}, {"last_heartbeat": now, "last_run_at": now})
