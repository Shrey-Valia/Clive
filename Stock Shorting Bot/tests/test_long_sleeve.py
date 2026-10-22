"""The overnight long sleeve (config.OVERNIGHT_LONG_ENABLED): 15:55 buys after the cover-all,
sizing and caps, the news skip, the T+1 sell-then-short ordering (late and partial sells), halts,
monitor alerts, and the ETB history log."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os

import pytest

import daily
import monitor
from conftest import ET, gainer, qty_for, run_daily
from shortingbot import broker, config, news, risk, strategy


def today():
    return dt.datetime.now(ET).date().isoformat()


def kinds(fdb):
    return [r["kind"] for r in fdb.rows("alerts")]


def long_events(fake, side=None):
    return [e for e in fake.events if e[0] == "long" and (side is None or e[1] == side)]


@pytest.fixture
def sleeve(monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)


@pytest.fixture
def closing(fake, sleeve):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5, seconds=1))
    return fake


@pytest.fixture
def opening(fake, sleeve):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    return fake


def gainers(scan_inputs, fake, prices: dict):
    for t, px in prices.items():
        scan_inputs.append(gainer(t, close=px))
        fake.adv[t] = 50e6
        fake.prices[t] = px


def write_picks(tickers, scan=20.0):
    daily.save_picks(config.PICKS_FILE, {
        "run_id": 42, "scan_date": "2026-10-21", "trade_date": today(), "tickers": tickers,
        "picks": [{"ticker": t, "score": 80, "scan_price": scan, "target_pct": 10.0, "scan_qty": 50} for t in tickers]})


def write_long_state(longs: dict, fake=None, price=20.0):
    """Yesterday's 15:55 buys, and the positions they left."""
    daily.save_picks(daily.long_state_path(), {
        "entry_date": "2026-10-21", "exit_date": today(), "run_id": 41, "kind": "limit", "status": "entered",
        "orders": [{"ticker": t, "qty": q, "id": f"b{t}", "status": "filled", "filled_qty": q} for t, q in longs.items()]})
    if fake is not None:
        for t, q in longs.items():
            fake.pos[t] = {"qty": q, "entry": price, "price": price}


# ---------------------------------------------------------------- 15:55 entry
def test_cover_all_never_sells_longs(fake, fdb, sleeve):
    fake.short("A", 10, 20.0)
    fake.pos["L"] = {"qty": 7, "entry": 30.0, "price": 30.0}
    out = daily.cover_all(1, True, dt.datetime.now(ET) + dt.timedelta(minutes=5))
    assert [(o["ticker"], o["side"]) for o in fake.orders] == [("A", "buy")]
    assert fake.pos == {"L": {"qty": 7, "entry": 30.0, "price": 30.0}} and out["left"] == {}


def test_1555_buys_picks_after_the_cover_all(closing, fdb, scan_inputs):
    fake = closing
    fake.short("OLD", 10, 20.0)
    gainers(scan_inputs, fake, {"G0": 20.0, "G1": 25.0, "G2": 40.0})
    assert run_daily("--execute") == 0
    ev = [e for e in fake.events if e[0] in ("submit", "long")]
    assert ev[0] == ("submit", "buy", "OLD", 10)                         # the cover first
    picks = json.load(open(config.PICKS_FILE))["tickers"]
    assert len(picks) == 3 and sorted(e[3] for e in ev[1:]) == sorted(picks)
    assert all(e[1:3] == ("buy", "limit") for e in ev[1:])
    assert {t: q for _, _, _, t, q in ev[1:]} == {t: qty_for(10_000, 5.0, fake.prices[t]) for t in picks}
    limits = {o["ticker"]: o["limit_price"] for o in fake.orders if o.get("kind") == "limit"}
    assert limits == {t: round(fake.prices[t] * 1.005, 2) for t in picks}
    assert fake.client_ids[1:] == [f"v2-long-buy-{today()}-{t}-0" for t in [e[3] for e in ev[1:]]]
    rows = [r for r in fdb.rows("short_orders") if r.get("sleeve") == "overnight_long"]
    assert {r["purpose"] for r in rows} == {"long_entry"} and len(rows) == 3
    state = json.load(open(daily.long_state_path()))
    assert state["status"] == "entered" and state["exit_date"] == fake.next_day.isoformat()
    assert sorted(o["ticker"] for o in state["orders"]) == sorted(picks)
    assert {t: p["qty"] for t, p in fake.pos.items()} == {t: qty_for(10_000, 5.0, fake.prices[t]) for t in picks}


