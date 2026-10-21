"""Roll-date-constrained regime-filter backtest engine on CBOE strategy indexes.

Conventions
- Signals are computed from closes. A decision taking effect on decision day r
  uses only data up to close of r-1 (1-day lag).
- Exposure is only changed on decision days (monthly: 3rd-Friday expiry, or the
  prior trading day if that Friday is a holiday; weekly: last trading day of week).
- Daily return = cash + e * (R_index - cash) - cost. Index returns already include
  the T-bill collateral, so e=0 means T-bills and e=1.5 means 1.5x the option
  overlay on the same collateral.
- Cost = SWITCH_COST * |e_new - e_old| charged on the decision day.
"""
import numpy as np
import pandas as pd

from data import panel

SWITCH_COST = 0.0010
START = "2007-01-01"
IS_END = "2016-12-31"
OOS_START = "2017-01-01"


# ---------------------------------------------------------------- signals
def build() -> pd.DataFrame:
    df = panel()
    # VIX3M proxy for 2008-01 .. 2009-09 from VIX and VIX6M (log-linear fit on overlap)
    o = df[["VIX", "VIX3M", "VIX6M"]].dropna()
    X = np.c_[np.ones(len(o)), np.log(o.VIX.values), np.log(o.VIX6M.values)]
    b = np.linalg.lstsq(X, np.log(o.VIX3M.values), rcond=None)[0]
    proxy = np.exp(b[0] + b[1] * np.log(df.VIX) + b[2] * np.log(df.VIX6M))
    df["VIX3M_P"] = df["VIX3M"].fillna(proxy.where(df.index < o.index[0]))
    df["TS"] = df.VIX / df.VIX3M_P
    df["TS9"] = df.VIX9D / df.VIX
    r = np.log(df.SPX).diff()
    df["RV21"] = r.rolling(21).std() * np.sqrt(252) * 100
    df["VRP"] = df.VIX - df.RV21
    for n in (100, 200):
        df[f"SMA{n}"] = df.SPX.rolling(n).mean()
    df["DD"] = df.SPX / df.SPX.rolling(252, min_periods=1).max() - 1
    df["VIXMED"] = df.VIX.rolling(252).median()
    for k in ["PUT", "BXM", "BXMD", "CNDR", "WPUT", "PPUT", "CLL", "BFLY", "SPX"]:
        df[f"R_{k}"] = df[k].pct_change()
    df["R_SPXTR"] = df["R_SPX"] + 0.018 / 252  # ~1.8%/yr dividend approximation
    df["PUTVOL63"] = df.R_PUT.rolling(63).std() * np.sqrt(252)
    df["WPUTVOL63"] = df.R_WPUT.rolling(63).std() * np.sqrt(252)
    df = df[df.index >= START].copy()
    return df


def monthly_decision_days(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    out = []
    months = pd.period_range(idx[0], idx[-1], freq="M")
    s = pd.Series(idx, index=idx)
    for m in months:
        first = m.start_time
        # third Friday
        offs = (4 - first.weekday()) % 7
        tf = first + pd.Timedelta(days=offs + 14)
        cand = s[s <= tf]
        if len(cand) and cand.iloc[-1] >= first:
            out.append(cand.iloc[-1])
    return pd.DatetimeIndex(out)


def weekly_decision_days(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    wk = s.groupby(idx.to_period("W-FRI")).max()
    return pd.DatetimeIndex(wk.values)


# ---------------------------------------------------------------- engine
def run(df: pd.DataFrame, target: pd.Series, asset="PUT", freq="M",
        cost=SWITCH_COST) -> pd.Series:
    """target: desired exposure series computed from same-day close signals.
    It is lagged 1 day and sampled only on decision days."""
    idx = df.index
    dd = monthly_decision_days(idx) if freq == "M" else weekly_decision_days(idx)
    tgt = target.reindex(idx).shift(1)  # use close of r-1
    tgt = tgt.fillna(1.0)  # missing signal history -> stay invested (filter inactive)
    e = pd.Series(np.nan, index=idx)
    e.loc[dd] = tgt.loc[dd]
    e.iloc[0] = tgt.iloc[0] if np.isnan(e.iloc[0]) else e.iloc[0]
    e = e.ffill()
    chg = e.diff().abs().fillna(0.0)
    R = df[f"R_{asset}"].fillna(0.0)
    c = df["CASH_RET"].fillna(0.0)
    ret = c + e * (R - c) - cost * chg
    ret.attrs["exposure"] = e
    ret.attrs["switches"] = int((chg > 1e-9).sum())
    return ret


def run_with_exit(df, target, exit_sig, asset="PUT", freq="M", cost=SWITCH_COST,
                  exit_cost=0.0030):
    """Like run(), but exit_sig (bool, close-based) can flatten mid-period at the
    next close (lag 1); re-entry only at the next decision day."""
    idx = df.index
    dd = set(monthly_decision_days(idx) if freq == "M" else weekly_decision_days(idx))
    tgt = target.reindex(idx).shift(1).fillna(1.0).values
    ex = exit_sig.reindex(idx).shift(1).fillna(False).astype(bool).values
    e = np.zeros(len(idx))
    cur = tgt[0]
    costs = np.zeros(len(idx))
    for i, d in enumerate(idx):
        if d in dd:
            new = tgt[i]
            costs[i] += cost * abs(new - cur)
            cur = new
        elif ex[i] and cur > 0:
            # signal seen at close i-1, buy-back executed during day i (approx. at
            # close i): day i's return is still earned at the old exposure (conservative).
            e[i] = cur
            costs[i] += exit_cost * cur
            cur = 0.0
            continue
        e[i] = cur
    e = pd.Series(e, index=idx)
    R = df[f"R_{asset}"].fillna(0.0)
    c = df["CASH_RET"].fillna(0.0)
    ret = c + e * (R - c) - pd.Series(costs, index=idx)
    ret.attrs["exposure"] = e
    ret.attrs["switches"] = int((e.diff().abs() > 1e-9).sum())
    return ret


# ---------------------------------------------------------------- metrics
def stats(ret: pd.Series, cash: pd.Series, exposure=None) -> dict:
    ret = ret.dropna()
    if len(ret) < 20:
        return {}
    c = cash.reindex(ret.index).fillna(0)
    ex = ret - c
    yrs = (ret.index[-1] - ret.index[0]).days / 365.25
    eq = (1 + ret).cumprod()
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = ret.std() * np.sqrt(252)
    sharpe = ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else np.nan
    dn = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(252)
    sortino = ex.mean() * 252 / dn if dn > 0 else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    mon = (1 + ret).groupby(ret.index.to_period("M")).prod() - 1
    out = dict(CAGR=cagr, Vol=vol, Sharpe=sharpe, Sortino=sortino, MaxDD=mdd,
               WorstM=mon.min())
    if exposure is not None:
        e = exposure.reindex(ret.index)
        out["Invested"] = (e > 1e-9).mean()
        out["AvgExp"] = e.mean()
    return out


def period_ret(ret: pd.Series, a, b) -> float:
    r = ret[(ret.index >= a) & (ret.index <= b)]
    return float((1 + r).prod() - 1)
