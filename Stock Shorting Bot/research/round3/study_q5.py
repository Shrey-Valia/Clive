"""Q5 regime filters (market trend, VIX level and term structure, weekday, month).

A filter is reported as real only if it raises the v2 book Sharpe in IS AND in OOS, and the
skipped days' mean trade is worse than the kept days' in both periods (on v2 picks and on the
broad strict-gate set). Every filter is a counted trial.

    python3 study_q5.py
"""
from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pandas as pd

import common3 as c3
import sim
from r3common import CACHE, IS_END, OOS_START, RES

ev, B, days = c3.load_all()
v3 = pd.read_csv(os.path.join(CACHE, "VIX3M_History.csv"))
v3["date"] = pd.to_datetime(v3.DATE).dt.strftime("%Y-%m-%d")
ev["vix3m_T"] = ev.date.map(v3.set_index("date").CLOSE.to_dict())
ev["vix_term"] = ev.vix_T / ev.vix3m_T                 # > 1 = backwardation (stress), known at T close
ev["vix_vs_ma"] = ev.vix_T / ev.vix_ma20 - 1
ev["wd"] = ev.weekday.astype(float)
ev["mo"] = ev.month.astype(float)
LIVE = sim.Rule()
BK = sim.Book(score="score_live")
tr = sim.trade_returns(ev, B, LIVE)
rows = []

REG = {
    "spy_above_sma50": ("spy_sma50", ">", 0), "spy_below_sma50": ("spy_sma50", "<", 0),
    "spy_above_sma200": ("spy_sma200", ">", 0), "spy_below_sma200": ("spy_sma200", "<", 0),
    "iwm_above_sma50": ("iwm_sma50", ">", 0), "iwm_below_sma50": ("iwm_sma50", "<", 0),
    "iwm_ret20_pos": ("iwm_ret20", ">", 0), "iwm_ret20_neg": ("iwm_ret20", "<", 0),
    "spy_up_on_T": ("spy_ret_T", ">", 0), "spy_down_on_T": ("spy_ret_T", "<", 0),
    "vix_lt15": ("vix_T", "<", 15), "vix_ge15": ("vix_T", ">=", 15), "vix_lt20": ("vix_T", "<", 20),
    "vix_ge20": ("vix_T", ">=", 20), "vix_lt30": ("vix_T", "<", 30),
    "term_contango": ("vix_term", "<", 1.0), "term_backwardation": ("vix_term", ">=", 1.0),
    "vix_below_ma20": ("vix_vs_ma", "<", 0), "vix_above_ma20": ("vix_vs_ma", ">", 0),
}
for wd in range(5):
    REG[f"skip_T_weekday{wd}"] = ("wd", "!=", wd)
for mo in range(1, 13):
    REG[f"skip_month{mo}"] = ("mo", "!=", mo)

exl = ev.exclude_bot.fillna("")
okb = tr.ok.values & ~exl.str.startswith("squeeze").values & ~exl.str.startswith("real").values
row0, d0, t0 = sim.evaluate(ev, B, days, LIVE, BK, "anchor", tr=tr, log=False)
print("live", c3.fmt(row0))
for nm, f in REG.items():
    row, d, t = sim.evaluate(ev, B, days, LIVE, replace(BK, name=nm, regime=(f,)), "Q5", tr=tr)
    col, op, val = f
    keep = sim.OPS[op](ev[col].values.astype(float), val) & np.isfinite(ev[col].values.astype(float))
    for p, per in (("is", ev.date.values <= IS_END), ("oos", ev.date.values >= OOS_START)):
        sel_v2 = t0[(t0.date <= IS_END) if p == "is" else (t0.date >= OOS_START)]
        kv2 = keep[sel_v2.i.values]
        row[f"{p}_v2_keep_mean"] = sel_v2.net[kv2].mean()
        row[f"{p}_v2_skip_mean"] = sel_v2.net[~kv2].mean()
        row[f"{p}_v2_skip_n"] = int((~kv2).sum())
        mb = okb & per
        row[f"{p}_broad_keep_mean"] = tr.net[mb & keep].clip(-1.5, 1).mean()
        row[f"{p}_broad_skip_mean"] = tr.net[mb & ~keep].clip(-1.5, 1).mean()
    row["holds_both"] = bool(row["is_sharpe"] > row0["is_sharpe"] and row["oos_sharpe"] > row0["oos_sharpe"]
                             and row["is_v2_skip_mean"] < row["is_v2_keep_mean"]
                             and row["oos_v2_skip_mean"] < row["oos_v2_keep_mean"]
                             and row["is_broad_skip_mean"] < row["is_broad_keep_mean"]
                             and row["oos_broad_skip_mean"] < row["oos_broad_keep_mean"])
    rows.append(row)
    print(f"{nm:<20} {c3.fmt(row)} | skip n IS {row['is_v2_skip_n']:3d} OOS {row['oos_v2_skip_n']:3d} "
          f"| v2 keep/skip IS {100 * row['is_v2_keep_mean']:+.2f}/{100 * row['is_v2_skip_mean']:+.2f} "
          f"OOS {100 * row['oos_v2_keep_mean']:+.2f}/{100 * row['oos_v2_skip_mean']:+.2f} | both={row['holds_both']}", flush=True)
pd.DataFrame(rows).drop(columns=["cfg"]).to_csv(os.path.join(RES, "q5_regime.csv"), index=False)
c3.flush_trials("q5")
