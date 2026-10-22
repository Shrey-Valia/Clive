"""Selection gates, sizing, gross cap, open re-checks and the PDT rule. Pure functions."""
from __future__ import annotations

import pytest

from conftest import qty_for
from shortingbot import config, strategy


def row(t, score=80.0, close=20.0, adv=50e6, **kw):
    return {"ticker": t, "short_score": score, "close": close, "adv20_usd": adv, "exclude_reason": None, **kw}


def test_v2_config_values():
    assert config.STRATEGIES == ["short_intraday"]
    assert config.STRATEGY_VERSION == "intraday_v2"
    assert config.MIN_SHORT_SCORE == 70 and config.MIN_PRICE_AT_SCAN == 10 and config.MIN_ADV20_USD == 10_000_000
    assert config.SIZE_PCT_PER_NAME == config.SIZE_PCT_MAX == config.SIZE_PCT_MIN == 10.0
    assert config.MAX_GROSS_PCT == 50
    assert config.BROKER_STOP_PCT is None and config.DAILY_LOSS_HALT_PCT is None and config.DRAWDOWN_HALT_PCT is None
    assert config.PDT_OVERFLOW == "skip"
    assert config.WEIGHTS["ftd"] == 0
    assert config.CATALYST_EXCLUDE and config.SQUEEZE_EXCLUDE_SI and config.SQUEEZE_EXCLUDE_DTC


@pytest.mark.parametrize("r, reason", [
    (row("A", score=69.9), "score below 70"),
    (row("A", score=70.0), None),
    (row("A", close=9.99), "price below $10"),
    (row("A", close=10.0), None),
    (row("A", adv=9_999_999), "ADV20 $10.0M below $10M"),
    (row("A", adv=None), "no ADV20 data"),
    (row("A", adv=10_000_000), None),
])
def test_gates(r, reason):
    assert strategy.gate_reason(r) == reason


def test_select_equal_10pct_and_at_most_5():
    ranked = [row(f"T{i}") for i in range(8)]
    picks = strategy.select(ranked, 10_000)
    assert [p["ticker"] for p in picks] == ["T0", "T1", "T2", "T3", "T4"]
    assert all(p["target_pct"] == 10.0 and p["target_qty"] == -50 for p in picks)
    assert sum(p["target_pct"] for p in picks) <= config.MAX_GROSS_PCT


@pytest.mark.parametrize("n_good", [0, 1, 3, 5])
def test_select_zero_to_five(n_good):
    ranked = [row(f"G{i}") for i in range(n_good)] + [row(f"B{i}", score=50) for i in range(10)]
    picks = strategy.select(ranked, 10_000)
    assert len(picks) == n_good
    assert n_good == 5 or all(r["exclude_reason"] == "score below 70" for r in ranked if r["ticker"].startswith("B"))


def test_select_skips_scoring_exclusions():
    ranked = [row("X", exclude_reason="hard to borrow on Alpaca"), row("Y")]
    assert [p["ticker"] for p in strategy.select(ranked, 10_000)] == ["Y"]


def test_gross_cap(monkeypatch):
    monkeypatch.setattr(config, "MAX_GROSS_PCT", 25.0)
    ranked = [row(f"T{i}") for i in range(5)]
    picks = strategy.select(ranked, 10_000)
    assert [p["target_pct"] for p in picks] == [10.0, 10.0, 5.0]
    assert ranked[3]["exclude_reason"] == "gross cap reached"


def test_plan_cover_only_shorts():
    assert strategy.plan_cover({"A": -10, "B": 5, "C": -1}) == [
        {"ticker": "A", "side": "buy", "qty": 10, "purpose": "cover"},
        {"ticker": "C", "side": "buy", "qty": 1, "purpose": "cover"}]


def pick(t, scan=20.0):
    return {"ticker": t, "scan_price": scan, "target_pct": 10.0}


def test_recheck_at_open_filters():
    picks = [pick("OK"), pick("HTB"), pick("NOSH"), pick("CHEAP"), pick("GAP"), pick("NOPX"), pick("HELD"),
             pick("GAP40", 10.0)]
    borrow = {t: {"shortable": True, "easy_to_borrow": True} for t in [p["ticker"] for p in picks]}
    borrow["HTB"] = {"shortable": True, "easy_to_borrow": False}
    borrow["NOSH"] = {"shortable": False, "easy_to_borrow": False}
    prices = {"OK": 21.0, "HTB": 20, "NOSH": 20, "CHEAP": 9.5, "GAP": 28.2, "NOPX": None, "HELD": 20, "GAP40": 14.0}
    entries, skips = strategy.recheck_at_open(picks, borrow, prices, {"HELD": -5}, 10_000)
    assert [e["ticker"] for e in entries] == ["OK", "GAP40"]          # +40.0% exactly is allowed
    assert entries[0]["qty"] == qty_for(10_000, 10, 21.0)
    why = {s["ticker"]: s["reason"] for s in skips}
    assert "hard to borrow" in why["HTB"] and "not shortable" in why["NOSH"]
    assert "below $10" in why["CHEAP"] and "gapped up 41%" in why["GAP"]
    assert why["NOPX"] == "no price at the open" and "already held" in why["HELD"]


