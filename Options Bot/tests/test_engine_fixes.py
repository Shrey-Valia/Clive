"""Regression tests for the core-engine bugs listed in tuning/*/BUGS.md.

Each test reproduces one bug on synthetic data (no network) and asserts the
fixed behaviour; where a legacy flag keeps the old behaviour, the test also
checks that the flag still reproduces it.

Run:  python3 -m pytest -q tests   (from the Options Bot folder)
"""
import json
import math
import multiprocessing as mp
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optionsbot import data as D  # noqa: E402
from optionsbot import engine as E  # noqa: E402
from optionsbot import metrics as M  # noqa: E402
from optionsbot import pricing as px  # noqa: E402
from optionsbot import strategies as S  # noqa: E402
from optionsbot.engine import Costs, Engine, Market, Quote  # noqa: E402

ZERO = Costs(slip_abs=0, slip_pct=0, fee_per_contract=0, stock_slip_bps=0, sec_fee_rate=0)


# ----------------------------------------------------------------- fakes
class ChainMarket:
    """Scripted market: an underlying path plus a fixed menu of quotes.

    `quotes` maps (cp, key) -> Quote, where key is a target delta or a strike.
    select() only returns quotes on `entry_days` (default: the first day)."""

    def __init__(self, closes, quotes=None, entry_days=None, symbol="TEST"):
        idx = pd.bdate_range("2025-01-06", periods=len(closes))
        self.stock = pd.DataFrame({"close": closes, "split_factor": 1.0, "dividend": 0.0,
                                   "vix": 15.0, "vix_ratio": 0.9}, index=idx)
        self.symbol, self.q, self.n_theo = symbol, 0.0, 0
        self.quotes = quotes or {}
        self.entry_days = set(entry_days if entry_days is not None else [idx[0]])
        exps = sorted({q.expiry for q in self.quotes.values()})
        self.expiries = {"C": exps, "P": exps}
        rows = [{"expiration_date": q.expiry, "cp": q.cp, "strike_price": q.strike}
                for q in self.quotes.values()]
        ch = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["expiration_date", "cp", "strike_price"])
        self._by_exp = {k: g.sort_values("strike_price") for k, g in ch.groupby(["expiration_date", "cp"])}

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

    is_monthly = staticmethod(Market.is_monthly)

    def select(self, d, cp, *, delta=None, strike=None, expiry=None, **kw):
        if d not in self.entry_days:
            return None
        return self.quotes.get((cp, strike if strike is not None else delta))


def quote(cp, K, exp, series, delta=0.0, iv=0.3):
    first = min(series)
    return Quote(f"T{cp}{K}", cp, float(K), exp, float(series[first]), iv, delta, False, dict(series))


def path_series(idx, prices):
    return {d: p for d, p in zip(idx, prices) if p is not None}


# --------------------------------------------------- 1. credit guard (sizing)
def _async_pcs_market():
    m = ChainMarket([600, 600, 600])
    idx = m.stock.index
    exp = idx[-1] + pd.Timedelta(days=30)
    # stale short print: $1.81 "credit" on a $2 wide spread -> max loss looks like $0.19
    m.quotes = {("P", 0.25): quote("P", 575, exp, {idx[0]: 1.86, idx[1]: 0.5}),
                ("P", 0.10): quote("P", 573, exp, {idx[0]: 0.05, idx[1]: 0.3})}
    return ChainMarket([600, 600, 600], m.quotes)


def test_bug1_pcs_async_credit_guard():
    m = _async_pcs_market()
    d0, end = m.stock.index[0], m.stock.index[-1]
    legacy = Engine(m, S.PutCreditSpread(max_credit_frac=None, close_dte=None, dte_min=7),
                    10_000, d0, end, costs=ZERO, early_assign=False).run()
    n_legacy = max(abs(l.qty) for p in legacy.closed + legacy.positions for l in p.legs)
    assert n_legacy > 100            # the bug: 2,500 / 19 -> 131 spreads on a $10k account
    fixed = Engine(m, S.PutCreditSpread(close_dte=None, dte_min=7), 10_000, d0, end,
                   costs=ZERO, early_assign=False).run()
    assert not fixed.closed and not fixed.positions
    assert any("max_credit_frac" in n for n in fixed.notes)


