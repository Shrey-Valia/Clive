"""Round 3 failure-mode fixes: the 15:55 cover survives a transient Alpaca error, and a 5xx on
an order POST is looked up by client_order_id instead of being recorded as rejected."""
from __future__ import annotations

import datetime as dt

import pytest
import requests

import daily
from conftest import ET, gainer, run_daily
from shortingbot import broker


def closing_in(fake, minutes):
    now = dt.datetime.now(ET)
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=minutes, seconds=1))


def test_1555_cover_retries_when_positions_fail(fake, fdb, monkeypatch, scan_inputs):
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    scan_inputs.append(gainer("G0"))
    fake.short("A", 10, 20.0)
    closing_in(fake, 6)
    real, calls = daily.cover_all, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) <= 2:                               # positions unreadable (retry() gave up) twice
            raise requests.exceptions.ConnectionError("502 bad gateway")
        return real(*a, **k)
    monkeypatch.setattr(daily, "cover_all", flaky)
    assert run_daily("--execute") == 0
    assert len(calls) == 3
    assert fake.pos == {} and [e for e in fake.events if e[0] == "submit"] == [("submit", "buy", "A", 10)]


def test_1555_cover_gives_up_near_the_cutoff(fake, fdb, monkeypatch):
    monkeypatch.setattr(broker, "signed_holdings", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        daily.cover_with_retries(None, True, dt.datetime.now(ET) + dt.timedelta(seconds=5))


class FakeAPIError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} server error")
        self.status_code = code


class Req:
    client_order_id = "v2-cover-X-A-0"


def test_send_looks_up_an_order_after_a_5xx(monkeypatch):
    found = object()

    class C:
        def submit_order(self, req):
            raise FakeAPIError(503)

        def get_order_by_client_id(self, coid):
            assert coid == "v2-cover-X-A-0"
            return found
    monkeypatch.setattr(broker, "client", lambda: C())
    assert broker._send(Req()) is found


def test_send_reraises_a_4xx(monkeypatch):
    class C:
        def submit_order(self, req):
            raise FakeAPIError(403)

        def get_order_by_client_id(self, coid):
            raise AssertionError("a 4xx is a real rejection: no lookup")
    monkeypatch.setattr(broker, "client", lambda: C())
    with pytest.raises(FakeAPIError):
        broker._send(Req())


def test_send_5xx_and_not_found_raises_the_original(monkeypatch):
    class C:
        def submit_order(self, req):
            raise FakeAPIError(500)

        def get_order_by_client_id(self, coid):
            raise FakeAPIError(404)
    monkeypatch.setattr(broker, "client", lambda: C())
    with pytest.raises(FakeAPIError, match="500"):
        broker._send(Req())
