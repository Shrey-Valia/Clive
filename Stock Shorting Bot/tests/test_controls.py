"""Optional controls: SIZE_PCT_PER_NAME, BROKER_STOP_PCT, the daily-loss / drawdown halts, and the
PDT day-trade cap (PDT_ENFORCE auto / always / never). Each on and off."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

import daily
import monitor
from conftest import ET, qty_for, run_daily
from shortingbot import config, risk
from test_daily import write_picks

FIVE = ["P1", "P2", "P3", "P4", "P5"]


def today():
    return dt.datetime.now(ET).date().isoformat()


@pytest.fixture
def opening(fake, monkeypatch):
    """09:31 with five $20 picks, all fine at the open."""
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(minutes=5))
    write_picks(FIVE)
    fake.prices.update({t: 20.0 for t in FIVE})
    return fake


def shorts(fake):
    return sorted(t for t, p in fake.pos.items() if p["qty"] < 0)


# ---------------------------------------------------------------- sizing knob
@pytest.mark.parametrize("pct", [10.0, 5.0])
def test_size_pct_per_name_at_open(opening, fdb, monkeypatch, pct):
    monkeypatch.setattr(config, "SIZE_PCT_PER_NAME", pct)
    assert run_daily("--open", "--execute") == 0
    assert [o["qty"] for o in opening.sent("sell")] == [qty_for(10_000, pct, 20.0)] * 5


# ---------------------------------------------------------------- broker-side stops
def test_stops_off_by_default(opening, fdb):
    assert run_daily("--open", "--execute") == 0
    assert not any(e[0] == "stop" for e in opening.events) and opening.open_orders == []


def test_stops_on_placed_at_fill_plus_pct(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "BROKER_STOP_PCT", 35)
    assert run_daily("--open", "--execute") == 0
    stops = [e for e in opening.events if e[0] == "stop"]
    assert [(t, q, px) for _, t, q, px in stops] == [(t, 50, 27.0) for t in FIVE]
    assert {r["purpose"] for r in fdb.rows("short_orders")} == {"short", "stop"}
    assert len(json.load(open(config.PICKS_FILE))["stops"]) == 5


def test_stops_cancelled_before_cover_and_filled_stop_is_fine(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "BROKER_STOP_PCT", 35)
    assert run_daily("--open", "--execute") == 0
    opening.trigger_stop("P2")                            # P2 spiked, the stop bought it back
    opening.events.clear()
    monkeypatch.setattr(daily, "scan", lambda: [])        # scan fails; the cover must not care
    assert run_daily("--execute") == 1                    # 15:40 job, same fake day
    kinds = [e[0] for e in opening.events]
    assert kinds.index("cancel") < kinds.index("submit")
    covered = [e[2] for e in opening.events if e[0] == "submit"]
    assert covered == ["P1", "P3", "P4", "P5"]            # nothing sent for P2
    assert opening.pos == {} and opening.open_orders == []
    assert not any(r["kind"] == "cover_failed" for r in fdb.rows("alerts"))


def test_cover_rejected_but_flat_is_not_a_problem(fake, fdb, monkeypatch):
    """The stop fills between the holdings read and the cover: Alpaca rejects the cover."""
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5))
    fake.short("A", 10, 20.0)
    real_submit = fake.submit

    def racy(ticker, qty, side):
        fake.pos.pop(ticker, None)
        raise RuntimeError("insufficient qty available for order")
    monkeypatch.setattr(daily.broker, "submit", racy)
    out = daily.cover_all(1, True, now + dt.timedelta(minutes=3))
    assert out["left"] == {} and out["problems"] == []


# ---------------------------------------------------------------- daily loss halt
def test_daily_loss_off_enters_normally(opening, fdb):
    opening.last_equity = 12_000.0                        # down 16.7% on the day
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == FIVE


def test_daily_loss_on_flattens_and_blocks(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 5.0)
    opening.last_equity = 12_000.0
    opening.short("LEFT", 10, 20.0, 30.0)                 # a leftover short
    assert run_daily("--open", "--execute") == 0
    assert opening.pos == {} and [o["side"] for o in opening.orders] == ["buy"]
    assert risk.halted_today(today())
    kinds = [r["kind"] for r in fdb.rows("alerts")]
    assert "daily_loss_halt" in kinds
    opening.last_equity = opening.equity                  # recovered, but the halt is for the whole day
    assert run_daily("--open", "--execute", "--force") == 0
    assert shorts(opening) == []


def test_monitor_daily_loss_halt(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0, 21.0)
    fake.last_equity = 11_000.0
    assert monitor.main(["--now"]) == 0                   # off: no trade
    assert fake.orders == []
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 5.0)
    assert monitor.main(["--now"]) == 0
    assert fake.pos == {} and risk.halted_today(today())
    assert monitor.main(["--now"]) == 0                   # already halted: no second flatten
    assert len(fake.orders) == 1


# ---------------------------------------------------------------- drawdown halt
def test_drawdown_off_and_on(opening, fdb, monkeypatch):
    risk.update_peak(13_000.0)                            # 23% under the peak
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == FIVE

    for t in FIVE:
        opening.pos.pop(t)
    os.remove(daily.OPEN_GUARD)
    monkeypatch.setattr(config, "DRAWDOWN_HALT_PCT", 20.0)
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == []
    assert any(r["kind"] == "drawdown_halt" for r in fdb.rows("alerts"))


def test_peak_tracks_highs():
    assert risk.update_peak(10_000) == 10_000
    assert risk.update_peak(9_000) == 10_000
    assert risk.update_peak(11_000) == 11_000


def test_monitor_drawdown_alert_dedupes(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "DRAWDOWN_HALT_PCT", 10.0)
    risk.update_peak(12_000.0)
    monitor.main(["--now"])
    monitor.main(["--now"])
    assert [r["kind"] for r in fdb.rows("alerts")].count("drawdown_halt") == 1 and fake.orders == []


# ---------------------------------------------------------------- PDT cap
def test_pdt_auto_no_restriction_trades_all_five(opening, fdb):
    """The paper account today: no PDT fields reported, rule retired -> all five."""
    opening.day_trades = 3                                # ignored while the cap is off
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == FIVE


@pytest.mark.parametrize("day_trades, expect", [(3, []), (2, FIVE[:1]), (0, FIVE[:3])])
def test_pdt_allowance_0_1_3_with_five_picks(opening, fdb, monkeypatch, day_trades, expect):
    monkeypatch.setattr(config, "PDT_ENFORCE", "always")
    opening.day_trades = day_trades
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == expect                      # rank order
    if not expect:
        assert any(r["kind"] == "pdt_limit" for r in fdb.rows("alerts"))
        assert fdb.updated("short_runs", 42)["status"] == "no_entries"


def test_pdt_auto_turns_on_when_account_reports_a_count(opening, fdb):
    opening.acct_extra.update(pattern_day_trader=False, daytrade_count=2)
    opening.day_trades = 2
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == ["P1"]


def test_pdt_no_cap_at_25k(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "PDT_ENFORCE", "always")
    opening.equity = opening.last_equity = 30_000.0
    opening.prices.update({t: 20.0 for t in FIVE})
    opening.day_trades = 3
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == FIVE


def test_pdt_unknown_count_enters_nothing(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "PDT_ENFORCE", "always")
    opening.day_trades = None
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == []


def test_pdt_never_overrides(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "PDT_ENFORCE", "never")
    opening.acct_extra.update(pattern_day_trader=True, daytrade_count=3)
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == FIVE


def test_pdt_rejection_turns_auto_cap_on_next_time(opening, fdb):
    opening.reject["P2"] = "trade denied due to pattern day trading protection"
    assert run_daily("--open", "--execute") == 2
    assert shorts(opening) == ["P1"] and risk.pdt_detected()
    for t in shorts(opening):
        opening.pos.pop(t)
    opening.reject.clear()
    os.remove(daily.OPEN_GUARD)
    opening.day_trades = 2
    assert run_daily("--open", "--execute") == 0
    assert shorts(opening) == ["P1"]                      # capped at 3 - 2 = 1


def test_buying_power_rejection_skips_and_continues(opening, fdb):
    opening.reject["P2"] = "insufficient day trading buying power"
    assert run_daily("--open", "--execute") == 2
    assert shorts(opening) == ["P1", "P3", "P4", "P5"]
    assert not risk.pdt_detected()
    assert not any(r["kind"] == "pdt_block" for r in fdb.rows("alerts"))