def test_recheck_gross_cap_counts_existing_shorts():
    picks = [pick(f"T{i}") for i in range(5)]
    borrow = {p["ticker"]: {"shortable": True, "easy_to_borrow": True} for p in picks}
    prices = {p["ticker"]: 20.0 for p in picks}
    entries, skips = strategy.recheck_at_open(picks, borrow, prices, {}, 10_000, existing_gross_pct=30.0)
    assert sum(e["qty"] * 20 for e in entries) / 10_000 * 100 <= 20.0 + 1e-9
    assert len(entries) == 2 and {s["reason"] for s in skips} == {"gross cap reached"}


@pytest.mark.parametrize("acct, n, blocked", [
    ({"equity": 10_000, "pattern_day_trader": None, "daytrade_count": None}, 5, False),   # not reported
    ({"equity": 10_000, "pattern_day_trader": True}, 1, True),
    ({"equity": 10_000, "pattern_day_trader": False, "daytrade_count": 0}, 3, False),
    ({"equity": 30_000, "pattern_day_trader": True, "daytrading_buying_power": 50_000}, 5, False),
    ({"equity": 30_000, "pattern_day_trader": True, "daytrading_buying_power": 0}, 5, True),
    ({"equity": 10_000, "pattern_day_trader": True}, 0, False),
])
def test_pdt_block(acct, n, blocked):
    assert bool(strategy.pdt_block(acct, n)) is blocked


def test_size_pct_per_name_is_the_knob(monkeypatch):
    monkeypatch.setattr(config, "SIZE_PCT_PER_NAME", 5.0)
    picks = strategy.select([row(f"T{i}") for i in range(5)], 10_000)
    assert [p["target_pct"] for p in picks] == [5.0] * 5 and picks[0]["target_qty"] == -25
    entries, _ = strategy.recheck_at_open([pick("A")], {"A": {"shortable": True, "easy_to_borrow": True}},
                                          {"A": 20.0}, {}, 10_000)
    assert entries[0]["qty"] == 25


def test_v1_linear_sizing_when_knob_off(monkeypatch):
    monkeypatch.setattr(config, "SIZE_PCT_PER_NAME", None)
    monkeypatch.setattr(config, "SIZE_PCT_MAX", 19.0)
    monkeypatch.setattr(config, "SIZE_PCT_MIN", 15.0)
    assert [strategy.size_pct(i) for i in range(5)] == [19.0, 18.0, 17.0, 16.0, 15.0]


def test_stop_price():
    assert strategy.stop_price(20.0, 35) == 27.0 and strategy.stop_price(13.37, 35) == 18.05


@pytest.mark.parametrize("equity, count, allowance", [
    (10_000, 3, 0), (10_000, 2, 1), (10_000, 0, 3), (10_000, 5, 0), (10_000, None, 0), (25_000, 3, None)])
def test_pdt_allowance(equity, count, allowance):
    assert strategy.pdt_allowance({"equity": equity}, count) == allowance


def test_cap_entries_rank_order():
    entries = [{"ticker": f"T{i}"} for i in range(5)]
    kept, skips = strategy.cap_entries(entries, 2)
    assert [e["ticker"] for e in kept] == ["T0", "T1"] and [s["ticker"] for s in skips] == ["T2", "T3", "T4"]
    assert strategy.cap_entries(entries, None) == (entries, [])


def test_halt_reason(monkeypatch):
    acct = {"equity": 9_000, "last_equity": 10_000}
    assert strategy.halt_reason(acct, 12_000) == (None, None)          # both off
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 10.0)
    monkeypatch.setattr(config, "DRAWDOWN_HALT_PCT", 25.0)
    day, dd = strategy.halt_reason(acct, 12_000)
    assert "10.0% on the day" in day and dd == "equity $9,000 is 25.0% under its $12,000 peak (halt at 25%)"
    assert strategy.halt_reason({"equity": 9_100, "last_equity": 10_000}, 12_000) == (None, None)
