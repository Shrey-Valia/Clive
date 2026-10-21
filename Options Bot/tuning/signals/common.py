"""Shared helpers for the signal-timed options study (tuning/signals/).

Run from the Options Bot folder:  python3 tuning/signals/study.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "0.64")   # <= 2 workers share the budget

from optionsbot import metrics as M                         # noqa: E402
from optionsbot.engine import Costs                          # noqa: E402
from optionsbot.runner import run_one, market                # noqa: E402
from optionsbot.strategies import REGISTRY                   # noqa: E402
from optionsbot.strategies_signals import SIGNAL_REGISTRY, features  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"
UNIV = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
ETFS = ["SPY", "QQQ", "IWM"]
MEGA = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
CAPITAL = 10_000

COSTS = {
    "default": None,
    "half": Costs(slip_abs=0.015, slip_pct=0.01, slip_cap=0.125),
    "double": Costs(slip_abs=0.06, slip_pct=0.04, slip_cap=0.50),
}


def cls_of(sid):
    return SIGNAL_REGISTRY.get(sid) or REGISTRY[sid]


def _key(sid, params, sym, cost, capital):
    p = {k: (sorted(v) if isinstance(v, (set, frozenset)) else v) for k, v in (params or {}).items()}
    s = json.dumps([sid, p, sym, cost, capital, START, END], sort_keys=True, default=str)
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def run_cached(sid, params, sym, cost="default", capital=CAPITAL):
    """One engine run, cached on disk (equity + trades + flat metrics)."""
    f = CACHE / f"{_key(sid, params, sym, cost, capital)}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    r = run_one(cls_of(sid), params, sym, START, END, capital, costs=COSTS[cost])
    out = {"equity": r["equity"][["equity", "spot", "n_positions", "capital_at_risk", "reserved"]],
           "trades": r["trades"], "infeasible": r["infeasible"], "notes": r["notes"],
           "params": {k: (sorted(v) if isinstance(v, (set, frozenset)) else v)
                      for k, v in r["params"].items()}}
    f.write_bytes(pickle.dumps(out))
    return out


# --------------------------------------------------------------------------
def trade_returns(tr: pd.DataFrame) -> pd.DataFrame:
    """Per-trade return on capital at risk (max loss at entry)."""
    if tr is None or tr.empty:
        return pd.DataFrame(columns=["underlying", "open_date", "ret", "pnl", "risk", "kind"])
    t = tr[tr["reason"] != "open_at_end"].copy()
    if t.empty:
        return pd.DataFrame(columns=["underlying", "open_date", "ret", "pnl", "risk", "kind"])
    credit = t["entry_cash"] > 0
    risk = np.where(credit, t["reserve_at_open"] - t["entry_cash"], -t["entry_cash"])
    t["risk"] = np.maximum(risk, 1.0)
    t["ret"] = t["pnl"] / t["risk"]
    t["open_date"] = pd.to_datetime(t["open_date"])
    return t[["underlying", "open_date", "close_date", "ret", "pnl", "risk", "kind", "reason"]]


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def boot_ci(df: pd.DataFrame, n=2000, seed=7, col="ret"):
    """95% CI of the mean per-trade return; clustered bootstrap by entry week
    (trades opened the same week on correlated tickers are resampled together)."""
    if df is None or len(df) < 3:
        return (float("nan"), float("nan"))
    wk = df["open_date"].dt.to_period("W")
    groups = [g[col].values for _, g in df.groupby(wk)]
    rng = np.random.default_rng(seed)
    G = len(groups)
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([len(g) for g in groups])
    means = []
    for _ in range(n):
        idx = rng.integers(0, G, G)
        means.append(sums[idx].sum() / max(cnts[idx].sum(), 1))
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def basket(eqs: dict) -> pd.Series:
    """Equal-weight daily-rebalanced basket of per-ticker equity curves."""
    rets = pd.DataFrame({k: v.pct_change() for k, v in eqs.items()}).dropna(how="all").fillna(0.0)
    eq = (1 + rets.mean(axis=1)).cumprod() * CAPITAL
    first = next(iter(eqs.values())).index[0]
    return pd.concat([pd.Series([CAPITAL], index=[first]), eq])


def em(eq: pd.Series, a=None, b=None):
    e = eq[a:b] if (a or b) else eq
    return M.equity_metrics(e)


def evaluate(sid, params, syms, cost="default", label=None, family=None):
    """Run a variant on `syms`; return summary row + raw per-ticker results."""
    res = {s: run_cached(sid, params, s, cost) for s in syms}
    eqs = {s: r["equity"]["equity"] for s, r in res.items()}
    b = basket(eqs)
    tr = pd.concat([trade_returns(r["trades"]) for r in res.values()], ignore_index=True)
    tis = tr[tr["open_date"] <= IS_END] if len(tr) else tr
    toos = tr[tr["open_date"] >= OOS_START] if len(tr) else tr
    row = {"family": family, "variant": label, "strategy": sid, "cost": cost,
           "underlyings": ",".join(syms), "params": json.dumps(_clean(params), sort_keys=True)}
    for tag, (a, z) in {"full": (None, None), "is": (None, IS_END), "oos": (OOS_START, None)}.items():
        m = em(b, a, z)
        row.update({f"{tag}_cagr": m.get("cagr"), f"{tag}_sharpe": m.get("sharpe"),
                    f"{tag}_maxdd": m.get("max_drawdown"), f"{tag}_ret": m.get("total_return"),
                    f"{tag}_vol": m.get("ann_vol")})
    for tag, t in {"is": tis, "oos": toos, "full": tr}.items():
        row[f"{tag}_ntr"] = int(len(t))
        row[f"{tag}_tr_mean"] = float(t["ret"].mean()) if len(t) else float("nan")
        row[f"{tag}_tr_t"] = tstat(t["ret"]) if len(t) else float("nan")
        row[f"{tag}_win"] = float((t["pnl"] > 0).mean()) if len(t) else float("nan")
    lo, hi = boot_ci(toos) if len(toos) >= 3 else (float("nan"), float("nan"))
    row["oos_tr_ci_lo"], row["oos_tr_ci_hi"] = lo, hi
    lo, hi = boot_ci(tis) if len(tis) >= 3 else (float("nan"), float("nan"))
    row["is_tr_ci_lo"], row["is_tr_ci_hi"] = lo, hi
    row["n_infeasible"] = sum(1 for r in res.values() if r["infeasible"])
    # per-ticker Sharpe (full / IS / OOS)
    for s, e in eqs.items():
        row[f"sh_{s}_is"] = em(e, None, IS_END).get("sharpe")
        row[f"sh_{s}_oos"] = em(e, OOS_START, None).get("sharpe")
    return row, res, b, tr


def _clean(p):
    out = {}
    for k, v in (p or {}).items():
        if isinstance(v, (set, frozenset)):
            out[k] = f"<{len(v)} months>"
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------
def deflated_sharpe(sr_ann, n_obs, n_trials, var_sr_ann, skew=0.0, kurt=3.0):
    """Bailey & Lopez de Prado (2014) deflated Sharpe ratio (probability the
    true Sharpe > 0 after selecting the best of n_trials). Inputs annualized;
    converted to per-day internally."""
    from scipy.stats import norm
    if not np.isfinite(sr_ann) or n_trials < 1:
        return float("nan"), float("nan")
    sr = sr_ann / math.sqrt(252)
    v = max(var_sr_ann, 1e-12) / 252
    g = 0.5772156649
    if n_trials > 1:
        sr0 = math.sqrt(v) * ((1 - g) * norm.ppf(1 - 1 / n_trials) + g * norm.ppf(1 - 1 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    den = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    z = (sr - sr0) * math.sqrt(n_obs - 1) / den
    return float(norm.cdf(z)), float(sr0 * math.sqrt(252))


def bh_equity(sym):
    return run_cached("buy_hold", {}, sym)["equity"]["equity"]


def monthly(eq):
    mr = M.monthly_returns(eq)
    return [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 5)} for d, v in mr.items()]


def series(eq, every=1, dd=False):
    s = M.drawdown(eq) if dd else eq
    s = s.iloc[::every]
    return [{"date": str(d.date()), "value": round(float(v), 4 if dd else 2)} for d, v in s.items()]
