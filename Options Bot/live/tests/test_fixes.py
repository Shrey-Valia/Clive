"""Regression tests for the fixes from REVIEW_orders.md and REVIEW_strategy.md.

Each test reproduces the failure the review described with a fake broker /
fake session (no network) and checks the fixed behaviour.

Run:  python3 -m pytest -q live/tests/
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import config as C  # noqa: E402
from live import db, execution, signals, state  # noqa: E402
from live import strategy as S  # noqa: E402
from live import run as R  # noqa: E402
from live.broker import ET, Broker, SafetyError, client_order_id, is_bot_order  # noqa: E402
from test_live import TODAY, FakeClock, FakeDataBroker, Q, _planner_setup, chain_for  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """State, env files and Supabase are isolated per test."""
    monkeypatch.setattr(C, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(C, "SHORTING_ENV_PATH", tmp_path / "shorting.env")
    monkeypatch.setattr(C, "ENV_PATH", tmp_path / "options.env")
    beats = []
    monkeypatch.setattr(db, "heartbeat", lambda st, msg, **k: beats.append((st, msg)))
    monkeypatch.setattr(db, "insert", lambda *a, **k: [])
    monkeypatch.setattr(db, "update", lambda *a, **k: None)
    return SimpleNamespace(tmp=tmp_path, beats=beats)


# ====================================================================== fake order book
class Book:
    """Alpaca-like order book with hooks for races and failures."""

    def __init__(self):
        self.orders, self.by_cid, self.n, self.log = {}, {}, 0, []
        self.on_get = None              # fn(book, order) run on every GET
        self.replace_mode = "ok"        # ok | raise | raise_after_apply | race_filled
        self.submit_mode = "ok"         # ok | raise_after_create | raise
        self.submit_fail_from = None    # submits numbered >= this raise (no order created)
        self.cancel_fail = 0

    def _new(self, legs, qty, limit, cid):
        self.n += 1
        oid = f"o{self.n}"
        o = {"id": oid, "client_order_id": cid, "status": "new", "qty": str(qty), "filled_qty": "0",
             "filled_avg_price": None, "limit_price": f"{limit:.2f}", "legs": legs}
        self.orders[oid], self.by_cid[cid] = o, oid
        return o

    def submit_mleg(self, legs, qty, limit, cid):
        self.log.append(("submit", qty, limit))
        if self.submit_fail_from is not None and sum(a[0] == "submit" for a in self.log) >= self.submit_fail_from:
            raise RuntimeError("HTTP 403 insufficient options buying power")
        if self.submit_mode == "raise":
            raise RuntimeError("timeout")
        o = self._new(legs, qty, limit, cid)
        if self.submit_mode == "raise_after_create":
            self.submit_mode = "ok"
            raise RuntimeError("read timeout")
        return dict(o)

    def get_order_by_client_id(self, cid):
        oid = self.by_cid.get(cid)
        return dict(self.orders[oid]) if oid else None

    def get_order(self, oid):
        o = self.orders[oid]
        if self.on_get:
            self.on_get(self, o)
        return dict(o)

    def replace_order(self, oid, limit):
        self.log.append(("replace", oid, limit))
        o = self.orders[oid]
        if self.replace_mode == "raise":
            raise RuntimeError("HTTP 422 not replaceable")
        new = self._new(o["legs"], int(o["qty"]), limit, o["client_order_id"] + "r")
        if self.replace_mode == "race_filled":   # old order fills first; Alpaca still answered with a new id
            o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["limit_price"])
            return dict(new)
        o.update(status="replaced", replaced_by=new["id"])
        if self.replace_mode == "raise_after_apply":
            raise RuntimeError("read timeout")
        return dict(new)

    def cancel_order(self, oid):
        self.log.append(("cancel", oid))
        if self.cancel_fail > 0:
            self.cancel_fail -= 1
            raise RuntimeError("HTTP 500")
        o = self.orders[oid]
        if o["status"] in ("new", "partially_filled", "accepted", "stopped"):
            o["status"] = "canceled"

    def open_orders(self):
        return [dict(o) for o in self.orders.values() if o["status"] in ("new", "partially_filled", "accepted")]

    def acts(self, kind):
        return [a for a in self.log if a[0] == kind]


def _job(prices, qty=2, credit=True, cutoff_price=None):
    legs = execution.open_legs("SPY261030P00700000", "SPY261030P00690000") if credit else \
        execution.close_legs("SPY261030P00700000", "SPY261030P00690000")
    return execution.Job("SPY-x", "open" if credit else "close", legs, qty, prices, credit, cutoff_price=cutoff_price)


def _work(book, jobs, minutes=4, step=30):
    clk = FakeClock()
    execution.work_orders(book, jobs, cutoff=clk.now() + dt.timedelta(minutes=minutes), clock=clk, step_wait=step)
    return clk


# ====================================================================== #2 replace / fill races
def test_partially_filled_order_is_never_replaced():
    b = Book()

    def partial(book, o):
        if o["id"] == "o1" and o["status"] == "new":
            o.update(status="partially_filled", filled_qty="1", filled_avg_price="-1.22")
    b.on_get = partial
    j = _job([1.22, 1.20])
    _work(b, [j])
    assert not b.acts("replace")                       # cancel + resubmit instead
    assert ("cancel", "o1") in b.log
    assert b.acts("submit")[1][1:] == (1, -1.20)       # only the remaining spread
    assert j.filled_qty == 1 and j.status == "partial" and j.avg_price == pytest.approx(1.22)
    assert not b.open_orders()


def test_replace_that_races_a_fill_keeps_the_fill_and_cancels_the_new_order():
    b = Book()
    b.replace_mode = "race_filled"
    j = _job([1.22, 1.20])
    _work(b, [j])
    assert j.filled_qty == 2 and j.status == "filled" and j.avg_price == pytest.approx(1.22)
    assert b.orders["o2"]["status"] == "canceled"      # the replacement can not also fill
    assert len(b.acts("submit")) == 1                  # nothing resubmitted
    assert j.fills == {"o1": (2.0, 1.22)}              # recomputed per order id


def test_replace_timeout_follows_replaced_by_instead_of_resubmitting():
    b = Book()
    b.replace_mode = "raise_after_apply"
    j = _job([1.22, 1.20])
    _work(b, [j])
    assert b.orders["o1"]["status"] == "replaced" and "o2" in j.order_ids
    assert len(b.acts("submit")) == 1                  # no second live order
    assert ("cancel", "o1") not in b.log
    assert not b.open_orders()                         # o2 canceled at the end


def test_replaced_and_stopped_are_not_terminal():
    assert "replaced" not in execution.TERMINAL
    assert {"stopped", "suspended"}.isdisjoint(execution.TERMINAL)
    assert execution.TERMINAL == {"filled", "canceled", "expired", "rejected", "done_for_day"}
    b = Book()
    b.on_get = lambda book, o: o.update(status="stopped") if o["status"] == "new" else None
    j = _job([1.22])
    _work(b, [j])
    assert ("cancel", "o1") in b.log and j.status == "unfilled"      # kept working, then canceled


# ====================================================================== #3 timeouts
def test_submit_timeout_adopts_the_order_found_by_client_order_id():
    b = Book()
    b.submit_mode = "raise_after_create"
    b.on_get = lambda book, o: o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["limit_price"])
    j = _job([1.22])
    _work(b, [j])
    assert b.n == 1 and j.order_ids == ["o1"]
    assert j.status == "filled" and j.filled_qty == 2


def test_final_sweep_cancels_open_orders_of_the_job(monkeypatch):
    b = Book()
    j = _job([1.22, 1.20])
    real_wait = execution._wait
    calls = {"n": 0}

    def crash(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom in the ladder")
        return real_wait(*a, **k)
    monkeypatch.setattr(execution, "_wait", crash)
    with pytest.raises(RuntimeError, match="boom"):
        _work(b, [j])
    assert not b.open_orders() and j.status == "unfilled"


# ====================================================================== #4 exception safety in the ladder
def test_resubmit_exception_is_contained_and_every_order_canceled():
    """The reviewer's reprice_fail.py: replace fails, the resubmit raises 403."""
    b = Book()
    b.replace_mode = "raise"
    b.submit_fail_from = 3
    ja = execution.Job("A", "open", [{"symbol": "A", "side": "sell", "position_intent": "sell_to_open"}], 1,
                       [1.0, 0.9], True)
    jb = execution.Job("B", "open", [{"symbol": "B", "side": "sell", "position_intent": "sell_to_open"}], 1,
                       [1.0, 0.9], True)
    _work(b, [ja, jb], minutes=8)                      # must not raise
    assert not b.open_orders()
    assert {ja.status, jb.status} <= {"error", "unfilled"}
    assert any("403" in (j.error or "") for j in (ja, jb))


