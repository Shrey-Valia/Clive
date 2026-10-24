"""Final round-3 table: v2 (live) vs the IS-selected candidates, with cost stress, per-year
results, worst trades, deflated Sharpe vs every trial so far, and the charts.

Candidates are declared in CANDIDATES below (each was chosen on IS in the study scripts).
    python3 final_r3.py
"""
from __future__ import annotations

import json
import os
from dataclasses import replace

import numpy as np
import pandas as pd

import common3 as c3
import sim
from plotstyle import AMBER, GREEN, GREY, RED, WHITE, plt
from r3common import IS_END, OOS_START, OUT, RES

ev, B, days = c3.load_all()
LIVE = sim.Rule()
BK = sim.Book(score="score_live")
SEL = json.load(open(os.path.join(RES, "selection_r3.json")))
CANDIDATES = {"v2_live": (LIVE, BK)}
for nm, c in SEL["candidates"].items():
    r = c.get("rule", {})
    if "checkpoint" in r and r["checkpoint"] is not None:
        r["checkpoint"] = tuple(r["checkpoint"])
    b = c.get("book", {})
    if "filters" in b:
        b["filters"] = tuple(tuple(f) for f in b["filters"])
    CANDIDATES[nm] = (replace(LIVE, name=nm, **r), replace(BK, name=nm, **b))
# feature screens (no Sharpe) that count toward N
EXTRA = SEL.get("extra_trials", 0)

rows, curves, trades = [], {}, {}
for nm, (rule, bk) in CANDIDATES.items():
    for cm in (1.0, 2.0):
        r = replace(rule, cost_mult=cm)
        tr = sim.trade_returns(ev, B, r)
        row, d, t = sim.evaluate(ev, B, days, r, bk, "final", log=False, tr=tr)
        di = d[d.date <= IS_END]
        do = d[d.date >= OOS_START]
        row.update(c3.dsr_for(di.ret.values, EXTRA))
        row["oos_psr"] = sim.psr(do.ret.values)
        row["cost_mult"] = cm
        row["name"] = nm
        rows.append(row)
        if cm == 1.0:
            curves[nm] = d
            trades[nm] = t
        print(f"{nm:<28} cost x{cm:g} {c3.fmt(row)} | DSR {row['dsr']:.3f} (N={row['n_trials']}) OOS PSR {row['oos_psr']:.3f}")
F = pd.DataFrame(rows).drop(columns=["cfg"])
F.to_csv(os.path.join(RES, "final_table.csv"), index=False)

yr = pd.DataFrame({nm: d.assign(y=d.date.str[:4]).groupby("y").ret.apply(lambda r: np.prod(1 + r) - 1)
                   for nm, d in curves.items()})
cnt = pd.DataFrame({f"{nm}_trades": t.assign(y=t.date.str[:4]).groupby("y").size() for nm, t in trades.items()})
by_year = pd.concat([yr, cnt], axis=1)
by_year.to_csv(os.path.join(RES, "final_by_year.csv"))
print(by_year.round(4).to_string())
for nm, t in trades.items():
    t.sort_values("net").head(10)[["date", "symbol", "ent", "ex", "te", "tx", "why", "gross", "net", "w"]].to_csv(
        os.path.join(RES, f"final_worst_{nm}.csv"), index=False)
    t.to_csv(os.path.join(RES, f"final_trades_{nm}.csv"), index=False)

# ---------------------------------------------------------------- charts
cols = [AMBER, GREEN, WHITE, "#6fa8ff", "#c792ea", RED]
fig, ax = plt.subplots(figsize=(12, 5.5))
for (nm, d), c in zip(curves.items(), cols):
    e = (1 + d.set_index("date").ret).cumprod()
    ax.plot(pd.to_datetime(e.index), e.values, color=c, lw=1.3, label=nm)
ax.axvline(pd.Timestamp(OOS_START), color=GREY, ls=":")
ax.text(pd.Timestamp(OOS_START), ax.get_ylim()[1], "  OOS ->", color=GREY, va="top")
ax.set_title("ROUND 3: EQUITY, NET OF COSTS (5% PER NAME)"); ax.legend(loc="upper left")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "final_equity.png"), dpi=130); plt.close(fig)

