#!/usr/bin/env python3
"""The daily runs. Which strategy runs is config.STRATEGIES; v2 ("short_intraday") is the default.

    python3 daily.py                    dry run of the 15:40 job: scan, rank, print, send nothing
    python3 daily.py --execute          cron 15:40 ET: 15:45 scan + save picks, 15:55 cover all shorts
    python3 daily.py --open             dry run of the 09:31 job: re-check the saved picks, print
    python3 daily.py --open --execute   cron 09:31 ET: short the saved picks at market
    python3 daily.py --now              skip the waits (testing)
    python3 daily.py --force            allow a second executed run of the same job on the same day

v2 schedule (short_intraday), research/PROPOSAL.md
  T   15:45  TradingView top gainers -> short data, borrow, ADV20, headlines -> score -> gates
             (score >= 70, price >= $10, ADV20 >= $10M) -> save up to 5 picks to
             logs/next_open_picks.json. The scan runs in a thread beside step 15:55.
  T   15:55  cover every open short. This happens even when the scan fails. No new shorts.
  T+1 09:31  re-check each pick (shortable / ETB, price >= $10, gap up <= 40%), short at
             market, 10% of equity each, gross <= 50%.

v1 (short_overnight) is the original bot: 15:55 cancel, net holdings against the new top 5
and hold overnight. It is off; see the README to switch back.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortingbot import alerts, broker, config, db, news, risk, scoring, shortdata, strategy, tradingview  # noqa: E402

log = logging.getLogger("shortingbot")
GUARD = os.path.join(config.LOG_DIR, "last_execute.json")       # 15:40 job
OPEN_GUARD = os.path.join(config.LOG_DIR, "last_open.json")     # 09:31 job
DRY_PICKS_FILE = config.PICKS_FILE.replace(".json", ".dry_run.json")
MILESTONE = {"milestone": True}


def setup_logging() -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(config.LOG_DIR, "shortingbot.log"))):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.addHandler(db.events)
    log.setLevel(logging.INFO)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def wait_until(when: dt.datetime, label: str) -> None:
    secs = (when - dt.datetime.now(broker.ET)).total_seconds()
    if secs > 0:
        log.info("waiting %.0fs for %s (%s ET)", secs, label, when.strftime("%H:%M"))
        time.sleep(secs)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def code_version() -> str | None:
    """Short git sha, '-dirty' when this folder has uncommitted changes."""
    try:
        run = lambda *c: subprocess.run(["git", *c], cwd=config.ROOT, capture_output=True,
                                        text=True, timeout=5).stdout.strip()
        sha = run("rev-parse", "--short", "HEAD")
        return (sha + ("-dirty" if run("status", "--porcelain", "--", ".") else "")) or None
    except Exception:
        return None


def config_snapshot() -> dict:
    """Every UPPER_CASE setting that is plain JSON. config.py holds no secrets."""
    out = {}
    for k in dir(config):
        if k.isupper():
            try:
                v = getattr(config, k)
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                pass
    return out


def run_fields() -> dict:
    return {"code_version": code_version(), "strategy_version": config.STRATEGY_VERSION,
            "config": config_snapshot()}


# ---------------------------------------------------------------- scan (shared by v1 and v2)
def scan() -> list[dict]:
    db.set_step("scan")
    rows = tradingview.fetch_gainers()
    log.info("tradingview: %d gainers after filters", len(rows), extra={**MILESTONE, "step": "source"})
    if not rows:
        return rows
    tickers = [r["ticker"] for r in rows]
    for label, fn in (("short data", shortdata.enrich), ("borrow", broker.borrow),
                      ("adv20", broker.adv20), ("news", news.enrich)):
        t0 = time.time()
        try:
            extra = fn(tickers)
        except Exception as e:                     # one dead source must not kill the scan
            log.warning("%s failed: %s", label, e, extra={"step": "source"})
            extra = {}
        for r in rows:
            r.update(extra.get(r["ticker"], {}))
        have = sum(1 for t in tickers if extra.get(t))
        log.info("%s: %.1fs, %d/%d with data", label, time.time() - t0, have, len(tickers),
                 extra={**MILESTONE, "step": "source"})
    db.set_step("score")
    return scoring.rank(rows)


def print_board(ranked: list[dict]) -> None:
    head = (f"{'#':>2} {'TICKER':<6} {'PRICE':>7} {'CHG%':>6} {'RVOL':>5} {'SI%':>5} {'DTC':>4} "
            f"{'ADV20':>7} {'CAT':<13} {'SCORE':>5}  PICK / WHY NOT")
    log.info(head)
    for r in ranked:
        fmt = lambda v, f: (f.format(v) if v is not None else "-")
        pick = f"SHORT {abs(r['target_qty'])} ({r['target_pct']}%)" if r.get("selected") else (r.get("exclude_reason") or "")
        adv = r.get("adv20_usd")
        log.info("%2d %-6s %7s %6s %5s %5s %4s %7s %-13s %5.1f  %s", r["rank"], r["ticker"],
                 fmt(r.get("close"), "{:.2f}"), fmt(r.get("change"), "{:.1f}"), fmt(r.get("rvol"), "{:.1f}"),
                 fmt(r.get("si_pct_float"), "{:.1f}"), fmt(r.get("days_to_cover"), "{:.1f}"),
                 f"{adv / 1e6:.1f}M" if adv is not None else "-",
                 r.get("catalyst") or "-", r["short_score"], pick)


CANDIDATE_FIELDS = [
    "rank", "ticker", "company", "sector", "close", "prev_close", "change", "high", "low", "VWAP",
    "market_cap", "volume", "rvol", "upper_wick", "close_in_range", "si_pct_float", "days_to_cover",
    "float_shares", "si_date", "si_source", "short_vol_ratio_latest", "short_vol_ratio_avg",
    "ftd_days", "ftd_max_shares", "shortable", "easy_to_borrow", "adv20_usd", "catalyst", "headlines",
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


def record_orders(run_id, sent: list[dict]) -> None:
    db.insert("short_orders", [{**{k: v for k, v in s.items() if k not in ("price", "target_pct")},
                                "run_id": run_id, "id": s.get("id") or f"rejected-{run_id}-{s['ticker']}-{s['side']}"}
                               for s in sent], upsert_on="id")


# ---------------------------------------------------------------- guards and picks
def guard_blocks(today: str, path: str | None = None) -> bool:
    try:
        path = path or GUARD
        return json.load(open(path)).get("date") == today
    except (OSError, ValueError):
        return False


def write_guard(today: str, sent: list[dict], path: str | None = None) -> None:
    with open(path or GUARD, "w") as fh:
        json.dump({"date": today, "orders": [s.get("id") for s in sent]}, fh)


def load_picks(path: str) -> dict | None:
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def save_picks(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1, default=str)
    os.replace(tmp, path)


def picks_payload(run_id, scan_date: str, trade_date: str | None, picks: list[dict], equity: float) -> dict:
    return {
        "run_id": run_id, "scan_date": scan_date, "trade_date": trade_date,
        "strategy_version": config.STRATEGY_VERSION, "saved_at": utcnow().isoformat(), "equity": equity,
        "tickers": [p["ticker"] for p in picks],
        "picks": [{"ticker": p["ticker"], "rank": p.get("rank"), "score": p.get("short_score"),
                   "scan_price": p.get("close"), "target_pct": p.get("target_pct"),
                   "scan_qty": abs(p.get("target_qty") or 0), "adv20_usd": p.get("adv20_usd"),
                   "catalyst": p.get("catalyst")} for p in picks],
    }


def is_buying_power_error(e) -> bool:
    """Intraday margin (or Reg T) said no: skip the name, carry on."""
    t = str(e).lower()
    return "buying power" in t or "margin" in t


def is_pdt_error(e) -> bool:
    t = str(e).lower()
    return ("pattern day" in t or "pdt" in t or "day trade" in t) and not is_buying_power_error(e)


# ---------------------------------------------------------------- order sending
def send_orders(orders: list[dict], wait: int | None = None) -> tuple[list[dict], str | None]:
    """Send market orders one by one. A rejected order is recorded and the rest go on, except
    after a PDT rejection, which stops the batch. Returns (sent, pdt_message)."""
    sent, pdt = [], None
    for o in orders:
        if pdt:
            sent.append({"id": None, "ticker": o["ticker"], "side": o["side"], "qty": o["qty"],
                         "status": "rejected", "purpose": o["purpose"], "error": "skipped after PDT rejection"})
            continue
        try:
            sent.append({**broker.submit(o["ticker"], o["qty"], o["side"]), "purpose": o["purpose"]})
            log.info("  %s %s %d (%s)", o["side"].upper(), o["ticker"], o["qty"], o["purpose"], extra=MILESTONE)
        except Exception as e:                     # one rejected order must not stop the others
            log.error("  order rejected %s %s: %s", o["side"], o["ticker"], e)
            sent.append({"id": None, "ticker": o["ticker"], "side": o["side"], "qty": o["qty"],
                         "status": "rejected", "purpose": o["purpose"], "error": str(e)[:300]})
            if is_pdt_error(e):
                pdt = f"{o['side']} {o['ticker']} rejected by the pattern day trader rule: {str(e)[:200]}"
                risk.set_pdt_detected(pdt)
            elif is_buying_power_error(e):
                log.warning("  %s %s skipped: not enough buying power (intraday margin)", o["side"], o["ticker"])
    ids = [s["id"] for s in sent if s.get("id")]
    if ids:
        try:
            if wait:
                states = broker.wait_filled(ids, wait)
            else:
                time.sleep(5)
                states = broker.refresh(ids)
            live = {s["id"]: s for s in states}
            sent = [{**s, **live.get(s.get("id"), {})} for s in sent]
        except Exception as e:
            log.warning("order status refresh failed: %s", e)
    return sent, pdt


# ---------------------------------------------------------------- v2: short_intraday, 15:40 job
def cover_all(run_id, execute: bool, cutoff: dt.datetime, also_cancel: list[str] | None = None) -> dict:
    """Buy back every open short. Returns {"planned", "sent", "problems", "left"}.

    Open orders on the shorted names (and on `also_cancel`, today's entries) go first, so a
    protective stop can't fire against a cover. Holdings are then re-read: a stop that filled
    before the cancel has already covered its name, and a cover rejected for a name that turns
    out flat is not a problem.
    """
    db.set_step("cover")
    held = broker.signed_holdings()
    covers = strategy.plan_cover(held)
    log.info("cover: %d shorts open %s", len(covers),
             {c["ticker"]: -c["qty"] for c in covers} or "{}", extra=MILESTONE)
    out = {"planned": covers, "sent": [], "problems": [], "left": {}}
    if not execute:
        for c in covers:
            log.info("  DRY RUN would BUY %s %d (cover)", c["ticker"], c["qty"])
        return out
    symbols = sorted({c["ticker"] for c in covers} | set(also_cancel or []))
    if symbols:
        try:
            n = broker.cancel_open_orders(symbols)
            if n:
                log.info("cancelled %d open orders (stops included) on %s", n, ", ".join(symbols), extra=MILESTONE)
                if not broker.wait_no_open_orders(symbols):
                    log.warning("some orders on %s still open after cancelling", ", ".join(symbols))
                held = broker.signed_holdings()
                covers = strategy.plan_cover(held)
                out["planned"] = covers
        except Exception as e:
            log.warning("cancel open orders failed: %s", e)
    if not covers:
        return out
    if dt.datetime.now(broker.ET) > cutoff:
        out["problems"].append(f"past the {cutoff:%H:%M} ET cutoff - covers not sent")
        out["left"] = {c["ticker"]: -c["qty"] for c in covers}
        return out
    sent, pdt = send_orders(covers, wait=config.FILL_WAIT_SECONDS)
    out["sent"] = sent
    record_orders(run_id, sent)
    try:
        out["left"] = {t: q for t, q in broker.signed_holdings().items() if q < 0}
    except Exception as e:
        log.warning("could not re-read positions after covering: %s", e)
        out["left"] = {s["ticker"]: -s["qty"] for s in sent if s.get("status") != "filled"}
    if pdt and out["left"]:
        alerts.raise_alert("pdt_block", pdt + " - the short stays open overnight", level="critical")
    for s in sent:
        if s.get("status") == "filled":
            continue
        if s["ticker"] in out["left"]:
            out["problems"].append(f"cover {s['ticker']} {s.get('status')}: {s.get('error') or ''}".strip())
        else:
            log.info("cover %s %s but the position is flat (stop filled first?)", s["ticker"], s.get("status"))
    return out


def close_intraday(a, today: str, mode: str) -> int:
    run_id, prev_run_id, cover, scan_box = None, None, None, {}
    try:
        db.set_step("setup")
        session = broker.session_today()
        if session is None and (a.execute or not a.now):
            log.info("market closed today - nothing to do", extra=MILESTONE)
            db.insert("short_runs", {"run_date": today, "mode": mode, "status": "market_closed",
                                     "started_at": utcnow(), "finished_at": utcnow(),
                                     "message": f"{today}: market closed", **run_fields()})
            db.heartbeat("idle", f"{today}: market closed")
            return 0
        close = session[1] if session else dt.datetime.now(broker.ET) + dt.timedelta(minutes=10)
        scan_at = close - dt.timedelta(minutes=config.SCAN_MINUTES_BEFORE_CLOSE)
        trade_at = close - dt.timedelta(minutes=config.TRADE_MINUTES_BEFORE_CLOSE)
        cutoff = close - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)
        if close.hour < 16:
            log.info("half day: close %s ET, scan %s, cover %s", close.strftime("%H:%M"),
                     scan_at.strftime("%H:%M"), trade_at.strftime("%H:%M"), extra=MILESTONE)

        if a.execute and guard_blocks(today) and not a.force:
            log.error("already executed today - use --force to run again")
            return 1

        # The shorts covered today were entered this morning from the picks saved at the last
        # scan. That scan's run row gets the exit. (Handover from v1: no picks file, so the
        # covers go on today's row.)
        prev = load_picks(config.PICKS_FILE)
        if prev and prev.get("trade_date") == today:
            prev_run_id = prev.get("run_id")
        try:
            trade_date = broker.next_session(dt.date.fromisoformat(today))
        except Exception as e:                     # must not cost the 15:55 cover
            log.warning("next session lookup failed: %s", e)
            trade_date = None

        if not a.now:
            wait_until(scan_at, "scan")
        db.heartbeat("running", f"{today}: scanning")
        run = db.insert("short_runs", {"run_date": today, "trade_date": trade_date, "mode": mode,
                                       "status": "running", "started_at": utcnow(), "scan_at": utcnow(),
                                       "session_close": close, "cutoff_at": cutoff, **run_fields()})
        run_id = run[0]["id"] if run else None
        log.info("scan started for trade date %s (run %s)", trade_date, run_id, extra=MILESTONE)

        picks_path = config.PICKS_FILE if a.execute else DRY_PICKS_FILE
        lock = threading.Lock()

        def scan_job():
            try:
                acct = broker.account()
                ranked = scan()
                if not ranked:
                    raise RuntimeError("TradingView returned no gainers")
                picks = strategy.select(ranked, acct["equity"])
                record_candidates(run_id, ranked)
                print_board(ranked)
                with lock:
                    if scan_box.get("abandoned"):
                        return
                    save_picks(picks_path, picks_payload(run_id, today, trade_date and trade_date.isoformat(),
                                                         picks, acct["equity"]))
                    scan_box.update(ranked=ranked, picks=picks, equity=acct["equity"])
                log.info("picks saved for %s: %s (%.0f%% gross of $%.2f) -> %s", trade_date,
                         ", ".join(p["ticker"] for p in picks) or "none",
                         sum(p["target_pct"] for p in picks), acct["equity"],
                         os.path.basename(picks_path), extra={**MILESTONE, "step": "picks"})
            except Exception as e:
                scan_box["error"] = e
                log.error("scan failed: %s\n%s", e, traceback.format_exc(), extra={"step": "scan"})

        t_scan = threading.Thread(target=scan_job, name="scan", daemon=True)
        t_scan.start()
        scan_deadline = time.time() + config.SCAN_TIMEOUT_SECONDS

        # 15:55: cover, whatever the scan is doing
        if not a.now:
            while t_scan.is_alive() and dt.datetime.now(broker.ET) < trade_at:
                t_scan.join(timeout=max(0.0, min(5.0, (trade_at - dt.datetime.now(broker.ET)).total_seconds())))
            wait_until(trade_at, "cover")
        entered = [e["ticker"] for e in (prev or {}).get("entered") or []] if prev_run_id else []
        cover = cover_all(prev_run_id or run_id, a.execute, cutoff if not a.now else close, entered)
        if a.execute:
            write_guard(today, cover["sent"])
        for p in cover["problems"]:
            log.error("cover problem: %s", p, extra={"step": "cover"})
        if cover["left"] and a.execute:
            alerts.raise_alert("cover_failed", f"still short after the 15:55 cover: {cover['left']}",
                               level="critical")
        db.events.flush_to(prev_run_id or run_id)

        t_scan.join(timeout=max(0.0, scan_deadline - time.time()))
        db.set_step("finish")
        with lock:
            if t_scan.is_alive():
                scan_box["abandoned"] = True
                scan_box["error"] = RuntimeError(f"scan still running after {config.SCAN_TIMEOUT_SECONDS}s")
                log.error("scan timed out - no picks saved for %s", trade_date)

        # exit row: the run whose picks were traded today
        n_cover_bad = len(cover["problems"])
        exit_status = "exited_with_errors" if n_cover_bad or cover["left"] else "exited"
        if a.execute and prev_run_id:
            db.update("short_runs", {"id": prev_run_id},
                      {"status": exit_status, "exit_at": utcnow(), "finished_at": utcnow(),
                       "n_orders": (prev.get("n_entered") or 0) + len(cover["sent"]),
                       "message": "; ".join(cover["problems"])[:500] or None})

        # today's row: the scan
        msg = []
        if not prev_run_id and cover["planned"]:
            msg.append(f"{'covered' if a.execute else 'would cover'} {len(cover['planned'])} shorts "
                       f"not from a v2 pick (handover)")
        msg += cover["problems"] if not prev_run_id else []
        if "error" in scan_box:
            alerts.raise_alert("scan_failed", f"{today}: {scan_box['error']} - nothing to short at the next open",
                               level="warning")
            db.update("short_runs", {"id": run_id}, {"status": "failed", "finished_at": utcnow(),
                                                     "message": "; ".join([str(scan_box["error"])] + msg)[:500]})
        else:
            picks = scan_box["picks"]
            if len(picks) < config.N_POSITIONS:
                log.warning("only %d eligible names today", len(picks))
            db.update("short_runs", {"id": run_id}, {
                "status": "picks_saved", "finished_at": utcnow(), "equity": scan_box["equity"],
                "n_candidates": len(scan_box["ranked"]), "n_selected": len(picks),
                "n_orders": len(cover["sent"]) if not prev_run_id else 0,
                "message": "; ".join(msg)[:500] or None})

        failed = "error" in scan_box or bool(cover["left"] and a.execute)
        state = "error" if failed else ("warning" if n_cover_bad else "ok")
        n_picks = len(scan_box.get("picks") or [])
        db.heartbeat(state, f"{today}: covered {len(cover['sent'] if a.execute else cover['planned'])}, "
                            f"{n_picks} picks for {trade_date}" + (f", scan failed: {scan_box['error']}"
                                                                   if "error" in scan_box else ""))
        log.info("done: %d covers%s, %d cover problems, %d picks saved%s",
                 len(cover["sent"] if a.execute else cover["planned"]), "" if a.execute else " planned",
                 n_cover_bad, n_picks, "" if a.execute else " (DRY RUN)", extra=MILESTONE)
        db.events.flush_to(run_id)
        return 1 if failed else (2 if n_cover_bad else 0)

    except Exception as e:
        log.error("run failed: %s\n%s", e, traceback.format_exc())
        if run_id:
            db.update("short_runs", {"id": run_id}, {"status": "failed", "message": str(e)[:500],
                                                     "finished_at": utcnow()})
        else:
            run = db.insert("short_runs", {"run_date": today, "mode": mode, "status": "failed",
                                           "message": str(e)[:500], "started_at": utcnow(), **run_fields()})
            run_id = run[0]["id"] if run else None
        alerts.raise_alert("run_failed", f"{today} 15:40 job: {e}", level="critical")
        db.heartbeat("error", f"{today}: {e}")
        db.events.flush_to(run_id)
        return 1


# ---------------------------------------------------------------- v2: short_intraday, 09:31 job
def place_stops(run_id, sent: list[dict], entries: list[dict]) -> list[dict]:
    """A DAY buy-stop BROKER_STOP_PCT above each filled short's fill price (planned price if
    the fill price is missing). Unfilled entries get none: the 15:55 cover handles them."""
    planned = {e["ticker"]: e["price"] for e in entries}
    stops = []
    for s in sent:
        qty = int(s.get("filled_qty") or 0)
        if s.get("side") != "sell" or qty < 1:
            if s.get("id"):
                log.warning("no stop for %s: entry %s, filled %s", s["ticker"], s.get("status"), s.get("filled_qty"))
            continue
        level = strategy.stop_price(s.get("filled_avg_price") or planned[s["ticker"]], config.BROKER_STOP_PCT)
        try:
            o = {**broker.submit_stop(s["ticker"], qty, level), "purpose": "stop"}
            log.info("  STOP BUY %s %d @ $%.2f (+%g%%)", s["ticker"], qty, level, config.BROKER_STOP_PCT, extra=MILESTONE)
        except Exception as e:
            alerts.raise_alert("stop_failed", f"stop for {s['ticker']} at ${level:.2f} rejected: {e}", ticker=s["ticker"])
            o = {"id": None, "ticker": s["ticker"], "side": "buy", "qty": qty, "status": "rejected",
                 "purpose": "stop", "error": str(e)[:300]}
        stops.append({**o, "stop_price": level})
    record_orders(run_id, stops)
    return stops


def halt_and_flatten(today: str, reason: str, run_id, session) -> str:
    """Daily loss halt: write logs/halt.json, alert, cover every short (stops cancelled first)."""
    risk.set_halt(today, reason)
    alerts.raise_alert("daily_loss_halt", f"{today}: {reason} - flattening, no new entries today", level="critical")
    close = session[1] if session else dt.datetime.now(broker.ET) + dt.timedelta(hours=1)
    out = cover_all(run_id, True, close - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE))
    if out["left"]:
        alerts.raise_alert("cover_failed", f"halt flatten left shorts open: {out['left']}", level="critical")
    return reason


def open_intraday(a, today: str, mode: str) -> int:
    run_id = None
    try:
        db.set_step("setup")
        session = broker.session_today()
        if session is None and (a.execute or not a.now):
            log.info("market closed today - nothing to enter")
            db.heartbeat("idle", f"{today}: market closed")
            return 0
        opened = session[0] if session else dt.datetime.now(broker.ET)
        entry_at = opened + dt.timedelta(minutes=config.ENTRY_MINUTES_AFTER_OPEN)
        cutoff = opened + dt.timedelta(minutes=config.ENTRY_CUTOFF_MINUTES_AFTER_OPEN)

        if a.execute and guard_blocks(today, OPEN_GUARD) and not a.force:
            log.error("already entered today - use --force to run again")
            return 1

        path = a.picks or (config.PICKS_FILE if a.execute or not os.path.exists(DRY_PICKS_FILE) else DRY_PICKS_FILE)
        data = load_picks(path)
        if data is None:
            alerts.raise_alert("no_picks", f"{today}: no picks file at {os.path.basename(path)} - nothing entered")
            db.heartbeat("warning", f"{today}: no picks file")
            return 0
        run_id = data.get("run_id")
        if data.get("trade_date") != today and not (a.now and not a.execute):
            alerts.raise_alert("stale_picks", f"{today}: picks file is for {data.get('trade_date')} "
                                              f"(scanned {data.get('scan_date')}) - nothing entered")
            db.heartbeat("warning", f"{today}: stale picks file")
            db.events.flush_to(run_id)
            return 0
        picks = data.get("picks") or []
        log.info("open: %d picks from the %s scan (run %s): %s", len(picks), data.get("scan_date"), run_id,
                 ", ".join(data.get("tickers") or []) or "none", extra=MILESTONE)
        if not picks:
            if a.execute:
                db.update("short_runs", {"id": run_id}, {"status": "no_entries", "message": "no picks at the scan"})
            db.heartbeat("ok", f"{today}: no picks to enter")
            db.events.flush_to(run_id)
            return 0

        if not a.now:
            wait_until(entry_at, "entry")
        if a.execute and not a.now and dt.datetime.now(broker.ET) > cutoff:
            raise RuntimeError(f"past the {cutoff:%H:%M} ET entry cutoff - refusing to enter late")

        db.set_step("entry")
        acct = broker.account()
        if not acct["shorting_enabled"] or acct["blocked"]:
            raise RuntimeError("account cannot short (shorting disabled or trading blocked)")
        log.info("PDT: pattern_day_trader=%s daytrade_count=%s daytrading_buying_power=%s equity=$%.2f%s",
                 acct["pattern_day_trader"], acct["daytrade_count"], acct["daytrading_buying_power"],
                 acct["equity"], "" if acct["pattern_day_trader"] is not None else " (not reported by Alpaca)",
                 extra=MILESTONE)
        def no_entries(kind: str, reason: str, level: str = "warning") -> int:
            alerts.raise_alert(kind, f"{today}: no entries - {reason}", level=level)
            if a.execute:
                db.update("short_runs", {"id": run_id}, {"status": "no_entries", "message": reason[:500]})
            db.heartbeat("warning", f"{today}: nothing entered: {reason}"[:500])
            db.events.flush_to(run_id)
            return 0

        pdt_on = strategy.pdt_applies(acct, risk.pdt_detected())
        log.info("PDT cap %s (PDT_ENFORCE=%s%s)", "ON" if pdt_on else "off", config.PDT_ENFORCE,
                 ", a PDT rejection was seen before" if risk.pdt_detected() else "", extra=MILESTONE)
        pdt = strategy.pdt_block(acct, len(picks)) if pdt_on else None
        if pdt:
            return no_entries("pdt_block", f"PDT: {pdt}")

        # account-level halts (off unless configured)
        peak = risk.update_peak(acct["equity"])
        halted = risk.halted_today(today)
        day_loss, drawdown = strategy.halt_reason(acct, peak)
        if day_loss and not halted:
            if a.execute:
                halted = halt_and_flatten(today, day_loss, run_id, session)
            else:
                log.warning("DRY RUN - daily loss halt would trigger: %s", day_loss)
                halted = day_loss
        if halted:
            return no_entries("daily_loss_halt", f"halted for the day: {halted}", "critical")
        if drawdown:
            return no_entries("drawdown_halt", drawdown, "critical")

        positions = broker.positions()
        held = {p["ticker"]: int(round(p["qty"])) for p in positions}
        existing = sum(abs(p["market_value"]) for p in positions if p["qty"] < 0) / acct["equity"] * 100
        leftover = {t: q for t, q in held.items() if q < 0}
        if leftover:
            alerts.raise_alert("overnight_leftover", f"{today}: shorts still open from before the open: {leftover} "
                                                     f"({existing:.0f}% of equity, counted against the gross cap)")
        tickers = [p["ticker"] for p in picks]
        borrow = broker.borrow(tickers)
        prices = broker.last_prices(tickers, today_only=session is not None and dt.datetime.now(broker.ET) >= opened)
        entries, skips = strategy.recheck_at_open(picks, borrow, prices, held, acct["equity"], existing)
        # PDT: never open more shorts than can be covered today without breaking 3-in-5
        capped = pdt_on and acct["equity"] < config.PDT_MIN_EQUITY
        n_day_trades = broker.day_trade_count() if capped else None
        if capped and n_day_trades is None:
            log.warning("PDT: day trade count unknown - entering nothing rather than risk an uncoverable short")
        allowance = strategy.pdt_allowance(acct, n_day_trades) if pdt_on else None
        if config.PDT_OVERFLOW != "skip":
            log.warning("PDT_OVERFLOW=%r is not implemented - using 'skip'", config.PDT_OVERFLOW)
        entries, over = strategy.cap_entries(entries, allowance)
        skips += over
        if allowance is not None:
            log.info("PDT allowance: %d new day trades today (%s in the last 5 sessions, equity $%.0f < $%s)",
                     allowance, n_day_trades, acct["equity"], f"{config.PDT_MIN_EQUITY:,}", extra=MILESTONE)
        for s in skips:
            log.info("  skip %s: %s", s["ticker"], s["reason"], extra=MILESTONE)
        for e in entries:
            log.info("  plan SELL %s %d @ ~$%.2f (%.1f%%)%s", e["ticker"], e["qty"], e["price"], e["target_pct"],
                     f", stop ~${strategy.stop_price(e['price'], config.BROKER_STOP_PCT):.2f}"
                     if config.BROKER_STOP_PCT else "")
        if allowance == 0 and (entries or over):
            return no_entries("pdt_limit", "PDT limit: no day trades left in the 5-session window "
                                           f"({len(over)} picks skipped)")

        if not a.execute:
            log.info("DRY RUN - no orders sent. Gross short exposure would be %.0f%% of $%.2f.",
                     existing + sum(e["target_pct"] for e in entries), acct["equity"], extra=MILESTONE)
            db.events.flush_to(run_id)
            return 0

        sent, pdt = send_orders(entries, wait=config.FILL_WAIT_SECONDS if config.BROKER_STOP_PCT else None)
        record_orders(run_id, sent)
        write_guard(today, sent, OPEN_GUARD)
        stops = place_stops(run_id, sent, entries) if config.BROKER_STOP_PCT else []
        if pdt:
            alerts.raise_alert("pdt_block", f"{today}: {pdt} - remaining entries skipped", level="warning")
        bad = [s for s in sent if s.get("status") in ("rejected", "canceled", "expired")]
        ok = len(sent) - len(bad)
        status = "no_entries" if not ok else ("entered_with_errors" if bad else "entered")
        msg = "; ".join([f"skip {s['ticker']}: {s['reason']}" for s in skips] +
                        [f"{s['ticker']} {s.get('status')}: {s.get('error') or ''}" for s in bad])
        db.update("short_runs", {"id": run_id}, {"status": status, "entry_at": utcnow(),
                                                 "n_orders": len(sent), "message": msg[:500] or None})
        data.update(entered_at=utcnow().isoformat(), n_entered=len(sent),
                    entered=[{"ticker": s["ticker"], "qty": s["qty"], "id": s.get("id"),
                              "status": s.get("status")} for s in sent], skipped=skips,
                    stops=[{"ticker": s["ticker"], "id": s.get("id"), "stop_price": s.get("stop_price")}
                           for s in stops])
        save_picks(path, data)
        db.heartbeat("ok" if not bad else "warning", f"{today}: entered {ok} shorts, {len(skips)} skipped, "
                                                     f"{len(bad)} problems")
        log.info("done: %d entries, %d skipped, %d problems", ok, len(skips), len(bad), extra=MILESTONE)
        db.events.flush_to(run_id)
        return 0 if not bad else 2

    except Exception as e:
        log.error("open run failed: %s\n%s", e, traceback.format_exc())
        if run_id and a.execute:
            db.update("short_runs", {"id": run_id}, {"status": "failed", "message": f"open: {e}"[:500]})
        alerts.raise_alert("run_failed", f"{today} 09:31 job: {e}", level="critical")
        db.heartbeat("error", f"{today}: open failed: {e}")
        db.events.flush_to(run_id)
        return 1


# ---------------------------------------------------------------- v1: short_overnight (off)
def execute_overnight(run_id, picks: list[dict], cutoff: dt.datetime) -> list[dict]:
    if dt.datetime.now(broker.ET) > cutoff:
        raise RuntimeError(f"past the {cutoff:%H:%M} ET cutoff - refusing to send orders")

    cancelled = broker.cancel_open_orders()
    if cancelled:
        log.info("cancelled %d open orders", cancelled)

    held = broker.signed_holdings()
    target = {p["ticker"]: p["target_qty"] for p in picks}
    exits, entries = strategy.plan_orders(held, target)
    log.info("held %s -> target %s", held or "{}", target)

    sent, _ = send_orders(exits, wait=config.FILL_WAIT_SECONDS) if exits else ([], None)
    unfilled = [s["ticker"] for s in sent if s.get("status") != "filled"]
    if unfilled:
        log.warning("exits not filled yet: %s", ", ".join(unfilled))
    more, _ = send_orders(entries)
    sent += more
    record_orders(run_id, sent)
    return sent


def close_overnight(a, today: str, mode: str) -> int:
    run_id = None
    try:
        session = broker.session_today()
        if session is None and (a.execute or not a.now):
            log.info("market closed today - nothing to do")
            db.insert("short_runs", {"run_date": today, "mode": mode, "status": "market_closed",
                                     "started_at": utcnow(), **run_fields()})
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

        started = utcnow()
        ranked = scan()
        if not ranked:
            raise RuntimeError("TradingView returned no gainers")
        picks = strategy.select(ranked, acct["equity"])
        run = db.insert("short_runs", {"run_date": today, "trade_date": today, "mode": mode,
                                       "started_at": started, "scan_at": started, "session_close": close,
                                       "cutoff_at": cutoff, "status": "scanned", "equity": acct["equity"],
                                       "n_candidates": len(ranked), "n_selected": len(picks), **run_fields()})
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
            db.update("short_runs", {"id": run_id}, {"status": "dry_run_done", "finished_at": utcnow()})
            db.heartbeat("ok", f"{today}: dry run, {len(picks)} picks")
            db.events.flush_to(run_id)
            return 0

        if not a.now:
            wait_until(trade_at, "trade")
        sent = execute_overnight(run_id, picks, cutoff if not a.now else close)
        write_guard(today, sent)

        bad = [s for s in sent if s.get("status") in ("rejected", "canceled", "expired")]
        status = "executed_with_errors" if bad else "executed"
        db.update("short_runs", {"id": run_id}, {"status": status, "n_orders": len(sent),
                                                 "entry_at": utcnow(), "finished_at": utcnow()})
        db.heartbeat("ok" if not bad else "warning",
                     f"{today}: {len(sent)} orders, {len(bad)} problems")
        log.info("done: %d orders sent, %d problems", len(sent), len(bad))
        db.events.flush_to(run_id)
        return 0 if not bad else 2

    except Exception as e:
        log.error("run failed: %s\n%s", e, traceback.format_exc())
        if run_id:
            db.update("short_runs", {"id": run_id}, {"status": "failed", "message": str(e)[:500]})
        else:
            run = db.insert("short_runs", {"run_date": today, "mode": mode, "status": "failed",
                                           "message": str(e)[:500], "started_at": utcnow(), **run_fields()})
            run_id = run[0]["id"] if run else None
        db.heartbeat("error", f"{today}: {e}")
        db.events.flush_to(run_id)
        return 1


# ---------------------------------------------------------------- dispatch
# One entry per strategy name in config.STRATEGIES. A long-side strategy adds its own jobs here.
CLOSE_JOBS = {"short_intraday": close_intraday, "short_overnight": close_overnight}
OPEN_JOBS = {"short_intraday": open_intraday}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--open", action="store_true", help="the 09:31 next-open entry job")
    ap.add_argument("--picks", help="picks file for --open (default: logs/next_open_picks.json)")
    a = ap.parse_args(argv)

    setup_logging()
    broker.load_env()
    mode = "execute" if a.execute else "dry_run"
    today = dt.datetime.now(broker.ET).date().isoformat()
    job = "open" if a.open else "close"
    log.info("=== daily %s run %s | %s | %s ===", job, today, mode, ", ".join(config.STRATEGIES))

    jobs = OPEN_JOBS if a.open else CLOSE_JOBS
    if "short_intraday" in config.STRATEGIES and "short_overnight" in config.STRATEGIES:
        log.error("config.STRATEGIES has both short_intraday and short_overnight - pick one")
        return 1
    rc = 0
    for name in config.STRATEGIES:
        fn = jobs.get(name)
        if fn is None:
            if name not in CLOSE_JOBS and name not in OPEN_JOBS:
                log.error("unknown strategy %r in config.STRATEGIES", name)
                rc = max(rc, 1)
            continue
        rc = max(rc, fn(a, today, mode))
    return rc


if __name__ == "__main__":
    sys.exit(main())
