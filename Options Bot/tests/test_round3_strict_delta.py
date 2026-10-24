"""tuning/round3 bug R3-1: strike search stops early when the window-edge strike did not print.

A chain of $1 calls priced at a flat 12% IV; the call search is seeded with a put-side IV of 25%
(as SpreadBook does with iv_hint=short put IV), so the first window sits far out of the money, and
the strike at the window's inner edge has no print. The legacy search then keeps a ~0.03-delta
call; the strict search (engine.STRICT_DELTA_SEARCH) walks in to ~0.10 delta."""
import numpy as np
import pandas as pd
import pytest

from optionsbot import engine as E
from optionsbot import pricing as px


def make_market(skip):
    d = pd.Timestamp("2024-04-30")
    exp = pd.Timestamp("2024-06-28")
    S = 500.0
    m = object.__new__(E.Market)
    m.symbol = "SPY"
    m.q = 0.013
    m.split_dates = []
    m.n_theo = 0
    m.stock = pd.DataFrame({"close": [S], "rv20": [0.12]}, index=[d])
    strikes = np.arange(480.0, 621.0, 1.0)
    syms = [f"C{int(k)}" for k in strikes]
    m._by_exp = {(exp, "C"): pd.DataFrame({"strike_price": strikes, "symbol": syms})}
    T = (exp - d).days / 365.0
    r = px.risk_free(d)
    prices = {s: px.bs_price(S, k, T, r, m.q, 0.12, "C") for s, k in zip(syms, strikes)}

    def bars(symbols, expiry):
        return {s: ({} if s in skip else {d: prices[s]}) for s in symbols}
    m.bars = bars
    return m, d, exp, T, r


def _delta(m, d, exp, q):
    return px.bs_delta(500.0, q.strike, (exp - d).days / 365.0, px.risk_free(d), m.q, 0.12, "C")


@pytest.fixture
def restore():
    old = E.STRICT_DELTA_SEARCH
    yield
    E.STRICT_DELTA_SEARCH = old


def test_legacy_search_stops_on_unprinted_edge(restore):
    E.STRICT_DELTA_SEARCH = False
    m, d, exp, T, r = make_market(skip=set())
    # find the legacy window's inner edge with the 25% seed, then make it not print
    sig0 = 0.25
    strikes = m._by_exp[(exp, "C")]["strike_price"].values
    mdel = np.array([px.bs_delta(500.0, k, T, r, m.q, sig0, "C") for k in strikes])
    center = int(np.argmin(np.abs(mdel - 0.10)))
    edge = f"C{int(strikes[max(0, center - 6)])}"
    m, d, exp, T, r = make_market(skip={edge})
    q = m.select(d, "C", delta=0.10, expiry=exp, iv_hint=sig0)
    assert _delta(m, d, exp, q) < 0.06          # far from target: the bug


def test_strict_search_reaches_target(restore):
    E.STRICT_DELTA_SEARCH = True
    sig0 = 0.25
    m, d, exp, T, r = make_market(skip=set())
    strikes = m._by_exp[(exp, "C")]["strike_price"].values
    mdel = np.array([px.bs_delta(500.0, k, T, r, m.q, sig0, "C") for k in strikes])
    center = int(np.argmin(np.abs(mdel - 0.10)))
    edge = f"C{int(strikes[max(0, center - 6)])}"
    m, d, exp, T, r = make_market(skip={edge})
    q = m.select(d, "C", delta=0.10, expiry=exp, iv_hint=sig0)
    assert abs(_delta(m, d, exp, q) - 0.10) <= 0.025


def test_flag_off_by_default():
    import importlib
    import os
    assert os.environ.get("OPTIONSBOT_STRICT_DELTA") != "1" or E.STRICT_DELTA_SEARCH
    assert importlib.import_module("optionsbot.engine").STRICT_DELTA_TOL == 0.02
