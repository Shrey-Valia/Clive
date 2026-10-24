#!/usr/bin/env python3
"""After the close: pull the truth from Alpaca into Supabase.

    python3 sync.py

  - account equity snapshot (one row per day)
  - current positions (replaced wholesale)
  - round-trip trades rebuilt from every fill since config.TRACK_FROM, each tagged with
    the run_id of the order that opened it (from short_orders)
  - one sync_runs row
  - config.AUTO_RESOLVE_EVENT_ALERTS: resolves event alerts whose session is over, the leftover /
    cover-failure alerts once the book holds no short, and condition alerts on names no longer held
  - heartbeat: writes bots.last_sync_status / last_sync_at only. run_state becomes the worse
    of the run and the sync, so a clean sync no longer hides a failed run.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortingbot import alerts, broker, config, db, risk  # noqa: E402
import daily  # noqa: E402
from daily import log, setup_logging  # noqa: E402


def round_trips(fills: list[dict]) -> list[dict]:
    """FIFO-pair fills per symbol into closed trades. Works for longs and shorts."""
    lots: dict[str, deque] = defaultdict(deque)   # symbol -> deque of [signed_qty, price, time, order_id]
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
                "entry_order_id": lot[3],
            })
            lot[0] += matched if lot[0] < 0 else -matched
            qty += matched if qty < 0 else -matched
            if lot[0] == 0:
                book.popleft()
        if qty:
            book.append([qty, px, ts, f.get("order_id")])
    return trades


def tag_run_ids(trades: list[dict]) -> list[dict]:
    """Replace each trade's entry_order_id with the run_id short_orders has for that order."""
    ids = sorted({t["entry_order_id"] for t in trades if t.get("entry_order_id")})
    run_of = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for row in db.select("short_orders", {"select": "id,run_id", "id": f"in.({','.join(chunk)})"}):
            run_of[row["id"]] = row.get("run_id")
    # the overnight long sleeve's trades: short_orders.sleeve (migration 007) on the entry order
    long_ids = sorted({t["entry_order_id"] for t in trades if t.get("side") == "long" and t.get("entry_order_id")})
    sleeve_of = {}
    for i in range(0, len(long_ids), 100):
        chunk = long_ids[i:i + 100]
        for row in db.select("short_orders", {"select": "id,sleeve", "id": f"in.({','.join(chunk)})"}):
            if row.get("sleeve"):
                sleeve_of[row["id"]] = row["sleeve"]
    out = []
    for t in trades:
        t = dict(t)
        oid = t.pop("entry_order_id", None)
        t["run_id"] = run_of.get(oid)
        if oid in sleeve_of:
            t["sleeve"] = sleeve_of[oid]
        out.append(t)
    return out


def moc_check(pos: list[dict], today: str) -> dict[str, float]:
    """STAGED_MOC_COVER: a name staged for the closing auction today that is still short at 16:15
    was not (fully) covered - paper fills cls orders at the closing quote and may expire part.
    Critical moc_cover_failed; the 09:31 leftover cover buys it back. Returns {ticker: qty}."""
    try:
        st = daily.load_picks(daily.moc_state_path()) or {}
        if st.get("date") != today:
            return {}
        staged = {o.get("ticker") for o in st.get("orders") or [] if o.get("id")}
        left = {p["ticker"]: p["qty"] for p in pos if p["qty"] < 0 and p["ticker"] in staged}
        if left:
            alerts.raise_alert("moc_cover_failed", f"{today}: still short after the closing auction although an "
                                                   f"MOC cover was staged: {left} - the 09:31 leftover cover buys "
                                                   f"them back", level="critical")
        return left
    except Exception as e:
        log.warning("MOC check failed: %s", e)
        return {}


def resolve_alerts(pos: list[dict], today: str) -> int:
    """16:15: auto-resolve alerts the close made stale (config.AUTO_RESOLVE_EVENT_ALERTS).
    Returns rows resolved. Never raises."""
    if not config.AUTO_RESOLVE_EVENT_ALERTS:
        return 0
    try:
        n = 0
        midnight = dt.datetime.combine(dt.date.fromisoformat(today), dt.time(), broker.ET)
        n += alerts.resolve_events(None, before=midnight, note="sync: the session is over",
                                   exclude_kinds=alerts.SHORT_STATE_KINDS | alerts.LONG_STATE_KINDS)
        shorts = {p["ticker"] for p in pos if p["qty"] < 0}
        longs = {p["ticker"] for p in pos if p["qty"] > 0}
        if not shorts:
            n += alerts.resolve_events(alerts.SHORT_STATE_KINDS, note="sync: no short open after the close")
        state = daily.load_picks(daily.long_state_path()) or {}
        fresh = ({o.get("ticker") for o in state.get("orders") or [] if o.get("status") != "rejected"}
                 if str(state.get("entry_date") or "") == today else set())
        if longs <= fresh:
            n += alerts.resolve_events(alerts.LONG_STATE_KINDS,
                                       note="sync: no sleeve long left from an earlier session")
        held = shorts | longs
        cleared = alerts.resolve_cleared("position_loss", held)
        cleared += alerts.resolve_cleared("long_loss", longs)
        cleared += alerts.resolve_cleared("long_outside_window", set())   # after 15:55 a long is in its window
        cleared += alerts.resolve_cleared("book_loss", {None} if pos else set())
        return n + len(cleared)
    except Exception as e:
        log.warning("alert auto-resolve failed: %s", e)
        return 0


def main() -> int:
    setup_logging()
    broker.load_env()
    today = dt.datetime.now(broker.ET).date().isoformat()
    acct, pos, trades = None, [], []
    try:
        acct = broker.account()
        risk.update_peak(acct["equity"])                 # for the optional DRAWDOWN_HALT_PCT
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

        moc_check(pos, today)
        resolve_alerts(pos, today)

        fills = broker.fills(config.TRACK_FROM)
        trades = tag_run_ids(round_trips(fills))
        db.insert("short_trades", trades, upsert_on="id")

        msg = f"{today}: synced, equity ${acct['equity']:,.2f}, {len(pos)} positions"
        log.info("sync: equity $%.2f, %d positions, %d fills -> %d closed trades (%d with a run_id)",
                 acct["equity"], len(pos), len(fills), len(trades), sum(1 for t in trades if t.get("run_id")))
        db.insert("sync_runs", {"bot_id": db.BOT_ID, "at": dt.datetime.now(dt.timezone.utc), "status": "ok",
                                "equity": acct["equity"], "n_positions": len(pos), "n_trades": len(trades),
                                "message": msg})
        db.heartbeat("ok", msg, source="sync")
        return 0
    except Exception as e:
        log.error("sync failed: %s", e)
        db.insert("sync_runs", {"bot_id": db.BOT_ID, "at": dt.datetime.now(dt.timezone.utc), "status": "error",
                                "equity": acct["equity"] if acct else None, "n_positions": len(pos),
                                "n_trades": len(trades), "message": f"sync failed: {e}"[:500]})
        db.heartbeat("error", f"{today}: sync failed: {e}", source="sync")
        return 1


if __name__ == "__main__":
    sys.exit(main())