def test_1555_skips_a_pick_still_short_after_the_cover(closing, fdb, scan_inputs):
    fake = closing
    gainers(scan_inputs, fake, {"G0": 20.0, "G1": 25.0})
    fake.short("G0", 10, 20.0)
    fake.reject["G0"] = "temporarily unavailable"                       # the cover fails
    assert run_daily("--execute") == 1
    assert [e[3] for e in long_events(fake)] == ["G1"]
    assert fake.pos["G0"]["qty"] == -10


def test_1555_limit_unfilled_rests_and_is_recorded(closing, fdb, scan_inputs):
    fake = closing
    gainers(scan_inputs, fake, {"G0": 20.0})
    fake.quote_map["G0"] = {"bid": 19.0, "ask": 19.0, "mid": 19.0, "last": 19.0}   # price runs past the limit
    assert run_daily("--execute") == 0
    o = next(o for o in fake.orders if o.get("kind") == "limit")
    assert o["status"] == "new" and o["limit_price"] == round(19.0 * 1.005, 2) and "G0" not in fake.pos


def test_moc_entry_goes_before_the_cover_and_skips_held_names(closing, fdb, scan_inputs, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENTRY_ORDER", "moc")
    fake = closing
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=15))   # 15:45: MOC still open
    fake.short("G1", 10, 25.0)                                          # entered as a short this morning
    gainers(scan_inputs, fake, {"G0": 20.0, "G1": 25.0})
    assert run_daily("--execute") == 0
    ev = [e for e in fake.events if e[0] in ("submit", "long")]
    assert ev == [("long", "buy", "moc", "G0", qty_for(10_000, 5.0, 20.0)), ("submit", "buy", "G1", 10)]
    assert json.load(open(daily.GUARD))["orders"] == ["o2", "o1"]


def test_long_entry_halted(closing, fdb, scan_inputs, monkeypatch):
    fake = closing
    gainers(scan_inputs, fake, {"G0": 20.0})
    risk.set_halt(today(), "test halt")
    assert run_daily("--execute") == 0
    assert long_events(fake) == []
    os.remove(os.path.join(config.LOG_DIR, "halt.json"))
    os.remove(daily.GUARD)
    monkeypatch.setattr(config, "DRAWDOWN_HALT_PCT", 10.0)
    risk.update_peak(13_000.0)
    assert run_daily("--execute") == 0
    assert long_events(fake) == []


def test_flag_off_buys_nothing(fake, fdb, scan_inputs, monkeypatch):
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5, seconds=1))
    gainers(scan_inputs, fake, {"G0": 20.0})
    assert run_daily("--execute") == 0
    assert long_events(fake) == [] and not os.path.exists(daily.long_state_path())


# ---------------------------------------------------------------- sizing, caps, news, price
def _pick(t, **kw):
    return {"ticker": t, "catalyst": "hype", "headlines": [], **kw}


def test_sizing_and_gross_cap():
    picks = [_pick(f"P{i}") for i in range(7)]
    q = {p["ticker"]: {"ask": 20.0, "last": 20.0, "mid": 19.99} for p in picks}
    entries, skips = strategy.plan_long_entries(picks, q, {}, 10_000)
    assert [e["qty"] for e in entries] == [25] * 5                   # 5% each, $500 / $20
    assert [s["reason"] for s in skips] == ["long gross cap reached"] * 2
    entries, _ = strategy.plan_long_entries(picks, q, {}, 10_000, existing_long_pct=12.5)
    assert [e["qty"] for e in entries] == [25, 25, 12]               # 12.5% left: 5 + 5 + 2.5
    entries, skips = strategy.plan_long_entries([_pick("BIG")], {"BIG": {"ask": 900.0}}, {}, 10_000)
    assert entries == [] and skips[0]["reason"] == "position too small for one share"


def test_news_skip_matches_classifier_and_headlines():
    picks = [_pick("D", catalyst="dilution"),
             _pick("O", catalyst="unclear", headlines=["XYZ announces $40M registered direct Offering"]),
             _pick("PR", headlines=["XYZ announces pricing of public offering"]),
             _pick("F", headlines=["XYZ files 424B5 prospectus"]),
             _pick("S", headlines=["XYZ files Form S-3 shelf"]),
             _pick("OK", catalyst="hype", headlines=["XYZ partners with AI firm"])]
    q = {p["ticker"]: {"ask": 20.0} for p in picks}
    entries, skips = strategy.plan_long_entries(picks, q, {}, 10_000)
    assert [e["ticker"] for e in entries] == ["OK"]
    assert {s["ticker"]: s["reason"].split("'")[1] for s in skips} == {
        "D": "dilution", "O": "offering", "PR": "offering", "F": "424B", "S": "S-3"}


