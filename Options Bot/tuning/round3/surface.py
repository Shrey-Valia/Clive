"""IV-surface proxy: iv(K, T, t) = base_t(T) * exp(s(z, T, state_t)).

base_t(T): the Cboe SPX vol-index term structure at T (VIX9D/VIX/VIX3M/VIX6M, linear in total
           variance), times RVX/VIX for IWM and VXN/VIX for QQQ.
z        : ln(K/F) / (base * sqrt(T)).
s        : log smile ratio, a cubic B-spline in z (clipped to [-6, 3]) whose coefficients are linear
           in x_T = ln(T / 30d), x_V = ln(VIX / 18) and x_S = VIX/VIX3M - 0.9.
Fit by least squares on ln(iv/base) of every OTM print with volume >= 1 (calib_iv.py).

`fit(sym, end)` fits on prints dated <= end; `Surface.iv(...)` evaluates.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.interpolate import BSpline

from common import HERE, WORK

ZLO, ZHI = -6.0, 3.0
KNOTS_INNER = [-4.5, -3.5, -2.75, -2.0, -1.25, -0.5, 0.25, 1.0, 1.75]
DEG = 3


def _knots():
    return np.r_[[ZLO] * (DEG + 1), KNOTS_INNER, [ZHI] * (DEG + 1)]


def _bz(z):
    z = np.clip(np.asarray(z, float), ZLO, ZHI - 1e-9)
    return BSpline.design_matrix(z, _knots(), DEG).toarray()


def _state(T, vix, ts):
    xT = np.log(np.asarray(T, float) * 365.0 / 30.0)
    xV = np.log(np.asarray(vix, float) / 18.0)
    xS = np.asarray(ts, float) - 0.9
    return np.c_[np.ones_like(xT), xT, xT ** 2, xV, xS, xV * xT]


def design(z, T, vix, ts):
    B = _bz(z)
    S = _state(T, vix, ts)
    return (B[:, :, None] * S[:, None, :]).reshape(len(B), -1)


class Surface:
    def __init__(self, coef, sym):
        self.coef = np.asarray(coef, float)
        self.sym = sym

    def log_ratio(self, z, T, vix, ts):
        return design(np.atleast_1d(z), np.atleast_1d(T), np.atleast_1d(vix), np.atleast_1d(ts)) @ self.coef

    def iv(self, base, z, T, vix, ts):
        return np.asarray(base) * np.exp(self.log_ratio(z, T, vix, ts))

    def save(self, name):
        (HERE / "out" / f"surface_{name}.json").write_text(json.dumps({"sym": self.sym, "coef": list(self.coef),
                                                                        "knots": KNOTS_INNER}))

    @classmethod
    def load(cls, name):
        js = json.loads((HERE / "out" / f"surface_{name}.json").read_text())
        return cls(js["coef"], js["sym"])


def fit(sym, start=None, end=None, max_rows=600_000, seed=0):
    df = pd.read_pickle(WORK / f"iv_{sym}.pkl")
    if start is not None:
        df = df[df.date >= start]
    if end is not None:
        df = df[df.date <= end]
    df = df[(df.dte >= 3) & (df.dte <= 190) & (df.z > -7) & (df.z < 3.5)]
    if len(df) > max_rows:
        df = df.sample(max_rows, random_state=seed)
    X = design(df.z.values, df["T"].values, df.VIX.values, df.TS.values)
    y = np.log(df.ratio.values)
    lam = 1e-3
    A = X.T @ X + lam * np.eye(X.shape[1])
    coef = np.linalg.solve(A, X.T @ y)
    return Surface(coef, sym)
