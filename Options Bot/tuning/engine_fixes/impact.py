"""Before/after impact of the core-engine fixes on the published finalists.

Run from the Options Bot folder (single process, cache first, light fetching):
    python3 tuning/engine_fixes/impact.py            # runs / resumes, then writes impact.csv

Scenarios
  legacy      every fix off: engine.LEGACY_MONTHLY, max_credit_frac=None,
              kill_loss_basis="notional", Wheel call_basis_fallback=True,
              WheelX call_basis="engine" (the published configs)
  monthly     only the is_monthly fix (#11) on, everything else legacy
  kill_only   legacy + kill switch on structural max loss (#2)          [kill variants]
  fixed       all default-on fixes: #1 credit guard, #2 kill basis, #3 strict basis
              (WheelX F1/F2 -> call_basis="strict"), #11 monthly rule
  fixed_lag1  fixed + Engine(signal_lag=1): every decision filled at the next close
  fixed_hold  fixed + CoveredCall(roll_itm=False)                       [core covered call]
Trade stats are option-leg stats (as published); the *_sh columns add the share
episodes (#4, runner metrics['trade_stats_with_shares']).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "1.0")

import pandas as pd  # noqa: E402

from optionsbot import engine as E  # noqa: E402
from optionsbot import metrics as M  # noqa: E402
from optionsbot import runner  # noqa: E402
from optionsbot.strategies import REGISTRY  # noqa: E402
from optionsbot.strategies_signals import SIGNAL_REGISTRY  # noqa: E402
from optionsbot.strategies_spreads import SpreadBook  # noqa: E402
from optionsbot.strategies_wheel import WHEEL_REGISTRY  # noqa: E402

START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"
UNIV = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
ETFS = ["SPY", "QQQ", "IWM"]
RAW = HERE / "impact_raw.jsonl"
OUT = HERE / "impact.csv"
T = ROOT / "tuning"


def _strip(p):
    return {k: v for k, v in p.items() if k not in ("strategy", "family", "selection")}


def published():
    """Published metrics keyed by (finalist, symbol, capital)."""
    pub = {}
    for f in json.loads((T / "signals/finalists.json").read_text()):
        if f["id"] in ("sig_dip_pcs_spy", "sig_dip_pcs_qqq"):
            pub[("dip_pcs", f["underlying"], 10_000)] = f["metrics"]
    for f in json.loads((T / "spreads/finalists.json").read_text()):
        if f["id"].startswith("F1_ic10"):
            pub[("spreads_F1_ic", "SPY", f["capital"])] = f["metrics"]
    for f in json.loads((T / "wheel/finalists.json").read_text())["finalists"]:
        if f["id"] in ("F1_wheel_qqq", "F2_cc_qqq"):
            pub[("wheel_" + f["id"].split("_")[0], "QQQ", f["capital"])] = f["metrics"]
    st = pd.read_csv(ROOT / "results/summary_table.csv")
    for _, r in st.iterrows():
        pub[(r["strategy"], r["underlying"], int(r["capital"]))] = {
            "cagr": r["cagr"], "sharpe": r["sharpe"], "max_drawdown": r["max_drawdown"], "trades": r["n_trades"]}
    return pub


def finalist_params():
    sig = next(f for f in json.loads((T / "signals/finalists.json").read_text()) if f["id"] == "sig_dip_pcs_spy")
    spr = next(f for f in json.loads((T / "spreads/finalists.json").read_text()) if f["id"].startswith("F1_ic10"))
    wh = {f["id"]: f for f in json.loads((T / "wheel/finalists.json").read_text())["finalists"]}
    return {"dip": _strip(sig["params"]), "ic": dict(spr["params"]),
            "wF1": _strip(wh["F1_wheel_qqq"]["params"]), "wF2": _strip(wh["F2_cc_qqq"]["params"])}


def jobs():
    fp = finalist_params()
    out = []
    S4 = ("legacy", "monthly", "fixed", "fixed_lag1")
    for sym in ETFS:
        for sc in S4:
            out.append(("dip_pcs", SIGNAL_REGISTRY["dip_pcs"], fp["dip"], sym, 10_000, sc))
    for cap in (10_000, 100_000):
        for sc in S4:
            out.append(("spreads_F1_ic", SpreadBook, fp["ic"], "SPY", cap, sc))
    for name, key in (("wheel_F1", "wF1"), ("wheel_F2", "wF2")):
        for sc in S4:
            out.append((name, WHEEL_REGISTRY["wheel_x"], fp[key], "QQQ", 100_000, sc))
    for sid in ("put_credit_spread", "iron_condor"):
        for cap in (10_000, 100_000):
            for sym in UNIV:
                for sc in ("legacy", "monthly", "fixed") + (("fixed_lag1",) if sym in ETFS else ()):
                    out.append((sid, REGISTRY[sid], {}, sym, cap, sc))
    for sid in ("pcs_kill", "iron_condor_kill"):
        for sym in UNIV:
            for sc in ("legacy", "kill_only", "fixed"):
                out.append((sid, REGISTRY[sid], {}, sym, 100_000, sc))
    for sid in ("wheel", "covered_call"):
        for sym in UNIV:
            scs = ("legacy", "fixed") + (("fixed_lag1",) if sym in ("SPY", "QQQ") else ()) \
                + (("fixed_hold",) if sid == "covered_call" else ())
            for sc in scs:
                out.append((sid, REGISTRY[sid], {}, sym, 100_000, sc))
    return out


def scenario_params(name, cls, params, sc):
    p = dict(params)
    legacy = sc in ("legacy", "monthly", "kill_only")
    core_spread = name in ("put_credit_spread", "iron_condor", "pcs_kill", "iron_condor_kill")
    if core_spread and legacy:
        p["max_credit_frac"] = None
    if name in ("pcs_kill", "iron_condor_kill") and sc in ("legacy", "monthly"):
        p["kill_loss_basis"] = "notional"
    if name == "wheel":
        p["call_basis_fallback"] = legacy
    if name in ("wheel_F1", "wheel_F2"):
        p["call_basis"] = "engine" if legacy else "strict"
    if sc == "fixed_hold":
        p["roll_itm"] = False
    return p


def clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def seg(eq, a=None, b=None):
    m = M.equity_metrics(eq[a:b])
    return m.get("sharpe"), m.get("cagr")


def run_job(job):
    name, cls, params, sym, cap, sc = job
    E.LEGACY_MONTHLY = sc in ("legacy", "kill_only")
    p = scenario_params(name, cls, params, sc)
    kw = {"signal_lag": 1} if sc == "fixed_lag1" else {}
    n0 = runner.client().n_requests
    t0 = time.time()
    r = runner.run_one(cls, p, sym, START, END, cap, **kw)
    met, tr = r["metrics"], r["trades"]
    eq = r["equity"]["equity"]
    sh = met.get("trade_stats_with_shares") or {}
    t = tr[tr["reason"] != "open_at_end"] if len(tr) else tr
    row = {
        "finalist": name, "symbol": sym, "capital": cap, "scenario": sc,
        "infeasible": bool(r["infeasible"]),
        "cagr": met.get("cagr"), "total_return": met.get("total_return"), "sharpe": met.get("sharpe"),
        "max_drawdown": met.get("max_drawdown"),
        "is_sharpe": seg(eq, None, IS_END)[0], "oos_sharpe": seg(eq, OOS_START, None)[0],
        "n_trades": met.get("n_trades", 0), "win_rate": met.get("win_rate"),
        "profit_factor": met.get("profit_factor"), "worst_trade": met.get("worst_trade"),
        "max_contracts": int(t["contracts"].max()) if len(t) else 0,
        "kill_events": met.get("kill_events", 0),
        "n_trades_sh": sh.get("n_trades"), "win_rate_sh": sh.get("win_rate"),
        "profit_factor_sh": sh.get("profit_factor"), "worst_trade_sh": sh.get("worst_trade"),
        "requests": runner.client().n_requests - n0, "secs": round(time.time() - t0, 1),
    }
    return {k: clean(v) for k, v in row.items()}


def main():
    done = set()
    if RAW.exists():
        for line in RAW.read_text().splitlines():
            r = json.loads(line)
            done.add((r["finalist"], r["symbol"], r["capital"], r["scenario"]))
    todo = [j for j in jobs() if (j[0], j[3], j[4], j[5]) not in done]
    print(f"{len(todo)} runs to do ({len(done)} cached)", flush=True)
    with RAW.open("a") as fh:
        for j in todo:
            row = run_job(j)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(j[0], j[3], j[4], j[5], f"cagr={row['cagr']} sharpe={row['sharpe']} "
                  f"trades={row['n_trades']} req={row['requests']} {row['secs']}s", flush=True)
    write_csv()


def write_csv():
    rows = [json.loads(x) for x in RAW.read_text().splitlines()]
    df = pd.DataFrame(rows).drop_duplicates(["finalist", "symbol", "capital", "scenario"], keep="last")
    pub = published()
    for k in ("cagr", "sharpe", "max_drawdown", "trades"):
        df[f"published_{k}"] = [clean((pub.get((f, s, int(c))) or {}).get(k))
                                for f, s, c in zip(df.finalist, df.symbol, df.capital)]
    base = df[df.scenario == "legacy"].set_index(["finalist", "symbol", "capital"])
    for k in ("cagr", "sharpe", "max_drawdown", "n_trades"):
        ref = base[k]
        df[f"d_{k}_vs_legacy"] = [None if (f, s, c) not in ref.index or v is None or ref[(f, s, c)] is None
                                  else v - ref[(f, s, c)]
                                  for f, s, c, v in zip(df.finalist, df.symbol, df.capital, df[k])]
    order = {s: i for i, s in enumerate(["legacy", "monthly", "kill_only", "fixed", "fixed_hold", "fixed_lag1"])}
    df["_o"] = df.scenario.map(order)
    df = df.sort_values(["finalist", "capital", "symbol", "_o"]).drop(columns="_o")
    df.to_csv(OUT, index=False, float_format="%.6g")
    print("wrote", OUT, len(df), "rows")


if __name__ == "__main__":
    if sys.argv[1:] == ["csv"]:
        write_csv()
    else:
        main()
