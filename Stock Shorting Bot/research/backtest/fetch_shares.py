"""Shares outstanding history from SEC XBRL frames (dei:EntityCommonStockSharesOutstanding).

Used for a market-cap proxy (raw price x shares) and an SI%-of-shares proxy.
CIK->ticker comes from today's SEC ticker map, so delisted/renamed names are
mostly unmapped; those fall back to a dollar-volume proxy.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE  # noqa: E402

UA = {"User-Agent": "StockShortingBot/1.0 (personal research)"}


def main():
    path = os.path.join(CACHE, "shares_out.parquet")
    tk = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=60).json()
    cik2t = {}
    for v in tk.values():
        cik2t.setdefault(int(v["cik_str"]), []).append(v["ticker"].upper())
    rows = []
    for y in range(2015, 2027):
        for q in range(1, 5):
            url = f"https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json"
            r = requests.get(url, headers=UA, timeout=120)
            time.sleep(0.25)
            if r.status_code != 200:
                continue
            for d in r.json().get("data", []):
                for t in cik2t.get(int(d["cik"]), []):
                    rows.append((t, d["end"], float(d["val"]), d.get("accn")))
            print(y, q, len(rows), flush=True)
    df = pd.DataFrame(rows, columns=["symbol", "end", "shares", "accn"])
    # several share classes per CIK are summed by the filer; keep one value per symbol/end
    df = df.groupby(["symbol", "end"], as_index=False)["shares"].max()
    df.to_parquet(path)
    print(len(df), df.symbol.nunique())


if __name__ == "__main__":
    main()
