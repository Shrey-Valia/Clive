"""Engineering round 3 (docs/ENGINEERING_ROUND3.md): host-death resilience, resting orders,
execution-quality journaling, the condor ex-dividend guard, alerts and heartbeat parity.

Fakes only: no network, no order endpoints.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import alerts, db, execution, resilience, state  # noqa: E402
from live import config as C  # noqa: E402
from live import run as R  # noqa: E402
from live import strategy as S  # noqa: E402
from live.broker import ET, SafetyError, is_resting_order  # noqa: E402
from test_condor import ChainFake, condor_positions, condor_record, leg_pos  # noqa: E402
from test_fixes import Book, _Resp, _broker, _work, isolated  # noqa: E402,F401
from test_live import TODAY, FakeClock, _planner_setup  # noqa: E402

SS, LS = "SPY261030P00700000", "SPY261030P00690000"
_REAL = {k: getattr(db, k) for k in ("heartbeat", "insert", "update")}     # before the isolated fixture fakes them


def dip_record(qty=2, entry="2026-10-19", mid=1.20, credit=1.18):
    return {"underlying": "SPY", "expiry": "2026-10-30", "short_symbol": SS, "long_symbol": LS,
            "short_strike": 700.0, "long_strike": 690.0, "qty": qty, "entry_date": entry,
            "entry_credit": credit, "entry_mid": mid}


SID = S.spread_id("SPY", dt.date(2026, 10, 30), 700, 690)


# ====================================================================== ex-dividend guard
def test_exdiv_rule_matches_alpaca_history_and_shifts_for_holidays():
    # Alpaca corporate actions (read 2026-10-23): SPY ex 2025-03-21, 06-20, 09-19, 12-19, 2026-03-20,
    # 2026-06-18 (Juneteenth Friday), 2026-09-18
    sessions = [dt.date(2026, 6, 17), dt.date(2026, 6, 18), dt.date(2026, 6, 22)]
    assert S.exdiv_dates("SPY", dt.date(2025, 1, 1), dt.date(2025, 12, 31)) == [
        dt.date(2025, 3, 21), dt.date(2025, 6, 20), dt.date(2025, 9, 19), dt.date(2025, 12, 19)]
    assert S.exdiv_dates("SPY", dt.date(2026, 6, 1), dt.date(2026, 6, 30), sessions) == [dt.date(2026, 6, 18)]
    assert S.exdiv_dates("SPY", dt.date(2026, 10, 23), dt.date(2026, 12, 31)) == [dt.date(2026, 12, 18)]
    # an announced date replaces the rule date of its month
    assert S.exdiv_dates("SPY", dt.date(2026, 12, 1), dt.date(2026, 12, 31),
                         known=[dt.date(2026, 12, 17)]) == [dt.date(2026, 12, 17)]
    assert S.exdiv_dates("IWM", dt.date(2026, 1, 1), dt.date(2026, 12, 31)) == []      # not modelled
    assert S.last_cum_div_session(dt.date(2026, 12, 18), [dt.date(2026, 12, 16), dt.date(2026, 12, 17)]) == \
        dt.date(2026, 12, 17)


def _call(k, bid, ask, exp=dt.date(2027, 1, 15)):
    return S.OptionQuote(S.occ_symbol("SPY", exp, "C", k), "SPY", exp, "C", k, bid, ask, 0.9)


def test_early_exercise_risk_rule():
    assert S.early_exercise_risk(740, _call(740, 1.0, 1.1), 730.0, 2.0)[0] is False          # OTM
    assert S.early_exercise_risk(740, _call(740, 40.4, 40.6), 780.0, 2.0)[0] is True         # extrinsic 0.5
    assert S.early_exercise_risk(740, _call(740, 43.0, 43.4), 780.0, 2.0)[0] is False        # extrinsic 3.2
    assert S.early_exercise_risk(740, None, 780.0, 2.0)[0] is True                           # fail safe


def _exdiv_ctx(now, spot=780.0, call_mid=40.5):
    exp = dt.date(2027, 1, 15)
    cid, r = condor_record((630, 650, 740, 760), exp=exp, entry="2026-11-20")
    chain = [S.OptionQuote(r["put_short_symbol"], "SPY", exp, "P", 650, 0.5, 0.6, -0.02),
             S.OptionQuote(r["put_long_symbol"], "SPY", exp, "P", 630, 0.2, 0.3, -0.01),
             _call(740, call_mid - 0.1, call_mid + 0.1, exp), _call(760, 21.9, 22.1, exp)]
    h = _planner_setup()[0]
    fb = ChainFake({"SPY": h.hist["SPY"]}, {"SPY": h.live["SPY"]}, chain)
    ctx = R.Ctx(fb, now.date(), now=now)
    ctx.signal = lambda sym: SimpleNamespace(close=spot)
    return ctx, {cid: r}


EXDIV = {"dates": [dt.date(2026, 12, 18)], "dividend": 2.0, "source": "test"}
SES = [dt.date(2026, 12, 16), dt.date(2026, 12, 17), dt.date(2026, 12, 18), dt.date(2026, 12, 21),
       dt.date(2027, 1, 14), dt.date(2027, 1, 15)]


def test_condor_closes_before_the_ex_date_when_the_short_call_would_be_exercised():
    ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 17, 15, 55, tzinfo=ET))
    jobs, rows = R.plan_condor_exits(ctx, condors, SES, mode="daily", exdiv=EXDIV)
    assert len(jobs) == 1 and jobs[0].meta["reason"] == "exdiv_exit" and "extrinsic" in rows[0]["exdiv"]
    assert jobs[0].cutoff_price is not None
    # out of the money / plenty of extrinsic: hold; the day before the last cum-div day: hold
    ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 17, 15, 55, tzinfo=ET), spot=730.0, call_mid=1.0)
    assert not R.plan_condor_exits(ctx, condors, SES, mode="daily", exdiv=EXDIV)[0]
    ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 16, 15, 55, tzinfo=ET))
    assert not R.plan_condor_exits(ctx, condors, SES, mode="daily", exdiv=EXDIV)[0]
    # flag off: research behaviour
    C_flag = C.CONDOR_EXDIV_GUARD
    try:
        C.CONDOR_EXDIV_GUARD = False
        ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 17, 15, 55, tzinfo=ET))
        assert not R.plan_condor_exits(ctx, condors, SES, mode="daily", exdiv=EXDIV)[0]
    finally:
        C.CONDOR_EXDIV_GUARD = C_flag


def test_exdiv_guard_runs_intraday_only_from_15_00_and_never_in_the_sweep():
    ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 17, 14, 45, tzinfo=ET))
    assert not R.plan_condor_exits(ctx, condors, SES, mode="intraday", exdiv=EXDIV)[0]
    ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 17, 15, 15, tzinfo=ET))
    jobs, _ = R.plan_condor_exits(ctx, condors, SES, mode="intraday", exdiv=EXDIV)
    assert [j.meta["reason"] for j in jobs] == ["exdiv_exit"]
    ctx, condors = _exdiv_ctx(dt.datetime(2026, 12, 17, 9, 45, tzinfo=ET))
    assert not R.plan_condor_exits(ctx, condors, SES, mode="sweep", exdiv=EXDIV)[0]


def test_exdiv_info_uses_alpaca_dividends_and_the_rule():
    class B:
        def cash_dividends(self, sym, start, end):
            return [{"ex_date": dt.date(2026, 9, 18), "rate": 2.4}]
    info = resilience.exdiv_info(B(), "SPY", dt.date(2026, 10, 23), dt.date(2026, 12, 31), [])
    assert info["dates"] == [dt.date(2026, 12, 18)] and info["dividend"] == 2.4
    info = resilience.exdiv_info(object(), "SPY", dt.date(2026, 10, 23), dt.date(2026, 12, 31), [])
    assert info["dividend"] == C.DIVIDEND_ESTIMATE["SPY"]


# ====================================================================== execution quality
def test_slippage_sign_and_take_profit_price():
    assert S.slippage(True, 1.20, 1.17) == pytest.approx(0.03)     # credit below mid: worse
    assert S.slippage(False, 0.50, 0.55) == pytest.approx(0.05)    # debit above mid: worse
    assert S.slippage(True, 1.20, 1.21) == pytest.approx(-0.01)
    assert S.slippage(True, None, 1.0) is None
    assert S.take_profit_debit(1.21) == 0.60 and S.take_profit_debit(0.01) == 0.01


def test_order_journal_records_mid_natural_and_slippage(isolated):
    j = execution.Job(SID, "close", execution.close_legs(SS, LS), 2, [0.50, 0.55], credit=False,
                      meta={"record": dip_record(), "reason": "take_profit", "mid": 0.50, "natural": 0.60})
    j.order_ids, j.fills, j.status = ["o1"], {"o1": (2.0, 0.53)}, "filled"
    records = {SID: dip_record()}
    R.apply_results([j], records, TODAY, None, persist=True)
    row = state.read_jsonl(state.ORDERS)[-1]
    assert row["mid_price"] == 0.50 and row["natural_price"] == 0.60
    assert row["slippage"] == pytest.approx(0.03) and row["slippage_usd"] == pytest.approx(6.0)
    assert row["reason"] == "take_profit" and SID not in records


def test_entry_jobs_carry_the_record_template():
    fb, _ = _planner_setup(dip=True)
    jobs, _ = R.plan_entries(R.Ctx(fb, TODAY), {}, {"equity": 10_000, "options_buying_power": 10_000},
                             (False, "ok"), ["SPY"])
    t = jobs[0].meta["template"]
    p = jobs[0].meta["pick"]
    assert t["entry_mid"] == p.mid_credit and t["short_symbol"] == p.short.symbol
    assert jobs[0].meta["mid"] == p.mid_credit and jobs[0].meta["natural"] == p.natural_credit
    json.dumps(t)                                                 # journal-safe


# ====================================================================== inflight journal / recovery
def _open_job():
    fb, _ = _planner_setup(dip=True)
    jobs, _ = R.plan_entries(R.Ctx(fb, TODAY), {}, {"equity": 10_000, "options_buying_power": 10_000},
                             (False, "ok"), ["SPY"])
    return jobs[0]


def test_inflight_journal_names_every_client_order_id_before_the_submit(isolated):
    b = Book()
    j = _open_job()
    seen_on_disk = []
    real = b.submit_mleg

    def submit(legs, qty, limit, cid):
        seen_on_disk.append(cid in state.load_inflight()["jobs"][0]["cids"])
        return real(legs, qty, limit, cid)
    b.submit_mleg = submit
    resilience.start_inflight([j], "daily", TODAY)
    _work(b, [j])
    assert seen_on_disk and all(seen_on_disk)                     # write-ahead
    assert state.load_inflight()["jobs"][0]["cids"] == j.cids


def test_dead_run_is_recovered_with_its_planned_metadata(isolated):
    """The host dies after the entry order was sent: the order fills at Alpaca while nobody books it.
    The next executing run looks it up by client_order_id and books it with the entry mid, the
    signal and the dead run's date, instead of a blind adoption."""
    b = Book()
    j = _open_job()
    resilience.start_inflight([j], "daily", TODAY)
    execution._submit(b, j, j.prices[0], FakeClock())             # ... and the host dies here
    o = b.orders["o1"]
    o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["limit_price"])
    assert state.load_positions() == {}
    msgs = R.recover_inflight(b)
    rec = state.load_positions()[j.key]
    assert rec["entry_mid"] == j.meta["pick"].mid_credit and rec["signal"]["rsi2"] == j.meta["signal"]["rsi2"]
    assert rec["entry_date"] == TODAY.isoformat() and rec["qty"] == j.qty
    assert rec["entry_credit"] == pytest.approx(j.prices[0]) and "adopted" not in rec
    assert state.load_inflight() == {} and msgs and "recovered" in msgs[0]
    assert alerts.open_alerts() == {}                             # an event alert, not a condition
    assert R.recover_inflight(b) == []                            # idempotent


