"""Terminal-styled charts from study.py outputs -> longhist/out/*.png"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
BG, AMB, WH, GR, RD, BL, GY = "#000000", "#ffa028", "#e8e8e8", "#3ddc84", "#ff4d4d", "#5ab4ff", "#8a8a8a"
plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": WH, "axes.labelcolor": WH, "xtick.color": WH, "ytick.color": WH,
    "axes.edgecolor": "#444444", "grid.color": "#222222", "grid.linewidth": 0.6,
    "font.family": "monospace", "font.size": 10, "axes.titlecolor": AMB,
    "axes.titleweight": "bold", "axes.titlesize": 12, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False,
})

R = pd.read_pickle(OUT / "rets.pkl")
C = pd.read_pickle(OUT / "cash.pkl")

SERIES = [  # (column, label, color, linestyle)
    ("PUT|M|Baseline", "PUT baseline", WH, "-"),
    ("PUT|M|Exit TS>x [1.2]", "PUT + exit VIX/VIX3M>1.2", AMB, "-"),
    ("PUT|M|Stop -x since roll [0.08]", "PUT + stop -8% since roll", GR, "-"),
    ("PUT|M|VIX/VIX3M<x [1.0]", "PUT gated VIX/VIX3M<1.0 @roll", BL, "--"),
    ("PUT|M|SPX>SMAx [200]", "PUT gated SPX>200DMA @roll", RD, "--"),
    ("SPX|-|SPX +1.8% div (approx TR)", "SPX (+1.8%/yr div approx)", GY, ":"),
]


def shade_oos(ax):
    ax.axvspan(pd.Timestamp("2017-01-01"), R.index[-1], color="#1a1206", zorder=0)
    ax.text(pd.Timestamp("2017-03-01"), 0.98, "OUT-OF-SAMPLE 2017-2026", transform=ax.get_xaxis_transform(),
            color=AMB, fontsize=8, va="top")


def endlabel(ax, x, y, txt, col):
    ax.annotate(txt, (x, y), xytext=(4, 0), textcoords="offset points", color=col, fontsize=8, va="center")


def equity():
    fig, ax = plt.subplots(figsize=(12, 6.5))
    shade_oos(ax)
    ends = []
    for col, lab, c, ls in SERIES:
        eq = (1 + R[col].fillna(0)).cumprod()
        ax.plot(eq.index, eq.values, color=c, ls=ls, lw=1.8 if c in (AMB, WH) else 1.4, label=lab)
        ends.append((eq.iloc[-1], f"{eq.iloc[-1]:.2f}x", c))
    ends.sort()
    last = -1
    for v, t, c in ends:  # simple de-collision in log space
        y = v if np.log(v) - last > 0.06 else np.exp(last + 0.06)
        endlabel(ax, R.index[-1], y, t, c)
        last = np.log(y)
    ax.set_yscale("log")
    ax.set_title("GROWTH OF $1, 2007-01 .. 2026-10 (log)  |  monthly-roll PUT index, T-bills when out")
    ax.grid(True, which="both")
    ax.legend(loc="upper left", fontsize=8.5)
    ax.set_ylabel("equity (log)")
    fig.tight_layout()
    fig.savefig(OUT / "equity_log.png", dpi=140)
    plt.close(fig)


def underwater():
    cols = SERIES[:5]
    fig, axes = plt.subplots(len(cols), 1, figsize=(12, 9), sharex=True, sharey=True)
    for ax, (col, lab, c, ls) in zip(axes, cols):
        eq = (1 + R[col].fillna(0)).cumprod()
        dd = eq / eq.cummax() - 1
        ax.fill_between(dd.index, dd.values * 100, 0, color=c, alpha=0.35, lw=0)
        ax.plot(dd.index, dd.values * 100, color=c, lw=1)
        ax.text(0.33, 0.12, f"{lab}   maxDD {dd.min()*100:.1f}%", transform=ax.transAxes, color=c, fontsize=9)
        ax.grid(True)
        ax.set_ylim(-40, 2)
    axes[0].set_title("UNDERWATER (% below prior peak)")
    axes[-1].set_xlabel("")
    fig.tight_layout()
    fig.savefig(OUT / "underwater.png", dpi=140)
    plt.close(fig)


def crash_bars():
    res = pd.read_csv(OUT / "all_results.csv")
    keys = [("PUT", "Baseline", "PUT baseline", WH),
            ("PUT", "VIX/VIX3M<x [1.0]", "gate TS<1.0 @roll", BL),
            ("PUT", "SPX>SMAx [200]", "gate SPX>200DMA @roll", RD),
            ("PUT", "Exit TS>x [1.2]", "exit TS>1.2 anytime", AMB),
            ("PUT", "Stop -x since roll [0.08]", "stop -8% since roll", GR),
            ("SPX", "SPX +1.8% div (approx TR)", "SPX approx TR", GY)]
    cc = [c for c in res.columns if c.startswith("crash_")]
    fig, ax = plt.subplots(figsize=(13, 6))
    n = len(keys)
    w = 0.8 / n
    x = np.arange(len(cc))
    for i, (a, k, lab, col) in enumerate(keys):
        row = res[(res.asset == a) & (res.key == k)].iloc[0]
        vals = row[cc].values.astype(float) * 100
        ax.bar(x + (i - n / 2 + 0.5) * w, vals, width=w * 0.9, color=col, label=lab)
    ax.axhline(0, color="#666666", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([c.replace("crash_", "") for c in cc], fontsize=9)
    ax.set_ylabel("window return %")
    ax.set_title("CRASH WINDOWS: total return in each stress window (incl. recovery legs in window)")
    ax.grid(True, axis="y")
    ax.legend(fontsize=8.5, ncol=3, loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT / "crash_windows.png", dpi=140)
    plt.close(fig)


def rolling_sharpe():
    fig, ax = plt.subplots(figsize=(12, 6))
    shade_oos(ax)
    for col, lab, c, ls in SERIES:
        x = R[col].fillna(0) - C.reindex(R.index).fillna(0)
        s = x.rolling(756).mean() / x.rolling(756).std() * np.sqrt(252)
        ax.plot(s.index, s.values, color=c, ls=ls, lw=1.6 if c in (AMB, WH) else 1.2, label=lab)
    ax.axhline(0, color="#666666", lw=0.8)
    ax.set_title("ROLLING 3-YEAR SHARPE (excess of T-bills)")
    ax.grid(True)
    ax.legend(fontsize=8.5, loc="lower left", ncol=2)
    fig.tight_layout()
    fig.savefig(OUT / "rolling_sharpe_3y.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    equity()
    underwater()
    crash_bars()
    rolling_sharpe()
    print("charts written to", OUT)
