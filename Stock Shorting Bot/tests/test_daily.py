"""The v2 jobs end to end against the fake broker: 15:40 schedule, handover, --open, PDT, holidays."""
from __future__ import annotations

import datetime as dt
import json
import threading

import pytest

import daily
from conftest import ET, gainer, qty_for, run_daily
from shortingbot import alerts, config, db

HANDOVER = {"ASST": (51, 30.78), "AVX": (412, 4.41), "BNC": (317, 5.69), "NAKA": (201, 8.47), "RCEL": (96, 15.28)}


def today():
    return dt.datetime.now(ET).date().isoformat()


def set_close_in(fake, seconds):
    """Session closing so that 15:55 (close - 5 min) is `seconds` from now."""
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5, seconds=seconds))


def read(path):
    return json.load(open(path))


# ---------------------------------------------------------------- 15:40 job
def test_schedule_scan_then_cover_then_no_new_shorts(fake, fdb, scan_inputs, monkeypatch):
    """Waits for 15:45, scans, covers at 15:55, saves the picks, sends no sell."""
    set_close_in(fake, 1)
    waits = []
    monkeypatch.setattr(daily, "wait_until", lambda when, label: (waits.append(label), fake.events.append(("wait", label))))
    real_scan = daily.scan
    monkeypatch.setattr(daily, "scan", lambda: (fake.events.append(("scan",)), real_scan())[1])
    scan_inputs += [gainer(f"G{i}", close=20 + i) for i in range(6)]
    fake.adv.update({f"G{i}": 50e6 for i in range(6)})
    fake.short("OLD", 10, 20.0)

    assert run_daily("--execute") == 0
    kinds = [e[0] if e[0] != "wait" else f"wait:{e[1]}" for e in fake.events]
    assert kinds.index("wait:scan") < kinds.index("scan") < kinds.index("submit")
    assert waits == ["scan", "cover"]
    assert [o["side"] for o in fake.orders] == ["buy"]                       # the cover, nothing new
    assert fake.pos == {}
    picks = read(config.PICKS_FILE)
    assert picks["scan_date"] == today() and picks["trade_date"] == fake.next_day.isoformat()
    assert len(picks["tickers"]) == 5 and picks["run_id"]
    assert {"ticker", "score", "scan_price", "target_pct", "scan_qty"} <= set(picks["picks"][0])
    run = fdb.rows("short_runs")[0]
    assert run["strategy_version"] == "intraday_v2" and run["code_version"] == "test"
    assert run["trade_date"] == fake.next_day and run["session_close"] == fake.session[1]
    assert run["config"]["MIN_SHORT_SCORE"] == 70
    assert fdb.updated("short_runs", run["id"])["status"] == "picks_saved"
    assert read(daily.GUARD)["date"] == today()


def test_cover_happens_when_scan_fails(fake, fdb, monkeypatch):
    set_close_in(fake, 0)
    monkeypatch.setattr(daily, "scan", lambda: (_ for _ in ()).throw(RuntimeError("tradingview down")))
    fake.short("OLD", 10, 20.0)
    assert run_daily("--execute") == 1
    assert [(o["side"], o["ticker"]) for o in fake.orders] == [("buy", "OLD")]
    run = fdb.rows("short_runs")[0]
    assert fdb.updated("short_runs", run["id"])["status"] == "failed"
    assert any(r["kind"] == "scan_failed" for r in fdb.rows("alerts"))


def test_cover_happens_when_scan_hangs(fake, fdb, monkeypatch):
    set_close_in(fake, 0.5)
    monkeypatch.setattr(config, "SCAN_TIMEOUT_SECONDS", 1)
    gate = threading.Event()
    monkeypatch.setattr(daily, "scan", lambda: (gate.wait(5), [])[1])
    fake.short("OLD", 10, 20.0)
    try:
        assert run_daily("--execute") == 1
    finally:
        gate.set()
    assert [(o["side"], o["ticker"]) for o in fake.orders] == [("buy", "OLD")]
    assert not (config.PICKS_FILE and __import__("os").path.exists(config.PICKS_FILE))


def test_handover_covers_v1_shorts_and_saves_picks(fake, fdb, scan_inputs, monkeypatch):
    """Today 2026-10-22: 5 overnight v1 shorts from 10-21, guard file from 10-21, no picks file."""
    set_close_in(fake, 0)
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    for t, (q, px) in HANDOVER.items():
        fake.short(t, q, px)
    json.dump({"date": (dt.datetime.now(ET).date() - dt.timedelta(days=1)).isoformat(), "orders": ["x"]},
              open(daily.GUARD, "w"))
    scan_inputs += [gainer("NEW1", close=25), gainer("AVX", close=4.5), gainer("NAKA", close=12)]
    fake.adv.update({"NEW1": 30e6, "AVX": 2e6, "NAKA": 1.6e6})

    assert run_daily("--execute") == 0
    assert sorted((o["ticker"], o["qty"]) for o in fake.orders) == sorted((t, float(q)) for t, (q, _) in HANDOVER.items())
    assert all(o["side"] == "buy" for o in fake.orders)
    assert fake.pos == {}
    picks = read(config.PICKS_FILE)
    assert picks["tickers"] == ["NEW1"]                    # AVX < $10, NAKA ADV20 < $10M
    run = fdb.rows("short_runs")[0]
    assert {o["run_id"] for o in fdb.rows("short_orders")} == {run["id"]}
    assert "handover" in fdb.updated("short_runs", run["id"])["message"]
    assert read(daily.GUARD)["date"] == today()


