"""Round 3: exchange-staged exits (STAGED_MOC_COVER), entry protection after the fill, and
the improved cover loop (COVER_KEEP_WORKING, collars). The fake broker enforces Alpaca's qty
reservation and wash-trade rules (fake.enforce) in these tests."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import daily
import sync
from conftest import ET, gainer, qty_for, run_daily
from shortingbot import broker, config, db

QA, QB = qty_for(10_000, 5.0, 20.0), qty_for(10_000, 5.0, 25.0)     # 25, 20


def today():
    return dt.datetime.now(ET).date().isoformat()


def kinds(fdb):
    return [r["kind"] for r in fdb.rows("alerts")]


@pytest.fixture
def nowait(monkeypatch):
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)


@pytest.fixture
def opening(fake, nowait):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    fake.enforce = True
    return fake


def write_picks(tickers, scan=20.0):
    daily.save_picks(config.PICKS_FILE, {
        "run_id": 42, "scan_date": "2026-10-22", "trade_date": today(), "tickers": tickers,
        "picks": [{"ticker": t, "score": 80, "scan_price": scan, "target_pct": 5.0, "scan_qty": 25} for t in tickers]})


def closing_in(fake, minutes):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=minutes, seconds=1))


def mocs(fake):
    return {o["ticker"]: o["qty"] - o["filled_qty"] for o in fake.open_orders if o.get("tif") == "cls"}


def stops(fake):
    return {o["ticker"]: o["qty"] for o in fake.open_orders if o.get("stop_price")}


def market_buys(fake):
    return [(e[2], e[3]) for e in fake.events if e[0] == "submit" and e[1] == "buy"]


def enter(fake, mode, monkeypatch):
    monkeypatch.setattr(config, "STAGED_MOC_COVER", mode)
    fake.prices.update(A=20.0, B=25.0)
    write_picks(["A", "B"])
    assert run_daily("--open", "--execute") == 0


# ---------------------------------------------------------------- defaults and the flag-off path
def test_round3_defaults():
    assert config.STAGED_MOC_COVER == "off"
    assert config.COVER_KEEP_WORKING is False
    assert config.ENTRY_ORDER == "market" and config.COVER_ORDER == "market"
    assert config.ENTRY_FILL_WAIT_SECONDS >= 120          # the bug fix: entries filled in 1-3 min on paper
    assert config.MOC_STAGE_BY_MINUTES_BEFORE_CLOSE > config.MOC_CUTOFF_MINUTES_BEFORE_CLOSE


def test_flag_off_never_sends_a_cls_order(opening, fdb, monkeypatch):
    enter(opening, "off", monkeypatch)
    assert stops(opening) == {"A": QA, "B": QB} and mocs(opening) == {}
    assert not any(e[0] == "moc" for e in opening.events)
    assert not any(e[0] == "cancel_order" for e in opening.events)
    assert not daily.staging_possible(today())


def test_reservation_forbids_a_stop_and_an_moc_on_the_same_shares(opening, fdb, monkeypatch):
    """Why there is no "stop + full MOC" mode: Alpaca rejects the second buy for the same shares."""
    enter(opening, "off", monkeypatch)
    with pytest.raises(RuntimeError, match="insufficient qty"):
        opening.submit_moc("A", QA)
    with pytest.raises(RuntimeError, match="insufficient qty"):
        opening.submit("A", QA, "buy")                    # nor a market cover while the stop rests


# ---------------------------------------------------------------- 09:31 staging
def test_full_mode_stages_an_moc_for_every_filled_share_and_no_stop(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    assert mocs(opening) == {"A": QA, "B": QB} and stops(opening) == {}
    assert f"v2-moc-{today()}-A-0" in opening.client_ids
    st = json.load(open(daily.moc_state_path()))
    assert st["date"] == today() and {o["ticker"] for o in st["orders"]} == {"A", "B"}
    picks = json.load(open(config.PICKS_FILE))
    assert picks["staged_moc"] == {"A": QA, "B": QB}
    # the host dies: nothing else runs; the closing auction covers everything
    opening.close_auction()
    assert opening.pos == {}


def test_split_mode_stop_and_moc_never_cover_the_same_share(opening, fdb, monkeypatch):
    enter(opening, "split", monkeypatch)
    sa, sb = -(-QA // 2), -(-QB // 2)                     # ceil
    assert stops(opening) == {"A": sa, "B": sb}
    assert mocs(opening) == {"A": QA - sa, "B": QB - sb}
    # A squeezes through its stop; the host is dead; the auction covers the rest: flat, never long
    opening.trigger_stop("A")
    opening.close_auction()
    assert "A" not in opening.pos and opening.pos["B"]["qty"] == -sb   # B's stop part: overnight, as today


def test_handoff_mode_keeps_the_stop_at_the_open(opening, fdb, monkeypatch):
    enter(opening, "handoff", monkeypatch)
    assert stops(opening) == {"A": QA, "B": QB} and mocs(opening) == {}


def test_handoff_swaps_the_stop_for_an_moc(opening, fdb, monkeypatch):
    enter(opening, "handoff", monkeypatch)
    monkeypatch.setattr(config, "MOC_HANDOFF_AT", (0, 0))
    out = daily.repair_protection(None, today(), opening.session[1], True, why=" test")
    assert out["moc"]["staged"] == {"A": QA, "B": QB}
    assert stops(opening) == {} and mocs(opening) == {"A": QA, "B": QB}
    i_cancel = max(i for i, e in enumerate(opening.events) if e[0] == "cancel_order")
    i_moc = min(i for i, e in enumerate(opening.events) if e[0] == "moc")
    assert i_cancel < i_moc                               # the stop is gone before the MOC goes in


def test_handoff_not_due_yet_changes_nothing(opening, fdb, monkeypatch):
    enter(opening, "handoff", monkeypatch)
    monkeypatch.setattr(config, "MOC_HANDOFF_AT", (23, 59))
    now = dt.datetime.now(ET)
    opening.session = (opening.session[0], now + dt.timedelta(hours=3))
    if daily.handoff_due(now, opening.session[1]):
        pytest.skip("clamped to the stage deadline this late in the day")
    daily.repair_protection(None, today(), opening.session[1], True)
    assert stops(opening) == {"A": QA, "B": QB} and mocs(opening) == {}


def test_stage_mocs_refuses_after_the_deadline(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "STAGED_MOC_COVER", "full")
    fake.short("A", 10, 20.0)
    out = daily.stage_mocs(None, today(), dt.datetime.now(ET) + dt.timedelta(minutes=5))
    assert out["sent"] == [] and "deadline" in out["problems"][0] and mocs(fake) == {}


def test_stage_mocs_skips_a_name_whose_entry_still_works(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "STAGED_MOC_COVER", "full")
    opening.market_mode["A"] = ("partial", 10)
    opening.submit("A", 25, "sell")                       # entry: 10 of 25 filled, still working
    out = daily.stage_mocs(None, today(), opening.session[1])
    assert "A" in out["skipped"] and mocs(opening) == {}


def test_stage_mocs_resizes_an_moc_bigger_than_the_short(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    opening.enforce = False
    opening.submit("A", 5, "buy")                         # someone covered 5 by hand
    opening.enforce = True
    out = daily.stage_mocs(None, today(), opening.session[1], tag="-S1")
    assert mocs(opening)["A"] == QA - 5 and out["staged"]["A"] == QA - 5


def test_stage_mocs_alerts_when_an_oversized_moc_cannot_be_cancelled(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    moc_a = next(o for o in opening.open_orders if o["ticker"] == "A" and o.get("tif") == "cls")
    opening.uncancellable.add(moc_a["id"])
    opening.enforce = False
    opening.submit("A", 5, "buy")
    daily.stage_mocs(None, today(), opening.session[1], tag="-S1")
    assert "moc_cover_failed" in kinds(fdb)


def test_moc_rejection_is_reported_and_the_stop_stays_in_split(opening, fdb, monkeypatch):
    opening.reject_moc["A"] = "cls orders not supported"
    enter(opening, "split", monkeypatch)
    assert "A" in stops(opening) and "A" not in mocs(opening)
    assert "moc_stage_failed" in kinds(fdb)


# ---------------------------------------------------------------- 15:40 job, 15:50 fallback, flatten
def test_close_job_leaves_staged_mocs_and_covers_nothing_twice(opening, fdb, monkeypatch, scan_inputs):
    enter(opening, "full", monkeypatch)
    scan_inputs.append(gainer("G0"))
    opening.events.clear()
    closing_in(opening, 20)
    assert run_daily("--execute") == 0
    assert market_buys(opening) == [] and mocs(opening) == {"A": QA, "B": QB}
    assert "cover_failed" not in kinds(fdb)
    opening.close_auction()
    assert opening.pos == {}


def test_close_job_split_covers_only_the_stop_part_at_market(opening, fdb, monkeypatch, scan_inputs):
    enter(opening, "split", monkeypatch)
    scan_inputs.append(gainer("G0"))
    opening.events.clear()
    closing_in(opening, 20)
    assert run_daily("--execute") == 0
    sa, sb = -(-QA // 2), -(-QB // 2)
    assert sorted(market_buys(opening)) == [("A", sa), ("B", sb)]
    assert stops(opening) == {} and mocs(opening) == {"A": QA - sa, "B": QB - sb}
    opening.close_auction()
    assert opening.pos == {}


def test_close_job_after_the_cls_cutoff_still_never_double_covers(opening, fdb, monkeypatch, scan_inputs):
    """15:55 with a staged MOC Alpaca will not cancel: the market cover skips the reserved shares."""
    enter(opening, "full", monkeypatch)
    opening.uncancellable |= {o["id"] for o in opening.open_orders}
    scan_inputs.append(gainer("G0"))
    closing_in(opening, 5)
    assert run_daily("--execute") == 0
    assert market_buys(opening) == [] and "cover_failed" not in kinds(fdb)


def test_fallback_with_staged_mocs(opening, fdb, monkeypatch, scan_inputs):
    enter(opening, "full", monkeypatch)
    scan_inputs.append(gainer("G0"))
    closing_in(opening, 10)
    assert run_daily("--execute", "--fallback") == 0
    assert market_buys(opening) == [] and mocs(opening) == {"A": QA, "B": QB}


def test_halt_flatten_takes_the_mocs_back(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    daily.halt_and_flatten(today(), "test halt", None, opening.session)
    assert mocs(opening) == {} and opening.pos == {}
    assert sorted(market_buys(opening)) == [("A", QA), ("B", QB)]


def test_release_all_hands_back_to_the_1555_market_cover(opening, fdb, monkeypatch, scan_inputs):
    enter(opening, "full", monkeypatch)
    monkeypatch.setattr(config, "MOC_RELEASE", "all")
    scan_inputs.append(gainer("G0"))
    closing_in(opening, 20)
    assert run_daily("--execute") == 0
    assert mocs(opening) == {} and sorted(market_buys(opening)) == [("A", QA), ("B", QB)] and opening.pos == {}


def test_release_overlap_frees_todays_pick_for_the_long_sleeve(opening, fdb, monkeypatch, scan_inputs):
    enter(opening, "full", monkeypatch)
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    scan_inputs.append(gainer("A", close=20.0))           # A gains again: today's pick and still short
    opening.adv["A"] = 5e7
    closing_in(opening, 20)
    assert run_daily("--execute") == 0
    assert "A" not in mocs(opening) and mocs(opening) == {"B": QB}
    assert ("A", QA) in market_buys(opening)              # covered at 15:55 at market
    assert opening.pos.get("A", {}).get("qty", 0) > 0     # and the sleeve bought it


def test_release_mocs_keeps_a_name_whose_cancel_does_not_land(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    opening.uncancellable |= {o["id"] for o in opening.open_orders if o["ticker"] == "B"}
    got = daily.release_mocs(today(), {"A", "B"}, opening.session[1])
    assert got == {"A"} and mocs(opening) == {"B": QB}


def test_release_after_the_cls_cutoff_does_nothing(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    got = daily.release_mocs(today(), {"A"}, dt.datetime.now(ET) + dt.timedelta(minutes=9))
    assert got == set() and mocs(opening) == {"A": QA, "B": QB}


# ---------------------------------------------------------------- after the close / next morning
def test_sync_alerts_when_the_auction_left_a_short(opening, fdb, monkeypatch):
    enter(opening, "full", monkeypatch)
    opening.close_auction(fill_ratio=0.5)
    monkeypatch.setattr(broker, "fills", lambda after: [])
    monkeypatch.setattr(db, "delete", lambda *a, **k: None)
    assert sync.main() == 0
    assert "moc_cover_failed" in kinds(fdb)


def test_orphan_long_from_an_moc_is_sold_at_the_open(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "STAGED_MOC_COVER", "full")
    daily.save_picks(daily.moc_state_path(), {"date": "2026-01-02", "orders": [
        {"ticker": "X", "qty": 10, "id": "m1", "status": "new"}]})
    opening.pos["X"] = {"qty": 4, "entry": 20.0, "price": 20.0}
    run_daily("--open", "--execute")
    assert "moc_orphan_long" in kinds(fdb) and "X" not in opening.pos


# ---------------------------------------------------------------- entry protection (bug fix, on)
def test_entry_still_working_gets_no_stop_then_the_repair_places_it(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "STAGED_MOC_COVER", "off")
    opening.prices.update(A=20.0, B=25.0)
    opening.market_mode["A"] = ("partial", 10)
    write_picks(["A", "B"])
    run_daily("--open", "--execute")
    assert stops(opening) == {"B": QB} and "stop_missing" in kinds(fdb)
    sell_a = next(o for o in opening.open_orders if o["ticker"] == "A" and o["side"] == "sell")
    opening.open_orders.remove(sell_a)                    # the rest of the entry fills later
    opening._apply("A", -(QA - 10), 20.0)
    sell_a.update(status="filled", filled_qty=float(QA))
    out = daily.repair_protection(None, today(), opening.session[1], True, why=" (10:00)")
    assert [s["ticker"] for s in out["stops"]] == ["A"] and stops(opening) == {"A": QA, "B": QB}


def test_entry_wait_uses_entry_fill_wait(opening, fdb, monkeypatch):
    waits = []
    monkeypatch.setattr(broker, "wait_filled", lambda ids, seconds: waits.append(seconds) or opening.refresh(ids))
    enter(opening, "off", monkeypatch)
    assert config.ENTRY_FILL_WAIT_SECONDS in waits


def test_stop_repair_off_places_nothing(midday_fake, fdb, monkeypatch):
    fake = midday_fake
    monkeypatch.setattr(config, "STOP_REPAIR", False)
    fake.short("A", 10, 20.0)
    fake.orders.append({"id": "o1", "ticker": "A", "side": "sell", "qty": 10.0, "filled_qty": 10.0, "status": "filled"})
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert stops(fake) == {}
    monkeypatch.setattr(config, "STOP_REPAIR", True)
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert stops(fake) == {"A": 10}


@pytest.fixture
def midday_fake(fake, nowait):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=2), now + dt.timedelta(hours=4))
    fake.enforce = True
    return fake


def test_stage_moc_job(midday_fake, fdb, monkeypatch):
    fake = midday_fake
    assert run_daily("--stage-moc", "--execute") == 0 and mocs(fake) == {}       # off
    monkeypatch.setattr(config, "STAGED_MOC_COVER", "full")
    fake.short("A", 10, 20.0)
    fake.orders.append({"id": "o1", "ticker": "A", "side": "sell", "qty": 10.0, "filled_qty": 10.0, "status": "filled"})
    assert run_daily("--stage-moc", "--execute") == 0 and mocs(fake) == {"A": 10}
    assert run_daily("--stage-moc", "--execute") == 0 and mocs(fake) == {"A": 10}  # idempotent
    fake.short("OLD", 3, 20.0)                            # a leftover is not today's: not staged
    assert run_daily("--stage-moc", "--execute") == 0 and "OLD" not in mocs(fake)


# ---------------------------------------------------------------- collars and the keep-working loop
def test_entry_collar_cancels_what_did_not_fill(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "ENTRY_ORDER", "limit_collar")
    opening.prices.update(A=20.0, B=26.0)
    opening.quote_map["B"] = {"bid": 26.0, "ask": 26.1, "mid": 26.05, "last": 26.0}   # 25.74 <= 26: fills
    opening.quote_map["A"] = {"bid": 21.0, "ask": 21.1, "mid": 21.05, "last": 21.0}   # A ran up: 20.79 > 20
    write_picks(["A", "B"])
    run_daily("--open", "--execute")
    lim = {e[2]: e[4] for e in opening.events if e[0] == "limit"}
    assert lim == {"A": 20.79, "B": 25.74}
    qb = qty_for(10_000, 5.0, 26.0)
    assert "A" not in opening.pos and opening.pos["B"]["qty"] == -qb
    assert not any(o["ticker"] == "A" for o in opening.open_orders)
    assert stops(opening) == {"B": qb}


def test_cover_collar_first_pass_then_market(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "COVER_ORDER", "limit_collar")
    fake.short("A", 10, 20.0)
    fake.prices["A"] = 20.0
    fake.quote_map["A"] = {"bid": 19.0, "ask": 19.5, "mid": 19.25, "last": 19.5}       # limit 19.70 < 20: rests
    out = daily.cover_all(None, True, dt.datetime.now(ET) + dt.timedelta(hours=1), purpose="leftover")
    assert out["left"] == {} and fake.pos == {}
    assert [e[0] for e in fake.events if e[0] in ("limit", "submit")] == ["limit", "submit"]


def test_keep_working_never_cancels_a_working_cover(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "COVER_KEEP_WORKING", True)
    fake.short("A", 10, 20.0)
    fake.short("B", 5, 20.0)
    fake.market_mode["A"] = "rest"                        # A's cover is slow (paper at the open)

    def fill_later(f):
        o = next((o for o in f.open_orders if o["ticker"] == "A" and o["side"] == "buy"), None)
        if o and len(f.progress_waits) >= 2:
            f.open_orders.remove(o)
            f._apply("A", 10, 20.0)
            o.update(status="filled", filled_qty=10.0)
    fake.on_wait = fill_later
    out = daily.cover_all(None, True, dt.datetime.now(ET) + dt.timedelta(hours=1), purpose="leftover",
                          fill_wait=150)
    assert out["left"] == {} and fake.pos == {}
    assert market_buys(fake) == [("A", 10), ("B", 5)]    # sent once each, never re-sent
    assert not any(e[0] == "cancel_order" for e in fake.events)


def test_keep_working_tops_up_only_a_rejected_cover(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "COVER_KEEP_WORKING", True)
    fake.short("A", 10, 20.0)
    real = fake.submit
    calls = []

    def flaky(ticker, qty, side, client_order_id=None):
        calls.append((ticker, qty, client_order_id))
        if len(calls) == 1:
            raise RuntimeError("503 service unavailable")
        return real(ticker, qty, side, client_order_id)
    monkeypatch.setattr(broker, "submit", flaky)
    out = daily.cover_all(None, True, dt.datetime.now(ET) + dt.timedelta(hours=1), purpose="leftover")
    assert out["left"] == {} and fake.pos == {}
    assert [c[1] for c in calls] == [10, 10] and calls[0][2] != calls[1][2]


def test_keep_working_leftover_still_working_is_a_warning_not_cover_failed(fake, fdb, monkeypatch):
    monkeypatch.setattr(config, "COVER_KEEP_WORKING", True)
    fake.short("OLD", 10, 20.0)
    fake.market_mode["OLD"] = "rest"
    clock = [daily.time.time()]

    def tick(f):
        clock[0] += 60                                    # each poll: a minute of the paper open
    fake.on_wait = tick
    monkeypatch.setattr(daily.time, "time", lambda: clock[0])
    out = daily.cover_leftovers(None, today(), True, dt.datetime.now(ET) + dt.timedelta(hours=1))
    assert out["working"] == {"OLD": 10.0}
    assert "cover_failed" not in kinds(fdb) and "cover_working" in kinds(fdb)
    assert market_buys(fake) == [("OLD", 10)]


# ---------------------------------------------------------------- broker.wait_filled_progress
def test_wait_filled_progress_extends_while_fills_move(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(broker.time, "time", lambda: clock["t"])
    monkeypatch.setattr(broker.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    seq = iter([5, 5, 6, 7, 8, 9, 10])

    def refresh(ids):
        n = next(seq, 10)
        return [{"id": "x", "status": "filled" if n == 10 else "partially_filled", "filled_qty": n}]
    monkeypatch.setattr(broker, "refresh", refresh)
    out = broker.wait_filled_progress(["x"], 3, extend_until=100, stall_seconds=5)
    assert out[0]["status"] == "filled"


def test_wait_filled_progress_gives_up_on_a_stall(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(broker.time, "time", lambda: clock["t"])
    monkeypatch.setattr(broker.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(broker, "refresh", lambda ids: [{"id": "x", "status": "new", "filled_qty": 0}])
    broker.wait_filled_progress(["x"], 10, extend_until=1000, stall_seconds=5)
    assert 10 <= clock["t"] < 20


def test_is_staged_moc():
    assert broker.is_staged_moc({"tif": "cls", "side": "buy"})
    assert not broker.is_staged_moc({"tif": "day", "side": "buy"})
    assert not broker.is_staged_moc({"tif": "cls", "side": "sell"})
