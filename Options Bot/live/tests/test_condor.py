"""Tests for the SPY iron condor sleeve (spreads finalist F1) and its isolation from the dip sleeve.

Fakes only: no network, no order endpoints.

Run:  python3 -m pytest -q live/tests/
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import config as C  # noqa: E402
from live import execution, signals, state  # noqa: E402
from live import strategy as S  # noqa: E402
from live import run as R  # noqa: E402
from live.broker import ET, SafetyError  # noqa: E402
from test_fixes import ENV, Book, _Resp, _broker, _daily_fake, _patch_daily, _work, isolated  # noqa: E402,F401
from test_live import TODAY, FakeClock, FakeDataBroker, Q, _planner_setup  # noqa: E402

CEXP = dt.date(2026, 12, 18)            # monthly, 58 DTE from TODAY (2026-10-21)
NOW = dt.datetime.combine(TODAY, dt.time(15, 55, 10), ET)


# ====================================================================== fakes
def condor_chain(spot=700.0, exp=CEXP, ts=None, und="SPY"):
    """Puts and calls every $5 with a smooth 58 DTE delta curve and 4-cent markets."""
    out = []
    for k in range(int(spot * 0.75) // 5 * 5, int(spot * 1.25), 5):
        m = (k - spot) / spot
        if k <= spot:
            d = -0.5 * math.exp(m / 0.045)
            px = max(0.10, 40.0 * abs(d) ** 1.3)
            out.append(S.OptionQuote(S.occ_symbol(und, exp, "P", k), und, exp, "P", float(k),
                                     round(px - 0.02, 2), round(px + 0.02, 2), round(d, 4), None, ts))
        if k >= spot:
            d = 0.5 * math.exp(-m / 0.035)
            px = max(0.10, 30.0 * d ** 1.3)
            out.append(S.OptionQuote(S.occ_symbol(und, exp, "C", k), und, exp, "C", float(k),
                                     round(px - 0.02, 2), round(px + 0.02, 2), round(d, 4), None, ts))
    return out


def leg_pos(sym, qty, avg=1.0):
    return {"symbol": sym, "qty": abs(qty), "side": "short" if qty < 0 else "long", "avg_entry_price": avg,
            "asset_class": "us_option"}


def condor_record(pick_or_strikes=(630, 650, 740, 760), exp=CEXP, qty=1, credit=2.0, entry="2026-10-14"):
    pl, ps, cs, cl = pick_or_strikes
    r = {"sleeve": "condor", "underlying": "SPY", "expiry": exp.isoformat(),
         "put_short_symbol": S.occ_symbol("SPY", exp, "P", ps), "put_long_symbol": S.occ_symbol("SPY", exp, "P", pl),
         "call_short_symbol": S.occ_symbol("SPY", exp, "C", cs), "call_long_symbol": S.occ_symbol("SPY", exp, "C", cl),
         "put_short_strike": ps, "put_long_strike": pl, "call_short_strike": cs, "call_long_strike": cl,
         "qty": qty, "entry_date": entry, "entry_credit": credit, "entry_mid": credit}
    return S.condor_id("SPY", exp, pl, ps, cs, cl), r


def condor_positions(r, qty=None):
    q = int(qty if qty is not None else r["qty"])
    return [leg_pos(r["put_short_symbol"], -q, 3.0), leg_pos(r["put_long_symbol"], q, 2.0),
            leg_pos(r["call_short_symbol"], -q, 1.5), leg_pos(r["call_long_symbol"], q, 0.5)]


class ChainFake(FakeDataBroker):
    """FakeDataBroker whose option_chain honours cp (the base fake returns everything)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.chain_calls = []

    def option_chain(self, sym, lo, hi, cp="P"):
        self.chain_calls.append((sym, cp))
        return [q for q in self.chain if q.underlying == sym and q.cp == cp and lo <= q.expiry <= hi]


