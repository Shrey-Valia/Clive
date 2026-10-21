"""Supabase writes over its REST API (PostgREST). No-ops when not configured,
so a dry run works on a fresh checkout."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import requests

from . import config

BOT_ID = "shorting"
log = logging.getLogger("shortingbot")


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _headers(prefer: str) -> dict:
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": prefer}


def _clean(v):
    if isinstance(v, float) and v != v:      # NaN is not valid JSON
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
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"
    params = {}
    if upsert_on:
        prefer += ",resolution=merge-duplicates"
        params["on_conflict"] = upsert_on
    r = requests.post(url, params=params, headers=_headers(prefer),
                      data=json.dumps(rows, default=str), timeout=config.REQUEST_TIMEOUT)
    if r.status_code >= 300:
        log.error("supabase %s insert failed: %s %s", table, r.status_code, r.text[:300])
        return []
    return r.json()


def update(table: str, match: dict, values: dict) -> None:
    if not enabled():
        return
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"
    params = {k: f"eq.{v}" for k, v in match.items()}
    r = requests.patch(url, params=params, headers=_headers("return=minimal"),
                       data=json.dumps({k: _clean(v) for k, v in values.items()}, default=str),
                       timeout=config.REQUEST_TIMEOUT)
    if r.status_code >= 300:
        log.error("supabase %s update failed: %s %s", table, r.status_code, r.text[:300])


def delete(table: str, match: dict) -> None:
    if not enabled():
        return
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"
    requests.delete(url, params={k: f"eq.{v}" for k, v in match.items()},
                    headers=_headers("return=minimal"), timeout=config.REQUEST_TIMEOUT)


def heartbeat(status: str, message: str) -> None:
    # The bots row is seeded by the migration, so this only ever updates it.
    update("bots", {"id": BOT_ID}, {"run_state": status, "last_message": message[:500],
                                    "last_heartbeat": dt.datetime.now(dt.timezone.utc)})
