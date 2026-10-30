"""Long-history check of the proxy against Cboe benchmark indexes (1991-2026, includes 1998,
2001-02, 2008, 2010, 2011, 2015, 2018, 2020, 2022, 2024, 2025):

* PUT  - sell a 1-month ATM SPX put each 3rd Friday, fully collateralized in T-bills.
* PPUT - long the S&P 500 + a 1-month 5% OTM SPX put, rolled monthly.

Monthly returns are rebuilt with proxy prices (SPY surface fitted on 2024-26 prints, Cboe VIX term
structure of the roll day) and compared with the published index returns. No costs (the indexes
have none). Writes out/cboe_validation_{monthly,annual}.csv.
"""
import numpy as np
import pandas as pd

import psim as P
import r3lib as L
from common import OUT

u = P.U("SPX10", surf_name="SPY", div=0.018)
idx = u.idx
cal = [e for e, k in P.expiry_calendar(idx) if k == "M" and e >= pd.Timestamp("1990-06-01") and e <= idx[-1]]
PUT, PPUT, SPX = L.cboe("PUT"), L.cboe("PPUT"), L.cboe("SPX")
rows = []
for e0, e1 in zip(cal[:-1], cal[1:]):
    S0, S1 = u.spot(e0), u.spot(e1)
    T = (e1 - e0).days / 365.0
    i0 = u._pos[e0]
    r = u._r[i0]
    K = round(S0 / 0.5) * 0.5                     # SPX/10 units: $5 SPX strikes
    p_atm, _ = P.price_strike(u, e0, e1, "P", K)
    put_ret = (1 + p_atm / K) * (1 + r * T) - max(K - S1, 0) / K - 1
    K5 = round(0.95 * S0 / 0.5) * 0.5
    p5, _ = P.price_strike(u, e0, e1, "P", K5)
    pput_ret = (S1 * (1 + 0.018 * T) + max(K5 - S1, 0)) / (S0 + p5) - 1

    def ir(s):
        a = s.reindex([e0, e1], method="ffill")
        return float(a.iloc[1] / a.iloc[0] - 1)
    rows.append({"start": e0.date(), "end": e1.date(), "proxy_PUT": put_ret, "cboe_PUT": ir(PUT),
                 "proxy_PPUT": pput_ret, "cboe_PPUT": ir(PPUT), "spx": ir(SPX), "vix0": u._vix[i0],
                 "premium_atm_pct": p_atm / K, "premium_5otm_pct": p5 / S0})
m = pd.DataFrame(rows)
m = m[pd.to_datetime(m.start) >= "1991-04-01"]
m.to_csv(OUT / "cboe_validation_monthly.csv", index=False)
m["year"] = pd.to_datetime(m.end).dt.year
ann = m.groupby("year").apply(lambda g: pd.Series({c: (1 + g[c]).prod() - 1 for c in
                                                    ["proxy_PUT", "cboe_PUT", "proxy_PPUT", "cboe_PPUT", "spx"]}))
ann.to_csv(OUT / "cboe_validation_annual.csv")
pd.set_option("display.width", 200)
print(ann.round(3).to_string())
for a, b in (("proxy_PUT", "cboe_PUT"), ("proxy_PPUT", "cboe_PPUT")):
    d = m[a] - m[b]
    print(a, "monthly corr", round(np.corrcoef(m[a], m[b])[0, 1], 3), "mean diff/month", round(d.mean(), 4),
          "MAE", round(d.abs().mean(), 4), "ann. CAGR proxy/cboe",
          round((1 + m[a]).prod() ** (12 / len(m)) - 1, 4), round((1 + m[b]).prod() ** (12 / len(m)) - 1, 4))
# the Cboe PUT csv is stale/zero-filled before 2007 (flat years, jumps); compare from 2007
m7 = m[pd.to_datetime(m.start) >= "2007-01-01"]
for a, b in (("proxy_PUT", "cboe_PUT"), ("proxy_PPUT", "cboe_PPUT")):
    d = m7[a] - m7[b]
    print("2007+", a, "monthly corr", round(np.corrcoef(m7[a], m7[b])[0, 1], 3), "mean diff", round(d.mean(), 4),
          "MAE", round(d.abs().mean(), 4), "CAGR proxy/cboe", round((1 + m7[a]).prod() ** (12 / len(m7)) - 1, 4),
          round((1 + m7[b]).prod() ** (12 / len(m7)) - 1, 4),
          "worst month proxy/cboe", round(m7[a].min(), 4), round(m7[b].min(), 4))
# premium level by decade vs VIX (sanity)
m["dec"] = (pd.to_datetime(m.start).dt.year // 5) * 5
print(m.groupby("dec")[["vix0", "premium_atm_pct", "premium_5otm_pct"]].mean().round(4))