def daily_fake(now=NOW, positions=None, extra_chain=(), equity=10_000.0):
    """test_fixes._daily_fake with the condor chain added and cp honoured."""
    base = _daily_fake(now, positions, extra_chain=list(extra_chain))
    spot = round(base.live["SPY"]["close"])
    base.chain = base.chain + condor_chain(spot=spot, ts=now)
    base.chain_calls = []
    orig = base.option_chain

    def option_chain(sym, lo, hi, cp="P"):
        base.chain_calls.append((sym, cp))
        return [q for q in orig(sym, lo, hi, cp) if q.cp == cp]
    base.option_chain = option_chain
    base.account = lambda: {"equity": equity, "options_buying_power": equity, "options_trading_level": 3,
                            "trading_blocked": False}
    return base


def capture(monkeypatch, fill=False):
    seen = []

    def work(broker, jobs, **kw):
        seen.extend(jobs)
        if fill:
            for j in jobs:
                j.order_ids.append(f"o-{j.key}")
                j.fills[f"o-{j.key}"] = (float(j.qty), j.prices[0])
                j.status = "filled"
        return jobs
    monkeypatch.setattr(R.execution, "work_orders", work)
    return seen


# ====================================================================== regression: flag off == old dip plan
# Generated from the dip-only code before the condor sleeve existed (commit 8ac1e3f's live/), with the
# scenario below: a QQQ dip spread at its expiry exit plus a fresh SPY dip signal.
GOLDEN_DIP_PLAN = json.loads("""
[["QQQ-20261022-P400/390", "close", [{"symbol": "QQQ261022P00400000", "side": "buy", "position_intent": "buy_to_close", "ratio_qty": 1}, {"symbol": "QQQ261022P00390000", "side": "sell", "position_intent": "sell_to_close", "ratio_qty": 1}], 2, [0.3, 0.32, 0.33, 0.34, 0.35], false, 0.4],
 ["SPY-20261030-P683/668", "open", [{"symbol": "SPY261030P00683000", "side": "sell", "position_intent": "sell_to_open", "ratio_qty": 1}, {"symbol": "SPY261030P00668000", "side": "buy", "position_intent": "buy_to_open", "ratio_qty": 1}], 1, [1.38, 1.37, 1.36], true, null]]
""")


def _dip_scenario(monkeypatch, with_condor_chain=False):
    exp = TODAY + dt.timedelta(days=1)
    ss, ls = S.occ_symbol("QQQ", exp, "P", 400), S.occ_symbol("QQQ", exp, "P", 390)
    pos = [leg_pos(ss, -2, 1.0), leg_pos(ls, 2, 0.2)]
    extra = [Q(400, -0.05, 0.30, 0.35, exp=exp, und="QQQ"), Q(390, -0.01, 0.0, 0.05, exp=exp, und="QQQ")]
    fake = daily_fake(NOW, pos, extra) if with_condor_chain else _daily_fake(NOW, pos, extra_chain=extra)
    _patch_daily(monkeypatch, fake, NOW)
    state.save_positions({S.spread_id("QQQ", exp, 400, 390): {
        "underlying": "QQQ", "expiry": exp.isoformat(), "short_symbol": ss, "long_symbol": ls, "short_strike": 400,
        "long_strike": 390, "qty": 2, "entry_date": "2026-10-19", "entry_credit": 0.8, "entry_mid": 0.8}})
    return fake


def _plan(jobs):
    return [[j.key, j.purpose, j.legs, j.qty, j.prices, j.credit, j.cutoff_price] for j in jobs]


def test_flag_off_dip_order_plan_is_identical_to_the_pre_condor_code(monkeypatch, isolated):
    assert C.CONDOR_ENABLED is False                                   # shipped default
    fake = _dip_scenario(monkeypatch, with_condor_chain=True)          # condor-eligible chain is there, flag is off
    seen = capture(monkeypatch)
    assert R.main(["--execute", "--now"]) == 0
    assert json.loads(json.dumps(_plan(seen))) == GOLDEN_DIP_PLAN
    assert all(j.sleeve == "dip" for j in seen)
    assert not any(cp == "C" for _, cp in fake.chain_calls)            # the condor sleeve never even looked
    assert not (C.STATE_DIR / state.CONDORS).exists()
    assert not any("condor" in m for _, m in isolated.beats)


