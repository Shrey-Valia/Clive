"""Shared harness for the spreads tuning study. Run from the Options Bot folder:
    PY tuning/spreads/<script>.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "1.0")
# macOS: a forked worker that lets requests/urllib query the system proxy config (_scproxy,
# SystemConfiguration/ObjC) segfaults. Setting no_proxy keeps urllib on the env-var path.
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"   # ~60 req/min per worker (shared budget)

from optionsbot import metrics as M                      # noqa: E402
from optionsbot.engine import Costs                      # noqa: E402
from optionsbot.runner import run_one, IS_END, OOS_START  # noqa: E402
from optionsbot.strategies import BuyHold                # noqa: E402
from optionsbot.strategies_spreads import SpreadBook     # noqa: E402

OUT = Path(__file__).resolve().parent

# In-process memo of parsed option bar series (the shared client re-reads + re-parses the JSON
# cache file on every call; this is a pure speed-up, results are identical).
from optionsbot import data as _data  # noqa: E402

_orig_option_bars = _data.AlpacaData.option_bars
_MEMO: dict = {}


def _memo_option_bars(self, symbols, start, end):
    out, miss = {}, []
    for s in symbols:
        k = (s, start, end)
        if k in _MEMO:
            out[s] = _MEMO[k]
        else:
            miss.append(s)
    if miss:
        got = _orig_option_bars(self, miss, start, end)
        for s in miss:
            if s in got:
                _MEMO[(s, start, end)] = got[s]
                out[s] = got[s]
    return out


_data.AlpacaData.option_bars = _memo_option_bars
START, END = "2024-03-01", "2026-10-20"

COSTS = {
    "default": Costs(),
    "half": Costs(slip_abs=0.015, slip_pct=0.01, slip_cap=0.125),
    "double": Costs(slip_abs=0.06, slip_pct=0.04, slip_cap=0.50),
    "zero": Costs(slip_abs=0.0, slip_pct=0.0, slip_cap=0.0, fee_per_contract=0.0),
}
# Calibrated: slip = 1.25 x the median observed half-spread/mid (Alpaca indicative quotes,
# 30-45 DTE 0.04-0.40 delta puts, 2026-10-20 close), $0.01 floor, $0.20 cap. Per underlying.
CALIB_PCT = {"SPY": 0.0072, "QQQ": 0.0137, "IWM": 0.0154}


def costs_for(name, sym):
    if name == "calib":
        return Costs(slip_abs=0.01, slip_pct=CALIB_PCT.get(sym, 0.015), slip_cap=0.20)
    if name == "calib_x2":
        return Costs(slip_abs=0.02, slip_pct=2 * CALIB_PCT.get(sym, 0.015), slip_cap=0.40)
    return COSTS[name]

_bh: dict = {}


def bh_equity(sym):
    if sym not in _bh:
        _bh[sym] = run_one(BuyHold, None, sym, START, END, 100_000)["equity"]["equity"]
    return _bh[sym]


def seg_metrics(eq: pd.Series) -> dict:
    m = M.equity_metrics(eq)
    return {k: m.get(k, float("nan")) for k in ("cagr", "total_return", "sharpe", "sortino",
                                                 "max_drawdown", "ann_vol", "calmar")}


def trade_stats(tr: pd.DataFrame, lo=None, hi=None) -> dict:
    if tr is None or tr.empty:
        return {"trades": 0, "win_rate": float("nan"), "profit_factor": float("nan"), "avg_pnl": float("nan")}
    t = tr[tr["reason"] != "open_at_end"].copy()
    od = pd.to_datetime(t["open_date"])
    if lo is not None:
        t = t[od >= pd.Timestamp(lo)]
        od = pd.to_datetime(t["open_date"])
    if hi is not None:
        t = t[od <= pd.Timestamp(hi)]
    if t.empty:
        return {"trades": 0, "win_rate": float("nan"), "profit_factor": float("nan"), "avg_pnl": float("nan")}
    w, l = t.pnl[t.pnl > 0], t.pnl[t.pnl <= 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    return {"trades": int(len(t)), "win_rate": float(len(w) / len(t)), "profit_factor": float(pf),
            "avg_pnl": float(t.pnl.mean())}


def beta(eq, sym="SPY"):
    b = bh_equity(sym)
    j = pd.concat([eq.pct_change(), b.pct_change()], axis=1).dropna()
    if len(j) < 20 or j.iloc[:, 1].var() == 0:
        return float("nan")
    return float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())


def evaluate(eq: pd.Series, tr: pd.DataFrame, extra: dict | None = None) -> dict:
    row = {}
    for tag, seg in (("full", eq), ("is", eq[:IS_END]), ("oos", eq[OOS_START:])):
        for k, v in seg_metrics(seg).items():
            row[f"{tag}_{k}"] = v
    for tag, lo, hi in (("full", None, None), ("is", None, IS_END), ("oos", OOS_START, None)):
        for k, v in trade_stats(tr, lo, hi).items():
            row[f"{tag}_{k}"] = v
    row["beta_spy"] = beta(eq, "SPY")
    row["beta_spy_oos"] = beta(eq[OOS_START:], "SPY")
    # daily-return moments for deflated Sharpe (IS)
    r = eq[:IS_END].pct_change().dropna()
    row["is_n"] = len(r)
    row["is_sr_d"] = float(r.mean() / r.std()) if r.std() > 0 else float("nan")
    row["is_skew"] = float(r.skew())
    row["is_kurt"] = float(r.kurt() + 3)
    if tr is not None and len(tr):
        row["exit_reasons"] = json.dumps(tr["reason"].value_counts().to_dict())
    if extra:
        row.update(extra)
    return row


def run_variant(params: dict, sym: str, capital: float, costs: str = "default"):
    """sym may be 'PORT' = equal split across SPY/QQQ/IWM (separate sub-accounts, summed)."""
    if sym == "PORT":
        eqs, trs, slip, notes = [], [], 0.0, 0
        for s in ("SPY", "QQQ", "IWM"):
            r = run_one(SpreadBook, params, s, START, END, capital / 3, costs=costs_for(costs, s))
            eqs.append(r["equity"]["equity"])
            if len(r["trades"]):
                trs.append(r["trades"])
            slip += r["metrics"].get("slippage", 0.0)
            notes += r["engine"].blocked_days
        eq = pd.concat(eqs, axis=1).ffill().sum(axis=1)
        tr = pd.concat(trs) if trs else pd.DataFrame()
        return eq, tr, {"slippage": slip, "blocked_days": notes, "kill_events": None}
    r = run_one(SpreadBook, params, sym, START, END, capital, costs=costs_for(costs, sym))
    met = r["metrics"]
    return r["equity"]["equity"], r["trades"], {"slippage": met.get("slippage"), "fees": met.get("fees"),
                                                 "blocked_days": met.get("blocked_days"),
                                                 "kill_events": met.get("kill_events"),
                                                 "n_theo": met.get("n_theo_selections"),
                                                 "pct_stale_trades": met.get("pct_trades_with_stale_marks")}


def job(spec):
    """spec: dict(vid, stage, params, sym, capital, costs)."""
    try:
        eq, tr, extra = run_variant(spec["params"], spec["sym"], spec["capital"], spec.get("costs", "default"))
        row = evaluate(eq, tr, extra)
    except Exception as e:  # keep the grid going; record the failure
        row = {"error": repr(e)[:200]}
    row.update({"vid": spec["vid"], "stage": spec["stage"], "sym": spec["sym"], "capital": spec["capital"],
                "costs": spec.get("costs", "default"), "params": json.dumps(spec["params"], sort_keys=True)})
    return row


def _key(spec):
    return f"{spec['vid']}|{spec['sym']}|{spec['capital']}|{spec.get('costs', 'default')}"


def run_grid(specs, workers=2, out_csv=None):
    """Crash-safe: every finished row is appended to <out_csv>.jsonl at once; a restart skips
    finished rows; a dead worker (BrokenProcessPool) rebuilds the pool and resubmits."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from concurrent.futures.process import BrokenProcessPool
    ck = OUT / ((out_csv or "grid.csv") + ".jsonl")
    done = {}
    if ck.exists():
        for line in ck.read_text().splitlines():
            try:
                r = json.loads(line)
                done[r["_key"]] = r
            except Exception:
                pass
    todo = [s for s in specs if _key(s) not in done]
    print(f"  {len(specs)} specs, {len(done)} already done, {len(todo)} to run", flush=True)
    n = 0
    while todo:
        try:
            with ProcessPoolExecutor(max(workers, 1), mp_context=mp.get_context("fork")) as ex:
                futs = {ex.submit(job, s): s for s in todo}
                for f in as_completed(futs):
                    row = f.result()
                    row["_key"] = _key(futs[f])
                    with open(ck, "a") as fh:
                        fh.write(json.dumps(row, default=float) + "\n")
                    done[row["_key"]] = row
                    n += 1
                    if n % 25 == 0:
                        print(f"  {n}/{len(todo)}", flush=True)
        except BrokenProcessPool:
            print("  worker died; rebuilding pool", flush=True)
        todo = [s for s in specs if _key(s) not in done]
    rows = [done[_key(s)] for s in specs]
    df = pd.DataFrame(rows).drop(columns=["_key"])
    if out_csv:
        df.to_csv(OUT / out_csv, index=False)
    return df


