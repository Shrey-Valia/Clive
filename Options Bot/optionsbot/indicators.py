"""Technical / volatility / regime indicators for options and stock research.

Pure pandas + numpy, vectorized, no I/O. Every function takes pandas Series
(or a DataFrame with ``open/high/low/close`` columns) indexed by date and
returns a Series/DataFrame aligned to the same index. Leading values that
cannot be computed yet are ``NaN`` (no look-ahead: a value at ``t`` uses only
data up to and including ``t``, except the explicitly forward-looking helpers
``forward_return`` and ``forward_realized_vol`` which are for research labels
only).

Conventions
-----------
* Wilder smoothing (RSI, ATR, ADX) is seeded with the simple mean of the first
  ``n`` observations, then ``x_t = x_{t-1} + (v_t - x_{t-1}) / n`` -- the
  textbook definition, so values match published RSI/ATR tables.
* EMA uses ``alpha = 2 / (n + 1)`` with ``adjust=False`` and is seeded with the
  first observation (pandas default). Use ``ema(..., seed_sma=True)`` for the
  SMA-seeded variant.
* Volatilities are annualized with ``periods_per_year`` (default 252) and are
  expressed as decimals (0.20 == 20 %). Multiply by 100 to compare with VIX.
* Bollinger standard deviation is the population std (``ddof=0``), as in
  John Bollinger's definition.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

__all__ = [
    "sma", "ema", "wilder", "rsi", "macd", "true_range", "atr", "adx",
    "bollinger", "keltner", "squeeze", "ibs", "donchian",
    "log_returns", "rv_close", "rv_parkinson", "rv_garman_klass", "rv_yang_zhang",
    "forward_return", "forward_realized_vol",
    "iv_rank", "iv_percentile", "vix_term_ratio", "zscore", "momentum",
    "drawdown", "streak", "turn_of_month", "regime",
]


# --------------------------------------------------------------------------
# Moving averages / smoothing
# --------------------------------------------------------------------------
def sma(s: pd.Series, n: int) -> pd.Series:
    """Simple moving average over ``n`` periods."""
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int, seed_sma: bool = False) -> pd.Series:
    """Exponential moving average, ``alpha = 2/(n+1)``.

    ``seed_sma=False`` (default): recursion starts at the first value.
    ``seed_sma=True``: first output at index ``n-1`` equals SMA(n) of the
    first ``n`` values, and NaN before.
    """
    if not seed_sma:
        return s.ewm(span=n, adjust=False, min_periods=1).mean()
    return _seeded_ewm(s, n, alpha=2.0 / (n + 1))


def _seeded_ewm(s: pd.Series, n: int, alpha: float) -> pd.Series:
    """EWM recursion seeded by the SMA of the first ``n`` non-NaN values."""
    s = s.astype(float)
    valid = s.dropna()
    out = pd.Series(np.nan, index=s.index)
    if len(valid) < n:
        return out
    x = valid.copy()
    seed = valid.iloc[:n].mean()
    x.iloc[: n - 1] = np.nan
    x.iloc[n - 1] = seed
    res = x.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    res.iloc[: n - 1] = np.nan
    out.loc[res.index] = res
    return out


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (RMA): SMA seed then ``alpha = 1/n``."""
    return _seeded_ewm(s, n, alpha=1.0 / n)


