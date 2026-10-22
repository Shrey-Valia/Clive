"""Pull 1-minute bars for the 4 legs of every SPY 1DTE iron condor (Z1_ic_w5) in
tuning/events/trades_1dte.csv, from T 14:00 ET through the T+1 close, so the
earlier close-to-settle loss can be split overnight / intraday and put / call.
GET only, ~60 req/min, cached in .cache/d1/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zdata as z  # noqa: E402


def legs_of(row):
    sp, lp, sc, lc = [float(x) for x in row.strikes.split("/")]
    exp = pd.Timestamp(row.front)
    return {"sp": z.occ(exp, "P", sp), "lp": z.occ(exp, "P", lp),
            "sc": z.occ(exp, "C", sc), "lc": z.occ(exp, "C", lc)}


def trades():
    t = pd.read_csv(HERE.parent / "events" / "trades_1dte.csv")
    t = t[(t.ticker == "SPY") & (t.variant == "Z1_ic_w5") & (t.slip_mult == 0.0) & (t.ok == True)]  # noqa: E712
    return t.reset_index(drop=True)


def main():
    t = trades()
    (z.CACHE / "d1").mkdir(exist_ok=True)
    for n, row in t.iterrows():
        f = z.CACHE / "d1" / f"{row.D}.json"
        if f.exists():
            continue
        legs = legs_of(row)
        js = z.client().paged("/v1beta1/options/bars", {
            "symbols": ",".join(legs.values()), "timeframe": "1Min",
            "start": f"{row.D}T18:00:00Z", "end": f"{row.front}T21:15:00Z", "limit": 10000})
        f.write_text(json.dumps({k: js.get(s, []) for k, s in legs.items()}))
        if n % 50 == 0:
            print(n, row.D, "req", z.client().n, flush=True)
    print("done", len(t), "req", z.client().n)


if __name__ == "__main__":
    main()
