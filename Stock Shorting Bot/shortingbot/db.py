"""Supabase writes over its REST API (PostgREST). No-ops when not configured,
so a dry run works on a fresh checkout.

Nothing here may stop trading: every call catches its own errors, logs them and
returns an empty result. A column the database doesn't have yet (migration not
pushed) is dropped from the row and the write retried, so new fields never cost
the whole row.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import threading

import requests

from . import config

BOT_ID = "shorting"
log = logging.getLogger("shortingbot.db")
_MISSING_COL = re.compile(r"Could not find the '([^']+)' column")
_warned: set[str] = set()


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _headers(prefer: str) -> dict:
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": prefer}


def _url(table: str) -> str:
    return f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"


def _clean(v):
    if isinstance(v, float) and v != v:      # NaN is not valid JSON
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _send(method: str, table: str, rows, params: dict, prefer: str):
    """One request, retried without any column PostgREST says is missing. Returns the response or None."""
    for _ in range(12):
        try:
            r = requests.request(method, _url(table), params=params, headers=_headers(prefer),
                                 data=json.dumps(rows, default=str) if rows is not None else None,
                                 timeout=config.REQUEST_TIMEOUT)
        except Exception as e:
            log.error("supabase %s %s failed: %s", table, method, e)
            return None
        if r.status_code < 300:
            return r
        m = _MISSING_COL.search(r.text)
        if m and rows is not None:
            col = m.group(1)
            if f"{table}.{col}" not in _warned:
                _warned.add(f"{table}.{col}")
                log.warning("supabase %s has no column %s - writing without it (push the migration)", table, col)
            if isinstance(rows, list):
                rows = [{k: v for k, v in row.items() if k != col} for row in rows]
            else:
                rows = {k: v for k, v in rows.items() if k != col}
            continue
        if r.status_code == 404 or (method == "GET" and r.status_code == 400):   # not migrated yet: say so once
            if f"{table}.{method}" not in _warned:
                _warned.add(f"{table}.{method}")
                log.warning("supabase %s %s failed (migration not pushed?): %s", table, method, r.text[:200])
            return None
        log.error("supabase %s %s failed: %s %s", table, method, r.status_code, r.text[:300])
        return None
    return None


def insert(table: str, rows, upsert_on: str | None = None) -> list[dict]:
    if not enabled():
        return []
    try:
        rows = rows if isinstance(rows, list) else [rows]
        if not rows:
            return []
        rows = [{k: _clean(v) for k, v in r.items()} for r in rows]
        # PostgREST bulk inserts need every row to carry the same keys
        keys = sorted({k for r in rows for k in r})
        rows = [{k: r.get(k) for k in keys} for r in rows]
        prefer = "return=representation"
        params = {}
        if upsert_on:
            prefer += ",resolution=merge-duplicates"
            params["on_conflict"] = upsert_on
        r = _send("POST", table, rows, params, prefer)
        return r.json() if r is not None else []
    except Exception as e:
        log.error("supabase %s insert failed: %s", table, e)
        return []


def update(table: str, match: dict, values: dict) -> None:
    if not enabled() or any(v is None for v in match.values()):
        return
    try:
        params = {k: f"eq.{v}" for k, v in match.items()}
        _send("PATCH", table, {k: _clean(v) for k, v in values.items()}, params, "return=minimal")
    except Exception as e:
        log.error("supabase %s update failed: %s", table, e)


def update_where(table: str, params: dict, values: dict) -> list[dict]:
    """PATCH with raw PostgREST filters, e.g. {"kind": "in.(a,b)", "resolved_at": "is.null"}.
    Returns the updated rows ([] when off or on any error). Refuses an empty filter."""
    if not enabled() or not params:
        return []
    try:
        r = _send("PATCH", table, {k: _clean(v) for k, v in values.items()}, params, "return=representation")
        return r.json() if r is not None else []
    except Exception as e:
        log.error("supabase %s update failed: %s", table, e)
        return []


def delete(table: str, match: dict) -> None:
    if not enabled():
        return
    try:
        _send("DELETE", table, None, {k: f"eq.{v}" for k, v in match.items()}, "return=minimal")
    except Exception as e:
        log.error("supabase %s delete failed: %s", table, e)


def select(table: str, params: dict) -> list[dict]:
    """GET with raw PostgREST params, e.g. {"select": "id,run_id", "id": "in.(a,b)"}."""
    if not enabled():
        return []
    try:
        r = _send("GET", table, None, params, "")
        return r.json() if r is not None else []
    except Exception as e:
        log.error("supabase %s select failed: %s", table, e)
        return []


# ---------------------------------------------------------------- heartbeat
# daily.py owns last_run_status / last_run_at, sync.py owns last_sync_status / last_sync_at.
# run_state is the worse of the two, so a good sync can no longer paint over a bad run.
SEVERITY = {"error": 4, "warning": 3, "running": 2, "ok": 1, "idle": 0}


def worst(*states) -> str:
    states = [s for s in states if s in SEVERITY]
    return max(states, key=SEVERITY.get) if states else "idle"


def heartbeat(status: str, message: str, source: str = "run") -> None:
    """source='run' from daily.py, 'sync' from sync.py."""
    if not enabled():
        return
    other = "sync" if source == "run" else "run"
    now = dt.datetime.now(dt.timezone.utc)
    row = select("bots", {"select": f"last_{other}_status", "id": f"eq.{BOT_ID}"})
    values = {"last_message": message[:500], "last_heartbeat": now,
              f"last_{source}_status": status, f"last_{source}_at": now}
    if row and f"last_{other}_status" in row[0]:
        values["run_state"] = worst(status, row[0][f"last_{other}_status"])
    elif source == "run" or status == "error":
        # before the 004 migration: the old behaviour, minus sync overwriting a bad run
        values["run_state"] = status
    update("bots", {"id": BOT_ID}, values)


# ---------------------------------------------------------------- run_events
_steps: dict[int, str] = {}


def set_step(step: str | None) -> None:
    """Tag this thread's log lines with a step (scan, source, cover, entry, ...) for run_events."""
    _steps[threading.get_ident()] = step


class RunEventHandler(logging.Handler):
    """Buffers WARNING+ and INFO lines logged with extra={"milestone": True}, and writes
    them to run_events on flush(run_id). Never raises: a logging or Supabase failure
    must not reach the trading code."""

    MAX_BUFFER = 500

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.buffer: list[dict] = []
        self._flushing = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._flushing or record.name == log.name:      # our own Supabase errors would loop
                return
            if record.levelno < logging.WARNING and not getattr(record, "milestone", False):
                return
            if len(self.buffer) >= self.MAX_BUFFER:
                return
            self.buffer.append({
                "at": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(),
                "level": record.levelname,
                "step": getattr(record, "step", None) or _steps.get(record.thread),
                "message": record.getMessage().split("\n")[0][:1000],
            })
        except Exception:
            pass

    def flush_to(self, run_id) -> int:
        """Write the buffer with this run_id. Returns rows written (0 when Supabase is off)."""
        if self._flushing:
            return 0
        self._flushing = True
        try:
            rows, self.buffer = self.buffer, []
            if not rows:
                return 0
            return len(insert("run_events", [{**r, "run_id": run_id, "bot_id": BOT_ID} for r in rows]))
        except Exception:
            return 0
        finally:
            self._flushing = False

    def handleError(self, record) -> None:      # noqa: N802 - logging API name
        pass


events = RunEventHandler()
