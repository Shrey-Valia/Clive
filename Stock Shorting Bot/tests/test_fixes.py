"""Pre-deployment review fixes: calendar / positions retries (H1), extra cover passes (H2),
Alpaca timeouts and client_order_ids (H3), --half-day-only (H4), cancel / re-read safety (M1),
job locks and the started guard (M2), atomic writes (L1), fractional shorts (L4)."""
from __future__ import annotations

import datetime as dt
import json
import os
import types

import pytest

import daily
import monitor
from conftest import ET, run_daily
from shortingbot import alerts, broker, config, risk, strategy

pytestmark = pytest.mark.usefixtures("legacy_v2_config")   # pre-2026-10-23 settings; see conftest


def today():
    return dt.datetime.now(ET).date().isoformat()


def soon(minutes=5):
    return dt.datetime.now(ET) + dt.timedelta(minutes=minutes)


def kinds(fdb):
    return [r["kind"] for r in fdb.rows("alerts")]


def flaky(fn, fails, exc=RuntimeError("timeout")):
    """fn, failing the first `fails` calls."""
    calls = {"n": 0}

    def wrapped(*a, **k):
        calls["n"] += 1
        if calls["n"] <= fails:
            raise exc
        return fn(*a, **k)
    wrapped.calls = calls
    return wrapped


@pytest.fixture
def closing(fake, monkeypatch):
    """15:55 now: close in 5 minutes, the scan fails at once (the cover must not care)."""
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5, seconds=1))
    monkeypatch.setattr(daily, "scan", lambda: [])
    return fake


# ---------------------------------------------------------------- H1 retries
def test_calendar_retried_then_ok(closing, fdb, monkeypatch):
    real = closing.session_today
    monkeypatch.setattr(broker, "session_today", flaky(real, 2))
    closing.short("A", 10, 20.0)
    assert run_daily("--execute") == 1                    # 1: the scan failed; the cover went
    assert broker.session_today.calls["n"] == 3
    assert closing.pos == {} and "calendar_failed" not in kinds(fdb)


class _At(dt.datetime):
    at = None

    @classmethod
    def now(cls, tz=None):
        return cls.at


@pytest.mark.parametrize("day, expect", [(dt.date(2026, 10, 22), (9, 30, 16, 0)), (dt.date(2026, 10, 24), None)])
def test_calendar_down_falls_back_to_16_on_a_weekday(fdb, monkeypatch, day, expect):
    monkeypatch.setattr(broker, "session_today", flaky(lambda: None, 99))
    _At.at = dt.datetime.combine(day, dt.time(15, 40), ET)
    monkeypatch.setattr(daily.dt, "datetime", _At)
    if expect is None:                                    # Saturday: no guessing
        with pytest.raises(RuntimeError):
            daily.session_or_fallback()
        return
    o, c = daily.session_or_fallback()
    assert (o.hour, o.minute, c.hour, c.minute) == expect and c.date() == day
    assert broker.session_today.calls["n"] == 3 and "calendar_failed" in kinds(fdb)


