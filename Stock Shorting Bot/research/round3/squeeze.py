"""What a squeeze day looks like intraday, and what a stop at X% buys and costs.

From the 09:31 fill (v2 live entry), per trade: the maximum adverse excursion (MAE, the highest
high before 15:55 vs entry), the minute it is reached, and the 15:55 return with no stop.
A stop at X% "saves" a trade whose 15:55 return is worse than -X% and "costs" one that touched
+X% but would have finished better than -X%.

    python3 squeeze.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

import common3 as c3
import sim
from plotstyle import AMBER, GREEN, GREY, RED, WHITE, plt
from r3common import IS_END, OOS_START, OUT, RES

ev, B, days = c3.load_all()
H, O = B["H"], B["O"]
nostop = sim.trade_returns(ev, B, sim.Rule(name="nostop", stop=None))
BK = sim.Book(score="score_live")
_, t = sim.book(ev, nostop, BK, days)

exl = ev.exclude_bot.fillna("")
broad_m = nostop.ok.values & ~exl.str.startswith("squeeze").values & ~exl.str.startswith("real").values


def mae_table(tr: pd.DataFrame) -> pd.DataFrame:
    i = tr.i.values
    te = tr.te.values
    ent = tr.ent.values
    mae = np.full(len(tr), np.nan)
    tmax = np.full(len(tr), -1)
    for k, (ii, a) in enumerate(zip(i, te)):
        h = H[ii, a + 1:385]
        if np.isfinite(h).any():
            j = np.nanargmax(h)
            mae[k] = h[j] / ent[k] - 1
            tmax[k] = a + 1 + j
    return tr.assign(mae=mae, t_mae=tmax, ret1555=-(tr.ex.values / ent - 1))


v2 = mae_table(t)
br = mae_table(nostop[broad_m])
rows = []
for nm, df in (("v2", v2), ("broad", br)):
    for p, s in (("IS", df[df.date <= IS_END]), ("OOS", df[df.date >= OOS_START])):
        for x in (0.05, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50):
            touched = s.mae >= x
            saved = touched & (s.ret1555 < -x)
            cost = touched & (s.ret1555 >= -x)
            rows.append({"set": nm, "period": p, "stop": x, "n": len(s), "touch_pct": 100 * touched.mean(),
                         "saved_n": int(saved.sum()), "cost_n": int(cost.sum()),
                         "saved_sum": float((-x - s.ret1555[saved]).sum()),          # return points saved
                         "cost_sum": float((s.ret1555[cost] + x).sum()),             # return points given up
                         "net_effect_per_trade": float(((-x - s.ret1555[saved]).sum() - (s.ret1555[cost] + x).sum()) / len(s))})
ST = pd.DataFrame(rows)
ST.to_csv(os.path.join(RES, "squeeze_stop_ledger.csv"), index=False)
print(ST.round(4).to_string())

# when does the high of a bad day print?
for nm, df in (("v2", v2), ("broad", br)):
    bad = df[df.mae >= 0.20]
    print(nm, "MAE>=20%:", len(bad), "| median minute of the high", np.median(bad.t_mae),
          "| share of highs before 10:00 %.0f%%" % (100 * (bad.t_mae < 30).mean()),
          "| share finishing below entry (short wins anyway) %.0f%%" % (100 * (bad.ret1555 > 0).mean()))

worst = v2.sort_values("ret1555").head(15)
worst[["date", "symbol", "ent", "ex", "ret1555", "mae", "t_mae"]].to_csv(os.path.join(RES, "squeeze_worst_v2.csv"), index=False)
print(worst[["date", "symbol", "ret1555", "mae", "t_mae"]].round(3).to_string())

# ---------------------------------------------------------------- plots
fig, ax = plt.subplots(figsize=(12, 5.5))
mins = np.arange(390)
lab = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
for k, r in enumerate(worst.head(10).itertuples()):
    p = B["C"][r.i] / r.ent - 1
    ax.plot(mins, 100 * p, lw=1.1, label=f"{r.symbol} {r.date}", alpha=0.9)
for x, c in ((0.10, GREEN), (0.20, AMBER), (0.35, RED)):
    ax.axhline(100 * x, color=c, ls="--", lw=0.8)
    ax.text(392, 100 * x, f"stop {int(100 * x)}%", color=c, va="center", fontsize=8)
ax.axvline(1, color=GREY, ls=":")
ax.set_xticks(range(0, 390, 60), lab)
ax.set_ylabel("price vs 09:31 short fill, %  (up = loss)")
ax.set_title("THE 10 WORST v2 SHORTS (NO STOP), MINUTE CLOSES ON THE TRADE DAY")
ax.legend(fontsize=7, ncol=2, loc="upper left")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "squeeze_worst_paths.png"), dpi=130); plt.close(fig)

fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
for ax, nm in zip(axs, ("v2", "broad")):
    s = ST[(ST.set == nm)]
    for p, c in (("IS", AMBER), ("OOS", WHITE)):
        q = s[s.period == p]
        ax.plot(100 * q.stop, 100 * q.net_effect_per_trade, marker="o", color=c, label=p)
    ax.axhline(0, color=GREY)
    ax.set_xlabel("stop level, % above entry"); ax.set_ylabel("net effect per trade, % (+ = stop helps)")
    ax.set_title(f"STOP LEDGER, {nm.upper()} ({'v2 picks' if nm == 'v2' else 'all eligible strict-gate'})")
    ax.legend()
fig.tight_layout(); fig.savefig(os.path.join(OUT, "squeeze_stop_ledger.png"), dpi=130); plt.close(fig)
