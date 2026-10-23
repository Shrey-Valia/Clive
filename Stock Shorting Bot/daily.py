#!/usr/bin/env python3
"""The daily runs. Which strategy runs is config.STRATEGIES; v2 ("short_intraday") is the default.

    python3 daily.py                    dry run of the 15:40 job: scan, rank, print, send nothing
    python3 daily.py --execute          cron 15:40 ET: 15:45 scan + save picks, 15:55 cover all shorts
    python3 daily.py --open             dry run of the 09:31 job: re-check the saved picks, print
    python3 daily.py --open --execute   cron 09:31 ET: short the saved picks at market
    python3 daily.py --now              skip the waits (testing)
    python3 daily.py --force            allow a second executed run of the same job on the same day
    python3 daily.py --execute --half-day-only   cron 12:40 ET: the 15:40 job, but only on a half day
    python3 daily.py --execute --fallback        cron 15:50 ET: exit 0 if the 15:40 job started (guard or
                                        close.lock); else cover all at once, scan, long-sleeve entry
    python3 daily.py --cover-leftovers --execute cron 10:00/12:00/14:00 ET: cover every short not
                                        opened today (a missed cover), any time in market hours

v2 schedule (short_intraday), research/PROPOSAL.md
  T   15:45  TradingView top gainers -> short data, borrow, ADV20, headlines -> score -> gates
             (score >= 70, price >= $10, ADV20 >= $10M) -> save up to 5 picks to
             logs/next_open_picks.json. The scan runs in a thread beside step 15:55.
             Also gated: no earnings after the scan-day close or in the next 2 sessions, at most 2
             picks per industry.
  T   15:55  cover every open short. This happens even when the scan fails. No new shorts.
  T+1 09:31  first cover any short not opened today (a leftover from a missed cover), then sell
             the sleeve's longs, then re-check each pick (shortable / ETB, price >= $10, gap up
             <= 40%), short at market, 5% of equity each, gross <= 50%, DAY buy-stop at +35%.

Overnight long sleeve (config.OVERNIGHT_LONG_ENABLED, on since 2026-10-23; README "Overnight long sleeve")
  T   15:55  after the cover-all, buy the same picks: 5% of equity each, long gross <= 25%,
             marketable DAY limit at ask x 1.005 (or MOC, sent at 15:49). logs/overnight_longs.json.
  T+1 09:31  the --open job sells every long at market first, waits for the fills, re-sells what
             is left (up to 2 more passes, until 09:50), and only then shorts the picks.

v1 (short_overnight) is the original bot: 15:55 cancel, net holdings against the new top 5
and hold overnight. It is off; see the README to switch back.
"""
from __future__ import annotations

import argparse
import contextlib
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

from shortingbot import (alerts, broker, config, db, earnings, news, risk, scoring, shortdata,  # noqa: E402
                         strategy, tradingview)

log = logging.getLogger("shortingbot")
GUARD = os.path.join(config.LOG_DIR, "last_execute.json")       # 15:40 job
OPEN_GUARD = os.path.join(config.LOG_DIR, "last_open.json")     # 09:31 job
DRY_PICKS_FILE = config.PICKS_FILE.replace(".json", ".dry_run.json")
MILESTONE = {"milestone": True}
ORDER_ID_SUFFIX = ""        # --force sets one, so a deliberate re-run doesn't collide with today's ids


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


