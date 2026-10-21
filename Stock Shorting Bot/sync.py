#!/usr/bin/env python3
"""After the close: pull the truth from Alpaca into Supabase.

    python3 sync.py

  - account equity snapshot (one row per day)
  - current positions (replaced wholesale)
  - round-trip trades rebuilt from every fill since config.TRACK_FROM
  - fill status for today's orders
  - heartbeat
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortingbot import broker, config, db  # noqa: E402
from daily import log, setup_logging  # noqa: E402


def round_trips(fills: list[dict]) -> list[dict]:
    """FIFO-pair fills per symbol into closed trades. Works for longs and shorts."""
    lots: dict[str, deque] = defaultdict(deque)   # symbol -> deque of [signed_qty, price, time]
    trades = []
    for f in fills:
        sym, px, ts = f["symbol"], float(f["price"]), f["transaction_time"]
        qty = float(f["qty"]) * (1 if f["side"] == "buy" else -1)
        book = lots[sym]
        while qty and book and (book[0][0] > 0) != (qty > 0):
            lot = book[0]
            matched = min(abs(qty), abs(lot[0]))
            side = "long" if lot[0] > 0 else "short"
            pnl = (px - lot[1]) * matched * (1 if side == "long" else -1)
            trades.append({
                "id": f"{sym}-{lot[2]}-{ts}-{matched:g}", "ticker": sym, "side": side, "qty": matched,
                "entry_at": lot[2], "entry_price": lot[1], "exit_at": ts, "exit_price": px,
                "pnl": round(pnl, 2), "pnl_pct": round(100 * pnl / (lot[1] * matched), 2),
            })
            lot[0] += matched if lot[0] < 0 else -matched
            qty += matched if qty < 0 else -matched
            if lot[0] == 0:
                book.popleft()
        if qty:
            book.append([qty, px, ts])
    return trades


def main() -> int:
    setup_logging()
    broker.load_env()
    today = dt.datetime.now(broker.ET).date().isoformat()
    try:
        acct = broker.account()
        db.insert("equity_snapshots", {
            "bot_id": db.BOT_ID, "snap_date": today, "equity": acct["equity"], "cash": acct["cash"],
            "day_pl": round(acct["equity"] - acct["last_equity"], 2),
            "long_market_value": acct["long_market_value"], "short_market_value": acct["short_market_value"],
            "taken_at": dt.datetime.now(dt.timezone.utc),
        }, upsert_on="bot_id,snap_date")

        pos = broker.positions()
        db.delete("positions", {"bot_id": db.BOT_ID})
        db.insert("positions", [{"bot_id": db.BOT_ID, **p, "updated_at": dt.datetime.now(dt.timezone.utc)}
                                for p in pos])

        fills = broker.fills(config.TRACK_FROM)
        trades = round_trips(fills)
        db.insert("short_trades", trades, upsert_on="id")

        log.info("sync: equity $%.2f, %d positions, %d fills -> %d closed trades",
                 acct["equity"], len(pos), len(fills), len(trades))
        db.heartbeat("ok", f"{today}: synced, equity ${acct['equity']:,.2f}, {len(pos)} positions")
        return 0
    except Exception as e:
        log.error("sync failed: %s", e)
        db.heartbeat("error", f"{today}: sync failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
