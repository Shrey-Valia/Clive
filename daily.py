#!/usr/bin/env python3
"""The daily loop: scan at 3:50pm ET, rotate the book at 3:55pm ET.

    python3 daily.py                 # run the daemon (DRY RUN - no orders)
    python3 daily.py --execute       # same, but actually place orders
    python3 daily.py --now           # run both steps immediately, once
    python3 daily.py --now --execute

DRY RUN IS THE DEFAULT. Without --execute nothing reaches the broker; the
plan is printed so you can see exactly what would have gone on.

At 3:55 every open position is closed BEFORE new ones open, so each trade is
a clean ~24h hold and yesterday's book never overlaps today's.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

from shortbot import (broker, config, expectancy, ftd, journal, report,
                      scoring, shortdata, shortvolume, tradingview)

TZ = ZoneInfo(config.MARKET_TZ)


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def log(msg: str) -> None:
    print(f"[{now():%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


def market_open_today(paper: bool) -> bool:
    """Ask Alpaca's calendar. Falls back to a weekday check if it is down."""
    try:
        from alpaca.trading.requests import GetCalendarRequest

        today = now().date()
        cal = broker._client(paper).get_calendar(
            GetCalendarRequest(start=today, end=today)
        )
        return bool(cal)
    except Exception as e:
        log(f"  calendar check failed ({str(e)[:60]}); falling back to weekday")
        return now().weekday() < 5


def scan() -> list[dict]:
    log("SCAN starting")
    scraped_at = now()
    rows = tradingview.fetch_gainers()
    if not rows:
        log("  no candidates passed the screen")
        return []
    tickers = [r["ticker"] for r in rows]
    log(f"  {len(rows)} candidates: {', '.join(tickers)}")

    for label, fn in (("short interest", lambda: shortdata.enrich(tickers)),
                      ("short volume", lambda: shortvolume.fetch(tickers)),
                      ("fails-to-deliver", lambda: ftd.fetch(tickers))):
        try:
            data = fn()
            for r in rows:
                r.update(data.get(r["ticker"], {}))
        except Exception as e:
            log(f"  WARNING {label} unavailable: {str(e)[:80]}")

    scored = scoring.score_all(rows)
    expectancy.enrich(scored)
    path = report.write_csv(scored, scraped_at=scraped_at)
    log(f"  scored and written to {path}")
    report.print_summary(scored)
    return scored


STATE = os.path.join("output", "last_rotate.json")


def already_rotated_today() -> bool:
    """True if a live rotation already ran today.

    Without this, a second manual run flattens the position the first run
    just opened - close_all fires before entries, so it sells what it bought
    minutes earlier, books a meaningless round trip, and then gets the
    re-entry rejected as a wash trade. Fine for a once-a-day cron; wrong for
    a human running it twice.
    """
    if not os.path.exists(STATE):
        return False
    try:
        return json.load(open(STATE)).get("date") == dt.date.today().isoformat()
    except Exception:
        return False