# --------------------------------------------------------------------------
# Momentum oscillators
# --------------------------------------------------------------------------
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI. ``rsi(close, 2)`` is Connors' RSI(2).

    First value appears at position ``n`` (needs ``n`` price changes).
    If average loss is 0 the RSI is 100; if both are 0 it is 50.
    """
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / dn
        out = 100 - 100 / (1 + rs)
    out = out.where(dn != 0, 100.0)
    out = out.where(~((up == 0) & (dn == 0)), 50.0)
    return out.where(up.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line (EMA_fast - EMA_slow), signal line (EMA of MACD), histogram."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def momentum(close: pd.Series, n: int) -> pd.Series:
    """Trailing ``n``-period simple return: ``C_t / C_{t-n} - 1``."""
    return close / close.shift(n) - 1


def zscore(s: pd.Series, n: int) -> pd.Series:
    """Rolling z-score ``(x - mean_n) / std_n`` (sample std, ddof=1)."""
    m = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=1)
    return (s - m) / sd.replace(0, np.nan)


# --------------------------------------------------------------------------
# Range / trend
# --------------------------------------------------------------------------
def true_range(df: pd.DataFrame) -> pd.Series:
    """True range: max(H-L, |H-C_prev|, |L-C_prev|). First bar uses H-L."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1)
    return tr.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average true range with Wilder smoothing (seeded by SMA of first n TRs)."""
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder DMI / ADX. Returns columns ``plus_di``, ``minus_di``, ``dx``, ``adx``.

    +DM = up-move if up-move > down-move and > 0, else 0 (and vice-versa).
    DI = 100 * Wilder(DM) / ATR; DX = 100 * |+DI - -DI| / (+DI + -DI);
    ADX = Wilder(DX). The first bar has no DM so smoothing starts at bar 1.
    """
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pdm.iloc[0] = mdm.iloc[0] = np.nan
    tr = true_range(df)
    tr.iloc[0] = np.nan
    atr_ = wilder(tr, n)
    pdi = 100 * wilder(pdm, n) / atr_
    mdi = 100 * wilder(mdm, n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "dx": dx, "adx": wilder(dx, n)})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger bands. Columns: ``mid, upper, lower, pct_b, bandwidth``.

    ``pct_b = (C - lower) / (upper - lower)``; ``bandwidth = (upper - lower) / mid``.
    """
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    width = (up - lo)
    return pd.DataFrame({"mid": mid, "upper": up, "lower": lo,
                         "pct_b": (close - lo) / width.replace(0, np.nan),
                         "bandwidth": width / mid})


def keltner(df: pd.DataFrame, n: int = 20, atr_n: int = 10, mult: float = 2.0) -> pd.DataFrame:
    """Keltner channel: EMA(n) of close +/- ``mult`` * ATR(atr_n)."""
    mid = ema(df["close"], n)
    a = atr(df, atr_n)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a})


def squeeze(df: pd.DataFrame, n: int = 20, bb_k: float = 2.0, kc_mult: float = 1.5) -> pd.Series:
    """TTM-style volatility squeeze: Bollinger(n, bb_k) entirely inside Keltner(n, kc_mult).

    Returns a boolean Series (False where inputs are not yet defined).
    """
    bb = bollinger(df["close"], n, bb_k)
    kc = keltner(df, n, n, kc_mult)
    return ((bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])).fillna(False)


def ibs(df: pd.DataFrame) -> pd.Series:
    """Internal bar strength ``(C - L) / (H - L)``; NaN when H == L."""
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (df["close"] - df["low"]) / rng


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Donchian channel: rolling ``n`` max high, min low and their midpoint."""
    hi = df["high"].rolling(n, min_periods=n).max()
    lo = df["low"].rolling(n, min_periods=n).min()
    return pd.DataFrame({"upper": hi, "lower": lo, "mid": (hi + lo) / 2})


# --------------------------------------------------------------------------
# Realized volatility (annualized, decimal)
# --------------------------------------------------------------------------
def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


def rv_close(close: pd.Series, n: int = 21, periods_per_year: int = TRADING_DAYS,
             demean: bool = True) -> pd.Series:
    """Close-to-close realized vol. ``demean=True``: sample std (ddof=1);
    ``demean=False``: sqrt(mean(r^2)) (zero-mean, as used for variance swaps)."""
    r = log_returns(close)
    if demean:
        v = r.rolling(n, min_periods=n).std(ddof=1)
    else:
        v = np.sqrt((r ** 2).rolling(n, min_periods=n).mean())
    return v * np.sqrt(periods_per_year)


def rv_parkinson(df: pd.DataFrame, n: int = 21, periods_per_year: int = TRADING_DAYS) -> pd.Series:
    """Parkinson (1980) high-low estimator: ``sqrt(mean(ln(H/L)^2) / (4 ln 2))``."""
    hl = np.log(df["high"] / df["low"]) ** 2
    return np.sqrt(hl.rolling(n, min_periods=n).mean() / (4 * np.log(2)) * periods_per_year)


def rv_garman_klass(df: pd.DataFrame, n: int = 21, periods_per_year: int = TRADING_DAYS) -> pd.Series:
    """Garman-Klass (1980): ``0.5 ln(H/L)^2 - (2 ln2 - 1) ln(C/O)^2`` averaged."""
    hl = np.log(df["high"] / df["low"]) ** 2
    co = np.log(df["close"] / df["open"]) ** 2
    var = 0.5 * hl - (2 * np.log(2) - 1) * co
    return np.sqrt(var.rolling(n, min_periods=n).mean().clip(lower=0) * periods_per_year)


def rv_yang_zhang(df: pd.DataFrame, n: int = 21, periods_per_year: int = TRADING_DAYS) -> pd.Series:
    """Yang-Zhang (2000) drift-independent estimator including overnight gaps.

    ``var = var_overnight + k var_open_to_close + (1 - k) var_RogersSatchell``,
    ``k = 0.34 / (1.34 + (n + 1) / (n - 1))``. Overnight/open-close variances
    are sample variances (ddof=1); Rogers-Satchell is a mean.
    """
    o = np.log(df["open"] / df["close"].shift(1))
    c = np.log(df["close"] / df["open"])
    rs = (np.log(df["high"] / df["close"]) * np.log(df["high"] / df["open"])
          + np.log(df["low"] / df["close"]) * np.log(df["low"] / df["open"]))
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = (o.rolling(n, min_periods=n).var(ddof=1)
           + k * c.rolling(n, min_periods=n).var(ddof=1)
           + (1 - k) * rs.rolling(n, min_periods=n).mean())
    return np.sqrt(var.clip(lower=0) * periods_per_year)