def _daily_fake(now, positions=None, extra_chain=()):
    fb, spot = _planner_setup(dip=True)
    chain = [dataclasses.replace(q, ts=now) for q in fb.chain + list(extra_chain)]

    class DailyFake(FakeDataBroker):
        has_trading = True
        execute = True
        data_creds = SimpleNamespace(source="options")
        trading_error = None

        def __init__(self):
            super().__init__(fb.hist, fb.live, chain, positions or [])
            self.cancels = 0

        def stock_snapshot(self, sym):
            return {**super().stock_snapshot(sym), "trade_time": now}

        def calendar(self, a, b):
            return [{"date": d.date().isoformat(), "open": "09:30", "close": "16:00"} for d in pd.bdate_range(a, b)]

        def account(self):
            return {"equity": 10_000.0, "options_buying_power": 10_000.0, "options_trading_level": 3,
                    "trading_blocked": False}

        def cancel_all(self, timeout=30):
            self.cancels += 1
            return []

        def fills(self, after):
            return []

        def activities(self, types, after):
            return []

        def open_orders(self):
            return []

    return DailyFake()


def _patch_daily(monkeypatch, fake, now):
    monkeypatch.setattr(R, "Broker", lambda execute=False: fake)
    monkeypatch.setattr(R, "now_et", lambda: now)
    monkeypatch.setattr(R.signals, "vix_levels", lambda today, now=None: {
        "vix": 17.0, "vix3m": 19.0, "ratio": 17 / 19, "fresh": True, "stale_reason": "", "source": "test"})
    monkeypatch.setattr(R, "_lock", lambda timeout: open(C.STATE_DIR / ".lock-test", "w"))
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)


