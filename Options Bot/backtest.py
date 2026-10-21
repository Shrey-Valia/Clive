#!/usr/bin/env python3
"""Options-strategy backtester CLI (Alpaca historical data; data-only, no orders).

    python3 backtest.py                       # everything: main, calibration, sensitivity, dashboard
    python3 backtest.py --steps main,dashboard --symbols SPY,QQQ --workers 3
    python3 backtest.py --steps sensitivity --grid-symbols SPY,QQQ,IWM

Outputs go to results/ (see README).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "results"
UNIVERSE = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
CAPITALS = [10_000, 100_000]
START, END = "2024-03-01", "2026-10-20"
MAIN_STRATEGIES = [
    "buy_hold", "covered_call", "csp", "wheel", "put_credit_spread", "iron_condor", "collar",
    "pmcc", "trend_pcs", "regime_pcs", "vix_pcs", "iron_condor_hold", "pmcc_sma50",
    "csp_kill", "pcs_kill", "iron_condor_kill", "put_write_kill",
]


def _clean(o):
    """JSON-safe floats (NaN/inf -> None), numpy -> python."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return str(o.date())
    return o


# ---------------------------------------------------------------- workers
def _worker_symbol(args):
    sym, strategies, capitals, start, end, interval = args
    os.environ["OPTIONSBOT_MIN_INTERVAL"] = str(interval)
    from optionsbot.runner import run_one
    from optionsbot import metrics as M
    out = []
    for cap in capitals:
        bench = run_one("buy_hold", None, sym, start, end, cap)
        beq = bench["equity"]["equity"]
        for s in strategies:
            t0 = time.time()
            r = bench if s == "buy_hold" else run_one(s, None, sym, start, end, cap)
            r["metrics"]["vs_buy_hold"] = M.compare(r["equity"]["equity"], beq)
            out.append({"strategy": s, "underlying": sym, "capital": cap,
                        "name": type(r["engine"].strategy).name, "params": r["params"],
                        "metrics": r["metrics"], "notes": r["notes"], "infeasible": r["infeasible"],
                        "equity": r["equity"][["equity", "cash", "delta_dollars", "capital_at_risk",
                                               "n_positions", "stale_legs"]],
                        "trades": r["trades"]})
            print(f"  {sym:5s} {cap:>7,} {s:18s} TR {r['metrics'].get('total_return', 0):+7.1%} "
                  f"SR {r['metrics'].get('sharpe', float('nan')):5.2f} n={r['metrics'].get('n_trades')} "
                  f"{time.time() - t0:4.0f}s", flush=True)
    return out


def step_main(symbols, strategies, capitals, workers):
    print(f"[main] {len(strategies)} strategies x {len(symbols)} symbols x {len(capitals)} capitals")
    from optionsbot.runner import market
    market("SPY")   # builds/caches the VIX proxy once before forking
    interval = 0.32 * max(1, workers)
    tasks = [(s, strategies, capitals, START, END, interval) for s in symbols]
    results = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(_worker_symbol, tasks):
                results += res
    else:
        for t in tasks:
            results += _worker_symbol(t)
    write_main(results)
    return results


def write_main(results):
    RESULTS.mkdir(exist_ok=True)
    by_s = {}
    for r in results:
        by_s.setdefault(r["strategy"], []).append(r)
    runs = []
    for s, rs in by_s.items():
        d = RESULTS / s
        d.mkdir(exist_ok=True)
        eq = pd.concat({f"{r['underlying']}_{r['capital']}": r["equity"]["equity"] for r in rs}, axis=1)
        eq.index.name = "date"
        eq.round(2).to_csv(d / "equity_curves.csv")
        tr = [r["trades"] for r in rs if r["trades"] is not None and len(r["trades"])]
        (pd.concat(tr) if tr else pd.DataFrame()).to_csv(d / "trades.csv", index=False)
        for r in rs:
            runs.append({k: r[k] for k in ("strategy", "name", "underlying", "capital", "params",
                                           "metrics", "notes", "infeasible")})
    # full per-run payload kept for the dashboard step
    (RESULTS / "runs.json").write_text(json.dumps(_clean(runs)))


