"""Cross-family analysis: trial count, deflated Sharpe, verdict table inputs, combination with the
shorting bot, charts.

DSR (Bailey & Lopez de Prado 2014): all registered trials (every variant of every family, including the
parameter-neighbourhood and cross-ETF robustness runs) define N and the cross-trial variance of the daily
Sharpe. Sharpe estimates use the common window 2017-02-01..2026-10-20 (all trials exist there), daily
returns with 0 on flat days.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from common import OUT, RES, TRIALS, TRIALS_META, by_year, corr_block, deflated_sharpe, perf, shortbot_daily, spy_daily, split_perf

T = pd.read_parquet(TRIALS)
T.index = pd.to_datetime(T.index)
meta = json.load(open(TRIALS_META))
W0, W1 = "2017-02-01", "2026-10-20"
spy = spy_daily()
cal = spy[(spy.index >= W0) & (spy.index <= W1)].index
TT = T.reindex(cal).fillna(0)
sr = TT.mean() / TT.std()
N = len(sr)
var_sr = sr.var()
fam = pd.Series({k: v["family"] for k, v in meta.items()})
print("trials:", N, "by family:", fam.value_counts().to_dict())
print("cross-trial var of daily SR:", var_sr, "-> annualised SD", np.sqrt(var_sr * 252))

rows = []
for k in TT.columns:
    x = TT[k]
    s = sr[k]
    d, sr0 = deflated_sharpe(s, len(x), stats.skew(x), stats.kurtosis(x, fisher=False), var_sr, N)
    oos = x[x.index >= "2022-01-01"]
    so = oos.mean() / oos.std() if oos.std() > 0 else np.nan
    d_oos, _ = deflated_sharpe(so, len(oos), stats.skew(oos), stats.kurtosis(oos, fisher=False), var_sr, N) if so == so else (np.nan, np.nan)
    rows.append(dict(name=k, family=fam.get(k), sharpe_win=s * np.sqrt(252), sr0_ann=sr0 * np.sqrt(252), dsr_win=d,
                     oos_sharpe=so * np.sqrt(252), dsr_oos=d_oos, psr_oos_vs0=stats.norm.cdf(so * np.sqrt(len(oos) - 1)) if so == so else np.nan))
D = pd.DataFrame(rows).sort_values("dsr_win", ascending=False)
D.to_csv(os.path.join(RES, "dsr_all_trials.csv"), index=False)
json.dump({"n_trials": int(N), "var_daily_sr": float(var_sr), "window": [W0, W1],
           "by_family": fam.value_counts().to_dict()}, open(os.path.join(RES, "trial_summary.json"), "w"), indent=1)
pd.set_option("display.width", 220)
print(D.head(25).round(3).to_string())

# ---------------------------------------------------------------- candidates
CANDS = {
    "noise_QQQ": "F5b_NOISE_voltgt_QQQ_fillnext",
    "trend_xasset_lf": "F3_TS_xasset_lf_blend",
    "overnight_IWM": "F2a_ON_IWM_c0.5",
    "mom121_LS": "F7_MOM121_M_LS",
}
sb = shortbot_daily().reindex(spy.index).fillna(0)
C = pd.DataFrame({k: T[v] for k, v in CANDS.items()}).reindex(spy.index)
C["shortbot_P7"] = sb
C["SPY"] = spy
C = C[C.index >= "2016-09-01"].fillna(0)
C.to_parquet(os.path.join(RES, "candidates_daily.parquet"))
print("\ncorrelation matrix (daily, 2016-09..):")
print(C.corr().round(3).to_string())
print("\nmonthly correlation:")
M = (1 + C).resample("ME").prod() - 1
print(M.corr().round(3).to_string())
M.corr().to_csv(os.path.join(RES, "candidates_corr_monthly.csv"))
C.corr().to_csv(os.path.join(RES, "candidates_corr_daily.csv"))

# combination: the shorting bot plus each sleeve at equal capital share, and all three
combos = {
    "shortbot_only": C.shortbot_P7,
    "shortbot+noise (50/50)": 0.5 * C.shortbot_P7 + 0.5 * C.noise_QQQ,
    "shortbot+trend (50/50)": 0.5 * C.shortbot_P7 + 0.5 * C.trend_xasset_lf,
    "shortbot+noise+trend (1/3 each)": (C.shortbot_P7 + C.noise_QQQ + C.trend_xasset_lf) / 3,
    "noise+trend (50/50)": 0.5 * C.noise_QQQ + 0.5 * C.trend_xasset_lf,
    # overlay: the short bot uses <=50% gross on ~30 days a year, so the sleeves can share the same capital
    "overlay: shortbot + noise + trend on full capital each": C.shortbot_P7 + 0.5 * C.noise_QQQ + 0.5 * C.trend_xasset_lf,
}
cr = []
for k, v in combos.items():
    p = perf(v); sp = split_perf(v)
    cr.append(dict(combo=k, cagr=p["cagr"], vol=p["vol"], sharpe=p["sharpe"], maxdd=p["maxdd"],
                   is_sharpe=sp["is_sharpe"], oos_sharpe=sp["oos_sharpe"], worst_month=((1 + v).resample("ME").prod() - 1).min()))
CR = pd.DataFrame(cr)
CR.to_csv(os.path.join(RES, "combos.csv"), index=False)
print(CR.round(3).to_string())

# stress windows
win = {"2018-02 Volmageddon": ("2018-01-26", "2018-02-09"), "2018-Q4 selloff": ("2018-10-01", "2018-12-24"),
       "2020-02/03 Covid": ("2020-02-19", "2020-03-23"), "2021-01 meme squeeze": ("2021-01-19", "2021-02-05"),
       "2022 bear": ("2022-01-03", "2022-10-12"), "2024-08 yen carry": ("2024-07-31", "2024-08-07"),
       "2025-04 tariff shock": ("2025-04-02", "2025-04-08")}
st = []
for k, (a, b) in win.items():
    x = C[(C.index >= a) & (C.index <= b)]
    st.append({"window": k, **((1 + x).prod() - 1).round(4).to_dict()})
ST = pd.DataFrame(st)
ST.to_csv(os.path.join(RES, "stress_windows.csv"), index=False)
print(ST.to_string())
BY = pd.DataFrame({k: by_year(C[k]) for k in C.columns})
BY.to_csv(os.path.join(RES, "candidates_by_year.csv"))
print(BY.round(3).to_string())

# ---------------------------------------------------------------- charts
plt.rcParams.update({"figure.dpi": 130, "axes.spines.top": False, "axes.spines.right": False, "font.size": 9})
colors = {"noise_QQQ": "#1f77b4", "trend_xasset_lf": "#2ca02c", "overnight_IWM": "#9467bd", "mom121_LS": "#8c564b",
          "shortbot_P7": "#d62728", "SPY": "#7f7f7f"}
fig, ax = plt.subplots(figsize=(9, 4.5))
for k in C.columns:
    ax.plot((1 + C[k]).cumprod(), label=k, color=colors.get(k), lw=1.6 if k in ("noise_QQQ", "trend_xasset_lf") else 1)
ax.axvline(pd.Timestamp("2022-01-01"), color="k", ls="--", lw=0.8); ax.text(pd.Timestamp("2022-01-15"), ax.get_ylim()[1] * 0.95, "OOS", fontsize=8)
ax.set_yscale("log"); ax.set_title("Candidate sleeves vs the shorting bot (P7) and SPY, growth of $1 (log)")
ax.legend(fontsize=7, ncol=3); fig.tight_layout(); fig.savefig(os.path.join(OUT, "candidates_equity.png")); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 5))
cm = M.corr()
im = ax.imshow(cm, vmin=-1, vmax=1, cmap="RdBu_r")
ax.set_xticks(range(len(cm))); ax.set_xticklabels(cm.columns, rotation=45, ha="right"); ax.set_yticks(range(len(cm))); ax.set_yticklabels(cm.index)
for i in range(len(cm)):
    for j in range(len(cm)):
        ax.text(j, i, f"{cm.iloc[i, j]:.2f}", ha="center", va="center", fontsize=7)
ax.set_title("Monthly return correlation"); fig.colorbar(im, ax=ax, shrink=0.7); fig.tight_layout()
fig.savefig(os.path.join(OUT, "corr_monthly.png")); plt.close(fig)

fig, ax = plt.subplots(figsize=(8, 4))
for k in ["shortbot_only", "shortbot+noise+trend (1/3 each)", "overlay: shortbot + noise + trend on full capital each"]:
    ax.plot((1 + combos[k][combos[k].index >= "2016-09-01"]).cumprod(), label=k)
ax.set_title("Adding the two sleeves to the shorting bot"); ax.legend(fontsize=7); fig.tight_layout()
fig.savefig(os.path.join(OUT, "combo_equity.png")); plt.close(fig)

# DSR scatter
fig, ax = plt.subplots(figsize=(7, 4.5))
for f, g in D.groupby("family"):
    ax.scatter(g.sharpe_win, g.oos_sharpe, s=12, label=f)
for k, v in CANDS.items():
    r = D[D.name == v].iloc[0]; ax.annotate(k, (r.sharpe_win, r.oos_sharpe), fontsize=7)
ax.axhline(0, color="k", lw=0.5); ax.axvline(D.sr0_ann.iloc[0], color="r", ls="--", lw=0.8)
ax.text(D.sr0_ann.iloc[0] + 0.02, ax.get_ylim()[0] + 0.1, "expected max SR under null", color="r", fontsize=7)
ax.set_xlabel("Sharpe 2017-02..2026-10 (common window)"); ax.set_ylabel("OOS Sharpe 2022-2026")
ax.set_title(f"All {N} trials"); ax.legend(fontsize=6, ncol=2); fig.tight_layout()
fig.savefig(os.path.join(OUT, "trials_scatter.png")); plt.close(fig)

# family-level DSR (selection within the family the candidate came from)
FAMS = {"noise_QQQ": ("F5b_NOISE_voltgt_QQQ_fillnext", ["F5_intraday", "F5_noise_nbhd", "F5_noise_xs"]),
        "trend_xasset_lf": ("F3_TS_xasset_lf_blend", ["F3_trend", "F3_trend_nbhd"]),
        "overnight_IWM": ("F2a_ON_IWM_c0.5", ["F2_overnight"]),
        "mom121_LS": ("F7_MOM121_M_LS", ["F7_52wh"])}
fr = []
Di = D.set_index("name")
for k, (nm, fams) in FAMS.items():
    cols = [c for c in TT.columns if fam.get(c) in fams]
    v = sr[cols].var(); n = len(cols); x = TT[nm]
    d, s0 = deflated_sharpe(sr[nm], len(x), stats.skew(x), stats.kurtosis(x, fisher=False), v, n)
    oos = x[x.index >= "2022-01-01"]; so = oos.mean() / oos.std()
    d2, _ = deflated_sharpe(so, len(oos), stats.skew(oos), stats.kurtosis(oos, fisher=False), v, n)
    fr.append(dict(candidate=k, trial=nm, family_trials=n, sr0_ann=s0 * np.sqrt(252), sharpe_win=sr[nm] * np.sqrt(252),
                   dsr_family_win=d, dsr_family_oos=d2, dsr_all=Di.loc[nm, "dsr_win"], dsr_all_oos=Di.loc[nm, "dsr_oos"],
                   psr_oos_vs0=Di.loc[nm, "psr_oos_vs0"]))
FR = pd.DataFrame(fr); FR.to_csv(os.path.join(RES, "dsr_candidates.csv"), index=False)
print(FR.round(3).to_string())
