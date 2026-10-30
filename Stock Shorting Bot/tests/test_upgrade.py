"""The 2026-10-23 upgrade, on the new defaults: leftover covers at the open and mid-day, the 15:50
fallback close job, 5% sizing with the +35% stop and the squeeze cap, the industry cap, the
earnings gate and the overnight long sleeve on with those gates."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

import daily
from conftest import ET, ROOT, gainer, qty_for, run_daily
from shortingbot import config, db, earnings, strategy

REAL_MARKET_EVENTS = earnings.from_market_events          # conftest replaces both with "no earnings"
REAL_NASDAQ = earnings.from_nasdaq

INCIDENT = {"ASST": 51, "AVX": 412, "BNC": 317, "NAKA": 201, "RCEL": 96}   # shorts stuck on 2026-10-22


def today():
    return dt.datetime.now(ET).date().isoformat()


def kinds(fdb):
    return [r["kind"] for r in fdb.rows("alerts")]


def alert(fdb, kind):
    return next(r for r in fdb.rows("alerts") if r["kind"] == kind)


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


def write_picks(tickers, scan=20.0, trade_date=None, **extra):
    daily.save_picks(config.PICKS_FILE, {
        "run_id": 42, "scan_date": "2026-10-22", "trade_date": trade_date or today(), "tickers": tickers,
        "picks": [{"ticker": t, "score": 80, "scan_price": scan, "target_pct": 5.0, "scan_qty": 25} for t in tickers],
        **extra})


def incident_book(fake):
    for t, q in INCIDENT.items():
        fake.short(t, q, 20.0)
        fake.prices[t] = 20.0


def buys(fake):
    return [(e[2], e[3]) for e in fake.events if e[0] == "submit" and e[1] == "buy"]


# ---------------------------------------------------------------- defaults
def test_new_defaults_in_config_py():
    env = {k: v for k, v in os.environ.items() if k != "SHORTBOT_OVERNIGHT_LONG"}
    code = ("from shortingbot import config as c; import json; print(json.dumps([c.SIZE_PCT_PER_NAME, "
            "c.BROKER_STOP_PCT, c.MAX_SQUEEZE_LOSS_PCT, c.MAX_PER_INDUSTRY, c.EARNINGS_GATE, c.EARNINGS_SESSIONS, "
            "c.OVERNIGHT_LONG_ENABLED, c.OVERNIGHT_LONG_SIZE_PCT, c.OVERNIGHT_LONG_MAX_GROSS, "
            "c.COVER_LEFTOVERS_AT_OPEN]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [5.0, 35, 5.0, 2, True, 2, True, 5.0, 25.0, True]


# ---------------------------------------------------------------- 1a. leftover cover at the open
def test_open_covers_the_incident_shorts_with_no_picks_file(opening, fdb):
    """2026-10-23 09:31: the five 2026-10-21 shorts are still open and there is no picks file."""
    fake = opening
    incident_book(fake)
    assert run_daily("--open", "--execute") == 0
    assert sorted(buys(fake)) == sorted(INCIDENT.items())
    assert fake.client_ids == [f"v2-leftover-{today()}-{t}-0" for t in sorted(INCIDENT)]
    assert fake.pos == {}
    a = alert(fdb, "leftover_covered")
    assert a["level"] == "warning" and all(f"{t} {q}" in a["message"] for t, q in INCIDENT.items())
    assert "no_picks" in kinds(fdb) and "cover_failed" not in kinds(fdb)
    assert {r["purpose"] for r in fdb.rows("short_orders")} == {"leftover"}


def test_open_covers_leftovers_then_sells_longs_then_shorts(opening, fdb, monkeypatch):
    fake = opening
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    fake.short("OLD", 10, 20.0)
    fake.pos["L"] = {"qty": 7, "entry": 20.0, "price": 20.0}           # last night's sleeve long
    fake.prices.update(A=20.0, L=20.0)
    write_picks(["A", "L"])
    assert run_daily("--open", "--execute") == 0
    seq = [e for e in fake.events if e[0] in ("submit", "long", "stop")]
    assert seq[0] == ("submit", "buy", "OLD", 10)                       # 1. leftover cover
    assert seq[1] == ("long", "sell", "market", "L", 7)                 # 2. sleeve exit
    assert sorted(e[2] for e in seq[2:4]) == ["A", "L"] and all(e[:2] == ("submit", "sell") for e in seq[2:4])
    assert all(e[0] == "stop" for e in seq[4:])                         # 3. entries, then their stops
    q = qty_for(10_000, 5.0, 20.0)
    assert {t: p["qty"] for t, p in fake.pos.items()} == {"A": -q, "L": -q}
    assert json.load(open(daily.OPEN_GUARD))["shorts"] == ["A", "L"]


def test_open_rerun_never_covers_todays_shorts(opening, fdb):
    fake = opening
    fake.prices["A"] = 20.0
    write_picks(["A"])
    assert run_daily("--open", "--execute") == 0
    n = len(fake.orders)
    assert run_daily("--open", "--execute", "--force") == 0           # today's short is not a leftover
    assert not [e for e in fake.events if e[0] == "submit" and e[1] == "buy"]
    assert fake.pos["A"]["qty"] == -qty_for(10_000, 5.0, 20.0) and len(fake.orders) >= n


def test_open_dry_run_plans_the_leftover_cover_first(opening, fdb, caplog):
    fake = opening
    incident_book(fake)
    fake.prices["A"] = 20.0
    write_picks(["A"])
    assert run_daily("--open", "--now") == 0
    assert fake.orders == []
    text = caplog.text
    assert all(f"DRY RUN would BUY {t} {q} (leftover)" in text for t, q in INCIDENT.items())
    assert "plan SELL A 25" in text and "stop ~$27.00" in text         # 5%, +35%
    assert text.index("(leftover)") < text.index("plan SELL A")


# ---------------------------------------------------------------- 1c. late wake: covers yes, entries no
def test_late_start_covers_leftovers_but_enters_nothing(fake, fdb, nowait):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(minutes=22), now + dt.timedelta(hours=6))   # "09:52"
    fake.short("OLD", 10, 20.0)
    fake.prices["A"] = 20.0
    write_picks(["A"])
    assert run_daily("--open", "--execute") == 1
    assert buys(fake) == [("OLD", 10)] and "A" not in fake.pos
    assert "leftover_covered" in kinds(fdb) and "entry cutoff" in alert(fdb, "run_failed")["message"]


# ---------------------------------------------------------------- 1c. --cover-leftovers
def test_cover_leftovers_midday_leaves_todays_shorts_and_stops(midday, fdb):
    fake = midday
    fake.prices.update(A=20.0, OLD=20.0)
    fake.submit("A", 25, "sell")                                       # today's 09:31 short
    fake.submit_stop("A", 25, 27.0)
    fake.short("OLD", 10, 20.0)                                        # yesterday's, missed cover
    fake.events.clear(); fake.client_ids.clear()
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert buys(fake) == [("OLD", 10)]
    hhmm = dt.datetime.now(ET).strftime("%H%M")
    assert fake.client_ids == [f"v2-leftover-{today()}-OLD-0-L{hhmm}"]
    assert ("cancel", ("OLD",)) in fake.events and fake.pos["A"]["qty"] == -25
    assert [o["ticker"] for o in fake.open_orders] == ["A"]            # today's stop is untouched
    assert "leftover_covered" in kinds(fdb)


def test_cover_leftovers_knows_todays_shorts_from_the_guard_too(midday, fdb):
    fake = midday
    fake.short("A", 25, 20.0)
    daily.write_guard(today(), [{"id": "x", "ticker": "A", "side": "sell", "purpose": "short"}], daily.OPEN_GUARD)
    assert run_daily("--cover-leftovers", "--execute") == 0
    # not covered; round 3 STOP_REPAIR (on) gives today's stop-less short its +35% stop
    assert [(o["side"], o.get("stop_price")) for o in fake.orders] == [("buy", 27.0)]


def test_cover_leftovers_refuses_to_guess(midday, fdb):
    """Alpaca's sells unreadable and the 09:31 run unfinished: it might have shorted any name."""
    fake = midday
    fake.short("A", 25, 20.0)
    fake.sold_today_error = "timeout"
    daily.write_guard(today(), [], daily.OPEN_GUARD, status="started")
    assert run_daily("--cover-leftovers", "--execute") == 2
    assert fake.orders == []