def test_recovery_cancels_a_still_working_order_and_skips_what_was_booked(isolated):
    b = Book()
    j = _open_job()
    resilience.start_inflight([j], "intraday", TODAY)
    execution._submit(b, j, j.prices[0], FakeClock())
    R.recover_inflight(b)                                         # unfilled, still working at Alpaca
    assert b.orders["o1"]["status"] == "canceled" and state.load_positions() == {}
    # booked already by the dead run (normal record): the journal must not double it
    j2 = _open_job()
    state.save_positions({j2.key: {**j2.meta["template"], "qty": j2.qty, "entry_date": "2026-10-20",
                                   "entry_credit": 1.0}})
    resilience.start_inflight([j2], "daily", TODAY)
    execution._submit(b, j2, j2.prices[0], FakeClock())
    o = b.orders["o2"]
    o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["limit_price"])
    R.recover_inflight(b)
    assert state.load_positions()[j2.key]["qty"] == j2.qty and state.load_positions()[j2.key]["entry_credit"] == 1.0


def test_recovery_keeps_the_journal_when_alpaca_is_unreachable(isolated):
    b = Book()
    j = _open_job()
    resilience.start_inflight([j], "daily", TODAY)
    execution._submit(b, j, j.prices[0], FakeClock())

    def down(cid):
        raise RuntimeError("ConnectionError")
    b.get_order_by_client_id = down
    msgs = R.recover_inflight(b)
    assert "UNRESOLVED" in msgs[0] and state.load_inflight()


