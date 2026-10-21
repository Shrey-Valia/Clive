#!/usr/bin/env python3
"""The daily run. cron starts it at 15:40 ET; it waits for the scan and trade times itself.

    python3 daily.py                 dry run: scan, rank, print the orders, send nothing
    python3 daily.py --execute       same, then flatten and short the top 5 at 15:55 ET
    python3 daily.py --now           skip the waits (testing during market hours)
    python3 daily.py --force         allow a second executed run on the same day

Steps
  1. TradingView top gainers, mcap > $25M, previous close > $3, top 20
  2. Short data (FINRA / yfinance / SEC), Alpaca borrow, headlines
  3. Score against the checklist and rank all 20
  4. 15:55 ET: cancel open orders, net current holdings against the new top 5
     (covers first, then shorts), sized 19% down to 15% of equity
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortingbot import broker, config, db, news, scoring, shortdata, strategy, tradingview  # noqa: E402

log = logging.getLogger("shortingbot")
GUARD = os.path.join(config.LOG_DIR, "last_execute.json")


def setup_logging() -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(config.LOG_DIR, "shortingbot.log"))):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def wait_until(when: dt.datetime, label: str) -> None:
    secs = (when - dt.datetime.now(broker.ET)).total_seconds()
    if secs > 0:
        log.info("waiting %.0fs for %s (%s ET)", secs, label, when.strftime("%H:%M"))
        time.sleep(secs)


def scan() -> list[dict]:
    rows = tradingview.fetch_gainers()
    log.info("tradingview: %d gainers after filters", len(rows))
    if not rows:
        return rows
    tickers = [r["ticker"] for r in rows]
    for label, fn in (("short data", shortdata.enrich), ("borrow", broker.borrow), ("news", news.enrich)):
        t0 = time.time()
        extra = fn(tickers)
        for r in rows:
            r.update(extra.get(r["ticker"], {}))
        log.info("%s: %.1fs", label, time.time() - t0)
    return scoring.rank(rows)


def print_board(ranked: list[dict]) -> None:
    head = f"{'#':>2} {'TICKER':<6} {'CHG%':>6} {'RVOL':>5} {'SI%':>5} {'DTC':>4} {'CAT':<13} {'SCORE':>5}  PICK / WHY NOT"
    log.info(head)
    for r in ranked:
        fmt = lambda v, f: (f.format(v) if v is not None else "-")
        pick = f"SHORT {abs(r['target_qty'])} ({r['target_pct']}%)" if r.get("selected") else (r.get("exclude_reason") or "")
        log.info("%2d %-6s %6s %5s %5s %4s %-13s %5.1f  %s", r["rank"], r["ticker"],
                 fmt(r.get("change"), "{:.1f}"), fmt(r.get("rvol"), "{:.1f}"),
                 fmt(r.get("si_pct_float"), "{:.1f}"), fmt(r.get("days_to_cover"), "{:.1f}"),
                 r.get("catalyst") or "-", r["short_score"], pick)


CANDIDATE_FIELDS = [
    "rank", "ticker", "company", "sector", "close", "prev_close", "change", "high", "low", "VWAP",
    "market_cap", "volume", "rvol", "upper_wick", "close_in_range", "si_pct_float", "days_to_cover",
    "float_shares", "si_date", "si_source", "short_vol_ratio_latest", "short_vol_ratio_avg",
    "ftd_days", "ftd_max_shares", "shortable", "easy_to_borrow", "catalyst", "headlines",
    "short_score", "components", "coverage", "flags", "exclude_reason", "selected",
    "target_pct", "target_qty",
]


def record_candidates(run_id, ranked) -> None:
    rows = []
    for r in ranked:
        rec = {k.lower(): r.get(k) for k in CANDIDATE_FIELDS}
        rec["selected"] = bool(r.get("selected"))
        rec["run_id"] = run_id
        rows.append(rec)
    db.insert("short_candidates", rows)


def guard_blocks(today: str) -> bool:
    try:
        return json.load(open(GUARD)).get("date") == today
    except (OSError, ValueError):
        return False


def execute(run_id, picks: list[dict], cutoff: dt.datetime) -> list[dict]:
    if dt.datetime.now(broker.ET) > cutoff:
        raise RuntimeError(f"past the {cutoff:%H:%M} ET cutoff - refusing to send orders")

    cancelled = broker.cancel_open_orders()
    if cancelled:
        log.info("cancelled %d open orders", cancelled)

    held = broker.signed_holdings()
    target = {p["ticker"]: p["target_qty"] for p in picks}
    exits, entries = strategy.plan_orders(held, target)
    log.info("held %s -> target %s", held or "{}", target)

    sent = []
    for o in exits:
        sent.append({**broker.submit(o["ticker"], o["qty"], o["side"]), "purpose": o["purpose"]})
        log.info("  %s %s %d (%s)", o["side"].upper(), o["ticker"], o["qty"], o["purpose"])
    if exits:
        states = {s["id"]: s for s in broker.wait_filled([s["id"] for s in sent], config.FILL_WAIT_SECONDS)}
        sent = [{**s, **states.get(s["id"], {})} for s in sent]
        unfilled = [s["ticker"] for s in sent if s["status"] != "filled"]
        if unfilled:
            log.warning("exits not filled yet: %s", ", ".join(unfilled))

    for o in entries:
        try:
            sent.append({**broker.submit(o["ticker"], o["qty"], o["side"]), "purpose": o["purpose"]})
            log.info("  %s %s %d (%s)", o["side"].upper(), o["ticker"], o["qty"], o["purpose"])
        except Exception as e:                     # one rejected short must not stop the others
            log.error("  order rejected %s: %s", o["ticker"], e)
            sent.append({"id": None, "ticker": o["ticker"], "side": o["side"], "qty": o["qty"],
                         "status": "rejected", "purpose": o["purpose"], "error": str(e)[:300]})

    time.sleep(5)
    live = {s["id"]: s for s in broker.refresh([s["id"] for s in sent if s.get("id")])}
    sent = [{**s, **live.get(s.get("id"), {})} for s in sent]
    db.insert("short_orders", [{**s, "run_id": run_id, "id": s.get("id") or f"rejected-{run_id}-{s['ticker']}"}
                               for s in sent], upsert_on="id")
    return sent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    setup_logging()
    broker.load_env()
    mode = "execute" if a.execute else "dry_run"
    today = dt.datetime.now(broker.ET).date().isoformat()
    log.info("=== daily run %s | %s ===", today, mode)

    run_id = None
    try:
        session = broker.session_today()
        if session is None and (a.execute or not a.now):
            log.info("market closed today - nothing to do")
            db.heartbeat("idle", f"{today}: market closed")
            return 0
        close = session[1] if session else dt.datetime.now(broker.ET) + dt.timedelta(minutes=10)
        scan_at = close - dt.timedelta(minutes=config.SCAN_MINUTES_BEFORE_CLOSE)
        trade_at = close - dt.timedelta(minutes=config.TRADE_MINUTES_BEFORE_CLOSE)
        cutoff = close - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)

        if a.execute and guard_blocks(today) and not a.force:
            log.error("already executed today - use --force to run again")
            return 1

        if not a.now:
            wait_until(scan_at, "scan")
        db.heartbeat("running", f"{today}: scanning")
        acct = broker.account()
        if not acct["shorting_enabled"] or acct["blocked"]:
            raise RuntimeError("account cannot short (shorting disabled or trading blocked)")

        started = dt.datetime.now(dt.timezone.utc)
        ranked = scan()
        if not ranked:
            raise RuntimeError("TradingView returned no gainers")
        picks = strategy.select(ranked, acct["equity"])
        run = db.insert("short_runs", {"run_date": today, "mode": mode, "started_at": started,
                                       "status": "scanned", "equity": acct["equity"],
                                       "n_candidates": len(ranked), "n_selected": len(picks)})
        run_id = run[0]["id"] if run else None
        record_candidates(run_id, ranked)
        print_board(ranked)

        if len(picks) < config.N_POSITIONS:
            log.warning("only %d eligible names today", len(picks))
        if not picks:
            raise RuntimeError("no eligible short today - every candidate was excluded")

        if not a.execute:
            log.info("DRY RUN - no orders sent. Gross short exposure would be %.0f%% of $%.2f.",
                     sum(p["target_pct"] for p in picks), acct["equity"])
            db.update("short_runs", {"id": run_id}, {"status": "dry_run_done"})
            db.heartbeat("ok", f"{today}: dry run, {len(picks)} picks")
            return 0

        if not a.now:
            wait_until(trade_at, "trade")
        sent = execute(run_id, picks, cutoff if not a.now else close)
        with open(GUARD, "w") as fh:
            json.dump({"date": today, "orders": [s.get("id") for s in sent]}, fh)

        bad = [s for s in sent if s.get("status") in ("rejected", "canceled", "expired")]
        status = "executed_with_errors" if bad else "executed"
        db.update("short_runs", {"id": run_id}, {"status": status, "n_orders": len(sent)})
        db.heartbeat("ok" if not bad else "warning",
                     f"{today}: {len(sent)} orders, {len(bad)} problems")
        log.info("done: %d orders sent, %d problems", len(sent), len(bad))
        return 0 if not bad else 2

    except Exception as e:
        log.error("run failed: %s\n%s", e, traceback.format_exc())
        if run_id:
            db.update("short_runs", {"id": run_id}, {"status": "failed", "message": str(e)[:500]})
        else:
            db.insert("short_runs", {"run_date": today, "mode": mode, "status": "failed",
                                     "message": str(e)[:500], "started_at": dt.datetime.now(dt.timezone.utc)})
        db.heartbeat("error", f"{today}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