def retry(fn, label: str, tries: int = 3, wait: float = 5):
    """fn() up to `tries` times, `wait` seconds apart. The last error is raised."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            log.warning("%s failed (%s) - retry %d/%d in %gs", label, e, i + 1, tries - 1, wait)
            time.sleep(wait)


# Used only when Alpaca's calendar can't be read: NYSE full-day closures and 13:00 early closes.
NYSE_HOLIDAYS_FALLBACK = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE_FALLBACK = {"2026-11-27", "2026-12-24", "2027-11-26"}


def session_or_fallback():
    """Today's (open, close), None on a holiday. The calendar is retried; if it still fails on
    a weekday a regular 09:30-16:00 session is assumed (13:00 close on a known half day), so the
    15:55 cover still happens. A known NYSE holiday is None: no DAY order goes out."""
    try:
        return retry(broker.session_today, "exchange calendar")
    except Exception as e:
        now = dt.datetime.now(broker.ET)
        day = now.date().isoformat()
        if day in NYSE_HOLIDAYS_FALLBACK:
            log.warning("exchange calendar unavailable (%s) - %s is an NYSE holiday (built-in list)", e, day)
            return None
        if now.weekday() >= 5:
            raise
        close_h = 13 if day in NYSE_EARLY_CLOSE_FALLBACK else 16
        alerts.raise_alert("calendar_failed", f"exchange calendar unavailable ({e}) - assuming a {close_h}:00 close")
        at = lambda h, m: now.replace(hour=h, minute=m, second=0, microsecond=0)
        return at(9, 30), at(close_h, 0)


def order_id(purpose: str, ticker: str, attempt: int = 0, tag: str = "") -> str:
    """Deterministic client_order_id: a POST alpaca-py retries is rejected as a duplicate
    instead of filling twice (broker._send looks the first one up). `tag` tells apart two runs
    of the same job on one day (the --cover-leftovers passes: -L1000, -L1200, ...)."""
    return f"v2-{purpose}-{dt.datetime.now(broker.ET).date()}-{ticker}-{attempt}{tag}{ORDER_ID_SUFFIX}"


# ---------------------------------------------------------------- job locks
class LockBusy(RuntimeError):
    pass


_held_locks: set[str] = set()


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return False                              # ours, but not held: left by a recycled pid
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def job_lock(name: str):
    """logs/<name>.lock, created O_EXCL with our pid, removed on the way out. A lock whose pid is
    dead is cleared. Re-entrant in one process: the 15:40 job holds "close" and its cover_all
    takes it again; monitor.py's flatten takes the same lock, so the two can't cover at once."""
    if name in _held_locks:
        yield
        return
    path = os.path.join(config.LOG_DIR, f"{name}.lock")
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                raw = open(path).read().strip()
                age = time.time() - os.path.getmtime(path)
            except OSError:
                continue                          # gone meanwhile: try again
            pid = int(raw) if raw.isdigit() else None
            if (pid and _pid_alive(pid)) or (pid is None and age < 60):   # no pid yet: being written
                raise LockBusy(f"{name} lock held by pid {pid or '?'} ({os.path.basename(path)})")
            log.warning("clearing stale %s lock (pid %s is not running)", name, pid)
            with contextlib.suppress(OSError):
                os.remove(path)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        break
    else:
        raise LockBusy(f"could not take the {name} lock")
    _held_locks.add(name)
    try:
        yield
    finally:
        _held_locks.discard(name)
        with contextlib.suppress(OSError):
            os.remove(path)


def lock_busy(name: str) -> bool:
    """Another live process holds logs/<name>.lock (a stale one does not count)."""
    path = os.path.join(config.LOG_DIR, f"{name}.lock")
    try:
        raw = open(path).read().strip()
    except OSError:
        return False
    if name in _held_locks:
        return False
    pid = int(raw) if raw.isdigit() else None
    if pid:
        return _pid_alive(pid)
    try:
        return time.time() - os.path.getmtime(path) < 60          # no pid yet: being written
    except OSError:
        return False


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
    "rank", "ticker", "company", "sector", "industry", "close", "prev_close", "change", "high", "low", "VWAP",
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


def write_guard(today: str, sent: list[dict], path: str | None = None, status: str | None = None) -> None:
    """status="started" goes down before any order is sent, so a run that dies mid-way still
    blocks a second run (use --force)."""
    path = path or GUARD
    data = {"date": today, "orders": [s.get("id") for s in sent]}
    shorts = sorted({s["ticker"] for s in sent if s.get("purpose") == "short" and s.get("id")})
    if shorts:
        data["shorts"] = shorts                   # names shorted today: not leftovers (opened_today)
    if status:
        data["status"] = status
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


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
def send_orders(orders: list[dict], wait: int | None = None, attempt: int = 0,
                tag: str = "") -> tuple[list[dict], str | None]:
    """Send market orders one by one. A rejected order is recorded and the rest go on, except
    after a PDT rejection, which stops the batch. Returns (sent, pdt_message). `attempt` goes
    into each client_order_id, so a later cover pass is a new order, not a duplicate."""
    sent, pdt = [], None
    for o in orders:
        if pdt:
            sent.append({"id": None, "ticker": o["ticker"], "side": o["side"], "qty": o["qty"],
                         "status": "rejected", "purpose": o["purpose"], "error": "skipped after PDT rejection"})
            continue
        try:
            sent.append({**broker.submit(o["ticker"], o["qty"], o["side"],
                                         client_order_id=order_id(o["purpose"], o["ticker"], attempt, tag)),
                         "purpose": o["purpose"]})
            log.info("  %s %s %g (%s)", o["side"].upper(), o["ticker"], o["qty"], o["purpose"], extra=MILESTONE)
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


# ---------------------------------------------------------------- ETB history (research)
ETB_FIELDS = ["date", "ticker", "price", "shortable", "etb"]


def log_etb(ranked: list[dict], today: str, execute: bool) -> int:
    """Append every scanned name's Alpaca shortable / easy_to_borrow flags to
    logs/etb_history.csv (dry runs: etb_history.dry_run.csv). Returns rows written. Never raises."""
    name = config.ETB_HISTORY_FILE if execute else config.ETB_HISTORY_FILE.replace(".csv", ".dry_run.csv")
    path = os.path.join(config.LOG_DIR, name)
    try:
        import csv
        new = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=ETB_FIELDS)
            if new:
                w.writeheader()
            for r in ranked:
                flag = lambda k: "" if r.get(k) is None else int(bool(r.get(k)))
                w.writerow({"date": today, "ticker": r["ticker"], "price": r.get("close"),
                            "shortable": flag("shortable"), "etb": flag("easy_to_borrow")})
        return len(ranked)
    except Exception as e:
        log.warning("could not append %s: %s", name, e)
        return 0


# ---------------------------------------------------------------- overnight long sleeve
SLEEVE = "overnight_long"


def long_state_path(execute: bool = True) -> str:
    return os.path.join(config.LOG_DIR, "overnight_longs.json" if execute else "overnight_longs.dry_run.json")


def long_state_has_open(state: dict | None) -> bool:
    """The state file says the sleeve bought something that has not been sold yet."""
    return bool(state and state.get("status") not in ("exited", "flattened")
                and any(o.get("status") != "rejected" for o in state.get("orders") or []))


def long_sleeve_active() -> bool:
    """Run the exit / flatten / monitor checks: the sleeve is on, or it was on and still has
    longs on the books (turning the flag off never strands a position)."""
    return config.OVERNIGHT_LONG_ENABLED or long_state_has_open(load_picks(long_state_path()))


def send_long_orders(orders: list[dict], kind: str, action: str, attempt: int = 0,
                     wait: float | None = None) -> list[dict]:
    """Send the sleeve's orders with broker.submit_long. client_order_id v2-long-<action>-DATE-T-n
    (action buy / sell / flatten). A rejection is recorded and the rest go on."""
    sent = []
    for o in orders:
        try:
            r = broker.submit_long(o["ticker"], o["qty"], o["side"], kind, limit_price=o.get("limit_price"),
                                   client_order_id=order_id(f"long-{action}", o["ticker"], attempt))
            sent.append({**o, **r, "purpose": o["purpose"], "sleeve": SLEEVE})
            log.info("  %s %s %g (%s, %s%s)", o["side"].upper(), o["ticker"], o["qty"], o["purpose"], kind,
                     f" ${o['limit_price']:.2f}" if o.get("limit_price") else "", extra=MILESTONE)
        except Exception as e:
            log.error("  order rejected %s %s: %s", o["side"], o["ticker"], e)
            sent.append({**o, "id": None, "status": "rejected", "error": str(e)[:300], "sleeve": SLEEVE})
    ids = [x["id"] for x in sent if x.get("id")]
    if ids:
        try:
            states = broker.wait_filled(ids, wait) if wait else broker.refresh(ids)
            live = {x["id"]: x for x in states}
            sent = [{**x, **live.get(x.get("id"), {})} for x in sent]
        except Exception as e:
            log.warning("order status refresh failed: %s", e)
    return sent


def long_record(run_id, sent: list[dict]) -> None:
    record_orders(run_id, [{k: v for k, v in x.items() if k not in ("price", "target_pct")} for x in sent])


def exit_longs(run_id, execute: bool, deadline: dt.datetime, purpose: str = "long_exit",
               action: str = "sell", first_kind: str = "market", first_wait: float | None = None,
               before_send=None) -> dict:
    """Sell every long. Returns {"planned", "sent", "left", "problems"}.

    The cover_all pattern for the long side: each pass cancels open orders on the names, waits for
    them to go, re-reads positions and sells exactly what is still long, then waits for the fills.
    Longs left get up to OVERNIGHT_LONG_EXIT_EXTRA_PASSES more passes before `deadline`. Positions
    unreadable after a cancel: nothing is sent (a stale qty could sell past the long into a short).
    Only the first pass may use first_kind ("opg"); later passes are DAY market orders."""
    db.set_step("long_exit")
    held = retry(broker.signed_holdings, "positions")
    plan = strategy.plan_long_exits(held, purpose)
    log.info("long exit: %d longs open %s", len(plan), {p["ticker"]: p["qty"] for p in plan} or "{}", extra=MILESTONE)
    out = {"planned": plan, "sent": [], "left": {}, "problems": []}
    if not execute:
        for p in plan:
            log.info("  DRY RUN would SELL %s %g (%s)", p["ticker"], p["qty"], purpose)
        out["left"] = {p["ticker"]: p["qty"] for p in plan}
        return out
    if not plan:
        return out
    with job_lock("close"):                       # monitor.py's flatten can't sell the same longs at once
        symbols = [p["ticker"] for p in plan]
        for attempt in range(1 + config.OVERNIGHT_LONG_EXIT_EXTRA_PASSES):
            if attempt:
                symbols = sorted(out["left"])
                if dt.datetime.now(broker.ET) > deadline:
                    out["problems"].append(f"past the {deadline:%H:%M} ET long exit deadline after {attempt} passes")
                    break
                log.warning("long exit pass %d: still long %s", attempt + 1, out["left"], extra=MILESTONE)
            found, failed, still_open = 0, False, set()
            try:
                found = broker.cancel_open_orders(symbols)
            except Exception as e:
                failed = True
                log.warning("cancel open orders failed: %s", e)
            try:
                if not broker.wait_no_open_orders(symbols, config.CANCEL_WAIT_SECONDS):
                    still_open = set(broker.open_order_symbols(symbols))
            except Exception as e:
                log.warning("could not check open orders: %s", e)
                still_open = set(symbols) if (found or failed) else set()
            try:
                held = retry(broker.signed_holdings, "positions after cancel")
            except Exception as e:
                out["problems"].append(f"positions unreadable before the long sell - nothing sent ({e})")
                out["left"] = out["left"] or {p["ticker"]: p["qty"] for p in plan}
                break
            long_now = {t: q for t, q in held.items() if t in symbols and q > 0}
            sells = [x for x in strategy.plan_long_exits(long_now, purpose) if x["ticker"] not in still_open]
            out["left"] = long_now
            if not sells:
                if not long_now:
                    break
                continue
            kind = first_kind if attempt == 0 else "market"
            refs = broker.quotes([x["ticker"] for x in sells])
            for x in sells:
                x.update(ref_mid=refs.get(x["ticker"], {}).get("mid"), ref_last=refs.get(x["ticker"], {}).get("last"))
            wait = first_wait if (attempt == 0 and first_wait) else config.OVERNIGHT_LONG_FILL_WAIT_SECONDS
            if before_send and not out["sent"]:
                before_send()
            sent = send_long_orders(sells, kind, action, attempt, wait=wait)
            out["sent"] += sent
            long_record(run_id, sent)
            try:
                out["left"] = {t: q for t, q in retry(broker.signed_holdings, "positions").items()
                               if t in symbols and q > 0}
            except Exception as e:
                log.warning("could not re-read positions after selling: %s", e)
                out["left"] = {x["ticker"]: x["qty"] - (x.get("filled_qty") or 0) for x in sent
                               if x.get("status") != "filled"}
            if not out["left"]:
                break
    for x in out["sent"]:
        if x.get("status") != "filled" and x["ticker"] in out["left"]:
            out["problems"].append(f"long sell {x['ticker']} {x.get('status')}: {x.get('error') or ''}".strip())
    return out


def log_flip_fills(exit_sent: list[dict], short_sent: list[dict]) -> list[dict]:
    """The flip names' fills against the opening print and the midpoint at send. Returns rows."""
    names = sorted({x["ticker"] for x in exit_sent + short_sent})
    if not names:
        return []
    try:
        opens = broker.open_prints(names)
    except Exception as e:
        log.warning("open prints unavailable: %s", e)
        opens = {}
    rows = []
    for x in exit_sent + short_sent:
        fill = x.get("filled_avg_price")
        if not fill:
            continue
        o, mid = opens.get(x["ticker"]), x.get("ref_mid")
        bp = lambda ref: f"{1e4 * (fill / ref - 1):+.0f}bp" if ref else "-"
        row = {"ticker": x["ticker"], "purpose": x.get("purpose"), "qty": x.get("filled_qty"), "fill": fill,
               "open_print": o, "mid_at_send": mid}
        rows.append(row)
        log.info("  fill %s %s %g @ $%.2f | open print %s (%s) | mid at send %s (%s)", x.get("purpose"), x["ticker"],
                 x.get("filled_qty") or 0, fill, f"${o:.2f}" if o else "-", bp(o), f"${mid:.2f}" if mid else "-",
                 bp(mid), extra=MILESTONE)
    return rows


