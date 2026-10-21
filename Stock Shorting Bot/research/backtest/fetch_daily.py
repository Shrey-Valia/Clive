"""Download daily bars (raw and fully adjusted) for the NASDAQ/NYSE/AMEX common-stock universe.

Universe = Alpaca assets, active AND inactive, so names delisted since 2016 are
included where Alpaca still serves their history (partial survivorship fix).
"""
from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, alpaca_get  # noqa: E402

BARS = "https://data.alpaca.markets/v2/stocks/bars"
EXCL = re.compile(r"\b(warrants?|units?|rights?|preferred|depositary shares? representing|notes due|"
                  r"debentures?|etf|etn|fund|index|trust preferred|% series|cumulative)\b", re.I)


def universe() -> pd.DataFrame:
    a = json.load(open(os.path.join(CACHE, "assets.json")))
    rows = []
    for x in a:
        if x["exchange"] not in ("NASDAQ", "NYSE", "AMEX"):
            continue
        s = x["symbol"]
        if not re.fullmatch(r"[A-Z]{1,5}", s):
            continue
        if EXCL.search(x["name"] or ""):
            continue
        rows.append({"symbol": s, "name": x["name"], "exchange": x["exchange"], "status": x["status"],
                     "shortable": x["shortable"], "easy_to_borrow": x["easy_to_borrow"]})
    return pd.DataFrame(rows).sort_values("status").drop_duplicates("symbol")


def fetch_batch(args):
    k, syms, adj = args
    path = os.path.join(CACHE, f"daily_{adj}", f"b{k:04d}.parquet")
    if os.path.exists(path):
        return k
    params = {"symbols": ",".join(syms), "timeframe": "1Day", "start": "2015-10-01",
              "end": "2026-10-20T23:59:00Z", "feed": "sip", "adjustment": adj, "limit": 10000}
    out = []
    while True:
        j = alpaca_get(BARS, params)
        for s, bars in (j.get("bars") or {}).items():
            for b in bars:
                out.append((s, b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw"), b.get("n")))
        tok = j.get("next_page_token")
        if not tok:
            break
        params["page_token"] = tok
    df = pd.DataFrame(out, columns=["symbol", "date", "o", "h", "l", "c", "v", "vw", "n"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path)
    return k


def main():
    u = universe()
    u.to_csv(os.path.join(CACHE, "universe.csv"), index=False)
    print(len(u), "symbols;", u.status.value_counts().to_dict(), flush=True)
    syms = sorted(u.symbol)
    batches = [syms[i:i + 100] for i in range(0, len(syms), 100)]
    for adj in sys.argv[1:] or ["raw", "all"]:
        jobs = [(k, b, adj) for k, b in enumerate(batches)]
        with ThreadPoolExecutor(4) as pool:
            for k in pool.map(fetch_batch, jobs):
                if k % 10 == 0:
                    print(adj, "batch", k, "/", len(batches), flush=True)


if __name__ == "__main__":
    main()
