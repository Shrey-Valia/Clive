"""2026-10-23 follow-ups: the first live condor entry did not fill on Alpaca paper, and the final
sweep's ERROR line made the health probe mark the daily run FAILED.

Fakes only: no network, no order endpoints.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import config as C  # noqa: E402
from live import execution  # noqa: E402
from live import run as R  # noqa: E402
from live import strategy as S  # noqa: E402
from live.broker import ET  # noqa: E402
from test_fixes import Book, _work, isolated  # noqa: E402,F401
from test_live import TODAY, _planner_setup  # noqa: E402

SS, LS = "SPY261030P00700000", "SPY261030P00690000"


def _today_condor():
    """The 2026-10-23 15:55 quotes of the first live condor (live log): mid 2.29 (2.285), natural 2.13."""
    exp = dt.date(2026, 12, 18)

    def q(cp, k, b, a, d):
        return S.OptionQuote(S.occ_symbol("SPY", exp, cp, k), "SPY", exp, cp, k, b, a, d)
    return S.CondorPick("SPY", exp, q("P", 675, 3.80, 3.92, -0.10), q("P", 650, 2.75, 2.82, -0.06),
                        q("C", 814, 1.64, 1.68, 0.10), q("C", 835, 0.41, 0.49, 0.03), 56, 759.92)


def test_backtest_assumed_credit_for_the_first_condor():
    p = _today_condor()
    assert p.mid_credit == pytest.approx(2.285) and p.natural_credit == pytest.approx(2.13)
    bt = S.backtest_credit([p.put_short, p.call_short], [p.put_long, p.call_long])
    assert bt == pytest.approx(2.285 - (0.0772 + 0.0557 + 0.0332 + 0.03), abs=0.002)        # ~2.09 < natural
    # the default (halfway) ladder never reaches the natural: on paper it cannot fill unless the market moves
    assert S.condor_entry_prices(p) == [2.28, 2.26, 2.24, 2.22, 2.20]


def test_natural_ladder_reaches_the_natural_and_respects_the_backtest_floor(monkeypatch):
    monkeypatch.setattr(C, "CONDOR_ENTRY_LADDER", "natural")
    p = _today_condor()
    lad = S.condor_entry_prices(p)
    assert lad[0] == 2.28 and lad[-1] == 2.13 and lad == sorted(lad, reverse=True) and len(lad) == 5
    # natural worse than the backtest assumed: stop at the backtest credit, never below it
    assert S.natural_ladder(2.29, 1.90, 2.09)[-1] == 2.09
    assert S.natural_ladder(2.29, 2.13, 2.09)[-1] == 2.13
    assert S.natural_ladder(1.00, 0.95, 0.90, min_credit=0.97)[-1] == 0.97
    assert S.natural_ladder(1.005, 1.0, 0.9) == [1.0]
    # sizing uses the worst ladder credit: still 1 condor at $15k
    n, _ = S.size_position(15_000, p.width, lad[-1], 0.0, 15_000, risk_per_position=C.CONDOR_RISK_PER_POSITION)
    assert n == 1 and S.max_loss(p.width, lad[-1], 1) == pytest.approx(2287.0)


def test_dip_ladder_default_is_unchanged_and_natural_is_opt_in(monkeypatch):
    fb, _ = _planner_setup(dip=True)
    jobs, _ = R.plan_entries(R.Ctx(fb, TODAY), {}, {"equity": 10_000, "options_buying_power": 10_000},
                             (False, "ok"), ["SPY"])
    p = jobs[0].meta["pick"]
    assert jobs[0].prices == S.entry_ladder(p)
    monkeypatch.setattr(C, "DIP_ENTRY_LADDER", "natural")
    lad = S.dip_entry_prices(p)
    target = max(p.natural_credit, S.backtest_credit([p.short], [p.long]), C.MIN_CREDIT_FRAC * p.width,
                 C.MIN_CREDIT_ABS)
    assert lad[0] <= p.mid_credit and lad[-1] >= target - 1e-9 and lad[-1] - target < 0.01


def test_cleanly_canceled_entry_does_not_log_an_error_in_the_final_sweep(caplog):
    """The open-orders list still showed the just-canceled entry; the sweep logged ERROR and the
    health probe counted the daily run as FAILED."""
    b = Book()
    real_open, real_cancel, stale = b.open_orders, b.cancel_order, {}
    b.open_orders = lambda: [dict(o, status="new") for o in stale.values()] + real_open()

    def cancel(oid):
        real_cancel(oid)
        stale[oid] = b.orders[oid]                     # Alpaca lists it as open once more
    b.cancel_order = cancel
    j = execution.Job("SPY-x", "open", execution.open_legs(SS, LS), 1, [1.22, 1.20], credit=True)
    with caplog.at_level("INFO", logger="optionslive"):
        _work(b, [j])
    assert j.status == "unfilled"
    assert not [r for r in caplog.records if r.levelname == "ERROR"], [r.message for r in caplog.records]
    assert any("stale open-orders list" in r.message for r in caplog.records)


def test_a_really_open_leftover_is_a_warning_and_still_canceled(caplog):
    b = Book()
    j = execution.Job("SPY-x", "open", execution.open_legs(SS, LS), 1, [1.22], credit=True)
    clk = __import__("test_live").FakeClock()
    execution._submit(b, j, 1.22, clk)
    j.order_id = None                                  # the job lost track of it (e.g. an exception)
    with caplog.at_level("INFO", logger="optionslive"):
        execution.final_sweep(b, [j], clk)
    assert b.orders["o1"]["status"] == "canceled"
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_early_condor_entry_works_before_the_decision_time(isolated, monkeypatch):
    from test_condor import daily_fake
    from test_fixes import _patch_daily
    monkeypatch.setattr(C, "CONDOR_ENTRY_EARLY", True)
    now = dt.datetime.combine(TODAY, dt.time(15, 45, 5), ET)
    fake = daily_fake(now)
    _patch_daily(monkeypatch, fake, now)
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    calls = []

    def work(broker, jobs, **kw):
        calls.append(([j.key for j in jobs], kw.get("cutoff"), kw.get("step_wait")))
        return jobs
    monkeypatch.setattr(R.execution, "work_orders", work)
    assert R.main(["--execute"]) == 0
    decide = dt.datetime.combine(TODAY, dt.time(15, 55), ET)
    keys, cutoff, step = calls[0]
    assert len(keys) == 1 and "IC" in keys[0] and cutoff == decide - dt.timedelta(minutes=1)
    assert step == C.CONDOR_EARLY_STEP_SECONDS
    assert any("IC" in k for k in calls[1][0])          # unfilled early: the 15:55 decision tries again


def test_early_condor_entry_is_off_by_default(isolated, monkeypatch):
    from test_condor import daily_fake
    from test_fixes import _patch_daily
    now = dt.datetime.combine(TODAY, dt.time(15, 45, 5), ET)
    _patch_daily(monkeypatch, daily_fake(now), now)
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(R.execution, "work_orders", lambda broker, jobs, **kw: calls.append(jobs) or jobs)
    assert R.main(["--execute"]) == 0
    assert len(calls) == 1
