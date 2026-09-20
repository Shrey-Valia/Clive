"""Block 4 - SEC fails-to-deliver.

Published semi-monthly as a ZIP per half-month, on roughly a two-week lag,
so the newest file is always somewhat behind. The scanner discovers the most
recent one that actually exists rather than assuming a date.

Fails alone prove nothing. They matter as corroboration: big, repeated fails
clustering around price moves, alongside high short interest and borrow
stress, thicken the squeeze case. Size is judged against the float, since a
100k-share fail means something very different on a 5M float than a 500M one.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import zipfile
from collections import defaultdict

import requests

from . import config

URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{period}.zip"
CACHE = ".cache"

# SEC requires a descriptive UA identifying the requester.
HEADERS = {"User-Agent": "ShortSqueezeScanner/1.0 (research; contact via repo)"}


def _periods(months_back: int) -> list[str]:
    """Candidate period codes, newest first: 202609b, 202609a, 202608b..."""
    out, today = [], dt.date.today()
    y, m = today.year, today.month
    for _ in range(months_back + 1):
        out.extend([f"{y}{m:02d}b", f"{y}{m:02d}a"])
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _load(period: str) -> bytes | None:
    path = os.path.join(CACHE, f"cnsfails{period}.zip")
    if os.path.exists(path):
        return open(path, "rb").read()
    try:
        r = requests.get(URL.format(period=period), headers=HEADERS, timeout=60)
    except Exception:
        return None
    # SEC serves a 404 HTML page for periods not yet published.
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        return None
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(r.content)
    return r.content


def fetch(tickers: list[str], months_back: int | None = None) -> dict[str, dict]:
    months_back = months_back or config.FTD_LOOKBACK_MONTHS
    wanted = set(tickers)
    rows: dict[str, list[tuple[str, int]]] = defaultdict(list)
    periods_used: list[str] = []

    for period in _periods(months_back):
        blob = _load(period)
        if blob is None:
            continue
        periods_used.append(period)
        try:
            z = zipfile.ZipFile(io.BytesIO(blob))
            with z.open(z.namelist()[0]) as fh:
                for line in fh:
                    p = line.decode("latin-1").strip().split("|")
                    if len(p) < 4 or p[2] not in wanted:
                        continue
                    try:
                        rows[p[2]].append((p[0], int(p[3] or 0)))
                    except ValueError:
                        continue
        except Exception:
            continue
        # Two published half-months is a wide enough window.
        if len(periods_used) >= 2:
            break

    out: dict[str, dict] = {}
    for t in tickers:
        hits = sorted(rows.get(t, []))
        if not hits:
            out[t] = {
                "ftd_days": 0, "ftd_max_shares": None, "ftd_total_shares": None,
                "ftd_latest_date": None, "ftd_periods": ",".join(periods_used) or None,
            }
            continue
        q = [n for _, n in hits]
        out[t] = {
            "ftd_days": len(hits),
            "ftd_max_shares": max(q),
            "ftd_total_shares": sum(q),
            "ftd_latest_date": hits[-1][0],
            "ftd_periods": ",".join(periods_used),
        }
    return out
