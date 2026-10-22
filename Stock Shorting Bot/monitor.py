#!/usr/bin/env python3
"""Intraday risk watch. Reads Alpaca, writes alerts. It never trades, with one opt-in
exception: when config.DAILY_LOSS_HALT_PCT is set and the account is down that much on
the day, it flattens the shorts and halts new entries for the day (daily.halt_and_flatten).

    python3 monitor.py          what cron runs (*/15 9-15 ET); acts only 09:45-15:45 on trading days
    python3 monitor.py --now    ignore the time window (testing)

  - position alert when one position is ALERT_POSITION_LOSS_PCT (25%) or more against us
  - book alert when open positions are down ALERT_BOOK_LOSS_PCT (5%) of equity or more
  - an alert stays open (no repeat, no repeat push) until its condition clears, then it is resolved
  - optional ntfy.sh push when NTFY_TOPIC is set in .env
  - DRAWDOWN_HALT_PCT (optional): alert while equity is that far under its peak; 09:31 enters nothing
  - overnight long sleeve (when on, or still holding longs): long_loss when a long is
    OVERNIGHT_LONG_ALERT_LOSS_PCT (25%) down, long_outside_window when any long is open after the
    09:50 exit deadline and before the 15:55 entry. Longs then leave the position_loss check.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortingbot import alerts, broker, config, risk, strategy  # noqa: E402
import daily  # noqa: E402
from daily import log, setup_logging  # noqa: E402


def in_window(now: dt.datetime, session) -> bool:
    """09:45-15:45 ET on a trading day. On a half day the end moves with the close."""
    if session is None:
        return False
    start = now.replace(hour=config.MONITOR_START[0], minute=config.MONITOR_START[1], second=0, microsecond=0)
    end = now.replace(hour=config.MONITOR_END[0], minute=config.MONITOR_END[1], second=0, microsecond=0)
    end = min(end, session[1] - dt.timedelta(minutes=15))
    return start <= now <= end


def check(positions: list[dict], equity: float, skip_longs: bool = False) -> tuple[dict[str, str], str | None]:
    """({ticker: message} for positions past the loss line, book message or None).
    skip_longs: longs are the long sleeve's, checked by long_checks instead."""
    hits = {}
    for p in positions:
        if skip_longs and p["qty"] > 0:
            continue
        loss_pct = -100 * p["unrealized_plpc"]       # Alpaca's plpc is already signed for shorts
        if loss_pct >= config.ALERT_POSITION_LOSS_PCT:
            hits[p["ticker"]] = (f"{p['side']} {abs(p['qty']):g} {p['ticker']} is {loss_pct:.1f}% against us "
                                 f"(entry ${p['avg_entry_price']:.2f}, now ${p['current_price']:.2f}, "
                                 f"P&L ${p['unrealized_pl']:,.2f})")
    book_pl = sum(p["unrealized_pl"] for p in positions)
    book_pct = -100 * book_pl / equity if equity else 0
    book = None
    if book_pct >= config.ALERT_BOOK_LOSS_PCT:
        book = (f"open positions down ${-book_pl:,.2f}, {book_pct:.1f}% of equity ${equity:,.2f} "
                f"(alert line {config.ALERT_BOOK_LOSS_PCT:g}%)")
    return hits, book


def long_checks(positions: list[dict], now: dt.datetime, session) -> tuple[dict[str, str], dict[str, str]]:
    """({ticker: msg} longs down OVERNIGHT_LONG_ALERT_LOSS_PCT+, {ticker: msg} longs open outside
    the sleeve's window). The window is T 15:55 (entry) to T+1 09:50 (exit deadline)."""
    loss, outside = {}, {}
    if session is not None:
        exit_by = session[0] + dt.timedelta(minutes=config.OVERNIGHT_LONG_EXIT_DEADLINE_MINUTES_AFTER_OPEN)
        entry_at = session[1] - dt.timedelta(minutes=config.TRADE_MINUTES_BEFORE_CLOSE)
        in_day = exit_by < now < entry_at
    else:
        in_day = False
    for p in positions:
        if p["qty"] <= 0:
            continue
        down = -100 * p["unrealized_plpc"]
        if down >= config.OVERNIGHT_LONG_ALERT_LOSS_PCT:
            loss[p["ticker"]] = (f"long {p['qty']:g} {p['ticker']} is down {down:.1f}% (entry ${p['avg_entry_price']:.2f}, "
                                 f"now ${p['current_price']:.2f}, P&L ${p['unrealized_pl']:,.2f})")
        if in_day:
            outside[p["ticker"]] = (f"long {p['qty']:g} {p['ticker']} still open at {now:%H:%M} ET - the overnight "
                                    f"sleeve sells by {exit_by:%H:%M}; sell it by hand")
    return loss, outside


def run(positions: list[dict], equity: float, now: dt.datetime | None = None, session=None) -> dict:
    """Raise new alerts, leave open ones alone, resolve cleared ones."""
    sleeve = daily.long_sleeve_active()
    hits, book = check(positions, equity, skip_longs=sleeve)
    out_long: dict = {}
    if sleeve:
        loss, outside = long_checks(positions, now or dt.datetime.now(broker.ET), session)
        for t, msg in loss.items():
            alerts.raise_alert("long_loss", msg, ticker=t, level="critical", dedupe=True)
        for t, msg in outside.items():
            alerts.raise_alert("long_outside_window", msg, ticker=t, level="critical", dedupe=True)
        alerts.resolve_cleared("long_loss", set(loss))
        alerts.resolve_cleared("long_outside_window", set(outside))
        out_long = {"long_loss": sorted(loss), "long_outside_window": sorted(outside)}
    raised = [t for t, msg in hits.items()
              if alerts.raise_alert("position_loss", msg, ticker=t, level="critical", dedupe=True)]
    if book and alerts.raise_alert("book_loss", book, level="critical", dedupe=True):
        raised.append("book")
    resolved = alerts.resolve_cleared("position_loss", set(hits))
    resolved += alerts.resolve_cleared("book_loss", {None} if book else set())
    return {"hits": sorted(hits), "book": book, "raised": raised, "resolved": resolved, **out_long}


def halts(acct: dict, session, today: str) -> dict:
    """Optional account-level halts. Returns {"daily", "drawdown", "flattened"}."""
    day_loss, drawdown = strategy.halt_reason(acct, risk.update_peak(acct["equity"]))
    flattened = False
    if day_loss and not risk.halted_today(today):
        daily.halt_and_flatten(today, day_loss, None, session)
        flattened = True
    if drawdown:
        alerts.raise_alert("drawdown_halt", f"{drawdown} - no new entries", level="critical", dedupe=True)
    else:
        alerts.resolve_cleared("drawdown_halt", set())
    return {"daily": day_loss, "drawdown": drawdown, "flattened": flattened}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="ignore the 09:45-15:45 window")
    a = ap.parse_args(argv)
    setup_logging()
    broker.load_env()
    try:
        now = dt.datetime.now(broker.ET)
        session = broker.session_today()
        if not a.now and not in_window(now, session):
            return 0
        acct = broker.account()
        pos = broker.positions()
        out = run(pos, acct["equity"], now, session)
        h = halts(acct, session, now.date().isoformat())
        if h["flattened"]:
            pos = broker.positions()
        log.info("monitor: %d positions, equity $%.2f, over the line: %s%s, raised %s, resolved %s",
                 len(pos), acct["equity"], out["hits"] or "none", " + book" if out["book"] else "",
                 out["raised"] or "none", out["resolved"] or "none")
        return 0
    except Exception as e:
        log.error("monitor failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
