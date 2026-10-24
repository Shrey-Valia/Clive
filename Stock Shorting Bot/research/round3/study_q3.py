"""Q3 selection: feature power, score re-weights, new-feature filters, ranking vs a rules filter.

Target for the feature study: the net short return of each eligible strict-gate event under the
live execution rule (09:31 entry, 35% stop, 15:55 exit), winsorised 0.5/99.5%.
Features are all known by the decision time (15:45 on T; premarket ones by 09:30 on T+1).

    python3 study_q3.py
"""
from __future__ import annotations

import math
import os
from dataclasses import replace

import numpy as np
import pandas as pd
from scipy import stats as sst

import common3 as c3
import rescore as rs
import sim
from r3common import CACHE, IS_END, OOS_START, RES

ev, B, days = c3.load_all()
LIVE = sim.Rule()
BK = sim.Book(score="score_live")
tr = sim.trade_returns(ev, B, LIVE)

# ---------------------------------------------------------------- derived features
ev["pm_gap"] = ev.pm_last / ev.p1545 - 1                  # premarket last vs scan price
ev["pm_dvol"] = ev.pm_vol * ev.pm_last                    # premarket dollar volume (adjusted px x vol = raw $)
ev["pm_dvol_rel"] = ev.pm_dvol / ev.adv20
ev["vwap_dist"] = ev.p1545 / ev.vwap1545 - 1              # 15:45 price vs T session VWAP
ev["runup5"] = ev.ret5_prior
ev["cat_hype"] = (ev.cat_class == "hype").astype(float)
ev["cat_none"] = (ev.cat_class == "no_news").astype(float)
ev["cat_unclear"] = (ev.cat_class == "unclear").astype(float)
ev["cat_dil"] = (ev.cat_class == "dilution").astype(float)
ev["log_dvol"] = np.log10(ev.dollar_vol1545)
sic = pd.read_csv(os.path.join(CACHE, "sic.csv")) if os.path.exists(os.path.join(CACHE, "sic.csv")) else None
if sic is not None:
    sic = sic.drop_duplicates("symbol")
    sic["sic2"] = (pd.to_numeric(sic.sic, errors="coerce") // 100)
    ev = ev.merge(sic[["symbol", "sic2"]], on="symbol", how="left")
    # same-industry peers in the strict top-20 scan that day (sector-wide move proxy)
    cnt = ev.groupby(["date", "sic2"]).symbol.transform("size")
    ev["peers"] = np.where(ev.sic2.notna(), cnt - 1, np.nan)
    ev["biotech"] = ev.sic2.isin([28, 80]).astype(float).where(ev.sic2.notna())
ev = ev.reset_index(drop=True)
tr = tr.reset_index(drop=True)

FEATURES = ["score_live", "short_score", "move_volume", "crowding", "catalyst", "chg1545", "rvol", "dtc", "si_pct",
            "wick", "cir", "pm_gap", "pm_dvol_rel", "vwap_dist", "runup5", "ext20", "float_rot", "gap_open_T",
            "intraday_T", "off_high", "hod_min", "last30_ret", "logcap", "log_dvol", "atr14", "n_news",
            "cat_hype", "cat_none", "cat_unclear", "cat_dil", "peers", "biotech", "rank1545"]
FEATURES = [f for f in FEATURES if f in ev.columns]

exl = ev.exclude_bot.fillna("")
base_m = tr.ok.values & ~exl.str.startswith("squeeze").values & ~exl.str.startswith("real").values
E = ev[base_m].copy()
E["y"] = tr.net.values[base_m]
lo, hi = E.y.quantile([0.005, 0.995])
E["yw"] = E.y.clip(lo, hi)

ic_rows = []
for f in FEATURES:
    for per, s in (("IS", E[E.date <= IS_END]), ("OOS", E[E.date >= OOS_START])):
        s = s[["date", f, "yw"]].dropna()
        ics = []
        for d, g in s.groupby("date"):
            if len(g) >= 4 and g[f].nunique() > 1:
                ics.append(sst.spearmanr(g[f], g.yw)[0])
        ics = np.array([x for x in ics if np.isfinite(x)])
        q = pd.qcut(s[f].rank(method="first"), 5, labels=False) if len(s) > 200 else None
        qm = s.groupby(q).yw.mean() if q is not None else pd.Series([np.nan] * 5)
        ic_rows.append({"feature": f, "period": per, "n": len(s), "days": len(ics),
                        "mean_ic": ics.mean() if len(ics) else np.nan,
                        "ic_t": ics.mean() / ics.std(ddof=1) * math.sqrt(len(ics)) if len(ics) > 2 else np.nan,
                        "q1": qm.iloc[0], "q5": qm.iloc[-1], "q5_q1": qm.iloc[-1] - qm.iloc[0]})
IC = pd.DataFrame(ic_rows)
IC.to_csv(os.path.join(RES, "q3_feature_ic.csv"), index=False)
piv = IC.pivot(index="feature", columns="period", values=["mean_ic", "ic_t", "q5_q1"])
piv.columns = [f"{a}_{b}" for a, b in piv.columns]
piv["same_sign_sig"] = (np.sign(piv.mean_ic_IS) == np.sign(piv.mean_ic_OOS)) & (piv.ic_t_IS.abs() > 2) & (piv.ic_t_OOS.abs() > 2)
print(piv.sort_values("ic_t_IS").round(3).to_string())
# Each feature screened counts as a trial (feature search is selection too)
for f in FEATURES:
    sim.TRIALS.append({"group": "Q3ic", "rule": "ic", "book": f, "is_sharpe": np.nan})

rows = []


def run(bk, group, rule=LIVE, trr=tr):
    row, d, t = sim.evaluate(ev, B, days, rule, bk, group, tr=trr)
    rows.append(row)
    print(f"{group:<8} {bk.name:<34} {c3.fmt(row)}", flush=True)
    return row, d, t


# ---------------------------------------------------------------- score variants
W0 = {"move_volume": 35, "crowding": 20, "ftd": 0, "catalyst": 35}
MS0 = {"change": 40, "rvol": 25, "wick": 20, "close_in_range": 15}
variants = {
    "score_norvol": dict(MOVE_SUBWEIGHTS={**MS0, "rvol": 0}),
    "score_dtc": dict(crowding_fn=rs.dtc_only_crowding),
    "score_norvol_dtc": dict(MOVE_SUBWEIGHTS={**MS0, "rvol": 0}, crowding_fn=rs.dtc_only_crowding),
    "score_crowd40": dict(WEIGHTS={**W0, "crowding": 40}),
    "score_crowd40_dtc_norvol": dict(WEIGHTS={**W0, "crowding": 40}, MOVE_SUBWEIGHTS={**MS0, "rvol": 0},
                                     crowding_fn=rs.dtc_only_crowding),
}
cache = os.path.join(CACHE, "r3_score_variants.parquet")
SV = pd.read_parquet(cache) if os.path.exists(cache) else pd.DataFrame(index=range(len(ev)))
for nm, kw in variants.items():
    if nm not in SV.columns or len(SV) != len(ev):
        SV[nm] = rs.score(ev, **kw)
SV.to_parquet(cache)
for nm in variants:
    ev[nm] = SV[nm].values
run(BK, "anchor")
for nm in variants:
    # same threshold (70) and also the threshold that matches the live score's IS trade count
    run(replace(BK, name=nm, score=nm), "Q3score")
    n_live = (ev[(ev.date <= IS_END)].score_live >= 70).sum()
    s_is = np.sort(ev[(ev.date <= IS_END)][nm].values)[::-1]
    thr = float(s_is[min(n_live, len(s_is) - 1)])
    run(replace(BK, name=f"{nm}_thr{thr:.0f}", score=nm, min_score=thr), "Q3score")

# ---------------------------------------------------------------- ranking vs a simple rules filter
# rules: no score at all; a name passes on raw facts at 15:45, top 5 by % change among those that pass
ev["neg_rank"] = -ev.rank1545
for chg in (0.20, 0.30, 0.50):
    for cir in (None, 70, 85):
        flt = [("chg1545", ">=", chg)]
        if cir is not None:
            flt.append(("cir", "<=", cir))
        nm = f"rules_chg{int(chg * 100)}_cir{cir}"
        run(sim.Book(name=nm, score="chg1545", min_score=None, filters=tuple(flt)), "Q3rules")

# ---------------------------------------------------------------- new-feature filters on top of v2 (one at a time)
filters = {
    "pm_gap<=0.10": ("pm_gap", "<=", 0.10), "pm_gap<=0.0": ("pm_gap", "<=", 0.0),
    "pm_gap>=-0.05": ("pm_gap", ">=", -0.05),
    "vwap_dist>=0.0": ("vwap_dist", ">=", 0.0), "vwap_dist<=0.10": ("vwap_dist", "<=", 0.10),
    "cir<=85": ("cir", "<=", 85), "cir<=70": ("cir", "<=", 70),
    "wick>=0.15": ("wick", ">=", 0.15),
    "runup5<=0.5": ("runup5", "<=", 0.5), "runup5>=0.0": ("runup5", ">=", 0.0),
    "ext20<=1.0": ("ext20", "<=", 1.0),
    "float_rot>=0.5": ("float_rot", ">=", 0.5), "float_rot<=2.0": ("float_rot", "<=", 2.0),
    "dtc<=5": ("dtc", "<=", 5), "rvol<=8": ("rvol", "<=", 8),
    "chg1545>=0.3": ("chg1545", ">=", 0.3), "chg1545<=1.0": ("chg1545", "<=", 1.0),
    "cat_hype==0": ("cat_hype", "==", 0), "cat_none==1": ("cat_none", "==", 1),
    "hod_min>=300": ("hod_min", ">=", 300), "hod_min<=300": ("hod_min", "<=", 300),
    "last30_ret<=0": ("last30_ret", "<=", 0.0),
}
if "peers" in ev.columns:
    filters.update({"peers==0": ("peers", "==", 0), "peers>=1": ("peers", ">=", 1), "biotech==0": ("biotech", "==", 0)})
for nm, f in filters.items():
    run(replace(BK, name=f"v2+{nm}", filters=(f,)), "Q3filter")

df = pd.DataFrame(rows)
df.drop(columns=["cfg"]).to_csv(os.path.join(RES, "q3_selection.csv"), index=False)
c3.flush_trials("q3")
ev[["symbol", "date"] + [c for c in ("pm_gap", "vwap_dist", "peers", "sic2", "biotech", "pm_dvol_rel") if c in ev.columns]
   + list(variants)].to_parquet(os.path.join(CACHE, "r3_features.parquet"))