def test_flag_off_preview_dry_run_is_identical_too(monkeypatch, isolated):
    _dip_scenario(monkeypatch)
    seen = capture(monkeypatch)
    jobs = {}
    orig = R.plan_entries

    def spy(*a, **k):
        out = orig(*a, **k)
        jobs["entries"] = out[0]
        return out
    monkeypatch.setattr(R, "plan_entries", spy)
    assert R.main(["--now", "--preview"]) == 0                         # dry run: nothing worked
    assert not seen
    assert json.loads(json.dumps(_plan(jobs["entries"]))) == GOLDEN_DIP_PLAN[1:]


def test_dip_client_order_ids_carry_the_dip_prefix_and_condor_ids_the_condor_prefix():
    bk = Book()
    dip = execution.Job("SPY-x", "open", execution.open_legs("A", "B"), 1, [1.22], credit=True)
    con = execution.Job("SPY-20261218-IC630/650/740/760", "open", execution.condor_open_legs("A", "B", "C", "D"),
                        1, [2.0], credit=True, sleeve=C.SLEEVE_CONDOR)
    _work(bk, [dip, con])
    assert dip.cids[0].startswith("options-dip-open-") and con.cids[0].startswith("options-condor-open-")
    assert all(len(c) <= 128 for c in dip.cids + con.cids)


# ====================================================================== picking and guards
def test_pick_condor_strikes_deltas_wings_and_dte():
    spot = 700.0
    p, why = S.pick_condor(condor_chain(spot), "SPY", TODAY, spot)
    assert p is not None, why
    assert p.expiry == CEXP and C.CONDOR_DTE_MIN <= p.dte <= C.CONDOR_DTE_MAX
    assert abs(abs(p.put_short.delta) - 0.10) <= C.CONDOR_DELTA_TOLERANCE
    assert abs(p.call_short.delta - 0.10) <= C.CONDOR_DELTA_TOLERANCE
    assert p.put_long.strike < p.put_short.strike < spot < p.call_short.strike < p.call_long.strike
    wing = 0.03 * spot                                                  # 21 -> nearest $5 strike, $20 wings
    assert abs(p.put_short.strike - wing - p.put_long.strike) <= 2.5
    assert abs(p.call_short.strike + wing - p.call_long.strike) <= 2.5
    assert {q.expiry for q in p.legs} == {CEXP}
    assert p.width == max(p.put_width, p.call_width)
    assert p.mid_credit == pytest.approx(sum(q.mid * s for q, s in zip(p.legs, (1, -1, 1, -1))), abs=1e-9)
    assert S.check_condor(p) is None


def test_pick_condor_needs_the_50_70_dte_window_and_a_spot():
    near = condor_chain(exp=TODAY + dt.timedelta(days=30))
    assert S.pick_condor(near, "SPY", TODAY, 700.0)[0] is None
    assert S.pick_condor(condor_chain(), "SPY", TODAY, None)[0] is None


def _pick():
    return S.pick_condor(condor_chain(), "SPY", TODAY, 700.0)[0]


def test_condor_credit_guards():
    p = _pick()
    rich = dataclasses.replace(p, put_short=dataclasses.replace(p.put_short, bid=14.0, ask=14.04))
    assert "bad quote guard" in S.check_condor(rich)                    # credit > 60% of width
    px = dict(zip(("put_short", "put_long", "call_short", "call_long"), (1.00, 0.98, 0.50, 0.45)))
    cheap = dataclasses.replace(p, **{k: dataclasses.replace(getattr(p, k), bid=v, ask=v) for k, v in px.items()})
    assert "<= $0.10" in S.check_condor(cheap)                          # credit 0.07: research needs > $0.10
    lad = S.condor_entry_ladder(p)
    assert lad[0] == pytest.approx(math.floor(p.mid_credit * 100) / 100) and lad == sorted(lad, reverse=True)
    assert lad[-1] >= p.mid_credit - 0.5 * (p.mid_credit - p.natural_credit) - 0.011


def test_condor_stale_or_untimestamped_quotes_fail_closed():
    p = _pick()
    assert "no timestamp" in S.check_condor(p, now=NOW)
    fresh = S.pick_condor(condor_chain(ts=NOW - dt.timedelta(minutes=1)), "SPY", TODAY, 700.0)[0]
    assert S.check_condor(fresh, now=NOW) is None
    old = S.pick_condor(condor_chain(ts=NOW - dt.timedelta(minutes=30)), "SPY", TODAY, 700.0)[0]
    assert "stale" in S.check_condor(old, now=NOW)


