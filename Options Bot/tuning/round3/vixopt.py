"""VIX futures / VIX option proxy, validated against the Cboe VXTH index.

VIX future for expiry T (the Wednesday 30 days before the next month's 3rd Friday):
  F = forward 30-day vol implied by the SPX vol-index term structure between T and T+30d
      (total-variance interpolation of VIX9D/VIX/VIX3M/VIX6M), minus a convexity adjustment
      0.5 * F * (vv^2 * tau), vv = VVIX/100;  F -> VIX spot as tau -> 0.
VIX option: Black-76 on F with vol = VVIX/100 x vv_mult (fitted on VXTH, see below), settled at
the VIX open on the expiry Wednesday (the SOQ proxy).

VXTH (Cboe VIX Tail Hedge): S&P 500 + 1-month 30-delta VIX calls, bought at each VIX expiry;
weight 0% if VIX <= 15, 1% if 15 < VIX <= 30, 0.5% if 30 < VIX <= 50, 0% above (spot VIX here
stands in for the front future). Overlay return per roll = VXTH return - (1 - w) x SPX return;
the proxy rebuilds it and we compare.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.special import ndtr

import r3lib as L


def vix_expiries(start="2004-01-01", end="2027-12-31", trading=None):
    out = []
    for m in pd.date_range(start, end, freq="MS"):
        nxt = m + pd.DateOffset(months=1)
        offs = (4 - nxt.weekday()) % 7
        third_fri = nxt + pd.Timedelta(days=offs + 14)
        e = third_fri - pd.Timedelta(days=30)
        if trading is not None:
            k = 0
            while e not in trading and k < 5:
                e = e - pd.Timedelta(days=1)
                k += 1
        out.append(e)
    return out


def b76_call(F, K, T, sig, r=0.0):
    if T <= 0 or sig <= 0:
        return max(F - K, 0.0)
    v = sig * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    return math.exp(-r * T) * (F * ndtr(d1) - K * ndtr(d1 - v))


def b76_delta(F, K, T, sig):
    if T <= 0 or sig <= 0:
        return 1.0 if F > K else 0.0
    v = sig * math.sqrt(T)
    return float(ndtr((math.log(F / K) + 0.5 * v * v) / v))


class VixModel:
    def __init__(self, vv_mult=0.85, conv=1.0):
        idx = L.cboe("VIX").index
        self.vp = L.vol_panel(idx)
        self.vix_open = L.cboe_col("VIX", "OPEN") if hasattr(L, "cboe_col") else None
        self.vv_mult = vv_mult
        self.conv = conv

    def future(self, d, T_exp):
        row = self.vp.loc[d]
        tau = max((T_exp - d).days, 0) / 365.0
        vols = row[["VIX9D", "VIX", "VIX3M", "VIX6M"]].to_numpy(float)[None, :]
        if tau <= 1 / 365:
            return float(row["VIX"])
        t1 = np.array([tau + 30 / 365.0])
        t0 = np.array([tau])
        w1 = L.term_vol(vols, t1)[0] ** 2 * t1[0]
        w0 = L.term_vol(vols, t0)[0] ** 2 * t0[0]
        fwd = math.sqrt(max(w1 - w0, 1e-8) / (30 / 365.0)) * 100
        # blend toward spot for short tau (the front future converges to spot VIX)
        a = min(1.0, tau / (30 / 365.0))
        fwd = a * fwd + (1 - a) * float(row["VIX"])
        vv = self.vol(d)
        return fwd * (1 - 0.5 * self.conv * vv * vv * tau)

    def vol(self, d):
        vv = self.vp.at[d, "VVIX"]
        if not np.isfinite(vv):
            vv = 85.0
        return vv / 100.0 * self.vv_mult

    def call(self, d, T_exp, K):
        F = self.future(d, T_exp)
        tau = max((T_exp - d).days, 0) / 365.0
        return b76_call(F, K, tau, self.vol(d)), F

    def strike_for_delta(self, d, T_exp, delta=0.30, step=1.0):
        F = self.future(d, T_exp)
        tau = max((T_exp - d).days, 0) / 365.0
        Ks = np.arange(max(step, math.floor(F)), F * 4, step)
        ds = np.array([b76_delta(F, k, tau, self.vol(d)) for k in Ks])
        return float(Ks[int(np.argmin(np.abs(ds - delta)))])
