"""Shared helpers: env, Alpaca session with rate limiting, cache paths."""
from __future__ import annotations

import os
import threading
import time

import requests

RESEARCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_ROOT = os.path.dirname(RESEARCH)
CACHE = os.path.join(RESEARCH, ".cache")
RESULTS = os.path.join(RESEARCH, "results")
OUT = os.path.join(RESEARCH, "out")
for d in (CACHE, RESULTS, OUT):
    os.makedirs(d, exist_ok=True)


def load_env() -> None:
    path = os.path.join(BOT_ROOT, ".env")
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_lock = threading.Lock()
_stamps: list[float] = []
MAX_PER_MIN = 180


def _throttle() -> None:
    with _lock:
        now = time.time()
        while _stamps and now - _stamps[0] > 60:
            _stamps.pop(0)
        if len(_stamps) >= MAX_PER_MIN:
            time.sleep(60 - (now - _stamps[0]) + 0.05)
        _stamps.append(time.time())


def alpaca_get(url: str, params: dict, tries: int = 6) -> dict:
    load_env()
    h = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
         "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    for i in range(tries):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=h, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429:
            time.sleep(5 * (i + 1))
            continue
        if r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        return r.json()
    raise RuntimeError("alpaca: too many retries")
