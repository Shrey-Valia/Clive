"""Round 3 flag: config.ROUND3_SHORT_ENTRY_ORDER = "opg" (opening-auction short entry). Off by default.

research/round3/REPORT.md: market-on-open entry (with the broker stop off) beat the 09:31 market
entry in both halves of the 2016-2026 backtest. These tests pin the wiring: the flag off changes
nothing; on, picks are sold short in the auction before 09:28, names the overnight sleeve still
holds (and anything the auction skipped, or Alpaca let die unfilled) go through 09:31, a resting
auction order is never entered twice, and stops / PDT / halts / dry runs behave."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import daily
from conftest import ET, run_daily
from shortingbot import config

pytestmark = pytest.mark.usefixtures("legacy_v2_config")


def today():
    return dt.datetime.now(ET).date().isoformat()


def write_picks(tickers, scan=20.0):
    daily.save_picks(config.PICKS_FILE, {
        "run_id": 42, "scan_date": "2026-10-21", "trade_date": today(), "tickers": tickers,
        "picks": [{"ticker": t, "score": 80, "scan_price": scan, "target_pct": 10.0, "scan_qty": 50} for t in tickers]})


@pytest.fixture
def premarket(fake, monkeypatch):
    """The --open job started at ~09:20: the session opens in 8 minutes + the OPG cutoff."""
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)
    fake.session = (now + dt.timedelta(minutes=10), now + dt.timedelta(hours=6))
    for t in ("A", "B"):
        fake.prices[t] = 20.0
    write_picks(["A", "B"])
    return fake


@pytest.fixture
def opg(monkeypatch):
    monkeypatch.setattr(config, "ROUND3_SHORT_ENTRY_ORDER", "opg")


def kinds(fake):
    return [(e[0], e[1], e[2]) for e in fake.events if e[0] in ("opg", "submit")]


def test_default_is_off():
    assert config.ROUND3_SHORT_ENTRY_ORDER == "market"


def test_flag_off_shorts_at_0931_as_before(premarket, fdb):
    assert run_daily("--open", "--execute") == 0
    assert kinds(premarket) == [("submit", "sell", "A"), ("submit", "sell", "B")]
    assert premarket.pos["A"]["qty"] == -50 and premarket.pos["B"]["qty"] == -50


def test_opg_shorts_in_the_auction_and_not_again(premarket, fdb, opg):
    assert run_daily("--open", "--execute") == 0
    assert kinds(premarket) == [("opg", "sell", "A"), ("opg", "sell", "B")]
    assert premarket.pos["A"]["qty"] == -50 and premarket.pos["B"]["qty"] == -50
    picks = json.load(open(config.PICKS_FILE))
    assert {e["ticker"] for e in picks["entered"]} == {"A", "B"}
    assert not [r for r in fdb.rows("alerts") if r["kind"] == "overnight_leftover"]


def test_opg_after_the_cutoff_uses_0931(premarket, fdb, opg):
    now = dt.datetime.now(ET)
    premarket.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    assert run_daily("--open", "--execute") == 0
    assert kinds(premarket) == [("submit", "sell", "A"), ("submit", "sell", "B")]


def test_sleeve_name_goes_through_0931(premarket, fdb, opg, monkeypatch):
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", True)
    monkeypatch.setattr(config, "OVERNIGHT_LONG_EXIT_ORDER", "opg")   # the round-3 pairing
    daily.save_picks(daily.long_state_path(), {
        "entry_date": "2026-10-21", "exit_date": today(), "run_id": 41, "kind": "limit", "status": "entered",
        "orders": [{"ticker": "A", "qty": 25, "id": "bA", "status": "filled", "filled_qty": 25}]})
    premarket.pos["A"] = {"qty": 25, "entry": 20.0, "price": 20.0}
    assert run_daily("--open", "--execute") == 0
    ev = [e for e in premarket.events if e[0] in ("opg", "submit", "long")]
    # B in the auction first; A (still long at 09:20) is sold by the sleeve, then shorted at 09:31
    assert ev[0] == ("opg", "sell", "B", 50)
    assert ("submit", "sell", "A", 50) in ev
    assert ev.index(("submit", "sell", "A", 50)) > max(i for i, e in enumerate(ev) if e[0] == "long")
    assert premarket.pos["A"]["qty"] == -50 and premarket.pos["B"]["qty"] == -50


def test_expired_auction_order_falls_back_to_0931(premarket, fdb, opg):
    premarket.opg_mode["A"] = "expire"
    assert run_daily("--open", "--execute") == 0
    assert kinds(premarket) == [("opg", "sell", "A"), ("opg", "sell", "B"), ("submit", "sell", "A")]
    assert premarket.pos["A"]["qty"] == -50


def test_resting_auction_order_is_not_entered_twice(premarket, fdb, opg):
    premarket.opg_mode["A"] = "rest"
    assert run_daily("--open", "--execute") == 0
    assert kinds(premarket) == [("opg", "sell", "A"), ("opg", "sell", "B")]
    assert "A" not in premarket.pos


def test_rejected_auction_order_falls_back_to_0931(premarket, fdb, opg, monkeypatch):
    real = premarket.submit_opg

    def reject_a(ticker, qty, side, client_order_id=None):
        if ticker == "A":
            raise RuntimeError("opg not allowed")
        return real(ticker, qty, side, client_order_id)
    monkeypatch.setattr(daily.broker, "submit_opg", reject_a)
    assert run_daily("--open", "--execute") == 0
    assert ("submit", "sell", "A") in kinds(premarket) and premarket.pos["A"]["qty"] == -50


def test_gap_up_in_premarket_is_left_to_0931(premarket, fdb, opg):
    premarket.prices["A"] = 30.0          # +50% over the $20 scan price: skipped in the auction
    assert run_daily("--open", "--execute") == 0
    k = kinds(premarket)
    assert ("opg", "sell", "A") not in k and k[0] == ("opg", "sell", "B")
    assert "A" not in premarket.pos       # 09:31 re-check skips it too (same price)


def test_stops_placed_on_auction_fills(premarket, fdb, opg, monkeypatch):
    monkeypatch.setattr(config, "BROKER_STOP_PCT", 35)
    assert run_daily("--open", "--execute") == 0
    stops = sorted((e[1], e[2], e[3]) for e in premarket.events if e[0] == "stop")
    assert stops == [("A", 50, 27.0), ("B", 50, 27.0)]


def test_pdt_cap_or_halt_disables_the_auction(premarket, fdb, opg, monkeypatch):
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 50.0)
    assert run_daily("--open", "--execute") == 0
    assert [k[0] for k in kinds(premarket)] == ["submit", "submit"]


def test_pdt_cap_disables_the_auction(premarket, fdb, opg):
    premarket.acct_extra["daytrade_count"] = 0          # account reports PDT data, equity < $25k
    assert run_daily("--open", "--execute") == 0
    assert not any(k[0] == "opg" for k in kinds(premarket))


def test_dry_run_sends_nothing(premarket, fdb, opg, caplog):
    assert run_daily("--open") == 0
    assert premarket.orders == []
    assert "DRY RUN would SELL OPG A 50" in caplog.text
