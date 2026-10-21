"""Offline unit tests for the live dip put credit spread bot (no network).

Run:  python3 -m pytest -q live/tests/
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from live import config as C  # noqa: E402
from live import execution, signals, state  # noqa: E402
from live import strategy as S  # noqa: E402
from live import run as R  # noqa: E402
from live.broker import Broker, SafetyError  # noqa: E402
from optionsbot import indicators as ind  # noqa: E402

TODAY = dt.date(2026, 10, 21)          # a Wednesday
UTC = dt.timezone.utc


# ====================================================================== fakes
def Q(strike, delta, bid, ask, exp=dt.date(2026, 10, 30), und="SPY"):
    return S.OptionQuote(S.occ_symbol(und, exp, "P", strike), und, exp, "P", strike, bid, ask, delta)


def chain_for(exp=dt.date(2026, 10, 30), und="SPY", spot=700.0):
    """Puts every $5 from spot-80 to spot with a smooth delta curve and penny quotes."""
    out = []
    for k in np.arange(spot - 80, spot + 1, 5.0):
        m = (spot - k) / spot
        delta = -0.5 * math.exp(-m / 0.028)
        px = max(0.05, 12.0 * abs(delta) ** 1.1)
        out.append(Q(float(k), round(delta, 4), round(px - 0.02, 2), round(px + 0.02, 2), exp, und))
    return out


def bars_dip(n=260, start=550.0):
    """Long uptrend then three down days: close > 200DMA, RSI2 low."""
    idx = pd.bdate_range(end=pd.Timestamp(TODAY) - pd.Timedelta(days=1), periods=n)
    c = start * np.exp(np.linspace(0, 0.30, n))
    c[-3:] = c[-4] * np.array([0.99, 0.98, 0.97])
    df = pd.DataFrame({"open": c * 1.001, "high": c * 1.005, "low": c * 0.995, "close": c}, index=idx)
    return df


class FakeClock:
    def __init__(self, t0=dt.datetime(2026, 10, 21, 19, 50, tzinfo=UTC)):
        self.t = t0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += dt.timedelta(seconds=max(s, 0.001))


class FakeBroker:
    """Order book that fills an mleg order once its price is good enough.

    credit orders (negative limit): fill when |limit| <= fill_at
    debit orders  (positive limit): fill when limit >= fill_at
    """

    def __init__(self, fill_at: dict | None = None, replace_fails=False, never_fill=False):
        self.fill_at = fill_at or {}
        self.orders = {}
        self.n = 0
        self.replace_fails = replace_fails
        self.never_fill = never_fill
        self.log = []

    def _new(self, legs, qty, price, key):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"id": oid, "status": "new", "qty": qty, "limit": price, "legs": legs,
                            "filled_qty": "0", "filled_avg_price": None, "key": key}
        return dict(self.orders[oid])

    def _key(self, legs):
        return legs[0]["symbol"]

    def submit_mleg(self, legs, qty, limit_price, client_order_id):
        self.log.append(("submit", limit_price))
        return self._new(legs, qty, limit_price, self._key(legs))

    def replace_order(self, oid, limit_price):
        self.log.append(("replace", limit_price))
        if self.replace_fails:
            raise RuntimeError("HTTP 422 order not replaceable")
        o = self.orders[oid]
        o["status"] = "replaced"
        return self._new(o["legs"], o["qty"], limit_price, o["key"])

    def cancel_order(self, oid):
        self.log.append(("cancel", oid))
        o = self.orders[oid]
        if o["status"] not in ("filled",):
            o["status"] = "canceled"

    def get_order(self, oid):
        o = self.orders[oid]
        if o["status"] == "new" and not self.never_fill:
            thr = self.fill_at.get(o["key"])
            lim = o["limit"]
            if thr is not None and ((lim < 0 and abs(lim) <= thr) or (lim > 0 and lim >= thr)):
                o["status"] = "filled"
                o["filled_qty"] = str(o["qty"])
                o["filled_avg_price"] = str(lim)
        return dict(o)


class FakeDataBroker:
    """Market data + account for the planner (no trading account)."""
    has_trading = False

    def __init__(self, hist, live, chain, positions=None):
        self.hist, self.live, self.chain = hist, live, chain
        self._positions = positions or []

    def daily_bars(self, sym, start, end):
        return self.hist[sym]

    def stock_snapshot(self, sym):
        lv = self.live[sym]
        return {"price": lv["close"], "trade_time": None, "bar_date": TODAY, "open": lv["open"],
                "high": lv["high"], "low": lv["low"], "close": lv["close"], "feed": "fake"}

    def sip_intraday_range(self, sym, day):
        return None

    def option_chain(self, sym, lo, hi, cp="P"):
        return [q for q in self.chain if q.underlying == sym and lo <= q.expiry <= hi]

    def option_quotes(self, syms):
        return {q.symbol: q for q in self.chain if q.symbol in syms}

    def positions(self):
        return self._positions


# ====================================================================== signals
def test_with_today_appends_partial_bar_and_replaces_existing():
    h = bars_dip()
    b = signals.with_today(h, TODAY, 10, 11, 9, 9.5)
    assert b.index[-1] == pd.Timestamp(TODAY) and len(b) == len(h) + 1
    b2 = signals.with_today(b, TODAY, 10, 11, 9, 10.5)            # re-run replaces today's row
    assert len(b2) == len(b) and b2["close"].iloc[-1] == 10.5
    b3 = signals.with_today(h, TODAY, 10, 10.2, 9.8, 10.6)        # live price above the high widens it
    assert b3["high"].iloc[-1] == 10.6


def test_compute_matches_indicators_module():
    h = bars_dip()
    last = h["close"].iloc[-1]
    b = signals.with_today(h, TODAY, last, last * 1.002, last * 0.97, last * 0.975)
    sig = signals.compute("SPY", b)
    assert sig.asof == TODAY
    assert sig.rsi2 == pytest.approx(ind.rsi(b["close"], 2).iloc[-1])
    assert sig.ibs == pytest.approx((last * 0.975 - last * 0.97) / (last * 1.002 - last * 0.97))
    assert sig.sma200 == pytest.approx(b["close"].iloc[-200:].mean())
    assert sig.sma5 == pytest.approx(b["close"].iloc[-5:].mean())


def test_entry_signal_dip_passes_and_reasons_listed():
    h = bars_dip()
    last = h["close"].iloc[-1]
    sig = S.entry_signal(signals.compute("SPY", signals.with_today(h, TODAY, last, last * 1.001, last * 0.97,
                                                                   last * 0.972)))
    assert sig.ok, sig.reasons
    bad = S.entry_signal(S.Signal("X", 100, 110, 99, 50, 0.5, TODAY))
    assert not bad.ok and len(bad.reasons) == 3
    nan = S.entry_signal(S.Signal("X", 100, math.nan, 99, 5, 0.1, TODAY))
    assert not nan.ok and "not available" in nan.reasons[0]
    # boundaries are strict
    assert not S.entry_signal(S.Signal("X", 100, 90, 99, C.RSI_MAX, 0.1, TODAY)).ok
    assert not S.entry_signal(S.Signal("X", 100, 90, 99, 5, C.IBS_MAX, TODAY)).ok


def test_kill_switch():
    assert S.kill_switch(18, 20)[0] is False
    assert S.kill_switch(24, 20)[0] is True                       # ratio 1.2
    assert S.kill_switch(41, 40)[0] is True                       # VIX > 40
    assert S.kill_switch(None, 20)[0] is True                     # fail closed


def test_sessions_and_day_counts():
    idx = pd.bdate_range("2026-10-12", "2026-10-21")
    ses = signals.sessions_from_bars(idx, TODAY)
    assert S.held_trading_days(dt.date(2026, 10, 14), TODAY, ses) == 5
    assert S.trading_days_until(dt.date(2026, 10, 23), TODAY, ses) == 2
    assert S.trading_days_until(dt.date(2026, 10, 22), TODAY, ses) == 1


# ====================================================================== strikes
def test_rank_expiries_prefers_target_friday():
    exps = [dt.date(2026, 10, 26), dt.date(2026, 10, 28), dt.date(2026, 10, 30), dt.date(2026, 11, 4),
            dt.date(2026, 11, 20)]
    r = S.rank_expiries(exps, TODAY)
    assert r[0] == dt.date(2026, 10, 30)                            # Friday, 9 DTE
    assert dt.date(2026, 10, 26) not in r                           # 5 DTE outside window
    assert dt.date(2026, 11, 20) not in r                           # 30 DTE outside window


def test_pick_spread_deltas_and_same_expiry():
    ch = chain_for() + chain_for(exp=dt.date(2026, 10, 28))
    p, why = S.pick_spread(ch, "SPY", TODAY)
    assert p is not None, why
    assert p.expiry == dt.date(2026, 10, 30) and p.long.expiry == p.short.expiry
    assert abs(abs(p.short.delta) - 0.25) < 0.05 and abs(abs(p.long.delta) - 0.10) < 0.05
    assert p.long.strike < p.short.strike


def test_pick_spread_skips_unquoted_and_falls_back_to_next_expiry():
    good = chain_for(exp=dt.date(2026, 10, 28))
    dead = [S.OptionQuote(q.symbol, q.underlying, q.expiry, "P", q.strike, 0.0, 0.0, q.delta)
            for q in chain_for()]                                     # 10-30 has no bids
    p, why = S.pick_spread(dead + good, "SPY", TODAY)
    assert p is not None and p.expiry == dt.date(2026, 10, 28)
    p, why = S.pick_spread(dead, "SPY", TODAY)
    assert p is None and "no quoted put" in why
    p, why = S.pick_spread([], "SPY", TODAY)
    assert p is None


def test_pick_spread_requires_delta_within_tolerance():
    ch = [Q(650, -0.45, 5, 5.1), Q(640, -0.40, 4, 4.1)]
    p, why = S.pick_spread(ch, "SPY", TODAY)
    assert p is None


# ====================================================================== credit / width guard
def _pick(sb, sa, lb, la, ks=700, kl=690):
    return S.Pick("SPY", dt.date(2026, 10, 30), Q(ks, -0.25, sb, sa), Q(kl, -0.10, lb, la), 9)


def test_guard_rejects_credit_above_60pct_of_width():               # BUGS.md #7
    p = _pick(8.0, 8.05, 1.0, 1.05)                                # 7.0 credit on 10 wide
    assert "bad quote guard" in S.check_pick(p)


def test_guard_rejects_tiny_credit():
    p = _pick(0.40, 0.42, 0.20, 0.22)                              # 0.20 on 10 wide = 2%
    assert "<" in S.check_pick(p)


def test_guard_rejects_wide_markets():
    assert "bid/ask too wide" in S.check_pick(_pick(1.5, 2.5, 0.4, 0.45))
    assert S.check_pick(_pick(2.00, 2.03, 0.80, 0.82)) is None    # 1.205 on 10 wide = 12%


def test_entry_ladder_monotone_and_floored():
    p = _pick(2.00, 2.10, 0.80, 0.85)                              # mid 1.225, natural 1.15
    lad = S.entry_ladder(p)
    assert lad[0] == 1.22 and lad == sorted(lad, reverse=True)
    assert lad[-1] >= round(p.mid_credit - 0.5 * (p.mid_credit - p.natural_credit), 2) - 0.01
    assert all(x >= C.MIN_CREDIT_FRAC * p.width for x in lad)


def test_exit_ladder_and_cutoff_price():
    lad = S.exit_ladder(0.50, 0.60)
    assert lad[0] == 0.50 and lad[-1] == 0.60 and lad == sorted(lad)
    assert S.exit_cutoff_price(0.60, 10) == 0.65
    assert S.exit_cutoff_price(9.99, 10) == 10                      # never above width


# ====================================================================== sizing
def test_size_per_position_cap():
    n, _ = S.size_position(10_000, 10, 1.2, 0)                     # 880 max loss -> 2500 // 880
    assert n == 2


def test_size_total_cap():
    n, _ = S.size_position(10_000, 10, 1.2, 4_500)                 # only 500 room under 50%
    assert n == 0
    n, _ = S.size_position(10_000, 5, 0.6, 4_000)                  # 440/spread, 1000 room
    assert n == 2


def test_size_buying_power_and_max_contracts():
    n, why = S.size_position(10_000, 10, 1.2, 0, options_bp=1_500)
    assert n == 1 and "BP" in why
    n, _ = S.size_position(10_000_000, 1, 0.12, 0)
    assert n == C.MAX_CONTRACTS


# ====================================================================== exits
BASE = dict(entry_credit=1.00, close=100, sma5=101, held_days=2, trading_days_to_expiry=5, end_of_day=True)


def test_exit_take_profit():
    assert S.exit_reason(**{**BASE, "cost_to_close": 0.50}) == "take_profit"
    assert S.exit_reason(**{**BASE, "cost_to_close": 0.51}) is None


def test_exit_sma5_requires_one_day():
    assert S.exit_reason(**{**BASE, "cost_to_close": 0.8, "close": 102}) == "sma5_exit"
    assert S.exit_reason(**{**BASE, "cost_to_close": 0.8, "close": 102, "held_days": 0}) is None


def test_exit_time_and_expiry():
    assert S.exit_reason(**{**BASE, "cost_to_close": 0.8, "held_days": 5}) == "time_exit"
    assert S.exit_reason(**{**BASE, "cost_to_close": 0.8, "trading_days_to_expiry": 1}) == "expiry_exit"


def test_exit_stop_optional():
    assert S.exit_reason(**{**BASE, "cost_to_close": 2.5}) is None                 # off by default
    # research semantics (strategies.credit_exit): stop when cost >= credit * (1 + sl_mult)
    assert S.exit_reason(**{**BASE, "cost_to_close": 2.9, "stop_mult": 2.0}) is None
    assert S.exit_reason(**{**BASE, "cost_to_close": 3.0, "stop_mult": 2.0}) == "stop_loss"


def test_intraday_only_price_rules():
    kw = {**BASE, "end_of_day": False, "held_days": 9, "close": 200}
    assert S.exit_reason(**{**kw, "cost_to_close": 0.8}) is None
    assert S.exit_reason(**{**kw, "cost_to_close": 0.4}) == "take_profit"


# ====================================================================== positions
def test_group_spreads_pairs_and_orphans():
    e = dt.date(2026, 10, 30)
    pos = [{"symbol": S.occ_symbol("SPY", e, "P", 700), "qty": -2, "side": "short", "avg_entry_price": 2.0,
            "asset_class": "us_option"},
           {"symbol": S.occ_symbol("SPY", e, "P", 690), "qty": 3, "side": "long", "avg_entry_price": 0.8,
            "asset_class": "us_option"},
           {"symbol": "AAPL", "qty": 5, "side": "long", "asset_class": "us_equity"}]
    sp, orph = S.group_spreads(pos)
    assert len(sp) == 1 and sp[0].qty == 2 and sp[0].width == 10 and sp[0].broker_credit == pytest.approx(1.2)
    assert sp[0].id == "SPY-20261030-P700/690"
    assert {o["symbol"] for o in orph} == {"AAPL", S.occ_symbol("SPY", e, "P", 690)}


def test_reconcile_adopts_and_flags_vanished():
    e = dt.date(2026, 10, 30)
    sp = [S.Spread("SPY", e, "a", "b", 700, 690, 1, 2.0, 0.8)]
    rec, adopted, gone = state.reconcile(sp, {"QQQ-x": {"underlying": "QQQ"}}, TODAY)
    assert adopted == [sp[0].id] and rec[sp[0].id]["entry_credit"] == pytest.approx(1.2)
    assert gone[0]["id"] == "QQQ-x"


def test_occ_roundtrip():
    s = S.occ_symbol("SPY", dt.date(2026, 10, 30), "P", 761)
    assert s == "SPY261030P00761000"
    assert S.parse_occ(s) == ("SPY", dt.date(2026, 10, 30), "P", 761.0)


# ====================================================================== order lifecycle
def _job(price_ladder, credit=True, cutoff_price=None, key="SPY261030P00700000"):
    legs = execution.open_legs(key, "SPY261030P00690000") if credit else \
        execution.close_legs(key, "SPY261030P00690000")
    return execution.Job("SPY-x", "open" if credit else "close", legs, 2, price_ladder, credit,
                         cutoff_price=cutoff_price)


def test_order_fills_after_repricing():
    fb = FakeBroker(fill_at={"SPY261030P00700000": 1.18})
    clk = FakeClock()
    j = _job([1.22, 1.20, 1.18, 1.16])
    execution.work_orders(fb, [j], cutoff=clk.now() + dt.timedelta(minutes=8), clock=clk, step_wait=30)
    assert j.status == "filled" and j.filled_qty == 2 and j.avg_price == pytest.approx(1.18)
    assert [a for a, _ in fb.log] == ["submit", "replace", "replace"]
    assert fb.log[0][1] == -1.22                                   # credit is sent negative


def test_order_canceled_after_ladder_when_never_filled():
    fb = FakeBroker(never_fill=True)
    clk = FakeClock()
    t0 = clk.now()
    j = _job([1.22, 1.20])
    execution.work_orders(fb, [j], cutoff=t0 + dt.timedelta(minutes=8), clock=clk, step_wait=30)
    assert j.status == "unfilled" and [a for a, _ in fb.log] == ["submit", "replace", "cancel"]
    assert t0 + dt.timedelta(seconds=59) <= clk.now() <= t0 + dt.timedelta(seconds=70)


def test_order_canceled_by_cutoff():
    fb = FakeBroker(never_fill=True)
    clk = FakeClock()
    cutoff = clk.now() + dt.timedelta(minutes=8)
    j = _job([1.34, 1.32, 1.30, 1.28, 1.26, 1.24, 1.22])
    execution.work_orders(fb, [j], cutoff=cutoff, clock=clk, step_wait=120)   # step shrunk to fit the window
    assert j.status == "unfilled" and fb.log[-1][0] == "cancel"
    assert cutoff - dt.timedelta(seconds=5) <= clk.now() <= cutoff + dt.timedelta(seconds=10)


def test_exit_uses_aggressive_price_at_cutoff():
    fb = FakeBroker(fill_at={"SPY261030P00700000": 0.65})
    clk = FakeClock()
    cutoff = clk.now() + dt.timedelta(minutes=2)
    j = _job([0.50, 0.55, 0.60], credit=False, cutoff_price=0.65)
    execution.work_orders(fb, [j], cutoff=cutoff, hard_stop=cutoff + dt.timedelta(seconds=90), clock=clk,
                          step_wait=30)
    assert j.status == "filled" and j.used_cutoff and j.avg_price == pytest.approx(0.65)
    assert fb.log[0][1] == 0.50                                    # debit is sent positive


def test_replace_failure_falls_back_to_cancel_and_resubmit():
    fb = FakeBroker(fill_at={"SPY261030P00700000": 1.20}, replace_fails=True)
    clk = FakeClock()
    j = _job([1.22, 1.20])
    execution.work_orders(fb, [j], cutoff=clk.now() + dt.timedelta(minutes=8), clock=clk, step_wait=30)
    assert j.status == "filled"
    acts = [a for a, _ in fb.log]
    assert acts == ["submit", "replace", "cancel", "submit"]


def test_past_cutoff_sends_nothing():
    fb = FakeBroker()
    clk = FakeClock()
    j = _job([1.2])
    execution.work_orders(fb, [j], cutoff=clk.now() - dt.timedelta(seconds=1), clock=clk)
    assert j.status == "skipped" and fb.log == []


def test_multiple_jobs_step_together():
    fb = FakeBroker(fill_at={"A": 1.0, "B": 0.5})
    clk = FakeClock()
    ja = execution.Job("a", "open", [{"symbol": "A", "side": "sell", "position_intent": "sell_to_open"}], 1,
                       [1.1, 1.0], True)
    jb = execution.Job("b", "open", [{"symbol": "B", "side": "sell", "position_intent": "sell_to_open"}], 1,
                       [0.7, 0.6], True)
    t0 = clk.now()
    execution.work_orders(fb, [ja, jb], cutoff=t0 + dt.timedelta(minutes=8), clock=clk, step_wait=30)
    assert ja.status == "filled" and jb.status == "unfilled"
    assert clk.now() < t0 + dt.timedelta(minutes=8, seconds=10)


# ====================================================================== planner end to end
def _planner_setup(dip=True):
    h = bars_dip()
    last = float(h["close"].iloc[-1])
    if dip:
        live = {"open": last, "high": last * 1.001, "low": last * 0.97, "close": last * 0.972}
    else:
        live = {"open": last, "high": last * 1.03, "low": last * 0.999, "close": last * 1.028}
    spot = live["close"]
    chain = chain_for(spot=round(spot))
    return FakeDataBroker({"SPY": h}, {"SPY": live}, chain), spot


def test_plan_entries_creates_sized_job_on_dip():
    fb, _ = _planner_setup(dip=True)
    ctx = R.Ctx(fb, TODAY)
    acct = {"equity": 10_000, "options_buying_power": 10_000}
    jobs, rows = R.plan_entries(ctx, {}, acct, (False, "ok"), ["SPY"])
    assert len(jobs) == 1, rows
    j = jobs[0]
    p = j.meta["pick"]
    assert j.credit and j.prices[0] == pytest.approx(math.floor(p.mid_credit * 100) / 100)
    assert S.max_loss(p.width, j.prices[-1], j.qty) <= 0.25 * 10_000 + 1e-6
    assert [l["position_intent"] for l in j.legs] == ["sell_to_open", "buy_to_open"]


def test_plan_entries_respects_kill_switch_and_open_position():
    fb, _ = _planner_setup(dip=True)
    acct = {"equity": 10_000, "options_buying_power": None}
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY), {}, acct, (True, "VIX 45"), ["SPY"])
    assert not jobs and "kill switch" in rows[0]["action"]
    rec = {"SPY-1": {"underlying": "SPY", "short_strike": 700, "long_strike": 690, "entry_credit": 1, "qty": 1}}
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY), rec, acct, (False, "ok"), ["SPY"])
    assert not jobs and "already open" in rows[0]["action"]


def test_plan_entries_no_signal_no_job():
    fb, _ = _planner_setup(dip=False)
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY), {}, {"equity": 10_000}, (False, "ok"), ["SPY"])
    assert not jobs and rows[0]["action"].startswith("no signal")


def test_plan_exit_and_apply_results_books_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    fb, spot = _planner_setup(dip=False)                           # rally: close > 5DMA
    short = next(q for q in fb.chain if abs(abs(q.delta) - 0.25) < 0.05)
    long = next(q for q in fb.chain if q.strike < short.strike and abs(abs(q.delta) - 0.10) < 0.05)
    sid = S.spread_id("SPY", short.expiry, short.strike, long.strike)
    rec = {sid: {"underlying": "SPY", "expiry": short.expiry.isoformat(), "short_symbol": short.symbol,
                 "long_symbol": long.symbol, "short_strike": short.strike, "long_strike": long.strike,
                 "qty": 2, "entry_date": "2026-10-19", "entry_credit": 3.00}}
    ses = signals.sessions_from_bars(fb.hist["SPY"].index, TODAY)
    jobs, rows = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=True)
    assert len(jobs) == 1 and rows[0]["reason"] in ("take_profit", "sma5_exit")
    j = jobs[0]
    assert [l["position_intent"] for l in j.legs] == ["buy_to_close", "sell_to_close"]
    ob = FakeBroker(fill_at={short.symbol: j.prices[0]})
    clk = FakeClock()
    execution.work_orders(ob, jobs, cutoff=clk.now() + dt.timedelta(minutes=8), clock=clk, step_wait=30)
    trades = R.apply_results(jobs, rec, TODAY, None, persist=True)
    assert rec == {} and len(trades) == 1
    assert trades[0]["pnl"] == pytest.approx((3.00 - j.avg_price) * 200, abs=0.01)
    assert state.read_jsonl(state.TRADES)[0]["spread_id"] == sid


# ====================================================================== credential / safety rules
def _env(**kw):
    base = {"OPT_APCA_API_KEY_ID": "PKTEST", "OPT_APCA_API_SECRET_KEY": "s", "OPT_APCA_PAPER": "true"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


def test_trading_creds_refusals():
    assert C.trading_creds(_env()).source == "options"
    with pytest.raises(C.ConfigError, match="missing"):
        C.trading_creds(_env(OPT_APCA_API_KEY_ID=None))
    with pytest.raises(C.ConfigError, match="paper-only"):
        C.trading_creds(_env(OPT_APCA_PAPER="false"))
    with pytest.raises(C.ConfigError, match="not the paper endpoint"):
        C.trading_creds(_env(OPT_APCA_BASE_URL="https://api.alpaca.markets"))
    with pytest.raises(C.ConfigError, match="LIVE key"):
        C.trading_creds(_env(OPT_APCA_API_KEY_ID="AKLIVE"))


def test_options_env_ignores_apca_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "PKSHORTING")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "x")
    env = C.options_env(tmp_path / "missing.env")
    assert "APCA_API_KEY_ID" not in env and "OPT_APCA_API_KEY_ID" not in env
    with pytest.raises(C.ConfigError):
        C.trading_creds(env)


def test_execute_without_opt_keys_refuses():
    with pytest.raises(C.ConfigError):
        Broker(execute=True, env={})


def test_dry_run_broker_blocks_mutations_and_data_host_guard():
    b = Broker(execute=False, env=_env())
    b._account_checked = True
    with pytest.raises(SafetyError, match="dry run"):
        b.submit_mleg([], 1, -1.0, "x")
    with pytest.raises(SafetyError, match="dry run"):
        b.cancel_order("x")
    with pytest.raises(SafetyError):
        b._data("/v2/orders")


class _Resp:
    def __init__(self, js, code=200):
        self._js, self.status_code, self.text = js, code, "x"

    def json(self):
        return self._js


class _Sess:
    def __init__(self, account):
        self.account, self.calls = account, []

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        return _Resp(self.account)


def test_non_paper_account_number_refused():
    s = _Sess({"account_number": "XX123"})
    b = Broker(execute=True, env=_env(), session=s)
    with pytest.raises(SafetyError, match="paper"):
        b.positions()
    assert all(u.startswith("https://paper-api.alpaca.markets") for _, u in s.calls)


def test_shorting_keys_never_reach_trading_host():
    b = Broker(execute=False, env={})                              # data falls back to shorting keys
    assert not b.has_trading
    assert b.positions() == [] and b.account()["simulated"]
    with pytest.raises(Exception):
        b._trade("GET", "/v2/account")
