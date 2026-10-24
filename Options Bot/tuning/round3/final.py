"""Round-3 wrap-up: candidate configurations vs the live book on the proxy (2016-10..2026-10, $15k,
integer contracts), deflated Sharpe against every round-3 trial, stress of the candidates, charts.
Writes out/final_candidates.csv, out/final_dsr.csv, out/final_stress.csv, out/*.png."""
import json
import multiprocessing as mp

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import book as BK  # noqa: E402
import psim as P  # noqa: E402
import q5_stress as S  # noqa: E402
import rlib as R  # noqa: E402
from common import OUT, WORK  # noqa: E402

C = {"live": "#2a78d6", "c1": "#eb6834", "c2": "#1baf7a", "c3": "#6250d6", "spy": "#52514e"}
CANDS = {
    "live (condor 20% + dip SPY/IWM 25%, 50% total)": dict(),
    "dip SPY+IWM, no condor (25%/50%)": dict(condor=False),
    "dip SPY only 25%, no condor": dict(condor=False, iwm=None),
    "dip SPY only 15%, no condor": dict(condor=False, iwm=None, dip_kw=dict(risk_frac=0.15)),
    "dip SPY only 10%, no condor": dict(condor=False, iwm=None, dip_kw=dict(risk_frac=0.10)),
    "dip SPY only 10%, 25/05 wing": dict(condor=False, iwm=None, dip_kw=dict(risk_frac=0.10, long_delta=0.05)),
    "dip SPY+IWM 5% each, 10% total, no condor": dict(condor=False, dip_kw=dict(risk_frac=0.05), risk_total=0.10),
    "dip SPY+IWM 10%/20%, 25/05 wing": dict(condor=False, dip_kw=dict(risk_frac=0.10, long_delta=0.05),
                                           risk_total=0.20),
}
STRESS_CFG = {"live": dict(),
              "SPY dip 10%, no condor": dict(condor_on=False, dip=dict(risk_frac=0.10), spy_only=True),
              "SPY+IWM dip 5%/10%, no condor": dict(condor_on=False, dip=dict(risk_frac=0.05), risk_total=0.10)}


def cand_job(name):
    kw = dict(CANDS[name])
    rt = kw.pop("risk_total", 0.5)
    bk = BK.run_book(15_000, risk_total=rt, **kw)
    eq = bk.equity_series()
    t = bk.trades()
    t = t[t.reason != "open"]
    m = P.metrics(eq)
    ev = R.event_table(eq)
    row = {"config": name, **m, "is_sharpe": P.metrics(eq[:"2020-12-31"])["sharpe"],
           "oos_sharpe": P.metrics(eq["2021-01-01":])["sharpe"],
           "real_overlap_sharpe(2024-03..)": P.metrics(eq["2024-03-01":])["sharpe"],
           "trades": len(t), "win": float((t.pnl > 0).mean()), "worst_trade_$": float(t.pnl.min())}
    for k, v in ev["max_dd"].items():
        row[f"dd {k}"] = v
    yrs = R.per_year(eq)
    return row, eq, yrs


def n_trials():
    n = {}
    for f in ("q2_A", "q2_B", "q3_A", "q3_B", "q3_C"):
        p = WORK / f"{f}.jsonl"
        n[f] = sum(1 for _ in p.open()) if p.exists() else 0
    n["q1 runs"] = 14
    n["q3 names"] = 12
    n["q4 hedges"] = 60
    n["q5 caps"] = 26
    n["final candidates"] = len(CANDS)
    return n


