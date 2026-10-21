"""Unit tests for pricing and engine mechanics (synthetic data, no network).

Run:  python3 -m pytest tests/  (or python3 tests/test_pricing_engine.py)
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optionsbot import pricing as px  # noqa: E402
from optionsbot.engine import Costs, Engine, Quote  # noqa: E402
from optionsbot import metrics as M  # noqa: E402


# ------------------------------------------------------------- pricing
def test_put_call_parity():
    S, K, T, r, q, v = 100, 95, 0.25, 0.04, 0.01, 0.3
    c = px.bs_price(S, K, T, r, q, v, "C")
    p = px.bs_price(S, K, T, r, q, v, "P")
    assert abs((c - p) - (S * math.exp(-q * T) - K * math.exp(-r * T))) < 1e-9


def test_iv_round_trip():
    for cp in "CP":
        for K in (70, 90, 100, 110, 130):
            for v in (0.08, 0.2, 0.45, 1.2):
                for T in (7 / 365, 40 / 365, 1.0):
                    price = px.bs_price(100, K, T, 0.045, 0.01, v, cp)
                    if price < 1e-3:
                        continue
                    iv = px.implied_vol(price, 100, K, T, 0.045, 0.01, cp)
                    assert iv is not None
                    assert abs(px.bs_price(100, K, T, 0.045, 0.01, iv, cp) - price) < 1e-4


def test_iv_rejects_arbitrage_prices():
    assert px.implied_vol(0.5, 100, 80, 0.1, 0.04, 0.0, "C") is None   # below intrinsic
    assert px.implied_vol(150, 100, 80, 0.1, 0.04, 0.0, "C") is None   # above spot


def test_delta_signs_and_bounds():
    dc = px.bs_delta(100, 100, 0.1, 0.04, 0, 0.2, "C")
    dp = px.bs_delta(100, 100, 0.1, 0.04, 0, 0.2, "P")
    assert 0.5 < dc < 0.6 and -0.5 < dp < -0.4
    assert abs(dc - dp - 1) < 1e-9


# --------------------------------------------------------------- engine
class FakeMarket:
    """Minimal Market stand-in: a scripted underlying path, no chain."""

    def __init__(self, closes, symbol="TEST"):
        idx = pd.bdate_range("2025-01-06", periods=len(closes))
        self.stock = pd.DataFrame({"close": closes, "split_factor": 1.0, "dividend": 0.0}, index=idx)
        self.symbol = symbol
        self.q = 0.0
        self.n_theo = 0
        self.expiries = {"C": [], "P": []}

    def spot(self, d):
        return float(self.stock.at[d, "close"])

    def T(self, d, exp):
        return max((exp - d).days, 0) / 365

    def r(self, d):
        return 0.04

    def iv_from_price(self, price, d, K, exp, cp):
        T = self.T(d, exp)
        return px.implied_vol(price, self.spot(d), K, T, 0.04, 0.0, cp) if T > 0 else None

    def model_price(self, d, K, exp, cp, iv):
        return px.bs_price(self.spot(d), K, self.T(d, exp), 0.04, 0.0, iv, cp)

    def model_delta(self, d, K, exp, cp, iv):
        return px.bs_delta(self.spot(d), K, self.T(d, exp), 0.04, 0.0, iv, cp)

    @staticmethod
    def is_monthly(e):
        return False


class Scripted:
    """Strategy that opens given legs on day 0 and does nothing else."""

    def __init__(self, legs_fn, reserve_fn=lambda: 0.0, settle="cash", shares=0):
        self.legs_fn, self.reserve_fn, self.settle, self.shares = legs_fn, reserve_fn, settle, shares
        self.p = {}
        self.done = False

    def on_day(self, eng, d):
        if not self.done:
            if self.shares:
                eng.buy_shares(self.shares)
            eng.open(self.legs_fn(eng, d), "test", reserve=self.reserve_fn(), settle=self.settle)
            self.done = True


def q(m, d, cp, K, exp, price):
    return Quote(f"T{cp}{K}", cp, K, exp, price, 0.3, 0.0, False, {d: price})


ZERO = Costs(slip_abs=0, slip_pct=0, fee_per_contract=0, stock_slip_bps=0, sec_fee_rate=0)


def test_short_put_expires_itm_cash_settles_at_intrinsic():
    closes = [100, 99, 97, 95, 92]
    m = FakeMarket(closes)
    exp = m.stock.index[-1]
    strat = Scripted(lambda e, d: [(q(m, d, "P", 100, exp, 2.0), -1)], lambda: 100 * 100)
    eng = Engine(m, strat, 20_000, m.stock.index[0], exp, costs=ZERO, early_assign=False).run()
    tr = eng.trades_frame()
    # credit 200, intrinsic at expiry (100-92)*100 = 800 -> pnl -600
    assert len(tr) == 1 and tr.iloc[0]["pnl"] == -600.0
    assert tr.iloc[0]["reason"] == "expired_itm"
    assert abs(eng.cash - (20_000 - 600)) < 1e-6
    assert eng.reserved() == 0


def test_wheel_put_assignment_delivers_shares():
    closes = [100, 98, 96, 94, 90]
    m = FakeMarket(closes)
    exp = m.stock.index[-1]
    strat = Scripted(lambda e, d: [(q(m, d, "P", 95, exp, 1.5), -1)], lambda: 95 * 100, settle="physical")
    eng = Engine(m, strat, 20_000, m.stock.index[0], exp, costs=ZERO, early_assign=False).run()
    assert eng.shares == 100
    assert abs(eng.cash - (20_000 + 150 - 9_500)) < 1e-6
    assert abs(eng.equity() - (20_000 + 150 - 9_500 + 100 * 90)) < 1e-6   # = 19,650


def test_otm_expiry_keeps_full_credit():
    m = FakeMarket([100, 101, 102, 103])
    exp = m.stock.index[-1]
    strat = Scripted(lambda e, d: [(q(m, d, "C", 110, exp, 0.8), -1)], shares=100)
    eng = Engine(m, strat, 20_000, m.stock.index[0], exp, costs=ZERO, early_assign=False).run()
    tr = eng.trades_frame()
    assert tr.iloc[0]["pnl"] == 80.0 and tr.iloc[0]["reason"] == "expired"


def test_put_spread_loss_capped_at_width_minus_credit():
    closes = [100, 95, 85, 70, 60]          # crash far through both strikes
    m = FakeMarket(closes)
    exp = m.stock.index[-1]
    width, credit = 5.0, 1.2
    strat = Scripted(lambda e, d: [(q(m, d, "P", 95, exp, 2.0), -2), (q(m, d, "P", 90, exp, 0.8), 2)],
                     lambda: width * 100 * 2)
    eng = Engine(m, strat, 10_000, m.stock.index[0], exp, costs=ZERO, early_assign=False).run()
    tr = eng.trades_frame()
    max_loss = (width - credit) * 100 * 2
    assert abs(tr.iloc[0]["pnl"] + max_loss) < 1e-6
    assert eng.equity_frame()["equity"].min() >= 10_000 - max_loss - 1e-6


def test_capital_check_blocks_oversized_position():
    m = FakeMarket([100, 100, 100])
    exp = m.stock.index[-1]
    strat = Scripted(lambda e, d: [(q(m, d, "P", 100, exp, 2.0), -2)], lambda: 100 * 100 * 2)
    eng = Engine(m, strat, 15_000, m.stock.index[0], exp, costs=ZERO).run()
    assert eng.trades_frame().empty      # 2 puts need $20k collateral


def test_slippage_and_fees_reduce_credit():
    m = FakeMarket([100, 100])
    exp = m.stock.index[-1] + pd.Timedelta(days=30)
    c = Costs(slip_abs=0.03, slip_pct=0.02, fee_per_contract=0.03)
    strat = Scripted(lambda e, d: [(q(m, d, "P", 90, exp, 1.0), -1)], lambda: 9_000)
    eng = Engine(m, strat, 20_000, m.stock.index[0], m.stock.index[0], costs=c).run()
    p = eng.positions[0]
    assert abs(p.entry_cash - ((1.0 - 0.03) * 100 - 0.03)) < 1e-9


def test_metrics_drawdown():
    eq = pd.Series([100, 110, 99, 120, 60, 90], index=pd.bdate_range("2025-01-01", periods=6))
    m = M.equity_metrics(eq)
    assert abs(m["max_drawdown"] - (-0.5)) < 1e-9
    assert m["max_dd_duration_days"] == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
