"""1-minute SIP bars (adjustment=all) for SPY, QQQ, IWM, 2016-01-04 .. 2026-10-20, regular + extended
hours as served. Cached per symbol-year."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from common import CACHE, bars

D = os.path.join(CACHE, "minute_etf"); os.makedirs(D, exist_ok=True)


def job(a):
    s, y = a
    p = os.path.join(D, f"{s}_{y}.parquet")
    if os.path.exists(p):
        return p
    df = bars([s], f"{y}-01-01T00:00:00Z", f"{y}-12-31T23:59:59Z" if y < 2026 else "2026-10-21T00:00:00Z",
              "1Min", "all")
    df.to_parquet(p)
    return p


if __name__ == "__main__":
    syms = sys.argv[1:] or ["SPY", "QQQ", "IWM"]
    jobs = [(s, y) for s in syms for y in range(2016, 2027)]
    with ThreadPoolExecutor(6) as ex:
        for p in ex.map(job, jobs):
            print(p, flush=True)
