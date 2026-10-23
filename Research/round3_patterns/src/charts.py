"""Extra charts: noise-momentum robustness (cross-ETF, neighbourhood, cost), trend drawdowns vs SPY."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import OUT, RES

plt.rcParams.update({"figure.dpi": 130, "axes.spines.top": False, "axes.spines.right": False, "font.size": 9})

xs = pd.read_csv(os.path.join(RES, "f5b_xs.csv"))
fig, ax = plt.subplots(figsize=(8, 3.6))
x = np.arange(len(xs)); w = 0.4
ax.bar(x - w / 2, xs.is_sharpe, w, label="IS 2016-21", color="#9ecae1")
ax.bar(x + w / 2, xs.oos_sharpe, w, label="OOS 2022-26", color="#1f77b4")
ax.set_xticks(x); ax.set_xticklabels(xs.sym); ax.axhline(0, color="k", lw=0.6)
ax.set_title("Noise-area intraday momentum, identical rules on 13 ETFs (0.5 bp/side)"); ax.legend(fontsize=7)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "noise_cross_etf.png")); plt.close(fig)

dd = pd.read_csv(os.path.join(RES, "f5b_deep.csv"))
nb = dd[dd.name.str.startswith("F5b_NB_")].copy()
base = dd[dd.name == "F5b_NOISE_voltgt_QQQ_fillnext"].iloc[0]
nb = pd.concat([nb, pd.DataFrame([{**base.to_dict(), "lb": 14, "step": 30, "band": 1.0}])])
fig, axs = plt.subplots(1, 3, figsize=(10, 3.2), sharey=True)
for a, b in zip(axs, [0.75, 1.0, 1.25]):
    g = nb[nb.band == b].pivot_table(index="lb", columns="step", values="oos_sharpe")
    im = a.imshow(g.values, vmin=0, vmax=1.6, cmap="Blues")
    a.set_xticks(range(g.shape[1])); a.set_xticklabels(g.columns); a.set_yticks(range(g.shape[0])); a.set_yticklabels(g.index)
    for i in range(g.shape[0]):
        for j in range(g.shape[1]):
            a.text(j, i, f"{g.values[i, j]:.2f}", ha="center", va="center", fontsize=8)
    a.set_title(f"band x{b}"); a.set_xlabel("check step (min)")
axs[0].set_ylabel("sigma lookback (days)")
fig.suptitle("QQQ noise momentum: OOS Sharpe across the parameter neighbourhood"); fig.tight_layout()
fig.savefig(os.path.join(OUT, "noise_neighbourhood.png")); plt.close(fig)

cs = dd[dd.name.str.startswith("QQQ_cost")].copy()
cs = pd.concat([cs, dd[dd.name == "F5b_NOISE_voltgt_QQQ_fillnext"]]).sort_values("cost_bp")
fig, ax = plt.subplots(figsize=(5, 3.2))
ax.plot(cs.cost_bp, cs.is_sharpe, "o-", label="IS"); ax.plot(cs.cost_bp, cs.oos_sharpe, "o-", label="OOS")
ax.plot(cs.cost_bp, cs.post_pub_sharpe, "o-", label="post-publication")
ax.axhline(0, color="k", lw=0.6); ax.set_xlabel("cost per side (bp)"); ax.set_ylabel("Sharpe")
ax.set_title("QQQ noise momentum: cost sensitivity"); ax.legend(fontsize=7); fig.tight_layout()
fig.savefig(os.path.join(OUT, "noise_cost.png")); plt.close(fig)

s3 = pd.read_parquet(os.path.join(RES, "f3_series.parquet"))
fig, ax = plt.subplots(figsize=(8, 3.6))
for k, c in [("F3_TS_xasset_lf_blend", "#2ca02c"), ("BH_SPY", "#7f7f7f"), ("F3_TS_xasset_ls_blend", "#98df8a")]:
    r = s3[k].dropna(); r = r[r.index >= "2001-07-01"]
    eq = (1 + r).cumprod(); ax.plot(eq / eq.cummax() - 1, label=k, color=c, lw=1)
ax.axvline(pd.Timestamp("2016-01-01"), color="k", ls="--", lw=0.8)
ax.set_title("Drawdowns: cross-asset trend (long/flat, long/short) vs SPY, 2001-2026"); ax.legend(fontsize=7)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "trend_drawdowns.png")); plt.close(fig)
print("ok")