# ------------------------------------------------------------ calibration
CBOE = {"cal_bxm": ("BXM", {2024: 0.201, 2025: 0.089}),
        "cal_bxmd": ("BXMD", {2024: 0.238, 2025: 0.121}),
        "cal_put": ("PUT", {2024: 0.178, 2025: 0.092})}


def step_calibration():
    """SPY clones of Cboe BXM / BXMD / PUT on $1M (to remove lot rounding),
    calendar-year returns, with and without T-bill yield on cash."""
    from optionsbot.runner import run_one
    print("[calibration]")
    rows = []
    for s in ["buy_hold", "cal_bxm", "cal_bxmd", "cal_put"]:
        for cy in (False, True):
            r = run_one(s, None, "SPY", "2024-01-19", END, 1_000_000, cash_yield=cy)
            eq = r["equity"]["equity"]
            ye = eq.resample("YE").last()
            yr = {2024: ye.iloc[0] / eq.iloc[0] - 1, 2025: ye.loc["2025"].iloc[0] / ye.iloc[0] - 1,
                  "2026ytd": eq.iloc[-1] / ye.loc["2025"].iloc[0] - 1}
            idx, ref = CBOE.get(s, ("SPY total return", {}))
            for y, v in yr.items():
                rows.append({"clone": s, "cboe_index": idx, "cash_yield": cy, "year": str(y),
                             "clone_return": round(float(v), 4),
                             "cboe_return": ref.get(y), "gap_pp": (round(100 * (float(v) - ref[y]), 2)
                                                                   if y in ref else None),
                             "avg_net_delta": round(r["metrics"]["avg_net_delta_pct"], 3),
                             "n_trades": r["metrics"].get("n_trades")})
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "calibration.csv", index=False)
    print(df.to_string())
    return df


# ------------------------------------------------------------ sensitivity
def grid_specs():
    specs = []
    for sd, (dmin, dmax, dt), tp, wing, filt in itertools.product(
            [0.16, 0.20, 0.25, 0.30], [(30, 45, 38), (38, 52, 45)], [0.5, None],
            ["delta10", "abs5"], [None, "sma200", "sma200_vix", "kill"]):
        p = dict(short_delta=sd, dte_min=dmin, dte_max=dmax, dte_target=dt, tp=tp)
        p.update({"long_delta": 0.10} if wing == "delta10" else {"width_abs": 5.0})
        if filt == "kill":
            p["kill_switch"] = True
        else:
            p["filter"] = filt
        specs.append(("put_credit_spread", p, f"sd={sd} dte={dt} tp={tp} wing={wing} filt={filt}"))
    for sd, (dmin, dmax, dt), tp, wing in itertools.product(
            [0.12, 0.16, 0.20], [(30, 45, 38), (38, 52, 45)], [0.5, None], ["delta05", "pct3"]):
        p = dict(short_delta=sd, dte_min=dmin, dte_max=dmax, dte_target=dt, tp=tp)
        p.update({"long_delta": 0.05} if wing == "delta05" else {"width_pct": 0.03})
        specs.append(("iron_condor", p, f"sd={sd} dte={dt} tp={tp} wing={wing}"))
    for pd_, (dmin, dmax, dt), tp in itertools.product(
            [0.20, 0.30, 0.40], [(30, 45, 38), (38, 52, 45)], [0.5, 0.75, None]):
        specs.append(("csp", dict(put_delta=pd_, dte_min=dmin, dte_max=dmax, dte_target=dt, tp=tp),
                      f"pd={pd_} dte={dt} tp={tp}"))
    return specs