def test_settle_clears_the_journal(isolated):
    b = Book()
    b.on_get = lambda book, o: o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["limit_price"])
    j = _open_job()
    resilience.start_inflight([j], "daily", TODAY)
    _work(b, [j])
    b.cancel_all = lambda timeout=20: []
    b.execute = True
    records = {}
    R._settle(b, [j], records, TODAY, None)
    assert j.key in records and state.load_inflight() == {}


def test_truncated_journal_line_is_skipped(isolated):
    state.append(state.TRADES, {"id": "a", "pnl": 1.0})
    with open(C.STATE_DIR / state.TRADES, "a") as fh:
        fh.write('{"id": "b", "pn')                                # the host died mid-append
    assert [t["id"] for t in state.read_jsonl(state.TRADES)] == ["a"]


# ====================================================================== resting GTC orders
def test_gtc_needs_the_resting_prefix_and_is_sent_as_gtc(isolated):
    sent = []

    def handler(method, path, params, body):
        sent.append(body)
        return _Resp({"id": "x", "status": "new"})
    b, _ = _broker(handler)
    legs = execution.close_legs(SS, LS)
    with pytest.raises(SafetyError):
        b.submit_mleg(legs, 1, 0.6, "options-dip-close-x", time_in_force="gtc")
    with pytest.raises(SafetyError):
        b.submit_mleg(legs, 1, 0.6, C.REST_ORDER_PREFIX + "tp-x", time_in_force="ioc")
    b.submit_mleg(legs, 1, 0.6, C.REST_ORDER_PREFIX + "tp-x", time_in_force="gtc")
    assert sent[-1]["time_in_force"] == "gtc" and sent[-1]["order_class"] == "mleg" and sent[-1]["limit_price"] == "0.60"