# ====================================================================== 4-leg mleg conventions
def test_condor_legs_follow_the_mleg_conventions_and_submit_as_one_4_leg_order():
    p = _pick()
    o = execution.condor_open_legs(*(q.symbol for q in p.legs))
    c = execution.condor_close_legs(*(q.symbol for q in p.legs))
    assert [(l["side"], l["position_intent"], l["ratio_qty"]) for l in o] == [
        ("sell", "sell_to_open", 1), ("buy", "buy_to_open", 1), ("sell", "sell_to_open", 1), ("buy", "buy_to_open", 1)]
    assert [(l["side"], l["position_intent"]) for l in c] == [
        ("buy", "buy_to_close"), ("sell", "sell_to_close"), ("buy", "buy_to_close"), ("sell", "sell_to_close")]
    jo = execution.Job(p.id, "open", o, 1, [2.0], credit=True, sleeve="condor")
    jc = execution.Job(p.id, "close", c, 1, [1.0], credit=False, sleeve="condor")
    assert jo.signed(2.0) == -2.0 and jc.signed(1.0) == 1.0           # credit negative, debit positive
    bodies = []

    def h(method, path, params, body):
        if method == "POST":
            bodies.append(body)
            return _Resp({"id": "n1", "status": "new"})
        raise AssertionError(path)
    b, _ = _broker(h)                                                   # scripted session: nothing leaves the test
    b.submit_mleg(o, 1, jo.signed(2.0), "options-condor-open-x")
    assert bodies[0]["order_class"] == "mleg" and bodies[0]["limit_price"] == "-2.00" and len(bodies[0]["legs"]) == 4
    assert [l["ratio_qty"] for l in bodies[0]["legs"]] == ["1"] * 4
    with pytest.raises(SafetyError, match="at most 4"):
        b.submit_mleg(o + o[:1], 1, -2.0, "options-condor-open-y")
    with pytest.raises(SafetyError, match="repeats"):
        b.submit_mleg(o[:3] + o[:1], 1, -2.0, "options-condor-open-z")
    with pytest.raises(SafetyError, match="invalid"):
        b.submit_mleg([{**o[0], "position_intent": "buy_to_open"}] + o[1:], 1, -2.0, "options-condor-open-w")
    assert len(bodies) == 1


def test_condor_job_fills_through_the_order_lifecycle():
    p = _pick()
    bk = Book()
    clk = FakeClock()
    j = execution.Job(p.id, "open", execution.condor_open_legs(*(q.symbol for q in p.legs)), 1,
                      S.condor_entry_ladder(p), credit=True, sleeve="condor")

    def fill_at_step_2(book, o):
        if o["status"] == "new" and abs(float(o["limit_price"])) <= j.prices[2] + 1e-9:
            o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["limit_price"])
    bk.on_get = fill_at_step_2
    execution.work_orders(bk, [j], cutoff=clk.now() + dt.timedelta(minutes=4), clock=clk, step_wait=30)
    assert j.status == "filled" and j.avg_price == pytest.approx(j.prices[2])
    assert len(bk.orders["o1"]["legs"]) == 4 and not bk.open_orders()


# ====================================================================== grouping by sleeve
def test_group_book_without_condors_is_group_spreads():
    e = dt.date(2026, 10, 30)
    pos = [leg_pos(S.occ_symbol("SPY", e, "P", 700), -1, 2.0), leg_pos(S.occ_symbol("SPY", e, "P", 690), 2, 0.8)]
    a = S.group_spreads(pos)
    sp, co, orph = S.group_book(pos, {}, {})
    assert (sp, orph) == a and co == []


