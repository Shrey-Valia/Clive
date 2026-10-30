"""Validate the VIX-call proxy against the Cboe VXTH overlay (2006-2026); fit vv_mult on 2006-2015.
Writes out/vxth_validation.csv."""
import numpy as np
import pandas as pd

import r3lib as L
import vixopt as V
from common import OUT

VXTH, SPX = L.cboe("VXTH"), L.cboe("SPX")
vopen = L.cboe_col("VIX", "OPEN")
vix = L.cboe("VIX")
idx = vix.index
tset = set(idx)
ex = [e for e in V.vix_expiries("2006-03-01", "2026-10-15", tset) if e in tset and e <= idx[-1]]


def overlay(mult, conv=1.0):
    m = V.VixModel(vv_mult=mult, conv=conv)
    rows = []
    for e0, e1 in zip(ex[:-1], ex[1:]):
        v0 = vix.at[e0]
        w = 0.0 if v0 <= 15 else (0.01 if v0 <= 30 else (0.005 if v0 <= 50 else 0.0))
        K = m.strike_for_delta(e0, e1, 0.30)
        prem, F = m.call(e0, e1, K)
        pay = max(vopen.get(e1, vix.at[e1]) - K, 0.0)
        proxy = w * (pay / prem - 1) if prem > 0.01 else 0.0
        a = VXTH.reindex([e0, e1], method="ffill")
        s = SPX.reindex([e0, e1], method="ffill")
        sret = s.iloc[1] / s.iloc[0] - 1 + 0.018 * (e1 - e0).days / 365
        actual = (a.iloc[1] / a.iloc[0] - 1) - (1 - w) * sret
        rows.append({"start": e0.date(), "vix0": v0, "w": w, "F0": F, "K": K, "prem": prem, "pay": pay,
                     "proxy_overlay": proxy, "vxth_overlay": actual})
    return pd.DataFrame(rows)


best = None
for mult in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1):
    for conv in (0.0, 1.0, 2.0):
        o = overlay(mult, conv)
        isd = o[(pd.to_datetime(o.start) < "2016-01-01") & (o.w > 0)]
        err = (isd.proxy_overlay - isd.vxth_overlay)
        # match the cumulative overlay drag (the hedge's cost) and the payoffs
        score = abs(err.mean()) + 0.1 * err.abs().mean()
        print(mult, conv, round(err.mean() * 1e4, 1), "bp/mo bias", round(err.abs().mean() * 1e4, 1), "bp MAE",
              flush=True)
        if best is None or score < best[0]:
            best = (score, mult, conv, o)
_, mult, conv, o = best
o.to_csv(OUT / "vxth_validation.csv", index=False)
for seg, msk in (("IS 2006-15", pd.to_datetime(o.start) < "2016-01-01"), ("OOS 2016-26", pd.to_datetime(o.start) >= "2016-01-01")):
    x = o[msk & (o.w > 0)]
    print(seg, "vv_mult", mult, "conv", conv, "n", len(x), "corr", round(np.corrcoef(x.proxy_overlay, x.vxth_overlay)[0, 1], 3),
          "mean proxy/actual bp", round(x.proxy_overlay.mean() * 1e4, 1), round(x.vxth_overlay.mean() * 1e4, 1),
          "sum proxy/actual", round(x.proxy_overlay.sum(), 3), round(x.vxth_overlay.sum(), 3))
