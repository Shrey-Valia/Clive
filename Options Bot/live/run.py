#!/usr/bin/env python3
"""Live paper trading: RSI(2)/IBS dip -> 7-14 DTE 0.25/0.10 delta put credit spread.

    python3 live/run.py                    dry run: wait for 15:50 ET, print what it would do
    python3 live/run.py --now              dry run immediately (any time; uses the latest prices)
    python3 live/run.py --execute          cron 15:45 ET: exits + entries at 15:50, cancel by 15:58
    python3 live/run.py --intraday --execute   take-profit / stop checks only (every 15 min)
    python3 live/run.py --check            health check (keys, paper account, data, VIX, Supabase)
    python3 live/run.py --close-all        emergency: show how every spread would be closed
    python3 live/run.py --close-all --execute  ... and close them now (aggressive prices)

Only --execute sends orders, and only to the dedicated options PAPER account
(OPT_APCA_* in "Options Bot/.env"). See live/README.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import logging
import math
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live import config as C  # noqa: E402
from live import db, execution, signals, state  # noqa: E402
from live import strategy as S  # noqa: E402
from live.broker import ET, Broker, BrokerError, SafetyError  # noqa: E402

log = logging.getLogger("optionslive")
# Known early closes (13:00 ET), used only when no options account (calendar) is available.
EARLY_CLOSES = {"2026-11-27", "2026-12-24", "2027-07-02", "2027-11-26", "2027-12-23"}


def setup_logging() -> None:
    if log.handlers:
        return
    C.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(C.LOG_DIR / f"{dt.datetime.now(ET):%Y-%m-%d}.log")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


# ---------------------------------------------------------------- session
def market_session(broker: Broker, today: dt.date) -> dict:
    """{'open': bool, 'open_at', 'close_at', 'source'} for today."""
    if broker.has_trading:
        cal = broker.calendar(today, today) or []
        if not cal or cal[0]["date"] != today.isoformat():
            return {"open": False, "source": "alpaca calendar"}
        o = dt.datetime.combine(today, dt.time.fromisoformat(cal[0]["open"]), ET)
        c = dt.datetime.combine(today, dt.time.fromisoformat(cal[0]["close"]), ET)
        return {"open": True, "open_at": o, "close_at": c, "source": "alpaca calendar"}
    if today.weekday() >= 5:
        return {"open": False, "source": "weekend"}
    snap = broker.stock_snapshot("SPY")
    if snap["bar_date"] != today:
        return {"open": False, "source": f"no SPY bar today (last {snap['bar_date']})"}
    close = dt.time(13, 0) if today.isoformat() in EARLY_CLOSES else dt.time(16, 0)
    return {"open": True, "open_at": dt.datetime.combine(today, dt.time(9, 30), ET),
            "close_at": dt.datetime.combine(today, close, ET), "source": "SPY bar + weekday (no calendar)"}


def sessions_list(broker: Broker, hist_index, today: dt.date) -> list[dt.date]:
    cal = broker.calendar(today + dt.timedelta(days=1), today + dt.timedelta(days=40)) if broker.has_trading else None
    return signals.sessions_from_bars(hist_index, today, calendar=cal)


# ---------------------------------------------------------------- planning
class Ctx:
    """Per-run caches (daily history, live signals) so each symbol is fetched once."""

    def __init__(self, broker: Broker, today: dt.date):
        self.broker, self.today = broker, today
        self.hist: dict = {}
        self.sig: dict = {}

    def signal(self, sym: str) -> S.Signal:
        if sym not in self.sig:
            if sym not in self.hist:
                self.hist[sym] = self.broker.daily_bars(sym, self.today - dt.timedelta(days=signals.HISTORY_DAYS),
                                                        self.today - dt.timedelta(days=1))
            sig, bars, lb = signals.signal_for(self.broker, sym, self.today, self.hist[sym])
            sig.live = lb
            self.sig[sym] = S.entry_signal(sig)
        return self.sig[sym]


def plan_exits(ctx: Ctx, records: dict, sessions: list[dt.date], end_of_day: bool, force_all: bool = False):
    """Returns (jobs, rows) for open spreads that should close now."""
    jobs, rows = [], []
    if not records:
        return jobs, rows
    syms = sorted({r[k] for r in records.values() for k in ("short_symbol", "long_symbol")})
    quotes = ctx.broker.option_quotes(syms)
    for sid, r in sorted(records.items()):
        sq, lq = quotes.get(r["short_symbol"]), quotes.get(r["long_symbol"])
        width = r["short_strike"] - r["long_strike"]
        mid = natural = None
        if sq and lq and sq.valid and lq.valid:
            mid = round(sq.mid - lq.mid, 4)
            natural = round(sq.ask - lq.bid, 4)
        exp = dt.date.fromisoformat(r["expiry"])
        held = S.held_trading_days(dt.date.fromisoformat(r["entry_date"]), ctx.today, sessions)
        tdte = S.trading_days_until(exp, ctx.today, sessions)
        try:
            sig = ctx.signal(r["underlying"])
            close, sma5 = sig.close, sig.sma5
        except Exception as e:
            log.error("%s: no live features for exit check: %s", sid, e)
            close, sma5 = math.nan, math.nan
        reason = "emergency_close" if force_all else S.exit_reason(
            entry_credit=float(r["entry_credit"]), cost_to_close=mid, close=close, sma5=sma5,
            held_days=held, trading_days_to_expiry=tdte, end_of_day=end_of_day)
        row = {"id": sid, "qty": r["qty"], "entry_credit": r["entry_credit"], "mid_debit": mid,
               "natural_debit": natural, "held": held, "tdte": tdte, "close": close, "sma5": sma5,
               "reason": reason}
        rows.append(row)
        if not reason:
            continue
        if mid is None:
            log.error("%s: wants %s but has no two-sided quotes; NOT closing automatically", sid, reason)
            row["reason"] = f"{reason} (blocked: no quotes)"
            continue
        if force_all:
            ladder = S.exit_ladder(mid, natural, steps=2)
        else:
            ladder = S.exit_ladder(mid, natural)
        jobs.append(execution.Job(sid, "close", execution.close_legs(r["short_symbol"], r["long_symbol"]),
                                  int(r["qty"]), ladder, credit=False,
                                  cutoff_price=S.exit_cutoff_price(natural, width),
                                  meta={"record": r, "reason": reason, "mid": mid, "natural": natural}))
    return jobs, rows


def open_max_loss(records: dict) -> float:
    return sum(S.max_loss(r["short_strike"] - r["long_strike"], float(r["entry_credit"]), int(r["qty"]))
               for r in records.values())


def plan_entries(ctx: Ctx, records: dict, acct: dict, kill: tuple[bool, str], symbols: list[str],
                 preview: bool = False):
    """Entry jobs for symbols with a signal. ``preview`` also prices the spread
    for symbols without one (display only; never produces a job)."""
    jobs, rows = [], []
    used = open_max_loss(records)
    bp = acct.get("options_buying_power")
    held = {r["underlying"] for r in records.values()}
    for sym in symbols:
        row = {"symbol": sym}
        rows.append(row)
        try:
            sig = ctx.signal(sym)
        except Exception as e:
            row["action"] = f"skip: data error {e}"
            continue
        row.update(close=sig.close, sma200=sig.sma200, rsi2=sig.rsi2, ibs=sig.ibs, src=sig.live["source"])
        if sym in held:
            row["action"] = "skip: position already open"
            continue
        blocked = None
        if not sig.ok:
            blocked = "no signal: " + "; ".join(sig.reasons)
        elif kill[0]:
            blocked = f"signal, but kill switch: {kill[1]}"
        if blocked and not preview:
            row["action"] = blocked
            continue
        exp_hi = ctx.today + dt.timedelta(days=C.DTE_MAX)
        if sym in C.SINGLE_NAMES:
            from optionsbot.strategies_signals import EARNINGS
            import pandas as pd
            if any(pd.Timestamp(ctx.today) <= e <= pd.Timestamp(exp_hi) for e in EARNINGS.get(sym, [])):
                row["action"] = "signal, but earnings before expiry"
                continue
        chain = ctx.broker.option_chain(sym, ctx.today + dt.timedelta(days=C.DTE_MIN), exp_hi)
        pick, why = S.pick_spread(chain, sym, ctx.today)
        if pick is None:
            row["action"] = f"signal, but no spread: {why}"
            continue
        row["pick"] = pick
        bad = S.check_pick(pick)
        if blocked:
            n, why = S.size_position(acct["equity"], pick.width, pick.mid_credit, used, bp)
            row["action"] = f"{blocked} | preview: {'guard: ' + bad if bad else f'would size {n}x ({why})'}"
            continue
        if bad:
            row["action"] = f"signal, but skip: {bad}"
            continue
        n, why = S.size_position(acct["equity"], pick.width, pick.mid_credit, used, bp)
        if n == 0:
            row["action"] = f"signal, but size 0: {why}"
            continue
        ladder = S.entry_ladder(pick)
        used += S.max_loss(pick.width, ladder[-1], n)
        if bp is not None:
            bp -= pick.width * 100 * n
        sid = S.spread_id(sym, pick.expiry, pick.short.strike, pick.long.strike)
        row["action"] = f"OPEN {n}x  ({why})"
        row["qty"], row["ladder"] = n, ladder
        jobs.append(execution.Job(sid, "open", execution.open_legs(pick.short.symbol, pick.long.symbol), n,
                                  ladder, credit=True,
                                  meta={"pick": pick, "signal": {k: getattr(sig, k) for k in
                                                                 ("close", "sma200", "sma5", "rsi2", "ibs")}}))
    return jobs, rows


# ---------------------------------------------------------------- reporting
def _fmt(x, f="{:.2f}"):
    return f.format(x) if isinstance(x, (int, float)) and math.isfinite(x) else "-"


def print_plan(acct, kill, vix, exit_rows, entry_rows, mode):
    log.info("=" * 96)
    log.info("MODE %s | equity $%s%s | %s", mode, f"{acct['equity']:,.2f}",
             " (SIMULATED, no options account yet)" if acct.get("simulated") else "", vix["source"])
    log.info("kill switch: %s -> %s", "BLOCK" if kill[0] else "ok", kill[1])
    log.info("-- open spreads: %d", len(exit_rows))
    for r in exit_rows:
        log.info("   %-26s x%-3s credit %s  mid %s  nat %s  held %sd  tdte %s  close %s vs 5DMA %s -> %s",
                 r["id"], r["qty"], _fmt(float(r["entry_credit"])), _fmt(r["mid_debit"]), _fmt(r["natural_debit"]),
                 r["held"], r["tdte"], _fmt(r["close"]), _fmt(r["sma5"]), r["reason"] or "hold")
    log.info("-- entry signals")
    for r in entry_rows:
        log.info("   %-5s close %s 200DMA %s RSI2 %s IBS %s [%s] -> %s", r["symbol"], _fmt(r.get("close")),
                 _fmt(r.get("sma200")), _fmt(r.get("rsi2"), "{:.1f}"), _fmt(r.get("ibs")),
                 r.get("src", "-"), r.get("action"))
        p = r.get("pick")
        if p:
            log.info("         %s %s DTE %d: sell %s (Δ%s %s/%s) buy %s (Δ%s %s/%s) width %g mid %.2f nat %.2f (%.0f%% of width)",
                     p.underlying, p.expiry, p.dte, p.short.symbol, _fmt(p.short.delta), _fmt(p.short.bid),
                     _fmt(p.short.ask), p.long.symbol, _fmt(p.long.delta), _fmt(p.long.bid), _fmt(p.long.ask),
                     p.width, p.mid_credit, p.natural_credit, 100 * p.mid_credit / p.width)
        if r.get("ladder"):
            log.info("         limit credits (re-priced in order): %s; max loss $%s",
                     ", ".join(f"{x:.2f}" for x in r["ladder"]),
                     f"{S.max_loss(p.width, r['ladder'][-1], r['qty']):,.0f}")
    log.info("=" * 96)


# ---------------------------------------------------------------- applying results
def apply_results(jobs, records: dict, today: dt.date, run_id, persist: bool) -> list[dict]:
    trades = []
    for j in jobs:
        q = int(round(j.filled_qty))
        orow = {"key": j.key, "purpose": j.purpose, "status": j.status, "qty": j.qty, "filled_qty": q,
                "avg_price": j.avg_price, "order_ids": j.order_ids, "history": j.history, "error": j.error,
                "date": today.isoformat()}
        log.info("%s %s: %s %d/%d @ %s", j.purpose, j.key, j.status, q, j.qty, _fmt(j.avg_price))
        if persist:
            state.append(state.ORDERS, orow)
            db.insert("options_live_orders", {
                "id": j.order_ids[-1] if j.order_ids else f"none-{j.key}-{today}", "run_id": run_id,
                "spread_id": j.key, "underlying": j.key.split("-")[0], "purpose": j.purpose,
                "qty": j.qty, "filled_qty": q, "limit_prices": j.prices, "avg_price": j.avg_price,
                "status": j.status, "attempts": len(j.order_ids), "history": j.history, "error": j.error,
                "submitted_at": j.history[0]["t"] if j.history else None}, upsert_on="id")
        if q <= 0:
            continue
        if j.purpose == "open":
            p = j.meta["pick"]
            records[j.key] = {"underlying": p.underlying, "expiry": p.expiry.isoformat(),
                              "short_symbol": p.short.symbol, "long_symbol": p.long.symbol,
                              "short_strike": p.short.strike, "long_strike": p.long.strike, "qty": q,
                              "entry_date": today.isoformat(), "entry_credit": j.avg_price,
                              "entry_mid": p.mid_credit, "signal": j.meta["signal"],
                              "short_delta": p.short.delta, "long_delta": p.long.delta,
                              "order_ids": j.order_ids}
        else:
            r = j.meta["record"]
            width = r["short_strike"] - r["long_strike"]
            pnl = round((float(r["entry_credit"]) - j.avg_price) * 100 * q, 2)
            t = {"id": f"{j.key}-{r['entry_date']}-{today}", "spread_id": j.key, "underlying": r["underlying"],
                 "expiry": r["expiry"], "short_strike": r["short_strike"], "long_strike": r["long_strike"],
                 "qty": q, "opened_at": r["entry_date"], "closed_at": today.isoformat(),
                 "entry_credit": r["entry_credit"], "exit_debit": j.avg_price, "pnl": pnl,
                 "pnl_pct": round(pnl / max(S.max_loss(width, float(r["entry_credit"]), q), 1e-9), 4),
                 "exit_reason": j.meta["reason"]}
            trades.append(t)
            left = int(r["qty"]) - q
            if left > 0:
                records[j.key] = {**r, "qty": left}
            else:
                records.pop(j.key, None)
            if persist:
                state.append(state.TRADES, t)
                db.insert("options_live_trades", t, upsert_on="id")
    return trades


# ---------------------------------------------------------------- modes
def load_book(broker: Broker, today: dt.date, persist: bool):
    pos = broker.positions()
    spreads, orphans = S.group_spreads(pos)
    for o in orphans:
        log.warning("unpaired position (not traded automatically): %s qty %s", o.get("symbol"), o.get("qty"))
    stored = state.load_positions()
    records, adopted, vanished = state.reconcile(spreads, stored, today)
    for a in adopted:
        log.warning("adopted spread %s from the broker with no local record (entry date set to today)", a)
    for v in vanished:
        log.warning("spread %s is no longer at the broker (expired/assigned/closed outside the bot)", v["id"])
        if persist:
            state.append(state.TRADES, {**v, "closed_at": today.isoformat(), "exit_reason": "external"})
    if persist and (adopted or vanished):
        state.save_positions(records)
    return records, orphans, pos


def run_daily(args) -> int:
    broker = Broker(execute=args.execute)
    persist = args.execute
    now = now_et()
    today = now.date()
    if broker.data_creds.source != "options":
        log.info("market data via the Stock Shorting Bot keys (read-only data calls only)")
    if not broker.has_trading:
        log.info("no options account: %s", broker.trading_error)
    sess = market_session(broker, today)
    if not sess["open"]:
        log.info("market closed today (%s); nothing to do", sess["source"])
        db.heartbeat("idle", f"{today}: market closed")
        return 0
    close_at = sess["close_at"]
    decide = close_at - dt.timedelta(minutes=C.DECIDE_MINUTES_BEFORE_CLOSE)
    cutoff = close_at - dt.timedelta(minutes=C.CUTOFF_MINUTES_BEFORE_CLOSE)
    hard_stop = close_at - dt.timedelta(seconds=30)
    if args.execute and now >= cutoff:
        if not args.now:                        # e.g. the 15:45 cron line on a 13:00 half day
            log.info("today's window ended at %s ET (close %s); nothing to do", f"{cutoff:%H:%M}", f"{close_at:%H:%M}")
            return 0
        log.error("too late to trade today (%s ET >= cutoff %s ET); refusing", f"{now:%H:%M}", f"{cutoff:%H:%M}")
        return 1
    if args.execute and now < sess["open_at"]:
        log.error("market not open yet; refusing")
        return 1
    if args.execute and not args.force and state.already_ran(today):
        log.error("already executed today (%s); use --force to run again", today)
        return 1
    if not args.now and (decide - now).total_seconds() > 20 * 60:
        # lets one crontab carry both a 15:45 and a 12:45 (half day) line
        log.info("decision time today is %s ET; this run is too early, exiting", f"{decide:%H:%M}")
        return 0
    if not args.now and now < decide:
        secs = (decide - now).total_seconds()
        log.info("waiting %.0fs for the 15:50-equivalent decision time (%s ET)", secs, f"{decide:%H:%M}")
        time.sleep(secs)
    acct = broker.account()
    if args.execute:
        lvl = acct.get("options_trading_level")
        if lvl is None or int(lvl) < 3:
            log.error("options trading level %s < 3: spreads need level 3 on the paper account", lvl)
            return 1
        if acct.get("trading_blocked"):
            log.error("account trading is blocked")
            return 1
        stale = broker.open_orders()
        if stale:
            log.warning("canceling %d open orders left from earlier", len(stale))
            broker.cancel_all()
    records, orphans, _ = load_book(broker, today, persist)
    ctx = Ctx(broker, today)
    spy = ctx.hist.setdefault("SPY", broker.daily_bars("SPY", today - dt.timedelta(days=signals.HISTORY_DAYS),
                                                       today - dt.timedelta(days=1)))
    sessions = sessions_list(broker, spy.index, today)
    vix = signals.vix_levels(today)
    kill = S.kill_switch(vix["vix"], vix["vix3m"])
    exit_jobs, exit_rows = plan_exits(ctx, records, sessions, end_of_day=True)
    closing = {j.key for j in exit_jobs}
    symbols = args.symbols.split(",") if args.symbols else C.universe()
    entry_jobs, entry_rows = plan_entries(ctx, records, acct, kill, symbols, preview=args.preview)
    mode = "EXECUTE" if args.execute else "DRY RUN"
    print_plan(acct, kill, vix, exit_rows, entry_rows, mode)
    run = db.insert("options_live_runs", {
        "run_date": today, "mode": "execute" if args.execute else "dry_run", "kind": "daily",
        "status": "planned", "started_at": dt.datetime.now(dt.timezone.utc), "equity": acct["equity"],
        "vix": vix["vix"], "vix3m": vix["vix3m"], "kill_switch": kill[0],
        "signals": [{k: v for k, v in r.items() if k not in ("pick",)} for r in entry_rows],
        "n_exits": len(exit_jobs), "n_entries": len(entry_jobs),
        "message": f"{len(exit_jobs)} exits, {len(entry_jobs)} entries planned"})
    run_id = run[0]["id"] if run else None
    if not args.execute:
        window = (f"from {max(now, decide):%H:%M} to {cutoff:%H:%M} ET" if now < cutoff
                  else f"in the {decide:%H:%M}-{cutoff:%H:%M} ET window (market already closed; prices are the latest available)")
        log.info("DRY RUN: nothing sent. %d exit(s) and %d entr%s would be worked %s.",
                 len(exit_jobs), len(entry_jobs), "y" if len(entry_jobs) == 1 else "ies", window)
        db.heartbeat("ok", f"{today}: dry run, {len(exit_jobs)} exits / {len(entry_jobs)} entries planned")
        return 0
    state.mark_ran(today)
    jobs = exit_jobs + [j for j in entry_jobs if j.key not in closing]
    execution.work_orders(broker, jobs, cutoff=cutoff, hard_stop=hard_stop)
    trades = apply_results(jobs, records, today, run_id, persist=True)
    state.save_positions(records)
    n_open = sum(1 for j in entry_jobs if j.filled_qty > 0)
    n_close = sum(1 for j in exit_jobs if j.filled_qty > 0)
    unfilled_exits = [j.key for j in exit_jobs if j.remaining > 0]
    msg = f"{today}: opened {n_open}, closed {n_close}, {len(records)} open, realized ${sum(t['pnl'] for t in trades):,.2f}"
    if unfilled_exits:
        msg += f"; EXITS NOT FILLED: {', '.join(unfilled_exits)}"
        log.error(msg)
    if run_id:
        db.update("options_live_runs", {"id": run_id}, {"status": "executed", "message": msg})
    db.heartbeat("warning" if unfilled_exits else "ok", msg)
    log.info(msg)
    return 0


def run_intraday(args) -> int:
    broker = Broker(execute=args.execute)
    now = now_et()
    today = now.date()
    sess = market_session(broker, today)
    if not sess["open"]:
        return 0
    start = sess["open_at"] + dt.timedelta(minutes=C.INTRADAY_START_MINUTES_AFTER_OPEN)
    stop = sess["close_at"] - dt.timedelta(minutes=C.START_MINUTES_BEFORE_CLOSE + 2)
    if not args.now and not (start <= now <= stop):
        log.info("intraday check outside its window (%s-%s ET)", f"{start:%H:%M}", f"{stop:%H:%M}")
        return 0
    records, _, _ = load_book(broker, today, args.execute)
    if not records:
        log.info("intraday: no open spreads")
        return 0
    ctx = Ctx(broker, today)
    spy = broker.daily_bars("SPY", today - dt.timedelta(days=30), today - dt.timedelta(days=1))
    jobs, rows = plan_exits(ctx, records, sessions_list(broker, spy.index, today), end_of_day=False)
    for r in rows:
        log.info("intraday %-26s mid %s credit %s -> %s", r["id"], _fmt(r["mid_debit"]),
                 _fmt(float(r["entry_credit"])), r["reason"] or "hold")
    for j in jobs:                              # intraday take-profits do not chase
        if j.meta["reason"] == "take_profit":
            j.cutoff_price = None
    if not jobs or not args.execute:
        if jobs:
            log.info("DRY RUN: %d intraday exit(s) not sent", len(jobs))
        return 0
    cutoff = min(now + dt.timedelta(minutes=4), stop)
    execution.work_orders(broker, jobs, cutoff=cutoff, hard_stop=cutoff + dt.timedelta(minutes=1),
                          step_wait=40)
    apply_results(jobs, records, today, None, persist=True)
    state.save_positions(records)
    return 0


def run_close_all(args) -> int:
    broker = Broker(execute=args.execute)
    now = now_et()
    today = now.date()
    if args.execute:
        clk = broker.clock()
        if not clk or not clk.get("is_open"):
            log.error("market is closed; emergency close needs an open market")
            return 1
        broker.cancel_all()
    records, orphans, _ = load_book(broker, today, args.execute)
    ctx = Ctx(broker, today)
    sessions = signals.sessions_from_bars([], today)
    jobs, rows = plan_exits(ctx, records, sessions, end_of_day=True, force_all=True)
    for r in rows:
        log.info("close-all %-26s x%s mid %s nat %s", r["id"], r["qty"], _fmt(r["mid_debit"]), _fmt(r["natural_debit"]))
    for o in orphans:
        log.info("close-all unpaired leg %s qty %s", o.get("symbol"), o.get("qty"))
    if not args.execute:
        log.info("DRY RUN: %d spread(s) and %d unpaired leg(s) would be closed", len(jobs), len(orphans))
        return 0
    execution.work_orders(broker, jobs, cutoff=now + dt.timedelta(minutes=3),
                          hard_stop=now + dt.timedelta(minutes=5), step_wait=30)
    apply_results(jobs, records, today, None, persist=True)
    state.save_positions(records)
    if orphans:
        quotes = broker.option_quotes([o["symbol"] for o in orphans])
        for o in orphans:
            q, qty = quotes.get(o["symbol"]), abs(int(round(float(o["qty"]))))
            if not q or qty == 0:
                log.error("unpaired %s: no quote, close it manually", o.get("symbol"))
                continue
            short = o.get("side") == "short" or float(o["qty"]) < 0
            px = q.ask + C.EXIT_CUTOFF_EXTRA if short else max(q.bid - C.EXIT_CUTOFF_EXTRA, C.TICK)
            broker.submit_single(o["symbol"], qty, "buy" if short else "sell",
                                 "buy_to_close" if short else "sell_to_close", px,
                                 f"{C.BOT_ID}-flat-{o['symbol']}-{int(time.time())}")
    db.heartbeat("warning", f"{today}: emergency close-all run")
    return 0


def health() -> int:
    ok = True

    def line(name, good, msg):
        nonlocal ok
        ok &= bool(good) or good is None
        log.info("%-14s %-5s %s", name, {True: "OK", False: "FAIL", None: "WARN"}[good], msg)

    env = C.options_env()
    line("env file", C.ENV_PATH.exists() or None, str(C.ENV_PATH) + ("" if C.ENV_PATH.exists() else " missing"))
    try:
        C.trading_creds(env)
        line("trading keys", True, "OPT_APCA_* present, paper flag true, paper URL")
    except C.ConfigError as e:
        line("trading keys", None, f"{e} (dry runs still work)")
    try:
        b = Broker(execute=False, env=env)
        line("data keys", True, f"source: {b.data_creds.source}")
    except Exception as e:
        line("data keys", False, str(e))
        return 1
    if b.has_trading:
        try:
            a = b.account()
            lvl = a.get("options_trading_level")
            line("paper account", True, f"...{a['account_number_tail']} status {a['status']} equity ${a['equity']:,.2f}")
            line("options level", lvl is not None and int(lvl) >= 3, f"level {lvl} (spreads need 3)")
            line("positions", True, f"{len(b.positions())} legs, {len(b.open_orders())} open orders")
        except (BrokerError, SafetyError) as e:
            line("paper account", False, str(e))
    today = now_et().date()
    try:
        s = b.stock_snapshot("SPY")
        line("stock data", s["price"] is not None, f"SPY {s['price']} ({s['feed']}, bar {s['bar_date']})")
        ch = b.option_chain("SPY", today + dt.timedelta(days=C.DTE_MIN), today + dt.timedelta(days=C.DTE_MAX))
        line("option chain", len(ch) > 0, f"{len(ch)} SPY puts 7-14 DTE, {sum(q.delta is not None for q in ch)} with greeks")
    except Exception as e:
        line("market data", False, str(e))
    v = signals.vix_levels(today)
    line("VIX", v["vix"] is not None and v["vix3m"] is not None, f"{v['vix']} / {v['vix3m']} ({v['source']})")
    line("supabase", True if db.enabled() else None, "enabled" if db.enabled() else "not configured (writes are no-ops)")
    rec = state.load_positions()
    line("state", True, f"{len(rec)} stored spreads in {C.STATE_DIR}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="send orders (paper options account only)")
    ap.add_argument("--now", action="store_true", help="skip the wait for the decision time")
    ap.add_argument("--check", action="store_true", help="health check")
    ap.add_argument("--close-all", action="store_true", help="emergency: close every spread")
    ap.add_argument("--intraday", action="store_true", help="take-profit / stop checks only")
    ap.add_argument("--force", action="store_true", help="allow a second executed daily run today")
    ap.add_argument("--symbols", help="comma-separated override of the universe")
    ap.add_argument("--preview", action="store_true",
                    help="also pick and price the spread for symbols without a signal (display only)")
    args = ap.parse_args(argv)
    setup_logging()
    if args.check:
        return health()
    lock = None
    try:
        if args.execute:
            C.STATE_DIR.mkdir(parents=True, exist_ok=True)
            lock = open(C.STATE_DIR / ".lock", "w")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log.error("another executing run holds the lock; exiting")
                return 1
        if args.close_all:
            return run_close_all(args)
        if args.intraday:
            return run_intraday(args)
        return run_daily(args)
    except (C.ConfigError, SafetyError) as e:
        log.error("REFUSED: %s", e)
        return 2
    except Exception as e:
        log.error("run failed: %s\n%s", e, traceback.format_exc())
        db.heartbeat("error", f"run failed: {e}")
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())
