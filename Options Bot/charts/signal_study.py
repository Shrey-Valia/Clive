"""Signal study: forward returns / forward realized vol conditioned on simple signals.

For each ticker (max Alpaca history, 2016-01 onward) and each signal, reports
for forward horizons 5/10/21/30 trading days:

* n        signal days with a defined forward return
* episodes number of distinct signal runs (onsets) -- the honest sample size
* mean / median / hit (share > 0) of the forward return on signal days
* excess   mean(signal days) - mean(non-signal days)
* t_nw     Newey-West t-stat of ``excess`` (regression of fwd return on a
           signal dummy, HAC lag = horizon) -- corrects for overlapping windows
* fwd_rv21 mean forward 21d realized vol (annualized %) on signal days, and
           ``rv_ratio`` vs non-signal days

Small samples (< 30 episodes) are flagged ``*`` in the markdown.

Also computes the SPY volatility-risk-premium proxy: VIX_t minus the realized
21-day vol of SPY over (t, t+21].

Outputs: charts/signal_study.csv, charts/signal_study.md, charts/vrp_study.csv

Run:  python3 charts/signal_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from fetch_bars import TICKERS, load_bars, load_cboe  # noqa: E402
from optionsbot import indicators as ind  # noqa: E402

HORIZONS = [5, 10, 21, 30]
MIN_EPISODES = 30
VIX_BINS = [0, 15, 20, 25, 30, 100]
VIX_LABELS = ["VIX<15", "VIX 15-20", "VIX 20-25", "VIX 25-30", "VIX>30"]


# ----------------------------------------------------------------------------
def vix_frame() -> pd.DataFrame:
    vix = load_cboe("VIX")["close"].rename("vix")
    v3m = load_cboe("VIX3M")["close"].rename("vix3m")
    v9d = load_cboe("VIX9D")["close"].rename("vix9d")
    out = pd.concat([vix, v3m, v9d], axis=1)
    out["ts_ratio"] = out["vix"] / out["vix3m"]
    return out


def build_features(df: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """All indicators + signals + forward labels for one ticker."""
    f = df.copy()
    c = f["close"]
    f["sma50"] = ind.sma(c, 50)
    f["sma200"] = ind.sma(c, 200)
    bb = ind.bollinger(c, 20, 2)
    f[["bb_mid", "bb_up", "bb_lo", "pct_b", "bb_width"]] = bb.values
    f["rsi2"] = ind.rsi(c, 2)
    f["rsi14"] = ind.rsi(c, 14)
    f["atr14"] = ind.atr(f, 14)
    f["atr_pct"] = f["atr14"] / c
    f["ibs"] = ind.ibs(f)
    f["squeeze"] = ind.squeeze(f)
    f["rv21"] = ind.rv_close(c, 21)
    f["yz21"] = ind.rv_yang_zhang(f, 21)
    f["dd"] = ind.drawdown(c)
    f["streak"] = ind.streak(c)
    f["tom"] = ind.turn_of_month(f.index, before=1, after=3)
    f = f.join(vix, how="left")
    f[["vix", "vix3m", "vix9d", "ts_ratio"]] = f[["vix", "vix3m", "vix9d", "ts_ratio"]].ffill(limit=3)
    for h in HORIZONS:
        f[f"fwd{h}"] = ind.forward_return(c, h)
    f["fwd_rv21"] = ind.forward_realized_vol(c, 21) * 100
    # trim the incomplete final month so turn-of-month is not mis-flagged
    last_month = f.index[-1].to_period("M")
    f.loc[f.index.to_period("M") == last_month, "tom"] = False
    return f


def signals(f: pd.DataFrame) -> dict[str, pd.Series]:
    """Boolean signal masks (only where the signal is computable)."""
    has200 = f["sma200"].notna()
    has_ts = f["ts_ratio"].notna()
    has_v = f["vix"].notna()
    s = {
        "ALL (baseline)": pd.Series(True, index=f.index),
        "RSI2<10": f["rsi2"] < 10,
        "RSI2>90": f["rsi2"] > 90,
        "Above 200DMA": has200 & (f["close"] > f["sma200"]),
        "Below 200DMA": has200 & (f["close"] <= f["sma200"]),
        "RSI2<10 & >200DMA": (f["rsi2"] < 10) & has200 & (f["close"] > f["sma200"]),
        "RSI2<10 & <200DMA": (f["rsi2"] < 10) & has200 & (f["close"] <= f["sma200"]),
        "IBS<0.2": f["ibs"] < 0.2,
        "IBS>0.8": f["ibs"] > 0.8,
        "BB squeeze": f["squeeze"].astype(bool),
        "Turn of month": f["tom"].astype(bool),
        "VIX/VIX3M<0.9": has_ts & (f["ts_ratio"] < 0.9),
        "VIX/VIX3M 0.9-1.0": has_ts & (f["ts_ratio"] >= 0.9) & (f["ts_ratio"] <= 1.0),
        "VIX/VIX3M>1": has_ts & (f["ts_ratio"] > 1.0),
    }
    vb = pd.cut(f["vix"], VIX_BINS, labels=VIX_LABELS, right=False)
    for lab in VIX_LABELS:
        s[lab] = has_v & (vb == lab)
    return {k: v.fillna(False).astype(bool) for k, v in s.items()}


# ----------------------------------------------------------------------------
def nw_tstat(y: np.ndarray, d: np.ndarray, lag: int) -> float:
    """Newey-West t-stat of slope b in y = a + b*d (Bartlett kernel)."""
    X = np.column_stack([np.ones_like(d, dtype=float), d.astype(float)])
    n = len(y)
    if n < 10 or d.sum() < 2 or d.sum() > n - 2:
        return np.nan
    with np.errstate(all="ignore"):  # numpy+Accelerate emits spurious FP warnings in matmul
        return _nw_slope_t(X, y, lag)


def _nw_slope_t(X: np.ndarray, y: np.ndarray, lag: int) -> float:
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    u = y - X @ beta
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1)
        G = Xu[L:].T @ Xu[:-L]
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return float(beta[1] / np.sqrt(V[1, 1]))


def nw_tstat_mean(y: np.ndarray, lag: int) -> float:
    """Newey-West t-stat of the mean of a contiguous series."""
    n = len(y)
    if n < 10:
        return np.nan
    u = y - y.mean()
    s = (u * u).sum() / n            # elementwise sums: avoid spurious macOS BLAS FP warnings
    for L in range(1, lag + 1):
        s += 2 * (1 - L / (lag + 1)) * (u[L:] * u[:-L]).sum() / n
    return float(y.mean() / np.sqrt(s / n))


def episodes(mask: pd.Series) -> int:
    m = mask.astype(int)
    return int(((m == 1) & (m.shift(fill_value=0) == 0)).sum())


def study_ticker(sym: str, f: pd.DataFrame) -> list[dict]:
    rows = []
    sig = signals(f)
    for name, mask in sig.items():
        for h in HORIZONS:
            y = f[f"fwd{h}"]
            ok = y.notna()
            if name.startswith("VIX/VIX3M"):
                ok &= f["ts_ratio"].notna()
            elif name.startswith("VIX"):
                ok &= f["vix"].notna()
            elif "200DMA" in name:
                ok &= f["sma200"].notna()
            m = mask & ok
            yy = y[m]
            rest = y[ok & ~mask]
            if name == "ALL (baseline)":
                t = nw_tstat_mean(y[ok].values, h)
                excess = np.nan
            else:
                t = nw_tstat(y[ok].values, mask[ok].values, h)
                excess = yy.mean() - rest.mean() if len(yy) else np.nan
            rv = f.loc[m, "fwd_rv21"].mean()
            rv_rest = f.loc[ok & ~mask, "fwd_rv21"].mean() if name != "ALL (baseline)" else rv
            rows.append({
                "ticker": sym, "signal": name, "horizon": h,
                "n": int(m.sum()), "episodes": episodes(m),
                "mean": yy.mean(), "median": yy.median(), "hit": (yy > 0).mean() if len(yy) else np.nan,
                "excess": excess, "t_nw": t,
                "fwd_rv21": rv, "rv_ratio": rv / rv_rest if rv_rest else np.nan,
                "start": f.index[0].date(), "end": f.index[-1].date(),
            })
    return rows


def vrp_study(spy: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """VIX_t vs SPY realized vol over (t, t+21]."""
    d = spy[["close", "vix", "ts_ratio", "rv21", "fwd_rv21"]].dropna(subset=["vix", "fwd_rv21"]).copy()
    d["vrp"] = d["vix"] - d["fwd_rv21"]
    d["trail_spread"] = d["vix"] - d["rv21"] * 100
    summary = {
        "days": len(d), "start": d.index[0].date(), "end": d.index[-1].date(),
        "mean_vix": d["vix"].mean(), "mean_fwd_rv": d["fwd_rv21"].mean(),
        "mean_vrp": d["vrp"].mean(), "median_vrp": d["vrp"].median(),
        "pct_vrp_pos": (d["vrp"] > 0).mean(), "t_nw": nw_tstat_mean(d["vrp"].values, 21),
        "worst_vrp": d["vrp"].min(), "worst_date": d["vrp"].idxmin().date(),
        "p05_vrp": d["vrp"].quantile(0.05),
        "corr_trail_fwd": d[["trail_spread", "vrp"]].corr().iloc[0, 1],
    }
    vb = pd.cut(d["vix"], VIX_BINS, labels=VIX_LABELS, right=False)
    tb = pd.cut(d["ts_ratio"], [0, 0.9, 1.0, 10], labels=["TS<0.9", "TS 0.9-1.0", "TS>1"])
    sb = pd.cut(d["trail_spread"], [-100, 0, 3, 6, 100], labels=["VIX-RV<0", "VIX-RV 0-3", "VIX-RV 3-6", "VIX-RV>6"])
    parts = []
    for col, b in (("vix_bucket", vb), ("ts_bucket", tb), ("trail_spread_bucket", sb)):
        g = d.groupby(b, observed=True)["vrp"]
        t = g.agg(n="count", mean="mean", median="median", pct_pos=lambda x: (x > 0).mean(),
                  p05=lambda x: x.quantile(0.05))
        t.insert(0, "by", col)
        parts.append(t)
    return pd.concat(parts).reset_index(names="bucket"), summary


# ----------------------------------------------------------------------------
def fmt_pct(x, d=2):
    return "" if pd.isna(x) else f"{x * 100:+.{d}f}%"


def write_markdown(res: pd.DataFrame, vrp_tab: pd.DataFrame, vrp_sum: dict, feed: str) -> str:
    L = []
    L.append("# Signal study: conditional forward returns & volatility\n")
    L.append(f"Data: Alpaca daily bars (feed `{feed}`, split/dividend adjusted), "
             f"{res['start'].min()} to {res['end'].max()}; CBOE VIX/VIX3M/VIX9D closes. "
             "Generated by `charts/signal_study.py`.\n")
    L.append("Columns: `n` signal days, `ep` distinct episodes (signal onsets), `mean`/`med` forward return, "
             "`hit` share of positive forward returns, `excess` = mean on signal days minus mean on other days, "
             "`t_nw` Newey-West t-stat of the excess (HAC lag = horizon; baseline row: t-stat of the mean), "
             "`fwdRV` mean forward 21d realized vol of that ticker (ann. %), `RVx` ratio vs non-signal days. "
             f"`*` = fewer than {MIN_EPISODES} episodes (treat as anecdotal). |t| >= 2 bolded.\n")
    L.append("Caveat: overlapping horizons and 9 highly correlated tickers mean many tests are run on "
             "~10 years of one market regime; expect a few |t|>2 by chance. Consistency across tickers "
             "matters more than any single t-stat.\n")

    # --- consistency summary across tickers, 10d and 21d
    L.append("## Cross-ticker consistency (equal-weight average across 9 tickers)\n")
    for h in (5, 21):
        sub = res[(res.horizon == h) & (res.signal != "ALL (baseline)")]
        g = sub.groupby("signal", sort=False).agg(
            avg_excess=("excess", "mean"), pos=("excess", lambda x: (x > 0).sum()),
            t_sig=("t_nw", lambda x: (x.abs() >= 2).sum()), avg_hit=("hit", "mean"),
            avg_rv_ratio=("rv_ratio", "mean"), avg_ep=("episodes", "mean"))
        L.append(f"### {h}-day horizon\n")
        L.append("| signal | avg excess | tickers excess>0 | tickers abs(t)>=2 | avg hit | avg fwdRV ratio | avg episodes |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        for name, r in g.iterrows():
            L.append(f"| {name} | {fmt_pct(r.avg_excess)} | {int(r.pos)}/9 | {int(r.t_sig)}/9 | {r.avg_hit:.0%} | "
                     f"{r.avg_rv_ratio:.2f} | {r.avg_ep:.0f}{'*' if r.avg_ep < MIN_EPISODES else ''} |")
        L.append("")

    # --- VRP
    s = vrp_sum
    L.append("## SPY volatility risk premium proxy (VIX_t - realized vol over next 21 days)\n")
    L.append(f"{s['days']} days, {s['start']} to {s['end']}. Mean VIX {s['mean_vix']:.2f} vs mean subsequent "
             f"21d realized {s['mean_fwd_rv']:.2f} -> mean VRP **{s['mean_vrp']:+.2f} vol pts** "
             f"(median {s['median_vrp']:+.2f}, NW t {s['t_nw']:.2f}); VIX exceeded subsequent realized vol on "
             f"**{s['pct_vrp_pos']:.0%}** of days. 5th percentile {s['p05_vrp']:+.1f}; worst "
             f"{s['worst_vrp']:+.1f} on {s['worst_date']}. Correlation of the *trailing* spread (VIX - past 21d RV, "
             f"known at t) with the forward VRP: {s['corr_trail_fwd']:.2f}.\n")
    L.append("| conditioned on | bucket | n | mean VRP | median | % days VRP>0 | 5th pct |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for _, r in vrp_tab.iterrows():
        L.append(f"| {r['by']} | {r['bucket']} | {r['n']} | {r['mean']:+.2f} | {r['median']:+.2f} | "
                 f"{r['pct_pos']:.0%} | {r['p05']:+.1f} |")
    L.append("")

    # --- full per-ticker tables
    L.append("## Per-ticker detail\n")
    for sym in res["ticker"].unique():
        sub = res[res.ticker == sym]
        L.append(f"### {sym} ({sub['start'].iloc[0]} to {sub['end'].iloc[0]})\n")
        L.append("| signal | h | n | ep | mean | med | hit | excess | t_nw | fwdRV | RVx |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in sub.iterrows():
            flag = "*" if (r.episodes < MIN_EPISODES and r.signal != "ALL (baseline)") else ""
            t = "" if pd.isna(r.t_nw) else (f"**{r.t_nw:+.2f}**" if abs(r.t_nw) >= 2 else f"{r.t_nw:+.2f}")
            L.append(f"| {r.signal}{flag} | {r.horizon} | {r.n} | {r.episodes} | {fmt_pct(r['mean'])} | "
                     f"{fmt_pct(r['median'])} | {r.hit:.0%} | {fmt_pct(r.excess)} | {t} | "
                     f"{r.fwd_rv21:.1f} | {r.rv_ratio:.2f} |")
        L.append("")
    return "\n".join(L)


def main():
    bars = load_bars(TICKERS)
    vix = vix_frame()
    feed_p = HERE.parent / ".cache" / "charts" / "feed.txt"
    feed = feed_p.read_text().strip() if feed_p.exists() else "?"
    rows, feats = [], {}
    for sym in TICKERS:
        if sym not in bars:
            print(f"skip {sym}: no data")
            continue
        f = build_features(bars[sym], vix)
        feats[sym] = f
        rows += study_ticker(sym, f)
    res = pd.DataFrame(rows)
    res.to_csv(HERE / "signal_study.csv", index=False, float_format="%.6g")
    vrp_tab, vrp_sum = vrp_study(feats["SPY"])
    vrp_tab.to_csv(HERE / "vrp_study.csv", index=False, float_format="%.6g")
    (HERE / "signal_study.md").write_text(write_markdown(res, vrp_tab, vrp_sum, feed))
    print(f"wrote {HERE/'signal_study.csv'}, {HERE/'signal_study.md'}, {HERE/'vrp_study.csv'}")
    return res, vrp_tab, vrp_sum


if __name__ == "__main__":
    main()