def test_group_book_never_mixes_condor_legs_into_a_dip_spread_on_the_same_expiry():
    cid, r = condor_record((630, 650, 740, 760))
    dip_s, dip_l = S.occ_symbol("SPY", CEXP, "P", 645), S.occ_symbol("SPY", CEXP, "P", 635)
    dip_id = S.spread_id("SPY", CEXP, 645, 635)
    dip_rec = {dip_id: {"underlying": "SPY", "short_symbol": dip_s, "long_symbol": dip_l, "short_strike": 645,
                        "long_strike": 635, "qty": 1}}
    pos = condor_positions(r) + [leg_pos(dip_s, -1, 1.0), leg_pos(dip_l, 1, 0.5)]
    # naive dip grouping pairs the condor's 650 short with the dip's 635 long: the hazard
    naive, _ = S.group_spreads(pos)
    assert any(s.short_strike == 650 and s.long_strike == 635 for s in naive)
    for stored in ({cid: r}, {}):                                       # tracked, then untracked (adoption)
        sp, co, orph = S.group_book(pos, stored, dip_rec)
        assert [c.id for c in co] == [cid] and co[0].qty == 1, stored
        assert [s.id for s in sp] == [dip_id] and not orph, stored
        assert co[0].broker_credit == pytest.approx(3.0 - 2.0 + 1.5 - 0.5)


def test_partial_condor_legs_are_condor_orphans_never_dip_spreads():
    cid, r = condor_record()
    pos = condor_positions(r)[:3]                                       # call wing closed by hand
    sp, co, orph = S.group_book(pos, {cid: r}, {})
    assert not sp and not co
    assert len(orph) == 3 and all(o["sleeve"] == "condor" for o in orph)
    assert sorted(R.orphan_qty(o) for o in orph) == [-1, -1, 1]


def test_load_book_splits_sleeves_and_adopts_an_untracked_condor(monkeypatch):
    cid, r = condor_record()
    fb = FakeDataBroker({}, {}, [], condor_positions(r, qty=2))
    fb.fills = lambda after: []
    condors = {}
    records, orphans, stocks, _ = R.load_book(fb, TODAY, persist=False, condors_out=condors)
    assert records == {} and not orphans and list(condors) == [cid] and condors[cid]["qty"] == 2
    assert condors[cid]["entry_credit"] == pytest.approx(2.0) and condors[cid]["sleeve"] == "condor"


def test_vanished_condor_is_journaled_with_pnl_from_activities():
    cid, r = condor_record(credit=2.0)
    state.save_condors({cid: r})
    fb = FakeDataBroker({}, {}, [], [])
    fb.activities = lambda types, after: [{"activity_type": "OPEXP", "symbol": s} for s in S.condor_leg_symbols(r)]
    condors = {}
    R.load_book(fb, TODAY, persist=True, condors_out=condors)
    t = state.read_jsonl(state.TRADES)[-1]
    assert condors == {} and state.load_condors() == {}
    assert t["sleeve"] == "condor" and t["exit_reason"] == "external_expired" and t["pnl"] == pytest.approx(200.0)
    assert t["call_short_strike"] == 740 and t["short_strike"] == 650


# ====================================================================== exits: held to expiry, closed before it
def _exit_ctx(today, now, quotes=True):
    cid, r = condor_record(exp=today if today == CEXP else CEXP)
    chain = condor_chain(exp=CEXP, ts=now) if quotes else []
    fb = ChainFake({"SPY": _planner_setup()[0].hist["SPY"]}, {"SPY": _planner_setup()[0].live["SPY"]}, chain)
    ctx = R.Ctx(fb, today, now=now)
    return ctx, {cid: r}


def test_condor_exit_rules():
    kw = dict(expiry=CEXP, today=TODAY)
    assert S.condor_exit_reason(trading_days_to_expiry=1, mode="daily", **kw) == "expiry_exit"
    assert S.condor_exit_reason(trading_days_to_expiry=2, mode="daily", **kw) is None       # no TP / stop
    assert S.condor_exit_reason(trading_days_to_expiry=1, mode="intraday", **kw) is None
    assert S.condor_exit_reason(trading_days_to_expiry=1, mode="sweep", **kw) is None
    assert S.condor_exit_reason(expiry=TODAY, today=TODAY, trading_days_to_expiry=0, mode="sweep") == "expiry_day_sweep"
    assert S.condor_exit_reason(trading_days_to_expiry=40, mode="daily", force_all=True, **kw) == "emergency_close"


