"""Reporting helpers: per-year / per-event tables, trade stats, deflated Sharpe, run cache."""
from __future__ import annotations

import hashlib
import json
import math
import pickle

import numpy as np
import pandas as pd
from scipy.stats import norm

from common import WORK

EVENTS = {   # window start, end (inclusive) for event P&L
    "1997-10 Asia": ("1997-10-20", "1997-11-10"),
    "1998-08 LTCM": ("1998-07-20", "1998-10-15"),
    "2001-09 9/11": ("2001-09-01", "2001-10-15"),
    "2002-07": ("2002-06-15", "2002-08-15"),
    "2008-09 GFC": ("2008-09-01", "2008-12-31"),
    "2010-05 flash": ("2010-04-26", "2010-07-10"),
    "2011-08 downgrade": ("2011-07-22", "2011-10-10"),
    "2015-08 China": ("2015-08-14", "2015-10-02"),
    "2018-02 Volmageddon": ("2018-01-26", "2018-02-28"),
    "2018-Q4": ("2018-10-01", "2018-12-31"),
    "2020-03 COVID": ("2020-02-19", "2020-04-15"),
    "2022 bear": ("2022-01-01", "2022-10-15"),
    "2024-08 yen carry": ("2024-07-24", "2024-08-20"),
    "2025-04 tariffs": ("2025-03-25", "2025-04-30"),
}


def per_year(eq: pd.Series) -> pd.Series:
    e = eq.groupby(eq.index.year).last()
    first = eq.iloc[0]
    prev = e.shift(1)
    prev.iloc[0] = first
    return e / prev - 1


def event_table(eq: pd.Series, events=EVENTS) -> pd.DataFrame:
    rows = []
    for k, (a, b) in events.items():
        x = eq[(eq.index >= a) & (eq.index <= b)]
        if len(x) < 3 or eq.index[0] > pd.Timestamp(a):
            continue
        prior = eq[eq.index < a]
        base = prior.iloc[-1] if len(prior) else x.iloc[0]
        path = pd.concat([pd.Series([base]), x]).reset_index(drop=True)
        rows.append({"event": k, "return": x.iloc[-1] / base - 1, "max_dd": (path / path.cummax() - 1).min()})
    return pd.DataFrame(rows).set_index("event") if rows else pd.DataFrame()


def trade_stats(tr: pd.DataFrame, equity_at_open: pd.Series | None = None) -> dict:
    t = tr[tr.reason != "open"] if len(tr) else tr
    if not len(t):
        return {"n": 0}
    ret = t.pnl / t.max_loss0.where(t.max_loss0 > 0)
    out = {"n": len(t), "win": float((t.pnl > 0).mean()), "mean_pnl": t.pnl.mean(),
           "worst_pnl": t.pnl.min(), "mean_ret_on_risk": float(ret.mean()),
           "worst_ret_on_risk": float(ret.min()),
           "n_maxloss_90": int((ret <= -0.9).sum()), "n_loss_50": int((ret <= -0.5).sum())}
    if equity_at_open is not None:
        eo = equity_at_open.reindex(pd.to_datetime(t.open_date), method="ffill").values
        out["worst_pnl_pct_equity"] = float((t.pnl.values / eo).min())
    return out


def sharpe(r: pd.Series) -> float:
    r = r.dropna()
    return float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else np.nan


def deflated_sharpe(sr_ann: float, n_obs: int, n_trials: int, skew: float = 0.0, kurt: float = 3.0,
                    sr_var_trials: float | None = None, periods: int = 252) -> float:
    """Bailey & Lopez de Prado (2014) DSR with per-period SR; sr_var_trials = variance of the
    trial Sharpe ratios (per period); default: the null var of an estimated SR (1/n)."""
    sr = sr_ann / math.sqrt(periods)
    v = sr_var_trials if sr_var_trials is not None else 1.0 / n_obs
    g = 0.5772156649
    if n_trials <= 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(v) * ((1 - g) * norm.ppf(1 - 1.0 / n_trials) + g * norm.ppf(1 - 1.0 / (n_trials * math.e)))
    den = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr))
    return float(norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / den))


def cached(tag: str, fn, **kw):
    key = hashlib.sha1(json.dumps([tag, kw], sort_keys=True, default=str).encode()).hexdigest()[:16]
    p = WORK / "runs" / f"{tag}_{key}.pkl"
    p.parent.mkdir(exist_ok=True)
    if p.exists():
        return pickle.loads(p.read_bytes())
    out = fn(**kw)
    p.write_bytes(pickle.dumps(out))
    return out