def test_run_daily_books_fills_and_cancels_even_when_work_orders_raises(monkeypatch, isolated):
    now = dt.datetime.combine(TODAY, dt.time(15, 55, 10), ET)
    fake = _daily_fake(now)
    _patch_daily(monkeypatch, fake, now)

    def fill_then_crash(broker, jobs, **kw):
        j = jobs[0]
        j.order_ids.append("o1")
        j.fills["o1"] = (float(j.qty), j.prices[0])
        raise RuntimeError("network died mid-ladder")
    monkeypatch.setattr(R.execution, "work_orders", fill_then_crash)
    assert R.main(["--execute", "--now"]) == 1
    rec = state.load_positions()
    assert len(rec) == 1 and next(iter(rec.values()))["entry_date"] == TODAY.isoformat()
    assert fake.cancels >= 2                            # startup sweep + finally sweep
    assert isolated.beats[-1][0] == "error" and "network died" in isolated.beats[-1][1]


def test_entry_date_recovered_from_journal_then_fills(monkeypatch):
    e = dt.date(2026, 10, 30)
    ss, ls = S.occ_symbol("SPY", e, "P", 700), S.occ_symbol("SPY", e, "P", 690)
    pos = [{"symbol": ss, "qty": -1, "side": "short", "avg_entry_price": 2.0, "asset_class": "us_option"},
           {"symbol": ls, "qty": 1, "side": "long", "avg_entry_price": 0.8, "asset_class": "us_option"}]
    fb = FakeDataBroker({}, {}, [], pos)
    fb.fills = lambda after: [{"symbol": ss, "side": "sell", "transaction_time": "2026-10-19T19:56:00Z"}]
    rec, *_ = R.load_book(fb, TODAY, persist=False)
    sid = "SPY-20261030-P700/690"
    assert rec[sid]["entry_date"] == "2026-10-19" and rec[sid]["entry_date_source"] == "fills"
    state.append(state.ORDERS, {"key": sid, "purpose": "open", "filled_qty": 1, "date": "2026-10-16"})
    rec, *_ = R.load_book(fb, TODAY, persist=False)
    assert rec[sid]["entry_date"] == "2026-10-16" and rec[sid]["entry_date_source"] == "order journal"


# ====================================================================== #1 cron lock collision
def test_intraday_checks_its_window_before_taking_the_lock(monkeypatch):
    monkeypatch.setattr(R, "Broker", lambda execute=False: SimpleNamespace())
    monkeypatch.setattr(R, "market_session", lambda b, d: {
        "open": True, "open_at": dt.datetime.combine(TODAY, dt.time(9, 30), ET),
        "close_at": dt.datetime.combine(TODAY, dt.time(16, 0), ET)})
    monkeypatch.setattr(R, "now_et", lambda: dt.datetime.combine(TODAY, dt.time(15, 45), ET))

    def no_lock(timeout):
        raise AssertionError("lock taken outside the window")
    monkeypatch.setattr(R, "_lock", no_lock)
    assert R.main(["--intraday", "--execute"]) == 0


def test_daily_waits_for_lock_and_sends_heartbeat_when_it_fails(monkeypatch, isolated):
    now = dt.datetime.combine(TODAY, dt.time(15, 45, 5), ET)
    fake = _daily_fake(now)
    _patch_daily(monkeypatch, fake, now)
    seen = {}

    def busy(timeout):
        seen["timeout"] = timeout
        return None
    monkeypatch.setattr(R, "_lock", busy)
    assert R.main(["--execute"]) == 1
    assert seen["timeout"] == C.LOCK_TIMEOUT_SECONDS > 0          # blocking wait, not LOCK_NB-and-quit
    assert isolated.beats and isolated.beats[-1][0] == "error" and "lock" in isolated.beats[-1][1]


