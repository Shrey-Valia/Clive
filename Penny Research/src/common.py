"""Paths, read-only links to the shorting research, and a single-worker 60 req/min GET helper."""
from __future__ import annotations

import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache")
RESULTS = os.path.join(ROOT, "results")
OUT = os.path.join(ROOT, "out")
BASE = os.path.dirname(ROOT)
SBOT = os.path.join(BASE, "Stock Shorting Bot")
SRES = os.path.join(SBOT, "research")
SCACHE = os.path.join(SRES, ".cache")
SBT = os.path.join(SRES, "backtest")
for d in (CACHE, RESULTS, OUT):
    os.makedirs(d, exist_ok=True)

IS_END, OOS_START = "2021-12-31", "2022-01-01"
LAST_DAY = "2026-10-19"

_env_loaded = False


def load_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    for line in open(os.path.join(SBOT, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _env_loaded = True


_last = [0.0]
MIN_GAP = 1.0  # seconds between requests -> ~60/min, one worker


def get(url: str, params: dict, tries: int = 6) -> dict:
    """GET only. Never used for order endpoints."""
    assert "data.alpaca.markets" in url or url.endswith("/v2/assets"), url
    load_env()
    h = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
         "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    for i in range(tries):
        wait = MIN_GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(url, params=params, headers=h, timeout=60)
        except requests.RequestException:
            time.sleep(3 * (i + 1))
            continue
        if r.status_code == 429:
            time.sleep(10 * (i + 1))
            continue
        if r.status_code >= 500:
            time.sleep(3 * (i + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
        return r.json()
    raise RuntimeError("too many retries")
