"""Supabase helpers, heartbeat split, run_events handler, alerts, monitor, sync run_id tagging."""
from __future__ import annotations

import logging

import pytest

import monitor
import sync
from shortingbot import alerts, config, db


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body, text

    def json(self):
        return self._body


@pytest.fixture
def supa(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test")


def test_db_noop_when_unset(monkeypatch):
    assert db.insert("short_runs", {"a": 1}) == [] and db.select("bots", {}) == []
    db.update("bots", {"id": "x"}, {"a": 1})
    db.heartbeat("ok", "m")


def test_db_drops_missing_column_and_retries(supa, monkeypatch):
    calls = []

    def fake_request(method, url, params=None, headers=None, data=None, timeout=None):
        calls.append(data)
        if '"trade_date"' in data:
            return Resp(400, text="{\"code\":\"PGRST204\",\"message\":\"Could not find the 'trade_date' column of 'short_runs' in the schema cache\"}")
        return Resp(201, [{"id": 5}])
    monkeypatch.setattr(db.requests, "request", fake_request)
    assert db.insert("short_runs", {"run_date": "2026-10-22", "trade_date": "2026-10-23"}) == [{"id": 5}]
    assert len(calls) == 2 and "trade_date" not in calls[1]


def test_db_never_raises(supa, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("supabase down")
    monkeypatch.setattr(db.requests, "request", boom)
    assert db.insert("short_runs", {"a": 1}) == []
    db.update("short_runs", {"id": 1}, {"a": 1})
    db.delete("positions", {"bot_id": "x"})
    assert db.select("bots", {}) == []
    db.heartbeat("ok", "still fine")


# ---------------------------------------------------------------- heartbeat split
@pytest.fixture
def bots(monkeypatch, supa):
    state = {"row": {"last_run_status": None, "last_sync_status": None}, "writes": []}
    monkeypatch.setattr(db, "select", lambda t, p: [dict(state["row"])] if state["row"] is not None else [])

    def upd(table, match, values):
        state["writes"].append(values)
        if state["row"] is not None:
            state["row"].update({k: v for k, v in values.items() if k.endswith("_status")})
    monkeypatch.setattr(db, "update", upd)
    return state


def test_good_sync_does_not_hide_bad_run(bots):
    db.heartbeat("error", "run failed", source="run")
    db.heartbeat("ok", "synced", source="sync")
    last = bots["writes"][-1]
    assert last["run_state"] == "error" and last["last_sync_status"] == "ok" and "last_run_status" not in last


def test_sync_failure_shows(bots):
    db.heartbeat("ok", "run fine", source="run")
    db.heartbeat("error", "sync failed", source="sync")
    assert bots["writes"][-1]["run_state"] == "error"
    db.heartbeat("ok", "run fine", source="run")
    assert bots["writes"][-1]["run_state"] == "error"      # the sync is still broken


def test_heartbeat_before_migration(bots):
    bots["row"] = None                                     # select fails: columns don't exist yet
    db.heartbeat("ok", "synced", source="sync")
    assert "run_state" not in bots["writes"][-1]
    db.heartbeat("error", "sync failed", source="sync")
    assert bots["writes"][-1]["run_state"] == "error"
    db.heartbeat("warning", "run", source="run")
    assert bots["writes"][-1]["run_state"] == "warning"


def test_worst():
    assert db.worst("ok", "warning") == "warning" and db.worst("error", "ok") == "error"
    assert db.worst(None, "ok") == "ok" and db.worst() == "idle"


# ---------------------------------------------------------------- run_events
def make_logger():
    lg = logging.getLogger("shortingbot.test")
    lg.propagate = False                                   # keep pytest's own handlers out of it
    lg.handlers.clear()
    h = db.RunEventHandler()
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    return lg, h


def test_run_events_buffers_warnings_and_milestones(monkeypatch, supa):
    lg, h = make_logger()
    db.set_step("scan")
    lg.info("noise")
    lg.info("scan started", extra={"milestone": True})
    lg.warning("only 2 names")
    lg.error("cover failed", extra={"step": "cover"})
    assert [(e["level"], e["step"], e["message"]) for e in h.buffer] == [
        ("INFO", "scan", "scan started"), ("WARNING", "scan", "only 2 names"), ("ERROR", "cover", "cover failed")]
    got = []
    monkeypatch.setattr(db, "insert", lambda t, rows, upsert_on=None: got.extend(rows) or rows)
    assert h.flush_to(9) == 3
    assert {r["run_id"] for r in got} == {9} and {r["bot_id"] for r in got} == {"shorting"}
    assert h.buffer == []


def test_run_events_handler_swallows_errors(monkeypatch, supa):
    lg, h = make_logger()
    lg.warning("x")

    def boom(*a, **k):
        raise RuntimeError("supabase exploded")
    monkeypatch.setattr(db, "insert", boom)
    assert h.flush_to(1) == 0                              # no exception

    class Bad:
        def __str__(self):
            raise ValueError("unprintable")
    lg.warning("%s", Bad())                                # getMessage raises inside emit
    monkeypatch.setattr(h, "buffer", None)                 # even a broken buffer
    lg.warning("still logging")


def test_run_events_ignore_own_supabase_errors(supa, monkeypatch):
    lg, h = make_logger()
    logging.getLogger("shortingbot").addHandler(h)
    try:
        logging.getLogger("shortingbot.db").error("supabase short_runs insert failed")
        assert h.buffer == []
    finally:
        logging.getLogger("shortingbot").removeHandler(h)


# ---------------------------------------------------------------- monitor
def pos(t, qty, entry, price):
    pl = (price - entry) * qty
    return {"ticker": t, "qty": float(qty), "side": "short" if qty < 0 else "long", "avg_entry_price": entry,
            "current_price": price, "market_value": qty * price, "unrealized_pl": pl,
            "unrealized_plpc": pl / abs(qty * entry)}


def test_monitor_thresholds():
    hits, book = monitor.check([pos("A", -10, 10.0, 12.49), pos("B", -10, 10.0, 12.5)], 1_000_000)
    assert list(hits) == ["B"] and book is None
    hits, book = monitor.check([pos("A", -100, 10.0, 12.0)], 4_000)     # -$200 = 5% of 4,000
    assert book and list(hits) == []
    hits, book = monitor.check([pos("A", -100, 10.0, 12.0)], 4_001)
    assert book is None


def test_monitor_dedupes_and_resolves(monkeypatch, fdb):
    pushes = []
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setattr(alerts.requests, "post", lambda url, **k: pushes.append(url))
    bad = [pos("A", -10, 10.0, 13.0)]
    out = monitor.run(bad, 1_000)
    assert out["raised"] == ["A"] and len(pushes) == 1
    assert monitor.run(bad, 1_000)["raised"] == []        # still open: no repeat, no push
    assert len(pushes) == 1 and len(fdb.rows("alerts")) == 1
    out = monitor.run([pos("A", -10, 10.0, 10.5)], 1_000)
    assert out["resolved"] == ["position_loss:A"]
    assert fdb.updates[-1][0] == "alerts" and "resolved_at" in fdb.updates[-1][2]
    assert monitor.run(bad, 1_000)["raised"] == ["A"]     # comes back: a new alert
    assert len(pushes) == 2


def test_monitor_book_alert_dedupe(fdb):
    bad = [pos("A", -100, 10.0, 11.0)]                    # -$100 on $1,000 = 10%
    assert "book" in monitor.run(bad, 1_000)["raised"]
    assert "book" not in monitor.run(bad, 1_000)["raised"]
    assert monitor.run([], 1_000)["resolved"] == ["book_loss:*"]


def test_monitor_window(fake):
    import datetime as dt
    et = fake.session[0].tzinfo
    day = dt.datetime(2026, 10, 22, tzinfo=et)
    sess = (day.replace(hour=9, minute=30), day.replace(hour=16))
    assert not monitor.in_window(day.replace(hour=9, minute=30), sess)
    assert monitor.in_window(day.replace(hour=9, minute=45), sess)
    assert monitor.in_window(day.replace(hour=15, minute=45), sess)
    assert not monitor.in_window(day.replace(hour=15, minute=46), sess)
    assert not monitor.in_window(day.replace(hour=12), None)
    half = (sess[0], day.replace(hour=13))
    assert not monitor.in_window(day.replace(hour=12, minute=50), half)


def test_monitor_never_trades(fake, fdb, monkeypatch):
    fake.short("A", 10, 10.0, 20.0)
    assert monitor.main(["--now"]) == 0
    assert fake.orders == [] and any(r["kind"] == "position_loss" for r in fdb.rows("alerts"))


# ---------------------------------------------------------------- sync
def test_sync_tags_trades_with_opening_run(monkeypatch, supa):
    fills = [
        {"symbol": "A", "price": "20", "qty": "10", "side": "sell_short", "transaction_time": "t1", "order_id": "o-open"},
        {"symbol": "A", "price": "18", "qty": "10", "side": "buy", "transaction_time": "t2", "order_id": "o-cover"},
    ]
    trades = sync.round_trips(fills)
    assert trades[0]["entry_order_id"] == "o-open" and trades[0]["pnl"] == 20.0
    monkeypatch.setattr(db, "select", lambda t, p: [{"id": "o-open", "run_id": 42}] if "o-open" in p["id"] else [])
    tagged = sync.tag_run_ids(trades)
    assert tagged[0]["run_id"] == 42 and "entry_order_id" not in tagged[0]


def test_sync_writes_sync_heartbeat_and_row(fake, fdb, monkeypatch):
    from shortingbot import broker
    monkeypatch.setattr(broker, "fills", lambda after: [])
    monkeypatch.setattr(db, "delete", lambda *a, **k: None)
    assert sync.main() == 0
    assert fdb.heartbeats[-1][0] == "sync" and fdb.heartbeats[-1][1] == "ok"
    row = fdb.rows("sync_runs")[0]
    assert row["status"] == "ok" and row["n_positions"] == 0 and row["equity"] == 10_000.0

    monkeypatch.setattr(broker, "account", lambda: (_ for _ in ()).throw(RuntimeError("alpaca down")))
    assert sync.main() == 1
    assert fdb.heartbeats[-1][:2] == ("sync", "error") and fdb.rows("sync_runs")[-1]["status"] == "error"