def test_lock_helper_times_out_and_is_reacquirable(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    a = R._lock(0)
    assert a is not None
    assert R._lock(4) is None
    a.close()
    b = R._lock(0)
    assert b is not None
    b.close()


def test_readme_cron_lines_avoid_the_1545_collision():
    text = (Path(C.LIVE_DIR) / "README.md").read_text()
    assert "*/15 10-15" not in text
    assert "0,15,30 15 * * 1-5" in text and "*/15 10-14 * * 1-5" in text
    assert "--sweep --execute" in text


# ====================================================================== #5 shorting bot account
def test_refuses_the_shorting_bot_key_without_printing_it(isolated):
    isolated.tmp.joinpath("shorting.env").write_text("APCA_API_KEY_ID=PKSAMEKEY123\nAPCA_API_SECRET_KEY=zz\n")
    env = {"OPT_APCA_API_KEY_ID": "PKSAMEKEY123", "OPT_APCA_API_SECRET_KEY": "s", "OPT_APCA_PAPER": "true"}
    with pytest.raises(C.ConfigError) as e:
        C.trading_creds(env)
    assert "PKSAMEKEY123" not in str(e.value) and "Shorting" in str(e.value)


class _Resp:
    def __init__(self, js=None, code=200):
        self._js, self.status_code = js, code
        self.text = "" if js is None else json.dumps(js)

    def json(self):
        return self._js


class Sess:
    """Scripted requests.Session: handler(method, path, params, body) -> _Resp."""

    def __init__(self, handler, account=None):
        self.handler, self.calls = handler, []
        self.account = account or {"account_number": "PA111", "id": "acc-1"}

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = url.replace("https://paper-api.alpaca.markets", "")
        self.calls.append((method, path))
        if path == "/v2/account":
            return _Resp(self.account)
        return self.handler(method, path, params, json)


ENV = {"OPT_APCA_API_KEY_ID": "PKTEST", "OPT_APCA_API_SECRET_KEY": "s", "OPT_APCA_PAPER": "true"}


def _broker(handler, account=None, execute=True):
    s = Sess(handler, account)
    b = Broker(execute=execute, env=dict(ENV), session=s)
    b.sleep = lambda x: None
    return b, s


def test_refuses_the_shorting_bot_account_id(isolated):
    isolated.tmp.joinpath("options.env").write_text("SHORTING_ACCOUNT_ID=acc-shorting\n")
    b, _ = _broker(lambda *a: _Resp([]), account={"account_number": "PA9", "id": "acc-shorting"})
    with pytest.raises(SafetyError) as e:
        b.positions()
    assert "acc-shorting" not in str(e.value)


def test_account_is_pinned_and_a_different_one_refused():
    b, _ = _broker(lambda *a: _Resp([]))
    assert b.positions() == []
    b2, _ = _broker(lambda *a: _Resp([]), account={"account_number": "PA222", "id": "acc-2"})
    with pytest.raises(SafetyError, match="different account"):
        b2.positions()


def test_cancel_all_touches_only_bot_orders_and_waits_until_none_open():
    open_ = [{"id": "b1", "client_order_id": "options-open-x-1", "status": "new"},
             {"id": "f1", "client_order_id": "shortbot-123", "status": "new"}]

    def h(method, path, params, body):
        if method == "GET" and path == "/v2/orders":
            return _Resp(list(open_))
        if method == "DELETE" and path == "/v2/orders/b1":
            open_.pop(0)
            return _Resp(None, 204)
        raise AssertionError(f"unexpected {method} {path}")
    b, s = _broker(h)
    assert b.cancel_all() == []
    assert ("DELETE", "/v2/orders") not in s.calls and ("DELETE", "/v2/orders/f1") not in s.calls
    assert s.calls.count(("GET", "/v2/orders")) == 2              # polled until no bot order is open
    assert is_bot_order({"client_order_id": "options-x"}) and not is_bot_order({"client_order_id": "x"})


# ====================================================================== #10 cancel retry
def test_failed_cancel_is_retried_with_backoff():
    b = Book()
    b.cancel_fail = 2
    j = _job([1.22])
    _work(b, [j])
    assert len(b.acts("cancel")) >= 3 and b.orders["o1"]["status"] == "canceled"
    assert j.status == "unfilled"


# ====================================================================== #11 client_order_id
def test_client_order_ids_are_unique_and_fit_128_chars():
    a = client_order_id("open", "SPY-20261030-P652.5/642.5", 0)
    b = client_order_id("open", "SPY-20261030-P652.5/642.5", 0)
    assert a != b and a.startswith("options-") and "/" not in a
    long = client_order_id("close", "X" * 300, 12)
    assert len(long) <= 128 and long.startswith("options-")
    j = _job([1.22])
    bk = Book()
    _work(bk, [j])
    assert len(j.cids[0]) <= 128 and j.cids[0].startswith(C.ORDER_PREFIX)


# ====================================================================== #12 filled_avg_price fallback
def test_avg_price_falls_back_to_leg_prices():
    j = _job([1.22])
    o = {"id": "o1", "status": "filled", "filled_qty": "2", "filled_avg_price": None,
         "legs": [{"side": "sell", "filled_avg_price": "2.00", "ratio_qty": "1"},
                  {"side": "buy", "filled_avg_price": "0.80", "ratio_qty": "1"}]}
    execution._absorb(j, o)
    assert j.avg_price == pytest.approx(1.20)
    j2 = _job([1.22])
    execution._absorb(j2, {"id": "o2", "status": "filled", "filled_qty": "2", "limit_price": "-1.18"})
    assert j2.avg_price == pytest.approx(1.18)                    # last resort: the limit


# ====================================================================== #13 retries
def test_post_is_retried_only_after_a_client_order_id_lookup():
    seq = {"post": 0, "lookup": 0}

    def h(method, path, params, body):
        if method == "POST":
            seq["post"] += 1
            return _Resp({"message": "oops"}, 503) if seq["post"] == 1 else _Resp({"id": "new1", "status": "new"})
        if path == "/v2/orders:by_client_order_id":
            seq["lookup"] += 1
            return _Resp({"message": "not found"}, 404)
        raise AssertionError(path)
    b, s = _broker(h)
    o = b.submit_mleg([{"symbol": "A", "side": "sell", "position_intent": "sell_to_open"}], 1, -1.0, "options-t-1")
    assert o["id"] == "new1" and seq == {"post": 2, "lookup": 1}
    idx = [c for c in s.calls if c[0] != "GET" or c[1] != "/v2/account"]
    assert idx[0][0] == "POST" and idx[1][1] == "/v2/orders:by_client_order_id" and idx[2][0] == "POST"


def test_post_failure_finds_existing_order_and_does_not_resubmit():
    seq = {"post": 0}

    def h(method, path, params, body):
        if method == "POST":
            seq["post"] += 1
            return _Resp({"message": "gateway"}, 504)
        return _Resp({"id": "existing", "status": "new", "client_order_id": params["client_order_id"]})
    b, _ = _broker(h)
    o = b.submit_mleg([], 1, -1.0, "options-t-2")
    assert o["id"] == "existing" and seq["post"] == 1


def test_get_retries_on_429():
    n = {"k": 0}

    def h(method, path, params, body):
        n["k"] += 1
        return _Resp({"message": "rate"}, 429) if n["k"] < 3 else _Resp([])
    b, _ = _broker(h)
    assert b.positions() == [] and n["k"] == 3


# ====================================================================== #16 exact hostname
@pytest.mark.parametrize("url", ["https://paper-api.alpaca.markets.evil.com", "http://paper-api.alpaca.markets",
                                 "https://evil.com/paper-api.alpaca.markets", "https://api.alpaca.markets",
                                 "https://user@paper-api.alpaca.markets", "https://paper-api.alpaca.markets:8443"])
def test_paper_url_requires_exact_https_host(url):
    with pytest.raises(C.ConfigError):
        C.assert_paper_url(url)


def test_paper_url_accepts_the_real_endpoint():
    C.assert_paper_url("https://paper-api.alpaca.markets")
    C.assert_paper_url("https://paper-api.alpaca.markets/")


# ====================================================================== zero bids / expiry (orders #6, strategy #3)
def test_exit_prices_accept_zero_bid_on_the_leg_being_sold():
    sq = Q(700, -0.05, 0.30, 0.35)
    lq = Q(690, -0.01, 0.0, 0.05)
    mid, nat, note = S.exit_prices(sq, lq)
    assert mid == pytest.approx(0.30) and nat == pytest.approx(0.35) and "bids 0" in note
    mid, nat, note = S.exit_prices(sq, None)
    assert mid == pytest.approx(0.325) and "unquoted" in note
    assert S.exit_prices(Q(700, -0.05, 0.0, 0.0), lq)[0] is None


def _exit_setup(expiry, short_q=(0.30, 0.35), long_q=(0.0, 0.05), entry_credit=1.0, entry_mid=None):
    fb, _ = _planner_setup(dip=False)
    sq = Q(700, -0.05, *short_q, exp=expiry)
    lq = Q(690, -0.01, *long_q, exp=expiry)
    fb.chain = [q for q in (sq, lq) if q.ask > 0]
    sid = S.spread_id("SPY", expiry, 700, 690)
    rec = {sid: {"underlying": "SPY", "expiry": expiry.isoformat(), "short_symbol": sq.symbol,
                 "long_symbol": lq.symbol, "short_strike": 700, "long_strike": 690, "qty": 1,
                 "entry_date": TODAY.isoformat(), "entry_credit": entry_credit, "entry_mid": entry_mid}}
    ses = signals.sessions_from_bars(fb.hist["SPY"].index, TODAY)
    return fb, rec, ses


def test_expiry_exit_not_blocked_by_a_zero_bid_long_leg():
    fb, rec, ses = _exit_setup(TODAY + dt.timedelta(days=1))           # tdte 1
    jobs, rows = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=True)
    assert len(jobs) == 1 and rows[0]["reason"] == "expiry_exit"
    assert jobs[0].prices[0] == pytest.approx(0.30)