def _worker_grid(args):
    sym, specs, cap, interval = args
    os.environ["OPTIONSBOT_MIN_INTERVAL"] = str(interval)
    from optionsbot.runner import run_one
    rows = []
    for strat, p, label in specs:
        r = run_one(strat, p, sym, START, END, cap)
        m = r["metrics"]
        ins, oos = m.get("in_sample", {}), m.get("out_of_sample", {})
        rows.append({"strategy": strat, "label": label, "underlying": sym, "capital": cap,
                     "params": json.dumps(p, sort_keys=True),
                     "sharpe": m.get("sharpe"), "cagr": m.get("cagr"), "max_dd": m.get("max_drawdown"),
                     "n_trades": m.get("n_trades"), "win_rate": m.get("win_rate"),
                     "is_sharpe": ins.get("sharpe"), "is_cagr": ins.get("cagr"), "is_max_dd": ins.get("max_drawdown"),
                     "oos_sharpe": oos.get("sharpe"), "oos_cagr": oos.get("cagr"),
                     "oos_max_dd": oos.get("max_drawdown")})
    print(f"  grid {sym} done ({len(rows)} runs)", flush=True)
    return rows


def step_sensitivity(symbols, workers, cap=100_000):
    specs = grid_specs()
    print(f"[sensitivity] {len(specs)} param sets x {len(symbols)} symbols")
    from optionsbot.runner import market
    market("SPY")
    interval = 0.32 * max(1, workers)
    # split each symbol's specs into chunks so all workers stay busy
    chunks = max(1, workers // max(1, len(symbols)) + 1)
    tasks = []
    for s in symbols:
        for k in range(chunks):
            tasks.append((s, specs[k::chunks], cap, interval))
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(_worker_grid, tasks):
            rows += res
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "sensitivity.csv", index=False)
    return df


def sensitivity_summary(df: pd.DataFrame) -> dict:
    """Pick the best param set per strategy by mean IN-SAMPLE Sharpe across
    the grid universe, then report its OUT-OF-SAMPLE Sharpe next to the
    default's and the IS->OOS rank correlation (overfitting check)."""
    out = {}
    defaults = {"put_credit_spread": "sd=0.25 dte=38 tp=0.5 wing=delta10 filt=None",
                "iron_condor": "sd=0.16 dte=45 tp=0.5 wing=delta05",
                "csp": "pd=0.3 dte=38 tp=0.5"}
    for s, g in df.groupby("strategy"):
        agg = g.groupby("label")[["is_sharpe", "oos_sharpe", "sharpe", "cagr", "max_dd"]].mean()
        best = agg["is_sharpe"].idxmax()
        rho = agg["is_sharpe"].rank().corr(agg["oos_sharpe"].rank())
        d = defaults.get(s)
        out[s] = {"n_param_sets": int(len(agg)), "best_in_sample": best,
                  "best_is_sharpe": agg.at[best, "is_sharpe"], "best_oos_sharpe": agg.at[best, "oos_sharpe"],
                  "default": d,
                  "default_is_sharpe": agg.at[d, "is_sharpe"] if d in agg.index else None,
                  "default_oos_sharpe": agg.at[d, "oos_sharpe"] if d in agg.index else None,
                  "oos_rank_of_is_best": int(agg["oos_sharpe"].rank(ascending=False)[best]),
                  "is_oos_rank_corr": rho,
                  "median_oos_sharpe_all_sets": agg["oos_sharpe"].median(),
                  "top5_by_oos": agg["oos_sharpe"].nlargest(5).round(3).to_dict()}
    return _clean(out)


# -------------------------------------------------------------- summary
FLAT_KEYS = ["total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown",
             "max_dd_duration_days", "calmar", "n_trades", "win_rate", "avg_win", "avg_loss",
             "profit_factor", "worst_trade", "pct_time_in_market", "avg_capital_at_risk_pct",
             "avg_net_delta_pct", "pct_trades_with_stale_marks", "pct_theo_entries",
             "pct_days_with_stale_legs", "n_assigned", "n_early_assign", "kill_events",
             "blocked_days", "fees", "slippage", "dividends"]