# ------------------------------------------------------------------ deflated Sharpe
def deflated_sharpe(sr_d: float, n_obs: int, skew: float, kurt: float, trial_srs_d, n_trials=None) -> float:
    """Bailey & Lopez de Prado (2014). All Sharpe ratios are per-period (daily)."""
    from scipy.stats import norm
    srs = np.asarray([x for x in trial_srs_d if np.isfinite(x)])
    N = n_trials or len(srs)
    if N < 2 or not np.isfinite(sr_d):
        return float("nan"), float("nan")
    g = 0.5772156649
    sr0 = np.sqrt(srs.var(ddof=1)) * ((1 - g) * norm.ppf(1 - 1 / N) + g * norm.ppf(1 - 1 / (N * math.e)))
    den = math.sqrt(max(1 - skew * sr_d + (kurt - 1) / 4 * sr_d ** 2, 1e-9))
    return float(norm.cdf((sr_d - sr0) * math.sqrt(n_obs - 1) / den)), float(sr0)


def psr(sr_d, n_obs, skew, kurt, sr_star=0.0):
    from scipy.stats import norm
    den = math.sqrt(max(1 - skew * sr_d + (kurt - 1) / 4 * sr_d ** 2, 1e-9))
    return float(norm.cdf((sr_d - sr_star) * math.sqrt(n_obs - 1) / den))
