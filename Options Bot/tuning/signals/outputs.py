"""Terminal-styled charts (out/*.png) + finalists.json from analyze.py outputs."""
from __future__ import annotations

import json
import pickle

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import common as C  # noqa: E402
from common import IS_END, OOS_START, em, monthly, series  # noqa: E402
from optionsbot.strategies_signals import features  # noqa: E402

BG, AMB, WH, GR, RD, GY = "#000000", "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#555555"
plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "axes.edgecolor": GY, "axes.labelcolor": WH, "xtick.color": WH, "ytick.color": WH,
    "text.color": WH, "font.family": "monospace", "font.size": 9, "axes.grid": True,
    "grid.color": "#222222", "grid.linestyle": "-", "legend.facecolor": BG,
    "legend.edgecolor": GY, "axes.titlecolor": AMB, "axes.titleweight": "bold",
})

A = json.load(open(C.HERE / "cache" / "analysis.json"))
SER = pickle.load(open(C.HERE / "cache" / "series.pkl", "rb"))
RES = pd.read_csv(C.HERE / "results.csv")
FIN = json.load(open(C.HERE / "cache" / "finalists_spec.json"))   # written by hand-off step


def oos_line(ax):
    ax.axvline(pd.Timestamp(OOS_START), color=AMB, lw=0.8, ls="--")
    ax.text(pd.Timestamp(OOS_START), ax.get_ylim()[1], " OOS", color=AMB, va="top", fontsize=8)