def test_news_skip_in_the_full_run(closing, fdb, scan_inputs, monkeypatch):
    fake = closing
    gainers(scan_inputs, fake, {"G0": 20.0, "G1": 25.0})
    monkeypatch.setattr(news, "enrich", lambda t: {x: {"catalyst": "dilution" if x == "G1" else "hype",
                                                       "catalyst_score": 1.0,
                                                       "headlines": ["prices offering"] if x == "G1" else []} for x in t})
    assert run_daily("--execute") == 0
    assert "G1" in json.load(open(config.PICKS_FILE))["tickers"]     # still a v2 short pick
    assert [e[3] for e in long_events(fake)] == ["G0"]


def test_min_price_for_any_trade():
    entries, skips = strategy.plan_long_entries([_pick("C")], {"C": {"ask": 9.99, "last": 9.98}}, {}, 10_000)
    assert entries == [] and skips[0]["reason"] == "price $9.99 below $10"


def test_held_names_are_never_bought():
    entries, skips = strategy.plan_long_entries([_pick("S"), _pick("L")], {"S": {"ask": 20.0}, "L": {"ask": 20.0}},
                                                {"S": -5, "L": 3}, 10_000)
    assert entries == [] and [s["reason"] for s in skips] == ["still short (-5) after the cover", "already long (3)"]


# ---------------------------------------------------------------- T+1: sell, then short
def test_open_sells_longs_then_shorts_the_same_names(opening, fdb, caplog):
    fake = opening
    write_long_state({"A": 25, "B": 20}, fake)
    write_picks(["A", "B", "C"])
    fake.prices.update(A=20.0, B=20.0, C=20.0)
    assert run_daily("--open", "--execute") == 0
    ev = [e for e in fake.events if e[0] in ("submit", "long")]
    assert ev == [("long", "sell", "market", "A", 25), ("long", "sell", "market", "B", 20),
                  ("submit", "sell", "A", 50), ("submit", "sell", "B", 50), ("submit", "sell", "C", 50)]
    assert fake.client_ids == [f"v2-long-sell-{today()}-A-0", f"v2-long-sell-{today()}-B-0",
                               f"v2-short-{today()}-A-0", f"v2-short-{today()}-B-0", f"v2-short-{today()}-C-0"]
    assert {t: p["qty"] for t, p in fake.pos.items()} == {"A": -50, "B": -50, "C": -50}
    state = json.load(open(daily.long_state_path()))
    assert state["status"] == "exited" and state["left"] == {}
    exits = [r for r in fdb.rows("short_orders") if r.get("purpose") == "long_exit"]
    assert {r["run_id"] for r in exits} == {41} and {r["sleeve"] for r in exits} == {"overnight_long"}
    assert json.load(open(daily.OPEN_GUARD))["orders"] == ["o1", "o2", "o3", "o4", "o5"]
    assert "fill long_exit A 25 @ $20.00 | open print $20.00 (+0bp) | mid at send $19.99 (+5bp)" in caplog.text
    assert "fill short A 50 @ $20.00 | open print $20.00 (+0bp)" in caplog.text


def test_open_partial_sell_gets_a_second_pass_before_the_short(opening, fdb):
    fake = opening
    write_long_state({"A": 25}, fake)
    write_picks(["A"])
    fake.prices["A"] = 20.0
    fake.long_mode["A"] = ("partial", 10)
    assert run_daily("--open", "--execute") == 0
    ev = [e[:5] if e[0] == "long" else e for e in fake.events if e[0] in ("submit", "long", "cancel")]
    assert ev == [("cancel", ("A",)), ("long", "sell", "market", "A", 25), ("cancel", ("A",)),
                  ("long", "sell", "market", "A", 15), ("submit", "sell", "A", 50)]
    assert fake.client_ids[:2] == [f"v2-long-sell-{today()}-A-0", f"v2-long-sell-{today()}-A-1"]
    assert fake.pos["A"]["qty"] == -50


def test_open_late_fill_is_not_sold_twice(opening, fdb, monkeypatch):
    fake = opening
    write_long_state({"A": 25}, fake)
    write_picks(["A"])
    fake.prices["A"] = 20.0
    fake.long_mode["A"] = "rest"                                       # nothing filled within the wait
    real_cancel = fake.cancel_open_orders

    def cancel(symbols=None):                                          # it fills just before the cancel
        if any(o["ticker"] == "A" for o in fake.open_orders):
            fake.fill_resting("A")
        return real_cancel(symbols)
    monkeypatch.setattr(broker, "cancel_open_orders", cancel)
    assert run_daily("--open", "--execute") == 0
    assert [e[1:5] for e in long_events(fake)] == [("sell", "market", "A", 25)]
    assert fake.pos["A"]["qty"] == -50 and "long_exit_failed" not in kinds(fdb)


