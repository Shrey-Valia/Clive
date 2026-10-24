"""Full-session minute bars for round 3.

For every scan event that passes the strict liquidity gate (15:45 raw price >= $10, ADV20 >= $10M):
  day1/<T>.parquet : T+1 bars 04:00 ET -> T+1 close (premarket + regular), plus SPY/IWM/QQQ
  day0/<T>.parquet : T bars 09:30 -> T close-15m (what the 15:45 scan could see), plus ETFs
Keyed by the scan date T. Columns: symbol, t (minutes after 09:30 ET; negative = premarket),
o h l c v vw n. adjustment=all, feed=sip (same as the original research caches).

    python3 fetch_intraday.py            # all days
    python3 fetch_intraday.py test       # one day (2021-01-26) only
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

from r3common import CACHE, SRC, alpaca_get

ET = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
ETFS = ["SPY", "IWM", "QQQ"]


def bars(syms, start, end, t0):
    out = []
    syms = sorted(syms)
    for i in range(0, len(syms), 50):
        params = {"symbols": ",".join(syms[i:i + 50]), "timeframe": "1Min",
                  "start": start.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                  "end": end.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                  "feed": "sip", "adjustment": "all", "limit": 10000}
        while True:
            j = alpaca_get(BARS, params)
            for s, bb in (j.get("bars") or {}).items():
                for b in bb:
                    t = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                    out.append((s, int((t - t0).total_seconds() // 60), b["o"], b["h"], b["l"], b["c"], b["v"],
                                b.get("vw"), b.get("n")))
            if not j.get("next_page_token"):
                break
            params["page_token"] = j["next_page_token"]
    return pd.DataFrame(out, columns=["symbol", "t", "o", "h", "l", "c", "v", "vw", "n"])


def job(args):
    try:
        return _job(args)
    except Exception as e:  # noqa: BLE001  (a failed day is retried on the next run)
        print("FAILED", args[0], str(e)[:120], flush=True)
        return args[0]


def _job(args):
    d0, d1, close0, close1, syms = args
    p1 = os.path.join(CACHE, "day1", f"{d0}.parquet")
    p0 = os.path.join(CACHE, "day0", f"{d0}.parquet")
    if not os.path.exists(p1):
        t0 = dt.datetime.fromisoformat(d1).replace(hour=9, minute=30, tzinfo=ET)
        hh, mm = map(int, close1.split(":"))
        df = bars(set(syms) | set(ETFS), t0.replace(hour=4, minute=0), t0.replace(hour=hh, minute=mm), t0)
        df.to_parquet(p1 + ".tmp")
        os.replace(p1 + ".tmp", p1)
    if not os.path.exists(p0):
        t0 = dt.datetime.fromisoformat(d0).replace(hour=9, minute=30, tzinfo=ET)
        hh, mm = map(int, close0.split(":"))
        end = t0.replace(hour=hh, minute=mm) - dt.timedelta(minutes=15)
        df = bars(set(syms) | set(ETFS), t0, end, t0)
        df.to_parquet(p0 + ".tmp")
        os.replace(p0 + ".tmp", p0)
    return d0


def main():
    for s in ("day1", "day0"):
        os.makedirs(os.path.join(CACHE, s), exist_ok=True)
    ev = pd.read_parquet(os.path.join(SRC, "events.parquet"), columns=["symbol", "date", "p1545_raw", "adv20"])
    ev = ev[(ev.p1545_raw >= 10) & (ev.adv20 >= 10e6)]
    cal = pd.read_csv(os.path.join(SRC, "calendar.csv"))
    nxt = dict(zip(cal.date[:-1], cal.date[1:]))
    close = dict(zip(cal.date, cal.close))
    jobs = []
    for d, g in ev.groupby("date"):
        d1 = nxt.get(d)
        if d1 is None or d1 > "2026-10-21":
            continue
        jobs.append((d, d1, close[d], close[d1], list(g.symbol)))
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        jobs = [j for j in jobs if j[0] == "2021-01-26"]
    print(len(jobs), "days", len(ev), "events", flush=True)
    with ThreadPoolExecutor(6) as ex:
        for n, d in enumerate(ex.map(job, jobs)):
            if n % 100 == 0:
                print(n, d, flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
