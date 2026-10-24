"""SIC industry codes from SEC submissions for the strict-gate symbols (industry-cap and sector tests).

CIK <- ticker uses today's SEC ticker map, so delisted / renamed names are mostly unmapped (NaN SIC).
SIC is a slow-moving company attribute; using today's value for past events is a small look-ahead
that only matters for re-classified companies. Writes .cache/sic.csv.
"""
import os
import time

import pandas as pd
import requests

from r3common import CACHE, SRC

UA = {"User-Agent": "StockShortingBot/1.0 (personal research)"}


def main():
    ev = pd.read_parquet(os.path.join(SRC, "events.parquet"), columns=["symbol", "p1545_raw", "adv20"])
    syms = sorted(set(ev[(ev.p1545_raw >= 10) & (ev.adv20 >= 10e6)].symbol))
    tk = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=60).json()
    t2c = {v["ticker"].upper(): int(v["cik_str"]) for v in tk.values()}
    rows = []
    for n, s in enumerate(syms):
        cik = t2c.get(s) or t2c.get(s.replace(".", "-"))
        if not cik:
            rows.append((s, None, None, None)); continue
        for i in range(4):
            try:
                r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=UA, timeout=30)
                break
            except requests.RequestException:
                time.sleep(2)
        time.sleep(0.12)
        if r.status_code != 200:
            rows.append((s, cik, None, None)); continue
        j = r.json()
        rows.append((s, cik, j.get("sic"), j.get("sicDescription")))
        if n % 250 == 0:
            print(n, s, j.get("sic"), flush=True)
    df = pd.DataFrame(rows, columns=["symbol", "cik", "sic", "sic_desc"])
    df.to_csv(os.path.join(CACHE, "sic.csv"), index=False)
    print(len(df), df.sic.notna().mean())


if __name__ == "__main__":
    main()
