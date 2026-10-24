"""The live options book on the proxy, plus the shorting bot's backtest P&L, plus tail hedges.

Live book ($15k): SPY condor F1 (20% max loss, planned first), dip PCS SPY then IWM (25% each),
shared 50% max-loss budget, live kill switch on entries, condor closed the day before expiry.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import psim as P

SHORT_TRADES = Path("/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot/research/results/"
                    "trades_P7_intraday_top5_eq50_min70_strict.csv")
_U = {}


def U(sym):
    if sym not in _U:
        _U[sym] = P.U(sym) if sym != "SPX10" else P.U("SPX10", surf_name="SPY", div=0.018)
    return _U[sym]


def live_sleeves(spy="SPY", iwm="IWM", condor=True, dip=True, dip_kw=None, condor_kw=None, hedge=None):
    out = []
    if condor:
        out.append(P.Condor(U(spy), **(condor_kw or {})))
    if dip:
        names = [U(spy)] + ([U(iwm)] if iwm else [])
        out.append(P.DipPCS(names, **(dip_kw or {})))
    if hedge is not None:
        out.append(P.PutHedge(U(spy), **hedge))
    return out


def run_book(capital=15_000, start="2016-10-20", end="2026-10-20", risk_total=0.5, costs=None, **kw):
    bk = P.Book(capital, start, end, live_sleeves(**kw), risk_total=risk_total, costs=costs).run()
    return bk


def shorting_daily(weight=0.05, stop=0.35) -> pd.Series:
    """Daily return of the shorting bot (P7 backtest, 2016-2026) at `weight` per name and a buy-stop
    loss cap of `stop` per name (the live +35% broker stop; gaps through the stop are ignored)."""
    t = pd.read_csv(SHORT_TRADES, parse_dates=["date"])
    r = t["net_ret"].clip(lower=-stop) if stop else t["net_ret"]
    contrib = r * weight
    return contrib.groupby(t["date"]).sum()


def combine(opt_eq: pd.Series, short_ret: pd.Series, short_capital=10_000.0):
    """Two accounts side by side (options $X, shorting $10k); returns the combined NAV."""
    idx = opt_eq.index
    sr = short_ret.reindex(idx).fillna(0.0)
    s_eq = short_capital * (1 + sr).cumprod()
    return opt_eq + s_eq, s_eq
