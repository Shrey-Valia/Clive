"""Minute bars for the last 20 minutes of each session, for the candidate pool.

Per trading day T one multi-symbol request covers pool(T) (scan at close-15m,
entry at close-5m) and pool(T-1) (exit at close-5m on the next day).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, alpaca_get  # noqa: E402

ET = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
D = os.path.join(CACHE, "minute")


def day_job(args):
    date, close, syms = args
    path = os.path.join(D, f"{date}.parquet")
    if os.path.exists(path):
        return date
    hh, mm = map(int, close.split(":"))
    c = dt.datetime.fromisoformat(date).replace(hour=hh, minute=mm, tzinfo=ET)
    start = (c - dt.timedelta(minutes=20)).astimezone(dt.timezone.utc)
    end = (c - dt.timedelta(minutes=1)).astimezone(dt.timezone.utc)
    out = []
    syms = sorted(syms)
    for i in range(0, len(syms), 100):
        params = {"symbols": ",".join(syms[i:i + 100]), "timeframe": "1Min",
                  "start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"),
                  "feed": "sip", "adjustment": "all", "limit": 10000}
        while True:
            j = alpaca_get(BARS, params)
            for s, bars in (j.get("bars") or {}).items():
                for b in bars:
                    t = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                    mins_to_close = int((c - t).total_seconds() // 60)
                    out.append((s, mins_to_close, b["o"], b["h"], b["l"], b["c"], b["v"]))
            if not j.get("next_page_token"):
                break
            params["page_token"] = j["next_page_token"]
    pd.DataFrame(out, columns=["symbol", "mtc", "o", "h", "l", "c", "v"]).to_parquet(path)
    return date


def main():
    os.makedirs(D, exist_ok=True)
    pool = pd.read_parquet(os.path.join(CACHE, "pool.parquet"), columns=["symbol", "date"])
    cal = pd.read_csv(os.path.join(CACHE, "calendar.csv"))
    cal = cal[(cal.date >= "2016-01-04") & (cal.date <= "2026-10-20")]
    by_day = pool.groupby("date").symbol.apply(set).to_dict()
    jobs, prev = [], set()
    for date, close in zip(cal.date, cal.close):
        cur = by_day.get(date, set())
        if cur or prev:
            jobs.append((date, close, cur | prev))
        prev = cur
    print(len(jobs), "days", flush=True)
    with ThreadPoolExecutor(10) as pool_:
        for n, d in enumerate(pool_.map(day_job, jobs)):
            if n % 100 == 0:
                print(n, d, flush=True)


if __name__ == "__main__":
    main()