def test_cover_leftovers_outside_hours_and_holiday(fake, fdb):
    now = dt.datetime.now(ET)
    fake.short("OLD", 10, 20.0)
    fake.session = (now + dt.timedelta(hours=1), now + dt.timedelta(hours=7))       # before the open
    assert run_daily("--cover-leftovers", "--execute") == 0
    fake.session = (now - dt.timedelta(hours=7), now - dt.timedelta(minutes=1))     # after the close
    assert run_daily("--cover-leftovers", "--execute") == 0
    fake.session = None
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert fake.orders == []


def test_cover_leftovers_skips_while_the_open_job_runs(midday, fdb):
    fake = midday
    fake.short("OLD", 10, 20.0)
    with open(os.path.join(config.LOG_DIR, "open.lock"), "w") as fh:
        fh.write(str(os.getppid()))                                    # a live process that isn't us
    assert run_daily("--cover-leftovers", "--execute") == 0
    assert fake.orders == []


# ---------------------------------------------------------------- 1b. the 15:50 fallback
def test_fallback_covers_at_once_then_scans_and_buys_longs(fake, fdb, scan_inputs, nowait, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    closing_in(fake, 10)                                                # 15:50
    fake.short("OLD", 10, 20.0)
    for t in ("G0", "G1"):
        scan_inputs.append(gainer(t, close=20.0))
        fake.adv[t] = 50e6
        fake.prices[t] = 20.0
    assert run_daily("--execute", "--fallback") == 0
    assert nowait == []                                                 # no wait for 15:45 or 15:55
    assert buys(fake)[0] == ("OLD", 10) and "OLD" not in fake.pos
    assert fake.client_ids[0] == f"v2-cover-{today()}-OLD-0"           # the 15:40 job's ids: never twice
    assert json.load(open(config.PICKS_FILE))["tickers"] == ["G0", "G1"]
    assert sorted(e[3] for e in fake.events if e[0] == "long") == ["G0", "G1"]
    assert "close_fallback" in kinds(fdb)
    assert json.load(open(daily.GUARD))["date"] == today()


def test_fallback_skips_when_the_close_job_already_ran(fake, fdb, scan_inputs, nowait):
    closing_in(fake, 5)
    fake.short("OLD", 10, 20.0)
    scan_inputs.append(gainer("G0"))
    assert run_daily("--execute") == 0                                  # 15:40 job: covers at 15:55
    n = len(fake.orders)
    assert run_daily("--execute", "--fallback") == 0
    assert len(fake.orders) == n and buys(fake) == [("OLD", 10)]


@pytest.mark.parametrize("state", ["started_guard", "lock_held"])
def test_no_double_cover_while_the_1540_job_is_mid_run(fake, fdb, scan_inputs, nowait, state, monkeypatch):
    closing_in(fake, 10)
    fake.short("OLD", 10, 20.0)
    if state == "started_guard":
        daily.write_guard(today(), [], status="started")
    else:                                                               # 15:40 job waiting for 15:55
        with open(os.path.join(config.LOG_DIR, "close.lock"), "w") as fh:
            fh.write(str(os.getppid()))
        # while the fallback polls the lock, the 15:40 job reaches 15:55 and writes its guard
        monkeypatch.setattr(daily.time, "sleep", lambda s: daily.write_guard(today(), [], status="started"))
    assert run_daily("--execute", "--fallback") == 0
    assert fake.orders == [] and "job_locked" not in kinds(fdb)


def test_fallback_holds_the_lock_so_a_late_1540_job_cannot_cover(fake, fdb, scan_inputs, nowait, monkeypatch):
    """The fallback writes the started guard before its cover; a 15:40 run after it is refused."""
    closing_in(fake, 10)
    fake.short("OLD", 10, 20.0)
    scan_inputs.append(gainer("G0"))
    assert run_daily("--execute", "--fallback") == 0
    assert run_daily("--execute") == 1                                  # "already executed today"
    assert buys(fake) == [("OLD", 10)]


def test_fallback_past_the_cutoff_alerts_and_sends_nothing(fake, fdb, nowait):
    closing_in(fake, 1, 0)                                              # 15:59
    fake.short("OLD", 10, 20.0)
    assert run_daily("--execute", "--fallback") == 1
    assert fake.orders == [] and alert(fdb, "close_missed")["level"] == "critical"


def test_fallback_half_day_only_is_a_noop_on_a_regular_day(fake, fdb, monkeypatch):
    now = dt.datetime.now(ET)
    fake.short("OLD", 10, 20.0)
    fake.session = (now.replace(hour=9, minute=30), now.replace(hour=16, minute=0))
    assert run_daily("--execute", "--fallback", "--half-day-only") == 0
    assert fake.orders == []


# ---------------------------------------------------------------- 2. sizing, stop, squeeze cap
def test_size_5pct_stop_35pct_cancelled_before_the_cover_never_long(opening, fdb, scan_inputs):
    fake = opening
    scan_inputs.append(gainer("G0"))
    fake.prices.update(A=20.0, B=25.0)
    write_picks(["A", "B"])
    assert run_daily("--open", "--execute") == 0
    qa, qb = qty_for(10_000, 5.0, 20.0), qty_for(10_000, 5.0, 25.0)
    assert {t: p["qty"] for t, p in fake.pos.items()} == {"A": -qa, "B": -qb}
    stops = [e for e in fake.events if e[0] == "stop"]
    assert stops == [("stop", "A", qa, 27.0), ("stop", "B", qb, 33.75)]
    assert [c for c in fake.client_ids if "-stop-" in c] == [f"v2-stop-{today()}-A-0", f"v2-stop-{today()}-B-0"]
    # B squeezes through its stop; A is still open at 15:55
    fake.prices["B"] = 33.75
    fake.trigger_stop("B")
    fake.events.clear()
    closing_in(fake, 5)
    assert run_daily("--execute") == 0
    i_cancel = next(i for i, e in enumerate(fake.events) if e[0] == "cancel")
    i_buy = next(i for i, e in enumerate(fake.events) if e[0] == "submit")
    assert i_cancel < i_buy and fake.open_orders == []
    assert buys(fake) == [("A", qa)] and fake.pos == {}                # flat, nothing bought past into a long


def test_squeeze_cap_bounds_the_size():
    assert strategy.squeeze_cap_pct() == 5.0
    old = config.SIZE_PCT_PER_NAME
    try:
        config.SIZE_PCT_PER_NAME = 10.0
        assert strategy.size_pct(0) == 5.0                              # +100% squeeze costs <= 5% of equity
        config.MAX_SQUEEZE_LOSS_PCT = 3.0
        assert strategy.size_pct(0) == 3.0
    finally:
        config.SIZE_PCT_PER_NAME, config.MAX_SQUEEZE_LOSS_PCT = old, 5.0
    picks = strategy.select([{**gainer(f"T{i}"), "short_score": 90, "adv20_usd": 5e7, "rank": i} for i in range(5)],
                            10_000)
    assert [p["target_pct"] for p in picks] == [5.0] * 5


# ---------------------------------------------------------------- 3. industry cap
def _row(t, rank, industry="Biotechnology", sector="Health Technology", **kw):
    return {**gainer(t, industry=industry, sector=sector), "short_score": 90.0 - rank, "adv20_usd": 5e7,
            "rank": rank, **kw}


def test_industry_cap_two_of_four_same_industry():
    ranked = [_row("B1", 1), _row("B2", 2), _row("B3", 3), _row("B4", 4), _row("X1", 5, industry="Packaged Software")]
    picks = strategy.select(ranked, 10_000)
    assert [p["ticker"] for p in picks] == ["B1", "B2", "X1"]
    assert ranked[2]["exclude_reason"] == ranked[3]["exclude_reason"] == "industry cap: 2 picks already in Biotechnology"


def test_industry_cap_counts_only_picks_and_falls_back_to_sector():
    ranked = [_row("B1", 1, short_score=50.0), _row("B2", 2), _row("B3", 3), _row("B4", 4),
              _row("S1", 5, industry=None, sector="Finance"), _row("S2", 6, industry=None, sector="Finance"),
              _row("S3", 7, industry=None, sector="Finance")]
    picks = strategy.select(ranked, 10_000)
    assert ranked[0]["exclude_reason"] == "score below 70"              # a gated name doesn't use a slot
    assert [p["ticker"] for p in picks] == ["B2", "B3", "S1", "S2"]
    assert ranked[6]["exclude_reason"] == "industry cap: 2 picks already in Finance"


# ---------------------------------------------------------------- 4. earnings gate
SCAN = dt.date(2026, 10, 22)
NEXT = [dt.date(2026, 10, 23), dt.date(2026, 10, 26)]


def test_earnings_from_market_events(monkeypatch):
    """Nasdaq down: a market_events hit still excludes; a name market_events doesn't cover is
    excluded too (EARNINGS_REQUIRE_NASDAQ, review M3); with the flag off, the older rule."""
    monkeypatch.setattr(earnings, "from_market_events", lambda t, a, b: ({"AAA": [(dt.date(2026, 10, 26), "BMO")]}, True))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({}, False))
    got = earnings.blocks(["AAA", "BBB"], SCAN, NEXT)
    assert got["AAA"] == "earnings within 2 sessions (2026-10-26)" and "fail closed" in got["BBB"]
    monkeypatch.setattr(config, "EARNINGS_REQUIRE_NASDAQ", False)
    assert earnings.blocks(["AAA", "BBB"], SCAN, NEXT) == {"AAA": "earnings within 2 sessions (2026-10-26)", "BBB": None}