def mark_rotated_today(orders) -> None:
    os.makedirs("output", exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump({"date": dt.date.today().isoformat(),
                   "tickers": [o.ticker for o in orders]}, fh)


def rotate(scored: list[dict], paper: bool, live: bool, force: bool = False) -> None:
    log("ROTATE starting" + ("" if live else "  [DRY RUN - no orders will be sent]"))

    if live and already_rotated_today() and not force:
        prior = json.load(open(STATE)).get("tickers", [])
        log(f"  ALREADY ROTATED TODAY ({', '.join(prior) or 'no orders'}) - refusing.")
        log("  Running again would close today's positions and re-open them.")
        log("  Use --force only if you genuinely want to flatten and re-enter.")
        return

    acct = broker.account(paper)
    log(f"  equity ${acct['equity']:,.2f} | shorting_enabled={acct['shorting_enabled']}")
    if acct["blocked"]:
        log("  ACCOUNT BLOCKED - aborting")
        return

    stale = broker.cancel_open_orders(paper, live)
    if stale:
        log(f"  {'cancelled' if live else 'would cancel'} {len(stale)} unfilled "
            f"order(s): {'; '.join(stale)}")

    # MOC orders are refused after the cutoff, and a plain market order this
    # late would fill at the next OPEN instead of the close.
    h, m = (int(x) for x in config.MOC_CUTOFF.split(":"))
    if live and (now().hour, now().minute) >= (h, m):
        log(f"  PAST THE {config.MOC_CUTOFF} ET MOC CUTOFF - refusing to submit.")
        log("  A market order now would fill at tomorrow's OPEN, not today's close.")
        log("  Nothing sent. Run before the cutoff tomorrow.")
        return

    held = broker.open_positions(paper)
    for hp in held:
        log(f"  holding {hp['side']} {hp['qty']} {hp['ticker']}  "
            f"P/L ${hp['unrealized_pl']:+,.2f}")

    shorts = [r["ticker"] for r in scored
              if r.get("call") == "SHORT" and r.get("conviction") == config.EXECUTE_CONVICTION]
    smap = broker.shortable(shorts, paper) if shorts else {}
    target, skipped = broker.plan(scored, acct["equity"], smap)
    for sk in skipped:
        log(f"  skip {sk}")

    cap = acct["equity"] * config.MAX_TOTAL_EXPOSURE_PCT / 100.0
    total = sum(o.notional for o in target)
    if total > cap:
        log(f"  exposure ${total:,.0f} over the {config.MAX_TOTAL_EXPOSURE_PCT:.0f}% "
            f"cap (${cap:,.0f}) - trimming")
        kept, running = [], 0.0
        for o in target:
            if running + o.notional <= cap:
                kept.append(o)
                running += o.notional
        target = kept

    # Net current holdings against the target: exits, entries and unchanged
    # names all resolve into one order set with no wash trades.
    orders = broker.reconcile(target, paper)
    if not orders:
        log("  already positioned as intended - nothing to send")
        if live:
            mark_rotated_today(target)
        return

    # Journal the positions this rotation retires, while their P/L is still
    # readable. Anything held but absent from the target is an exit.
    target_tickers = {o.ticker for o in target}
    exits = [hp for hp in held if hp["ticker"] not in target_tickers]

    log(f"  {'submitting' if live else 'would submit'} {len(orders)} "
        f"market-on-close order(s) - fills at today's official close")
    for o in orders:
        exp = f"{o.expectancy:+.2f}%" if o.expectancy is not None else "n/a"
        tag = "EXIT" if o.conviction == "EXIT" else f"{o.conviction} base {exp}"
        log(f"    {o.side.upper():<4} {o.qty:>6} {o.ticker:<6} @ ~${o.price:>9,.2f} "
            f"= ${o.notional:>10,.2f}  [{tag}]")

    if live and exits:
        n = journal.record_exits(exits)
        if n:
            log(f"  {n} round trip(s) written to {journal.JOURNAL}")

    if live and target:
        journal.record_entries(target, scored)

    results = broker.submit(orders, paper, live)
    for res in results:
        log(f"    -> {res['ticker']:<6} {res['status']}"
            + (f"  {res.get('error','')}" if res["status"] == "FAILED" else ""))
    if live and any(r["status"] != "FAILED" for r in results):
        mark_rotated_today([o for o in orders
                            if any(r["ticker"] == o.ticker and r["status"] != "FAILED"
                                   for r in results)])


def wait_until(hhmm: str) -> bool:
    """Block until hh:mm ET today. False if that time has already passed."""
    h, m = (int(x) for x in hhmm.split(":"))
    target = now().replace(hour=h, minute=m, second=0, microsecond=0)
    if now() >= target:
        return False
    log(f"  waiting until {hhmm} ET ({(target-now()).total_seconds()/60:.0f} min)")
    while now() < target:
        time.sleep(min(20, max(1, (target - now()).total_seconds())))
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Daily 3:50 scan / 3:55 rotate.")
    p.add_argument("--execute", action="store_true",
                   help="actually place orders (default is a dry run)")
    p.add_argument("--live", action="store_true",
                   help="use the LIVE account instead of paper")
    p.add_argument("--force", action="store_true",
                   help="rotate even if one already ran today (flattens and re-enters)")
    p.add_argument("--now", action="store_true",
                   help="run both steps immediately instead of waiting")
    a = p.parse_args()

    paper = not a.live
    log(f"account={'LIVE' if a.live else 'PAPER'} | "
        f"mode={'EXECUTE' if a.execute else 'DRY RUN'} | "
        f"scan {config.SCAN_TIME} ET, rotate {config.TRADE_TIME} ET")
    if a.live and a.execute:
        log("!! LIVE EXECUTION ENABLED")

    if not a.now and not market_open_today(paper):
        log("market closed today - nothing to do")
        return 0

    if a.now:
        rotate(scan(), paper, a.execute, a.force)
        return 0

    if not wait_until(config.SCAN_TIME):
        log(f"{config.SCAN_TIME} ET already passed - running the scan now")
    scored = scan()

    wait_until(config.TRADE_TIME)
    rotate(scored, paper, a.execute, a.force)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(130)