def open_long_exit(a, today: str, session, opened: dt.datetime) -> dict:
    """09:30-09:31 T+1: sell the sleeve's longs before any short goes out. Returns exit_longs()."""
    deadline = opened + dt.timedelta(minutes=config.OVERNIGHT_LONG_EXIT_DEADLINE_MINUTES_AFTER_OPEN)
    state = load_picks(long_state_path())
    run_id = (state or {}).get("run_id")
    kind, first_wait = "market", None
    opg_by = opened - dt.timedelta(minutes=config.OPG_CUTOFF_MINUTES_BEFORE_OPEN)
    if config.OVERNIGHT_LONG_EXIT_ORDER == "opg" and dt.datetime.now(broker.ET) < opg_by and not a.now:
        kind = "opg"
        first_wait = (opened - dt.datetime.now(broker.ET)).total_seconds() + config.OVERNIGHT_LONG_FILL_WAIT_SECONDS
    elif not a.now:
        wait_until(opened + dt.timedelta(seconds=1), "long exit at the open")
    now = dt.datetime.now(broker.ET)
    if a.execute and session is not None and kind != "opg":
        close_cutoff = session[1] - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)
        if not (opened <= now <= close_cutoff):
            try:
                held = {t: q for t, q in retry(broker.signed_holdings, "positions").items() if q > 0}
            except Exception as e:
                log.warning("could not read positions: %s", e)
                held = {o["ticker"]: o.get("filled_qty") or o.get("qty") for o in (state or {}).get("orders") or []
                        if o.get("status") != "rejected"}
            msg = (f"{today}: long exit not sent - {now:%H:%M} ET is outside market hours "
                   f"({opened:%H:%M}-{close_cutoff:%H:%M}); still long {held or 'nothing'}")
            log.error("%s", msg, extra={"step": "long_exit"})
            alerts.raise_alert("long_exit_failed", msg, level="critical")
            return {"planned": [], "sent": [], "left": held, "problems": [msg]}
    out = exit_longs(run_id, a.execute, deadline, first_kind=kind, first_wait=first_wait,
                     before_send=lambda: write_guard(today, [], OPEN_GUARD, status="started"))
    for p in out["problems"]:
        log.error("long exit problem: %s", p, extra={"step": "long_exit"})
    if a.execute:
        if out["sent"]:
            write_guard(today, out["sent"], OPEN_GUARD, status="started")
        if out["left"]:
            alerts.raise_alert("long_exit_failed", f"{today}: still long after the open sell: {out['left']} - "
                                                   f"no short on those names; sell by hand before "
                                                   f"{deadline:%H:%M} ET", level="critical")
        if state:
            state.update(status="exited" if not out["left"] else "exit_incomplete", exited_at=utcnow().isoformat(),
                         exit_orders=[{k: x.get(k) for k in ("ticker", "qty", "id", "status", "filled_qty",
                                                             "filled_avg_price", "ref_mid")} for x in out["sent"]],
                         left=out["left"])
            save_picks(long_state_path(), state)
    return out


def exit_late_longs(today: str, execute: bool, session, skip_hours_check: bool = False, when: str = "") -> dict | None:
    """--cover-leftovers / --fallback: sell the sleeve's longs from an earlier session (state
    entry_date < today and not exited: the 09:31 sell was missed). None when there are none.
    Ids v2-long-sell-L{HHMM}-DATE-T-n. The caller holds close.lock."""
    state = load_picks(long_state_path())
    if not long_state_has_open(state):
        return None
    entry = str(state.get("entry_date") or "")
    if not entry or entry >= today:
        return None
    now = dt.datetime.now(broker.ET)
    cutoff = session[1] - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)
    if not skip_hours_check and not (session[0] <= now <= cutoff):
        log.info("late long exit: outside market hours (%s-%s ET) - not selling", f"{session[0]:%H:%M}", f"{cutoff:%H:%M}")
        return None
    log.warning("late long exit%s: the sleeve's longs from %s were not sold (state %s) - selling",
                when, entry, state.get("status"), extra=MILESTONE)
    out = exit_longs(state.get("run_id"), execute, cutoff if not skip_hours_check else session[1],
                     purpose="long_exit_late", action=f"sell-L{now:%H%M}")
    for p in out["problems"]:
        log.error("late long exit problem: %s", p, extra={"step": "long_exit"})
    if execute:
        if out["sent"] or out["left"]:
            sold = ", ".join(f"{x['ticker']} {x['qty']:g}" for x in out["sent"] if x.get("id")) or "nothing"
            alerts.raise_alert("long_exit_late", f"{today}{when}: sold sleeve longs left from {entry}: {sold}"
                                                 + (f" - STILL LONG {out['left']}" if out["left"] else ""),
                               level="warning")
        if out["left"]:
            alerts.raise_alert("long_exit_failed", f"{today}{when}: sleeve longs from {entry} still open after "
                                                   f"the late sell: {out['left']}", level="critical")
        state.update(status="exited" if not out["left"] else "exit_incomplete", exited_at=utcnow().isoformat(),
                     exit_orders=(state.get("exit_orders") or []) + [
                         {k: x.get(k) for k in ("ticker", "qty", "id", "status", "filled_qty", "filled_avg_price")}
                         for x in out["sent"]], left=out["left"])
        save_picks(long_state_path(), state)
    return out