def test_open_sell_never_fills_alerts_and_skips_that_short(opening, fdb, monkeypatch, caplog):
    fake = opening
    write_long_state({"A": 25}, fake)
    write_picks(["A", "B"])
    fake.prices.update(A=20.0, B=20.0)
    real = fake.submit_long

    def stuck(ticker, *a, **k):
        fake.long_mode[ticker] = "rest"
        return real(ticker, *a, **k)
    monkeypatch.setattr(broker, "submit_long", stuck)
    assert run_daily("--open", "--execute") == 0
    assert len(long_events(fake)) == 1 + config.OVERNIGHT_LONG_EXIT_EXTRA_PASSES
    assert fake.pos["A"]["qty"] == 25 and fake.pos["B"]["qty"] == -50
    assert [o["ticker"] for o in fake.sent("sell") if o.get("kind") is None] == ["B"]
    assert "long_exit_failed" in kinds(fdb)
    assert "skip A: overnight long not sold yet (25)" in caplog.text


def test_open_already_held_short_still_skipped(opening, fdb, caplog):
    fake = opening
    fake.short("SHT", 4, 25.0)
    write_long_state({"A": 25}, fake)
    write_picks(["SHT", "A"])
    fake.prices.update(SHT=25.0, A=20.0)
    assert run_daily("--open", "--execute") == 0
    assert [e[3] for e in long_events(fake)] == ["A"]                   # the short is never sold or bought
    assert [o["ticker"] for o in fake.orders if o["side"] == "sell" and o.get("kind") is None] == ["A"]
    assert fake.pos["SHT"]["qty"] == -4
    assert "skip SHT: already held (-4)" in caplog.text


def test_open_sells_even_without_picks(opening, fdb):
    fake = opening
    write_long_state({"A": 25}, fake)
    assert run_daily("--open", "--execute") == 0                       # no picks file at all
    assert [e[1:5] for e in long_events(fake)] == [("sell", "market", "A", 25)] and fake.pos == {}


def test_flag_turned_off_overnight_still_sells(fake, fdb, monkeypatch):
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    write_long_state({"A": 25}, fake)
    write_picks(["A"])
    fake.prices["A"] = 20.0
    assert run_daily("--open", "--execute") == 0
    assert [e[1:5] for e in long_events(fake)] == [("sell", "market", "A", 25)]
    assert fake.pos["A"]["qty"] == -50


def test_opg_exit_before_0928(fake, fdb, sleeve, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_EXIT_ORDER", "opg")
    now = dt.datetime.now(ET)
    fake.session = (now + dt.timedelta(minutes=10), now + dt.timedelta(hours=6))
    write_long_state({"A": 25}, fake)
    write_picks(["A"])
    fake.prices["A"] = 20.0
    assert run_daily("--open", "--execute") == 0
    assert [e[1:5] for e in long_events(fake)] == [("sell", "opg", "A", 25)]
    assert fake.pos["A"]["qty"] == -50


def test_open_dry_run_plans_sell_then_short(opening, fdb, caplog):
    fake = opening
    write_long_state({"A": 25}, fake)
    write_picks(["A"])
    fake.prices["A"] = 20.0
    assert run_daily("--open") == 0
    assert fake.orders == []
    assert "DRY RUN would SELL A 25" in caplog.text and "plan SELL A 50" in caplog.text


# ---------------------------------------------------------------- halts
def test_halt_flatten_sells_longs(fake, fdb, sleeve, monkeypatch):
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 5.0)
    fake.short("A", 10, 20.0)
    fake.pos["L"] = {"qty": 7, "entry": 30.0, "price": 30.0}
    fake.last_equity = 11_000.0
    assert monitor.main(["--now"]) == 0
    assert fake.pos == {}
    assert [(o["ticker"], o["side"]) for o in fake.orders] == [("A", "buy"), ("L", "sell")]
    assert fake.client_ids == [f"v2-flatten-{today()}-A-0", f"v2-long-flatten-{today()}-L-0"]
    assert risk.halted_today(today())


