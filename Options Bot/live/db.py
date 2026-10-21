"""Supabase writes over PostgREST, same pattern as Stock Shorting Bot/shortingbot/db.py.
No-op unless SUPABASE_URL and SUPABASE_SERVICE_KEY are set (Options Bot/.env or env).
Failures are logged and never stop trading."""
from __future__ import annotations

import datetime as dt
import json
import logging

import requests

from . import config as C

BOT_ID = C.BOT_ID
log = logging.getLogger("optionslive")


def _env() -> dict:
    return C.options_env()


def enabled() -> bool:
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
    try:
        r = requests.post(_url(table), params=params, headers=_headers(prefer),
                          data=json.dumps(rows, default=str), timeout=C.REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log.error("supabase %s insert failed: %s", table, e)
        return []
    if r.status_code >= 300:
        log.error("supabase %s insert failed: %s %s", table, r.status_code, r.text[:300])
        return []
    return r.json()


def update(table: str, match: dict, values: dict) -> None:
    if not enabled():
        return
    try:
        r = requests.patch(_url(table), params={k: f"eq.{v}" for k, v in match.items()},
                           headers=_headers("return=minimal"),
                           data=json.dumps({k: _clean(v) for k, v in values.items()}, default=str),
                           timeout=C.REQUEST_TIMEOUT)
        if r.status_code >= 300:
            log.error("supabase %s update failed: %s %s", table, r.status_code, r.text[:300])
    except requests.RequestException as e:
        log.error("supabase %s update failed: %s", table, e)


def delete(table: str, match: dict) -> None:
    if not enabled():
        return
    try:
        requests.delete(_url(table), params={k: f"eq.{v}" for k, v in match.items()},
                        headers=_headers("return=minimal"), timeout=C.REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log.error("supabase %s delete failed: %s", table, e)


def heartbeat(status: str, message: str) -> None:
    update("bots", {"id": BOT_ID}, {"run_state": status, "last_message": message[:500],
                                    "last_heartbeat": dt.datetime.now(dt.timezone.utc)})