def step_summary():
    runs = json.loads((RESULTS / "runs.json").read_text())
    table = []
    for r in runs:
        m = r["metrics"]
        row = {"strategy": r["strategy"], "underlying": r["underlying"], "capital": r["capital"],
               "infeasible": r["infeasible"], **{k: m.get(k) for k in FLAT_KEYS},
               "is_sharpe": (m.get("in_sample") or {}).get("sharpe"),
               "oos_sharpe": (m.get("out_of_sample") or {}).get("sharpe"),
               "is_cagr": (m.get("in_sample") or {}).get("cagr"),
               "oos_cagr": (m.get("out_of_sample") or {}).get("cagr"),
               "excess_vs_bh": (m.get("vs_buy_hold") or {}).get("excess_total_return_vs_bh"),
               "beta_vs_bh": (m.get("vs_buy_hold") or {}).get("beta_vs_bh"),
               "notes": "; ".join(r["notes"])}
        table.append(row)
    t = pd.DataFrame(table)
    t.to_csv(RESULTS / "summary_table.csv", index=False)
    feas = t[~t["infeasible"].astype(bool)]
    agg = (feas.groupby(["strategy", "capital"])
           .agg(n_underlyings=("underlying", "count"), median_cagr=("cagr", "median"),
                median_sharpe=("sharpe", "median"), median_max_dd=("max_drawdown", "median"),
                worst_max_dd=("max_drawdown", "min"), median_is_sharpe=("is_sharpe", "median"),
                median_oos_sharpe=("oos_sharpe", "median"), median_excess_vs_bh=("excess_vs_bh", "median"),
                median_win_rate=("win_rate", "median"), median_net_delta=("avg_net_delta_pct", "median"))
           .reset_index().sort_values(["capital", "median_sharpe"], ascending=[True, False]))
    infeas = t[t["infeasible"].astype(bool)][["strategy", "underlying", "capital", "notes"]]
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": {"start": START, "end": END, "in_sample": [START, "2025-06-30"],
                   "out_of_sample": ["2025-07-01", END]},
        "universe": sorted(t["underlying"].unique().tolist()), "capitals": CAPITALS,
        "assumptions": ASSUMPTIONS,
        "aggregate_by_strategy": agg.to_dict("records"),
        "runs": table,
        "infeasible_runs": infeas.to_dict("records"),
    }
    cal = RESULTS / "calibration.csv"
    if cal.exists():
        summary["calibration"] = pd.read_csv(cal).to_dict("records")
    cost = RESULTS / "cost_sensitivity.csv"
    if cost.exists():
        summary["cost_sensitivity"] = pd.read_csv(cost).to_dict("records")
    sens = RESULTS / "sensitivity.csv"
    if sens.exists():
        summary["sensitivity"] = sensitivity_summary(pd.read_csv(sens))
    (RESULTS / "summary.json").write_text(json.dumps(_clean(summary), indent=1))
    print(agg.to_string())
    return summary


ASSUMPTIONS = {
    "fills": "daily close; buy at bar close + max($0.03, 2%) capped $0.25/leg, sell at close - same",
    "fees": "$0 commission; $0.03/contract regulatory pass-through; SEC fee on stock sales; 1bp stock slippage",
    "marks": "option daily bar close (trade prints); if no print, Black-Scholes at last inferred IV (flagged stale)",
    "greeks": "IV inferred by inverting Black-Scholes (European, dividend yield) on bar close vs underlying close",
    "rates": "piecewise fed-funds approximation (pricing.risk_free); idle cash earns 0% unless cash_yield=True",
    "settlement": "ITM at expiry settles at intrinsic on underlying close; wheel takes physical delivery",
    "early_assignment": "short ITM calls with time value < next-day dividend assumed exercised the day before ex-div",
    "capital": "fully cash-secured; no naked options; spreads reserve width*100*n; no margin",
    "splits": "no option position may span a stock split (OCC symbols are re-used after splits)",
}


# ------------------------------------------------------------ dashboard
def _downsample(s: pd.Series, n: int = 260) -> pd.Series:
    if len(s) <= n:
        return s
    step = int(math.ceil(len(s) / n))
    idx = list(range(0, len(s), step))
    if idx[-1] != len(s) - 1:
        idx.append(len(s) - 1)
    return s.iloc[idx]


