"""Terminal-styled charts: black background, amber / white / green / red, monospace."""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import OOS_START, OUT  # noqa: E402

AMBER, WHITE, GREEN, RED, GREY = "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#555555"
CYC = [AMBER, GREEN, "#6fa8ff", "#c792ea", "#ffd27f", "#9be7c4", "#ff9e9e"]
plt.rcParams.update({
    "figure.facecolor": "black", "axes.facecolor": "black", "savefig.facecolor": "black",
    "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "xtick.color": WHITE, "ytick.color": WHITE,
    "text.color": WHITE, "font.family": "monospace", "font.size": 9, "axes.grid": True,
    "grid.color": "#222222", "axes.titlecolor": AMBER, "axes.titleweight": "bold",
    "legend.facecolor": "black", "legend.edgecolor": GREY, "legend.labelcolor": WHITE,
})


def plots(SERIES, finals, yr, dec, stress, comp, wf_series):
    # 1. equity curves vs v2 and SPY
    fig, ax = plt.subplots(figsize=(13, 6))
    for i, n in enumerate([f for f in finals if f in SERIES][:7]):
        e = (1 + SERIES[n]).cumprod()
        ax.plot(pd.to_datetime(e.index), e.values, color=CYC[i % len(CYC)], lw=1.1, label=n)
    for n, col, lw in (("v2_short", WHITE, 1.8), ("SPY", RED, 1.3)):
        e = (1 + SERIES[n]).cumprod()
        ax.plot(pd.to_datetime(e.index), e.values, color=col, lw=lw, label=n)
    ax.axvline(pd.Timestamp(OOS_START), color=GREY, ls=":")
    ax.text(pd.Timestamp(OOS_START), 1.0, "  OOS ->", color=GREY)
    ax.set_yscale("log"); ax.set_title("EQUITY, NET OF COSTS (log, $1 start) - finalists vs v2 short and SPY")
    ax.legend(loc="upper left", fontsize=7.5, ncol=2)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "equity_curves.png"), dpi=130); plt.close(fig)

    # 2. overnight / intraday decomposition by price bucket
    d = dec[dec["sample"] == "all_top20"]
    bks = ["$1-3", "$3-5", "$5-10", "$10+"]
    fig, axs = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax, per in zip(axs, ("IS", "OOS")):
        x = d[d.period == per].set_index("bucket").reindex(bks)
        i = np.arange(len(bks)); w = 0.27
        ax.bar(i - w, 100 * x.overnight_long_mean, w, color=GREEN, label="overnight LONG 15:55->open")
        ax.bar(i, 100 * x.intraday_short_mean, w, color=AMBER, label="next-day SHORT open->close")
        ax.bar(i + w, 100 * x.roundtrip_cost_est, w, color=RED, label="est. round-trip cost ($10k order)")
        for j, n in enumerate(x.n.fillna(0).astype(int)):
            ax.text(j, ax.get_ylim()[0] if False else 0, f" n={n}", color=GREY, rotation=90, va="bottom", fontsize=7)
        ax.set_xticks(i, bks); ax.axhline(0, color=WHITE, lw=0.6)
        ax.set_title(f"MEAN GROSS LEG RETURN % - top-20 gainers, {per}")
    axs[0].legend(fontsize=7.5, loc="upper left")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "decomposition_by_bucket.png"), dpi=130); plt.close(fig)

    # 3. by-year bars
    cols = [c for c in yr.columns if c in finals][:5] + ["v2_short", "SPY"]
    fig, ax = plt.subplots(figsize=(13, 5))
    i = np.arange(len(yr)); w = 0.8 / len(cols)
    for k, c in enumerate(cols):
        col = WHITE if c == "v2_short" else RED if c == "SPY" else CYC[k % len(CYC)]
        ax.bar(i + (k - len(cols) / 2) * w + w / 2, 100 * yr[c].values, w, color=col, label=c)
    ax.set_xticks(i, yr.index); ax.axhline(0, color=WHITE, lw=0.6)
    ax.set_ylim(max(-100, ax.get_ylim()[0]), min(150, ax.get_ylim()[1]))
    ax.set_title("RETURN BY YEAR % (net)"); ax.legend(fontsize=7.5, ncol=4)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "by_year.png"), dpi=130); plt.close(fig)

    # 4. cost / borrow sensitivity
    if len(stress):
        names = list(dict.fromkeys(stress.name))
        vs = ["base", "cost_x2", "impact_x2", "borrow_x2", "locate_htb100_50bps", "locate_htb200_100bps",
              "equity_10k", "equity_1m", "equity_10m", "acct_10k_margin_pdt"]
        fig, ax = plt.subplots(figsize=(13, 5.5))
        i = np.arange(len(names)); w = 0.8 / len(vs)
        pal = [WHITE, RED, "#ff9e9e", AMBER, "#ffd27f", "#c76a00", GREEN, "#6fa8ff", "#3a5bbf", "#c792ea"]
        for k, v in enumerate(vs):
            y = [stress[(stress.name == n) & (stress.variant == v)].oos_sharpe.mean() for n in names]
            ax.bar(i + (k - len(vs) / 2) * w + w / 2, y, w, color=pal[k], label=v)
        ax.set_xticks(i, names, rotation=20, ha="right", fontsize=7.5); ax.axhline(0, color=WHITE, lw=0.6)
        ax.set_title("OOS SHARPE UNDER COST / BORROW / CAPACITY STRESS (2022-2026)")
        ax.legend(fontsize=7, ncol=5)
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "cost_borrow_sensitivity.png"), dpi=130); plt.close(fig)
