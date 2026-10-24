"""Fit the IV-surface proxy on 2024-01..2025-06 prints, evaluate on 2025-07..2026-10 prints.

Writes out/surface_<SYM>_is.json (IS fit, used for validation) and out/surface_<SYM>.json (full fit,
used for the long-history runs), and out/surface_errors.csv (pricing errors by slice).
"""
import numpy as np
import pandas as pd

import r3lib as L
import surface as SF
from common import OUT, WORK

rows = []
for sym in ["SPY", "IWM", "QQQ"]:
    df = pd.read_pickle(WORK / f"iv_{sym}.pkl")
    s_is = SF.fit(sym, end="2025-06-30")
    s_is.save(f"{sym}_is")
    s_full = SF.fit(sym)
    s_full.save(sym)
    for seg, d in (("IS", df[df.date <= "2025-06-30"]), ("OOS", df[df.date > "2025-06-30"])):
        d = d[(d.dte >= 3) & (d.dte <= 190)]
        if len(d) > 400_000:
            d = d.sample(400_000, random_state=1)
        piv = s_is.iv(d.base.values, d.z.values, d["T"].values, d.VIX.values, d.TS.values)
        cpn = np.where(d.cp == "C", 1.0, -1.0)
        pp = L.bs(d.spot.values, d.strike.values, d["T"].values, d.r.values, d.q.values, piv, cpn)
        d = d.assign(piv=piv, pp=pp, err=pp - d.close.values, ivErr=piv - d.iv.values)
        ad = d.delta.abs()
        slices = {
            "dip short leg: put |d| 0.18-0.32, 5-16 DTE": (d.cp == "P") & ad.between(0.18, 0.32) & d.dte.between(5, 16),
            "dip long leg: put |d| 0.06-0.14, 5-16 DTE": (d.cp == "P") & ad.between(0.06, 0.14) & d.dte.between(5, 16),
            "condor put short: |d| 0.07-0.13, 45-75 DTE": (d.cp == "P") & ad.between(0.07, 0.13) & d.dte.between(45, 75),
            "condor call short: d 0.07-0.13, 45-75 DTE": (d.cp == "C") & ad.between(0.07, 0.13) & d.dte.between(45, 75),
            "condor put wing: |d| 0.02-0.07, 45-75 DTE": (d.cp == "P") & ad.between(0.02, 0.07) & d.dte.between(45, 75),
            "condor call wing: d 0.01-0.05, 45-75 DTE": (d.cp == "C") & ad.between(0.01, 0.05) & d.dte.between(45, 75),
            "hedge put: |d| 0.01-0.06, 30-120 DTE": (d.cp == "P") & ad.between(0.01, 0.06) & d.dte.between(30, 120),
            "all OTM": ad > 0,
        }
        for name, msk in slices.items():
            x = d[msk]
            if len(x) < 50:
                continue
            hi = x.VIX > 25
            rows.append({"sym": sym, "seg": seg, "slice": name, "n": len(x),
                         "median_price": x.close.median(),
                         "bias_$": x.err.mean(), "mae_$": x.err.abs().mean(),
                         "median_abs_pct": (x.err.abs() / x.close).median(),
                         "bias_iv_pts": 100 * x.ivErr.mean(), "mae_iv_pts": 100 * x.ivErr.abs().mean(),
                         "n_vix_gt25": int(hi.sum()),
                         "bias_iv_pts_vix_gt25": 100 * x.ivErr[hi].mean() if hi.sum() else np.nan})
res = pd.DataFrame(rows)
res.to_csv(OUT / "surface_errors.csv", index=False)
pd.set_option("display.width", 250)
print(res.round(3).to_string())