def test_first_positions_read_retried(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    monkeypatch.setattr(broker, "signed_holdings", flaky(fake.signed_holdings, 2))
    out = daily.cover_all(1, True, soon())
    assert fake.pos == {} and out["left"] == {} and len(fake.sent("buy")) == 1


# ---------------------------------------------------------------- H2 extra cover passes
def test_partial_fill_gets_a_second_pass(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    real = fake.submit
    first = {"done": False}

    def half(ticker, qty, side, client_order_id=None):
        if not first["done"]:                             # only 4 of 10 fill, the rest is canceled
            first["done"] = True
            o = real(ticker, 4, side, client_order_id)
            fake.orders[-1].update(qty=float(qty), status="canceled")
            return {**o, "qty": float(qty), "status": "canceled"}
        return real(ticker, qty, side, client_order_id)
    monkeypatch.setattr(broker, "submit", half)
    out = daily.cover_all(1, True, soon())
    assert fake.pos == {} and out["left"] == {} and out["problems"] == []
    assert [e[3] for e in fake.events if e[0] == "submit"] == [4, 6]   # never more than the short
    assert fake.client_ids == [f"v2-cover-{today()}-A-0", f"v2-cover-{today()}-A-1"]
    assert [e[0] for e in fake.events].count("cancel") == 2           # each pass cancels first


def test_cover_stops_after_two_extra_passes_and_alerts(closing, fdb):
    closing.short("A", 10, 20.0)
    closing.reject["A"] = "temporarily unavailable"
    assert run_daily("--execute") == 1
    assert [e[0] for e in closing.events].count("submit") == 1 + config.COVER_EXTRA_PASSES
    assert closing.pos["A"]["qty"] == -10
    alert = next(r for r in fdb.rows("alerts") if r["kind"] == "cover_failed")
    assert alert["level"] == "critical"


def test_no_extra_pass_after_the_cutoff(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    fake.reject["A"] = "temporarily unavailable"
    real = fake.submit
    cutoff = soon()

    def late(*a, **k):                                    # the first pass takes us past the cutoff
        _At.at = cutoff + dt.timedelta(seconds=1)
        monkeypatch.setattr(daily.dt, "datetime", _At)
        return real(*a, **k)
    monkeypatch.setattr(broker, "submit", late)
    out = daily.cover_all(1, True, cutoff)
    assert [e[0] for e in fake.events].count("submit") == 1
    assert out["left"] == {"A": -10} and any("cutoff" in p for p in out["problems"])


# ---------------------------------------------------------------- H3 timeouts and client_order_id
def test_alpaca_clients_have_a_timeout(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setattr(broker, "_client", None)
    monkeypatch.setattr(broker, "_data", None)
    for c in (broker.client(), broker.data_client()):     # building a client makes no request
        assert c._session.request.keywords == {"timeout": config.ALPACA_TIMEOUT_SECONDS} == {"timeout": 15}


def _order(**kw):
    o = dict(id="abc", symbol="A", side=types.SimpleNamespace(value="buy"), qty="5",
             status=types.SimpleNamespace(value="filled"), submitted_at=None, filled_qty="5",
             filled_avg_price="20", filled_at=None, stop_price=None)
    o.update(kw)
    return types.SimpleNamespace(**o)


class FakeAlpaca:
    def __init__(self, error=None):
        self.error, self.reqs, self.looked_up, self.cancelled = error, [], [], []
        self.open = []
        self.cancel_error = {}

    def submit_order(self, req):
        self.reqs.append(req)
        if self.error:
            raise self.error
        return _order(id="new")

    def get_order_by_client_id(self, coid):
        self.looked_up.append(coid)
        return _order(id="first")

    def get_orders(self, req):
        return self.open

    def cancel_order_by_id(self, oid):
        if oid in self.cancel_error:
            raise RuntimeError(self.cancel_error[oid])
        self.cancelled.append(oid)


def test_submit_sends_client_order_id(monkeypatch):
    fa = FakeAlpaca()
    monkeypatch.setattr(broker, "client", lambda: fa)
    assert broker.submit("A", 5, "buy", client_order_id="v2-cover-x-A-0")["id"] == "new"
    broker.submit_stop("A", 5, 27.0, client_order_id="v2-stop-x-A-0")
    assert [r.client_order_id for r in fa.reqs] == ["v2-cover-x-A-0", "v2-stop-x-A-0"]


def test_duplicate_client_order_id_is_already_sent(monkeypatch):
    fa = FakeAlpaca(RuntimeError('{"code":40010001,"message":"client_order_id must be unique"}'))
    monkeypatch.setattr(broker, "client", lambda: fa)
    assert broker.submit("A", 5, "buy", client_order_id="v2-cover-x-A-0")["id"] == "first"
    assert fa.looked_up == ["v2-cover-x-A-0"]


def test_other_rejection_still_raises(monkeypatch):
    fa = FakeAlpaca(RuntimeError("insufficient buying power"))
    monkeypatch.setattr(broker, "client", lambda: fa)
    with pytest.raises(RuntimeError, match="buying power"):
        broker.submit("A", 5, "buy", client_order_id="v2-cover-x-A-0")
    assert fa.looked_up == []


def test_every_order_carries_a_deterministic_id(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "BROKER_STOP_PCT", 35)
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(minutes=5))
    from test_daily import write_picks
    write_picks(["P1"])
    fake.prices["P1"] = 20.0
    assert run_daily("--open", "--execute") == 0
    assert fake.client_ids == [f"v2-short-{today()}-P1-0", f"v2-stop-{today()}-P1-0"]


# ---------------------------------------------------------------- H4 --half-day-only
@pytest.mark.parametrize("close_hour, runs", [(16, False), (13, True), (None, False)])
def test_half_day_only(fake, fdb, monkeypatch, close_hour, runs):
    called = []
    monkeypatch.setattr(daily, "close_intraday", lambda a, today, mode: called.append(a) or 0)
    monkeypatch.setitem(daily.CLOSE_JOBS, "short_intraday", daily.close_intraday)
    now = dt.datetime.now(ET)
    fake.session = None if close_hour is None else (
        now.replace(hour=9, minute=30), now.replace(hour=close_hour, minute=0, second=0, microsecond=0))
    assert run_daily("--execute", "--half-day-only") == 0
    assert bool(called) == runs and fake.orders == []
    if not runs:
        assert fdb.rows("short_runs") == []


# ---------------------------------------------------------------- M1 cancel / re-read safety
def test_cancel_tries_every_order(monkeypatch):
    fa = FakeAlpaca()
    fa.open = [types.SimpleNamespace(id="1", symbol="A"), types.SimpleNamespace(id="2", symbol="B"),
               types.SimpleNamespace(id="3", symbol="C")]
    fa.cancel_error["1"] = "order is not cancelable (filled)"
    monkeypatch.setattr(broker, "client", lambda: fa)
    assert broker.cancel_open_orders(["A", "B"]) == 2
    assert fa.cancelled == ["2"]


def test_reread_fails_after_cancel_sends_nothing(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    fake.submit_stop("A", 10, 27.0)
    real = fake.signed_holdings
    reads = {"n": 0}

    def holdings():
        reads["n"] += 1
        if reads["n"] > 1:
            raise RuntimeError("positions timeout")
        return real()
    monkeypatch.setattr(broker, "signed_holdings", holdings)
    out = daily.cover_all(1, True, soon())
    assert fake.sent("buy") == [] and out["left"] == {"A": -10}
    assert "cover_blocked" in kinds(fdb)


def test_order_that_wont_cancel_gets_no_cover(fake, fdb, monkeypatch):
    fake.short("A", 10, 20.0)
    fake.short("B", 5, 20.0)
    stuck = {"ticker": "A", "status": "pending_cancel"}
    monkeypatch.setattr(broker, "wait_no_open_orders", lambda symbols, seconds=10: False)
    monkeypatch.setattr(broker, "open_order_symbols", lambda symbols: [stuck["ticker"]])
    out = daily.cover_all(1, True, soon())
    assert [o["ticker"] for o in fake.sent("buy")] == ["B"]
    assert out["left"] == {"A": -10}


# ---------------------------------------------------------------- M2 locks and the started guard
def test_lock_busy_and_stale(monkeypatch):
    path = os.path.join(config.LOG_DIR, "close.lock")
    open(path, "w").write(str(os.getppid()))             # a live process holds it
    with pytest.raises(daily.LockBusy):
        with daily.job_lock("close"):
            pass
    open(path, "w").write("999999")                       # a dead pid: cleared
    monkeypatch.setattr(daily, "_pid_alive", lambda pid: False)
    with daily.job_lock("close"):
        assert open(path).read() == str(os.getpid())
        with daily.job_lock("close"):                     # re-entrant in one process
            pass
        assert os.path.exists(path)
    assert not os.path.exists(path)


def test_execute_refuses_while_locked(closing, fdb):
    closing.short("A", 10, 20.0)
    open(os.path.join(config.LOG_DIR, "close.lock"), "w").write(str(os.getppid()))
    assert run_daily("--execute") == 1
    assert closing.orders == [] and "job_locked" in kinds(fdb)
    assert not os.path.exists(daily.GUARD)


def test_monitor_flatten_skips_while_the_cover_runs(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 5.0)
    fake.short("A", 10, 20.0, 21.0)
    fake.last_equity = 11_000.0
    open(os.path.join(config.LOG_DIR, "close.lock"), "w").write(str(os.getppid()))
    assert monitor.main(["--now"]) == 0
    assert fake.orders == [] and "cover_failed" in kinds(fdb)


def test_started_guard_before_any_order(closing, fdb, monkeypatch):
    closing.short("A", 10, 20.0)
    seen = []
    real = closing.submit
    monkeypatch.setattr(broker, "submit", lambda *a, **k: (seen.append(json.load(open(daily.GUARD))), real(*a, **k))[1])
    run_daily("--execute")
    assert seen == [{"date": today(), "orders": [], "status": "started"}]
    assert json.load(open(daily.GUARD)) == {"date": today(), "orders": ["o1"]}
    assert not os.path.exists(os.path.join(config.LOG_DIR, "close.lock"))


# ---------------------------------------------------------------- L1 atomic writes, L4 fractions
def test_writes_are_atomic():
    daily.write_guard("2026-10-22", [{"id": "x"}])
    risk.set_halt("2026-10-22", "test")
    alerts._save({"k": 1})
    assert json.load(open(daily.GUARD))["orders"] == ["x"] and risk.halted_today("2026-10-22") == "test"
    assert not [f for f in os.listdir(config.LOG_DIR) if f.endswith(".tmp")]


def test_fractional_short_never_overbought(monkeypatch, caplog):
    monkeypatch.setattr(broker, "positions", lambda: [{"ticker": "A", "qty": -51.0000001}, {"ticker": "B", "qty": -2.5},
                                                      {"ticker": "C", "qty": 3.0}])
    held = broker.signed_holdings()
    assert held == {"A": -51, "B": -2.5, "C": 3} and isinstance(held["A"], int)
    assert "fractional position B" in caplog.text
    assert [(c["ticker"], c["qty"]) for c in strategy.plan_cover(held)] == [("A", 51), ("B", 2.5)]
    assert strategy.cover_qty(-50.9999999) == 51 and strategy.cover_qty(-0.4) == 0.4