def test_leftover_cancel_keeps_resting_orders_unless_asked(isolated):
    calls = []
    orders = [{"id": "a", "client_order_id": "options-dip-open-x", "status": "new"},
              {"id": "b", "client_order_id": C.REST_ORDER_PREFIX + "tp-x", "status": "new"}]

    def handler(method, path, params, body):
        if method == "GET" and path == "/v2/orders":
            return _Resp([o for o in orders if o["status"] == "new"])
        if method == "DELETE":
            oid = path.rsplit("/", 1)[1]
            calls.append(oid)
            next(o for o in orders if o["id"] == oid)["status"] = "canceled"
            return _Resp(None, 204)
        return _Resp({})
    b, _ = _broker(handler)
    assert b.cancel_all(timeout=1) == [] and calls == ["a"]
    assert is_resting_order(orders[1]) and orders[1]["status"] == "new"
    assert b.cancel_all(timeout=1, include_resting=True) == [] and calls == ["a", "b"]


class RestBook(Book):
    def submit_mleg(self, legs, qty, limit, cid, time_in_force="day"):
        o = super().submit_mleg(legs, qty, limit, cid)
        self.orders[o["id"]]["tif"] = time_in_force
        return dict(self.orders[o["id"]])


def test_take_profit_is_staged_only_after_the_entry_day_and_not_near_expiry(isolated, monkeypatch):
    monkeypatch.setattr(C, "STAGE_TP_ORDERS", True)
    b = RestBook()
    ses = [dt.date(2026, 10, d) for d in (19, 20, 21, 22, 23, 26, 27, 28, 29, 30)]
    records = {SID: dip_record(mid=1.21), "SPY-new": {**dip_record(entry=TODAY.isoformat()),
                                                    "short_symbol": "SPY261030P00705000"}}
    msgs = resilience.stage_resting(b, records, {}, TODAY, ses)
    assert len(b.orders) == 1 and msgs == [f"resting TP {SID} 2x @ 0.60"]
    o = b.orders["o1"]
    assert o["tif"] == "gtc" and o["client_order_id"].startswith(C.REST_ORDER_PREFIX + "tp-")
    assert float(o["limit_price"]) == 0.60 and [l["position_intent"] for l in o["legs"]] == \
        ["buy_to_close", "sell_to_close"]
    assert state.load_resting()[SID]["order_id"] == "o1"
    assert resilience.stage_resting(b, records, {}, TODAY, ses) == [] and len(b.orders) == 1    # idempotent
    # one session to expiry: the daily expiry exit owns it
    state.save_resting({})
    assert resilience.stage_resting(b, records, {}, dt.date(2026, 10, 29), ses) == []