def test_bug1_iron_condor_async_credit_guard():
    m0 = ChainMarket([600, 600, 600])
    idx = m0.stock.index
    exp = idx[-1] + pd.Timedelta(days=45)
    qs = {("P", 0.16): quote("P", 575, exp, {idx[0]: 1.60}),
          ("P", 0.05): quote("P", 573, exp, {idx[0]: 0.05}),
          ("C", 0.16): quote("C", 625, exp, {idx[0]: 0.30}),
          ("C", 0.05): quote("C", 627, exp, {idx[0]: 0.05})}
    m = ChainMarket([600, 600, 600], qs)
    d0, end = idx[0], idx[-1]
    legacy = Engine(m, S.IronCondor(max_credit_frac=None), 10_000, d0, end, costs=ZERO).run()
    assert legacy.positions and abs(legacy.positions[0].legs[0].qty) > 100
    fixed = Engine(m, S.IronCondor(), 10_000, d0, end, costs=ZERO).run()
    assert not fixed.positions


# --------------------------------------------- 2. kill switch on spreads
def _losing_spread_market():
    closes = [100, 97, 96, 96, 96]
    idx = pd.bdate_range("2025-01-06", periods=len(closes))
    exp = idx[-1] + pd.Timedelta(days=40)
    # 100/95 put spread, credit 1.00 -> max loss 4.00/spread; day 1 marks: 3.90 - 0.80 = 3.10 (> 50% of max loss)
    sp = quote("P", 100, exp, path_series(idx, [1.5, 3.9, 3.9, 3.9, 3.9]))
    lp = quote("P", 95, exp, path_series(idx, [0.5, 0.8, 0.8, 0.8, 0.8]))
    return ChainMarket(closes, {("P", 0.25): sp, ("P", 0.10): lp})


def test_bug2_kill_switch_fires_on_spread_loss():
    m = _losing_spread_market()
    d0, end = m.stock.index[0], m.stock.index[-1]
    kw = dict(close_dte=None, sl_mult=None, tp=None, dte_min=7, max_credit_frac=None)
    # legacy: trigger = 8% of short-strike notional ($10,000/spread) > whole max loss ($400/spread)
    legacy = Engine(m, S.PCSKill(kill_loss_basis="notional", **kw), 100_000, d0, end, costs=ZERO).run()
    assert legacy.positions and legacy.kill_events == 0
    pos = legacy.positions[0]
    assert pos.max_loss() < 0.08 * 100 * 100 * abs(pos.legs[0].qty)   # can never fire
    fixed = Engine(m, S.PCSKill(**kw), 100_000, d0, end, costs=ZERO).run()
    assert fixed.kill_events == 1
    assert fixed.closed and fixed.closed[0].reason == "kill_switch"


def test_bug2_max_loss_structural():
    m = _losing_spread_market()
    d0 = m.stock.index[0]
    eng = Engine(m, S.PutCreditSpread(close_dte=None, dte_min=7, max_credit_frac=None), 100_000,
                 d0, d0, costs=ZERO).run()
    p = eng.positions[0]
    n = abs(p.legs[0].qty)
    assert abs(p.max_loss() - (5.0 - 1.0) * 100 * n) < 1e-6
    # a naked short put has no long protection -> None (notional rule applies)
    naked = E.Position(1, "x", [E.Leg("a", "P", 100, d0, -1, 1, 1, 1, 0.3, {})], d0, 100, -100, 0)
    assert naked.max_loss() is None