# ------------------------------------------------------ 1. equity overview
def equity_overview():
    fams = list(SER)
    fig, axes = plt.subplots(len(fams), 1, figsize=(12, 2.6 * len(fams)), sharex=True)
    for ax, fam in zip(axes, fams):
        d = SER[fam]
        lab = A["selected"][fam]["label"]
        ax.plot(d["eq"].index, d["eq"].values, color=AMB, lw=1.4, label=f"signal: {lab}")
        ax.plot(d["ctrl"].index, d["ctrl"].values, color=WH, lw=0.9, alpha=0.8,
                label="unconditional control" if fam != "S5_pmcc_signal" else "pmcc_sma50")
        ax.plot(d["bh"].index, d["bh"].values, color=GY, lw=0.9, label="buy & hold (same names)")
        ax.set_title(fam, loc="left", fontsize=10)
        ax.legend(loc="upper left", fontsize=7)
        oos_line(ax)
    fig.suptitle("SIGNAL-TIMED OPTIONS :: BASKET EQUITY ($10k sleeves, equal weight)", color=AMB,
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(C.OUT / "equity_overview.png", dpi=110)
    plt.close(fig)


# --------------------------------------------- 2. IS vs OOS degradation
def is_oos_scatter():
    t = RES[RES.role == "trial"]
    fig, ax = plt.subplots(figsize=(8, 6))
    cols = {"S1_dip_pcs": AMB, "S2_dip_cds": GR, "S3_ob_ccs": RD, "S4_vol_spike": "#66ccff",
            "S5_pmcc_signal": WH, "S6_rs_routing": "#cc88ff"}
    for f, g in t.groupby("family"):
        ax.scatter(g.is_sharpe, g.oos_sharpe, s=14, color=cols.get(f, WH), label=f, alpha=0.8)
    s = t[t.selected]
    ax.scatter(s.is_sharpe, s.oos_sharpe, s=90, facecolors="none", edgecolors=AMB, lw=1.5,
               label="IS-selected")
    lim = [min(t.is_sharpe.min(), t.oos_sharpe.min()) - 0.2, max(t.is_sharpe.max(), t.oos_sharpe.max()) + 0.2]
    ax.plot(lim, lim, color=GY, lw=0.8)
    ax.axhline(0, color=GY, lw=0.6)
    ax.axvline(0, color=GY, lw=0.6)
    rho = t[["is_sharpe", "oos_sharpe"]].corr(method="spearman").iloc[0, 1]
    ax.set_title(f"IS vs OOS Sharpe, {len(t)} trials (spearman {rho:.2f})", loc="left")
    ax.set_xlabel("in-sample Sharpe (2024-03..2025-06)")
    ax.set_ylabel("out-of-sample Sharpe (2025-07..2026-10)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(C.OUT / "is_vs_oos_sharpe.png", dpi=110)
    plt.close(fig)


# ---------------------------------------- 3. per-trade return distributions
def trade_dists():
    fams = [f for f in SER if f != "S5_pmcc_signal"]
    fig, axes = plt.subplots(1, len(fams), figsize=(3.2 * len(fams), 3.6))
    for ax, fam in zip(np.atleast_1d(axes), fams):
        d = SER[fam]
        tr = d["tr"]
        for (a, z), col, nm in (((None, IS_END), GY, "IS"), ((OOS_START, None), AMB, "OOS")):
            x = tr[(tr.open_date >= (a or "1900")) & (tr.open_date <= (z or "2100"))]["ret"]
            if len(x):
                ax.hist(x.clip(-1.2, 1.2), bins=25, color=col, alpha=0.6,
                        label=f"{nm} n={len(x)} mean={x.mean():+.1%}")
        ax.axvline(0, color=WH, lw=0.6)
        ax.set_title(fam, loc="left", fontsize=9)
        ax.legend(fontsize=6)
        ax.set_xlabel("P&L / max loss")
    fig.tight_layout()
    fig.savefig(C.OUT / "trade_return_dists.png", dpi=110)
    plt.close(fig)


# ------------------------------------------ 4. price + markers + indicators
def price_chart(fam, sym, fname):
    d = SER[fam]
    m = C.market(sym)
    f = features(m)
    f = f.loc[C.START:C.END]
    if "trades" in d:
        tr = d["trades"][sym]
        tr = tr[tr["kind"].isin(["pcs", "cds", "ccs", "pmcc_short"])] if len(tr) else tr
    else:
        tr = d["tr"][d["tr"]["underlying"] == sym]
    rows = 4
    fig, ax = plt.subplots(rows, 1, figsize=(13, 9), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1, 1, 1.4]})
    a0 = ax[0]
    a0.plot(f.index, f["close"], color=WH, lw=0.9, label=f"{sym} (split-adj)")
    a0.plot(f.index, f["sma200"], color=GY, lw=0.8, label="200DMA")
    a0.plot(f.index, f["sma50"], color=AMB, lw=0.6, alpha=0.6, label="50DMA")
    a0.plot(f.index, f["sma5"], color=AMB, lw=0.5, alpha=0.4, ls=":", label="5DMA")
    if len(tr):
        od = pd.to_datetime(tr["open_date"])
        cd = pd.to_datetime(tr["close_date"].dropna())
        win = tr["pnl"] > 0
        a0.scatter(od, f["close"].reindex(od).values, marker="^", s=36, color=GR, zorder=5,
                   label="entry")
        cdx = pd.to_datetime(tr["close_date"])
        for w, col, nm in ((True, GR, "exit (win)"), (False, RD, "exit (loss)")):
            sel = (win == w) & cdx.notna()
            a0.scatter(cdx[sel], f["close"].reindex(cdx[sel]).values, marker="v", s=36,
                       color=col, edgecolors=WH, linewidths=0.4, zorder=6, label=nm)
    from optionsbot.strategies_signals import EARNINGS
    for e in EARNINGS.get(sym, []):
        if f.index[0] <= e <= f.index[-1]:
            a0.axvline(e, color="#66ccff", lw=0.5, alpha=0.5)
    a0.legend(loc="upper left", fontsize=7, ncol=4)
    a0.set_title(f"{sym} :: {A['selected'][fam]['label']}", loc="left")
    oos_line(a0)
    a1 = ax[1]
    a1.plot(f.index, f["rsi2"], color=AMB, lw=0.6)
    for y in (10, 90):
        a1.axhline(y, color=RD if y == 10 else GR, lw=0.6, ls="--")
    a1.set_ylabel("RSI(2)")
    a2 = ax[2]
    if fam == "S4_vol_spike":
        a2.plot(f.index, f["vix_ratio"], color=AMB, lw=0.8)
        a2.axhline(1.0, color=RD, lw=0.6, ls="--")
        a2.set_ylabel("VIX/VIX3M")
    else:
        a2.plot(f.index, f["ibs"].rolling(1).mean(), color=AMB, lw=0.4)
        a2.axhline(0.2, color=RD, lw=0.6, ls="--")
        a2.set_ylabel("IBS")
    a3 = ax[3]
    eq = d["per"][sym] if "per" in d else None
    if eq is None and "act" in d:
        on = [pd.Period(mo).start_time for mo in sorted(d["act"][sym])]
        for t0 in on:
            a0.axvspan(t0, t0 + pd.offsets.MonthEnd(0), color=AMB, alpha=0.07, lw=0)
        a3.plot(f.index, f["mom12_1"], color=AMB, lw=0.8)
        a3.axhline(0, color=GY, lw=0.6)
    if eq is not None:
        a3.plot(eq.index, eq.values, color=AMB, lw=1.1, label="strategy $10k")
        b = C.bh_equity(sym)
        a3.plot(b.index, b.values, color=GY, lw=0.9, label="buy & hold")
        a3.legend(loc="upper left", fontsize=7)
    a3.set_ylabel("12-1 mom (shade = active month)" if eq is None else "equity $")
    a3.set_xlim(f.index[0], f.index[-1])
    fig.tight_layout()
    fig.savefig(C.OUT / fname, dpi=110)
    plt.close(fig)


