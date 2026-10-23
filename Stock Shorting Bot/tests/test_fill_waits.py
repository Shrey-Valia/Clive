"""2026-10-23: Alpaca paper took 1-3 min to fill the 09:31 leftover covers and ~19 s to confirm
cancels, so 30 s / 10 s waits cancelled orders that were about to fill. Leftover covers now wait
LEFTOVER_FILL_WAIT_SECONDS per pass and every cancel waits CANCEL_WAIT_SECONDS."""
from __future__ import annotations

import datetime as dt

import daily
from shortingbot import broker, config

ET = broker.ET


def test_leftover_cover_uses_long_fill_wait(monkeypatch):
    seen = {}
    monkeypatch.setattr(daily, "leftover_shorts", lambda today: {"ASST": -51})

    def fake_cover_all(run_id, execute, cutoff, **kw):
        seen.update(kw)
        return {"planned": [], "sent": [], "problems": [], "left": {}}

    monkeypatch.setattr(daily, "cover_all", fake_cover_all)
    monkeypatch.setattr(daily.alerts, "raise_alert", lambda *a, **k: True)
    daily.cover_leftovers(None, "2026-10-23", True, dt.datetime.now(ET) + dt.timedelta(hours=1))
    assert seen["fill_wait"] == config.LEFTOVER_FILL_WAIT_SECONDS >= 120
    assert seen["only"] == {"ASST"}


def test_cancel_wait_passed_to_broker(monkeypatch):
    waits = []
    monkeypatch.setattr(broker, "cancel_open_orders", lambda symbols: 1)
    monkeypatch.setattr(broker, "wait_no_open_orders", lambda symbols, seconds=10: waits.append(seconds) or True)
    monkeypatch.setattr(broker, "signed_holdings", lambda: {"ASST": -51.0})
    covers, short_now = daily.fresh_covers(["ASST"], "leftover", [])
    assert waits == [config.CANCEL_WAIT_SECONDS] and config.CANCEL_WAIT_SECONDS >= 25
    assert short_now == {"ASST": -51.0} and covers and covers[0]["ticker"] == "ASST"
