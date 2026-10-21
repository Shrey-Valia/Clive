"""Final evaluation of FROZEN configurations (frozen.json). Produces finalists.json,
finalists_detail.json, charts in out/, and prints tables used in REPORT.md."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa
from plots import AMBER, GREEN, RED, WHITE, GREY, CYAN, PALETTE, plt, shade_oos, shade_shocks  # noqa

from optionsbot.runner import IS_END, OOS_START

FROZEN = json.loads((OUT / "frozen.json").read_text())
SHOCKS = {"Aug-2024 (yen carry)": ("2024-07-16", "2024-08-30"),
          "Jan-2025 (DeepSeek)": ("2025-01-17", "2025-02-14"),
          "Apr-2025 (tariffs)": ("2025-03-25", "2025-05-09")}


def window(eq, a, b):
    e = eq[a:b]
    if len(e) < 2:
        return {}
    prev = eq[:a]
    base = prev.iloc[-1] if len(prev) else e.iloc[0]
    e2 = pd.concat([pd.Series([base], index=[e.index[0] - pd.Timedelta(days=1)]), e])
    return {"ret": float(e2.iloc[-1] / e2.iloc[0] - 1), "max_dd": float(M.drawdown(e2).min())}


def ds(eq, step=3):
    e = eq.iloc[::step]
    if e.index[-1] != eq.index[-1]:
        e = pd.concat([e, eq.iloc[[-1]]])
    return e


def dash_metrics(eq, tr, lo=None, hi=None):
    seg = eq[lo:hi] if (lo or hi) else eq
    m = M.equity_metrics(seg)
    t = trade_stats(tr, lo, hi)
    return {"cagr": m["cagr"], "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
            "sharpe": m["sharpe"], "sortino": m["sortino"], "ann_vol": m["ann_vol"], "calmar": m["calmar"],
            "win_rate": t["win_rate"], "profit_factor": t["profit_factor"], "trades": t["trades"]}


def main():
    trials = pd.read_csv(OUT / "results.csv")
    srs_all = trials["is_sr_d"].dropna().values
    n_trials = int(len(trials))
    dash, detail = [], {}
    sens_rows = []
    bh = {s: bh_equity(s) for s in ("SPY", "QQQ", "IWM")}
    bh["PORT"] = sum(bh[s] / bh[s].iloc[0] for s in ("SPY", "QQQ", "IWM")) / 3 * 100_000
    guard_rows = []
    for fz in FROZEN:
        for cap in (10_000, 100_000):
            sym = fz["underlying"]
            eq, tr, x = run_variant(fz["params"], sym, cap, "default")
            fid = f"{fz['id']}:{sym}:{cap}"
            ev = evaluate(eq, tr, x)
            r_is = eq[:IS_END].pct_change().dropna()
            dsr, sr0 = deflated_sharpe(r_is.mean() / r_is.std(), len(r_is), r_is.skew(), r_is.kurt() + 3,
                                       srs_all, n_trials)
            r_oos = eq[OOS_START:].pct_change().dropna()
            psr_oos = psr(r_oos.mean() / r_oos.std(), len(r_oos), r_oos.skew(), r_oos.kurt() + 3)
            r_full = eq.pct_change().dropna()
            dsr_full, _ = deflated_sharpe(r_full.mean() / r_full.std(), len(r_full), r_full.skew(),
                                          r_full.kurt() + 3, srs_all, n_trials)
            dd = M.drawdown(eq)
            mret = M.monthly_returns(eq)
            t = tr[tr.reason != "open_at_end"] if len(tr) else tr
            worst = t.nsmallest(5, "pnl")[["underlying", "open_date", "close_date", "legs", "contracts",
                                           "pnl", "reason"]] if len(t) else pd.DataFrame()
            if len(worst):
                eq_at = eq.reindex(pd.to_datetime(worst.open_date), method="ffill").values
                worst = worst.assign(pnl_pct_equity=worst.pnl.values / eq_at)
            shocks = {k: {"strategy": window(eq, a, b), "spy_bh": window(bh["SPY"], a, b)}
                      for k, (a, b) in SHOCKS.items()}
            entry = {
                "id": fid, "name": fz["name"], "underlying": sym, "capital": cap,
                "metrics": dash_metrics(eq, tr), "is_metrics": dash_metrics(eq, tr, None, IS_END),
                "oos_metrics": dash_metrics(eq, tr, OOS_START, None),
                "beta_vs_spy": ev["beta_spy"], "beta_vs_spy_oos": ev["beta_spy_oos"],
                "deflated_sharpe_is": dsr, "deflated_sharpe_full": dsr_full, "psr_oos_vs_0": psr_oos,
                "n_trials": n_trials, "sr0_daily": sr0,
                "equity": [{"date": str(d.date()), "value": round(float(v), 2)} for d, v in ds(eq).items()],
                "drawdown": [{"date": str(d.date()), "value": round(float(v), 4)} for d, v in ds(dd).items()],
                "monthly_returns": [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 4)} for d, v in mret.items()],
                "params": fz["params"], "shocks": shocks,
                "exit_reasons": t.reason.value_counts().to_dict() if len(t) else {},
                "slippage": x.get("slippage"), "blocked_days": x.get("blocked_days"),
                "worst_trades": json.loads(worst.to_json(orient="records", date_format="iso")) if len(worst) else [],
                "avg_contracts": float(t.contracts.mean()) if len(t) else None,
                "avg_days_held": float(t.days_held.mean()) if len(t) else None,
            }
            dash.append(entry)
            detail[fid] = {"eq": eq, "dd": dd, "tr": tr}
            # BUGS #7 guard: before (no guard) vs after (credit <= 60% of width)
            e0, t0, x0 = run_variant({**fz["params"], "max_credit_frac": None}, sym, cap, "default")
            ev0 = evaluate(e0, t0, x0)
            mx = int(tr.contracts.max()) if len(tr) else 0
            mx0 = int(t0.contracts.max()) if len(t0) else 0
            guard_rows.append(dict(id=fz["id"], capital=cap, guard="off", full_cagr=ev0["full_cagr"],
                                   full_sharpe=ev0["full_sharpe"], is_sharpe=ev0["is_sharpe"],
                                   oos_sharpe=ev0["oos_sharpe"], trades=ev0["full_trades"], max_contracts=mx0))
            guard_rows.append(dict(id=fz["id"], capital=cap, guard="on", full_cagr=ev["full_cagr"],
                                   full_sharpe=ev["full_sharpe"], is_sharpe=ev["is_sharpe"],
                                   oos_sharpe=ev["oos_sharpe"], trades=ev["full_trades"], max_contracts=mx))
            # cost sensitivity
            for c in ("zero", "calib", "half", "default", "double", "calib_x2"):
                e2, t2, x2 = (eq, tr, x) if c == "default" else run_variant(fz["params"], sym, cap, c)
                ev2 = evaluate(e2, t2, x2)
                sens_rows.append(dict(id=fz["id"], capital=cap, costs=c, full_cagr=ev2["full_cagr"],
                                      full_sharpe=ev2["full_sharpe"], full_mdd=ev2["full_max_drawdown"],
                                      is_sharpe=ev2["is_sharpe"], oos_sharpe=ev2["oos_sharpe"],
                                      oos_cagr=ev2["oos_cagr"], slippage=x2.get("slippage")))
    (OUT / "finalists.json").write_text(json.dumps(dash, indent=1, default=float))
    pd.DataFrame(guard_rows).to_csv(OUT / "finalists_guard_check.csv", index=False)
    print(pd.DataFrame(guard_rows).round(3).to_string(index=False))
    sens = pd.DataFrame(sens_rows)
    sens.to_csv(OUT / "finalists_cost_sensitivity.csv", index=False)
    charts(dash, detail, bh, sens)
    # print summary
    pd.set_option("display.width", 250)
    rows = []
    for e in dash:
        for tag in ("is_metrics", "oos_metrics", "metrics"):
            m = e[tag]
            rows.append(dict(id=e["id"], seg=tag.replace("_metrics", "") if tag != "metrics" else "full",
                             cagr=m["cagr"], ret=m["total_return"], sharpe=m["sharpe"], sortino=m["sortino"],
                             mdd=m["max_drawdown"], vol=m["ann_vol"], trades=m["trades"], win=m["win_rate"],
                             pf=m["profit_factor"]))
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    print(sens.round(3).to_string(index=False))
    for e in dash:
        print(e["id"], "beta", round(e["beta_vs_spy"], 3), "DSR_IS", round(e["deflated_sharpe_is"], 3),
              "DSR_full", round(e["deflated_sharpe_full"], 3), "PSR_OOS", round(e["psr_oos_vs_0"], 3),
              "slip", e["slippage"], "blocked", e["blocked_days"], "exits", e["exit_reasons"],
              "avg_n", e["avg_contracts"])
        print("  shocks", json.dumps({k: {kk: {a: round(b, 4) for a, b in vv.items()} for kk, vv in v.items()}
                                      for k, v in e["shocks"].items()}))
        for w in e["worst_trades"]:
            print("  worst", w["open_date"][:10], w["close_date"][:10], w["reason"], w["contracts"], round(w["pnl"]),
                  f"{w['pnl_pct_equity']:.2%}", w["legs"][:90])


def charts(dash, detail, bh, sens):
    out = OUT / "out"
    out.mkdir(exist_ok=True)
    for e in dash:
        d = detail[e["id"]]
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
        eq = d["eq"]
        a1.plot(eq.index, eq.values, color=AMBER, lw=1.4, label=e["name"])
        b = bh[e["underlying"]]
        b = b.reindex(eq.index).ffill()
        a1.plot(b.index, b / b.iloc[0] * eq.iloc[0], color=GREY, lw=1.0, label=f"{e['underlying']} buy & hold")
        shade_shocks(a1)
        shade_oos(a1)
        m, mi, mo = e["metrics"], e["is_metrics"], e["oos_metrics"]
        a1.set_title(f"{e['id']}  |  full CAGR {m['cagr']:+.1%}  Sharpe {m['sharpe']:.2f}  MDD {m['max_drawdown']:.1%}"
                     f"  |  IS Sharpe {mi['sharpe']:.2f}  OOS Sharpe {mo['sharpe']:.2f}", loc="left")
        a1.legend(loc="upper left")
        a1.set_ylabel("equity $")
        dd = d["dd"]
        a2.fill_between(dd.index, dd.values * 100, 0, color=RED, alpha=0.6, lw=0)
        bdd = M.drawdown(b)
        a2.plot(bdd.index, bdd.values * 100, color=GREY, lw=0.8)
        a2.set_ylabel("drawdown %")
        shade_oos(a2)
        fig.tight_layout()
        fig.savefig(out / f"equity_{e['id'].replace(':', '_')}.png", dpi=130)
        plt.close(fig)
    # summary: all $100k finalists vs SPY buy & hold, equity (normalised) + drawdown
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]})
    b = bh["SPY"]
    a1.plot(b.index, b / b.iloc[0], color=GREY, lw=1.0, label="SPY buy & hold")
    a2.plot(b.index, M.drawdown(b) * 100, color=GREY, lw=0.8)
    for i, e in enumerate([e for e in dash if e["capital"] == 100_000]):
        eq = detail[e["id"]]["eq"]
        col = PALETTE[i % len(PALETTE)]
        a1.plot(eq.index, eq / eq.iloc[0], color=col, lw=1.3,
                label=f"{e['id']}  OOS Sharpe {e['oos_metrics']['sharpe']:.2f}")
        a2.plot(eq.index, detail[e["id"]]["dd"] * 100, color=col, lw=1.0)
    shade_shocks(a1)
    shade_oos(a1)
    shade_oos(a2)
    a1.set_title("SPREAD FINALISTS ($100k, default costs) vs SPY", loc="left")
    a1.legend(loc="upper left", fontsize=7.5)
    a1.set_ylabel("growth of $1")
    a2.set_ylabel("drawdown %")
    fig.tight_layout()
    fig.savefig(out / "finalists.png", dpi=130)
    plt.close(fig)
    # IS vs OOS bars
    ids = [e["id"] for e in dash]
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.2))
    for ax, key, lab in zip(axs, ("sharpe", "cagr", "max_drawdown"), ("Sharpe", "CAGR", "max drawdown")):
        xs = np.arange(len(ids))
        iv = [e["is_metrics"][key] for e in dash]
        ov = [e["oos_metrics"][key] for e in dash]
        ax.bar(xs - 0.2, iv, 0.4, color=AMBER, label="in-sample")
        ax.bar(xs + 0.2, ov, 0.4, color=GREEN, label="out-of-sample")
        ax.set_xticks(xs)
        ax.set_xticklabels([i.replace(":", "\n") for i in ids], fontsize=6.5)
        ax.set_title(lab, loc="left")
        ax.axhline(0, color=WHITE, lw=0.6)
    axs[0].legend()
    fig.tight_layout()
    fig.savefig(out / "is_vs_oos.png", dpi=130)
    plt.close(fig)
    # cost sensitivity
    fig, ax = plt.subplots(figsize=(10, 4))
    order = ["zero", "calib", "half", "default", "double", "calib_x2"]
    for i, (k, g) in enumerate(sens[sens.capital == 100_000].groupby("id")):
        g = g.set_index("costs").reindex(order)
        ax.plot(order, g.full_sharpe.values, marker="o", color=PALETTE[i % len(PALETTE)], label=k)
    ax.axhline(0, color=WHITE, lw=0.6)
    ax.set_title("full-period Sharpe vs slippage model ($100k)", loc="left")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "cost_sensitivity.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
