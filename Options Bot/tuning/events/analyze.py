"""Stats, sizing simulation, charts and dashboard payload for the event study.

Inputs : ledger.csv, trades_earnings.csv, trades_1dte.csv (this folder)
Outputs: results.csv, finalists.json, stats.json, out/*.png
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
OOS = pd.Timestamp("2025-07-01")
GATE = 1.2            # pre-specified (research note / Milian 2023); IS scan reported only
AMBER, WHITE, GREEN, RED, GREY = "#ffa028", "#e8e8e8", "#3ddc84", "#ff4d4d", "#555555"
plt.rcParams.update({
    "figure.facecolor": "black", "axes.facecolor": "black", "savefig.facecolor": "black",
    "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "xtick.color": WHITE, "ytick.color": WHITE,
    "text.color": WHITE, "font.family": "monospace", "font.size": 9, "axes.grid": True,
    "grid.color": "#222222", "axes.titlecolor": AMBER, "axes.titleweight": "bold",
    "legend.facecolor": "black", "legend.edgecolor": GREY,
})
RNG = np.random.default_rng(7)


# ---------------------------------------------------------------- statistics
def stats(r: pd.Series) -> dict:
    r = pd.Series(r).dropna().astype(float)
    n = len(r)
    if n == 0:
        return {"n": 0}
    sd = r.std(ddof=1) if n > 1 else np.nan
    t = r.mean() / (sd / math.sqrt(n)) if n > 1 and sd > 0 else np.nan
    if n > 1:
        bs = RNG.choice(r.values, size=(10000, n), replace=True).mean(axis=1)
        lo, hi = np.percentile(bs, [2.5, 97.5])
    else:
        lo = hi = np.nan
    pos = r[r > 0]
    srt = r.sort_values(ascending=False)
    tot = r.sum()
    return {"n": n, "mean": r.mean(), "median": r.median(), "sd": sd, "t": t,
            "ci_lo": lo, "ci_hi": hi, "win": (r > 0).mean(), "worst": r.min(), "best": r.max(),
            "avg_win": pos.mean() if len(pos) else np.nan,
            "avg_loss": r[r <= 0].mean() if (r <= 0).any() else np.nan,
            "top3_share": srt.iloc[:3].sum() / tot if tot > 0 else np.nan}


# ---------------------------------------------------------------- sizing sim
def simulate(tr: pd.DataFrame, capital: float, frac: float):
    """tr: rows with exit_date, max_risk ($/share, 1-lot = x100), pnl ($/share).
    Each trade risks floor(frac*equity / (max_risk*100)) lots, sized off the equity
    at the start of its exit day (all trades closing that day sized together)."""
    eq = capital
    pts, skipped, taken = [], 0, 0
    for d, g in tr.sort_values("exit_date").groupby("exit_date"):
        e0, day = eq, 0.0
        for _, t in g.iterrows():
            lots = math.floor(frac * e0 / (t["max_risk"] * 100))
            if lots < 1:
                skipped += 1
                continue
            taken += 1
            day += lots * t["pnl"] * 100
        eq = e0 + day
        pts.append((d, eq))
    ser = pd.Series(dict(pts)).sort_index()
    return ser, taken, skipped


def curve_metrics(ser: pd.Series, capital: float, start, end) -> dict:
    idx = pd.bdate_range(start, end)
    s = ser.reindex(idx.union(ser.index)).ffill().fillna(capital).reindex(idx)
    s = pd.concat([pd.Series({idx[0] - pd.Timedelta(days=1): capital}), s])
    rets = s.pct_change().dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    tot = s.iloc[-1] / capital - 1
    cagr = (s.iloc[-1] / capital) ** (1 / yrs) - 1 if s.iloc[-1] > 0 and yrs > 0 else np.nan
    dd = s / s.cummax() - 1
    sh = rets.mean() / rets.std() * math.sqrt(252) if rets.std() > 0 else np.nan
    return s, dd, {"total_return": tot, "cagr": cagr, "sharpe": sh, "max_drawdown": dd.min(),
                   "end_equity": float(s.iloc[-1])}


# ---------------------------------------------------------------- load
def load():
    led = pd.read_csv(HERE / "ledger.csv", parse_dates=["D"])
    te = pd.read_csv(HERE / "trades_earnings.csv", parse_dates=["D"])
    t1 = pd.read_csv(HERE / "trades_1dte.csv", parse_dates=["D"]) if (HERE / "trades_1dte.csv").exists() \
        else pd.DataFrame()
    from optionsbot.runner import client
    cal = client().stock_bars("SPY", "2016-01-01", "2026-10-20").index
    nxt = dict(zip(cal[:-1], cal[1:]))
    te["exit_date"] = np.where(te["variant"].str.startswith("E2"), te["D"], te["D"].map(nxt))
    te["exit_date"] = pd.to_datetime(te["exit_date"])
    if len(t1):
        t1["exit_date"] = pd.to_datetime(t1["D"].map(nxt))
    return led, te, t1


def e1_tables(led):
    ok = led[led["ok"] == True].copy()
    rows = []
    for tk, g in list(ok.groupby("ticker")) + [("POOLED", ok)]:
        for per, h in (("ALL", g), ("IS", g[g.D < OOS]), ("OOS", g[g.D >= OOS])):
            if not len(h):
                continue
            rows.append({"ticker": tk, "period": per, "n": len(h), "mean_im": h.im.mean(),
                         "mean_rm": h.rm.mean(), "mean_ratio": h.ratio.mean(),
                         "median_ratio": h.ratio.median(), "pct_rm_gt_im": (h.rm > h.im).mean(),
                         "p90_ratio": h.ratio.quantile(0.9), "max_ratio": h.ratio.max(),
                         "mean_im_minus_rm": (h.im - h.rm).mean(),
                         "mean_gate_ratio": h.gate_ratio.mean()})
    return pd.DataFrame(rows)


def scatter(led):
    ok = led[led["ok"] == True]
    tks = sorted(ok.ticker.unique())
    fig, axs = plt.subplots(2, 4, figsize=(15, 7.5))
    for ax, tk in zip(axs.flat, tks):
        g = ok[ok.ticker == tk]
        m = max(g.im.max(), g.rm.max()) * 100 * 1.1
        ax.plot([0, m], [0, m], color=GREY, lw=1, ls="--")
        for per, col in (("IS", AMBER), ("OOS", WHITE)):
            h = g[(g.D >= OOS) if per == "OOS" else (g.D < OOS)]
            c = [RED if r > i else col for r, i in zip(h.rm, h.im)]
            ax.scatter(h.im * 100, h.rm * 100, c=c, s=34, marker="o" if per == "IS" else "D",
                       edgecolors=col, linewidths=0.8, label=per)
        ax.set_title(f"{tk}  RM>IM {int((g.rm > g.im).sum())}/{len(g)}  mean RM/IM {g.ratio.mean():.2f}",
                     fontsize=9)
        ax.set_xlim(0, m); ax.set_ylim(0, m)
        ax.set_xlabel("implied move %"); ax.set_ylabel("realized |D+1| %")
    axs.flat[0].legend(loc="upper left", fontsize=7)
    fig.suptitle("E1  IMPLIED vs REALIZED EARNINGS MOVE  (o IS <2025-07, ◇ OOS; red = realized > implied)",
                 color=AMBER, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "e1_implied_vs_realized.png", dpi=130)
    plt.close(fig)


def dist_plot(streams: dict, fname, title):
    n = len(streams)
    nc = min(4, n)
    nr = math.ceil(n / nc)
    fig, axs = plt.subplots(nr, nc, figsize=(3.6 * nc, 3.2 * nr), squeeze=False)
    for ax in axs.flat[n:]:
        ax.set_visible(False)
    for ax, (name, r) in zip(axs.flat, streams.items()):
        r = pd.Series(r).dropna() * 100
        bins = np.linspace(min(-100, r.min()), max(r.max(), 50), 26)
        ax.hist(r[r > 0], bins=bins, color=GREEN, alpha=0.85)
        ax.hist(r[r <= 0], bins=bins, color=RED, alpha=0.85)
        ax.axvline(r.mean(), color=AMBER, lw=1.5)
        ax.set_title(f"{name}\nn={len(r)} mean={r.mean():+.1f}% win={(r > 0).mean():.0%}", fontsize=8)
        ax.set_xlabel("P&L % of max risk")
    fig.suptitle(title, color=AMBER, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=130)
    plt.close(fig)


def equity_plot(curves: dict, fname, title):
    fig, ax = plt.subplots(figsize=(11, 4.8))
    cols = [AMBER, WHITE, GREEN, RED, "#59a5ff", "#c78bff", "#888888", "#66ffee"]
    for (name, s), c in zip(curves.items(), cols):
        ax.plot(s.index, s.values, color=c, lw=1.3, label=name)
    ax.axvline(OOS, color=GREY, ls=":", lw=1)
    ax.text(OOS, ax.get_ylim()[1], " OOS →", color=GREY, va="top", fontsize=8)
    ax.set_ylabel("equity $")
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    led, te, t1 = load()
    e1 = e1_tables(led)
    e1.to_csv(HERE / "e1_summary.csv", index=False)
    scatter(led)

    results = []

    def add(strategy, subset, gate, slip, mode, r, extra=None):
        st = stats(r)
        results.append({"strategy": strategy, "subset": subset, "gate": gate, "slip_mult": slip,
                        "mode": mode, **st, **(extra or {})})

    # ------------------------------------------------ earnings variants
    ok = te[te["ok"] == True].copy()
    ok["gated"] = ok["gate_ratio"] >= GATE
    variants = sorted(ok["variant"].unique())
    coverage = (te[(te.slip_mult == 1.0)].groupby(["variant", "mode"])
                .agg(events=("ok", "size"), priced=("ok", "sum")).reset_index())
    for v in variants:
        for mode in ("strict", "relaxed"):
            for sm in sorted(ok.slip_mult.unique()):
                x = ok[(ok.variant == v) & (ok["mode"] == mode) & (ok.slip_mult == sm)]
                if not len(x):
                    continue
                for gate, xg in (("all", x), (f"gate>={GATE}", x[x.gated])):
                    for sub, xs in (("ALL", xg), ("IS", xg[xg.D < OOS]), ("OOS", xg[xg.D >= OOS])):
                        add(v, sub, gate, sm, mode, xs["ret"])
                    if mode == "strict" and sm == 1.0:
                        for tk, xt in xg.groupby("ticker"):
                            add(v, f"ticker:{tk}", gate, sm, mode, xt["ret"])
    # Ticker selection decided on IS only: names whose IS median RM/IM < 0.8
    is_led = led[(led["ok"] == True) & (led.D < OOS)]
    med = is_led.groupby("ticker")["ratio"].median()
    selected = sorted(med[med < 0.8].index)
    for v in variants:
        for sm in sorted(ok.slip_mult.unique()):
            x = ok[(ok.variant == v) & (ok["mode"] == "strict") & (ok.slip_mult == sm)
                   & ok.ticker.isin(selected)]
            for sub, xs in (("ALL", x), ("IS", x[x.D < OOS]), ("OOS", x[x.D >= OOS])):
                add(v, sub, "is_selected:" + "+".join(selected), sm, "strict", xs["ret"])
    # IS-only gate scan (information; the gate is pre-specified at 1.2)
    scan = []
    for v in variants:
        x = ok[(ok.variant == v) & (ok["mode"] == "strict") & (ok.slip_mult == 1.0) & (ok.D < OOS)]
        for thr in (0.0, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6):
            xs = x[x.gate_ratio >= thr]
            s = stats(xs["ret"])
            scan.append({"variant": v, "thr": thr, "n": s["n"], "mean": s.get("mean"), "t": s.get("t"),
                         "win": s.get("win")})
    scan = pd.DataFrame(scan)
    scan.to_csv(HERE / "gate_scan_is.csv", index=False)

    # ------------------------------------------------ 1DTE
    if len(t1):
        o1 = t1[t1["ok"] == True].copy()
        for v in sorted(o1.variant.unique()):
            for sm in sorted(o1.slip_mult.unique()):
                x = o1[(o1.variant == v) & (o1.slip_mult == sm)]
                for tk in ("SPY", "QQQ", "BOTH"):
                    xt = x if tk == "BOTH" else x[x.ticker == tk]
                    for sub, xs in (("ALL", xt), ("IS", xt[xt.D < OOS]), ("OOS", xt[xt.D >= OOS])):
                        add(f"{v}|{tk}", sub, "all", sm, "strict", xs["ret"])
                    if sm == 1.0:
                        for ev in ("FOMC", "CPI"):
                            xe = xt[xt.event == ev]
                            add(f"{v}|{tk}", f"eve:{ev}", "all", sm, "strict", xe["ret"])
                            for sub, xs in (("IS", xe[xe.D < OOS]), ("OOS", xe[xe.D >= OOS])):
                                add(f"{v}|{tk}", f"eve:{ev}:{sub}", "all", sm, "strict", xs["ret"])
                        add(f"{v}|{tk}", "non_event_days", "all", sm, "strict",
                            xt[xt.event.isna() | (xt.event == "")]["ret"])
    res = pd.DataFrame(results)
    res.to_csv(HERE / "results.csv", index=False)

    # ------------------------------------------------ sizing simulations + charts
    base = ok[(ok["mode"] == "strict") & (ok.slip_mult == 1.0)]
    start, end = pd.Timestamp("2024-01-19"), pd.Timestamp("2026-10-20")
    sims, curves = {}, {}
    streams = {
        "E3 IC all": base[base.variant == "E3_ic_1.0_2.0"],
        "E3 IC gated": base[(base.variant == "E3_ic_1.0_2.0") & base.gated],
        "E4 fly all": base[base.variant == "E4_fly_1.5"],
        "E4 fly gated": base[(base.variant == "E4_fly_1.5") & base.gated],
        "E5 cal straddle": base[base.variant == "E5_cal_straddle"],
        "E5 cal call": base[base.variant == "E5_cal_call"],
        "E2 run-up k3": base[base.variant == "E2_k3"],
        "E3 IC IS-selected tickers": base[(base.variant == "E3_ic_1.0_2.0") & base.ticker.isin(selected)],
    }
    if len(t1):
        b1 = o1[o1.slip_mult == 1.0]
        streams.update({
            "Z1 IC $5 SPY+QQQ": b1[b1.variant == "Z1_ic_w5"],
            "Z1 IC 2xIM SPY+QQQ": b1[b1.variant == "Z1_ic_w2x"],
            "Z1 fly SPY+QQQ": b1[b1.variant == "Z1_fly_w1x"],
            "F1 FOMC-eve IC $5": b1[(b1.variant == "Z1_ic_w5") & (b1.event == "FOMC")],
            "F2 CPI-eve IC $5": b1[(b1.variant == "Z1_ic_w5") & (b1.event == "CPI")],
        })
    sim_rows = []
    for name, tr in streams.items():
        for cap in (10_000, 100_000):
            for frac in (0.02, 0.05):
                ser, taken, skipped = simulate(tr, cap, frac)
                s, dd, met = curve_metrics(ser, cap, start, end)
                ser_is = s[s.index < OOS]
                s_oos = s[s.index >= OOS]
                oos_tot = s_oos.iloc[-1] / s_oos.iloc[0] - 1 if len(s_oos) else np.nan
                is_tot = ser_is.iloc[-1] / cap - 1 if len(ser_is) else np.nan
                sim_rows.append({"stream": name, "capital": cap, "risk_frac": frac, "taken": taken,
                                 "skipped_too_small": skipped, **met, "is_return": is_tot,
                                 "oos_return": oos_tot})
                sims[(name, cap, frac)] = (s, dd, met, taken, skipped)
        curves[name] = sims[(name, 100_000, 0.02)][0]
    sim = pd.DataFrame(sim_rows)
    sim.to_csv(HERE / "sizing_sim.csv", index=False)

    earn_curves = {k: v for k, v in curves.items() if k.startswith("E")}
    equity_plot(earn_curves, "equity_earnings_100k_2pct.png",
                "EARNINGS STREAMS  $100k, 2% of equity max-risk per event, base costs")
    z_curves = {k: v for k, v in curves.items() if k[0] in "ZF"}
    if z_curves:
        equity_plot(z_curves, "equity_1dte_100k_2pct.png",
                    "SPY/QQQ 1DTE STREAMS  $100k, 2% of equity max-risk per trade, base costs")
    dist_plot({k: v["ret"] for k, v in streams.items() if k.startswith("E")},
              "pnl_dist_earnings.png", "EARNINGS  per-trade P&L as % of max risk (base costs, strict fills)")
    if len(t1):
        dist_plot({k: v["ret"] for k, v in streams.items() if k[0] in "ZF"},
                  "pnl_dist_1dte.png", "1DTE  per-trade P&L as % of max risk (base costs)")
    # cost sensitivity chart (pooled ALL)
    cs = res[(res.subset == "ALL") & (res["mode"] == "strict") & (res.gate == "all")]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    keep = [s for s in cs.strategy.unique() if ("|" not in s) or s.endswith("|BOTH")]
    for i, s in enumerate(keep):
        g = cs[cs.strategy == s].sort_values("slip_mult")
        ax.plot(g.slip_mult, g["mean"] * 100, marker="o", lw=1.2, label=s,
                color=[AMBER, WHITE, GREEN, RED, "#59a5ff", "#c78bff", "#888888", "#ffd27a",
                       "#66ffee", "#ff88cc", "#aaaa55"][i % 11])
    ax.axhline(0, color=GREY)
    ax.set_xlabel("slippage multiple (0 = trade at closes, 1 = engine Costs default)")
    ax.set_ylabel("mean P&L % of max risk")
    ax.legend(fontsize=6.5, ncol=2)
    ax.set_title("COST SENSITIVITY  mean per-trade return vs slippage")
    fig.tight_layout(); fig.savefig(OUT / "cost_sensitivity.png", dpi=130); plt.close(fig)

    json.dump({"coverage": coverage.to_dict("records")}, open(HERE / "coverage.json", "w"), indent=1,
              default=str)
    return led, te, t1, res, sim, sims, streams


def dashboard_entry(sid, name, und, cap, frac, sims, tr, params, extra=None):
    s, dd, met, taken, skipped = sims[(name, cap, frac)]
    st = stats(tr["ret"])
    is_tr, oos_tr = tr[tr.D < OOS], tr[tr.D >= OOS]
    s_oos = s[s.index >= OOS]
    def thin(x):
        x = x.iloc[::3] if len(x) > 400 else x
        return [{"date": str(d.date()), "value": round(float(v), 4)} for d, v in x.items()]
    mon = s.resample("ME").last().pct_change().dropna()
    first = s.resample("ME").last().iloc[0] / cap - 1
    mon = pd.concat([pd.Series({s.resample("ME").last().index[0]: first}), mon])
    oos_s = stats(oos_tr["ret"])
    oos_eq = s_oos
    om = {"n_trades": oos_s.get("n"), "mean_ret_per_trade": oos_s.get("mean"), "win_rate": oos_s.get("win"),
          "t_stat": oos_s.get("t"), "worst_trade": oos_s.get("worst"),
          "total_return": float(oos_eq.iloc[-1] / oos_eq.iloc[0] - 1) if len(oos_eq) else None,
          "max_drawdown": float((oos_eq / oos_eq.cummax() - 1).min()) if len(oos_eq) else None}
    return {"id": sid, "name": name, "underlying": und, "capital": cap,
            "metrics": {**{k: (None if (isinstance(v, float) and not np.isfinite(v)) else v)
                           for k, v in met.items()},
                        "n_trades": taken, "skipped_too_small": skipped, "risk_frac": frac,
                        "mean_ret_per_trade": st["mean"], "median_ret_per_trade": st["median"],
                        "win_rate": st["win"], "t_stat": st["t"], "boot_ci95": [st["ci_lo"], st["ci_hi"]],
                        "worst_trade": st["worst"], **(extra or {})},
            "equity": thin(s), "drawdown": thin(dd),
            "monthly_returns": [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 5)} for d, v in mon.items()],
            "params": params,
            "oos_metrics": {k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in om.items()}}


def _unused():
    pass


def write_finalists(sims, streams):
    """No event strategy passed the IS->OOS protocol. The entries below are
    reference curves for the dashboard, each carrying an explicit verdict."""
    picks = [
        ("events_e3_ic_is_selected", "E3 IC IS-selected tickers", "AAPL,AMZN,META,NVDA", 100_000, 0.02,
         {"structure": "short ±1.0x IM / long ±2.0x IM, first post-report expiry",
          "entry": "close D", "exit": "close D+1 (settle if expiry)",
          "tickers": "IS median RM/IM < 0.8: AAPL, AMZN, META, NVDA", "risk_frac": 0.02,
          "costs": "engine Costs default, volume>0 on all legs both days"},
         "REJECTED: IS mean +9.9%/trade (t 1.5, n 24) -> OOS -5.7%/trade (t -0.7, n 20). Shown as the best-looking in-sample earnings rule."),
        ("events_e3_ic_all8", "E3 IC all", "8 mega caps", 100_000, 0.02,
         {"structure": "short ±1.0x IM / long ±2.0x IM", "entry": "close D", "exit": "close D+1",
          "gate": "none", "risk_frac": 0.02},
         "REJECTED: pooled -1.8%/trade (t -0.46, n 88); median +18.7% but tail losses (-100% x3)."),
        ("events_z1_ic5", "Z1 IC $5 SPY+QQQ", "SPY,QQQ", 100_000, 0.02,
         {"structure": "1DTE IC, shorts ±1.0x 1-day IM, $5 wings", "entry": "close T",
          "exit": "settle at intrinsic, close T+1", "risk_frac": 0.02},
         "REJECTED: -3.8%/trade (t -3.4, n 1377) at base costs; break-even only at zero slippage."),
    ]
    out = []
    for sid, name, und, cap, frac, params, verdict in picks:
        e = dashboard_entry(sid, name, und, cap, frac, sims, streams[name], params,
                            extra={"verdict": verdict})
        e["verdict"] = verdict
        out.append(e)
    json.dump(out, open(HERE / "finalists.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    _led, _te, _t1, _res, _sim, _sims, _streams = main()
    write_finalists(_sims, _streams)