def test_daily_run_closes_the_condor_on_the_last_trading_day_before_expiry():
    day_before = dt.date(2026, 12, 17)
    now = dt.datetime.combine(day_before, dt.time(15, 55), ET)
    ctx, condors = _exit_ctx(day_before, now)
    ses = signals.sessions_from_bars(ctx.broker.hist["SPY"].index, day_before)
    jobs, rows = R.plan_condor_exits(ctx, condors, ses, mode="daily")
    assert len(jobs) == 1 and rows[0]["reason"] == "expiry_exit" and rows[0]["tdte"] == 1
    j = jobs[0]
    assert j.sleeve == "condor" and not j.credit and j.cutoff_price is not None and len(j.legs) == 4
    assert [l["position_intent"] for l in j.legs] == ["buy_to_close", "sell_to_close", "buy_to_close", "sell_to_close"]
    jobs, rows = R.plan_condor_exits(R.Ctx(ctx.broker, TODAY, now=NOW), condors, ses, mode="daily")
    assert not jobs and rows[0]["reason"] is None                       # weeks out: hold


def test_expiry_day_sweep_closes_before_the_deadline_and_never_after():
    ses = [CEXP]
    ctx, condors = _exit_ctx(CEXP, dt.datetime.combine(CEXP, dt.time(9, 45), ET))
    jobs, rows = R.plan_condor_exits(ctx, condors, ses, mode="sweep")
    assert len(jobs) == 1 and jobs[0].meta["reason"] == "expiry_day_sweep"
    for t in (dt.time(14, 55), dt.time(15, 0), dt.time(15, 31)):        # inside the margin, at, past the deadline
        ctx, condors = _exit_ctx(CEXP, dt.datetime.combine(CEXP, t, ET))
        for mode in ("sweep", "intraday", "daily"):
            jobs, rows = R.plan_condor_exits(ctx, condors, ses, mode=mode)
            assert not jobs and rows[0]["blocked"] and "BLOCKED" in rows[0]["reason"], (t, mode)
            assert any("Close it by hand" in w for w in ctx.warnings)
    # half day: one hour before a 13:00 close
    assert R.condor_deadline(CEXP, dt.datetime.combine(CEXP, dt.time(13, 0), ET)).time() == dt.time(12, 0)


def test_forced_condor_exit_without_quotes_is_priced_from_intrinsic():
    ctx, condors = _exit_ctx(CEXP, dt.datetime.combine(CEXP, dt.time(9, 45), ET), quotes=False)
    jobs, rows = R.plan_condor_exits(ctx, condors, [CEXP], mode="sweep")
    assert len(jobs) == 1 and jobs[0].prices[0] >= 0.05 and jobs[0].cutoff_price <= 20.0
    assert any("intrinsic" in w for w in ctx.warnings)


def test_intraday_sweep_caps_condor_orders_at_the_deadline(monkeypatch, isolated):
    now = dt.datetime.combine(CEXP, dt.time(14, 52), ET)               # 8 min before 15:00
    cid, r = condor_record(exp=CEXP)
    state.save_condors({cid: r})
    fake = daily_fake(now, condor_positions(r))
    fake.chain = condor_chain(exp=CEXP, ts=now, spot=round(fake.live["SPY"]["close"]))
    _patch_daily(monkeypatch, fake, now)
    kw = {}

    def work(broker, jobs, **k):
        kw.update(k, jobs=list(jobs))
        return jobs
    monkeypatch.setattr(R.execution, "work_orders", work)
    assert R.main(["--intraday", "--execute", "--now"]) == 0
    assert [j.key for j in kw["jobs"]] == [cid]
    deadline = dt.datetime.combine(CEXP, dt.time(15, 0), ET)
    assert kw["cutoff"] <= deadline and kw["hard_stop"] <= deadline


# ====================================================================== entries, kill switch, shared budget
def test_condor_entry_is_blocked_by_the_kill_switch():
    fb, _ = _planner_setup()
    fb.chain = condor_chain(spot=round(fb.live["SPY"]["close"]))
    ctx = R.Ctx(fb, TODAY)
    jobs, row = R.plan_condor_entry(ctx, {}, {"equity": 10_000}, (True, "VIX 45 > 40"), 0.0, None)
    assert not jobs and "kill switch" in row["action"]
    jobs, row = R.plan_condor_entry(ctx, {}, {"equity": 10_000}, (False, "ok"), 0.0, None)
    assert len(jobs) == 1, row


