"""Round 3: event-alert hygiene (config.AUTO_RESOLVE_EVENT_ALERTS)."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import daily
import sync
from shortingbot import alerts, broker, config, db


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body, text

    def json(self):
        return self._body


@pytest.fixture
def patches(monkeypatch):
    """Supabase on, every PATCH captured; each answers one resolved row of the filtered kind."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test")
    calls = []

    def fake_request(method, url, params=None, headers=None, data=None, timeout=None):
        calls.append({"method": method, "url": url, "params": dict(params or {}),
                      "data": json.loads(data) if data else None})
        if method == "PATCH":
            return Resp(200, [{"id": 1, "kind": "x"}])
        return Resp(200, [])
    monkeypatch.setattr(db.requests, "request", fake_request)
    return calls


def test_off_by_default_sends_nothing(patches):
    assert config.AUTO_RESOLVE_EVENT_ALERTS is False
    assert alerts.resolve_events(alerts.SHORT_STATE_KINDS) == 0
    assert patches == []


def test_resolve_events_filters(patches, monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    before = dt.datetime(2026, 10, 23, 13, 0, tzinfo=dt.timezone.utc)
    assert alerts.resolve_events({"cover_failed", "leftover_covered"}, before=before, note="n") == 1
    p = patches[-1]
    assert p["method"] == "PATCH" and p["url"].endswith("/alerts")
    assert p["params"]["kind"] == "in.(cover_failed,leftover_covered)"
    assert p["params"]["resolved_at"] == "is.null" and p["params"]["bot_id"] == "eq.shorting"
    assert p["params"]["at"] == f"lt.{before.isoformat()}"
    assert p["data"]["resolution"] == "n" and p["data"]["resolved_at"]


def test_sweep_skips_condition_kinds_and_tracked_rows(patches, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    json.dump({"drawdown_halt:*": {"id": 77, "message": "m", "at": "t"}}, open(alerts.STATE, "w"))
    alerts.resolve_events(None, exclude_kinds=alerts.SHORT_STATE_KINDS)
    p = patches[-1]["params"]
    assert p["kind"].startswith("not.in.(")
    for k in ("position_loss", "book_loss", "cover_failed", "leftover_covered"):
        assert k in p["kind"]
    assert p["id"] == "not.in.(77)"          # monitor's open drawdown row is its own to resolve


def test_resolve_events_never_raises(monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test")
    monkeypatch.setattr(db, "update_where", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert alerts.resolve_events({"no_picks"}) == 0


def test_update_where_refuses_empty_filter(patches):
    assert db.update_where("alerts", {}, {"resolved_at": "x"}) == [] and patches == []


def test_missing_resolution_column_is_dropped(monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test")
    sent = []

    def fake_request(method, url, params=None, headers=None, data=None, timeout=None):
        sent.append(data)
        if '"resolution"' in data:
            return Resp(400, text="Could not find the 'resolution' column of 'alerts' in the schema cache")
        return Resp(200, [{"id": 3, "kind": "no_picks"}])
    monkeypatch.setattr(db.requests, "request", fake_request)
    assert alerts.resolve_events({"no_picks"}, note="x") == 1
    assert len(sent) == 2 and "resolution" not in sent[1]


# ---------------------------------------------------------------- daily.py: the leftover check confirms
def test_leftover_check_clean_resolves_short_kinds(fake, monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    seen = []
    monkeypatch.setattr(alerts, "resolve_events", lambda kinds, **kw: seen.append((set(kinds), kw)) or 1)
    out = daily.cover_leftovers(None, "2026-10-23", True, dt.datetime.now(broker.ET) + dt.timedelta(hours=1),
                                when=" (10:00 safety net)")
    assert out["leftovers"] == {} and seen
    kinds, kw = seen[0]
    assert {"cover_failed", "leftover_covered", "close_missed"} <= kinds
    assert kw["before"] == daily.RUN_STARTED         # alerts this run raised stay up
    assert "10:00" in kw["note"]


def test_leftover_check_with_leftovers_does_not_resolve(fake, monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    fake.short("OLD", 10, 10.0)
    seen = []
    monkeypatch.setattr(alerts, "resolve_events", lambda kinds, **kw: seen.append(kinds) or 0)
    daily.cover_leftovers(None, "2026-10-23", True, dt.datetime.now(broker.ET) + dt.timedelta(hours=1))
    assert seen == [] and "OLD" not in fake.pos


def test_leftover_check_dry_run_does_not_resolve(fake, monkeypatch):
    seen = []
    monkeypatch.setattr(alerts, "resolve_events", lambda kinds, **kw: seen.append(kinds) or 0)
    daily.cover_leftovers(None, "2026-10-23", False, dt.datetime.now(broker.ET) + dt.timedelta(hours=1))
    assert seen == []


# ---------------------------------------------------------------- sync.py at 16:15
def _sync_env(monkeypatch, fake, fdb):
    monkeypatch.setattr(broker, "fills", lambda after: [])
    monkeypatch.setattr(db, "delete", lambda *a, **k: None)
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", True)
    calls = []
    monkeypatch.setattr(alerts, "resolve_events",
                        lambda kinds, **kw: calls.append((None if kinds is None else set(kinds), kw)) or 0)
    return calls


def test_sync_flat_book_resolves_everything(fake, fdb, monkeypatch):
    calls = _sync_env(monkeypatch, fake, fdb)
    assert sync.main() == 0
    sweep = [c for c in calls if c[0] is None]
    assert sweep and sweep[0][1]["exclude_kinds"] >= alerts.SHORT_STATE_KINDS
    midnight = sweep[0][1]["before"]
    assert midnight.hour == 0 and midnight.tzinfo is not None
    assert any(c[0] == set(alerts.SHORT_STATE_KINDS) for c in calls)
    assert any(c[0] == set(alerts.LONG_STATE_KINDS) for c in calls)


def test_sync_with_a_short_keeps_cover_failed(fake, fdb, monkeypatch):
    calls = _sync_env(monkeypatch, fake, fdb)
    fake.short("STUCK", 10, 10.0)
    assert sync.main() == 0
    assert not any(c[0] == set(alerts.SHORT_STATE_KINDS) for c in calls)


def test_sync_old_sleeve_long_keeps_long_exit_failed(fake, fdb, monkeypatch):
    calls = _sync_env(monkeypatch, fake, fdb)
    fake.pos["LNG"] = {"qty": 5, "entry": 10.0, "price": 10.0}
    daily.save_picks(daily.long_state_path(), {"entry_date": "2026-01-02", "status": "exit_incomplete",
                                               "orders": [{"ticker": "LNG", "qty": 5, "status": "filled"}]})
    assert sync.main() == 0
    assert not any(c[0] == set(alerts.LONG_STATE_KINDS) for c in calls)


def test_sync_todays_sleeve_long_is_fine(fake, fdb, monkeypatch):
    calls = _sync_env(monkeypatch, fake, fdb)
    fake.pos["LNG"] = {"qty": 5, "entry": 10.0, "price": 10.0}
    today = dt.datetime.now(broker.ET).date().isoformat()
    daily.save_picks(daily.long_state_path(), {"entry_date": today, "status": "entered",
                                               "orders": [{"ticker": "LNG", "qty": 5, "status": "filled"}]})
    assert sync.main() == 0
    assert any(c[0] == set(alerts.LONG_STATE_KINDS) for c in calls)


def test_sync_resolves_condition_alerts_on_names_gone(fake, fdb, monkeypatch):
    _sync_env(monkeypatch, fake, fdb)
    json.dump({"position_loss:GONE": {"id": None, "message": "m", "at": "t"},
               "position_loss:HELD": {"id": None, "message": "m", "at": "t"}}, open(alerts.STATE, "w"))
    fake.short("HELD", 10, 10.0)
    assert sync.main() == 0
    assert set(alerts.open_alerts()) == {"position_loss:HELD"}


def test_sync_flag_off_touches_no_alert(fake, fdb, monkeypatch):
    calls = _sync_env(monkeypatch, fake, fdb)
    monkeypatch.setattr(config, "AUTO_RESOLVE_EVENT_ALERTS", False)
    json.dump({"position_loss:GONE": {"id": None, "message": "m", "at": "t"}}, open(alerts.STATE, "w"))
    assert sync.main() == 0
    assert calls == [] and set(alerts.open_alerts()) == {"position_loss:GONE"}
