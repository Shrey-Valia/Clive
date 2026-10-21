"""Terminal-styled research charts -> charts/out/*.png

* <TICKER>_technical.png  price + SMA50/200 + Bollinger(20,2), RSI(2), ATR(14) % of price
* SPY_vix_regime.png      SPY with VIX/VIX3M backwardation shading, VIX, term-structure ratio
* SPY_vrp.png             VIX vs subsequent 21d realized vol of SPY, and the spread
* signal_bars_SPY.png     SPY mean forward return by signal, 4 horizons (vs baseline)
* signal_excess_heatmap_21d.png  excess 21d forward return, ticker x signal
* signal_fwd_vol.png      forward realized-vol ratio by signal (avg across tickers)

Run after ``charts/signal_study.py`` (the bar charts read signal_study.csv):
    python3 charts/make_charts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from fetch_bars import TICKERS, load_bars  # noqa: E402
from signal_study import build_features, vix_frame  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

BG, AMBER, WHITE, GREEN, RED = "#000000", "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d"
GREY, DIM, CYAN = "#8a8a8a", "#2a2a2a", "#5ec8ff"
LOOKBACK = 378  # ~18 months on the per-ticker technical charts

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "axes.edgecolor": "#444444", "axes.labelcolor": WHITE, "axes.titlecolor": AMBER,
    "text.color": WHITE, "xtick.color": GREY, "ytick.color": GREY,
    "grid.color": DIM, "grid.linewidth": 0.6, "axes.grid": True,
    "font.family": "monospace", "font.monospace": ["Menlo", "DejaVu Sans Mono", "Courier New"],
    "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "legend.facecolor": BG, "legend.edgecolor": "#444444", "legend.fontsize": 8, "legend.framealpha": 0.9,
    "lines.linewidth": 1.2, "axes.spines.top": False, "axes.spines.right": False,
})


def _header(fig, title: str, sub: str = ""):
    fig.text(0.012, 0.985, title, color=AMBER, fontsize=13, fontweight="bold", va="top")
    if sub:
        fig.text(0.012, 0.955, sub, color=GREY, fontsize=8.5, va="top")


def _datefmt(ax):
    loc = mdates.AutoDateLocator(minticks=5, maxticks=10)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))


def _shade(ax, mask: pd.Series, color: str, alpha: float):
    """Shade contiguous True runs of a boolean series as vertical spans."""
    m = mask.fillna(False).astype(bool)
    if not m.any():
        return
    grp = (m != m.shift()).cumsum()
    idx = m.index
    for _, run in m[m].groupby(grp[m]):
        a = run.index[0]
        pos = idx.get_loc(run.index[-1])
        b = idx[min(pos + 1, len(idx) - 1)]
        ax.axvspan(a, b, color=color, alpha=alpha, lw=0)


# --------------------------------------------------------------------------
def technical_chart(sym: str, f: pd.DataFrame):
    d = f.iloc[-LOOKBACK:]
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(13, 8.5), sharex=True,
                                     gridspec_kw={"height_ratios": [3.2, 1.1, 1.1], "hspace": 0.08})
    last = d.iloc[-1]
    chg = d["close"].iloc[-1] / d["close"].iloc[-22] - 1
    trend = "ABOVE" if last.close > last.sma200 else "BELOW"
    _header(fig, f"{sym}  {last.close:,.2f}",
            f"{d.index[0].date()} -> {d.index[-1].date()}  |  1M {chg:+.1%}  |  {trend} 200DMA  |  "
            f"RSI2 {last.rsi2:.0f}  |  ATR14 {last.atr14:.2f} ({last.atr_pct:.1%})  |  RV21 {last.rv21:.0%}  |  VIX {last.vix:.1f}")

    a1.fill_between(d.index, d["bb_lo"], d["bb_up"], color=CYAN, alpha=0.08, lw=0, label="Bollinger 20,2")
    a1.plot(d.index, d["bb_up"], color=CYAN, lw=0.6, alpha=0.6)
    a1.plot(d.index, d["bb_lo"], color=CYAN, lw=0.6, alpha=0.6)
    a1.plot(d.index, d["close"], color=WHITE, lw=1.3, label="Close")
    a1.plot(d.index, d["sma50"], color=AMBER, lw=1.1, label="SMA 50")
    a1.plot(d.index, d["sma200"], color="#c77dff", lw=1.1, label="SMA 200")
    sq = d["squeeze"].astype(bool)
    a1.scatter(d.index[sq], d["bb_lo"][sq] * 0.985, marker="s", s=6, color=AMBER, alpha=0.7, label="BB squeeze", zorder=3)
    a1.set_ylabel("price")
    a1.legend(loc="upper left", ncol=5)

    a2.plot(d.index, d["rsi2"], color=AMBER, lw=0.9)
    a2.axhline(90, color=RED, lw=0.8, ls="--")
    a2.axhline(10, color=GREEN, lw=0.8, ls="--")
    a2.fill_between(d.index, 0, d["rsi2"], where=d["rsi2"] < 10, color=GREEN, alpha=0.35, lw=0)
    a2.fill_between(d.index, d["rsi2"], 100, where=d["rsi2"] > 90, color=RED, alpha=0.35, lw=0)
    a2.set_ylim(0, 100)
    a2.set_yticks([10, 50, 90])
    a2.set_ylabel("RSI(2)")

    a3.plot(d.index, d["atr_pct"] * 100, color=WHITE, lw=1.0)
    a3.fill_between(d.index, 0, d["atr_pct"] * 100, color=WHITE, alpha=0.08, lw=0)
    a3.set_ylabel("ATR14 %")
    a3.set_ylim(bottom=0)
    _datefmt(a3)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.92, bottom=0.05)
    fig.savefig(OUT / f"{sym}_technical.png", dpi=140)
    plt.close(fig)


def vix_regime_chart(f: pd.DataFrame):
    d = f.dropna(subset=["ts_ratio"])
    back = d["ts_ratio"] > 1.0
    deep = d["ts_ratio"] < 0.9
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(13, 9), sharex=True,
                                     gridspec_kw={"height_ratios": [2.6, 1.3, 1.3], "hspace": 0.08})
    last = d.iloc[-1]
    _header(fig, "SPY vs VIX term-structure regime",
            f"{d.index[0].date()} -> {d.index[-1].date()}  |  red = backwardation (VIX/VIX3M > 1, {back.mean():.0%} of days)  |  "
            f"green = steep contango (< 0.9, {deep.mean():.0%})  |  now VIX {last.vix:.1f}, VIX3M {last.vix3m:.1f}, ratio {last.ts_ratio:.2f}")
    for ax in (a1, a2, a3):
        _shade(ax, back, RED, 0.22)
        _shade(ax, deep, GREEN, 0.045)
    a1.plot(d.index, d["close"], color=WHITE, lw=1.1, label="SPY (adj.)")
    a1.plot(d.index, d["sma200"], color=AMBER, lw=1.0, label="SMA 200")
    a1.set_yscale("log")
    lo, hi = d["close"].min(), d["close"].max()
    ticks = [t for t in (150, 200, 250, 300, 400, 500, 600, 700, 800, 1000) if lo * 0.9 <= t <= hi * 1.1]
    a1.set_yticks(ticks)
    a1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    a1.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    a1.set_ylabel("SPY (log)")
    a1.legend(loc="upper left")

    a2.plot(d.index, d["vix"], color=AMBER, lw=0.9, label="VIX")
    a2.plot(d.index, d["vix3m"], color=GREY, lw=0.9, label="VIX3M")
    for y in (15, 20, 30):
        a2.axhline(y, color="#555", lw=0.6, ls=":")
    a2.set_ylabel("index level")
    a2.legend(loc="upper left", ncol=2)

    a3.plot(d.index, d["ts_ratio"], color=WHITE, lw=0.8)
    a3.axhline(1.0, color=RED, lw=0.9, ls="--")
    a3.axhline(0.9, color=GREEN, lw=0.9, ls="--")
    box = dict(facecolor=BG, edgecolor="none", pad=1.5)
    a3.text(1.0, 1.0, "1.00 backwardation ", color=RED, fontsize=7.5, va="bottom", ha="right",
            transform=a3.get_yaxis_transform(), bbox=box)
    a3.text(1.0, 0.9, "0.90 steep contango ", color=GREEN, fontsize=7.5, va="top", ha="right",
            transform=a3.get_yaxis_transform(), bbox=box)
    a3.set_ylabel("VIX / VIX3M")
    _datefmt(a3)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.92, bottom=0.05)
    fig.savefig(OUT / "SPY_vix_regime.png", dpi=140)
    plt.close(fig)


def vrp_chart(f: pd.DataFrame):
    d = f.dropna(subset=["vix", "fwd_rv21"]).copy()
    d["vrp"] = d["vix"] - d["fwd_rv21"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1.3], "hspace": 0.08})
    _header(fig, "SPY volatility risk premium: VIX vs realized vol over the next 21 days",
            f"{d.index[0].date()} -> {d.index[-1].date()}  |  mean VIX {d.vix.mean():.1f} vs realized {d.fwd_rv21.mean():.1f}  |  "
            f"mean spread {d.vrp.mean():+.2f} pts, median {d.vrp.median():+.2f}  |  VIX > realized on {(d.vrp > 0).mean():.0%} of days")
    a1.plot(d.index, d["vix"], color=AMBER, lw=0.9, label="VIX at t (implied 30d)")
    a1.plot(d.index, d["fwd_rv21"], color=WHITE, lw=0.9, alpha=0.85, label="SPY realized vol, t -> t+21 (ann. %)")
    a1.set_ylabel("vol points")
    a1.set_yscale("log")
    a1.set_yticks([5, 10, 20, 40, 80])
    a1.yaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
    a1.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    a1.legend(loc="upper left")

    v = d["vrp"].clip(-25, 25)
    a2.fill_between(d.index, 0, v, where=v >= 0, color=GREEN, alpha=0.75, lw=0, label="VIX > realized (premium)")
    a2.fill_between(d.index, 0, v, where=v < 0, color=RED, alpha=0.85, lw=0, label="realized > VIX (short-vol loss)")
    a2.plot(d.index, d["vrp"].rolling(63).mean(), color=AMBER, lw=1.1, label="63d average")
    a2.axhline(0, color=GREY, lw=0.6)
    a2.set_ylim(-26, 34)
    a2.set_ylabel("VIX - fwd RV")
    worst = d["vrp"].idxmin()
    a2.annotate(f"{d.vrp.min():+.0f} pts ({worst.date()})\naxis clipped at -25", xy=(worst, -25),
                xytext=(10, 18), textcoords="offset points", color=RED, fontsize=7.5)
    a2.legend(loc="upper left", ncol=3)
    _datefmt(a2)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.92, bottom=0.05)
    fig.savefig(OUT / "SPY_vrp.png", dpi=140)
    plt.close(fig)


# --------------------------------------------------------------------------
ORDER = ["RSI2<10", "RSI2>90", "RSI2<10 & >200DMA", "RSI2<10 & <200DMA", "Above 200DMA", "Below 200DMA",
         "IBS<0.2", "IBS>0.8", "BB squeeze", "Turn of month", "VIX/VIX3M<0.9", "VIX/VIX3M 0.9-1.0",
         "VIX/VIX3M>1", "VIX<15", "VIX 15-20", "VIX 20-25", "VIX 25-30", "VIX>30"]


def signal_bars(res: pd.DataFrame, sym: str = "SPY"):
    r = res[res.ticker == sym]
    fig, axes = plt.subplots(1, 4, figsize=(15, 8), sharey=True, gridspec_kw={"wspace": 0.08})
    _header(fig, f"{sym} mean forward return by signal",
            f"{r.start.iloc[0]} -> {r.end.iloc[0]}  |  bar = mean fwd return on signal days; amber line = all-days baseline  |  "
            "label: t-stat of excess vs other days (Newey-West)  |  * < 30 episodes")
    y = np.arange(len(ORDER))
    for ax, h in zip(axes, [5, 10, 21, 30]):
        s = r[r.horizon == h].set_index("signal")
        base = s.loc["ALL (baseline)", "mean"] * 100
        vals = s.loc[ORDER, "mean"] * 100
        cols = [GREEN if v >= base else RED for v in vals]
        ax.barh(y, vals, color=cols, height=0.62, alpha=0.9)
        ax.axvline(base, color=AMBER, lw=1.2)
        ax.axvline(0, color=GREY, lw=0.6)
        span = max(abs(vals).max(), abs(base)) * 1.45
        ax.set_xlim(min(-0.15 * span, vals.min() * 1.4), span)
        for yi, sig in zip(y, ORDER):
            t, ep = s.loc[sig, "t_nw"], s.loc[sig, "episodes"]
            lab = f"t{t:+.1f}{'*' if ep < 30 else ''}"
            ax.text(span * 0.98, yi, lab, va="center", ha="right", fontsize=7.5,
                    color=WHITE if abs(t) >= 2 else GREY, fontweight="bold" if abs(t) >= 2 else "normal")
        ax.set_title(f"{h}-day  (baseline {base:+.2f}%)", fontsize=9.5)
        ax.set_xlabel("mean fwd return, %")
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(ORDER, color=WHITE)
    axes[0].invert_yaxis()
    fig.subplots_adjust(left=0.13, right=0.99, top=0.88, bottom=0.07)
    fig.savefig(OUT / f"signal_bars_{sym}.png", dpi=140)
    plt.close(fig)


def signal_heatmap(res: pd.DataFrame, h: int = 21):
    r = res[(res.horizon == h) & (res.signal != "ALL (baseline)")]
    m = r.pivot(index="signal", columns="ticker", values="excess").loc[ORDER, TICKERS] * 100
    t = r.pivot(index="signal", columns="ticker", values="t_nw").loc[ORDER, TICKERS]
    ep = r.pivot(index="signal", columns="ticker", values="episodes").loc[ORDER, TICKERS]
    m["AVG"] = m.mean(axis=1)
    lim = np.nanpercentile(np.abs(m.values), 95)
    cmap = LinearSegmentedColormap.from_list("rg", [RED, "#3a1414", "#1a1a1a", "#123a24", GREEN])
    fig, ax = plt.subplots(figsize=(13, 8.5))
    _header(fig, f"Excess {h}-day forward return by signal (signal days minus other days)",
            "cell = excess return %, amber box = |Newey-West t| >= 2, * = < 30 episodes  |  AVG = equal-weight mean across tickers  |  2016-01 -> 2026-10")
    ax.imshow(m.values, cmap=cmap, vmin=-lim, vmax=lim, aspect="auto")
    ax.grid(False)
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            v = m.iat[i, j]
            if np.isnan(v):
                continue
            if j < len(TICKERS):
                tt, e = t.iat[i, j], ep.iat[i, j]
                lab = f"{v:+.1f}{'*' if e < 30 else ''}"
                bold = abs(tt) >= 2
            else:
                lab, bold = f"{v:+.1f}", False
            ax.text(j, i, lab, ha="center", va="center", fontsize=8, color=WHITE)
            if bold:
                ax.add_patch(plt.Rectangle((j - 0.47, i - 0.44), 0.94, 0.88, fill=False,
                                           edgecolor=AMBER, lw=1.3))
    ax.axvline(len(TICKERS) - 0.5, color=AMBER, lw=1.2)
    ax.set_xticks(range(m.shape[1]))
    ax.set_xticklabels(m.columns, color=WHITE)
    ax.set_yticks(range(m.shape[0]))
    ax.set_yticklabels(m.index, color=WHITE)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.subplots_adjust(left=0.14, right=0.99, top=0.9, bottom=0.04)
    fig.savefig(OUT / f"signal_excess_heatmap_{h}d.png", dpi=140)
    plt.close(fig)


def signal_vol(res: pd.DataFrame):
    r = res[(res.horizon == 21) & (res.signal != "ALL (baseline)")]
    g = r.groupby("signal")["rv_ratio"].agg(["mean", "min", "max"]).loc[ORDER]
    g = g.sort_values("mean")
    fig, ax = plt.subplots(figsize=(12, 7.5))
    _header(fig, "Forward 21-day realized vol after each signal, relative to other days",
            "bar = equal-weight average ratio across 9 tickers; whisker = min-max across tickers  |  < 1 = calmer ahead, > 1 = more volatile ahead")
    y = np.arange(len(g))
    cols = [GREEN if v < 1 else RED for v in g["mean"]]
    ax.barh(y, g["mean"] - 1, left=1, color=cols, height=0.6, alpha=0.9)
    ax.errorbar(g["mean"], y, xerr=[g["mean"] - g["min"], g["max"] - g["mean"]], fmt="none",
                ecolor=GREY, elinewidth=0.8, capsize=2.5)
    ax.axvline(1, color=AMBER, lw=1.1)
    for yi, v in zip(y, g["mean"]):
        ax.text(max(g["max"].iloc[yi], 1.0) + 0.03, yi, f"{v:.2f}x", va="center", fontsize=8, color=WHITE)
    ax.set_axisbelow(True)
    ax.set_yticks(y)
    ax.set_yticklabels(g.index, color=WHITE)
    ax.set_xlabel("forward RV21 on signal days / forward RV21 on other days")
    ax.grid(axis="y", visible=False)
    fig.subplots_adjust(left=0.17, right=0.97, top=0.88, bottom=0.08)
    fig.savefig(OUT / "signal_fwd_vol.png", dpi=140)
    plt.close(fig)


def main():
    bars = load_bars(TICKERS)
    vix = vix_frame()
    feats = {s: build_features(bars[s], vix) for s in TICKERS if s in bars}
    for s, f in feats.items():
        technical_chart(s, f)
    vix_regime_chart(feats["SPY"])
    vrp_chart(feats["SPY"])
    res = pd.read_csv(HERE / "signal_study.csv")
    signal_bars(res, "SPY")
    signal_bars(res, "QQQ")
    signal_heatmap(res, 21)
    signal_heatmap(res, 5)
    signal_vol(res)
    for p in sorted(OUT.glob("*.png")):
        print(p)


if __name__ == "__main__":
    main()