q = pd.read_csv(os.path.join(RES, "q12_entry_exit.csv"))
live_row = q[(q.rule == "v2_live") & (q.book == "v2")].iloc[0]
for fam, fn, title in (("Q2stop", "stop_sweep", "HARD STOP LEVEL"), ("Q1entry", "entry_timing", "ENTRY TIME"),
                       ("Q2time", "time_exit", "TIME EXIT"), ("Q2trail", "trail", "TRAILING STOP"),
                       ("Q2target", "target", "PROFIT TARGET")):
    f = pd.concat([q[q.group == fam], live_row.to_frame().T])
    f = f.drop_duplicates("rule")
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.2))
    x = np.arange(len(f))
    for ax, (a, b, lab) in zip(axs, (("is_sharpe", "oos_sharpe", "v2 book Sharpe"),
                                     ("is_bmean", "oos_bmean", "broad mean net trade"))):
        mult = 100 if "mean" in a else 1
        ax.bar(x - 0.2, f[a].astype(float) * mult, 0.4, color=AMBER, label="IS 2016-21")
        ax.bar(x + 0.2, f[b].astype(float) * mult, 0.4, color=WHITE, label="OOS 2022-26")
        ax.set_xticks(x, f.rule, rotation=45, ha="right", fontsize=8)
        ax.axhline(0, color=GREY); ax.set_title(f"{title}: {lab}{' %' if mult == 100 else ''}"); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, f"q12_{fn}.png"), dpi=130); plt.close(fig)

ic = pd.read_csv(os.path.join(RES, "q3_feature_ic.csv"))
pv = ic.pivot(index="feature", columns="period", values="mean_ic").sort_values("IS")
fig, ax = plt.subplots(figsize=(10, 9))
y = np.arange(len(pv))
ax.barh(y + 0.2, pv.IS, 0.4, color=AMBER, label="IS 2016-21")
ax.barh(y - 0.2, pv.OOS, 0.4, color=WHITE, label="OOS 2022-26")
ax.set_yticks(y, pv.index); ax.axvline(0, color=GREY)
ax.set_title("FEATURE RANK IC vs T+1 NET SHORT RETURN (live execution)\n(+ = higher value -> better short)")
ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(OUT, "q3_feature_ic.png"), dpi=130); plt.close(fig)

rg = pd.read_csv(os.path.join(RES, "q5_regime.csv"))
fig, ax = plt.subplots(figsize=(12, 6))
x = np.arange(len(rg))
ax.bar(x - 0.2, rg.is_sharpe - live_row.is_sharpe, 0.4, color=AMBER, label="IS change in Sharpe")
ax.bar(x + 0.2, rg.oos_sharpe - live_row.oos_sharpe, 0.4, color=WHITE, label="OOS change in Sharpe")
ax.set_xticks(x, rg.book, rotation=70, ha="right", fontsize=7); ax.axhline(0, color=GREY)
ax.set_title("REGIME FILTERS: SHARPE CHANGE vs v2 LIVE (a real filter is up in BOTH)"); ax.legend()
fig.tight_layout(); fig.savefig(os.path.join(OUT, "q5_regime.png"), dpi=130); plt.close(fig)

sl = pd.read_csv(os.path.join(RES, "daily_sleeve_live.csv"))
dl = curves["v2_live"]
comb = dl.set_index("date").ret.add(sl.set_index("date").ret.reindex(dl.date).fillna(0).values)
fig, ax = plt.subplots(figsize=(12, 5))
for s, c, lab in ((dl.set_index("date").ret, AMBER, "v2 short (live)"), (sl.set_index("date").ret, GREEN, "overnight long sleeve (09:31 exit)"),
                  (comb, WHITE, "both")):
    e = (1 + s).cumprod()
    ax.plot(pd.to_datetime(e.index), e.values, color=c, lw=1.2, label=lab)
ax.axvline(pd.Timestamp(OOS_START), color=GREY, ls=":")
ax.set_title("OVERNIGHT LONG SLEEVE vs THE SHORT (5% PER NAME, NET)"); ax.legend(loc="upper left")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "q6_sleeve.png"), dpi=130); plt.close(fig)
print("charts written to", OUT)