def test_expiry_exit_without_any_quotes_is_priced_from_intrinsic_and_warns():
    fb, rec, ses = _exit_setup(TODAY + dt.timedelta(days=1), short_q=(0, 0), long_q=(0, 0))
    ctx = R.Ctx(fb, TODAY)
    jobs, rows = R.plan_exits(ctx, rec, ses, end_of_day=True)
    assert len(jobs) == 1 and jobs[0].cutoff_price <= 10
    assert jobs[0].prices == sorted(jobs[0].prices) and jobs[0].prices[0] > 0
    assert any("no usable quotes" in w for w in ctx.warnings)


def test_non_forced_exit_without_quotes_is_blocked_with_a_warning():
    fb, rec, ses = _exit_setup(TODAY + dt.timedelta(days=9), short_q=(0, 0), long_q=(0, 0))
    rec[next(iter(rec))]["entry_date"] = "2026-10-19"                   # held 2 days; rally -> sma5 exit
    ctx = R.Ctx(fb, TODAY)
    jobs, rows = R.plan_exits(ctx, rec, ses, end_of_day=True)
    assert not jobs and "blocked" in rows[0]["reason"] and ctx.warnings


def test_daily_alerts_error_when_an_expiry_exit_is_unfilled(monkeypatch, isolated):
    now = dt.datetime.combine(TODAY, dt.time(15, 55, 10), ET)
    exp = TODAY + dt.timedelta(days=1)
    ss, ls = S.occ_symbol("SPY", exp, "P", 700), S.occ_symbol("SPY", exp, "P", 690)
    pos = [{"symbol": ss, "qty": -1, "side": "short", "avg_entry_price": 1.0, "asset_class": "us_option"},
           {"symbol": ls, "qty": 1, "side": "long", "avg_entry_price": 0.2, "asset_class": "us_option"}]
    fake = _daily_fake(now, pos, extra_chain=[Q(700, -0.05, 0.30, 0.35, exp=exp), Q(690, -0.01, 0.0, 0.05, exp=exp)])
    _patch_daily(monkeypatch, fake, now)
    state.save_positions({S.spread_id("SPY", exp, 700, 690): {
        "underlying": "SPY", "expiry": exp.isoformat(), "short_symbol": ss, "long_symbol": ls, "short_strike": 700,
        "long_strike": 690, "qty": 1, "entry_date": "2026-10-19", "entry_credit": 0.8, "entry_mid": 0.8}})
    monkeypatch.setattr(R.execution, "work_orders", lambda broker, jobs, **kw: jobs)   # nothing fills
    assert R.main(["--execute", "--now", "--symbols", "QQQ"]) == 0
    st, msg = isolated.beats[-1]
    assert st == "error" and "EXPIRY EXIT NOT FILLED" in msg and "--sweep" in msg