def test_a_resting_take_profit_that_filled_is_booked_before_the_book_is_read(isolated, monkeypatch):
    monkeypatch.setattr(C, "STAGE_TP_ORDERS", True)
    b = RestBook()
    state.save_positions({SID: dip_record(mid=1.21)})
    resilience.stage_resting(b, state.load_positions(), {}, TODAY,
                             [dt.date(2026, 10, d) for d in (21, 22, 23, 26, 27, 28, 29, 30)])
    b.orders["o1"].update(status="filled", filled_qty="2", filled_avg_price="0.60")     # while the host was down
    stuck, warnings = R.prepare_resting(b, TODAY)
    assert not stuck and any("filled" in w for w in warnings)
    assert SID not in state.load_positions()
    t = state.read_jsonl(state.TRADES)[-1]
    assert t["exit_reason"] == "tp_resting" and t["exit_debit"] == 0.60
    assert t["pnl"] == pytest.approx((1.18 - 0.60) * 200)
    assert state.load_resting() == {}


def test_open_resting_orders_are_canceled_and_confirmed_before_trading(isolated, monkeypatch):
    monkeypatch.setattr(C, "STAGE_TP_ORDERS", True)
    b = RestBook()
    state.save_positions({SID: dip_record(mid=1.21)})
    resilience.stage_resting(b, state.load_positions(), {}, TODAY,
                             [dt.date(2026, 10, d) for d in (21, 22, 23, 26, 27, 28, 29, 30)])
    stuck, _ = R.prepare_resting(b, TODAY)
    assert not stuck and b.orders["o1"]["status"] == "canceled" and SID in state.load_positions()
    # a cancel that never confirms: that spread is not traded this run, and an alert is raised
    resilience.stage_resting(b, state.load_positions(), {}, TODAY,
                             [dt.date(2026, 10, d) for d in (21, 22, 23, 26, 27, 28, 29, 30)])
    b.cancel_order = lambda oid: None
    _settled, stuck, problems = resilience.cancel_resting(b, clock=FakeClock(), polls=2)
    assert SID in stuck and "not confirmed canceled" in problems[0] and SID in state.load_resting()


def test_unfilled_expiry_exit_rests_overnight_when_enabled(isolated, monkeypatch):
    monkeypatch.setattr(C, "REST_UNFILLED_EXPIRY_EXITS", True)
    b = RestBook()
    records = {SID: dip_record()}
    j = execution.Job(SID, "close", execution.close_legs(SS, LS), 2, [0.3, 0.4], credit=False, cutoff_price=0.45,
                      meta={"reason": "expiry_exit", "record": records[SID]})
    j.fills = {"x": (1.0, 0.4)}
    resilience.stage_resting(b, {SID: {**records[SID], "qty": 1}}, {}, TODAY, [], [j])
    o = b.orders["o1"]
    assert o["tif"] == "gtc" and o["qty"] == "1" and float(o["limit_price"]) == 0.45
    assert o["client_order_id"].startswith(C.REST_ORDER_PREFIX + "expiry-")


def test_flags_off_means_no_resting_calls(isolated):
    assert not resilience.resting_in_use()
    assert R.prepare_resting(object(), TODAY) == (set(), [])
    assert R.stage_resting_after(object(), {SID: dip_record()}, {}, TODAY, []) == []


