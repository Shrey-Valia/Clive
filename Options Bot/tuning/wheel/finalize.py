"""Build results.csv, finalists.json and the terminal-styled charts.

Run after all stages:  python3 tuning/wheel/finalize.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "1.0")

import matplotlib                       # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt         # noqa: E402

from analyze import (NAMES9, basket, basket_table, deflated_sharpe, flat_rows, load,  # noqa: E402
                     seg_metrics, sharpe)
from optionsbot import metrics as M     # noqa: E402
from optionsbot import runner           # noqa: E402
from optionsbot.strategies import BuyHold   # noqa: E402
from optionsbot.strategies_wheel import WHEEL_REGISTRY   # noqa: E402
from portfolio import UNIVERSE, equal_weight_bh   # noqa: E402
import run_portfolio                    # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"
SHOCKS = {"Aug-2024 (yen carry)": ("2024-07-10", "2024-08-30"),
          "Apr-2025 (tariffs)": ("2025-03-24", "2025-05-15")}

BG, AMBER, WHITE, GREEN, RED, GREY = "#000000", "#ffa028", "#e8e8e8", "#3ddc84", "#ff4d4d", "#5a5a5a"
plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "xtick.color": WHITE, "ytick.color": WHITE,
    "text.color": WHITE, "font.family": "monospace", "font.size": 9, "axes.grid": True,
    "grid.color": "#222222", "grid.linewidth": 0.6, "axes.titlecolor": AMBER, "axes.titleweight": "bold",
    "legend.facecolor": BG, "legend.edgecolor": GREY, "lines.linewidth": 1.6,
})

FIN = json.loads((HERE / "finalist_choice.json").read_text())


def clean(x):
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return None if (math.isnan(x) or math.isinf(x)) else round(x, 6)
    return x


# ------------------------------------------------------------------ runs
def run_single(f, capital, cash_yield=False, symbol=None):
    cls = WHEEL_REGISTRY[f["strategy"]]
    sym = symbol or f["underlying"]
    r = runner.run_one(cls, f["params"], sym, START, END, capital, cash_yield=cash_yield)
    b = runner.run_one(BuyHold, None, sym, START, END, capital)
    return r["equity"]["equity"], r["trades"], b["equity"]["equity"], r


def run_pf(f, capital, cash_yield=False):
    row, p = run_portfolio.run_one(f["id"], f["params"], f["pkw"], capital=capital, cash_yield=cash_yield)
    eq = p.equity_frame()["equity"]
    return eq, p.trades_frame(), equal_weight_bh(UNIVERSE, capital=capital), p


def full_metrics(eq, tr):
    m = M.equity_metrics(eq)
    tm = M.trade_metrics(tr) if tr is not None and len(tr) else {"n_trades": 0}
    return {"cagr": clean(m["cagr"]), "total_return": clean(m["total_return"]),
            "max_drawdown": clean(m["max_drawdown"]), "sharpe": clean(m["sharpe"]),
            "sortino": clean(m["sortino"]), "calmar": clean(m["calmar"]), "ann_vol": clean(m["ann_vol"]),
            "win_rate": clean(tm.get("win_rate")), "profit_factor": clean(tm.get("profit_factor")),
            "trades": int(tm.get("n_trades", 0))}


def seg(eq):
    m = M.equity_metrics(eq)
    return {k: clean(m.get(k)) for k in ("cagr", "total_return", "sharpe", "sortino", "max_drawdown", "calmar")}


def shock_table(eq, bh):
    out = {}
    for k, (a, b) in SHOCKS.items():
        e, h = eq[a:b], bh[a:b]
        out[k] = {"strategy_ret": clean(e.iloc[-1] / e.iloc[0] - 1), "strategy_dd": clean(M.drawdown(e).min()),
                  "bh_ret": clean(h.iloc[-1] / h.iloc[0] - 1), "bh_dd": clean(M.drawdown(h).min())}
    return out


def worst_trades(tr, n=5):
    if tr is None or not len(tr):
        return []
    t = tr[tr.reason != "open_at_end"].nsmallest(n, "pnl")
    cols = [c for c in ["underlying", "kind", "open_date", "close_date", "legs", "pnl", "reason"] if c in t]
    return [{c: (str(v) if c in ("open_date", "close_date") else clean(v) if isinstance(v, float) else v)
             for c, v in r.items()} for r in t[cols].to_dict("records")]


def worst_episodes(eq, n=3):
    """Largest peak-to-trough equity drawdowns (non-overlapping)."""
    dd = M.drawdown(eq)
    eps, used = [], pd.Series(False, index=eq.index)
    for _ in range(n):
        d = dd[~used]
        if d.empty or d.min() >= -0.005:
            break
        trough = d.idxmin()
        peak = eq[:trough].idxmax()
        rec = eq[trough:][eq[trough:] >= eq[peak]]
        end = rec.index[0] if len(rec) else eq.index[-1]
        eps.append({"peak": str(peak.date()), "trough": str(trough.date()),
                    "recovered": str(end.date()) if len(rec) else None, "depth": clean(dd[trough])})
        used[peak:end] = True
    return eps


def downsample(s, every=1):
    return [{"date": str(d.date()), "value": round(float(v), 4)} for d, v in s.iloc[::every].items()]


# ---------------------------------------------------------------- charts
def chart_finalist(fid, title, eq, bh, bh_label, path):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True,
                                 gridspec_kw={"height_ratios": [2.2, 1]})
    a1.plot(eq.index, eq.values, color=AMBER, label="strategy")
    a1.plot(bh.index, bh.values, color=WHITE, lw=1.1, ls="--", label=bh_label)
    a1.axvline(pd.Timestamp(OOS_START), color=GREY, lw=1)
    a1.text(pd.Timestamp(OOS_START), a1.get_ylim()[1], "  OOS ->", color=GREY, va="top")
    a1.set_title(title, loc="left")
    a1.set_ylabel("equity $")
    a1.legend(loc="upper left", frameon=True)
    a1.annotate(f"${eq.iloc[-1]:,.0f}", (eq.index[-1], eq.iloc[-1]), color=AMBER, xytext=(4, 0),
                textcoords="offset points", va="center")
    a1.annotate(f"${bh.iloc[-1]:,.0f}", (bh.index[-1], bh.iloc[-1]), color=WHITE, xytext=(4, 0),
                textcoords="offset points", va="center")
    d1, d2 = M.drawdown(eq) * 100, M.drawdown(bh) * 100
    a2.fill_between(d1.index, d1.values, 0, color=RED, alpha=0.45, lw=0, label="strategy DD")
    a2.plot(d2.index, d2.values, color=WHITE, lw=0.9, ls="--", label=f"{bh_label} DD")
    a2.set_ylabel("drawdown %")
    a2.legend(loc="lower left")
    for k, (a, b) in SHOCKS.items():
        for ax in (a1, a2):
            ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color="#331d00", lw=0, zorder=0)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def chart_is_oos(bt, chosen, path):
    fig, ax = plt.subplots(figsize=(8.5, 6))
    fams = {"wheel_core": AMBER, "wheel_mgmt": "#ffd08a", "covered_call": WHITE, "csp": "#9a9a9a",
            "collar": GREEN, "wheel_combo": "#ff9cf0", "portfolio": RED}
    for fam, g in bt.groupby("family"):
        if fam == "bench":
            continue
        ax.scatter(g.is_sharpe, g.oos_sharpe, s=16, color=fams.get(fam, GREY), label=f"{fam} ({len(g)})",
                   alpha=0.85, edgecolors="none")
    b = bt[bt.config.isin(["buy_hold", "ew_bh8"])]
    for _, r in b.iterrows():
        ax.scatter([r.is_sharpe], [r.oos_sharpe], marker="s", s=60, color=BG, edgecolors=WHITE)
        ax.annotate(r.config, (r.is_sharpe, r.oos_sharpe), color=WHITE, xytext=(5, -10), textcoords="offset points")
    for c in chosen:
        r = bt[bt.config == c]
        if len(r):
            r = r.iloc[0]
            ax.scatter([r.is_sharpe], [r.oos_sharpe], marker="*", s=180, color=AMBER, edgecolors=WHITE)
            ax.annotate(c, (r.is_sharpe, r.oos_sharpe), color=AMBER, xytext=(6, 4), textcoords="offset points")
    lim = [0.1, 2.6]
    ax.plot(lim, lim, color=GREY, lw=0.8, ls=":")
    ax.set_xlabel("in-sample Sharpe (2024-03 .. 2025-06)")
    ax.set_ylabel("out-of-sample Sharpe (2025-07 .. 2026-10)")
    rho = bt[bt.family != "bench"][["is_sharpe", "oos_sharpe"]].corr("spearman").iloc[0, 1]
    ax.set_title(f"every variant: IS vs OOS Sharpe  (spearman rho = {rho:+.2f})", loc="left")
    ax.legend(loc="lower right", fontsize=7.5)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return rho


def chart_names(tbl, path):
    """Per-name full-period Sharpe: finalist configs vs buy-hold."""
    fig, ax = plt.subplots(figsize=(10, 4.2))
    names = list(tbl.index)
    cols = list(tbl.columns)
    w = 0.8 / len(cols)
    palette = [WHITE, AMBER, "#ffd08a", GREEN, "#9a9a9a"]
    for i, c in enumerate(cols):
        ax.bar(np.arange(len(names)) + (i - (len(cols) - 1) / 2) * w, tbl[c].values, width=w * 0.9,
               color=palette[i % len(palette)], label=c)
    ax.set_xticks(np.arange(len(names)))
    ax.set_xticklabels(names)
    ax.axhline(0, color=GREY, lw=0.8)
    ax.set_ylabel("full-period Sharpe")
    ax.set_title("same config on every name ($100k each) - no name picking", loc="left")
    ax.legend(fontsize=7.5, ncol=len(cols), loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ main
def main():
    rows, eqs = load()
    import pickle
    eqs.update(pickle.loads((HERE / "runs" / "P.eq.pkl").read_bytes()))
    df = flat_rows(rows)
    bt = basket_table(eqs, df)
    # portfolio rows
    prow = [json.loads(l) for l in (HERE / "runs" / "P.jsonl").read_text().splitlines()]
    pf = []
    for r in prow:
        e = eqs[(r["config"], "PORTFOLIO", 100_000, False)].astype(float)
        pf.append({"config": r["config"], "family": "portfolio", **seg_metrics(e),
                   "params": json.dumps({**r["params"], **r["pkw"]}, sort_keys=True)})
    ew = eqs[("ew_bh8", "PORTFOLIO", 100_000, False)].astype(float)
    pf.append({"config": "ew_bh8", "family": "bench", **seg_metrics(ew), "params": "{}"})
    bt = pd.concat([bt, pd.DataFrame(pf)], ignore_index=True)

    # ---- trial accounting & deflated Sharpe (on IS daily returns of the selection object)
    trials = bt[bt.family != "bench"]
    n_total = len(trials)
    fam_n = trials.groupby("family").size().to_dict()
    is_var = trials.is_sharpe.var()

    # ---- results.csv: per-name rows + basket + portfolio rows
    per = df.copy()
    per["level"] = "single_name"
    bsk = bt.copy()
    bsk["symbol"] = np.where(bsk.family.eq("portfolio") | bsk.config.eq("ew_bh8"), "PORTFOLIO8", "BASKET9")
    bsk["level"] = np.where(bsk.symbol.eq("BASKET9"), "basket_eqw_9names", "shared_cash_portfolio")
    bsk["capital"] = 100_000
    res = pd.concat([bsk, per], ignore_index=True, sort=False)
    res["trial_counted"] = ~res.config.isin(["buy_hold", "ew_bh8"])
    res["is_minus_oos_sharpe"] = res.is_sharpe - res.oos_sharpe
    lead = ["level", "config", "family", "symbol", "capital", "cash_yield", "is_cagr", "is_sharpe",
            "is_max_drawdown", "oos_cagr", "oos_sharpe", "oos_max_drawdown", "full_cagr", "full_sharpe",
            "full_sortino", "full_max_drawdown", "full_calmar", "is_minus_oos_sharpe"]
    rest = [c for c in res.columns if c not in lead]
    res[lead + rest].to_csv(HERE / "results.csv", index=False, float_format="%.5f")

    # ---- finalists
    out, summary = [], {"n_trials_total": n_total, "n_trials_by_family": fam_n,
                        "is_sharpe_var_across_trials": is_var}
    bh_basket = basket(eqs, "buy_hold", NAMES9)
    name_tbl = {}
    for f in FIN["finalists"]:
        sel = f["selected_config"]
        if f["kind"] == "portfolio":
            eq, tr, bh, p = run_pf(f, f["capital"])
            bh_label = "EW buy&hold 8 names"
            eq_cy, _, _, _ = run_pf(f, f["capital"], cash_yield=True)
            sel_eq = eqs[(sel, "PORTFOLIO", 100_000, False)].astype(float)
            ref_is = M.equity_metrics(ew[:IS_END])["sharpe"]
            ref_oos = M.equity_metrics(ew[OOS_START:])["sharpe"]
        else:
            eq, tr, bh, r = run_single(f, f["capital"])
            bh_label = f"buy&hold {f['underlying']}"
            eq_cy, _, _, _ = run_single(f, f["capital"], cash_yield=True)
            sel_eq = basket(eqs, sel, NAMES9)
            ref_is = M.equity_metrics(bh_basket[:IS_END])["sharpe"]
            ref_oos = M.equity_metrics(bh_basket[OOS_START:])["sharpe"]
            tbl = {}
            for s in NAMES9:
                rr = df[(df.config == sel) & (df.symbol == s) & (df.capital == 100_000) & (~df.cash_yield)]
                bb = df[(df.config == "buy_hold") & (df.symbol == s) & (df.capital == 100_000)]
                tbl[s] = {"full_sharpe": clean(rr.full_sharpe.iloc[0]), "is_sharpe": clean(rr.is_sharpe.iloc[0]),
                          "oos_sharpe": clean(rr.oos_sharpe.iloc[0]), "full_cagr": clean(rr.full_cagr.iloc[0]),
                          "full_dd": clean(rr.full_max_drawdown.iloc[0]),
                          "bh_full_sharpe": clean(bb.full_sharpe.iloc[0]), "bh_full_cagr": clean(bb.full_cagr.iloc[0]),
                          "bh_full_dd": clean(bb.full_max_drawdown.iloc[0])}
            f["per_name"] = tbl
            name_tbl[f["id"]] = {s: v["full_sharpe"] for s, v in tbl.items()}
        sel_ret = sel_eq.pct_change().dropna()
        sel_is = sel_ret[:IS_END]
        is_sr = sharpe(sel_is)
        dsr_all, sr0_all = deflated_sharpe(is_sr, n_total, is_var, sel_is)
        fam_tr = trials[trials.family.isin(f["families"])]
        dsr_f, sr0_f = deflated_sharpe(is_sr, len(fam_tr), fam_tr.is_sharpe.var(), sel_is)
        oos_sr = sharpe(sel_ret[OOS_START:])
        sel_stats = {"selection_object": "BASKET9" if f["kind"] != "portfolio" else "PORTFOLIO8",
                     "is_sharpe": clean(is_sr), "oos_sharpe": clean(oos_sr),
                     "bench_is_sharpe": clean(ref_is), "bench_oos_sharpe": clean(ref_oos),
                     "is_excess_sharpe_vs_bench": clean(is_sr - ref_is),
                     "oos_excess_sharpe_vs_bench": clean(oos_sr - ref_oos),
                     "dsr_all_trials": clean(dsr_all), "sr0_all_trials": clean(sr0_all), "n_trials_all": n_total,
                     "dsr_family": clean(dsr_f), "sr0_family": clean(sr0_f), "n_trials_family": len(fam_tr)}
        mon = M.monthly_returns(eq)
        dd = M.drawdown(eq)
        rec = {
            "id": f["id"], "name": f["name"], "underlying": f["underlying"], "capital": f["capital"],
            "metrics": full_metrics(eq, tr),
            "is_metrics": seg(eq[:IS_END]), "oos_metrics": seg(eq[OOS_START:]),
            "benchmark": {"label": bh_label, "full": seg(bh), "is": seg(bh[:IS_END]), "oos": seg(bh[OOS_START:])},
            "cash_yield_on": {"full": seg(eq_cy), "is": seg(eq_cy[:IS_END]), "oos": seg(eq_cy[OOS_START:])},
            "equity": downsample(eq), "drawdown": downsample(dd),
            "benchmark_equity": downsample(bh),
            "monthly_returns": [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 5)} for d, v in mon.items()],
            "params": {**f["params"], **f.get("pkw", {}), "strategy": f["strategy"]},
            "selection": sel_stats, "selected_config": sel,
            "shocks": shock_table(eq, bh), "worst_trades": worst_trades(tr), "worst_drawdowns": worst_episodes(eq),
            "trade_count_by_kind": tr.groupby("kind").size().to_dict() if len(tr) else {},
            "per_name": f.get("per_name"),
        }
        # smaller-account feasibility
        alt = {}
        for cap in f.get("alt_capitals", []):
            if f["kind"] == "portfolio":
                e2, t2, b2, _ = run_pf(f, cap)
            else:
                e2, t2, b2, r2 = run_single(f, cap, symbol=f.get("alt_symbol", {}).get(str(cap)))
            alt[str(cap)] = {"symbol": f.get("alt_symbol", {}).get(str(cap), f["underlying"]),
                             "full": seg(e2), "is": seg(e2[:IS_END]), "oos": seg(e2[OOS_START:]),
                             "bh_full": seg(b2), "trades": int(len(t2))}
        rec["smaller_accounts"] = alt
        out.append(rec)
        chart_finalist(f["id"], f"{f['id']}  {f['name']}  (${f['capital']/1000:.0f}k)", eq, bh, bh_label,
                       OUT / f"{f['id']}_equity_dd.png")
        print(f["id"], rec["metrics"], rec["is_metrics"]["sharpe"], rec["oos_metrics"]["sharpe"], sel_stats)
    (HERE / "finalists.json").write_text(json.dumps({"generated": "2026-10-21", "summary": summary,
                                                     "finalists": out}, indent=1))
    rho = chart_is_oos(bt, [f["selected_config"] for f in FIN["finalists"]], OUT / "is_vs_oos_all_variants.png")
    bh_names = df[(df.config == "buy_hold") & (df.capital == 100_000)].set_index("symbol").full_sharpe
    t = pd.DataFrame({"buy&hold": bh_names, **{k: pd.Series(v) for k, v in name_tbl.items()}}).loc[NAMES9]
    chart_names(t, OUT / "per_name_sharpe.png")
    bt.to_csv(HERE / "out" / "basket_table.csv", index=False, float_format="%.4f")
    print("spearman IS/OOS", rho, "trials", n_total, fam_n)


if __name__ == "__main__":
    main()
