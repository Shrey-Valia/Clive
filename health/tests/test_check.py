"""Offline tests for health/check.py (round 3): transient-vs-missing migrations, Mac power/lid,
Linux memory, the pre-close readiness window and event-alert auto-resolution. No network."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check", HERE.parent / "check.py")
check = importlib.util.module_from_spec(spec)
sys.modules["check"] = check
spec.loader.exec_module(check)

ET = check.ET


def cal_of(*days, half=()):
    c = object.__new__(check.Calendar)
    c.source = "test"
    c.sessions = [check.Session(d, "09:30", "13:00" if d in half else "16:00") for d in days]
    c.by_date = {s.date: s for s in c.sessions}
    return c


def probe_at(when: dt.datetime, cal):
    p = check.Probe(when, "testhost")
    p.cal = cal
    return p


# ------------------------------------------------------------------ Supabase error classes
def test_schema_missing_only_on_postgrest_codes():
    assert check.schema_missing(check.SupaError("x", 400, '{"code":"42703","message":"column x does not exist"}'))
    assert check.schema_missing(check.SupaError("x", 404, '{"code":"PGRST205","message":"Could not find the table"}'))
    assert not check.schema_missing(check.SupaError("x", 503, "Service Unavailable"))
    assert not check.schema_missing(check.SupaError("x", 0, ""))
    assert not check.schema_missing(requests.ConnectionError("reset"))


def test_transient():
    assert check.transient(check.SupaError("x", 503))
    assert check.transient(check.SupaError("x", 429))
    assert check.transient(check.SupaError("x", 0))
    assert check.transient(requests.Timeout())
    assert not check.transient(check.SupaError("x", 401))
    assert not check.transient(check.SupaError("x", 400))


def test_with_retry_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(check.time, "sleep", lambda s: None)
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise check.SupaError("flaky", 502)
        return "ok"
    assert check.with_retry(fn) == "ok" and len(calls) == 3


def test_with_retry_does_not_retry_real_errors(monkeypatch):
    monkeypatch.setattr(check.time, "sleep", lambda s: None)
    calls = []

    def fn():
        calls.append(1)
        raise check.SupaError("denied", 401)
    with pytest.raises(check.SupaError):
        check.with_retry(fn)
    assert len(calls) == 1


def test_migrations_transient_is_unknown_not_missing(monkeypatch):
    """The 2026-10-23 07:15 false alarm: a 5xx during the probe said 'migration not applied'."""
    monkeypatch.setattr(check.time, "sleep", lambda s: None)

    def get(table, params):
        if table == "health_checks":
            raise check.SupaError("health_checks: HTTP 503", 503, "upstream connect error")
        return []
    (st, msg), detail = check.migration_status(get)
    assert st == "unknown"
    assert "not a missing migration" in msg
    assert detail["missing"] == {} and "008" in detail["unverified"]


def test_migrations_really_missing_fails(monkeypatch):
    monkeypatch.setattr(check.time, "sleep", lambda s: None)

    def get(table, params):
        if table == "short_orders":
            raise check.SupaError("x", 400, '{"code":"42703","message":"column short_orders.sleeve does not exist"}')
        return []
    (st, msg), detail = check.migration_status(get)
    assert st == "fail" and "007" in msg


def test_migrations_all_present():
    (st, msg), _ = check.migration_status(lambda t, q: [])
    assert st == "ok"


# ------------------------------------------------------------------ Mac power and lid
BATT = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t77%; discharging; 6:34 remaining present: true\n"
AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n"
LID_OPEN = '      "AppleClamshellCausesSleep" = No\n      "AppleClamshellState" = No\n'
LID_CLOSED = '      "AppleClamshellState" = Yes\n'
PMSET_CAFF = " sleep                1 (sleep prevented by caffeinate, powerd)\n displaysleep 2\n"
PMSET_PLAIN = " sleep                1\n"
PMSET_OFF = " SleepDisabled\t\t1\n sleep                0\n"


def power(batt, lid, g):
    return {**check.parse_pmset_g(g), **check.parse_pmset_batt(batt), "lid_closed": check.parse_clamshell(lid)}


def judged(info, window=True):
    return {n: (st, m) for n, st, m in check.judge_power(info, window)}


def test_parsers():
    b = check.parse_pmset_batt(BATT)
    assert b == {"source": "battery", "percent": 77, "state": "discharging", "has_battery": True}
    assert check.parse_pmset_batt(AC)["source"] == "ac"
    assert check.parse_clamshell(LID_OPEN) is False and check.parse_clamshell(LID_CLOSED) is True
    assert check.parse_clamshell("") is None
    g = check.parse_pmset_g(PMSET_CAFF)
    assert g["sleep_min"] == 1 and "caffeinate" in g["prevented_by"] and not g["sleep_disabled"]
    assert check.parse_pmset_g(PMSET_OFF)["sleep_disabled"]


def test_battery_without_assertion_in_market_hours_fails():
    assert judged(power(BATT, LID_OPEN, PMSET_PLAIN))["power_source"][0] == "fail"


def test_battery_with_caffeinate_is_warn():
    assert judged(power(BATT, LID_OPEN, PMSET_CAFF))["power_source"][0] == "warn"


def test_battery_low_fails_even_with_sleep_disabled():
    info = power(BATT.replace("77%", "12%"), LID_OPEN, PMSET_OFF)
    assert judged(info)["power_source"][0] == "fail"


def test_lid_closed_on_battery_fails_in_window_only():
    info = power(BATT, LID_CLOSED, PMSET_CAFF)
    assert judged(info)["lid"][0] == "fail"
    assert judged(info, window=False)["lid"][0] == "ok"
    assert judged(info, window=False)["power_source"][0] == "ok"


def test_lid_closed_on_ac():
    assert judged(power(AC, LID_CLOSED, PMSET_CAFF))["lid"][0] == "warn"
    assert judged(power(AC, LID_CLOSED, PMSET_OFF))["lid"][0] == "ok"
    assert judged(power(AC, LID_OPEN, PMSET_CAFF)) == {"power_source": ("ok", "on AC power, battery 100%"), "lid": ("ok", "lid open")}


def test_market_window():
    d = dt.date(2026, 10, 23)
    cal = cal_of(d)
    assert check.market_window(probe_at(dt.datetime(2026, 10, 23, 15, 30, tzinfo=ET), cal))
    assert not check.market_window(probe_at(dt.datetime(2026, 10, 23, 17, 0, tzinfo=ET), cal))
    assert not check.market_window(probe_at(dt.datetime(2026, 10, 24, 11, 0, tzinfo=ET), cal))   # Saturday


# ------------------------------------------------------------------ Linux memory
MEM = "MemTotal:        1004000 kB\nMemAvailable:     150000 kB\nSwapTotal:       2097148 kB\nSwapFree:        2000000 kB\n"


def test_memory_and_swap():
    m = check.parse_meminfo(MEM)
    assert m["total_mb"] == 980 and m["avail_mb"] == 146
    out = {n: st for n, st, _ in check.judge_memory(m)}
    assert out == {"memory": "warn", "swap": "ok"}
    no_swap = {**m, "swap_total_mb": 0, "swap_free_mb": 0}
    assert dict((n, st) for n, st, _ in check.judge_memory(no_swap))["swap"] == "warn"
    full = {**m, "swap_free_mb": 100, "avail_mb": 50}
    assert dict((n, st) for n, st, _ in check.judge_memory(full)) == {"memory": "fail", "swap": "fail"}


# ------------------------------------------------------------------ readiness window
def test_readiness_due_regular_and_half_day():
    d, h = dt.date(2026, 10, 23), dt.date(2026, 11, 27)
    cal = cal_of(d, h, half={h})
    at = lambda day, hh, mm: probe_at(dt.datetime.combine(day, dt.time(hh, mm), ET), cal)
    assert check.readiness_due(at(d, 15, 30))
    assert not check.readiness_due(at(d, 15, 15))
    assert not check.readiness_due(at(d, 15, 45))
    assert check.readiness_due(at(h, 12, 30))
    assert not check.readiness_due(at(h, 15, 30))
    assert not check.readiness_due(at(dt.date(2026, 10, 24), 15, 30))   # no session


def test_trading_role(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "MODE_FILE", tmp_path / "mode")
    monkeypatch.setattr(check, "crontab_text", lambda: "40 15 * * 1-5 cd x && python3 daily.py --execute >> logs/cron.log 2>&1\n")
    assert check.trading_role() == {"role": "mac-cron", "trades": True}
    monkeypatch.setattr(check, "crontab_text", lambda: "#CLIVE-ON-VM# 40 15 * * 1-5 python3 daily.py --execute\n")
    assert check.trading_role() == {"role": "mac-parked", "trades": False}
    (tmp_path / "mode").write_text("shadow\n")
    assert check.trading_role() == {"role": "vm-shadow", "trades": False}
    (tmp_path / "mode").write_text("live\n")
    assert check.trading_role() == {"role": "vm-live", "trades": True}


def test_readiness_guard_already_dated_today_fails(monkeypatch, tmp_path):
    d = dt.date(2026, 10, 23)
    short, opt = tmp_path / "Stock Shorting Bot", tmp_path / "Options Bot"
    (short / "logs").mkdir(parents=True)
    (opt / "live" / "state").mkdir(parents=True)
    (short / "logs" / "last_execute.json").write_text('{"date": "2026-10-23", "orders": []}')
    monkeypatch.setattr(check, "SHORT", short)
    monkeypatch.setattr(check, "OPT", opt)
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "trading_role", lambda: {"role": "vm-live", "trades": True})
    monkeypatch.setattr(check, "vm_mode", lambda: "live")
    monkeypatch.setattr(check.platform, "system", lambda: "Linux")
    monkeypatch.setattr(check.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "active\nactive\nactive\n"})())
    monkeypatch.setattr(check.socket, "getaddrinfo", lambda *a: [])
    monkeypatch.setattr(check.Path, "read_text", _meminfo_or_real(check.Path.read_text))
    ok_acct = {"status": "ACTIVE", "trading_blocked": False, "shorting_enabled": True}
    monkeypatch.setattr(check.SHORT_API, "get", lambda path, *a, **k: {"is_open": True, "next_close": "x"})
    monkeypatch.setattr(check, "SHORT_ENV", {"APCA_API_KEY_ID": "k", "APCA_API_SECRET_KEY": "s", "SUPABASE_URL": "u", "SUPABASE_SERVICE_KEY": "k"})
    monkeypatch.setattr(check, "OPT_ENV", {"OPT_APCA_API_KEY_ID": "k", "OPT_APCA_API_SECRET_KEY": "s"})
    p = probe_at(dt.datetime(2026, 10, 23, 15, 30, tzinfo=ET), cal_of(d))
    p.cache.update(sacct=ok_acct, oacct=ok_acct)
    check.check_readiness(p)
    rows = {r["check_name"]: r for r in p.results}
    assert rows["readiness.guards"]["status"] == "fail"
    assert rows["readiness.summary"]["status"] == "fail"
    assert "already dated 2026-10-23" in rows["readiness.summary"]["message"]
    for n in ("readiness.host_awake", "readiness.locks", "readiness.keys", "readiness.schedule", "readiness.network", "readiness.alpaca"):
        assert rows[n]["status"] == "ok", (n, rows[n]["message"])


def _meminfo_or_real(real):
    def f(self, *a, **k):
        if str(self) == "/proc/meminfo":
            return MEM.replace("150000", "500000")
        return real(self, *a, **k)
    return f


# ------------------------------------------------------------------ event-alert auto-resolution
def _alert(i, kind, at, bot="shorting"):
    return {"id": i, "bot_id": bot, "kind": kind, "level": "critical", "at": at.astimezone(dt.timezone.utc).isoformat()}


def test_plan_alert_resolution():
    thu, fri = dt.date(2026, 10, 22), dt.date(2026, 10, 23)
    cal = cal_of(dt.date(2026, 10, 19), dt.date(2026, 10, 20), dt.date(2026, 10, 21), thu, fri)
    now = dt.datetime(2026, 10, 23, 11, 0, tzinfo=ET)
    alerts = [
        _alert(1, "cover_failed", dt.datetime(2026, 10, 22, 15, 56, tzinfo=ET)),
        _alert(2, "run_failed", dt.datetime(2026, 10, 22, 15, 41, tzinfo=ET)),
        _alert(3, "position_loss", dt.datetime(2026, 10, 22, 11, 0, tzinfo=ET)),      # bot-owned: never
        _alert(4, "long_exit_failed", dt.datetime(2026, 10, 23, 9, 35, tzinfo=ET)),
        _alert(5, "mystery", dt.datetime(2026, 10, 19, 10, 0, tzinfo=ET)),            # 4 later sessions: stale
        _alert(6, "mystery", dt.datetime(2026, 10, 22, 10, 0, tzinfo=ET)),            # 1 later session: kept
        _alert(7, "cover_failed", now - dt.timedelta(minutes=3)),                      # too young
        _alert(8, "vm_job_failed", dt.datetime(2026, 10, 22, 15, 45, tzinfo=ET), bot="options"),
        _alert(9, "health_x", dt.datetime(2026, 10, 19, 10, 0, tzinfo=ET), bot="health"),
    ]
    results = [{"component": "positions", "check_name": "shorting.leftover_shorts", "status": "ok"},
               {"component": "positions", "check_name": "shorting.longs_window", "status": "fail"},
               {"component": "scheduler", "check_name": "options.daily", "status": "fail"}]
    got = {a["id"]: r for a, r in check.plan_alert_resolution(alerts, results, cal, now)}
    assert set(got) == {1, 2, 5}
    assert got[1] == "no shorts left open"
    assert "2026-10-23" in got[2]

    # shorts still open: the cover alert stays; options scheduler recovered: the VM alert goes
    results[0]["status"] = "fail"
    results[2]["status"] = "ok"
    got = {a["id"] for a, _ in check.plan_alert_resolution(alerts, results, cal, now)}
    assert got == {2, 5, 8}


def test_plan_alert_resolution_same_day_one_day_kind_waits():
    fri = dt.date(2026, 10, 23)
    cal = cal_of(fri)
    now = dt.datetime(2026, 10, 23, 16, 30, tzinfo=ET)
    a = [_alert(1, "scan_failed", dt.datetime(2026, 10, 23, 15, 46, tzinfo=ET))]
    assert check.plan_alert_resolution(a, [], cal, now) == []


def test_resolution_patch_is_idempotent(monkeypatch):
    """Every PATCH carries resolved_at=is.null, so an alert the bot already resolved is untouched."""
    fri = dt.date(2026, 10, 23)
    cal = cal_of(dt.date(2026, 10, 22), fri)
    now = dt.datetime(2026, 10, 23, 11, 0, tzinfo=ET)
    calls = []
    monkeypatch.setattr(check.SB, "ok", True)
    monkeypatch.setattr(check.SB, "try_get", lambda t, q, default=None: [_alert(1, "run_failed", dt.datetime(2026, 10, 22, 15, 41, tzinfo=ET))])
    monkeypatch.setattr(check.SB, "patch", lambda t, q, body: calls.append((t, q, body)))
    p = probe_at(now, cal)
    p.write = True
    check.check_alert_resolution(p)
    assert calls and calls[0][0] == "alerts" and "resolved_at=is.null" in calls[0][1] and "id=in.(1)" in calls[0][1]
    row = p.results[-1]
    assert row["check_name"] == "alerts.auto_resolve" and row["status"] == "ok" and "resolved 1" in row["message"]
    # dry run: plans, never patches
    calls.clear()
    p2 = probe_at(now, cal)
    check.check_alert_resolution(p2)
    assert calls == [] and "would resolve 1" in p2.results[-1]["message"]