# ====================================================================== dip expiry-day deadline (flag)
def test_dip_expiry_day_deadline_flag(monkeypatch):
    exp = TODAY
    ss, ls = S.occ_symbol("SPY", exp, "P", 700), S.occ_symbol("SPY", exp, "P", 690)
    rec = {S.spread_id("SPY", exp, 700, 690): {**dip_record(), "expiry": exp.isoformat(), "short_symbol": ss,
                                               "long_symbol": ls}}
    fb, _ = _planner_setup()
    fb.chain = fb.chain + [S.OptionQuote(ss, "SPY", exp, "P", 700, 0.3, 0.35, -0.05),
                           S.OptionQuote(ls, "SPY", exp, "P", 690, 0.0, 0.05, -0.01)]
    late = dt.datetime.combine(TODAY, dt.time(15, 10), ET)
    ddl = R.condor_deadline(TODAY)
    jobs, rows = R.plan_exits(R.Ctx(fb, TODAY, now=late), rec, [TODAY], end_of_day=False, mode="intraday",
                              deadline=ddl)
    assert len(jobs) == 1                                         # default: unchanged
    monkeypatch.setattr(C, "DIP_EXPIRY_DAY_DEADLINE_ENABLED", True)
    ctx = R.Ctx(fb, TODAY, now=late)
    jobs, rows = R.plan_exits(ctx, rec, [TODAY], end_of_day=False, mode="intraday", deadline=ddl)
    assert not jobs and rows[0]["blocked"] and any("by hand" in w for w in ctx.warnings)
    early = dt.datetime.combine(TODAY, dt.time(9, 45), ET)
    assert len(R.plan_exits(R.Ctx(fb, TODAY, now=early), rec, [TODAY], end_of_day=False, mode="sweep",
                            deadline=ddl)[0]) == 1


# ====================================================================== alerts, heartbeat, db
def test_alerts_dedupe_resolve_and_push(isolated, monkeypatch):
    monkeypatch.setattr(C, "ALERTS_ENABLED", True)
    monkeypatch.setattr(C, "ALERTS_PUSH", True)
    rows, updates, pushes = [], [], []
    monkeypatch.setattr(db, "insert", lambda table, row, **k: rows.append((table, row)) or [{"id": len(rows)}])
    monkeypatch.setattr(db, "update", lambda table, match, values: updates.append((table, match, values)))
    monkeypatch.setattr(alerts.requests, "post", lambda url, **k: pushes.append((url, k["headers"]["Title"])))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    isolated.tmp.joinpath("shorting.env").write_text("APCA_API_KEY_ID=x\nNTFY_TOPIC=clive-topic\n")
    assert alerts.raise_alert("assignment", "stock SPY", ticker="SPY", level="critical")
    assert not alerts.raise_alert("assignment", "stock SPY", ticker="SPY", level="critical")     # deduped
    assert rows[0][0] == "alerts" and rows[0][1]["bot_id"] == "options" and rows[0][1]["level"] == "critical"
    assert pushes == [("https://ntfy.sh/clive-topic", "Options bot: assignment SPY")]
    alerts.sync_condition("assignment", {})                                              # resolved
    assert updates[-1][1] == {"id": 1} and "resolved_at" in updates[-1][2] and alerts.open_alerts() == {}
    alerts.raise_alert("run_failed", "x", ticker="daily")
    alerts.raise_alert("run_failed", "y", ticker="sweep")
    assert alerts.resolve_one("run_failed", "daily")
    assert list(alerts.open_alerts()) == ["run_failed|sweep"]
    monkeypatch.setenv("NTFY_TOPIC", "")                                                 # explicit off wins
    assert C.ntfy_settings()[0] == "" and not alerts.push("t", "m", "info")


def test_dry_runs_raise_no_alerts(isolated, monkeypatch):
    from test_fixes import _daily_fake, _patch_daily
    now = dt.datetime.combine(TODAY, dt.time(15, 55, 10), ET)
    fake = _daily_fake(now, [leg_pos("SPY", 100)])                # stock left by an assignment
    _patch_daily(monkeypatch, fake, now)
    assert R.main(["--now", "--dry-run"]) == 0
    assert alerts.open_alerts() == {} and not db.enabled()
    assert R.main(["--dry-run", "--execute"]) == 2


