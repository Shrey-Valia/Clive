"""Deep dive on the cross-asset long/flat trend candidate (F3_TS_xasset_lf_blend).

Cost stress (x2, x3), vol-lookback and gross-cap neighbourhood, leave-one-asset-out jackknife, and the
Alpaca-data version over 2016-2026 (check that Yahoo vs Alpaca prices don't change the answer).
Neighbourhood variants are registered as trials.
"""
import os

import numpy as np
import pandas as pd

import f3_trend as F
from common import LH_IS_END, LH_OOS_START, RES, by_year, corr_block, perf, register, split_perf

rows = []


def rec(key, r, w, extra, reg=False):
    if reg:
        register(key, "F3_trend_nbhd", r, extra)
    rows.append(dict(name=key, **extra, avg_gross=w.abs().sum(axis=1).mean(),
                     turnover_yr=w.diff().abs().sum(axis=1).sum() / (len(w) / 252),
                     **split_perf(r, LH_IS_END, LH_OOS_START), **corr_block(r[r.index >= "2016"])))


base_r, base_w = F.run("xasset", "lf", [1, 3, 12])
rec("base", base_r, base_w, {})
pd.to_pickle((base_r, base_w), os.path.join(RES, "f3_base.pkl"))
for m in (2.0, 3.0):
    F.COST_MULT[0] = m
    r, w = F.run("xasset", "lf", [1, 3, 12]); rec(f"cost_x{m:g}", r, w, {"cost_mult": m})
F.COST_MULT[0] = 1.0
for vl in (21, 126):
    r, w = F.run("xasset", "lf", [1, 3, 12], vol_lb=vl); rec(f"F3_NB_vol{vl}", r, w, {"vol_lb": vl}, True)
for gc in (1.0, 2.0):
    r, w = F.run("xasset", "lf", [1, 3, 12], gross_cap=gc); rec(f"F3_NB_cap{gc:g}", r, w, {"gross_cap": gc}, True)
r, w = F.run("xasset", "lf", [3, 6, 12]); rec("F3_NB_lb3_6_12", r, w, {"lb": "3,6,12"}, True)
univ0 = list(F.UNIV["xasset"])
for s in univ0:
    F.UNIV["xasset"] = [x for x in univ0 if x != s]
    r, w = F.run("xasset", "lf", [1, 3, 12]); rec(f"drop_{s}", r, w, {"dropped": s})
F.UNIV["xasset"] = univ0
res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, "f3_deep.csv"), index=False)
pd.set_option("display.width", 250)
print(res[["name", "avg_gross", "turnover_yr", "is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr", "is_maxdd", "oos_maxdd",
           "corr_spy", "ret_on_spy_worst20"]].round(3).to_string())
print(by_year(base_r).round(3).to_string())
# weights snapshot (latest)
print(base_w.iloc[-1][base_w.iloc[-1].abs() > 0].round(3).to_string())
