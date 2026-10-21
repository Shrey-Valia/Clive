"""Variant runner for the stock-backed premium tuning study.

Usage (from the Options Bot folder):
  python3 tuning/wheel/harness.py --stage A --symbols SPY,QQQ,IWM,AAPL,AMZN --out tuning/wheel/runs/A_0.jsonl

Each line of the output is one (config, symbol, capital) run with full / IS / OOS
metrics. Configs are defined in configs.py; every config run counts as a trial.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "0.9")   # <= ~67 req/min per worker

from optionsbot import metrics as M          # noqa: E402
from optionsbot import runner                # noqa: E402
from optionsbot.strategies import REGISTRY   # noqa: E402
from optionsbot.strategies_wheel import WHEEL_REGISTRY   # noqa: E402

START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"


def cls_of(sid):
    return WHEEL_REGISTRY.get(sid) or REGISTRY[sid]


def _clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def seg(eq):
    m = M.equity_metrics(eq)
    keep = ("cagr", "total_return", "sharpe", "sortino", "max_drawdown", "calmar", "ann_vol")
    return {k: _clean(m.get(k)) for k in keep}


def run_cfg(cfg, sym, capital, engine_kw=None):
    engine_kw = engine_kw or {}
    r = runner.run_one(cls_of(cfg["strategy"]), cfg["params"], sym, START, END, capital, **engine_kw)
    eq = r["equity"]["equity"]
    met = r["metrics"]
    row = {"config": cfg["id"], "family": cfg["family"], "strategy": cfg["strategy"],
           "params": cfg["params"], "engine_kw": engine_kw, "symbol": sym, "capital": capital,
           "infeasible": bool(r["infeasible"]), "blocked_days": met.get("blocked_days", 0),
           "full": seg(eq), "is": seg(eq[:IS_END]), "oos": seg(eq[OOS_START:]),
           "n_trades": met.get("n_trades", 0), "win_rate": _clean(met.get("win_rate")),
           "profit_factor": _clean(met.get("profit_factor")), "worst_trade": _clean(met.get("worst_trade")),
           "n_assigned": met.get("n_assigned", 0), "n_early_assign": met.get("n_early_assign", 0),
           "kill_events": met.get("kill_events", 0), "interest": met.get("interest", 0),
           "pct_time_in_market": _clean(met.get("pct_time_in_market")),
           "avg_net_delta_pct": _clean(met.get("avg_net_delta_pct")),
           "pct_theo_entries": _clean(met.get("pct_theo_entries"))}
    return row, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--capital", type=float, default=100_000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cash_yield", action="store_true")
    a = ap.parse_args()
    import configs
    cfgs = configs.STAGES[a.stage]()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import pickle
    eqf = out.with_suffix(".eq.pkl")
    eqs = pickle.loads(eqf.read_bytes()) if eqf.exists() else {}
    done = set()
    if out.exists():
        keep = []
        for line in out.read_text().splitlines():
            j = json.loads(line)
            k = (j["config"], j["symbol"], j["capital"], bool(j["engine_kw"].get("cash_yield")))
            if k in eqs or "error" in j:
                done.add(k)
                keep.append(line)
        out.write_text("".join(x + "\n" for x in keep))
    ekw = {"cash_yield": True} if a.cash_yield else {}
    t0 = time.time()
    syms = a.symbols.split(",")
    with out.open("a") as f:
        for sym in syms:
            for cfg in cfgs:
                key = (cfg["id"], sym, a.capital, bool(a.cash_yield))
                if key in done:
                    continue
                try:
                    row, r = run_cfg(cfg, sym, a.capital, ekw)
                    eqs[key] = r["equity"]["equity"].astype("float32")
                except Exception as e:   # record and continue
                    row = {"config": cfg["id"], "family": cfg["family"], "symbol": sym,
                           "capital": a.capital, "engine_kw": ekw, "error": repr(e)}
                f.write(json.dumps(row) + "\n")
                f.flush()
            eqf.write_bytes(pickle.dumps(eqs))
            print(f"{sym} done {time.time() - t0:.0f}s req={runner.client().n_requests}", flush=True)


if __name__ == "__main__":
    main()