def test_guard_blocks_second_close_run(fake, fdb):
    json.dump({"date": today()}, open(daily.GUARD, "w"))
    fake.short("OLD", 10, 20.0)
    assert run_daily("--execute") == 1
    assert fake.orders == []


def test_exit_goes_on_the_run_that_entered(fake, fdb, scan_inputs, monkeypatch):
    set_close_in(fake, 0)
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    daily.save_picks(config.PICKS_FILE, {"run_id": 7, "scan_date": "2026-10-21", "trade_date": today(),
                                         "tickers": ["E"], "picks": [], "n_entered": 1})
    fake.short("E", 50, 20.0)
    scan_inputs.append(gainer("N", close=20))
    assert run_daily("--execute") == 0
    assert fdb.rows("short_orders")[0]["run_id"] == 7
    exit_row = fdb.updated("short_runs", 7)
    assert exit_row["status"] == "exited" and exit_row["exit_at"] and exit_row["n_orders"] == 2


def test_cover_rejected_by_pdt_raises_alert(fake, fdb, monkeypatch):
    set_close_in(fake, 0)
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    monkeypatch.setattr(daily, "scan", lambda: [])
    fake.short("A", 10, 20.0)
    fake.reject["A"] = "trade denied due to pattern day trading protection"
    assert run_daily("--execute") == 1                     # no crash; reported as a failure
    kinds = {r["kind"] for r in fdb.rows("alerts")}
    assert {"pdt_block", "cover_failed"} <= kinds


def test_holiday_writes_market_closed_row(fake, fdb):
    fake.session = None
    assert run_daily("--execute") == 0
    rows = fdb.rows("short_runs")
    assert len(rows) == 1 and rows[0]["status"] == "market_closed" and rows[0]["run_date"] == today()
    assert fake.orders == [] and fdb.heartbeats[-1][1] == "idle"


def test_half_day_uses_calendar_close(fake, fdb, scan_inputs, monkeypatch):
    now = dt.datetime.now(ET)
    close = now.replace(hour=13, minute=0, second=0, microsecond=0)
    fake.session = (close - dt.timedelta(hours=3, minutes=30), close)
    seen = []
    monkeypatch.setattr(daily, "wait_until", lambda when, label: seen.append((label, when.strftime("%H:%M"))))
    run_daily("--execute", "--now") if False else None
    monkeypatch.setattr(daily, "cover_all", lambda run_id, execute, cutoff: {"planned": [], "sent": [], "problems": [], "left": {}})
    run_daily("--execute")
    assert seen == [("scan", "12:45"), ("cover", "12:55")]
    assert fdb.rows("short_runs")[0]["session_close"] == close


# ---------------------------------------------------------------- 09:31 job
def write_picks(tickers, trade_date=None, scan=20.0, run_id=42):
    """scan: one scan price for all, or {ticker: price}."""
    px = scan if isinstance(scan, dict) else {t: scan for t in tickers}
    daily.save_picks(config.PICKS_FILE, {
        "run_id": run_id, "scan_date": "2026-10-22", "trade_date": trade_date or today(), "tickers": tickers,
        "picks": [{"ticker": t, "score": 80, "scan_price": px[t], "target_pct": 10.0, "scan_qty": 50} for t in tickers]})


@pytest.fixture
def open_now(fake, monkeypatch):
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    return fake


def test_open_enters_equal_10pct(open_now, fdb):
    fake = open_now
    write_picks(["A", "B", "C"], scan={"A": 20.0, "B": 25.0, "C": 48.0})
    fake.prices.update(A=20.0, B=25.0, C=50.0)
    assert run_daily("--open", "--execute") == 0
    assert [(o["ticker"], o["side"], o["qty"]) for o in fake.orders] == [
        ("A", "sell", qty_for(10_000, 10, 20)), ("B", "sell", qty_for(10_000, 10, 25)), ("C", "sell", qty_for(10_000, 10, 50))]
    assert fdb.updated("short_runs", 42)["status"] == "entered"
    assert {o["run_id"] for o in fdb.rows("short_orders")} == {42}
    assert read(config.PICKS_FILE)["n_entered"] == 3
    assert read(daily.OPEN_GUARD)["date"] == today()
    assert run_daily("--open", "--execute") == 1          # once per day
    assert len(fake.orders) == 3


