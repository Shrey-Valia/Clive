"""Q1 (entry timing, gap filters) and Q2 (minute-bar exits) on the v2 selection.

Every config is a counted trial. Selection inside each family is by IS Sharpe of the v2 book only;
OOS is printed beside it but never used to choose. The 'broad' columns repeat the rule on every
eligible strict-gate event (no score filter, ~11k trades) to check that an effect is not a
336-trade accident.

    python3 study_q12.py
"""
from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pandas as pd

import common3 as c3
import sim
from r3common import RES

ev, B, days = c3.load_all()
BK = sim.Book(score="score_live")     # v2 as live: live score (FTD weight 0) >= 70, top 5, 5% each
BK_P7 = sim.Book(name="p7_score", score="short_score")   # round-1 P7 score (FTD weight 10)
LIVE = sim.Rule()                     # 09:31 entry, 35% stop, gap-up skip 40%, 15:55 exit
rows = []


def run(rule, group, bk=BK, log=True):
    tr = sim.trade_returns(ev, B, rule)
    row, d, t = sim.evaluate(ev, B, days, rule, bk, group, log=log, tr=tr)
    row.update(c3.broad_stats(tr, ev, days))
    row["why"] = t.why.value_counts().to_dict()
    rows.append(row)
    print(f"{group:<8} {rule.name:<26} {c3.fmt(row)} | broad IS {100 * row['is_bmean']:+.2f}% OOS {100 * row['oos_bmean']:+.2f}%",
          flush=True)
    return row, d, t, tr


# ---------------------------------------------------------------- anchors
p7 = sim.Rule(name="p7_spec_open_close", entry="open", max_gap_up=None, stop=None, time_exit=390)
run(p7, "anchor", bk=replace(BK_P7, w=0.10, name="p7_10pct"), log=False)     # should be ~ round-1 P7
run(LIVE, "anchor", bk=BK_P7, log=False)                                       # live execution, P7 score
run(p7, "anchor", log=False)                                                   # P7 execution, live score
_, d_live, t_live, tr_live = run(LIVE, "anchor", log=False)

# ---------------------------------------------------------------- Q1 entry timing
for e in ("open", "m5", "m15", "m30", "orb5", "orb15", "orb30"):
    run(replace(LIVE, name=f"entry_{e}", entry=e), "Q1entry")
for g in (None, 0.20, 0.10, 0.0):
    run(replace(LIVE, name=f"gapup_max_{g}", max_gap_up=g), "Q1gap")
for g in (-0.05, -0.10, -0.20):
    run(replace(LIVE, name=f"gapdown_min_{g}", min_gap=g), "Q1gap")

# ---------------------------------------------------------------- Q2 exits
for s in (None, 0.05, 0.10, 0.15, 0.20, 0.25, 0.50, 0.75):
    run(replace(LIVE, name=f"stop_{s}", stop=s), "Q2stop")
for tr_ in (0.05, 0.10, 0.15, 0.20, 0.30):
    run(replace(LIVE, name=f"trail_{tr_}", trail=tr_), "Q2trail")
for arm, tr_ in ((0.05, 0.05), (0.10, 0.05), (0.10, 0.10)):
    run(replace(LIVE, name=f"trail_{tr_}_arm{arm}", trail=tr_, trail_arm=arm), "Q2trail")
for tg in (0.05, 0.10, 0.15, 0.20, 0.30):
    run(replace(LIVE, name=f"target_{tg}", target=tg), "Q2target")
for te in (60, 150, 270, 330, 390):
    run(replace(LIVE, name=f"time_{te}", time_exit=te), "Q2time")
for va in (15, 30, 60, 120):
    run(replace(LIVE, name=f"vwap_after{va}", vwap_exit=True, vwap_after=va), "Q2vwap")
for cp in ((30, 0.10), (60, 0.10), (30, 0.05), (60, 0.20)):
    run(replace(LIVE, name=f"checkpoint_{cp[0]}_{cp[1]}", checkpoint=cp), "Q2ckpt")

# ---------------------------------------------------------------- IS-only family winners, then one small combined grid
df = pd.DataFrame(rows)
best = {}
for fam in ("Q1entry", "Q1gap", "Q2stop", "Q2trail", "Q2target", "Q2time", "Q2vwap", "Q2ckpt"):
    f = df[df.group == fam]
    b = f.loc[f.is_sharpe.idxmax()]
    best[fam] = b.rule
    print(f"IS-best {fam:<9} {b.rule:<24} IS {b.is_sharpe:.2f}")

import json  # noqa: E402

cfgs = {r.rule: json.loads(r.cfg)["rule"] for r in df.itertuples()}
live_is = df[(df.rule == "v2_live") & (df.book == "v2")].is_sharpe.iloc[0]
print("live IS Sharpe", round(live_is, 3))
gain = {best[f]: df[df.rule == best[f]].is_sharpe.iloc[0] - live_is for f in best}
improvers = [nm for nm, g in sorted(gain.items(), key=lambda x: -x[1]) if g > 0.10][:3]
print("top-3 families whose IS-best beats live IS by > 0.10 (combined below):", improvers)
# combined grid: every subset (size 2-3) of the IS-improving family winners
import itertools  # noqa: E402

KEYS = {"Q1entry": ["entry", "entry_cutoff"], "Q1gap": ["max_gap_up", "min_gap"], "Q2stop": ["stop"],
        "Q2trail": ["trail", "trail_arm"], "Q2target": ["target"], "Q2time": ["time_exit"],
        "Q2vwap": ["vwap_exit", "vwap_after"], "Q2ckpt": ["checkpoint"]}
fam_of = {best[f]: f for f in best}
for k in (2, 3):
    for combo in itertools.combinations(improvers, k):
        kw = {}
        for nm in combo:
            for key in KEYS[fam_of[nm]]:
                v = cfgs[nm][key]
                kw[key] = tuple(v) if isinstance(v, list) else v
        run(replace(LIVE, name="+".join(combo), **kw), "Q12combo")

df = pd.DataFrame(rows)
df.drop(columns=["cfg"]).to_csv(os.path.join(RES, "q12_entry_exit.csv"), index=False)
c3.flush_trials("q12")
t_live.to_csv(os.path.join(RES, "trades_v2_live.csv"), index=False)
d_live.to_csv(os.path.join(RES, "daily_v2_live.csv"), index=False)

# ---------------------------------------------------------------- pre-declared execution family (auction in / auction out)
# Hypothesis from Research/institutional_review.md s.6 (written before round 3): use the opening
# and closing auctions instead of 09:31 / 15:55 market orders. Full 2x2x2 of entry x stop x exit.
sim.TRIALS.clear()
rows.clear()
for e in ("open", "m1"):
    for s in (0.35, None):
        for te in (385, 390):
            # a market-on-open order is sent before 09:28, so its gap-up skip can only use the
            # last premarket print (gap_ref="pm"), never the opening print itself
            run(replace(LIVE, name=f"exec_{e}_stop{s}_exit{te}", entry=e, stop=s, time_exit=te,
                        gap_ref="pm" if e == "open" else "open"), "Q12exec")
pd.DataFrame(rows).drop(columns=["cfg"]).to_csv(os.path.join(RES, "q12_exec_family.csv"), index=False)
c3.flush_trials("q12exec")
