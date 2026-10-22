"""The pre-deployment review's race reproductions: a protective stop fills around the 15:55
cancel. Before the fix the stale cover bought the short back a second time and left a long."""
from __future__ import annotations

import datetime as dt

import daily
from shortingbot import broker


def test_cancel_raises_then_no_stale_cover(fake, monkeypatch):
    fake.short("ASST", 51, 20.0)

    # a protective stop is open; it fills while we try to cancel it, so the cancel raises
    def cancel(symbols=None):
        fake.pos.pop("ASST", None)           # stop filled -> flat
        raise RuntimeError("order is not cancelable (filled)")
    monkeypatch.setattr(broker, "cancel_open_orders", cancel)
    cutoff = dt.datetime.now(broker.ET) + dt.timedelta(minutes=5)
    out = daily.cover_all("r1", True, cutoff)
    assert fake.signed_holdings().get("ASST", 0) == 0          # flat, not long
    assert out["sent"] == [] and out["left"] == {} and fake.sent("buy") == []


def test_stop_fills_between_read_and_cancel(fake, monkeypatch):
    fake.short("ASST", 51, 20.0)

    def cancel(symbols=None):
        fake.pos.pop("ASST", None)           # stop filled just before cancel: nothing open -> 0
        return 0
    monkeypatch.setattr(broker, "cancel_open_orders", cancel)
    cutoff = dt.datetime.now(broker.ET) + dt.timedelta(minutes=5)
    out = daily.cover_all("r1", True, cutoff)
    assert fake.signed_holdings().get("ASST", 0) == 0
    assert out["sent"] == [] and out["left"] == {}