# --------------------------------------- 3. Wheel call_above_basis is strict
def _wheel_market(above_price):
    closes = [100, 90, 90]
    idx = pd.bdate_range("2025-01-06", periods=len(closes))
    exp = idx[-1] + pd.Timedelta(days=35)
    qs = {("C", 0.30): quote("C", 92, exp, {idx[1]: 1.2}),
          ("C", 101.0): quote("C", 101, exp, {idx[1]: above_price})}
    return ChainMarket(closes, qs, entry_days=[idx[1]])


class _WheelFromShares(S.Wheel):
    def on_day(self, eng, d):
        if d == eng.dates[0]:
            eng.buy_shares(100)          # basis 100
            return
        super().on_day(eng, d)


def test_bug3_wheel_call_above_basis_strict():
    m = _wheel_market(above_price=0.03)          # above-basis call is "worthless"
    d0, end = m.stock.index[0], m.stock.index[-1]
    legacy = Engine(m, _WheelFromShares(call_basis_fallback=True), 50_000, d0, end, costs=ZERO).run()
    assert legacy.positions and legacy.positions[0].legs[0].strike == 92   # below basis 100
    fixed = Engine(m, _WheelFromShares(), 50_000, d0, end, costs=ZERO).run()
    assert not fixed.positions and fixed.shares == 100
    assert any("basis" in n for n in fixed.notes)
    # the above-basis strike is still used when it is worth selling
    ok = Engine(m := _wheel_market(above_price=0.40), _WheelFromShares(), 50_000, d0, end, costs=ZERO).run()
    assert ok.positions[0].legs[0].strike == 101


# ------------------------------------ 4. share-leg P&L in the trade log
class Scripted:
    def __init__(self, legs_fn, reserve=0.0, settle="cash", shares=0, sell_on=None):
        self.legs_fn, self.reserve, self.settle, self.shares = legs_fn, reserve, settle, shares
        self.sell_on = sell_on
        self.p, self.done = {}, False

    def on_day(self, eng, d):
        if not self.done:
            if self.shares:
                eng.buy_shares(self.shares)
            eng.open(self.legs_fn(eng, d), "test", reserve=self.reserve, settle=self.settle)
            self.done = True
        if self.sell_on is not None and d == self.sell_on and eng.shares:
            eng.sell_shares(eng.shares, "exit")


def test_bug4_assignment_trade_log_includes_share_pnl():
    closes = [100, 98, 96, 94, 90, 93, 95]
    m = ChainMarket(closes)
    idx = m.stock.index
    exp = idx[4]
    strat = Scripted(lambda e, d: [(quote("P", 95, exp, {d: 1.5}), -1)], reserve=9_500, settle="physical",
                     sell_on=idx[6])
    eng = Engine(m, strat, 20_000, idx[0], idx[-1], costs=ZERO, early_assign=False).run()
    legacy = eng.trades_frame()
    assert len(legacy) == 1 and legacy.iloc[0]["pnl"] == -350.0   # option leg only: looks like a loss
    full = eng.trades_frame(include_shares=True)
    sh = full[full["kind"] == "shares"]
    assert len(sh) == 1 and abs(sh.iloc[0]["pnl"] - 500.0) < 1e-6  # assigned at 90 (mark), sold 95
    assert abs(full["pnl"].sum() - (eng.equity() - 20_000)) < 1e-6
    met = M.trade_metrics(full)
    assert met["n_trades"] == 2 and met["total_trade_pnl"] == pytest.approx(150.0)


def test_bug4_share_rows_reconcile_with_equity_incl_called_away_and_dividend():
    closes = [100, 101, 103, 106, 108, 110]
    m = ChainMarket(closes)
    m.stock.loc[m.stock.index[2], "dividend"] = 0.5
    idx = m.stock.index
    exp = idx[4]
    strat = Scripted(lambda e, d: [(quote("C", 104, exp, {d: 1.0}), -1)], settle="physical", shares=100)
    eng = Engine(m, strat, 20_000, idx[0], idx[-1], costs=ZERO, early_assign=False, share_rows=True).run()
    tr = eng.trades_frame()                          # share_rows=True -> included by default
    assert set(tr["kind"]) == {"test", "shares"}
    assert eng.shares == 0
    assert abs(tr["pnl"].sum() - (eng.equity() - 20_000)) < 1e-6


