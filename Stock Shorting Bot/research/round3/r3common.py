"""Round 3 shared paths and the Alpaca data reader (data API only; never trades).

Source caches are READ-ONLY in the main checkout. New caches go to round3/.cache (gitignored).
"""
from __future__ import annotations

import os
import sys
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
MAIN_BOT = "/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot"
SRC = os.path.join(MAIN_BOT, "research", ".cache")          # read-only
CACHE = os.path.join(HERE, ".cache")
OUT = os.path.join(HERE, "out")
RES = os.path.join(HERE, "results")
for d in (CACHE, OUT, RES):
    os.makedirs(d, exist_ok=True)
sys.path.insert(0, os.path.join(RESEARCH, "backtest"))

IS_END, OOS_START = "2021-12-31", "2022-01-01"
LAST_DAY = "2026-10-19"


def _env() -> dict:
    out = {}
    for line in open(os.path.join(MAIN_BOT, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_lock = threading.Lock()
_stamps: list[float] = []
MAX_PER_MIN = 90          # the live bots share this API key (Alpaca: 200/min per key); leave them headroom
# ET windows when the live bots trade (09:31 entry, 10/12/14:00 leftover covers, 15:40-15:55 scan/cover):
# research requests pause so they can never starve a live order or scan.
BLACKOUT_ET = [((9, 15), (10, 10)), ((11, 55), (12, 8)), ((13, 55), (14, 8)), ((15, 25), (16, 15))]


def _in_blackout() -> bool:
    import datetime as _dt
    from zoneinfo import ZoneInfo
    now = _dt.datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return any(a <= hm < b for a, b in BLACKOUT_ET)


def _throttle() -> None:
    while _in_blackout():
        time.sleep(30)
    with _lock:
        now = time.time()
        while _stamps and now - _stamps[0] > 60:
            _stamps.pop(0)
        if len(_stamps) >= MAX_PER_MIN:
            time.sleep(60 - (now - _stamps[0]) + 0.05)
        _stamps.append(time.time())


def alpaca_get(url: str, params: dict, tries: int = 12) -> dict:
    e = _env()
    h = {"APCA-API-KEY-ID": e["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": e["APCA_API_SECRET_KEY"]}
    for i in range(tries):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=h, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(10 * (i + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        return r.json()
    raise RuntimeError("alpaca: too many retries")