def test_stale_vix_blocks_the_condor_in_a_daily_run(monkeypatch, isolated):
    fake = daily_fake(equity=20_000)
    _patch_daily(monkeypatch, fake, NOW)
    monkeypatch.setattr(R.signals, "vix_levels", lambda today, now=None: {
        "vix": 17.0, "vix3m": 19.0, "ratio": 0.9, "fresh": False, "stale_reason": "VIX quote 45 min old",
        "source": "test"})
    seen = capture(monkeypatch)
    assert R.main(["--execute", "--now", "--condor"]) == 0
    assert not seen                                                     # no condor, no dip


def test_one_condor_at_a_time():
    fb, _ = _planner_setup()
    fb.chain = condor_chain(spot=round(fb.live["SPY"]["close"]))
    cid, r = condor_record()
    jobs, row = R.plan_condor_entry(R.Ctx(fb, TODAY), {cid: r}, {"equity": 100_000}, (False, "ok"), 0.0, None)
    assert not jobs and row["action"].startswith("hold")


def test_daily_run_enters_when_flat_records_the_entry_and_shares_the_risk_budget(monkeypatch, isolated):
    eq = 20_000.0
    fake = daily_fake(equity=eq)
    _patch_daily(monkeypatch, fake, NOW)
    seen = capture(monkeypatch, fill=True)
    assert R.main(["--execute", "--now", "--condor"]) == 0
    con = [j for j in seen if j.sleeve == "condor"]
    dip = [j for j in seen if j.sleeve == "dip"]
    assert len(con) == 1 and len(dip) == 1
    p = con[0].meta["pick"]
    c_loss = S.max_loss(p.width, con[0].prices[-1], con[0].qty)
    d_loss = S.max_loss(dip[0].meta["pick"].width, dip[0].prices[-1], dip[0].qty)
    assert c_loss <= C.CONDOR_RISK_PER_POSITION * eq + 1e-6
    assert c_loss + d_loss <= C.RISK_TOTAL * eq + 1e-6
    assert not (p.symbols & {l["symbol"] for l in dip[0].legs})
    rec = state.load_condors()
    assert list(rec) == [p.id] and rec[p.id]["entry_date"] == TODAY.isoformat() and rec[p.id]["qty"] == con[0].qty
    assert rec[p.id]["entry_credit"] == pytest.approx(con[0].prices[0])
    assert p.id not in state.load_positions()                           # the dip store never sees the condor
    # the next day it is held: no second condor, and its max loss stays reserved for the dip sleeve
    nxt = NOW + dt.timedelta(days=1)
    fake2 = daily_fake(nxt, condor_positions(rec[p.id]) + [
        leg_pos(dip[0].legs[0]["symbol"], -dip[0].qty), leg_pos(dip[0].legs[1]["symbol"], dip[0].qty)], equity=eq)
    _patch_daily(monkeypatch, fake2, nxt)
    seen.clear()
    assert R.main(["--execute", "--now", "--force", "--condor"]) == 0
    assert not [j for j in seen if j.purpose == "open"]
    assert list(state.load_condors()) == [p.id]


