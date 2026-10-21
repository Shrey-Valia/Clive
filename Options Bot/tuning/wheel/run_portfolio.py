"""Grid over mechanical multi-name wheel portfolios (shared cash)."""
from __future__ import annotations

import itertools
import json
import os
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "1.0")

from portfolio import UNIVERSE, Portfolio, equal_weight_bh   # noqa: E402
from harness import seg                                       # noqa: E402

CORES = {
    "def": dict(put_delta=0.30, call_delta=0.30, dte_min=30, dte_max=45, dte_target=38, put_tp=0.5),
    "is": json.loads((HERE / "core_choice.json").read_text())["params"],
}


def grid():
    for core, rank, cap in itertools.product(CORES, ["fixed", "rotate", "trend", "mom", "rv_high", "rv_low"],
                                             [0.25, 0.34, 0.5]):
        yield f"pf_{core}_{rank}_cap{int(cap*100)}", CORES[core], dict(rank=rank, cap_frac=cap)


def run_one(cid, params, pkw, capital=100_000, cash_yield=False, universe=UNIVERSE):
    p = Portfolio(universe, params, capital, cash_yield=cash_yield, **pkw).run()
    ef = p.equity_frame()
    eq = ef["equity"]
    tr = p.trades_frame()
    row = {"config": cid, "family": "portfolio", "strategy": "portfolio_wheel", "params": params,
           "pkw": pkw, "symbol": "PORTFOLIO", "capital": capital, "engine_kw": {"cash_yield": cash_yield},
           "full": seg(eq), "is": seg(eq[:"2025-06-30"]), "oos": seg(eq["2025-07-01":]),
           "n_trades": int((tr.reason != "open_at_end").sum()) if len(tr) else 0,
           "names_traded": tr.groupby("underlying").size().to_dict() if len(tr) else {},
           "avg_active": float(ef.active.mean()), "avg_cash_idle": float((ef.pool / eq).mean()),
           "kill_events": p.kill_events(), "interest": p.interest + sum(e.interest_earned for e in p.eng.values())}
    return row, p


if __name__ == "__main__":
    out = HERE / "runs" / "P.jsonl"
    eqf = HERE / "runs" / "P.eq.pkl"
    eqs = pickle.loads(eqf.read_bytes()) if eqf.exists() else {}
    done = {json.loads(l)["config"] for l in out.read_text().splitlines()} if out.exists() else set()
    with out.open("a") as f:
        for cid, params, pkw in grid():
            if cid in done:
                continue
            row, p = run_one(cid, params, pkw)
            eqs[(cid, "PORTFOLIO", 100_000, False)] = p.equity_frame()["equity"].astype("float32")
            f.write(json.dumps(row) + "\n")
            f.flush()
            eqf.write_bytes(pickle.dumps(eqs))
            print(cid, round(row["is"]["sharpe"], 3), round(row["oos"]["sharpe"], 3),
                  round(row["full"]["cagr"], 3), round(row["full"]["max_drawdown"], 3), row["names_traded"],
                  flush=True)
    bh = equal_weight_bh(UNIVERSE)
    eqs[("ew_bh8", "PORTFOLIO", 100_000, False)] = bh.astype("float32")
    eqf.write_bytes(pickle.dumps(eqs))