if __name__ == "__main__":
    with mp.get_context("fork").Pool(min(7, len(CANDS))) as pool:
        res = pool.map(cand_job, list(CANDS))
    rows = [r for r, _, _ in res]
    df = pd.DataFrame(rows)
    nt = n_trials()
    N = sum(nt.values())
    # trial-Sharpe variance from the grids (daily Sharpe units)
    allsr = []
    for f in ("q2_A", "q2_B", "q3_A", "q3_B", "q3_C"):
        p = WORK / f"{f}.jsonl"
        if p.exists():
            allsr += [json.loads(x)["full_sharpe"] for x in p.open()]
    allsr = np.array([x for x in allsr if x is not None and np.isfinite(x)]) / np.sqrt(252)
    v = float(np.var(allsr))
    dsr = []
    for (r, eq, _) in res:
        rr = eq.pct_change().dropna()
        dsr.append({"config": r["config"], "sharpe": r["sharpe"], "n_days": len(rr), "skew": float(rr.skew()),
                    "kurt": float(rr.kurt() + 3),
                    "PSR(>0)": R.deflated_sharpe(r["sharpe"], len(rr), 1, float(rr.skew()), float(rr.kurt() + 3)),
                    f"DSR N={N}": R.deflated_sharpe(r["sharpe"], len(rr), N, float(rr.skew()), float(rr.kurt() + 3),
                                                    sr_var_trials=v)})
    dd = pd.DataFrame(dsr)
    df.to_csv(OUT / "final_candidates.csv", index=False)
    dd.to_csv(OUT / "final_dsr.csv", index=False)
    json.dump(nt, open(OUT / "trials.json", "w"), indent=1)
    pd.DataFrame({r["config"]: y for r, _, y in res}).to_csv(OUT / "final_years.csv")
    pd.set_option("display.width", 300)
    print(df.round(3).to_string())
    print(dd.round(3).to_string())
    print("trials", nt, "total", N)

    # stress of candidates (fully loaded at the worst time)
    srows = []
    for cname, cfg in STRESS_CFG.items():
        for E, lab in S.REPLAYS.items():
            bk = S.forced_book(E, dict(cfg))
            eq = bk.equity_series()
            srows.append({"config": cname, "event": lab, "worst_close_pct": eq.min() / 15_000 - 1,
                          "end_pct": eq.iloc[-1] / 15_000 - 1})
    st = pd.DataFrame(srows)
    st.to_csv(OUT / "final_stress.csv", index=False)
    print(st.pivot(index="event", columns="config", values="worst_close_pct").round(3).to_string())

    # ---- charts
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#e6e5e0", "grid.linewidth": 0.6})
    fig, ax = plt.subplots(figsize=(9, 4.2))
    cols = [C["live"], C["c1"], C["c2"], C["c3"]]
    keys = ["live (condor 20% + dip SPY/IWM 25%, 50% total)", "dip SPY+IWM, no condor (25%/50%)",
            "dip SPY only 10%, no condor", "dip SPY only 10%, 25/05 wing"]
    for k, c in zip(keys, cols):
        eq = next(e for r, e, _ in res if r["config"] == k)
        ax.plot(eq.index, eq / 15_000, color=c, lw=1.6, label=k)
    ax.set_yscale("log")
    ax.set_ylabel("equity / $15k (log)")
    ax.set_title("Proxy-chain backtest of the options book, Oct-2016 to Oct-2026 ($15k, integer contracts)",
                 loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT / "book_equity_2016_2026.png", dpi=140)

    a = pd.concat([pd.read_json(WORK / "q2_A.jsonl", lines=True), pd.read_json(WORK / "q2_B.jsonl", lines=True)])
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    ax.scatter(a.is_sharpe, a.oos_sharpe, s=9, color=C["live"], alpha=0.45, linewidths=0, label="grid config")
    lv = a[(a.delta == "10/10") & (a.wing == 0.03) & (a.dte == 60) & (a.mgmt == "hold") & (a.filter == "none")]
    ax.scatter(lv.is_sharpe, lv.oos_sharpe, s=60, color=C["c1"], label="live F1", zorder=3)
    ax.axhline(0, color="#8a8984", lw=0.8)
    ax.axvline(0, color="#8a8984", lw=0.8)
    ax.set_xlabel("IS Sharpe 1990-2009")
    ax.set_ylabel("OOS Sharpe 2010-2026")
    ax.set_title(f"Iron condor grid, {len(a)} configs (SPX proxy)", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "condor_grid_is_oos.png", dpi=140)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    pv = st.pivot(index="event", columns="config", values="worst_close_pct")
    order = pv["live"].sort_values().index
    pv = pv.loc[order]
    y = np.arange(len(pv))
    for j, (cn, c) in enumerate(zip(pv.columns, [C["live"], C["c2"], C["c3"]])):
        ax.barh(y + (j - 1) * 0.27, pv[cn] * 100, height=0.25, color=c, label=cn)
    ax.set_yticks(y)
    ax.set_yticklabels(pv.index, fontsize=8)
    ax.set_xlabel("worst close vs entry equity, % (book fully loaded the day before)")
    ax.set_title("Stress replay: options account loss in gap events", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT / "stress_replay.png", dpi=140)