def enter_longs(a, today: str, run_id, trade_date, picks: list[dict] | None, cutoff: dt.datetime,
                kind: str, exclude: set[str] | None = None, assume_flat: set[str] | None = None) -> dict:
    """T 15:55 (MOC: 15:49): buy v2's picks for the overnight hold. Returns {"entries", "skips", "sent"}.
    assume_flat: a dry run's planned covers, treated as done."""
    db.set_step("long_entry")
    out = {"entries": [], "skips": [], "sent": []}
    if picks is None:
        log.warning("long sleeve: no picks (scan failed or not finished) - no longs today")
        return out
    if config.OVERNIGHT_LONG_UNIVERSE != "v2_picks":
        log.error("long sleeve: OVERNIGHT_LONG_UNIVERSE=%r is not built - no longs", config.OVERNIGHT_LONG_UNIVERSE)
        return out
    if a.execute and dt.datetime.now(broker.ET) > cutoff:
        log.error("long sleeve: past the %s ET cutoff - no longs today", f"{cutoff:%H:%M}")
        return out
    acct = broker.account()
    halted = risk.halted_today(today)
    day_loss, drawdown = strategy.halt_reason(acct, risk.update_peak(acct["equity"]))
    if halted or day_loss or drawdown:
        log.warning("long sleeve: halted (%s) - no longs today", halted or day_loss or drawdown, extra=MILESTONE)
        out["skips"] = [{"ticker": p["ticker"], "reason": "halted"} for p in picks]
        return out
    held = {t: q for t, q in retry(broker.signed_holdings, "positions").items() if t not in (assume_flat or ())}
    long_pct = sum(abs(p["market_value"]) for p in broker.positions() if p["qty"] > 0) / acct["equity"] * 100
    quotes = broker.quotes([p["ticker"] for p in picks]) if picks else {}
    entries, skips = strategy.plan_long_entries(picks, quotes, held, acct["equity"], long_pct, kind, exclude)
    out.update(entries=entries, skips=skips)
    log.info("long sleeve (%s): %d of %d picks, %.1f%% long gross already open", kind, len(entries), len(picks),
             long_pct, extra=MILESTONE)
    for x in skips:
        log.info("  long skip %s: %s", x["ticker"], x["reason"], extra=MILESTONE)
    for e in entries:
        log.info("  plan BUY %s %d @ ~$%.2f (%.1f%%)%s", e["ticker"], e["qty"], e["price"], e["target_pct"],
                 f", limit ${e['limit_price']:.2f}" if e.get("limit_price") else "", extra=MILESTONE)
    state = {"entry_date": today, "exit_date": trade_date and str(trade_date), "run_id": run_id, "kind": kind,
             "saved_at": utcnow().isoformat(), "equity": acct["equity"], "skips": skips}
    if not a.execute:
        log.info("DRY RUN - no long orders sent. Long gross would be %.1f%% of $%.2f.",
                 long_pct + sum(e["target_pct"] for e in entries), acct["equity"], extra=MILESTONE)
        save_picks(long_state_path(False), {**state, "status": "dry_run", "orders": entries})
        return out
    if not entries:
        return out
    save_picks(long_state_path(), {**state, "status": "started", "orders": entries})
    wait = config.OVERNIGHT_LONG_FILL_WAIT_SECONDS if kind == "limit" else None
    sent = send_long_orders(entries, kind, "buy", wait=wait)
    out["sent"] = sent
    long_record(run_id, sent)
    save_picks(long_state_path(), {**state, "status": "entered", "orders": [
        {k: x.get(k) for k in ("ticker", "qty", "id", "status", "filled_qty", "filled_avg_price", "limit_price",
                               "ref_mid", "error")} for x in sent]})
    bad = [x for x in sent if x.get("status") == "rejected"]
    if bad:
        alerts.raise_alert("long_entry_failed", f"{today}: long buys rejected: "
                                                f"{', '.join(x['ticker'] + ' ' + (x.get('error') or '') for x in bad)}"[:900])
    return out


# ---------------------------------------------------------------- v2: short_intraday, 15:40 job
def cover_all(run_id, execute: bool, cutoff: dt.datetime, also_cancel: list[str] | None = None,
              purpose: str = "cover", only: set[str] | None = None, tag: str = "",
              fill_wait: int | None = None) -> dict:
    """Buy back every open short. Returns {"planned", "sent", "problems", "left"}.

    Each pass cancels the open orders on the names (and on `also_cancel`, today's entries, the
    first time), so a protective stop can't fire against a cover, waits until none is open,
    re-reads holdings and sends a cover for exactly what is still short. A stop that filled
    meanwhile has already covered its name. Shorts still open after a pass get up to
    COVER_EXTRA_PASSES more before the cutoff. If holdings can't be re-read after cancelling,
    nothing is sent: a stale quantity could buy past the short into a long.
    `only`: cover just these names (the leftovers), never another short. `tag`: order id tag.
    `fill_wait`: seconds to wait for each pass's fills (default FILL_WAIT_SECONDS).
    """
    db.set_step("cover")
    keep = (lambda t: True) if only is None else (lambda t: t in only)
    held = {t: q for t, q in retry(broker.signed_holdings, "positions").items() if keep(t)}
    covers = strategy.plan_cover(held, purpose)
    log.info("cover: %d shorts open %s", len(covers),
             {c["ticker"]: -c["qty"] for c in covers} or "{}", extra=MILESTONE)
    out = {"planned": covers, "sent": [], "problems": [], "left": {}}
    if not execute:
        for c in covers:
            log.info("  DRY RUN would BUY %s %g (%s)", c["ticker"], c["qty"], purpose)
        return out
    with job_lock("close"):
        symbols = sorted({c["ticker"] for c in covers} | set(also_cancel or []))
        for attempt in range(1 + config.COVER_EXTRA_PASSES):
            if attempt:
                symbols = sorted(out["left"])
                if dt.datetime.now(broker.ET) > cutoff:
                    out["problems"].append(f"past the {cutoff:%H:%M} ET cutoff after {attempt} cover passes")
                    break
                log.warning("cover pass %d: still short %s", attempt + 1, out["left"], extra=MILESTONE)
            if not symbols:
                break
            covers, short_now = fresh_covers(symbols, purpose, covers, attempt)
            if covers is None:
                why = "after cancelling orders" if not attempt else f"on cover pass {attempt + 1}"
                out["problems"].append(f"positions unreadable {why} - covers not sent")
                out["left"] = out["left"] or {c["ticker"]: -c["qty"] for c in out["planned"]}
                alerts.raise_alert("cover_blocked", f"positions could not be re-read {why} on "
                                                    f"{', '.join(symbols)} - nothing sent", level="critical")
                break
            if attempt == 0:
                out["planned"] = covers
            if not covers:                        # flat, or only names whose orders won't go away
                out["left"] = short_now
                if not short_now:
                    break
                continue
            if dt.datetime.now(broker.ET) > cutoff:
                out["problems"].append(f"past the {cutoff:%H:%M} ET cutoff - covers not sent")
                out["left"] = {c["ticker"]: -c["qty"] for c in covers}
                break
            sent, pdt = send_orders(covers, wait=fill_wait or config.FILL_WAIT_SECONDS, attempt=attempt, tag=tag)
            out["sent"] += sent
            record_orders(run_id, sent)
            try:
                out["left"] = {t: q for t, q in retry(broker.signed_holdings, "positions").items()
                               if q < 0 and keep(t)}
            except Exception as e:
                log.warning("could not re-read positions after covering: %s", e)
                out["left"] = {s["ticker"]: -s["qty"] for s in sent if s.get("status") != "filled"}
            if pdt:
                if out["left"]:
                    alerts.raise_alert("pdt_block", pdt + " - the short stays open overnight", level="critical")
                break
            if not out["left"]:
                break
    for s in out["sent"]:
        if s.get("status") == "filled":
            continue
        if s["ticker"] in out["left"]:
            out["problems"].append(f"cover {s['ticker']} {s.get('status')}: {s.get('error') or ''}".strip())
        else:
            log.info("cover %s %s but the position is flat now (stop filled first?)", s["ticker"], s.get("status"))
    return out