def test_intraday_leaves_tdte1_exit_to_the_daily_run_but_sweeps_expiry_day():
    fb, rec, ses = _exit_setup(TODAY + dt.timedelta(days=1))
    jobs, _ = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=False, mode="intraday")
    assert not jobs
    fb, rec, ses = _exit_setup(TODAY)
    jobs, _ = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=False, mode="intraday")
    assert len(jobs) == 1 and jobs[0].meta["reason"] == "expiry_day_sweep"


def test_sweep_mode_closes_only_spreads_expiring_today():
    fb, rec, ses = _exit_setup(TODAY)
    jobs, _ = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=False, mode="sweep")
    assert len(jobs) == 1 and jobs[0].cutoff_price is not None
    fb, rec, ses = _exit_setup(TODAY + dt.timedelta(days=1))
    jobs, _ = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=False, mode="sweep")
    assert not jobs


# ====================================================================== #8 close-all, #9 assignment
def test_close_all_uses_only_the_unmatched_quantity():
    e = dt.date(2026, 10, 30)
    pos = [{"symbol": S.occ_symbol("SPY", e, "P", 700), "qty": -2, "side": "short", "avg_entry_price": 2.0,
            "asset_class": "us_option"},
           {"symbol": S.occ_symbol("SPY", e, "P", 690), "qty": 3, "side": "long", "avg_entry_price": 0.8,
            "asset_class": "us_option"}]
    _, orph = S.group_spreads(pos)
    assert R.orphan_qty(orph[0]) == 1                                  # not the raw 3
    assert R.orphan_qty({"qty": 2, "side": "short"}) == -2