# --------------------------------------------------------------------------
# Forward-looking labels (research only -- never use as features)
# --------------------------------------------------------------------------
def forward_return(close: pd.Series, n: int) -> pd.Series:
    """Return from close ``t`` to close ``t+n``: ``C_{t+n} / C_t - 1``."""
    return close.shift(-n) / close - 1


def forward_realized_vol(close: pd.Series, n: int = 21, periods_per_year: int = TRADING_DAYS) -> pd.Series:
    """Annualized close-to-close vol of the ``n`` log returns over ``(t, t+n]``.

    Zero-mean (sqrt of mean squared return), which is how a variance swap /
    VIX settles and the right comparison against VIX at ``t``.
    """
    r2 = log_returns(close) ** 2
    fwd = r2.rolling(n, min_periods=n).mean().shift(-n)
    return np.sqrt(fwd * periods_per_year)


# --------------------------------------------------------------------------
# Implied-vol helpers
# --------------------------------------------------------------------------
def iv_rank(iv: pd.Series, n: int = 252) -> pd.Series:
    """IV rank (0-100): where today's IV sits in the trailing ``n``-bar min-max range
    (window includes today)."""
    lo = iv.rolling(n, min_periods=n).min()
    hi = iv.rolling(n, min_periods=n).max()
    return 100 * (iv - lo) / (hi - lo).replace(0, np.nan)


def iv_percentile(iv: pd.Series, n: int = 252) -> pd.Series:
    """IV percentile (0-100): share of the previous ``n - 1`` bars with IV strictly
    below today's (window of ``n`` including today)."""
    return 100 * iv.rolling(n, min_periods=n).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)


def vix_term_ratio(front: pd.Series, back: pd.Series) -> pd.Series:
    """Term-structure ratio, e.g. VIX / VIX3M. < 1 contango (calm), > 1 backwardation (stress).
    Inputs are aligned on their common dates."""
    f, b = front.align(back, join="inner")
    return f / b


# --------------------------------------------------------------------------
# Path / calendar features
# --------------------------------------------------------------------------
def drawdown(close: pd.Series) -> pd.Series:
    """Drawdown from running peak, ``C / cummax(C) - 1`` (<= 0)."""
    return close / close.cummax() - 1


def streak(close: pd.Series) -> pd.Series:
    """Signed consecutive up/down closes: +3 = third up close in a row,
    -2 = second down close in a row, 0 = unchanged close (or first bar)."""
    sgn = np.sign(close.diff()).fillna(0)
    grp = (sgn != sgn.shift()).cumsum()
    run = sgn.groupby(grp).cumcount() + 1
    return (run * sgn).astype(int)


def turn_of_month(index: pd.DatetimeIndex, before: int = 1, after: int = 3) -> pd.Series:
    """True on the last ``before`` and first ``after`` trading days of each month,
    based on the trading days present in ``index``.

    Note: partial months at either end of the index are treated as complete
    (the first available days of the first month are flagged as month-start,
    the last available days of the final month as month-end); trim the
    head/tail if that matters.
    """
    idx = pd.DatetimeIndex(index)
    per = pd.Series(idx.to_period("M"), index=idx)
    from_start = per.groupby(per).cumcount()                      # 0 = first day
    from_end = per.groupby(per).cumcount(ascending=False)         # 0 = last day
    return (from_start < after) | (from_end < before)


def regime(close: pd.Series, vol: pd.Series, trend_n: int = 200,
           vol_threshold: float | None = None, vol_lookback: int = 252) -> pd.Series:
    """Four-state regime: ``"up/low"``, ``"up/high"``, ``"down/low"``, ``"down/high"``.

    * Trend: close above (``up``) or at/below (``down``) its ``trend_n`` SMA.
    * Vol: ``vol`` above ``vol_threshold`` (e.g. VIX > 20) is ``high``; if no
      threshold is given, high = above its trailing ``vol_lookback`` median.
    NaN until both inputs are defined.
    """
    vol = vol.reindex(close.index)
    m = sma(close, trend_n)
    trend = np.where(close > m, "up", "down")
    ref = vol_threshold if vol_threshold is not None else vol.rolling(vol_lookback, min_periods=vol_lookback).median()
    v = np.where(vol > ref, "high", "low")
    out = pd.Series([f"{a}/{b}" for a, b in zip(trend, v)], index=close.index, dtype=object)
    ok = m.notna() & vol.notna()
    if vol_threshold is None:
        ok &= pd.Series(ref, index=close.index).notna()
    return out.where(ok)