def fresh_covers(symbols: list[str], purpose: str, planned: list[dict],
                 attempt: int = 0) -> tuple[list[dict] | None, dict]:
    """One cover pass's plan: cancel open orders on `symbols`, wait for them to go, re-read
    holdings, plan covers for those names only. Returns (covers, {ticker: qty} still short).
    A name whose order would not go away is skipped this pass. covers None = holdings
    unreadable after orders existed (or the cancel failed), or on any pass after the first:
    send nothing (`planned` is the first pass's full quantity, part of it may have filled).
    Unreadable on the first pass with no orders found falls back to `planned`."""
    found, cancel_failed, still_open = 0, False, set()
    try:
        found = broker.cancel_open_orders(symbols)
        if found:
            log.info("cancelled %d open orders (stops included) on %s", found, ", ".join(symbols), extra=MILESTONE)
    except Exception as e:
        cancel_failed = True
        log.warning("cancel open orders failed: %s", e)
    try:
        if not broker.wait_no_open_orders(symbols, config.CANCEL_WAIT_SECONDS):
            still_open = set(broker.open_order_symbols(symbols))
    except Exception as e:
        log.warning("could not check open orders: %s", e)
        still_open = set(symbols) if (found or cancel_failed) else set()
    if still_open:
        log.warning("orders on %s still open after cancelling - no cover for them this pass", ", ".join(sorted(still_open)))
    try:
        held = retry(broker.signed_holdings, "positions after cancel")
    except Exception as e:
        log.error("could not re-read positions after cancelling: %s", e)
        if found or cancel_failed or attempt > 0:
            return None, {}
        planned = [c for c in planned if c["ticker"] in symbols]
        return [c for c in planned if c["ticker"] not in still_open], {c["ticker"]: -c["qty"] for c in planned}
    short_now = {t: q for t, q in held.items() if t in symbols and q < 0}
    return [c for c in strategy.plan_cover(short_now, purpose) if c["ticker"] not in still_open], short_now


# ---------------------------------------------------------------- catch-ups: leftover shorts
def opened_today(today: str) -> set[str] | None:
    """Names shorted today: the open guard's "shorts", the picks file's entries for today, and
    Alpaca's filled sells today. None when Alpaca can't be read and the 09:31 job left an
    unfinished guard (it may have shorted names this file doesn't list): don't guess."""
    names: set[str] = set()
    try:
        g = json.load(open(OPEN_GUARD))
    except (OSError, ValueError):
        g = {}
    if g.get("date") == today:
        names |= set(g.get("shorts") or [])
    picks = load_picks(config.PICKS_FILE) or {}
    if picks.get("trade_date") == today:
        names |= {e["ticker"] for e in picks.get("entered") or [] if e.get("id") and e.get("status") != "rejected"}
    try:
        names |= broker.sold_today()
    except Exception as e:
        log.warning("could not read today's sell orders: %s", e)
        if g.get("date") == today and g.get("status") == "started":
            if today not in _unreadable_alerted:          # once per run: the 09:31 job checks twice
                _unreadable_alerted.add(today)
                alerts.raise_alert("cover_failed", f"{today}: leftover check unreadable - today's sells could not "
                                                   f"be read ({e}) and the 09:31 run is unfinished; leftover shorts "
                                                   f"not covered, check by hand", level="critical")
            return None
    return names


_unreadable_alerted: set[str] = set()


def leftover_shorts(today: str, held: dict[str, float] | None = None) -> dict[str, float] | None:
    """{ticker: qty < 0} for every short not opened today. None: can't tell (see opened_today)."""
    held = retry(broker.signed_holdings, "positions") if held is None else held
    shorts = {t: q for t, q in held.items() if q < 0}
    if not shorts:
        return {}
    mine = opened_today(today)
    if mine is None:
        return None
    return {t: q for t, q in shorts.items() if t not in mine}


def cover_leftovers(run_id, today: str, execute: bool, cutoff: dt.datetime, tag: str = "",
                    when: str = "") -> dict:
    """Cover the leftover shorts with cover_all (close.lock, passes, ids v2-leftover-DATE-T-n<tag>).
    Returns cover_all's dict plus "leftovers". Raises LockBusy when another cover is running."""
    left = leftover_shorts(today)
    if left is None:
        log.error("leftover check: today's sells unreadable and the 09:31 run is unfinished - not covering")
        _unreadable_alerted.discard(today)                # a later run (10:00, ...) alerts again
        return {"planned": [], "sent": [], "problems": ["leftover check unreadable"], "left": {}, "leftovers": {}}
    if not left:
        log.info("leftover check%s: no shorts from an earlier session", when, extra=MILESTONE)
        return {"planned": [], "sent": [], "problems": [], "left": {}, "leftovers": {}}
    log.warning("leftover check%s: %d shorts from an earlier session %s - covering", when, len(left), left,
                extra=MILESTONE)
    out = cover_all(run_id, execute, cutoff, purpose="leftover", only=set(left), tag=tag,
                    fill_wait=config.LEFTOVER_FILL_WAIT_SECONDS)
    out["leftovers"] = left
    for p in out["problems"]:
        log.error("leftover cover problem: %s", p, extra={"step": "cover"})
    if execute:
        names = ", ".join(f"{t} {-q:g}" for t, q in sorted(left.items()))
        alerts.raise_alert("leftover_covered", f"{today}{when}: covered shorts left from an earlier session: {names}"
                                               + (f" - STILL SHORT {out['left']}" if out["left"] else ""),
                           level="warning")
        if out["left"]:
            alerts.raise_alert("cover_failed", f"{today}{when}: leftover shorts still open after the cover: "
                                               f"{out['left']}", level="critical")
    return out


