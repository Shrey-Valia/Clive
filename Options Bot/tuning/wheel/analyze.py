"""Aggregate all variant runs: per-name rows + equal-weight 'basket' per config,
IS/OOS metrics, trial counts and deflated Sharpe."""
from __future__ import annotations

import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from optionsbot import metrics as M   # noqa: E402

IS_END, OOS_START = "2025-06-30", "2025-07-01"
NAMES9 = ["SPY", "QQQ", "IWM", "AAPL", "AMZN", "GOOGL", "META", "NVDA", "MSFT"]


def load(pattern="[ABCD]_*.jsonl"):
    rows, eqs = [], {}
    for f in sorted((HERE / "runs").glob(pattern)):
        for line in f.read_text().splitlines():
            rows.append(json.loads(line))
        pf = f.with_suffix(".eq.pkl")
        if pf.exists():
            eqs.update(pickle.loads(pf.read_bytes()))
    return rows, eqs


def flat_rows(rows):
    out = []
    for r in rows:
        if "error" in r:
            continue
        d = {k: r[k] for k in ("config", "family", "strategy", "symbol", "capital", "infeasible",
                               "blocked_days", "n_trades", "win_rate", "profit_factor",
                               "worst_trade", "n_assigned", "kill_events", "interest")}
        d["cash_yield"] = bool(r["engine_kw"].get("cash_yield"))
        for seg in ("full", "is", "oos"):
            for k, v in r[seg].items():
                d[f"{seg}_{k}"] = v
        d["params"] = json.dumps(r.get("params", {}), sort_keys=True)
        out.append(d)
    return pd.DataFrame(out)


def sharpe(r):
    r = r.dropna()
    return float(r.mean() / r.std() * math.sqrt(252)) if len(r) > 2 and r.std() > 0 else float("nan")


def basket(eqs, cfg, names, capital=100_000, cy=False):
    rets = []
    for s in names:
        e = eqs.get((cfg, s, capital, cy))
        if e is None:
            return None
        rets.append(e.astype(float).pct_change())
    r = pd.concat(rets, axis=1).mean(axis=1).fillna(0.0)
    return (1 + r).cumprod() * capital


def seg_metrics(eq):
    out = {}
    for tag, e in (("full", eq), ("is", eq[:IS_END]), ("oos", eq[OOS_START:])):
        m = M.equity_metrics(e)
        for k in ("cagr", "sharpe", "sortino", "max_drawdown", "calmar"):
            out[f"{tag}_{k}"] = m.get(k)
    return out


def deflated_sharpe(sr_ann, n_trials, sr_var_ann, rets):
    """Bailey & Lopez de Prado DSR. sr_* annualized; converted to daily."""
    rets = rets.dropna()
    T = len(rets)
    if T < 30 or n_trials < 1:
        return float("nan"), float("nan")
    sr = sr_ann / math.sqrt(252)
    v = sr_var_ann / 252
    g = 0.5772156649
    if n_trials > 1:
        sr0 = math.sqrt(max(v, 1e-12)) * ((1 - g) * norm.ppf(1 - 1 / n_trials) +
                                           g * norm.ppf(1 - 1 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    sk = float(rets.skew())
    ku = float(rets.kurt()) + 3
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr * sr, 1e-9))
    return float(norm.cdf(z)), float(sr0 * math.sqrt(252))


def basket_table(eqs, df, names=NAMES9, capital=100_000):
    rows = []
    for (cfg, fam), g in df[(df.capital == capital) & (~df.cash_yield)].groupby(["config", "family"]):
        b = basket(eqs, cfg, names, capital)
        if b is None:
            continue
        sub = g[g.symbol.isin(names)]
        row = {"config": cfg, "family": fam, **seg_metrics(b),
               "med_is_sharpe": sub.is_sharpe.median(), "med_oos_sharpe": sub.oos_sharpe.median(),
               "med_full_sharpe": sub.full_sharpe.median(),
               "med_is_dd": sub.is_max_drawdown.median(), "med_full_dd": sub.full_max_drawdown.median(),
               "params": g.params.iloc[0]}
        rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    rows, eqs = load()
    df = flat_rows(rows)
    bt = basket_table(eqs, df)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 400)
    cols = ["config", "family", "is_sharpe", "oos_sharpe", "full_sharpe", "is_cagr", "oos_cagr",
            "full_cagr", "full_max_drawdown", "med_is_sharpe", "med_oos_sharpe"]
    print(bt.sort_values(["family", "is_sharpe"], ascending=[True, False])[cols].round(3).to_string())