def test_earnings_from_nasdaq_and_scan_day_after_the_close_only(monkeypatch):
    monkeypatch.setattr(earnings, "from_market_events", lambda t, a, b: ({}, False))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({"AMC": [(SCAN, "AMC")], "BMO": [(SCAN, "BMO")],
                                                                "T1": [(NEXT[0], None)],
                                                                "LATE": [(dt.date(2026, 10, 27), "BMO")]}, True))
    got = earnings.blocks(["AMC", "BMO", "T1", "LATE"], SCAN, NEXT)
    assert got == {"AMC": "earnings within 2 sessions (2026-10-22)", "BMO": None,
                   "T1": "earnings within 2 sessions (2026-10-23)", "LATE": None}


def test_earnings_both_sources_fail_closed(monkeypatch, caplog):
    monkeypatch.setattr(earnings, "from_market_events", lambda t, a, b: ({}, False))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({}, False))
    got = earnings.blocks(["AAA"], SCAN, NEXT)
    assert "fail closed" in got["AAA"] and "no calendar answered for AAA" in caplog.text


def test_earnings_nasdaq_parsing_and_day_cache(monkeypatch):
    monkeypatch.setattr(earnings, "from_nasdaq", REAL_NASDAQ)
    calls = []

    class Resp:
        def __init__(self, day):
            self.day = day

        def raise_for_status(self):
            pass

        def json(self):
            rows = [{"symbol": "AAA", "time": "time-after-hours"}] if self.day == "2026-10-23" else None
            return {"data": {"rows": rows}, "status": {"rCode": 200}}

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params["date"])
        assert "Mozilla" in headers["User-Agent"] and url == config.EARNINGS_NASDAQ_URL
        return Resp(params["date"])
    monkeypatch.setattr(earnings.requests, "get", get)
    days = earnings.window(SCAN, NEXT)
    assert earnings.from_nasdaq(["AAA", "BBB"], days) == ({"AAA": [(NEXT[0], "AMC")]}, True)
    assert earnings.from_nasdaq(["AAA"], days)[1] is True
    assert calls == ["2026-10-22", "2026-10-23", "2026-10-26"]        # second pass from the day's cache


