"""With OVERNIGHT_LONG_ENABLED off, the bot sends exactly the orders it sent before the long
sleeve existed. tests/fixtures/flag_off_plan.json was generated from the code at 4330a74
(before the sleeve) by running this file with SHORTBOT_REGEN_PLAN=1."""
from __future__ import annotations

import datetime as dt
import json
import os

import daily
import monitor
from conftest import ET, gainer, run_daily
from shortingbot import config

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "flag_off_plan.json")


def _norm(fake) -> dict:
    day = dt.datetime.now(ET).date().isoformat()
    return {"events": [list(e) for e in fake.events if e[0] in ("submit", "stop", "cancel")],
            "client_ids": [c.replace(day, "DATE") if c else c for c in fake.client_ids],
            "positions": {t: p["qty"] for t, p in sorted(fake.pos.items())}}


def scenario(fake, fdb, scan_inputs, monkeypatch) -> dict:
    out = {}
    monkeypatch.setattr(daily, "wait_until", lambda when, label: None)
    now = dt.datetime.now(ET)

    # 1. T 15:40: a short and a stray long are open; six gainers, one under $10
    fake.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=5, seconds=1))
    fake.short("OLD", 10, 20.0)
    fake.pos["LNG"] = {"qty": 5, "entry": 30.0, "price": 30.0}
    scan_inputs += [gainer(f"G{i}", close=20 + i) for i in range(5)] + [gainer("CHP", close=8.0)]
    fake.adv.update({f"G{i}": 50e6 for i in range(5)} | {"CHP": 50e6})
    assert run_daily("--execute") == 0
    out["close"] = _norm(fake)
    out["close"]["picks"] = json.load(open(config.PICKS_FILE))["tickers"]

    # 2. T+1 09:31: picks include the stray long and a leftover short
    fake.events.clear(); fake.client_ids.clear()
    fake.session = (now - dt.timedelta(minutes=1), now + dt.timedelta(hours=6))
    fake.short("SHT", 4, 25.0)
    daily.save_picks(config.PICKS_FILE, {
        "run_id": 42, "scan_date": "2026-10-22", "trade_date": now.date().isoformat(),
        "tickers": ["A", "LNG", "SHT", "GAP"],
        "picks": [{"ticker": t, "score": 80, "scan_price": 20.0, "target_pct": 10.0, "scan_qty": 50}
                  for t in ["A", "LNG", "SHT", "GAP"]]})
    fake.prices.update(A=20.0, LNG=30.0, SHT=25.0, GAP=30.0)
    assert run_daily("--open", "--execute") == 0
    out["open"] = _norm(fake)

    # 3. daily-loss halt flatten from monitor.py: shorts covered, the long left alone
    fake.events.clear(); fake.client_ids.clear()
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 5.0)
    fake.last_equity = 11_000.0
    assert monitor.main(["--now"]) == 0
    out["halt"] = _norm(fake)
    return out


def test_flag_off_order_plans_unchanged(legacy_v2_config, fake, fdb, scan_inputs, monkeypatch):
    # pins its own config: the sleeve off and the pre-2026-10-23 v2 settings (conftest.LEGACY_V2)
    assert getattr(config, "OVERNIGHT_LONG_ENABLED", False) is False
    got = json.loads(json.dumps(scenario(fake, fdb, scan_inputs, monkeypatch)))
    if os.environ.get("SHORTBOT_REGEN_PLAN") == "1":
        json.dump(got, open(FIXTURE, "w"), indent=1)
    assert got == json.load(open(FIXTURE))
