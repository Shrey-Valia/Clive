"""Vectorized Black-Scholes, implied vol, Cboe vol-index panel and term-structure interpolation."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import ndtr

from common import LHCACHE

DIV = {"SPY": 0.015, "IWM": 0.013, "QQQ": 0.007, "DIA": 0.018, "SPX": 0.018}


# ------------------------------------------------------------------ Black-Scholes (arrays)
def bs(S, K, T, r, q, sig, cp):
    """cp: array/scalar of +1 (call) / -1 (put). Returns price."""
    S, K, T, r, q, sig, cp = np.broadcast_arrays(*[np.asarray(x, float) for x in (S, K, T, r, q, sig, cp)])
    out = np.where(cp > 0, np.maximum(S - K, 0), np.maximum(K - S, 0)).astype(float)
    ok = (T > 0) & (sig > 0)
    if ok.any():
        s, k, t, rr, qq, v, c = (x[ok] for x in (S, K, T, r, q, sig, cp))
        vs = v * np.sqrt(t)
        d1 = (np.log(s / k) + (rr - qq + 0.5 * v * v) * t) / vs
        d2 = d1 - vs
        call = s * np.exp(-qq * t) * ndtr(d1) - k * np.exp(-rr * t) * ndtr(d2)
        put = k * np.exp(-rr * t) * ndtr(-d2) - s * np.exp(-qq * t) * ndtr(-d1)
        out[ok] = np.where(c > 0, call, put)
    return out


def delta(S, K, T, r, q, sig, cp):
    S, K, T, r, q, sig, cp = np.broadcast_arrays(*[np.asarray(x, float) for x in (S, K, T, r, q, sig, cp)])
    vs = np.maximum(sig * np.sqrt(np.maximum(T, 1e-9)), 1e-9)
    d1 = (np.log(S / K) + (r - q + 0.5 * sig * sig) * T) / vs
    e = np.exp(-q * T)
    return np.where(cp > 0, e * ndtr(d1), -e * ndtr(-d1))


def implied_vol(P, S, K, T, r, q, cp, lo=0.01, hi=4.0, n=60):
    """Bisection on arrays; NaN where the price is outside [BS(lo), BS(hi)]."""
    P = np.asarray(P, float)
    a = np.full(P.shape, lo)
    b = np.full(P.shape, hi)
    plo = bs(S, K, T, r, q, a, cp)
    phi = bs(S, K, T, r, q, b, cp)
    bad = (P < plo - 1e-9) | (P > phi + 1e-9) | ~np.isfinite(P)
    for _ in range(n):
        m = 0.5 * (a + b)
        pm = bs(S, K, T, r, q, m, cp)
        up = pm > P
        b = np.where(up, m, b)
        a = np.where(up, a, m)
    iv = 0.5 * (a + b)
    iv[bad] = np.nan
    return iv


# ------------------------------------------------------------------ data
def cboe(sym: str) -> pd.Series:
    p = LHCACHE / "cboe" / f"{sym}.csv"
    df = pd.read_csv(p)
    df.columns = [c.strip().upper() for c in df.columns]
    df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    col = "CLOSE" if "CLOSE" in df.columns else df.columns[-1]
    s = pd.to_numeric(df.set_index("DATE")[col], errors="coerce").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s > 0]


def cboe_col(sym: str, col: str) -> pd.Series:
    df = pd.read_csv(LHCACHE / "cboe" / f"{sym}.csv")
    df.columns = [c.strip().upper() for c in df.columns]
    df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    s = pd.to_numeric(df.set_index("DATE")[col], errors="coerce").dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def tbill() -> pd.Series:
    df = pd.read_csv(LHCACHE / "DTB3.csv")
    df.columns = ["DATE", "DTB3"]
    s = pd.to_numeric(df.set_index(pd.to_datetime(df["DATE"]))["DTB3"], errors="coerce").ffill() / 100
    return s


def vol_panel(index) -> pd.DataFrame:
    """VIX9D / VIX / VIX3M / VIX6M (+ RVX, VXN, SKEW, VVIX), reindexed and ffilled (<=3 days).
    VIX3M before 2009-09 is filled from a log-linear fit on VIX and VIX6M (2008-) and before 2008
    from VIX alone (fit on the overlap), VIX9D before 2011 from VIX alone. Flags mark proxies."""
    want = pd.DatetimeIndex(index)
    full = _full_panel()
    return full.reindex(want.union(full.index)).ffill(limit=3).reindex(want)


_FULL = None


def _full_panel() -> pd.DataFrame:
    global _FULL
    if _FULL is not None:
        return _FULL
    idx = cboe("VIX").index
    idx = idx[idx >= "1990-01-01"]
    out = pd.DataFrame(index=idx)
    for s in ["VIX", "VIX9D", "VIX3M", "VIX6M", "RVX", "VXN", "SKEW", "VVIX", "VXD"]:
        out[s] = cboe(s).reindex(idx).ffill(limit=3)
    lv = np.log(out["VIX"])
    # VIX3M from VIX & VIX6M, then from VIX alone
    o = out[["VIX", "VIX3M", "VIX6M"]].dropna()
    X = np.c_[np.ones(len(o)), np.log(o.VIX), np.log(o.VIX6M)]
    b = np.linalg.lstsq(X, np.log(o.VIX3M), rcond=None)[0]
    p1 = np.exp(b[0] + b[1] * lv + b[2] * np.log(out["VIX6M"]))
    o2 = out[["VIX", "VIX3M"]].dropna()
    b2 = np.polyfit(np.log(o2.VIX), np.log(o2.VIX3M), 2)
    p2 = pd.Series(np.exp(np.polyval(b2, lv)), index=idx)
    out["VIX3M_proxy"] = out["VIX3M"].isna()
    out["VIX3M"] = out["VIX3M"].fillna(p1).fillna(p2)
    o3 = out.loc[~out["VIX3M_proxy"], ["VIX", "VIX6M", "VIX3M"]].dropna()
    b3 = np.polyfit(np.log(o3.VIX), np.log(o3.VIX6M), 2)
    out["VIX6M"] = out["VIX6M"].fillna(pd.Series(np.exp(np.polyval(b3, lv)), index=idx))
    o4 = out[["VIX", "VIX9D"]].dropna()
    b4 = np.polyfit(np.log(o4.VIX), np.log(o4.VIX9D), 2)
    out["VIX9D_proxy"] = out["VIX9D"].isna()
    out["VIX9D"] = out["VIX9D"].fillna(pd.Series(np.exp(np.polyval(b4, lv)), index=idx))
    out["TS"] = out["VIX"] / out["VIX3M"]
    tb = tbill()
    out["r"] = tb.reindex(tb.index.union(idx)).ffill().reindex(idx).values
    _FULL = out
    return out


TENORS = np.array([9, 30, 93, 183]) / 365.0


def term_vol(row_vols: np.ndarray, T: np.ndarray) -> np.ndarray:
    """row_vols: (n, 4) VIX9D/VIX/VIX3M/VIX6M in vol points; T: (n,) years.
    Linear interpolation in total variance; flat vol outside [9d, 183d]."""
    v = np.asarray(row_vols, float) / 100.0
    w = v ** 2 * TENORS[None, :]
    T = np.asarray(T, float)
    out = np.empty(len(T))
    Tc = np.clip(T, TENORS[0], TENORS[-1])
    j = np.clip(np.searchsorted(TENORS, Tc) - 1, 0, 2)
    t0, t1 = TENORS[j], TENORS[j + 1]
    a = (Tc - t0) / (t1 - t0)
    wi = w[np.arange(len(T)), j] * (1 - a) + w[np.arange(len(T)), j + 1] * a
    out = np.sqrt(np.maximum(wi, 1e-8) / Tc)
    return out