# ---------------------------------------- 5. covered call roll_itm flag
def _cc_market():
    closes = [100, 104, 108, 110, 112]
    idx = pd.bdate_range("2025-01-06", periods=len(closes))
    exp = idx[-1]
    c = quote("C", 103, exp, path_series(idx, [1.5, 3.0, 6.0, 7.5, 9.0]))
    return ChainMarket(closes, {("C", 0.30): c})


def test_bug5_covered_call_roll_itm_flag():
    m = _cc_market()
    d0, end = m.stock.index[0], m.stock.index[-1]
    kw = dict(roll_dte=30, dte_min=1)
    legacy = Engine(m, S.CoveredCall(**kw), 20_000, d0, end, costs=ZERO, early_assign=False).run()
    assert legacy.closed[0].reason == "roll"           # bought back ITM (default, unchanged)
    assert legacy.closed[0].entry_cash + legacy.closed[0].exit_cash < 0
    hold = Engine(m, S.CoveredCall(roll_itm=False, **kw), 20_000, d0, end, costs=ZERO,
                  early_assign=False).run()
    assert hold.closed[0].reason == "expired_itm"      # held, shares called away at 103
    assert any(t["side"] == "called_away" for t in hold.stock_trades)
    assert hold.shares == 100                          # lots re-bought after call-away


# ---------------------------------------------- 6. engine-level signal lag
def test_bug6_signal_lag_fills_next_close():
    closes = [100, 100, 100, 100]
    m = ChainMarket(closes)
    idx = m.stock.index
    exp = idx[-1] + pd.Timedelta(days=30)
    ser = path_series(idx, [2.0, 2.5, 2.6, 2.7])
    mk = lambda e, d: [(quote("P", 95, exp, {k: v for k, v in ser.items()}), -1)]  # noqa: E731
    lag0 = Engine(m, Scripted(mk, reserve=9_500), 20_000, idx[0], idx[-1], costs=ZERO).run()
    assert lag0.positions[0].open_date == idx[0] and lag0.positions[0].legs[0].entry_price == 2.0
    lag1 = Engine(m, Scripted(mk, reserve=9_500), 20_000, idx[0], idx[-1], costs=ZERO, signal_lag=1).run()
    p = lag1.positions[0]
    assert p.open_date == idx[1] and p.legs[0].entry_price == 2.5   # decided on d0, filled at d1 close


class _CloseOnDay:
    def __init__(self, close_day):
        self.p, self.close_day, self.done = {}, close_day, False

    def on_day(self, eng, d):
        if not self.done:
            exp = eng.dates[-1] + pd.Timedelta(days=30)
            ser = path_series(eng.dates, [2.0, 2.5, 3.0, 3.5, 4.0])
            eng.open([(quote("P", 95, exp, ser), -1)], "t", reserve=9_500)
            self.done = True
        if d == self.close_day:
            for pos in list(eng.positions):
                eng.close(pos, "signal_exit")


def test_bug6_signal_lag_delays_exits_too():
    m = ChainMarket([100] * 5)
    idx = m.stock.index
    eng = Engine(m, _CloseOnDay(idx[2]), 20_000, idx[0], idx[-1], costs=ZERO, signal_lag=1).run()
    p = eng.closed[0]
    assert p.open_date == idx[1] and p.close_date == idx[3]
    assert p.entry_cash == pytest.approx(250.0) and p.exit_cash == pytest.approx(-350.0)


# ------------------------------------------- 7. macOS fork-safe proxies
def _child_proxy_lookup():
    import requests
    requests.utils.get_environ_proxies("https://data.alpaca.markets/v2/stocks/bars")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only crash (_scproxy)")