def test_assignment_is_detected_and_flattened_with_a_stock_order():
    pos = [{"symbol": "SPY", "qty": 100, "side": "long", "asset_class": "us_equity", "avg_entry_price": 700}]
    fb = FakeDataBroker({}, {}, [], pos)
    sent = []
    fb.submit_stock = lambda sym, qty, side, cid: sent.append((sym, qty, side, cid)) or {"id": "s1", "status": "new"}
    records, orphans, stocks, _ = R.load_book(fb, TODAY, persist=False)
    assert stocks and stocks[0]["symbol"] == "SPY" and not orphans
    msgs = R.flatten_stock(fb, stocks, TODAY)
    assert sent[0][:3] == ("SPY", 100, "sell") and sent[0][3].startswith("options-") and "flatten" in msgs[0]


# ====================================================================== kill switch freshness (strategy #4)
def _vix(monkeypatch, quotes, hist):
    monkeypatch.setattr(signals, "_delayed_quote", lambda sym: quotes.get(sym))
    monkeypatch.setattr(signals, "_history_last", lambda sym: hist.get(sym))


def test_kill_switch_blocks_entries_on_stale_or_mixed_vix(monkeypatch):
    now = dt.datetime.combine(TODAY, dt.time(15, 55), ET)
    fresh = {"VIX": (17.0, now - dt.timedelta(minutes=16)), "VIX3M": (19.0, now - dt.timedelta(minutes=16))}
    _vix(monkeypatch, fresh, {})
    v = signals.vix_levels(TODAY, now=now)
    assert v["fresh"] and not S.kill_switch(v["vix"], v["vix3m"], v["fresh"], v["stale_reason"])[0]
    old = {"VIX": (17.0, now - dt.timedelta(minutes=45)), "VIX3M": (19.0, now - dt.timedelta(minutes=16))}
    _vix(monkeypatch, old, {"VIX": (17.5, TODAY - dt.timedelta(days=1))})
    v = signals.vix_levels(TODAY, now=now)
    assert not v["fresh"]
    blocked, why = S.kill_switch(v["vix"], v["vix3m"], v["fresh"], v["stale_reason"])
    assert blocked and "not fresh" in why
    yday = {"VIX": (17.0, now - dt.timedelta(days=1)), "VIX3M": (19.0, now - dt.timedelta(days=1))}
    _vix(monkeypatch, yday, {})
    assert not signals.vix_levels(TODAY, now=now)["fresh"]


# ====================================================================== #5/#6 strategy: TP basis, freshness, sizing
def test_take_profit_is_measured_against_entry_mid():
    fb, rec, ses = _exit_setup(TODAY + dt.timedelta(days=9), short_q=(0.55, 0.57), long_q=(0.01, 0.03),
                               entry_credit=1.00, entry_mid=1.12)
    jobs, rows = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=False, mode="intraday")
    assert rows[0]["reason"] == "take_profit"                         # 0.54 <= 1.12 * 0.5
    rec[next(iter(rec))]["entry_mid"] = None                          # adopted spread: falls back to the fill
    jobs, rows = R.plan_exits(R.Ctx(fb, TODAY), rec, ses, end_of_day=False, mode="intraday")
    assert rows[0]["reason"] is None                                  # 0.54 > 1.00 * 0.5


def test_history_checks_fail_closed():
    fb, _ = _planner_setup(dip=True)
    h = fb.hist["SPY"]
    with pytest.raises(signals.DataError, match="daily bars"):
        signals.check_history("SPY", h.iloc[-50:], TODAY)
    with pytest.raises(signals.DataError, match="days old"):
        signals.check_history("SPY", h.iloc[:-10], TODAY)
    bad = h.copy()
    bad.iloc[-3, bad.columns.get_loc("close")] = float("nan")
    with pytest.raises(signals.DataError, match="missing"):
        signals.check_history("SPY", bad, TODAY)
    fb.hist["SPY"] = h.iloc[-50:]
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY), {}, {"equity": 10_000}, (False, "ok"), ["SPY"])
    assert not jobs and "data error" in rows[0]["action"]


