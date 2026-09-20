"""Block 3 - FINRA off-exchange short sale volume.

Answers "are most trades short?" - the churn question. FINRA publishes a
daily RegSHO file of off-exchange (dark pool / wholesaler) short volume for
every US symbol. Free, no auth.

Read alongside short interest, not on its own. A high short-volume ratio with
LOW short interest means shorts are trading around a small base and closing
out intraday - churn, not a trapped structural position. That distinction is
the whole point of this block.
"""
from __future__ import annotations

import datetime as dt
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
CACHE = ".cache"


def _fetch_day(day: dt.date) -> dict[str, tuple[float, float]]:
    """One day's file -> {symbol: (short_volume, total_volume)}.

    Weekends, holidays and not-yet-published days 403/404; those are normal
    and come back empty.
    """
    stamp = day.strftime("%Y%m%d")
    path = os.path.join(CACHE, f"shvol_{stamp}.txt")

    if os.path.exists(path):
        text = open(path, encoding="latin-1").read()
    else:
        try:
            r = requests.get(URL.format(date=stamp), timeout=config.REQUEST_TIMEOUT)
            if r.status_code != 200 or not r.text.startswith("Date|"):
                return {}
            text = r.text
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(text)
        except Exception:
            return {}

    out: dict[str, tuple[float, float]] = {}
    for line in text.splitlines()[1:]:
        p = line.split("|")
        if len(p) < 5:
            continue
        try:
            out[p[1]] = (float(p[2]), float(p[4]))
        except ValueError:
            continue
    return out


def fetch(tickers: list[str], lookback_days: int | None = None) -> dict[str, dict]:
    """Short-volume ratio stats per ticker over the recent session window."""
    lookback_days = lookback_days or config.SHORT_VOLUME_LOOKBACK_DAYS

    # Over-fetch calendar days so weekends/holidays still leave enough sessions.
    today = dt.date.today()
    candidates = [today - dt.timedelta(days=i) for i in range(1, lookback_days * 2 + 5)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        days = list(pool.map(_fetch_day, candidates))

    # Newest first; keep only sessions that actually published.
    sessions = [(d, data) for d, data in zip(candidates, days) if data][:lookback_days]

    result: dict[str, dict] = {}
    for t in tickers:
        series = []
        for day, data in sessions:
            if t not in data:
                continue
            short_v, total_v = data[t]
            if total_v > 0:
                series.append((day, 100.0 * short_v / total_v, total_v))

        if not series:
            result[t] = {
                "short_vol_ratio_latest": None,
                "short_vol_ratio_avg": None,
                "short_vol_days_above_50": None,
                "short_vol_sessions": 0,
            }
            continue

        ratios = [r for _, r, _ in series]
        result[t] = {
            "short_vol_ratio_latest": round(ratios[0], 1),
            "short_vol_ratio_avg": round(sum(ratios) / len(ratios), 1),
            "short_vol_days_above_50": sum(1 for r in ratios if r >= 50),
            "short_vol_sessions": len(ratios),
        }
    return result