def test_open_condor_max_loss_is_reserved_in_the_dip_sizing(monkeypatch, isolated):
    cid, r = condor_record(credit=1.0)                                  # (20 - 1) * 100 = $1,900 reserved
    assert R.condor_open_max_loss({cid: r}) == pytest.approx(1900.0)
    eq = 100_000.0
    fb, _ = _planner_setup()
    acct = {"equity": eq, "options_buying_power": None}
    free, _ = R.plan_entries(R.Ctx(fb, TODAY), {}, acct, (False, "ok"), ["SPY"])
    held, _ = R.plan_entries(R.Ctx(fb, TODAY), {}, acct, (False, "ok"), ["SPY"], reserved=40_000.0)
    per = S.max_loss(free[0].meta["pick"].width, free[0].prices[-1], 1)
    assert free[0].qty == int(0.25 * eq // per)
    assert held[0].qty == int((0.50 * eq - 40_000.0) // per) < free[0].qty     # only the shared room is left
    none, rows = R.plan_entries(R.Ctx(fb, TODAY), {}, acct, (False, "ok"), ["SPY"], reserved=0.5 * eq)
    assert not none and "size 0" in rows[0]["action"]


def test_dip_entry_never_trades_a_condor_leg(monkeypatch):
    fb, _ = _planner_setup()
    acct = {"equity": 10_000, "options_buying_power": None}
    jobs, _ = R.plan_entries(R.Ctx(fb, TODAY), {}, acct, (False, "ok"), ["SPY"])
    sym = jobs[0].legs[1]["symbol"]
    jobs, rows = R.plan_entries(R.Ctx(fb, TODAY), {}, acct, (False, "ok"), ["SPY"], avoid={sym})
    assert not jobs and "condor" in rows[0]["action"]


def test_condor_close_books_a_trade_and_clears_the_record():
    cid, r = condor_record(credit=2.0, qty=2)
    condors = {cid: r}
    j = execution.Job(cid, "close", execution.condor_close_legs(*S.condor_leg_symbols(r)), 2, [0.5], credit=False,
                      sleeve="condor", meta={"record": r, "reason": "expiry_exit"})
    j.order_ids.append("o9")
    j.fills["o9"] = (2.0, 0.5)
    trades = R.apply_results([j], {}, TODAY, None, persist=True, condors=condors)
    assert condors == {} and trades[0]["pnl"] == pytest.approx(300.0) and trades[0]["sleeve"] == "condor"
    assert trades[0]["pnl_pct"] == pytest.approx(300.0 / ((20 - 2.0) * 200), abs=1e-4)
    assert state.read_jsonl(state.ORDERS)[-1]["sleeve"] == "condor"


def test_close_all_includes_condors(monkeypatch, isolated):
    cid, r = condor_record()
    state.save_condors({cid: r})
    fb = ChainFake({"SPY": _planner_setup()[0].hist["SPY"]}, {"SPY": _planner_setup()[0].live["SPY"]},
                   condor_chain(), condor_positions(r))
    fb.fills = lambda after: []
    monkeypatch.setattr(R, "Broker", lambda execute=False: fb)
    monkeypatch.setattr(R, "now_et", lambda: NOW)
    plans = []
    orig = R.plan_condor_exits
    monkeypatch.setattr(R, "plan_condor_exits", lambda *a, **k: plans.append(orig(*a, **k)) or plans[-1])
    assert R.main(["--close-all"]) == 0                                 # dry run
    assert [j.key for j in plans[0][0]] == [cid] and plans[0][0][0].meta["reason"] == "emergency_close"


def test_sync_reports_condors_separately_from_dip_spreads(monkeypatch, isolated):
    from live import db, sync
    cid, r = condor_record()
    state.save_condors({cid: r})
    legs = [{**p, "current_price": 1.0, "market_value": 100.0, "unrealized_pl": 0.0, "unrealized_plpc": 0.0}
            for p in condor_positions(r)]
    fb = ChainFake({}, {}, condor_chain(), legs)
    fb.has_trading = True
    fb.account = lambda: {"equity": 10_000.0, "cash": 10_000.0, "last_equity": 10_000.0, "long_market_value": 0,
                          "short_market_value": 0}
    state.append(state.TRADES, {"id": "t-c", "spread_id": cid, "pnl": 10.0, "sleeve": "condor",
                                "call_short_strike": 740, "call_long_strike": 760})
    state.append(state.TRADES, {"id": "t-d", "spread_id": "SPY-x", "pnl": 5.0})
    monkeypatch.setattr(sync, "Broker", lambda execute=False: fb)
    writes = []
    monkeypatch.setattr(db, "insert", lambda table, rows, **k: writes.append((table, rows)) or [])
    monkeypatch.setattr(db, "delete", lambda *a, **k: None)
    assert sync.main() == 0
    pos = [rows for t, rows in writes if t == "options_live_positions"]
    assert pos[0] == [] and pos[1][0]["spread_id"] == cid and pos[1][0]["sleeve"] == "condor"
    trades = [rows for t, rows in writes if t == "options_live_trades"]
    assert [x["id"] for x in trades[0]] == ["t-d"] and "sleeve" not in trades[0][0]      # dip rows unchanged
    assert [x["id"] for x in trades[1]] == ["t-c"] and trades[1][0]["sleeve"] == "condor"