def cover_leftovers_job(a, today: str, mode: str) -> int:
    """`daily.py --cover-leftovers [--execute]`: any time in market hours (cron 10:00, 12:00, 14:00),
    cover every short not opened today, then sell the sleeve's longs from an earlier session (a
    missed 09:31 sell), both under close.lock. Never enters anything. Exits 0 outside market
    hours, while the 09:31 job runs, or while another cover holds close.lock."""
    if "short_intraday" not in config.STRATEGIES:
        log.info("--cover-leftovers: short_intraday is not in config.STRATEGIES - nothing to do")
        return 0
    try:
        session = session_or_fallback()
        if session is None:
            log.info("--cover-leftovers: market closed today - nothing to do")
            return 0
        now = dt.datetime.now(broker.ET)
        cutoff = session[1] - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)
        if not a.now and not (session[0] <= now <= cutoff):
            log.info("--cover-leftovers: outside market hours (%s-%s ET) - nothing to do",
                     f"{session[0]:%H:%M}", f"{cutoff:%H:%M}")
            return 0
        if a.execute and lock_busy("open"):
            log.info("--cover-leftovers: the 09:31 job is running (it covers leftovers itself) - skipping")
            return 0
        when = f" ({now:%H:%M} safety net)"
        with job_lock("close") if a.execute else contextlib.nullcontext():
            try:
                out = cover_leftovers(None, today, a.execute, cutoff if not a.now else session[1],
                                      tag=f"-L{now:%H%M}", when=when)
            except LockBusy:
                raise
            except Exception as e:                 # the late long sell below still runs
                log.error("leftover cover failed: %s\n%s", e, traceback.format_exc(), extra={"step": "cover"})
                alerts.raise_alert("cover_failed", f"{today}{when}: leftover cover failed: {e}", level="critical")
                out = {"planned": [], "sent": [], "problems": [str(e)], "left": {}, "leftovers": {}, "error": True}
            longs = exit_late_longs(today, a.execute, session, skip_hours_check=a.now, when=when)
        db.events.flush_to(None)
        if a.execute and (out["left"] or out.get("error") or (longs and longs["left"])):
            return 1
        return 2 if out["problems"] or (longs and longs["problems"]) else 0
    except LockBusy as e:
        log.info("--cover-leftovers: %s - another cover is running, skipping", e)
        return 0
    except Exception as e:
        log.error("cover-leftovers run failed: %s\n%s", e, traceback.format_exc())
        alerts.raise_alert("run_failed", f"{today} cover-leftovers job: {e}", level="critical")
        return 1


def next_sessions_or_weekdays(today: str, n: int) -> list[dt.date]:
    """The n sessions after today from the calendar, else the next n weekdays."""
    d = dt.date.fromisoformat(today)
    try:
        out = broker.next_sessions(d, n)
        if len(out) == n:
            return out
    except Exception as e:
        log.warning("next sessions lookup failed (%s) - using weekdays for the earnings window", e)
    out = []
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def close_intraday(a, today: str, mode: str) -> int:
    run_id, prev_run_id, cover, scan_box = None, None, None, {}
    fallback = bool(getattr(a, "fallback", False))
    try:
        db.set_step("setup")
        session = session_or_fallback()
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
            if fallback:
                log.info("--fallback: today's close job already started or finished - nothing to do")
                return 0
            log.error("already executed today - use --force to run again")
            return 1
        if fallback:
            now = dt.datetime.now(broker.ET)
            if now > cutoff and not a.now:
                held = {t: q for t, q in retry(broker.signed_holdings, "positions").items() if q < 0}
                alerts.raise_alert("close_missed", f"{today}: the close job never ran and the fallback started at "
                                                   f"{now:%H:%M}, past the {cutoff:%H:%M} ET cutoff - still short "
                                                   f"{held or 'nothing'}; the 09:31 job covers them",
                                   level="critical")
                return 1
            if a.execute:
                alerts.raise_alert("close_fallback", f"{today}: the close job did not start - fallback covering "
                                                     f"now ({now:%H:%M} ET)", level="warning")

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

        if not a.now and not fallback:
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
                n_earn = earnings.annotate(ranked, dt.date.fromisoformat(today),
                                           next_sessions_or_weekdays(today, config.EARNINGS_SESSIONS))
                if n_earn:
                    log.info("earnings gate: %d of %d names excluded", n_earn, len(ranked), extra=MILESTONE)
                picks = strategy.select(ranked, acct["equity"])
                log_etb(ranked, today, a.execute)
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

        entered = [e["ticker"] for e in (prev or {}).get("entered") or []] if prev_run_id else []
        long_out = None
        long_kind = config.OVERNIGHT_LONG_ENTRY_ORDER
        if config.OVERNIGHT_LONG_ENABLED and long_kind == "moc":
            # MOC must be in by 15:50, so it can't wait for the 15:55 cover: wait for the scan until 15:49:30
            moc_by = close - dt.timedelta(minutes=config.MOC_CUTOFF_MINUTES_BEFORE_CLOSE, seconds=30)
            while t_scan.is_alive() and (a.now or dt.datetime.now(broker.ET) < moc_by) and time.time() < scan_deadline:
                t_scan.join(timeout=5.0)
            with lock:
                picks_now = scan_box.get("picks")
            held_now = retry(broker.signed_holdings, "positions")
            if a.execute:
                write_guard(today, [], status="started")
            long_out = enter_longs(a, today, run_id, trade_date, picks_now, moc_by if not a.now else close, "moc",
                                   exclude=set(entered) | {t for t, q in held_now.items() if q})

        # 15:55: cover, whatever the scan is doing (the fallback covers at once)
        if not a.now and not fallback:
            while t_scan.is_alive() and dt.datetime.now(broker.ET) < trade_at:
                t_scan.join(timeout=max(0.0, min(5.0, (trade_at - dt.datetime.now(broker.ET)).total_seconds())))
            wait_until(trade_at, "cover")
        if a.execute:
            write_guard(today, [], status="started")
        cover = cover_all(prev_run_id or run_id, a.execute, cutoff if not a.now else close, entered)
        if a.execute:
            write_guard(today, cover["sent"])
        for p in cover["problems"]:
            log.error("cover problem: %s", p, extra={"step": "cover"})
        if cover["left"] and a.execute:
            alerts.raise_alert("cover_failed", f"still short after the 15:55 cover: {cover['left']}",
                               level="critical")
        if fallback and session is not None:       # last night's sleeve longs the 09:31 job missed
            try:
                exit_late_longs(today, a.execute, session, skip_hours_check=a.now, when=" (15:50 fallback)")
            except Exception as e:
                log.error("late long exit failed: %s\n%s", e, traceback.format_exc(), extra={"step": "long_exit"})
                alerts.raise_alert("long_exit_failed", f"{today}: fallback late long exit failed: {e}", level="critical")
        db.events.flush_to(prev_run_id or run_id)

        t_scan.join(timeout=max(0.0, scan_deadline - time.time()))
        db.set_step("finish")
        with lock:
            if t_scan.is_alive():
                scan_box["abandoned"] = True
                scan_box["error"] = RuntimeError(f"scan still running after {config.SCAN_TIMEOUT_SECONDS}s")
                log.error("scan timed out - no picks saved for %s", trade_date)

        # 15:55 overnight long sleeve, after the cover-all (cover_all only ever buys back shorts)
        if config.OVERNIGHT_LONG_ENABLED and long_kind != "moc":
            try:
                long_out = enter_longs(a, today, run_id, trade_date, scan_box.get("picks"),
                                       cutoff if not a.now else close, long_kind,
                                       assume_flat=None if a.execute else {c["ticker"] for c in cover["planned"]})
            except Exception as e:
                log.error("long sleeve entry failed: %s\n%s", e, traceback.format_exc(), extra={"step": "long_entry"})
                alerts.raise_alert("long_entry_failed", f"{today}: long sleeve entry failed: {e}", level="critical")
        if a.execute and long_out and long_out["sent"]:
            write_guard(today, cover["sent"] + long_out["sent"])

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
        if long_out and (long_out["entries"] or long_out["skips"]):
            msg.append(f"long sleeve: {'bought' if a.execute else 'would buy'} "
                       f"{len(long_out['sent'] if a.execute else long_out['entries'])}, "
                       f"skipped {len(long_out['skips'])}")
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
            o = {**broker.submit_stop(s["ticker"], qty, level, client_order_id=order_id("stop", s["ticker"])),
                 "purpose": "stop"}
            log.info("  STOP BUY %s %d @ $%.2f (+%g%%)", s["ticker"], qty, level, config.BROKER_STOP_PCT, extra=MILESTONE)
        except Exception as e:
            alerts.raise_alert("stop_failed", f"stop for {s['ticker']} at ${level:.2f} rejected: {e}", ticker=s["ticker"])
            o = {"id": None, "ticker": s["ticker"], "side": "buy", "qty": qty, "status": "rejected",
                 "purpose": "stop", "error": str(e)[:300]}
        stops.append({**o, "stop_price": level})
    record_orders(run_id, stops)
    return stops


