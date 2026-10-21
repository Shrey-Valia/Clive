#!/usr/bin/env python3
"""After the close (cron 16:15 ET): pull the truth from the options paper account into Supabase.

    python3 live/sync.py

  - equity snapshot (equity_snapshots, bot_id 'options', one row per day)
  - option legs (positions, replaced wholesale) and spreads with marks (options_live_positions)
  - closed trades from the local journal (options_live_trades, upsert by id)
  - heartbeat on bots.id = 'options'
Needs the OPT_* keys; there is no fallback to the shorting account.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live import config as C  # noqa: E402
from live import db, state  # noqa: E402
from live import strategy as S  # noqa: E402
from live.broker import ET, Broker  # noqa: E402
from live.run import TRADE_COLS, log, setup_logging  # noqa: E402


def spread_rows(broker: Broker, records: dict, now: dt.datetime) -> list[dict]:
    syms = sorted({r[k] for r in records.values() for k in ("short_symbol", "long_symbol")})
    quotes = broker.option_quotes(syms) if syms else {}
    rows = []
    for sid, r in records.items():
        sq, lq = quotes.get(r["short_symbol"]), quotes.get(r["long_symbol"])
        mark = S.exit_prices(sq, lq)[0]
        width = r["short_strike"] - r["long_strike"]
        credit, qty = float(r["entry_credit"]), int(r["qty"])
        rows.append({"bot_id": C.BOT_ID, "spread_id": sid, "underlying": r["underlying"], "expiry": r["expiry"],
                     "short_symbol": r["short_symbol"], "long_symbol": r["long_symbol"],
                     "short_strike": r["short_strike"], "long_strike": r["long_strike"], "qty": qty,
                     "entry_date": r["entry_date"], "entry_credit": credit, "mark_debit": mark,
                     "unrealized_pl": round((credit - mark) * 100 * qty, 2) if mark is not None else None,
                     "max_loss": S.max_loss(width, credit, qty), "updated_at": now})
    return rows


def main() -> int:
    setup_logging()
    today = dt.datetime.now(ET).date()
    try:
        broker = Broker(execute=False)
        if not broker.has_trading:
            log.error("REFUSED: %s", broker.trading_error)
            return 2
        acct = broker.account()
        now = dt.datetime.now(dt.timezone.utc)
        db.insert("equity_snapshots", {
            "bot_id": C.BOT_ID, "snap_date": today, "equity": acct["equity"], "cash": acct["cash"],
            "day_pl": round(acct["equity"] - acct["last_equity"], 2),
            "long_market_value": acct["long_market_value"], "short_market_value": acct["short_market_value"],
            "taken_at": now}, upsert_on="bot_id,snap_date")
        pos = broker.positions()
        db.delete("positions", {"bot_id": C.BOT_ID})
        db.insert("positions", [{"bot_id": C.BOT_ID, "ticker": p["symbol"], "qty": p["qty"], "side": p["side"],
                                 "avg_entry_price": p["avg_entry_price"], "current_price": p["current_price"],
                                 "market_value": p["market_value"], "unrealized_pl": p["unrealized_pl"],
                                 "unrealized_plpc": p["unrealized_plpc"], "updated_at": now} for p in pos])
        spreads, orphans = S.group_spreads(pos)
        records, _, _ = state.reconcile(spreads, state.load_positions(), today)
        rows = spread_rows(broker, records, now)
        db.delete("options_live_positions", {"bot_id": C.BOT_ID})
        db.insert("options_live_positions", rows)
        trades = [{k: t.get(k) for k in TRADE_COLS} for t in state.read_jsonl(state.TRADES)
                  if t.get("pnl") is not None and t.get("id")]          # external closes with a known P&L too
        db.insert("options_live_trades", trades, upsert_on="id")
        msg = (f"{today}: synced, equity ${acct['equity']:,.2f}, {len(rows)} spreads"
               + (f", {len(orphans)} UNPAIRED legs" if orphans else ""))
        log.info("sync: %s; %d trades in journal", msg, len(trades))
        db.heartbeat("warning" if orphans else "ok", msg)
        return 0
    except Exception as e:
        log.error("sync failed: %s", e)
        db.heartbeat("error", f"{today}: sync failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