def test_earnings_nasdaq_failure_is_not_cached(monkeypatch):
    monkeypatch.setattr(earnings, "from_nasdaq", REAL_NASDAQ)

    def get(*a, **k):
        raise OSError("blocked")
    monkeypatch.setattr(earnings.requests, "get", get)
    assert earnings.from_nasdaq(["AAA"], [SCAN]) == ({}, False)
    assert not os.path.exists(earnings._nasdaq_cache(SCAN))


def test_earnings_market_events_query(monkeypatch):
    monkeypatch.setattr(earnings, "from_market_events", REAL_MARKET_EVENTS)
    assert earnings.from_market_events(["AAA"], SCAN, NEXT[-1]) == ({}, False)     # Supabase off
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    seen = {}

    class R:
        def json(self):
            return [{"ticker": "AAA", "event_date": "2026-10-23", "event_time": "AMC"}]

    def send(method, table, rows, params, prefer):
        seen.update(method=method, table=table, **params)
        return R()
    monkeypatch.setattr(db, "_send", send)
    assert earnings.from_market_events(["AAA", "BBB"], SCAN, NEXT[-1]) == ({"AAA": [(NEXT[0], "AMC")]}, True)
    assert seen["table"] == "market_events" and seen["kind"] == "eq.EARNINGS" and seen["ticker"] == "in.(AAA,BBB)"
    monkeypatch.setattr(db, "_send", lambda *a: None)                  # read failed
    assert earnings.from_market_events(["AAA"], SCAN, NEXT[-1]) == ({}, False)


