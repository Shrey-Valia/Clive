"""2026-10-22 pre-deploy review of the upgrade: H1 calendar fallback at the open, H2 a failing
leftover cover never costs the long exit, H3 late sleeve longs sold by --cover-leftovers and
--fallback, M1 no full re-send on a blind cover pass, M2 unreadable-leftover alert and the open
guard finished on early returns, M3 earnings coverage rule, M4 long exit only in market hours,
L1 fallback past a stuck close.lock, L2 holidays with the calendar down, L5 strategy gate."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

import daily
from conftest import ET, gainer, qty_for, run_daily
from shortingbot import broker, config, db, earnings


def today():
    return dt.datetime.now(ET).date().isoformat()


def kinds(fdb):
    return [r["kind"] for r in fdb.rows("alerts")]


def alert(fdb, kind):
    return next(r for r in fdb.rows("alerts") if r["kind"] == kind)


def buys(fake):
    return [(e[2], e[3]) for e in fake.events if e[0] == "submit" and e[1] == "buy"]


def long_events(fake, side=None):
    return [e for e in fake.events if e[0] == "long" and (side is None or e[1] == side)]


@pytest.fixture
def nowait(monkeypatch):
    waits = []
    monkeypatch.setattr(daily, "wait_until", lambda when, label: waits.append(label))
    return waits


@pytest.fixture
def opening(fake, nowait):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    return fake


@pytest.fixture
def midday(fake, nowait):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=2), now + dt.timedelta(hours=4))
    return fake


def closing_in(fake, minutes, seconds=1):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=minutes, seconds=seconds))


def write_picks(tickers, scan=20.0, trade_date=None):
    daily.save_picks(config.PICKS_FILE, {
        "run_id": 42, "scan_date": "2026-10-22", "trade_date": trade_date or today(), "tickers": tickers,
        "picks": [{"ticker": t, "score": 80, "scan_price": scan, "target_pct": 5.0, "scan_qty": 25} for t in tickers]})


def write_long_state(longs: dict, fake=None, price=20.0, entry_date="2026-10-21", status="entered"):
    daily.save_picks(daily.long_state_path(), {
        "entry_date": entry_date, "exit_date": today(), "run_id": 41, "kind": "limit", "status": status,
        "orders": [{"ticker": t, "qty": q, "id": f"b{t}", "status": "filled", "filled_qty": q} for t, q in longs.items()]})
    if fake is not None:
        for t, q in longs.items():
            fake.pos[t] = {"qty": q, "entry": price, "price": price}
            fake.prices.setdefault(t, price)


def calendar_down(monkeypatch):
    def boom():
        raise RuntimeError("calendar timeout")
    monkeypatch.setattr(broker, "session_today", boom)


class _At(dt.datetime):
    at = None

    @classmethod
    def now(cls, tz=None):
        return cls.at


def freeze(monkeypatch, when: dt.datetime):
    _At.at = when
    monkeypatch.setattr(daily.dt, "datetime", _At)


# ---------------------------------------------------------------- H1
def test_h1_calendar_failing_at_the_open_still_covers_leftovers(fake, fdb, nowait, monkeypatch):
    calendar_down(monkeypatch)
    freeze(monkeypatch, dt.datetime(2026, 10, 23, 9, 31, 5, tzinfo=ET))         # a Friday, 09:31
    fake.short("OLD", 10, 20.0)
    assert run_daily("--open", "--execute") == 0
    assert buys(fake) == [("OLD", 10)] and fake.pos == {}
    assert fake.client_ids == ["v2-leftover-2026-10-23-OLD-0"]
    assert {"calendar_failed", "leftover_covered", "no_picks"} <= set(kinds(fdb))
    assert "run_failed" not in kinds(fdb)


# ---------------------------------------------------------------- H2
def test_h2_leftover_block_exception_still_sells_sleeve_longs_and_enters(opening, fdb, monkeypatch):
    fake = opening
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    fake.short("OLD", 10, 20.0)
    write_long_state({"L": 7}, fake)
    fake.prices["A"] = 20.0
    write_picks(["A"])

    def boom(*a, **k):
        raise RuntimeError("positions endpoint 500")
    monkeypatch.setattr(daily, "cover_leftovers", boom)
    assert run_daily("--open", "--execute") == 0
    assert [e[1:5] for e in long_events(fake)] == [("sell", "market", "L", 7)]
    assert fake.pos["A"]["qty"] == -qty_for(10_000, 5.0, 20.0)                  # entries went on
    a = alert(fdb, "cover_failed")
    assert a["level"] == "critical" and "positions endpoint 500" in a["message"]
    assert "run_failed" not in kinds(fdb)


# ---------------------------------------------------------------- H3
def test_h3_cover_leftovers_sells_a_late_sleeve_long(midday, fdb, monkeypatch):
    fake = midday
    fake.short("OLD", 10, 20.0)
    write_long_state({"L": 7}, fake)                                          # 09:31 sell was missed
    assert run_daily("--cover-leftovers", "--execute") == 0
    hhmm = dt.datetime.now(ET).strftime("%H%M")
    assert buys(fake) == [("OLD", 10)]
    assert [e[1:5] for e in long_events(fake)] == [("sell", "market", "L", 7)] and fake.pos == {}
    assert fake.client_ids[-1] == f"v2-long-sell-L{hhmm}-{today()}-L-0"
    assert "long_exit_late" in [r["purpose"] for r in fdb.rows("short_orders")]
    assert alert(fdb, "long_exit_late")["level"] == "warning" and "long_exit_failed" not in kinds(fdb)
    state = json.load(open(daily.long_state_path()))
    assert state["status"] == "exited" and state["left"] == {}
    assert not os.path.exists(os.path.join(config.LOG_DIR, "close.lock"))


def test_h3_cover_leftovers_leaves_tonights_and_exited_longs(midday, fdb):
    fake = midday
    write_long_state({"L": 7}, fake, entry_date=today())                     # bought today: not late
    assert run_daily("--cover-leftovers", "--execute") == 0
    write_long_state({"M": 5}, fake, status="exited")                         # already sold at 09:31
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert long_events(fake) == [] and "long_exit_late" not in kinds(fdb)


def test_h3_cover_leftovers_late_long_skipped_while_close_lock_held(midday, fdb):
    fake = midday
    write_long_state({"L": 7}, fake)
    with open(os.path.join(config.LOG_DIR, "close.lock"), "w") as fh:
        fh.write(str(os.getppid()))
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert fake.orders == []


def test_h3_fallback_sells_a_late_sleeve_long_before_buying_tonights(fake, fdb, scan_inputs, nowait, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    closing_in(fake, 10)                                                      # 15:50
    write_long_state({"L": 7}, fake)
    scan_inputs.append(gainer("G0", close=20.0))
    fake.adv["G0"] = 50e6
    fake.prices["G0"] = 20.0
    assert run_daily("--execute", "--fallback") == 0
    seq = [e[1:5] for e in long_events(fake)]
    assert seq[0] == ("sell", "market", "L", 7) and seq[1][0] == "buy" and seq[1][2] == "G0"
    assert "L" not in fake.pos and "long_exit_late" in kinds(fdb)
    assert json.load(open(daily.long_state_path()))["entry_date"] == today()  # tonight's state replaces it


# ---------------------------------------------------------------- M1
def test_m1_fresh_covers_unreadable_on_pass_2_sends_nothing(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    planned = [{"ticker": "A", "side": "buy", "qty": 10, "purpose": "cover"}]

    def blind():
        raise RuntimeError("positions 503")
    monkeypatch.setattr(broker, "signed_holdings", blind)
    assert daily.fresh_covers(["A"], "cover", planned, attempt=0) == (planned, {"A": -10})   # pass 1: unchanged
    assert daily.fresh_covers(["A"], "cover", planned, attempt=1) == (None, {})


def test_m1_cover_all_partial_fill_then_blind_pass_never_resends_the_full_qty(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    real_submit, real_held = fake.submit, fake.signed_holdings
    calls = {"n": 0}

    def half(ticker, qty, side, client_order_id=None):               # 4 of 10 fill, the rest is canceled
        o = real_submit(ticker, 4, side, client_order_id)
        fake.orders[-1].update(qty=float(qty), status="canceled")
        return {**o, "qty": float(qty), "status": "canceled"}

    def held():                                                      # readable for the first pass only
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("positions 503")
        return real_held()
    monkeypatch.setattr(broker, "submit", half)
    monkeypatch.setattr(broker, "signed_holdings", held)
    out = daily.cover_all(1, True, dt.datetime.now(ET) + dt.timedelta(minutes=5))
    assert [e[0] for e in fake.events].count("submit") == 1          # one order, no blind re-send of 10
    assert fake.client_ids == [f"v2-cover-{today()}-A-0"]
    assert fake.pos["A"]["qty"] == -6 and out["left"] == {"A": -6}
    assert alert(fdb, "cover_blocked")["level"] == "critical"


# ---------------------------------------------------------------- leftover that is also today's pick
def test_leftover_that_is_also_todays_pick_with_stops_on(opening, fdb):
    fake = opening
    fake.short("A", 10, 20.0)                                        # yesterday's, missed cover
    fake.prices["A"] = 20.0
    write_picks(["A"])
    assert config.BROKER_STOP_PCT == 35
    assert run_daily("--open", "--execute") == 0
    q = qty_for(10_000, 5.0, 20.0)
    seq = [e for e in fake.events if e[0] in ("submit", "stop")]
    assert seq == [("submit", "buy", "A", 10), ("submit", "sell", "A", q), ("stop", "A", q, 27.0)]
    assert fake.pos["A"]["qty"] == -q and [o["ticker"] for o in fake.open_orders] == ["A"]
    assert json.load(open(daily.OPEN_GUARD))["shorts"] == ["A"]
    fake.events.clear()
    assert run_daily("--cover-leftovers", "--execute") == 0          # today's short is not a leftover
    assert buys(fake) == [] and fake.pos["A"]["qty"] == -q and len(fake.open_orders) == 1


# ---------------------------------------------------------------- M2
def test_m2_unreadable_leftover_check_raises_a_critical_alert(midday, fdb):
    fake = midday
    fake.short("A", 25, 20.0)
    fake.sold_today_error = "timeout"
    daily.write_guard(today(), [], daily.OPEN_GUARD, status="started")
    assert run_daily("--cover-leftovers", "--execute") == 2
    a = alert(fdb, "cover_failed")
    assert a["level"] == "critical" and "leftover check unreadable" in a["message"] and fake.orders == []


def test_m2_unreadable_leftover_check_alerts_once_at_the_open(opening, fdb):
    fake = opening
    fake.short("A", 25, 20.0)
    fake.sold_today_error = "timeout"
    daily.write_guard(today(), [], daily.OPEN_GUARD, status="started")       # an earlier 09:31 run died
    run_daily("--open", "--execute", "--force")
    assert kinds(fdb).count("cover_failed") == 1 and buys(fake) == []


def test_m2_open_guard_done_after_leftover_cover_and_no_picks(opening, fdb):
    fake = opening
    fake.short("OLD", 10, 20.0)
    assert run_daily("--open", "--execute") == 0                     # covers, then no picks file
    g = json.load(open(daily.OPEN_GUARD))
    assert g["date"] == today() and g["status"] == "done"
    assert run_daily("--open", "--execute") == 1                     # reruns still blocked
    fake.sold_today_error = "timeout"
    fake.short("NEW", 5, 20.0)
    assert daily.leftover_shorts(today()) == {"NEW": -5}             # not "unfinished" any more


@pytest.mark.parametrize("case", ["stale", "empty"])
def test_m2_open_guard_done_on_stale_or_empty_picks(opening, fdb, case):
    fake = opening
    fake.short("OLD", 10, 20.0)
    write_picks([] if case == "empty" else ["A"], trade_date="2026-01-02" if case == "stale" else None)
    assert run_daily("--open", "--execute") == 0
    assert json.load(open(daily.OPEN_GUARD))["status"] == "done"


# ---------------------------------------------------------------- M3
def test_m3_earnings_cleared_only_by_nasdaq_or_market_events_coverage(monkeypatch):
    scan, nxt = dt.date(2026, 10, 22), [dt.date(2026, 10, 23), dt.date(2026, 10, 26)]
    assert config.EARNINGS_REQUIRE_NASDAQ is True
    monkeypatch.setattr(earnings, "from_market_events", lambda t, a, b: ({}, True))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({}, False))
    monkeypatch.setattr(earnings, "market_events_coverage", lambda t, start: {"COV"})
    got = earnings.blocks(["COV", "NEW"], scan, nxt)
    assert got["COV"] is None and "fail closed" in got["NEW"]
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({}, True))     # Nasdaq answered: all decided
    assert earnings.blocks(["COV", "NEW"], scan, nxt) == {"COV": None, "NEW": None}


def test_m3_market_events_coverage_query(monkeypatch):
    assert earnings.market_events_coverage(["AAA"], dt.date(2026, 10, 22)) == set()   # Supabase off
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    seen = {}

    class R:
        def json(self):
            return [{"ticker": "AAA"}, {"ticker": "AAA"}]

    def send(method, table, rows, params, prefer):
        seen.update(method=method, table=table, **params)
        return R()
    monkeypatch.setattr(db, "_send", send)
    assert earnings.market_events_coverage(["BBB", "AAA"], dt.date(2026, 10, 22)) == {"AAA"}
    assert seen == {"method": "GET", "table": "market_events", "select": "ticker", "kind": "eq.EARNINGS",
                    "ticker": "in.(AAA,BBB)", "event_date": "gte.2026-10-22"}
    monkeypatch.setattr(db, "_send", lambda *a: None)
    assert earnings.market_events_coverage(["AAA"], dt.date(2026, 10, 22)) == set()


# ---------------------------------------------------------------- M4
def test_m4_open_long_exit_skipped_outside_market_hours(fake, fdb, nowait, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=7), now - dt.timedelta(minutes=1))   # after the close
    write_long_state({"L": 7}, fake)
    a = type("A", (), {"execute": True, "now": False})()
    out = daily.open_long_exit(a, today(), fake.session, fake.session[0])
    assert out["sent"] == [] and out["left"] == {"L": 7} and long_events(fake) == []
    assert alert(fdb, "long_exit_failed")["level"] == "critical"
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))   # in hours: sells
    out = daily.open_long_exit(a, today(), fake.session, fake.session[0])
    assert [e[1:5] for e in long_events(fake)] == [("sell", "market", "L", 7)]


# ---------------------------------------------------------------- L1
def test_l1_fallback_with_a_stuck_close_lock_covers_everything(fake, fdb, scan_inputs, nowait):
    closing_in(fake, 10)
    fake.short("OLD", 10, 20.0)
    lock = os.path.join(config.LOG_DIR, "close.lock")
    with open(lock, "w") as fh:
        fh.write(str(os.getppid()))                                   # held, and it never lets go
    assert run_daily("--execute", "--fallback") == 2
    assert buys(fake) == [("OLD", 10)] and fake.pos == {}
    assert fake.client_ids == [f"v2-cover-{today()}-OLD-0"]          # the 15:40 job's own id
    a = alert(fdb, "close_fallback")
    assert a["level"] == "critical" and "close.lock still held" in a["message"]
    assert json.load(open(daily.GUARD))["date"] == today()
    assert os.path.exists(lock) and "close" not in daily._held_locks


def test_l1_fallback_runs_normally_when_the_lock_clears_without_a_guard(fake, fdb, scan_inputs, nowait, monkeypatch):
    closing_in(fake, 10)
    fake.short("OLD", 10, 20.0)
    scan_inputs.append(gainer("G0"))
    fake.adv["G0"] = 50e6
    lock = os.path.join(config.LOG_DIR, "close.lock")
    with open(lock, "w") as fh:
        fh.write(str(os.getppid()))
    monkeypatch.setattr(daily.time, "sleep", lambda s: os.path.exists(lock) and os.remove(lock))
    assert run_daily("--execute", "--fallback") == 0
    assert buys(fake) == [("OLD", 10)] and json.load(open(config.PICKS_FILE))["tickers"] == ["G0"]
    assert "close_fallback" in kinds(fdb)


# ---------------------------------------------------------------- L2
@pytest.mark.parametrize("day, expect", [(dt.date(2026, 11, 26), None), (dt.date(2026, 12, 25), None),
                                         (dt.date(2027, 3, 26), None), (dt.date(2026, 11, 27), (9, 30, 13, 0)),
                                         (dt.date(2026, 10, 23), (9, 30, 16, 0))])
def test_l2_calendar_down_known_holidays_and_half_days(fdb, monkeypatch, day, expect):
    calendar_down(monkeypatch)
    freeze(monkeypatch, dt.datetime.combine(day, dt.time(10, 0), ET))
    s = daily.session_or_fallback()
    assert (s if s is None else (s[0].hour, s[0].minute, s[1].hour, s[1].minute)) == expect


def test_l2_holiday_with_the_calendar_down_sends_nothing(fake, fdb, nowait, monkeypatch):
    calendar_down(monkeypatch)
    freeze(monkeypatch, dt.datetime(2026, 11, 26, 12, 0, tzinfo=ET))           # Thanksgiving, a Thursday
    fake.short("OLD", 10, 20.0)
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert run_daily("--execute", "--fallback") == 0
    assert run_daily("--open", "--execute") == 0
    assert fake.orders == [] and "calendar_failed" not in kinds(fdb)


# ---------------------------------------------------------------- L5
def test_l5_cover_leftovers_needs_short_intraday(midday, fdb, monkeypatch):
    fake = midday
    fake.short("OLD", 10, 20.0)
    monkeypatch.setattr(config, "STRATEGIES", ["short_overnight"])
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert fake.orders == []