def halt_and_flatten(today: str, reason: str, run_id, session) -> str:
    """Daily loss halt: write logs/halt.json, alert, cover every short (stops cancelled first).
    With the long sleeve on (or holding longs), every long is sold too."""
    risk.set_halt(today, reason)
    alerts.raise_alert("daily_loss_halt", f"{today}: {reason} - flattening, no new entries today", level="critical")
    close = session[1] if session else dt.datetime.now(broker.ET) + dt.timedelta(hours=1)
    try:
        out = cover_all(run_id, True, close - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE),
                        purpose="flatten")
    except LockBusy as e:
        alerts.raise_alert("cover_failed", f"halt flatten not run: {e} (another job is covering)", level="critical")
        return reason
    if out["left"]:
        alerts.raise_alert("cover_failed", f"halt flatten left shorts open: {out['left']}", level="critical")
    if long_sleeve_active():
        try:
            lo = exit_longs(run_id, True, close - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE),
                            purpose="flatten_long", action="flatten")
        except LockBusy as e:
            alerts.raise_alert("long_exit_failed", f"halt flatten of longs not run: {e}", level="critical")
            return reason
        if lo["left"]:
            alerts.raise_alert("long_exit_failed", f"halt flatten left longs open: {lo['left']}", level="critical")
        state = load_picks(long_state_path())
        if state and lo["sent"] and not lo["left"]:
            state.update(status="flattened", exited_at=utcnow().isoformat())
            save_picks(long_state_path(), state)
    return reason


def finish_open_guard(a, today: str) -> None:
    """An early return of the 09:31 job (no picks, stale picks, nothing to enter): a "started"
    open guard left by the leftover cover or the long exit becomes "done". Reruns stay blocked;
    opened_today no longer treats the run as unfinished."""
    if not a.execute:
        return
    try:
        g = json.load(open(OPEN_GUARD))
    except (OSError, ValueError):
        return
    if g.get("date") != today or g.get("status") != "started":
        return
    g["status"] = "done"
    tmp = OPEN_GUARD + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(g, fh)
    os.replace(tmp, OPEN_GUARD)


def open_intraday(a, today: str, mode: str) -> int:
    run_id = None
    try:
        db.set_step("setup")
        session = session_or_fallback()               # retried; weekday 09:30-16:00 if the calendar is down
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

        # catch-up: a short from an earlier session (missed 15:40 job) is covered first, before
        # anything can return early. Allowed up to the close cutoff, even when entries are late.
        leftover = None
        if config.COVER_LEFTOVERS_AT_OPEN and session is not None:
            close_cutoff = session[1] - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)
            try:
                pending = leftover_shorts(today)          # None: can't tell, cover_leftovers says so
                if pending is None or pending:
                    if a.execute and not a.now:
                        wait_until(opened + dt.timedelta(seconds=1), "leftover cover at the open")
                    if a.execute and pending:
                        write_guard(today, [], OPEN_GUARD, status="started")
                    leftover = cover_leftovers(None, today, a.execute, close_cutoff if not a.now else session[1],
                                               when=" (09:31)")
            except LockBusy as e:
                alerts.raise_alert("cover_failed", f"{today}: leftover cover at the open not run: {e} "
                                                   f"(another cover is running)", level="critical")
            except Exception as e:                 # never costs the long exit or the entries below
                log.error("leftover cover at the open failed: %s\n%s", e, traceback.format_exc(), extra={"step": "cover"})
                alerts.raise_alert("cover_failed", f"{today}: leftover cover at the open failed: {e} - "
                                                   f"check for shorts left from an earlier session", level="critical")

        # overnight long sleeve: sell the longs at the open, before anything else can return early
        long_exit = open_long_exit(a, today, session, opened) if long_sleeve_active() else None

        path = a.picks or (config.PICKS_FILE if a.execute or not os.path.exists(DRY_PICKS_FILE) else DRY_PICKS_FILE)
        data = load_picks(path)
        if data is None:
            alerts.raise_alert("no_picks", f"{today}: no picks file at {os.path.basename(path)} - nothing entered")
            db.heartbeat("warning", f"{today}: no picks file")
            finish_open_guard(a, today)
            return 0
        run_id = data.get("run_id")
        if data.get("trade_date") != today and not (a.now and not a.execute):
            alerts.raise_alert("stale_picks", f"{today}: picks file is for {data.get('trade_date')} "
                                              f"(scanned {data.get('scan_date')}) - nothing entered")
            db.heartbeat("warning", f"{today}: stale picks file")
            finish_open_guard(a, today)
            db.events.flush_to(run_id)
            return 0
        picks = data.get("picks") or []
        log.info("open: %d picks from the %s scan (run %s): %s", len(picks), data.get("scan_date"), run_id,
                 ", ".join(data.get("tickers") or []) or "none", extra=MILESTONE)
        if not picks:
            if a.execute:
                db.update("short_runs", {"id": run_id}, {"status": "no_entries", "message": "no picks at the scan"})
            db.heartbeat("ok", f"{today}: no picks to enter")
            finish_open_guard(a, today)
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
            finish_open_guard(a, today)
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
        sleeve_longs = set(long_exit["left"]) | {x["ticker"] for x in long_exit["planned"]} if long_exit else set()
        if long_exit and not a.execute:           # dry run: the longs would be sold first, then shorted
            held = {t: q for t, q in held.items() if not (q > 0 and t in sleeve_longs)}
        dry_flat = set((leftover or {}).get("leftovers") or {}) if not a.execute else set()
        if dry_flat:                              # dry run: the leftovers would be covered first
            held = {t: q for t, q in held.items() if t not in dry_flat}
            positions = [p for p in positions if p["ticker"] not in dry_flat]
        existing = sum(abs(p["market_value"]) for p in positions if p["qty"] < 0) / acct["equity"] * 100
        leftover = {t: q for t, q in held.items() if q < 0}
        if leftover:
            alerts.raise_alert("overnight_leftover", f"{today}: shorts still open from before the open: {leftover} "
                                                     f"({existing:.0f}% of equity, counted against the gross cap)")
        tickers = [p["ticker"] for p in picks]
        borrow = broker.borrow(tickers)
        prices = broker.last_prices(tickers, today_only=session is not None and dt.datetime.now(broker.ET) >= opened)
        entries, skips = strategy.recheck_at_open(picks, borrow, prices, held, acct["equity"], existing,
                                                  sleeve_longs=sleeve_longs)
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

        flips = sorted({x["ticker"] for x in long_exit["sent"]}) if long_exit else []
        mids = broker.quotes(flips) if flips else {}     # midpoints at send, for the flip fill log
        write_guard(today, [], OPEN_GUARD, status="started")
        sent, pdt = send_orders(entries, wait=config.FILL_WAIT_SECONDS if config.BROKER_STOP_PCT else None)
        record_orders(run_id, sent)
        write_guard(today, sent, OPEN_GUARD)
        stops = place_stops(run_id, sent, entries) if config.BROKER_STOP_PCT else []
        if flips:
            log_flip_fills(long_exit["sent"], [{**x, "ref_mid": mids.get(x["ticker"], {}).get("mid")}
                                               for x in sent if x["ticker"] in flips])
            write_guard(today, long_exit["sent"] + sent, OPEN_GUARD)
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