def test_halt_at_0931_sells_longs_and_enters_nothing(opening, fdb, monkeypatch):
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 5.0)
    fake = opening
    fake.last_equity = 12_000.0
    write_long_state({"A": 25}, fake)
    write_picks(["A"])
    fake.prices["A"] = 20.0
    assert run_daily("--open", "--execute") == 0
    assert fake.pos == {} and [o["side"] for o in fake.orders] == ["sell"]   # the long sold, no short
    assert "daily_loss_halt" in kinds(fdb)


# ---------------------------------------------------------------- monitor
def _session(open_h=9, open_m=30):
    d = dt.datetime.now(ET).replace(second=0, microsecond=0)
    return d.replace(hour=open_h, minute=open_m), d.replace(hour=16, minute=0)


def _long(t, entry, price, qty=10):
    pl = (price - entry) * qty
    return {"ticker": t, "qty": float(qty), "side": "long", "avg_entry_price": entry, "current_price": price,
            "market_value": qty * price, "unrealized_pl": pl, "unrealized_plpc": pl / (qty * entry)}


def test_monitor_long_alerts(fdb, sleeve):
    s = _session()
    at = lambda h, m: s[0].replace(hour=h, minute=m)
    out = monitor.run([_long("L", 20.0, 14.0)], 10_000, at(9, 45), s)
    assert out["long_loss"] == ["L"] and out["long_outside_window"] == []    # 09:45: still in the exit window
    assert "position_loss" not in kinds(fdb)                                  # longs leave the short check
    out = monitor.run([_long("L", 20.0, 14.0)], 10_000, at(10, 0), s)
    assert out["long_outside_window"] == ["L"]
    assert kinds(fdb).count("long_loss") == 1 and kinds(fdb).count("long_outside_window") == 1   # deduped
    out = monitor.run([], 10_000, at(10, 15), s)
    assert out["long_loss"] == [] and len(out["resolved"]) == 0
    assert monitor.alerts.open_alerts() == {}


def test_monitor_long_window_ends_at_1555(fdb, sleeve):
    s = _session()
    out = monitor.run([_long("L", 20.0, 20.5)], 10_000, s[1] - dt.timedelta(minutes=4), s)
    assert out["long_outside_window"] == [] and out["long_loss"] == []


def test_monitor_flag_off_unchanged(fdb):
    s = _session()
    out = monitor.run([_long("L", 20.0, 14.0)], 10_000, s[0].replace(hour=10), s)
    assert "long_loss" not in out and out["hits"] == ["L"]                    # the old position_loss alert


# ---------------------------------------------------------------- ETB history
def test_etb_history_appended_after_each_scan(fake, fdb, scan_inputs, monkeypatch):
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5, seconds=1))
    gainers(scan_inputs, fake, {"G0": 20.0, "HTB": 30.0, "CHP": 5.0})
    fake.borrow_map["HTB"] = {"shortable": True, "easy_to_borrow": False}
    fake.borrow_map["CHP"] = {"shortable": False, "easy_to_borrow": False}
    assert run_daily("--execute") == 0
    path = os.path.join(config.LOG_DIR, "etb_history.csv")
    rows = list(csv.DictReader(open(path)))
    assert list(rows[0]) == ["date", "ticker", "price", "shortable", "etb"]
    assert {r["ticker"]: (r["date"], float(r["price"]), r["shortable"], r["etb"]) for r in rows} == {
        "G0": (today(), 20.0, "1", "1"), "HTB": (today(), 30.0, "1", "0"), "CHP": (today(), 5.0, "0", "0")}
    os.remove(daily.GUARD)
    assert run_daily("--execute") == 0
    assert len(list(csv.DictReader(open(path)))) == 6                          # appended, one header
    assert run_daily("--now") == 0                                             # a dry run writes its own file
    assert len(list(csv.DictReader(open(path)))) == 6
    assert len(list(csv.DictReader(open(os.path.join(config.LOG_DIR, "etb_history.dry_run.csv"))))) == 3


def test_dry_run_close_plans_longs_without_orders(closing, fdb, scan_inputs, caplog):
    fake = closing
    gainers(scan_inputs, fake, {"G0": 20.0})
    assert run_daily("--now") == 0
    assert fake.orders == [] and "plan BUY G0 25" in caplog.text
    assert not os.path.exists(daily.long_state_path())
    assert json.load(open(daily.long_state_path(False)))["status"] == "dry_run"


def test_moc_skipped_when_the_scan_misses_1550(closing, fdb, scan_inputs, monkeypatch, caplog):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENTRY_ORDER", "moc")
    fake = closing                                                      # close in 5 min: past 15:50
    gainers(scan_inputs, fake, {"G0": 20.0})
    assert run_daily("--execute") == 0
    assert long_events(fake) == [] and "no longs today" in caplog.text
