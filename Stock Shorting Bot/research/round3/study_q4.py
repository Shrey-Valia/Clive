"""Q4 sizing, gross caps and capacity on the v2 selection with the live execution rule.

    python3 study_q4.py
"""
from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pandas as pd

import common3 as c3
import sim
from r3common import IS_END, OOS_START, RES

ev, B, days = c3.load_all()
LIVE = sim.Rule()
BK = sim.Book(score="score_live")
tr = sim.trade_returns(ev, B, LIVE)
rows = []


def run(bk, group, log=True):
    row, d, t = sim.evaluate(ev, B, days, LIVE, bk, group, tr=tr, log=log)
    row["gross_mean_active"] = d[d.n > 0].gross.mean()
    rows.append(row)
    print(f"{group:<8} {bk.name:<30} {c3.fmt(row)} avg gross {row['gross_mean_active']:.3f}", flush=True)
    return row, d, t


run(BK, "anchor", log=False)
run(replace(BK, name="invvol", sizing="invvol"), "Q4size")
run(replace(BK, name="scorew", sizing="score"), "Q4size")
for thr in (70, 65, 60, 55):
    run(replace(BK, name=f"top10_2.5pct_min{thr}", n=10, w=0.025, min_score=thr), "Q4cap")
    if thr != 70:
        run(replace(BK, name=f"top5_5pct_min{thr}", min_score=thr), "Q4cap")
    run(replace(BK, name=f"top10_5pct_min{thr}_cap50", n=10, w=0.05, min_score=thr, gross_cap=0.50), "Q4cap")
run(replace(BK, name="top3_5pct_min70", n=3), "Q4cap")
run(replace(BK, name="top5_5pct_min75", min_score=75), "Q4cap")
run(replace(BK, name="top5_5pct_min80", min_score=80), "Q4cap")
pd.DataFrame(rows).drop(columns=["cfg"]).to_csv(os.path.join(RES, "q4_sizing.csv"), index=False)

# ---------------------------------------------------------------- capacity: how many names pass per day
exl = ev.exclude_bot.fillna("")
ok = ~exl.str.startswith("squeeze") & ~exl.str.startswith("real") & tr.ok.values
cap = []
for thr in (50, 55, 60, 65, 70, 75, 80):
    m = ok & (ev.score_live >= thr)
    per = ev[m].groupby("date").size().reindex(days).fillna(0)
    for p, s in (("IS", per[per.index <= IS_END]), ("OOS", per[per.index >= OOS_START])):
        cap.append({"min_score": thr, "period": p, "names_per_year": s.sum() / (len(s) / 252),
                    "days_with_any_pct": 100 * (s > 0).mean(), "mean_when_any": s[s > 0].mean(),
                    "p_days_gt5": 100 * (s > 5).mean(), "max": s.max()})
CAP = pd.DataFrame(cap)
CAP.to_csv(os.path.join(RES, "q4_capacity_counts.csv"), index=False)
print(CAP.round(2).to_string())

# ---------------------------------------------------------------- liquidity capacity: % of the first 5 minutes' volume
V, VW = B["V"], B["VW"]
dv5 = np.nansum(V[:, 1:6] * VW[:, 1:6], axis=1)       # $ traded 09:31-09:35 (adjusted px x vol = raw $)
v2 = sim.picks(ev, BK)
v2 = v2[v2.has_day1 & (dv5[v2.index] > 0)]
liq = []
for eq in (1e5, 1e6, 1e7):
    pos = 0.05 * eq
    part = pos / np.maximum(dv5[v2.index], 1)
    liq.append({"equity": eq, "pos_usd": pos, "median_pct_of_0931_0935_dollar_vol": 100 * np.median(part),
                "p90_pct": 100 * np.quantile(part, 0.9), "pct_of_adv20_median": 100 * np.median(pos / v2.adv20.values)})
LQ = pd.DataFrame(liq)
LQ.to_csv(os.path.join(RES, "q4_liquidity.csv"), index=False)
print(LQ.round(3).to_string())
c3.flush_trials("q4")