# ---------------------------------------------------------------- 15:50 fallback, close.lock held
FALLBACK_LOCK_POLL_SECONDS = 5
FALLBACK_LOCK_WAIT_MINUTES_BEFORE_CLOSE = 3      # poll for the lock until 15:57


def fallback_lock_held(a, today: str, mode: str, busy: Exception) -> int:
    """--fallback found close.lock held (the 15:40 job waiting for 15:55, or a cover / flatten
    running). Poll until the lock clears or ~15:57, then re-check the guard: started or done ->
    0. Lock cleared, no guard -> the normal fallback. Still held, no guard -> the holder is stuck
    before its cover: cover everything now (ids v2-cover-DATE-T-n, the 15:40 job's own, so the
    two can't both fill) and raise a critical alert."""
    log.info("--fallback: %s - waiting for it", busy)
    try:
        session = session_or_fallback()
    except Exception as e:
        alerts.raise_alert("close_missed", f"{today}: fallback found close.lock held and the calendar "
                                           f"unavailable ({e}) - not covering", level="critical")
        return 1
    if session is None:
        return 0
    close = session[1]
    until = close - dt.timedelta(minutes=FALLBACK_LOCK_WAIT_MINUTES_BEFORE_CLOSE)
    cutoff = close - dt.timedelta(minutes=config.CUTOFF_MINUTES_BEFORE_CLOSE)
    polls = max(0, int((until - dt.datetime.now(broker.ET)).total_seconds() // FALLBACK_LOCK_POLL_SECONDS) + 1)
    for _ in range(polls):
        if not lock_busy("close") or guard_blocks(today) or dt.datetime.now(broker.ET) >= until:
            break
        time.sleep(FALLBACK_LOCK_POLL_SECONDS)
    if guard_blocks(today):
        log.info("--fallback: today's close job started or finished - nothing to do")
        return 0
    if not lock_busy("close"):
        log.warning("--fallback: close.lock cleared and the close job has not started - running the fallback")
        try:
            with job_lock("close"):
                if guard_blocks(today):
                    log.info("--fallback: today's close job started meanwhile - nothing to do")
                    return 0
                return close_intraday(a, today, mode)
        except LockBusy as e:
            busy = e                               # taken again meanwhile: treat as stuck below
    if guard_blocks(today):
        return 0
    now = dt.datetime.now(broker.ET)
    if now > cutoff:
        alerts.raise_alert("close_missed", f"{today}: close.lock held ({busy}) and the close job never started; "
                                           f"past the {cutoff:%H:%M} ET cutoff - nothing sent, the 09:31 job "
                                           f"covers the shorts", level="critical")
        return 1
    alerts.raise_alert("close_fallback", f"{today}: close.lock still held at {now:%H:%M} ET ({busy}) and the close "
                                         f"job never started - fallback covering every short now", level="critical")
    _held_locks.add("close")                       # the holder is stuck: cover past its lock
    try:
        write_guard(today, [], status="started")
        cover = cover_all(None, True, cutoff)
        write_guard(today, cover["sent"])
    except Exception as e:
        log.error("fallback cover failed: %s\n%s", e, traceback.format_exc())
        alerts.raise_alert("cover_failed", f"{today}: fallback cover past a stuck close.lock failed: {e}",
                           level="critical")
        return 1
    finally:
        _held_locks.discard("close")
    for p in cover["problems"]:
        log.error("cover problem: %s", p, extra={"step": "cover"})
    if cover["left"]:
        alerts.raise_alert("cover_failed", f"still short after the fallback cover: {cover['left']}", level="critical")
        return 1
    db.events.flush_to(None)
    return 2                                       # covered, but only past a stuck lock


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
    ap.add_argument("--half-day-only", action="store_true",
                    help="exit at once unless today's session closes before 16:00 (the 12:40 cron line)")
    ap.add_argument("--fallback", action="store_true",
                    help="15:50 safety net for the close job: exit 0 if it started; else cover now, then scan")
    ap.add_argument("--cover-leftovers", action="store_true",
                    help="cover every short not opened today, any time in market hours (10:00/12:00/14:00)")
    a = ap.parse_args(argv)
    if a.open and (a.fallback or a.cover_leftovers):
        ap.error("--open cannot be combined with --fallback or --cover-leftovers")

    setup_logging()
    broker.load_env()
    mode = "execute" if a.execute else "dry_run"
    today = dt.datetime.now(broker.ET).date().isoformat()
    job = "open" if a.open else ("leftovers" if a.cover_leftovers else "close")
    global ORDER_ID_SUFFIX
    ORDER_ID_SUFFIX = f"-f{dt.datetime.now(broker.ET):%H%M%S}" if a.force else ""
    if a.half_day_only:
        try:
            session = session_or_fallback()
        except Exception as e:
            log.info("--half-day-only: calendar unavailable (%s) - nothing to do", e)
            return 0
        if session is None or session[1].hour >= 16:
            log.info("--half-day-only: %s - nothing to do",
                     "market closed today" if session is None else f"regular close {session[1]:%H:%M} ET")
            return 0
    if a.cover_leftovers:
        log.info("=== daily cover-leftovers run %s | %s ===", today, mode)
        return cover_leftovers_job(a, today, mode)
    if a.fallback and a.execute and guard_blocks(today) and not a.force:
        log.info("--fallback: today's close job already started or finished - nothing to do")
        return 0
    log.info("=== daily %s run %s | %s | %s%s ===", job, today, mode, ", ".join(config.STRATEGIES),
             " | fallback" if a.fallback else "")

    jobs = OPEN_JOBS if a.open else CLOSE_JOBS
    if "short_intraday" in config.STRATEGIES and "short_overnight" in config.STRATEGIES:
        log.error("config.STRATEGIES has both short_intraday and short_overnight - pick one")
        return 1
    rc = 0
    try:
        # one executed run of a job at a time; the close lock also keeps monitor.py's flatten out
        with job_lock(job) if a.execute else contextlib.nullcontext():
            for name in config.STRATEGIES:
                fn = jobs.get(name)
                if fn is None:
                    if name not in CLOSE_JOBS and name not in OPEN_JOBS:
                        log.error("unknown strategy %r in config.STRATEGIES", name)
                        rc = max(rc, 1)
                    continue
                rc = max(rc, fn(a, today, mode))
    except LockBusy as e:
        if a.fallback:
            return fallback_lock_held(a, today, mode, e)
        log.error("%s - another %s run is going, not starting", e, job)
        alerts.raise_alert("job_locked", f"{today} {job} job not run: {e}", level="critical")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