def test_earnings_excluded_in_the_scan_with_reason(fake, fdb, scan_inputs, nowait, monkeypatch):
    closing_in(fake, 5)
    for t in ("E1", "OK1"):
        scan_inputs.append(gainer(t, close=20.0))
        fake.adv[t] = 50e6
    monkeypatch.setattr(earnings, "from_market_events", lambda t, a, b: ({}, False))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({"E1": [(d[1], "BMO")]}, True))
    assert run_daily("--execute") == 0
    assert json.load(open(config.PICKS_FILE))["tickers"] == ["OK1"]
    cand = {r["ticker"]: r for r in fdb.rows("short_candidates")}
    assert cand["E1"]["exclude_reason"] == f"earnings within 2 sessions ({fake.next_day.isoformat()})"


def test_earnings_both_fail_excludes_every_name_in_the_scan(fake, fdb, scan_inputs, nowait, monkeypatch):
    closing_in(fake, 5)
    scan_inputs.append(gainer("A1", close=20.0))
    fake.adv["A1"] = 50e6
    monkeypatch.setattr(earnings, "from_market_events", lambda t, a, b: ({}, False))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({}, False))
    assert run_daily("--execute") == 0
    assert json.load(open(config.PICKS_FILE))["tickers"] == []
    assert "fail closed" in fdb.rows("short_candidates")[0]["exclude_reason"]