def rs_chart():
    d = SER["S6_rs_routing"]
    fig, ax = plt.subplots(2, 1, figsize=(12, 6), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    ax[0].plot(d["eq"].index, d["eq"].values, color=AMB, lw=1.3, label="RS-routed (IS-selected)")
    ax[0].plot(d["ctrl"].index, d["ctrl"].values, color=WH, lw=0.9, label="same structure, EW6 ungated")
    ax[0].plot(d["bh"].index, d["bh"].values, color=GR, lw=0.9, label="B&H top-k momentum")
    ax[0].plot(d["bh_ew"].index, d["bh_ew"].values, color=GY, lw=0.9, label="B&H EW6")
    ax[0].legend(fontsize=7, loc="upper left")
    ax[0].set_title(f"S6 :: {A['selected']['S6_rs_routing']['label']}", loc="left")
    oos_line(ax[0])
    months = pd.period_range(C.START, C.END, freq="M")
    for i, s in enumerate(C.MEGA):
        on = [mo for mo in months if mo.strftime("%Y-%m") in d["act"][s]]
        for mo in on:
            ax[1].barh(i, 28, left=mo.start_time, color=AMB, height=0.6)
    ax[1].set_yticks(range(len(C.MEGA)))
    ax[1].set_yticklabels(C.MEGA)
    ax[1].set_title("active names by month (top-k 12-1 momentum at prior month end)", loc="left",
                    fontsize=8)
    fig.tight_layout()
    fig.savefig(C.OUT / "S6_rs_routing.png", dpi=110)
    plt.close(fig)


# ----------------------------------------------------------- finalists.json
def metrics_block(eq, tr):
    m = em(eq)
    t = tr if tr is not None else pd.DataFrame(columns=["pnl"])
    pnl = t["pnl"] if len(t) else pd.Series(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None
    return {"cagr": m["cagr"], "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
            "sharpe": m["sharpe"], "sortino": m["sortino"],
            "win_rate": float(len(wins) / len(pnl)) if len(pnl) else None,
            "profit_factor": pf, "trades": int(len(pnl))}


def finalists_json():
    out = []
    for spec in FIN:
        fam, sym = spec["family"], spec["underlying"]
        d = SER[fam]
        if sym == "BASKET":
            eq, tr = d["eq"], d["tr"]
            cap = C.CAPITAL
        else:
            eq = d["per"][sym]
            t = d["trades"][sym]
            tr = t[t["reason"] != "open_at_end"]
            cap = C.CAPITAL
        tr_oos = tr[pd.to_datetime(tr["open_date"]) >= OOS_START] if len(tr) else tr
        tr_is = tr[pd.to_datetime(tr["open_date"]) <= IS_END] if len(tr) else tr
        sel = A["selected"][fam]
        out.append({
            "id": spec["id"], "name": spec["name"], "status": spec.get("status"), "underlying": sym if sym != "BASKET" else
            "+".join(d["syms"]), "capital": cap,
            "metrics": metrics_block(eq, tr),
            "equity": series(eq, 2), "drawdown": series(eq, 2, dd=True),
            "monthly_returns": monthly(eq),
            "params": sel["params"] | {"strategy": sel["sid"], "family": fam,
                                       "selection": "best IS Sharpe in family, frozen"},
            "is_metrics": metrics_block(eq[:IS_END], tr_is),
            "oos_metrics": metrics_block(eq[OOS_START:], tr_oos),
        })
    json.dump(out, open(C.HERE / "finalists.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    equity_overview()
    is_oos_scatter()
    trade_dists()
    rs_chart()
    for s in ("NVDA", "GOOGL"):
        price_chart("S6_rs_routing", s, f"S6_rs_routing_{s}_price.png")
    for spec in FIN:
        if spec["underlying"] != "BASKET":
            price_chart(spec["family"], spec["underlying"],
                        f"{spec['id']}_{spec['underlying']}_price.png")
    for fam, syms in {"S1_dip_pcs": ["SPY", "QQQ"], "S2_dip_cds": ["SPY"],
                      "S3_ob_ccs": ["SPY"], "S4_vol_spike": ["SPY"],
                      "S5_pmcc_signal": ["AAPL"]}.items():
        for s in syms:
            if fam in SER and s in SER[fam].get("per", {}):
                price_chart(fam, s, f"{fam}_{s}_price.png")
    finalists_json()
    print("outputs written")