def step_dashboard():
    from optionsbot import metrics as M
    runs = {(r["strategy"], r["underlying"], r["capital"]): r
            for r in json.loads((RESULTS / "runs.json").read_text())}
    keep = ["total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown",
            "max_dd_duration_days", "win_rate", "avg_win", "avg_loss", "profit_factor",
            "worst_trade", "pct_time_in_market", "avg_net_delta_pct", "avg_capital_at_risk_pct",
            "n_trades"]
    strategies = []
    bench = None
    for s in sorted({k[0] for k in runs}):
        f = RESULTS / s / "equity_curves.csv"
        if not f.exists():
            continue
        eqdf = pd.read_csv(f, index_col=0, parse_dates=True)
        for col in eqdf.columns:
            sym, cap = col.rsplit("_", 1)
            r = runs.get((s, sym, int(cap)))
            if r is None:
                continue
            eq = eqdf[col].dropna()
            dd = M.drawdown(eq)
            mr = M.monthly_returns(eq)
            m = r["metrics"]
            entry = {
                "id": f"{s}:{sym}:{cap}", "strategy": s, "name": r["name"], "underlying": sym,
                "capital": int(cap), "infeasible": r["infeasible"], "notes": r["notes"],
                "metrics": {**{k: m.get(k) for k in keep},
                            "is_sharpe": (m.get("in_sample") or {}).get("sharpe"),
                            "oos_sharpe": (m.get("out_of_sample") or {}).get("sharpe"),
                            "excess_vs_bh": (m.get("vs_buy_hold") or {}).get("excess_total_return_vs_bh")},
                "equity": [{"date": str(d.date()), "value": int(round(float(v)))}
                           for d, v in (_downsample(eq) if not r["infeasible"] else eq.iloc[[0, -1]]).items()],
                "drawdown": [{"date": str(d.date()), "value": round(float(v), 4)}
                             for d, v in (_downsample(dd) if not r["infeasible"] else dd.iloc[[0, -1]]).items()],
                "monthly_returns": [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 4)}
                                    for d, v in mr.items()],
                "trades_count": m.get("n_trades", 0),
            }
            strategies.append(entry)
            if s == "buy_hold" and sym == "SPY" and int(cap) == 100_000:
                bench = {k: entry[k] for k in ("id", "name", "underlying", "capital", "metrics",
                                               "equity", "drawdown", "monthly_returns")}
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "period": {"start": START, "end": END}, "strategies": strategies, "benchmark": bench}
    summ = RESULTS / "summary.json"
    if summ.exists():
        sj = json.loads(summ.read_text())
        out["aggregate_by_strategy"] = sj.get("aggregate_by_strategy")
        out["calibration"] = sj.get("calibration")
    txt = json.dumps(_clean(out), separators=(",", ":"))
    full = {e["id"]: (e["equity"], e["drawdown"]) for e in out["strategies"]}
    n = 200
    while len(txt) > 1_950_000 and n > 30:   # keep < 2 MB: thin the curves
        n = int(n * 0.75)
        for e in out["strategies"]:
            eqs, dds = full[e["id"]]
            k = max(1, math.ceil(len(eqs) / n))
            e["equity"] = eqs[::k] + ([eqs[-1]] if (len(eqs) - 1) % k else [])
            e["drawdown"] = dds[::k] + ([dds[-1]] if (len(dds) - 1) % k else [])
        txt = json.dumps(_clean(out), separators=(",", ":"))
    (RESULTS / "dashboard.json").write_text(txt)
    print(f"[dashboard] {len(strategies)} series, {len(txt) / 1e6:.2f} MB")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", default="main,calibration,sensitivity,summary,dashboard")
    ap.add_argument("--symbols", default=",".join(UNIVERSE))
    ap.add_argument("--strategies", default=",".join(MAIN_STRATEGIES))
    ap.add_argument("--capitals", default=",".join(str(c) for c in CAPITALS))
    ap.add_argument("--grid-symbols", default="SPY,QQQ,IWM")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    steps = a.steps.split(",")
    t0 = time.time()
    if "main" in steps:
        step_main(a.symbols.split(","), a.strategies.split(","),
                  [int(c) for c in a.capitals.split(",")], a.workers)
    if "calibration" in steps:
        step_calibration()
    if "sensitivity" in steps:
        step_sensitivity(a.grid_symbols.split(","), a.workers)
    if "summary" in steps:
        step_summary()
    if "dashboard" in steps:
        step_dashboard()
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