def test_stale_live_price_and_option_quotes_fail_closed_in_strict_mode():
    fb, _ = _planner_setup(dip=True)
    now = dt.datetime.combine(TODAY, dt.time(15, 55), ET)
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY, strict=True, now=now), {}, {"equity": 10_000}, (False, "ok"),
                                ["SPY"])
    assert not jobs and "no timestamp" in rows[0]["action"]           # live trade has no time
    p = S.Pick("SPY", dt.date(2026, 10, 30), dataclasses.replace(Q(700, -0.25, 2.0, 2.03), ts=now - dt.timedelta(hours=1)),
               dataclasses.replace(Q(690, -0.10, 0.80, 0.82), ts=now), 9)
    assert "stale" in S.check_pick(p, now=now)
    p2 = dataclasses.replace(p, short=dataclasses.replace(p.short, ts=None))
    assert "no timestamp" in S.check_pick(p2, now=now)
    p3 = dataclasses.replace(p, short=dataclasses.replace(p.short, ts=now))
    assert S.check_pick(p3, now=now) is None


def test_position_is_sized_with_the_last_ladder_price(monkeypatch):
    fb, _ = _planner_setup(dip=True)
    seen = []
    real = S.size_position
    monkeypatch.setattr(R.S, "size_position", lambda eq, w, credit, used, bp=None: seen.append(credit) or real(eq, w, credit, used, bp))
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY), {}, {"equity": 10_000, "options_buying_power": None},
                                (False, "ok"), ["SPY"])
    assert jobs and seen == [jobs[0].prices[-1]]


# ====================================================================== #8 journaling
def test_vanished_spread_is_journaled_with_pnl_from_activities():
    e = dt.date(2026, 10, 16)
    ss, ls = S.occ_symbol("SPY", e, "P", 700), S.occ_symbol("SPY", e, "P", 690)
    sid = S.spread_id("SPY", e, 700, 690)
    state.save_positions({sid: {"underlying": "SPY", "expiry": e.isoformat(), "short_symbol": ss, "long_symbol": ls,
                                "short_strike": 700, "long_strike": 690, "qty": 2, "entry_date": "2026-10-09",
                                "entry_credit": 1.10}})
    fb = FakeDataBroker({}, {}, [], [])
    fb.activities = lambda types, after: [{"activity_type": "OPEXP", "symbol": ss, "qty": "2"},
                                          {"activity_type": "OPEXP", "symbol": ls, "qty": "2"}]
    records, *_ = R.load_book(fb, TODAY, persist=True)
    assert records == {}
    t = state.read_jsonl(state.TRADES)[-1]
    assert t["exit_reason"] == "external_expired" and t["pnl"] == pytest.approx(220.0)
    assert set(t) == set(R.TRADE_COLS)


def test_two_partial_closes_on_one_day_get_distinct_trade_ids():
    e = dt.date(2026, 10, 30)
    r = {"underlying": "SPY", "expiry": e.isoformat(), "short_symbol": "a", "long_symbol": "b", "short_strike": 700,
         "long_strike": 690, "qty": 4, "entry_date": "2026-10-19", "entry_credit": 1.0}
    recs = {"SPY-x": r}
    ids = []
    for oid in ("o7", "o9"):
        j = _job([0.5], qty=1, credit=False)
        j.meta = {"record": recs["SPY-x"], "reason": "take_profit"}
        j.order_ids, j.fills, j.status = [oid], {oid: (1.0, 0.5)}, "filled"
        ids += [t["id"] for t in R.apply_results([j], recs, TODAY, None, persist=False)]
    assert len(set(ids)) == 2 and recs["SPY-x"]["qty"] == 2


# ====================================================================== LOWs
def test_is_monthly_includes_thursday_expiries_like_the_engine():
    assert S.is_monthly(dt.date(2026, 6, 18))          # Thursday before Juneteenth (Fri 2026-06-19)
    assert S.is_monthly(dt.date(2026, 4, 2)) is False  # Thursday, 1st week
    assert S.is_monthly(dt.date(2026, 10, 16))
    r = S.rank_expiries([dt.date(2026, 6, 18), dt.date(2026, 6, 19)], dt.date(2026, 6, 8))
    assert r[0] == dt.date(2026, 6, 18)


def test_stop_mult_uses_research_semantics():
    kw = dict(entry_credit=1.0, close=100, sma5=101, held_days=1, trading_days_to_expiry=5, end_of_day=True)
    assert S.exit_reason(cost_to_close=1.9, stop_mult=1.0, **kw) is None
    assert S.exit_reason(cost_to_close=2.0, stop_mult=1.0, **kw) == "stop_loss"


def test_decision_time_is_a_config_value_at_1555():
    assert C.DECIDE_MINUTES_BEFORE_CLOSE == 5 and C.CUTOFF_MINUTES_BEFORE_CLOSE == 2
    steps = C.ENTRY_STEPS + 1
    assert steps * C.STEP_WAIT_SECONDS <= (C.DECIDE_MINUTES_BEFORE_CLOSE - C.CUTOFF_MINUTES_BEFORE_CLOSE) * 60
