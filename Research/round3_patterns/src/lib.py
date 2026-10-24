"""Small backtest utilities shared by the family scripts."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
from scipy import stats

from common import CACHE, ETF_COST_SIDE, SELL_FEES


def yahoo(sym=None) -> pd.DataFrame:
    y = pd.read_parquet(os.path.join(CACHE, "etf_yahoo.parquet"))
    y["date"] = pd.to_datetime(y.date)
    return y if sym is None else y[y.symbol == sym].set_index("date").sort_index()


def alpaca_etf(sym=None) -> pd.DataFrame:
    y = pd.read_parquet(os.path.join(CACHE, "etf_alpaca.parquet"))
    y["date"] = pd.to_datetime(y.date)
    return y if sym is None else y[y.symbol == sym].set_index("date").sort_index()


def hold_mask_returns(px: pd.DataFrame, mask: pd.Series, cost_side=ETF_COST_SIDE) -> pd.Series:
    """Close-to-close strategy: in the market on days where mask is True (return of day t = c_t/c_{t-1}-1,
    meaning the position was bought at the close of t-1). Costs charged on each entry and exit."""
    r = px.c.pct_change()
    m = mask.reindex(px.index).fillna(False).astype(bool)
    pos = m.astype(float)
    # position held during day t is pos_t; trades happen at close of t-1 when pos_t != pos_{t-1}
    trade = (pos - pos.shift(1).fillna(0)).abs()
    out = pos * r
    # entry cost booked on the first held day, exit cost on the day after the last held day is booked
    # on the last held day (trade at its close): shift the exit indicator back one day
    entry = (pos.diff().fillna(pos) > 0).astype(float)
    exit_ = (pos.diff().shift(-1).fillna(0) < 0).astype(float)
    out = out - entry * cost_side - exit_ * (cost_side + SELL_FEES)
    return out.fillna(0)


def tstat_mean(x) -> float:
    x = pd.Series(x).dropna()
    return float(x.mean() / x.std() * np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def month_pos(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """For each trading day: position from month end (-1 = last day) and from month start (+1 = first day)."""
    d = pd.DataFrame(index=idx)
    ym = idx.to_period("M")
    d["fwd"] = d.groupby(ym).cumcount() + 1
    d["bwd"] = -(d.groupby(ym).cumcount(ascending=False) + 1)
    return d


def holidays_from_calendar(idx: pd.DatetimeIndex) -> pd.Series:
    """Pre-holiday flag: the next trading day is more than one business day away (weekday market closure)."""
    s = pd.Series(idx, index=idx)
    nxt = s.shift(-1)
    bdays = [len(pd.bdate_range(a, b)) - 1 if pd.notna(b) else 1 for a, b in zip(s, nxt)]
    return pd.Series(np.array(bdays) > 1, index=idx)


def vol_target_weights(r: pd.DataFrame, target_ann: float, lookback=60, cap=2.0) -> pd.DataFrame:
    vol = r.rolling(lookback, min_periods=int(lookback * 0.8)).std() * np.sqrt(252)
    return (target_ann / vol).clip(upper=cap)
