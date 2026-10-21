"""Terminal-styled PNG charts (black bg, amber, white, green/red for P&L, monospace) into out/."""
import pickle
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pairs_lib as L

AMB, WHT, GRN, RED, CYN, PUR, GRY = "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#4fc3f7", "#c792ea", "#7a7a7a"
plt.rcParams.update({"figure.facecolor": "black", "axes.facecolor": "black", "savefig.facecolor": "black",
                     "text.color": WHT, "axes.labelcolor": WHT, "xtick.color": WHT, "ytick.color": WHT,
                     "axes.edgecolor": "#444444", "grid.color": "#222222", "font.family": "monospace",
                     "font.size": 10, "axes.titlecolor": AMB, "axes.titleweight": "bold", "legend.frameon": False,
                     "lines.linewidth": 1.6})
OUT = L.ROOT / "out"; OUT.mkdir(exist_ok=True)
S = pickle.load(open(L.CACHE / "run_state.pkl", "rb"))
A = pickle.load(open(L.CACHE / "analyze_state.pkl", "rb"))
store, fin, series = S["store"], A["finalists"], A["series"]
panel = L.load_panel(); C, O, dates = panel["close"], panel["open"], panel["dates"]
OOS = pd.Timestamp("2022-01-01")


def style(ax, title):
    ax.set_title(title, loc="left"); ax.grid(True, lw=0.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


# 1. equity curves by method vs SPY (log scale, growth of $1)
fig, ax = plt.subplots(figsize=(12, 6.5))
plot = [("ggr_canonical", "GGR distance (2s, 20 pairs)", AMB), ("coint_lit_canonical", "Cointegration (lit. spec)", CYN),
        ("kalman_best_IS", "Kalman (IS best)", GRN), ("al_canonical", "Avellaneda-Lee residual", PUR),
        ("etf_distinct_ggr", "ETF pairs GGR (distinct)", "#ffd27a"), ("SPY", "SPY buy & hold", WHT), ("reversal_5d", "5d reversal (bench)", GRY)]
ends = []
for key, lab, col in plot:
    r = series.get(key)
    if r is None:
        continue
    eq = (1 + r).cumprod()
    ax.plot(eq.index, eq.values, color=col, lw=2 if key != "SPY" else 1.4, ls="--" if key in ("SPY", "reversal_5d") else "-", label=lab)
    ends.append([eq.iloc[-1], f" {lab.split(' (')[0]} {eq.iloc[-1]:.2f}x", col, eq.index[-1]])
ends.sort(key=lambda e: e[0])
ly = [np.log(e[0]) for e in ends]
for i in range(1, len(ly)):
    ly[i] = max(ly[i], ly[i - 1] + 0.045)  # keep end labels apart (log space)
for (v, txt, col, d), y in zip(ends, ly):
    ax.annotate(txt, (d, np.exp(y)), color=col, fontsize=8, va="center")
ax.set_yscale("log"); ax.axvline(OOS, color=AMB, lw=0.8, ls=":"); ax.text(OOS, ax.get_ylim()[1] * 0.92, "  OUT-OF-SAMPLE ->", color=AMB, fontsize=9)
ax.set_ylabel("growth of $1 (log), net of default costs")
style(ax, "PAIRS METHODS VS SPY  |  next-open fills, committed capital")
ax.legend(loc="upper left", fontsize=8, labelcolor=WHT)
ax.set_xlim(dates[0], dates[-1] + pd.Timedelta(days=330))
fig.tight_layout(); fig.savefig(OUT / "equity_by_method.png", dpi=130); plt.close(fig)

# 2. spread / z-score for 3 example pairs (best converger, typical, and the worst blow-up)
k = fin["coint_lit_canonical"]
t = store[k]["tdf"].copy()
built = {(tr["a"], tr["b"], tr["es"]): tr for _, _, trs in store[k]["built"] for tr in trs}
pick = [("WORST BLOW-UP", t.nsmallest(1, "net").iloc[0]), ("BEST CONVERGENCE", t[t.reason == "converge"].nlargest(1, "net").iloc[0]),
        ("TYPICAL OOS TRADE", t[(pd.to_datetime(t.entry) >= OOS) & (t.reason == "converge")].sort_values("net").iloc[len(t[(pd.to_datetime(t.entry) >= OOS) & (t.reason == "converge")]) // 2])]
fig, axes = plt.subplots(3, 1, figsize=(12, 10))
for ax, (title, row) in zip(axes, pick):
    port = [b for b in store[k]["built"] if any(tr["a"] == row.a and tr["b"] == row.b for tr in b[2]) and b[0] == pd.Series(dates).searchsorted(pd.Timestamp(row.port))]
    ts_, te_, trs = port[0]
    trs = [tr for tr in trs if tr["a"] == row.a and tr["b"] == row.b]
    tr0 = trs[0]
    # recompute formation params for the plot
    f0, f1 = ts_ - 252, ts_ - 1
    la, lb = np.log(C[row.a].values), np.log(C[row.b].values)
    beta = tr0["beta"]
    e = la[f0:f1 + 1] - beta * lb[f0:f1 + 1]
    mu, sd = e.mean(), e.std()
    lo, hi = f0 + 150, min(te_ + 10, len(dates) - 1)
    z = (la[lo:hi] - beta * lb[lo:hi] - mu) / sd
    ax.plot(dates[lo:hi], z, color=WHT, lw=1.2, label="z-score of log spread")
    for lev, col in ((2, AMB), (-2, AMB), (0.5, GRY), (-0.5, GRY), (3.5, RED), (-3.5, RED)):
        ax.axhline(lev, color=col, lw=0.7, ls="--")
    ax.axvspan(dates[ts_], dates[te_], color="#151515")
    for tr in trs:
        i0 = tr["es"] + 1; i1 = min(tr.get("xs", te_) + 1, len(dates) - 1)
        if lo <= i0 < hi:
            ax.scatter(dates[i0], z[i0 - lo], marker="^" if tr["side"] > 0 else "v", color=GRN, s=70, zorder=5)
        if lo <= i1 < hi:
            ax.scatter(dates[i1], z[i1 - lo], marker="x", color=RED if tr.get("reason") in ("stop", "time_stop", "period_end", "delist") else AMB, s=70, zorder=5)
            ax.annotate(tr.get("reason", ""), (dates[i1], z[i1 - lo]), color=GRY, fontsize=7, xytext=(4, 6), textcoords="offset points")
    style(ax, f"{title}: {row.a}/{row.b}  beta={beta:.2f}  net {row.net:+.1%} of leg  ({row.entry} -> {row.exit}, {row.reason})")
    ax.set_ylabel("z")
axes[0].text(0.01, 0.04, "green ^/v = entry (long/short spread)  x = exit   amber = +/-2 entry   red = +/-3.5 stop   shaded = trading window",
             transform=axes[0].transAxes, fontsize=7, color=GRY)
fig.tight_layout(); fig.savefig(OUT / "spread_examples.png", dpi=130); plt.close(fig)

# 3. returns by year (grouped bars)
by = A["by"]
cols = [("ggr_canonical", "GGR", AMB), ("coint_lit_canonical", "Coint", CYN), ("kalman_best_IS", "Kalman", GRN),
        ("al_canonical", "A-L", PUR), ("etf_distinct_ggr", "ETF GGR", "#ffd27a"), ("SPY", "SPY", WHT)]
cols = [c for c in cols if c[0] in by.columns]
by = by.loc[2017:]
fig, ax = plt.subplots(figsize=(12, 6))
w = 0.8 / len(cols); x = np.arange(len(by))
for j, (key, lab, col) in enumerate(cols):
    ax.bar(x + j * w - 0.4 + w / 2, by[key].values * 100, width=w * 0.9, color=col, label=lab)
ax.axhline(0, color=GRY, lw=0.8)
ax.set_xticks(x); ax.set_xticklabels([str(y) + ("*" if y == by.index[-1] else "") for y in by.index])
ax.set_ylabel("calendar-year return, % (net)")
style(ax, "RETURNS BY YEAR  |  * = partial year (to 2026-10-20)  |  2022+ is out-of-sample")
ax.legend(ncol=len(cols), fontsize=8, loc="upper left", labelcolor=WHT)
fig.tight_layout(); fig.savefig(OUT / "returns_by_year.png", dpi=130); plt.close(fig)

# 4. cost sensitivity (OOS Sharpe vs cost multiple)
cs = A["cs"]
fig, axs = plt.subplots(1, 2, figsize=(12, 5.5), sharey=True)
palette = {"ggr_canonical": AMB, "coint_lit_canonical": CYN, "kalman_best_IS": GRN, "etf_ggr": "#8a6d3b", "etf_distinct_ggr": "#ffd27a", "classic_ggr": "#ff8a80",
           "al_canonical": PUR, "al_longonly_canonical": "#e0b0ff", "chen_canonical": GRY}
for ax, per in zip(axs, ("IS", "OOS")):
    for cfg, g in cs[cs.config != "etf_ggr"].groupby("config"):  # near-duplicate ETF book (-4 to -10 OOS) off-scale; see csv
        g = g.sort_values("cost_mult")
        ax.plot(g.cost_mult, g[f"{per}_sharpe"], marker="o", ms=5, color=palette.get(cfg, WHT), label=cfg)
    ax.axhline(0, color=RED, lw=0.8, ls="--"); ax.axvline(1, color=GRY, lw=0.6, ls=":")
    ax.set_xlabel("cost multiple (1x = 3-5bp/side, 0.5%/yr borrow)")
    style(ax, f"{per} SHARPE VS COSTS")
axs[0].set_ylabel("annualized Sharpe (net)")
axs[0].legend(fontsize=7, loc="lower left", labelcolor=WHT)
fig.tight_layout(); fig.savefig(OUT / "cost_sensitivity.png", dpi=130); plt.close(fig)
print("charts written")