# ---------------------------------------------------------------- 5. long sleeve on, with the new gates
def test_long_sleeve_on_buys_only_gated_picks(fake, fdb, scan_inputs, nowait, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    closing_in(fake, 5)
    rows = {"B1": "Biotechnology", "B2": "Biotechnology", "B3": "Biotechnology", "B4": "Biotechnology",
            "EARN": "Packaged Software", "SW": "Packaged Software"}
    for t, ind in rows.items():
        scan_inputs.append(gainer(t, close=20.0, industry=ind))
        fake.adv[t] = 50e6
        fake.prices[t] = 20.0
    monkeypatch.setattr(earnings, "from_nasdaq", lambda t, d: ({"EARN": [(d[1], None)]}, True))
    assert run_daily("--execute") == 0
    picks = json.load(open(config.PICKS_FILE))["tickers"]
    assert sorted(picks) == ["B1", "B2", "SW"]
    longs = {e[3]: e[4] for e in fake.events if e[0] == "long"}
    assert sorted(longs) == sorted(picks) and set(longs.values()) == {qty_for(10_000, 5.0, 20.0)}
    reasons = {r["ticker"]: r["exclude_reason"] for r in fdb.rows("short_candidates")}
    assert reasons["B3"].startswith("industry cap") and reasons["EARN"].startswith("earnings within 2 sessions")