def test_bug7_no_proxy_set_and_forked_child_survives():
    assert os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or any(
        os.environ.get(k) for k in D.PROXY_ENV_VARS)
    ctx = mp.get_context("fork")
    pr = ctx.Process(target=_child_proxy_lookup)
    pr.start()
    pr.join(60)
    assert pr.exitcode == 0


# ------------------------------------------------- 8. TSLA / NFLX yields
def test_bug8_tsla_nflx_dividend_yield_zero():
    assert px.div_yield("TSLA") == 0.0
    assert px.div_yield("NFLX") == 0.0


# --------------------------------------------- 9. option bar volume
def test_bug9_bars_volume_exposed(tmp_path):
    bars = [{"t": "2025-01-07", "c": 1.25, "v": 12}, {"t": "2025-01-06", "c": 1.10, "v": 0}]
    s = D._bars_to_series(bars)
    assert list(s.values) == [1.10, 1.25]                 # unchanged default (closes)
    assert list(D._bars_to_series(bars, field="v").values) == [0, 12]
    f = D._bars_to_frame(bars)
    assert list(f.columns) == ["close", "volume"] and f["volume"].iloc[1] == 12
    cli = D.AlpacaData(cache_dir=tmp_path, offline=True)
    sym = "SPY250117P00500000"
    cli._cache_path("optbars", f"{sym}|2024-01-01|2025-01-17").write_text(json.dumps(bars))
    got = cli.option_bar_frames([sym], "2024-01-01", "2025-01-17")
    assert got[sym]["volume"].tolist() == [0, 12]


# ------------------------------------------- 10. bars cache key vs end
class _KeyClient:
    def __init__(self):
        self.calls = []

    def option_bars(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        return {}


def test_bug10_bar_cache_key_independent_of_market_end():
    keys = []
    for end in ("2025-06-30", "2026-03-31", E.DATA_END):
        m = Market.__new__(Market)
        m.client, m.end = _KeyClient(), pd.Timestamp(end)
        m.bars(["X"], pd.Timestamp("2026-12-18"))         # expires after every end above
        keys.append(m.client.calls[0][1:])
    assert len(set(keys)) == 1
    m = Market.__new__(Market)
    m.client, m.end = _KeyClient(), pd.Timestamp("2025-06-30")
    m.bars(["X"], pd.Timestamp("2025-03-21"))               # expired contracts: key = expiry (as before)
    assert m.client.calls[0][2] == "2025-03-21"


# ---------------------------------------------- 11. Thursday monthlies
def test_bug11_is_monthly_thursday_only_when_friday_is_holiday():
    T = pd.Timestamp
    assert Market.is_monthly(T("2025-04-17"))        # Thu before Good Friday 2025-04-18
    assert not Market.is_monthly(T("2025-04-18"))    # Good Friday: market closed
    assert Market.is_monthly(T("2026-06-18"))        # Thu before Juneteenth (Fri 2026-06-19)
    assert Market.is_monthly(T("2025-03-21"))        # ordinary 3rd Friday
    assert not Market.is_monthly(T("2025-03-20"))    # ordinary Thursday daily expiry (the bug)
    assert not Market.is_monthly(T("2024-12-19"))
    assert Market.is_monthly(T("2025-03-20"), legacy=True)   # legacy rule kept behind a flag


# --------------------------------------------- 12. profit factor is finite
def test_bug12_profit_factor_no_losses_is_none():
    tr = pd.DataFrame({"reason": ["expired"] * 3, "pnl": [10.0, 20.0, 5.0], "days_held": [5] * 3,
                       "theo_entry": [False] * 3, "stale_mark_days": [0] * 3, "assigned": [False] * 3})
    pf = M.trade_metrics(tr)["profit_factor"]
    assert pf is None
    json.dumps({"pf": pf}, allow_nan=False)        # strict JSON serialisable
    tr.loc[0, "pnl"] = -5.0
    assert M.trade_metrics(tr)["profit_factor"] == pytest.approx(5.0)