def test_open_rechecks_and_logs_skips(open_now, fdb, caplog):
    fake = open_now
    write_picks(["OK", "GAP", "HTB", "CHEAP"])
    fake.prices.update(OK=20.0, GAP=29.0, HTB=20.0, CHEAP=9.0)
    fake.borrow_map["HTB"] = {"shortable": True, "easy_to_borrow": False}
    assert run_daily("--open", "--execute") == 0
    assert [o["ticker"] for o in fake.orders] == ["OK"]
    text = caplog.text
    assert "skip GAP: gapped up 45%" in text and "skip HTB: hard to borrow" in text and "skip CHEAP" in text
    assert "skip GAP" in fdb.updated("short_runs", 42)["message"]


def test_open_gap_threshold_configurable(open_now, fdb, monkeypatch):
    monkeypatch.setattr(config, "MAX_GAP_UP_PCT", 50.0)
    write_picks(["GAP"])
    open_now.prices["GAP"] = 29.0
    assert run_daily("--open", "--execute") == 0
    assert [o["ticker"] for o in open_now.orders] == ["GAP"]


def test_open_no_picks_is_fine(open_now, fdb):
    write_picks([])
    assert run_daily("--open", "--execute") == 0
    assert open_now.orders == [] and fdb.updated("short_runs", 42)["status"] == "no_entries"


def test_open_stale_picks_enter_nothing(open_now, fdb):
    write_picks(["A"], trade_date="2026-10-01")
    open_now.prices["A"] = 20.0
    assert run_daily("--open", "--execute") == 0
    assert open_now.orders == [] and any(r["kind"] == "stale_picks" for r in fdb.rows("alerts"))


def test_open_gross_cap_with_leftover_short(open_now, fdb):
    fake = open_now
    fake.short("LEFT", 200, 20.0)                          # 40% of equity still short from yesterday
    write_picks(["A", "B"])
    fake.prices.update(A=20.0, B=20.0)
    assert run_daily("--open", "--execute") == 0
    assert [(o["ticker"], o["qty"]) for o in fake.orders] == [("A", 50.0)]
    assert any(r["kind"] == "overnight_leftover" for r in fdb.rows("alerts"))


def test_open_pdt_flagged_account_blocks(open_now, fdb):
    fake = open_now
    fake.acct_extra.update(pattern_day_trader=True)       # auto: the account reports a restriction
    write_picks(["A", "B"])
    fake.prices.update(A=20.0, B=20.0)
    assert run_daily("--open", "--execute") == 0
    assert fake.orders == []
    assert any(r["kind"] == "pdt_block" for r in fdb.rows("alerts"))
    assert fdb.updated("short_runs", 42)["status"] == "no_entries"
    assert fdb.heartbeats[-1][1] == "warning"


def test_open_pdt_rejection_does_not_crash(open_now, fdb):
    fake = open_now
    write_picks(["A", "B", "C"])
    fake.prices.update(A=20.0, B=20.0, C=20.0)
    fake.reject["B"] = "order denied: pattern day trader restriction"
    assert run_daily("--open", "--execute") == 2
    assert [o["ticker"] for o in fake.orders] == ["A"]   # C skipped after the PDT rejection
    assert any(r["kind"] == "pdt_block" for r in fdb.rows("alerts"))
    assert fdb.updated("short_runs", 42)["status"] == "entered_with_errors"
    statuses = {o["ticker"]: o["status"] for o in fdb.rows("short_orders")}
    assert statuses == {"A": "filled", "B": "rejected", "C": "rejected"}


def test_open_dry_run_sends_nothing(open_now, fdb):
    write_picks(["A"])
    open_now.prices["A"] = 20.0
    assert run_daily("--open") == 0
    assert open_now.orders == [] and fdb.updates == []


def test_open_holiday(open_now, fdb):
    open_now.session = None
    write_picks(["A"])
    assert run_daily("--open", "--execute") == 0
    assert open_now.orders == []


def test_full_cycle_scan_open_cover(fake, fdb, scan_inputs, monkeypatch):
    """T 15:40 saves picks, T+1 09:31 enters them, T+1 15:40 covers them onto the same run."""
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(minutes=5))
    fake.next_day = dt.datetime.now(ET).date()             # pretend tomorrow is today, for the file check
    scan_inputs += [gainer("A", close=20), gainer("B", close=30)]
    fake.adv.update(A=50e6, B=50e6)
    assert run_daily("--execute") == 0
    run_id = read(config.PICKS_FILE)["run_id"]

    fake.prices.update(A=21.0, B=31.0)
    daily_guard = daily.GUARD
    assert run_daily("--open", "--execute", "--force") == 0
    assert {t for t, p in fake.pos.items() if p["qty"] < 0} == {"A", "B"}

    import os
    os.remove(daily_guard)
    assert run_daily("--execute") == 0
    assert fake.pos == {}
    assert fdb.updated("short_runs", run_id)["status"] == "exited"


def test_unknown_or_conflicting_strategies(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "STRATEGIES", ["short_intraday", "short_overnight"])
    assert run_daily() == 1
    monkeypatch.setattr(config, "STRATEGIES", ["nope"])
    assert run_daily() == 1