def test_executing_run_raises_and_then_clears_condition_alerts(isolated, monkeypatch):
    from test_fixes import _daily_fake, _patch_daily
    monkeypatch.setattr(C, "ALERTS_ENABLED", True)
    now = dt.datetime.combine(TODAY, dt.time(15, 55, 10), ET)
    fake = _daily_fake(now, [leg_pos("SPY", 100)])
    fake.submit_stock = lambda *a, **k: {"id": "s1", "status": "accepted"}
    _patch_daily(monkeypatch, fake, now)
    monkeypatch.setattr(R.execution, "work_orders", lambda broker, jobs, **kw: jobs)
    assert R.main(["--now", "--execute"]) == 0
    assert "assignment|SPY" in alerts.open_alerts()
    fake._positions = []
    assert R.main(["--now", "--execute", "--force"]) == 0
    assert "assignment|SPY" not in alerts.open_alerts()


def test_run_failure_alert_is_resolved_by_the_next_good_run(isolated, monkeypatch):
    monkeypatch.setattr(C, "ALERTS_ENABLED", True)

    def boom(args, kind="intraday"):
        raise RuntimeError("NameResolutionError")
    monkeypatch.setattr(R, "run_intraday", boom)
    assert R.main(["--intraday", "--execute"]) == 1
    assert "run_failed|intraday" in alerts.open_alerts()
    monkeypatch.setattr(R, "run_intraday", lambda args, kind="intraday": 0)
    assert R.main(["--intraday", "--execute"]) == 0
    assert alerts.open_alerts() == {}


def test_heartbeat_split_keeps_a_run_error_through_an_ok_sync(monkeypatch):
    monkeypatch.setattr(db, "heartbeat", _REAL["heartbeat"])
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "select", lambda table, params: [{"last_run_status": "error"}])
    seen = []
    monkeypatch.setattr(db, "update", lambda table, match, values: seen.append(values))
    db.heartbeat("ok", "synced", source="sync")
    assert seen[-1]["run_state"] == "error" and seen[-1]["last_sync_status"] == "ok"
    monkeypatch.setattr(db, "select", lambda table, params: None)                       # before migration 004
    db.heartbeat("ok", "synced", source="sync")
    assert "run_state" not in seen[-1]
    db.heartbeat("warning", "run", source="run")
    assert seen[-1]["run_state"] == "warning"


def test_db_drops_a_missing_column_and_retries(monkeypatch):
    monkeypatch.setattr(db, "insert", _REAL["insert"])
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "_env", lambda: {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "k"})
    bodies = []

    class R_:
        def __init__(self, code, text):
            self.status_code, self.text = code, text

        def json(self):
            return [{"id": "o1"}]

    def req(method, url, params=None, headers=None, data=None, timeout=None):
        bodies.append(json.loads(data))
        if "slippage" in bodies[-1][0]:
            return R_(400, "Could not find the 'slippage' column of 'options_live_orders' in the schema cache")
        return R_(201, "[]")
    monkeypatch.setattr(db.requests, "request", req)
    assert db.insert("options_live_orders", {"id": "o1", "slippage": 0.02}) == [{"id": "o1"}]
    assert bodies[-1] == [{"id": "o1"}]


def test_disable_turns_db_off(monkeypatch):
    monkeypatch.setattr(db, "_env", lambda: {"SUPABASE_URL": "u", "SUPABASE_SERVICE_KEY": "k"})
    assert db.enabled()
    db.disable()
    assert not db.enabled()


def test_condor_preview_lists_the_ex_dates_inside_its_life():
    from test_condor import daily_fake
    fb = daily_fake()
    ctx = R.Ctx(fb, TODAY, now=dt.datetime.combine(TODAY, dt.time(15, 55, 10), ET))
    jobs, row = R.plan_condor_entry(ctx, {}, {"equity": 15_000, "options_buying_power": 15_000}, (False, "ok"),
                                    used=0.0, bp=15_000)
    assert row.get("pick") is not None and row["exdiv"] == [dt.date(2026, 12, 18)]
    p = row["pick"]
    # $15k: the condor max loss stays under 20% of equity and inside the options buying power
    n = row["size"]
    assert n >= 1 and S.max_loss(p.width, row["ladder"][-1], n) <= 0.20 * 15_000 and p.width * 100 * n <= 15_000
