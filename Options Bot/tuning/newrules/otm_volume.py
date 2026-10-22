#!/usr/bin/env python3
"""Daily OTM option volume for SPY/QQQ/IWM from Alpaca option daily bars (v), GET only.

For every monthly expiry, the puts struck 80-100% and the calls 100-115% of the spot range
over its last 75 days are fetched (tuning/newrules/.cache/obars, shared with the open-print
cache). Daily series per underlying:
  put_vol  = volume of puts with K in [0.90, 0.98] x spot, expiries 7-70 DTE (monthlies)
  call_vol = volume of calls with K in [1.02, 1.10] x spot, same expiries
Output: .cache/otm_volume.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L                                       # noqa: E402
from optionsbot.strategies_newrules import OBARS      # noqa: E402


def main():
    out = []
    for s in L.ETFS:
        m = L.market(s)
        spot = m.stock["close"]
        ch = m.chain
        exps = sorted(e for e in ch.expiration_date.unique()
                      if m.is_monthly(pd.Timestamp(e)) and pd.Timestamp("2024-03-01") <= e <= pd.Timestamp("2026-12-31"))
        per_day = {}
        for e in exps:
            e = pd.Timestamp(e)
            a, b = OBARS.window(e)
            sp = spot.loc[a:b]
            if sp.empty:
                continue
            lo, hi = sp.min(), sp.max()
            g = ch[(ch.expiration_date == e)]
            puts = g[(g.cp == "P") & (g.strike_price >= 0.88 * lo) & (g.strike_price <= 0.99 * hi)]
            calls = g[(g.cp == "C") & (g.strike_price >= 1.01 * lo) & (g.strike_price <= 1.12 * hi)]
            syms = list(puts.symbol) + list(calls.symbol)
            bars = OBARS.get(syms, e)
            K = dict(zip(g.symbol, g.strike_price))
            CP = dict(zip(g.symbol, g.cp))
            for sym, df in bars.items():
                for d, row in df.iterrows():
                    if d not in spot.index:
                        continue
                    dte = (e - d).days
                    if dte < 7 or dte > 70:
                        continue
                    S = spot.at[d]
                    k = K[sym] / S
                    rec = per_day.setdefault(d, {"put_vol": 0.0, "call_vol": 0.0})
                    if CP[sym] == "P" and 0.90 <= k <= 0.98:
                        rec["put_vol"] += float(row["v"])
                    elif CP[sym] == "C" and 1.02 <= k <= 1.10:
                        rec["call_vol"] += float(row["v"])
            print(s, e.date(), len(syms), "requests", OBARS.n_requests, flush=True)
        df = pd.DataFrame.from_dict(per_day, orient="index").sort_index()
        df["symbol"] = s
        out.append(df)
    res = pd.concat(out)
    res.index.name = "date"
    res.reset_index().to_parquet(HERE / ".cache" / "otm_volume.parquet")
    print(res.groupby("symbol").describe().T)


if __name__ == "__main__":
    main()
