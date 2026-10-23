#!/usr/bin/env python3
"""Live paper trading: RSI(2)/IBS dip -> 7-14 DTE 0.25/0.10 delta put credit spread.

    python3 live/run.py                    dry run: wait for the decision time (15:55 ET), print what it would do
    python3 live/run.py --now              dry run immediately (any time; uses the latest prices)
    python3 live/run.py --execute          cron 15:45 ET: exits + entries at the decision time, cancel by 15:58
    python3 live/run.py --intraday --execute   take-profit / stop checks only (every 15 min)
    python3 live/run.py --sweep --execute  expiry-day morning sweep (cron 09:45 ET, mandatory)
    python3 live/run.py --check            health check (keys, paper account, data, VIX, Supabase)
    python3 live/run.py --close-all        emergency: show how every spread would be closed
    python3 live/run.py --close-all --execute  ... and close them now (aggressive prices)
    python3 live/run.py --now --preview --condor   dry run with the SPY iron condor sleeve forced on

Two sleeves: the dip put credit spread (always on) and the SPY iron condor
(config.CONDOR_ENABLED, off by default; --condor forces it on for one run).

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
from live import alerts, db, execution, resilience, signals, state  # noqa: E402
from live import strategy as S  # noqa: E402
from live.broker import ET, Broker, BrokerError, SafetyError  # noqa: E402
from live.broker import client_order_id  # noqa: E402,F401

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
    """Per-run caches (daily history, live signals) so each symbol is fetched once.
    ``strict`` (execute mode, or a dry run waiting for the decision time) makes
    stale live prices and option quotes fail closed."""

    def __init__(self, broker: Broker, today: dt.date, strict: bool = False, now: dt.datetime | None = None):
        self.broker, self.today, self.strict = broker, today, strict
        self._now = now
        self.hist: dict = {}
        self.sig: dict = {}
        self.warnings: list[str] = []

    def now(self) -> dt.datetime:
        return self._now or now_et()

    def warn(self, msg: str) -> None:
        log.warning(msg)
        self.warnings.append(msg)

    def signal(self, sym: str) -> S.Signal:
        if sym not in self.sig:
            if sym not in self.hist:
                self.hist[sym] = self.broker.daily_bars(sym, self.today - dt.timedelta(days=signals.HISTORY_DAYS),
                                                        self.today - dt.timedelta(days=1))
            sig, bars, lb = signals.signal_for(
                self.broker, sym, self.today, self.hist[sym], now=self.now(),
                max_trade_age=C.MAX_STOCK_TRADE_AGE_SECONDS if self.strict else None)
            sig.live = lb
            self.sig[sym] = S.entry_signal(sig)
        return self.sig[sym]


def _stale(q, ctx: Ctx) -> bool:
    if not ctx.strict or q is None:
        return False
    age = q.age_seconds(ctx.now())
    return age is None or age > C.MAX_OPTION_QUOTE_AGE_SECONDS


def plan_exits(ctx: Ctx, records: dict, sessions: list[dt.date], end_of_day: bool, force_all: bool = False,
               mode: str = "daily", deadline: dt.datetime | None = None):
    """Returns (jobs, rows) for open spreads that should close now.

    mode: "daily" (all rules), "intraday" (take-profit / stop, plus spreads that
    expire today), "sweep" (only spreads expiring today or earlier), "close_all".
    A missing or zero bid on the long leg never blocks an exit, and a forced
    exit (expiry, sweep, close-all) without usable quotes is priced from the
    intrinsic value (S.blind_exit_prices) with a warning."""
    jobs, rows = [], []
    if not records:
        return jobs, rows
    syms = sorted({r[k] for r in records.values() for k in ("short_symbol", "long_symbol")})
    try:
        quotes = ctx.broker.option_quotes(syms)
    except Exception as e:                      # noqa: BLE001
        ctx.warn(f"option quotes unavailable for exits: {e}")
        quotes = {}
    for sid, r in sorted(records.items()):
        sq, lq = quotes.get(r["short_symbol"]), quotes.get(r["long_symbol"])
        width = r["short_strike"] - r["long_strike"]
        mid, natural, qnote = S.exit_prices(sq, lq)
        stale = _stale(sq, ctx) or (lq is not None and lq.quoted and _stale(lq, ctx))
        exp = dt.date.fromisoformat(r["expiry"])
        held = S.held_trading_days(dt.date.fromisoformat(r["entry_date"]), ctx.today, sessions)
        tdte = S.trading_days_until(exp, ctx.today, sessions)
        try:
            sig = ctx.signal(r["underlying"])
            close, sma5 = sig.close, sig.sma5
        except Exception as e:                  # noqa: BLE001
            log.error("%s: no live features for exit check: %s", sid, e)
            close, sma5 = math.nan, math.nan
        basis = float(r.get("entry_mid") or r["entry_credit"])     # research: TP vs the mid credit at entry
        if force_all:
            reason = "emergency_close"
        elif mode == "sweep":
            reason = "expiry_day_sweep" if exp <= ctx.today else None
        else:
            reason = S.exit_reason(entry_credit=basis, cost_to_close=None if stale else mid, close=close,
                                   sma5=sma5, held_days=held, trading_days_to_expiry=tdte, end_of_day=end_of_day)
            if mode == "intraday" and reason == "expiry_exit" and exp > ctx.today:
                reason = None                   # the daily run closes it at the decision time
            if mode == "intraday" and exp <= ctx.today:
                reason = "expiry_day_sweep"
            if (reason is None and end_of_day and r["underlying"] in C.SINGLE_NAMES
                    and _earnings_on(r["underlying"], ctx.today)):
                reason = "earnings_exit"
        row = {"id": sid, "qty": r["qty"], "entry_credit": r["entry_credit"], "entry_mid": r.get("entry_mid"),
               "mid_debit": mid, "natural_debit": natural, "held": held, "tdte": tdte, "close": close,
               "sma5": sma5, "reason": reason, "note": qnote, "expiry": exp}
        rows.append(row)
        if stale:
            ctx.warn(f"{sid}: option quotes are stale; take-profit not evaluated")
        if qnote:
            log.info("%s: %s", sid, qnote)
        if not reason:
            continue
        if (C.DIP_EXPIRY_DAY_DEADLINE_ENABLED and deadline is not None and exp <= ctx.today and not force_all
                and ctx.now() >= deadline - dt.timedelta(minutes=C.CONDOR_EXPIRY_DAY_MARGIN_MINUTES)):
            row["blocked"] = True
            row["reason"] = f"{reason} (BLOCKED: past the {deadline:%H:%M} ET expiry-day deadline)"
            ctx.warn(f"{sid} EXPIRES TODAY AND IS STILL OPEN after {deadline:%H:%M} ET; no order sent "
                     f"(DIP_EXPIRY_DAY_DEADLINE_ENABLED). Close it by hand now.")
            continue
        forced = force_all or reason in ("expiry_exit", "expiry_day_sweep")
        if mid is None:
            if not forced:
                log.error("%s: wants %s but the short leg has no offer; not closing this run", sid, reason)
                row["reason"] = f"{reason} (blocked: no quotes)"
                ctx.warn(f"{sid}: {reason} blocked, no quotes")
                continue
            ladder, cut = S.blind_exit_prices(r["short_strike"], r["long_strike"],
                                              close if math.isfinite(close) else None)
            ctx.warn(f"{sid}: {reason} with no usable quotes; pricing from intrinsic value {ladder} / cutoff {cut}")
            mid, natural = ladder[0], ladder[-1]
        else:
            ladder = S.exit_ladder(mid, natural, steps=2) if force_all else S.exit_ladder(mid, natural)
            cut = S.exit_cutoff_price(natural, width)
        if qnote and forced:
            ctx.warn(f"{sid}: {reason} with {qnote}")
        jobs.append(execution.Job(sid, "close", execution.close_legs(r["short_symbol"], r["long_symbol"]),
                                  int(r["qty"]), ladder, credit=False, cutoff_price=cut,
                                  meta={"record": r, "reason": reason, "mid": mid, "natural": natural,
                                        "tdte": tdte, "expiry": exp}))
    return jobs, rows


def _earnings_on(sym: str, day: dt.date) -> bool:
    from optionsbot.strategies_signals import EARNINGS
    import pandas as pd
    return pd.Timestamp(day) in EARNINGS.get(sym, [])


def _earnings_between(sym: str, a: dt.date, b: dt.date) -> bool:
    from optionsbot.strategies_signals import EARNINGS
    import pandas as pd
    return any(pd.Timestamp(a) <= e <= pd.Timestamp(b) for e in EARNINGS.get(sym, []))


def open_max_loss(records: dict) -> float:
    return sum(S.max_loss(r["short_strike"] - r["long_strike"], float(r["entry_credit"]), int(r["qty"]))
               for r in records.values())


def plan_entries(ctx: Ctx, records: dict, acct: dict, kill: tuple[bool, str], symbols: list[str],
                 preview: bool = False, reserved: float = 0.0, avoid: set | None = None):
    """Entry jobs for symbols with a signal. ``preview`` also prices the spread
    for symbols without one (display only; never produces a job).
    ``reserved``: max loss the condor sleeve holds (open or planned this run) in
    the shared RISK_TOTAL budget. ``avoid``: option symbols the condor sleeve
    holds or is opening; a dip spread never trades one of them (Alpaca nets
    positions per symbol, so the two sleeves would merge)."""
    jobs, rows = [], []
    used = open_max_loss(records) + reserved
    bp = acct.get("options_buying_power")
    held = {r["underlying"] for r in records.values()}
    for sym in symbols:
        row = {"symbol": sym}
        rows.append(row)
        try:
            sig = ctx.signal(sym)
        except Exception as e:                  # noqa: BLE001  (stale / missing data: fail closed)
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
        try:
            chain = ctx.broker.option_chain(sym, ctx.today + dt.timedelta(days=C.DTE_MIN), exp_hi)
        except Exception as e:                  # noqa: BLE001
            row["action"] = f"signal, but option chain unavailable: {e}" if not blocked else blocked
            continue
        pick, why = S.pick_spread(chain, sym, ctx.today)
        if pick is None:
            row["action"] = f"signal, but no spread: {why}" if not blocked else f"{blocked} | preview: {why}"
            continue
        row["pick"] = pick
        if sym in C.SINGLE_NAMES and _earnings_between(sym, ctx.today, pick.expiry):   # research window
            row["action"] = "signal, but earnings before expiry"
            continue
        bad = S.check_pick(pick, now=ctx.now() if ctx.strict else None)
        ladder = S.entry_ladder(pick)
        if avoid and {pick.short.symbol, pick.long.symbol} & avoid:
            bad = bad or "a leg is held or traded by the condor sleeve"
        if blocked:
            n, why = S.size_position(acct["equity"], pick.width, ladder[-1], used, bp)
            row["action"] = f"{blocked} | preview: {'guard: ' + bad if bad else f'would size {n}x ({why})'}"
            continue
        if bad:
            row["action"] = f"signal, but skip: {bad}"
            continue
        n, why = S.size_position(acct["equity"], pick.width, ladder[-1], used, bp)   # worst ladder credit
        if n == 0:
            row["action"] = f"signal, but size 0: {why}"
            continue
        used += S.max_loss(pick.width, ladder[-1], n)
        if bp is not None:
            bp -= pick.width * 100 * n
        sid = S.spread_id(sym, pick.expiry, pick.short.strike, pick.long.strike)
        row["action"] = f"OPEN {n}x  ({why})"
        row["qty"], row["ladder"] = n, ladder
        signal = {k: getattr(sig, k) for k in ("close", "sma200", "sma5", "rsi2", "ibs")}
        jobs.append(execution.Job(sid, "open", execution.open_legs(pick.short.symbol, pick.long.symbol), n,
                                  ladder, credit=True,
                                  meta={"pick": pick, "signal": signal, "mid": pick.mid_credit,
                                        "natural": pick.natural_credit, "template": dip_template(pick, signal)}))
    return jobs, rows


# ---------------------------------------------------------------- condor sleeve
def condor_enabled(args) -> bool:
    """config.CONDOR_ENABLED, or --condor for this run."""
    return bool(C.CONDOR_ENABLED or getattr(args, "condor", False))


def condor_open_max_loss(condors: dict) -> float:
    return sum(S.max_loss(S.condor_width(r), float(r["entry_credit"]), int(r["qty"])) for r in condors.values())


def condor_symbols(condors: dict) -> set[str]:
    return {s for r in condors.values() for s in S.condor_leg_symbols(r)}


def dip_symbols(records: dict) -> set[str]:
    return {r[k] for r in records.values() for k in ("short_symbol", "long_symbol") if r.get(k)}


def condor_deadline(today: dt.date, close_at: dt.datetime | None = None) -> dt.datetime:
    """Latest time a condor order may work on its expiry day: CONDOR_EXPIRY_DAY_DEADLINE
    (15:00 ET), or one hour before an early close."""
    d = dt.datetime.combine(today, dt.time.fromisoformat(C.CONDOR_EXPIRY_DAY_DEADLINE), ET)
    if close_at is not None:
        d = min(d, close_at - dt.timedelta(hours=1))
    return d


def condor_exdiv_info(broker, condors: dict, today: dt.date, sessions) -> dict | None:
    """Ex-dividend dates / dividend for the held condors' underlying (CONDOR_EXDIV_GUARD)."""
    if not C.CONDOR_EXDIV_GUARD or not condors:
        return None
    try:
        end = max(dt.date.fromisoformat(r["expiry"]) for r in condors.values())
        sym = next(iter(condors.values()))["underlying"]
        info = resilience.exdiv_info(broker, sym, today, end, sessions)
        log.info("ex-dividend check %s: dates %s, dividend %.2f (%s)", sym,
                 ", ".join(str(d) for d in info["dates"]) or "none before expiry", info["dividend"], info["source"])
        return info
    except Exception as e:                      # noqa: BLE001
        log.warning("ex-dividend info unavailable: %s", e)
        return None


def plan_condor_exits(ctx: Ctx, condors: dict, sessions: list[dt.date], mode: str = "daily",
                      force_all: bool = False, deadline: dt.datetime | None = None, exdiv: dict | None = None):
    """(jobs, rows) for open condors that should close now (S.condor_exit_reason:
    held to expiry, closed on the last trading day before it). On expiry day no
    job is made at or after ``deadline`` minus CONDOR_EXPIRY_DAY_MARGIN_MINUTES:
    the row is flagged ``blocked`` and the run raises an error to close by hand,
    so a condor order never works into Alpaca's 15:30 expiration handling.
    Without usable quotes the exit is priced from intrinsic value, as for dips."""
    jobs, rows = [], []
    if not condors:
        return jobs, rows
    deadline = deadline or condor_deadline(ctx.today)
    syms = sorted(condor_symbols(condors))
    try:
        quotes = ctx.broker.option_quotes(syms)
    except Exception as e:                      # noqa: BLE001
        ctx.warn(f"option quotes unavailable for condor exits: {e}")
        quotes = {}
    for cid, r in sorted(condors.items()):
        legs = [quotes.get(x) for x in S.condor_leg_symbols(r)]
        mid, natural, qnote = S.condor_exit_prices(*legs)
        exp = dt.date.fromisoformat(r["expiry"])
        held = S.held_trading_days(dt.date.fromisoformat(r["entry_date"]), ctx.today, sessions)
        tdte = S.trading_days_until(exp, ctx.today, sessions)
        reason = S.condor_exit_reason(expiry=exp, today=ctx.today, trading_days_to_expiry=tdte, mode=mode,
                                      force_all=force_all)
        try:
            spot = ctx.signal(r["underlying"]).close
        except Exception:                       # noqa: BLE001
            spot = math.nan
        exdiv_note = None
        if exdiv and not force_all and reason is None:
            reason, exdiv_note = condor_exdiv_check(ctx, r, exp, sessions, mode, quotes, spot, exdiv)
        row = {"id": cid, "qty": r["qty"], "entry_credit": r["entry_credit"], "entry_mid": r.get("entry_mid"),
               "mid_debit": mid, "natural_debit": natural, "held": held, "tdte": tdte, "spot": spot,
               "reason": reason, "note": qnote, "expiry": exp, "blocked": False,
               "max_loss": S.max_loss(S.condor_width(r), float(r["entry_credit"]), int(r["qty"])),
               "exdiv": exdiv_note}
        rows.append(row)
        if not reason:
            continue
        if (exp <= ctx.today and not force_all
                and ctx.now() >= deadline - dt.timedelta(minutes=C.CONDOR_EXPIRY_DAY_MARGIN_MINUTES)):
            row["blocked"] = True
            row["reason"] = f"{reason} (BLOCKED: past the {deadline:%H:%M} ET condor deadline)"
            ctx.warn(f"CONDOR {cid} EXPIRES TODAY AND IS STILL OPEN after {deadline:%H:%M} ET; no order sent so "
                     f"nothing works into Alpaca's 15:30 expiration handling. Close it by hand now.")
            continue
        width = S.condor_width(r)
        if mid is None:
            ladder, cut = S.condor_blind_exit_prices(r, spot if math.isfinite(spot) else None)
            ctx.warn(f"{cid}: {reason} with no usable quotes; pricing from intrinsic value {ladder} / cutoff {cut}")
            mid, natural = ladder[0], ladder[-1]
        else:
            ladder = S.exit_ladder(mid, natural, steps=2) if force_all else S.exit_ladder(mid, natural)
            cut = S.exit_cutoff_price(natural, width)
        if qnote:
            ctx.warn(f"{cid}: {reason} with {qnote}")
        jobs.append(execution.Job(cid, "close", execution.condor_close_legs(*S.condor_leg_symbols(r)),
                                  int(r["qty"]), ladder, credit=False, cutoff_price=cut, sleeve=C.SLEEVE_CONDOR,
                                  meta={"record": r, "reason": reason, "mid": mid, "natural": natural,
                                        "tdte": tdte, "expiry": exp}))
    return jobs, rows


def condor_exdiv_check(ctx: Ctx, r: dict, exp: dt.date, sessions, mode: str, quotes: dict, spot: float,
                       exdiv: dict) -> tuple[str | None, str | None]:
    """("exdiv_exit" or None, note). Fires on the last cum-dividend session before an ex-date
    inside the condor's life (CONDOR_EXDIV_GUARD): at the daily run, or from
    CONDOR_EXDIV_INTRADAY_FROM in the intraday runs (redundancy if the daily run is missed), when
    the short call is in the money with extrinsic value below the dividend."""
    if not C.CONDOR_EXDIV_GUARD:
        return None, None
    for d in exdiv.get("dates") or []:
        if not (ctx.today < d <= exp) or S.last_cum_div_session(d, sessions) != ctx.today:
            continue
        frm = dt.datetime.combine(ctx.today, dt.time.fromisoformat(C.CONDOR_EXDIV_INTRADAY_FROM), ET)
        if mode != "daily" and not (mode == "intraday" and ctx.now() >= frm):
            return None, f"ex-dividend {d} tomorrow; checked at the daily run / intraday from {frm:%H:%M}"
        risk, why = S.early_exercise_risk(float(r["call_short_strike"]), quotes.get(r["call_short_symbol"]),
                                          spot if math.isfinite(spot) else None, float(exdiv.get("dividend") or 0))
        note = f"ex-dividend {d} (dividend ~{float(exdiv.get('dividend') or 0):.2f}): {why}"
        if risk:
            ctx.warn(f"condor {r.get('underlying')} {r.get('expiry')}: early-exercise risk, closing before "
                     f"the ex-date: {note}")
            return "exdiv_exit", note
        return None, note
    return None, None


def _condor_spot(ctx: Ctx, sym: str, preview: bool) -> tuple[float | None, str | None]:
    """(live spot, None), or for a preview with no usable live bar (e.g. after hours)
    the last trade from the snapshot and a note. Entries never use the fallback."""
    try:
        return ctx.signal(sym).close, None
    except Exception as e:                      # noqa: BLE001  (stale / missing data: fail closed)
        if not preview:
            return None, f"data error {e}"
        try:
            px = ctx.broker.stock_snapshot(sym)["price"]
        except Exception:                       # noqa: BLE001
            px = None
        return px, f"data error {e}; preview priced on the last trade {px}"


def plan_condor_entry(ctx: Ctx, condors: dict, acct: dict, kill: tuple[bool, str], used: float,
                      bp: float | None, avoid: set | None = None, preview: bool = False):
    """(jobs, row) for the condor sleeve: one condor at a time, opened at the
    first daily run when flat. ``used`` is the shared budget already taken (dip
    spreads + open condors); the condor's max loss is <= CONDOR_RISK_PER_POSITION
    of equity and the total stays <= RISK_TOTAL. The kill switch and the data /
    quote freshness checks block it exactly as they block dip entries.
    ``preview`` prices it even when blocked (display only; never a job)."""
    sym = C.CONDOR_UNDERLYING
    row = {"symbol": f"{sym} condor"}
    if len(condors) >= C.CONDOR_MAX_POSITIONS:
        row["action"] = f"hold: {len(condors)} condor(s) open (max {C.CONDOR_MAX_POSITIONS}); next entry when flat"
        return [], row
    blocked = f"kill switch: {kill[1]}" if kill[0] else None
    if blocked and not preview:
        row["action"] = blocked
        return [], row
    spot, note = _condor_spot(ctx, sym, preview)
    row["spot"] = spot
    if note:
        blocked = (blocked + "; " if blocked else "") + note
        if not preview or spot is None:
            row["action"] = f"skip: {note}" if not preview else blocked
            return [], row
    lo = ctx.today + dt.timedelta(days=C.CONDOR_DTE_MIN)
    hi = ctx.today + dt.timedelta(days=C.CONDOR_DTE_MAX)
    try:
        chain = ctx.broker.option_chain(sym, lo, hi, cp="P") + ctx.broker.option_chain(sym, lo, hi, cp="C")
    except Exception as e:                      # noqa: BLE001
        row["action"] = f"skip: option chain unavailable: {e}" if not blocked else blocked
        return [], row
    pick, why = S.pick_condor(chain, sym, ctx.today, spot)
    if pick is None:
        row["action"] = f"skip: no condor: {why}" if not blocked else f"{blocked} | preview: {why}"
        return [], row
    row["pick"] = pick
    row["exdiv"] = S.exdiv_dates(sym, ctx.today, pick.expiry)
    bad = S.check_condor(pick, now=ctx.now() if ctx.strict else None)
    if avoid and pick.symbols & avoid:
        bad = bad or "a leg is held by the dip sleeve"
    ladder = S.condor_entry_ladder(pick)
    n, why = S.size_position(acct["equity"], pick.width, ladder[-1], used, bp,
                             risk_per_position=C.CONDOR_RISK_PER_POSITION)
    row.update(ladder=ladder, size=n, size_why=why, used_before=used)
    if blocked:
        row["action"] = f"{blocked} | preview: {'guard: ' + bad if bad else f'would size {n}x ({why})'}"
        return [], row
    if bad:
        row["action"] = f"skip: {bad}"
        return [], row
    if n == 0:
        row["action"] = f"skip: size 0: {why}"
        return [], row
    row["action"] = f"OPEN {n}x  ({why})"
    row["qty"] = n
    job = execution.Job(pick.id, "open", execution.condor_open_legs(*(q.symbol for q in pick.legs)), n, ladder,
                        credit=True, sleeve=C.SLEEVE_CONDOR,
                        meta={"pick": pick, "spot": pick.spot, "mid": pick.mid_credit, "natural": pick.natural_credit,
                              "template": condor_template(pick)})
    return [job], row


def print_condor(acct, exit_rows, entry_row, condor_on: bool, used_total: float | None = None):
    log.info("-- condor sleeve (%s): open %d", "ENABLED" if condor_on else "disabled; exits only", len(exit_rows))
    for r in exit_rows:
        log.info("   %-34s x%-3s credit %s (mid %s)  cost mid %s  nat %s  held %sd  tdte %s  spot %s  max loss $%s -> %s",
                 r["id"], r["qty"], _fmt(float(r["entry_credit"])), _fmt(r.get("entry_mid")), _fmt(r["mid_debit"]),
                 _fmt(r["natural_debit"]), r["held"], r["tdte"], _fmt(r["spot"]), f"{r['max_loss']:,.0f}",
                 r["reason"] or "hold to expiry")
    if entry_row is None:
        return
    log.info("   %-5s spot %s -> %s", entry_row["symbol"], _fmt(entry_row.get("spot")), entry_row.get("action"))
    p = entry_row.get("pick")
    if p:
        log.info("         %s %s DTE %d: put  sell %s (Δ%s %s/%s) buy %s (%s/%s) wing %g", p.underlying, p.expiry,
                 p.dte, p.put_short.symbol, _fmt(p.put_short.delta), _fmt(p.put_short.bid), _fmt(p.put_short.ask),
                 p.put_long.symbol, _fmt(p.put_long.bid), _fmt(p.put_long.ask), p.put_width)
        log.info("         %s %s DTE %d: call sell %s (Δ%s %s/%s) buy %s (%s/%s) wing %g", p.underlying, p.expiry,
                 p.dte, p.call_short.symbol, _fmt(p.call_short.delta), _fmt(p.call_short.bid), _fmt(p.call_short.ask),
                 p.call_long.symbol, _fmt(p.call_long.bid), _fmt(p.call_long.ask), p.call_width)
        log.info("         strikes %g/%g | %g/%g (wings 3%% of spot = %.2f) mid credit %.2f nat %.2f (%.0f%% of width %g)",
                 p.put_long.strike, p.put_short.strike, p.call_short.strike, p.call_long.strike,
                 C.CONDOR_WING_PCT * p.spot, p.mid_credit, p.natural_credit, 100 * p.mid_credit / p.width, p.width)
    if entry_row.get("ladder") and p:
        n = entry_row.get("size") or 0
        per = S.max_loss(p.width, entry_row["ladder"][-1], 1)
        eq = acct["equity"]
        log.info("         limit credits (re-priced in order): %s; max loss $%s per condor (worst ladder credit)",
                 ", ".join(f"{x:.2f}" for x in entry_row["ladder"]), f"{per:,.0f}")
        if n == 0:
            log.info("         sizing: 0x (%s); condor cap %.0f%% of equity = $%s, shared budget used $%s of $%s",
                     entry_row.get("size_why"), 100 * C.CONDOR_RISK_PER_POSITION,
                     f"{C.CONDOR_RISK_PER_POSITION * eq:,.0f}", f"{entry_row.get('used_before', 0):,.0f}",
                     f"{C.RISK_TOTAL * eq:,.0f}")
            return
        log.info("         Alpaca requirement (universal spread rule, per expiration): margin = wider wing x 100 = $%s; "
                 "cost basis = margin - credit x 100 = $%s per condor; the bot charges options BP the full $%s",
                 f"{p.width * 100:,.0f}", f"{(p.width - entry_row['ladder'][-1]) * 100:,.0f}", f"{p.width * 100:,.0f}")
        if entry_row.get("exdiv"):
            log.info("         expected %s ex-dividend date(s) inside the condor's life: %s (short call early-exercise guard "
                     "%s; the condor's last session before expiry is the last cum-dividend day when the ex-date is the "
                     "expiry)", p.underlying, ", ".join(str(d) for d in entry_row["exdiv"]),
                     "ON" if C.CONDOR_EXDIV_GUARD else "OFF")
        log.info("         sizing: %dx -> max loss $%s = %.1f%% of equity (cap %.0f%%); shared budget used before "
                 "$%s, after $%s of $%s (%.0f%% cap)", n, f"{per * n:,.0f}", 100 * per * n / eq,
                 100 * C.CONDOR_RISK_PER_POSITION, f"{entry_row.get('used_before', 0):,.0f}",
                 f"{entry_row.get('used_before', 0) + per * n:,.0f}", f"{C.RISK_TOTAL * eq:,.0f}", 100 * C.RISK_TOTAL)


# ---------------------------------------------------------------- reporting
def _fmt(x, f="{:.2f}"):
    return f.format(x) if isinstance(x, (int, float)) and math.isfinite(x) else "-"


def print_plan(acct, kill, vix, exit_rows, entry_rows, mode, decide=None):
    log.info("=" * 96)
    log.info("MODE %s | equity $%s%s | %s", mode, f"{acct['equity']:,.2f}",
             " (SIMULATED, no options account yet)" if acct.get("simulated") else "", vix["source"])
    if decide is not None:
        log.info("decision time %s ET (DECIDE_MINUTES_BEFORE_CLOSE=%d)", f"{decide:%H:%M}",
                 C.DECIDE_MINUTES_BEFORE_CLOSE)
    log.info("kill switch: %s -> %s", "BLOCK" if kill[0] else "ok", kill[1])
    log.info("-- open spreads: %d", len(exit_rows))
    for r in exit_rows:
        log.info("   %-26s x%-3s credit %s (mid %s)  cost mid %s  nat %s  held %sd  tdte %s  close %s vs 5DMA %s -> %s",
                 r["id"], r["qty"], _fmt(float(r["entry_credit"])), _fmt(r.get("entry_mid")), _fmt(r["mid_debit"]),
                 _fmt(r["natural_debit"]), r["held"], r["tdte"], _fmt(r["close"]), _fmt(r["sma5"]),
                 r["reason"] or "hold")
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


# ---------------------------------------------------------------- record templates (also journaled in inflight.json)
def dip_template(p: S.Pick, signal: dict | None) -> dict:
    """Everything a dip record holds except qty / entry_date / entry_credit / order_ids."""
    return {"underlying": p.underlying, "expiry": p.expiry.isoformat(),
            "short_symbol": p.short.symbol, "long_symbol": p.long.symbol,
            "short_strike": p.short.strike, "long_strike": p.long.strike,
            "entry_mid": p.mid_credit, "signal": signal, "short_delta": p.short.delta, "long_delta": p.long.delta}


def condor_template(p: S.CondorPick) -> dict:
    return {"sleeve": C.SLEEVE_CONDOR, "underlying": p.underlying, "expiry": p.expiry.isoformat(),
            "put_short_symbol": p.put_short.symbol, "put_long_symbol": p.put_long.symbol,
            "call_short_symbol": p.call_short.symbol, "call_long_symbol": p.call_long.symbol,
            "put_short_strike": p.put_short.strike, "put_long_strike": p.put_long.strike,
            "call_short_strike": p.call_short.strike, "call_long_strike": p.call_long.strike,
            "entry_mid": p.mid_credit, "entry_spot": p.spot, "dte": p.dte,
            "put_short_delta": p.put_short.delta, "call_short_delta": p.call_short.delta}


def _template(j) -> dict:
    t = j.meta.get("template")
    if t:
        return dict(t)
    p = j.meta["pick"]
    return condor_template(p) if j.sleeve == C.SLEEVE_CONDOR else dip_template(p, j.meta.get("signal"))


def _exec_quality(j) -> dict:
    """Planned mid / natural and the fill's slippage vs the mid (per share, and $ for the order)."""
    q = int(round(j.filled_qty))
    mid, nat = j.meta.get("mid"), j.meta.get("natural")
    slip = S.slippage(j.credit, mid, j.avg_price) if q > 0 else None
    return {"mid_price": mid, "natural_price": nat, "slippage": slip,
            "slippage_usd": round(slip * 100 * q, 2) if slip is not None else None}


# ---------------------------------------------------------------- applying results
TRADE_COLS = ("id", "spread_id", "underlying", "expiry", "short_strike", "long_strike", "qty", "opened_at",
              "closed_at", "entry_credit", "exit_debit", "pnl", "pnl_pct", "exit_reason")


def trade_id(key: str, entry_date: str, closed: str, order_id: str | None) -> str:
    """Unique per closing order: two partial closes on one day get two ids."""
    return f"{key}-{entry_date}-{closed}-{(order_id or 'x')[-12:]}"


def apply_results(jobs, records: dict, today: dt.date, run_id, persist: bool,
                  condors: dict | None = None) -> list[dict]:
    """Book fills: dip jobs into ``records``, condor jobs into ``condors``."""
    trades = []
    condors = {} if condors is None else condors
    for j in jobs:
        try:
            if j.sleeve == C.SLEEVE_CONDOR:
                t = _apply_condor(j, condors, today, run_id, persist)
            else:
                t = _apply_one(j, records, today, run_id, persist)
        except Exception as e:                  # noqa: BLE001  (one bad job must not lose the others)
            log.error("apply_results %s %s failed: %s\n%s", j.purpose, j.key, e, traceback.format_exc())
            continue
        if t:
            trades.append(t)
    return trades


def _apply_one(j, records: dict, today: dt.date, run_id, persist: bool):
    q = int(round(j.filled_qty))
    xq = _exec_quality(j)
    orow = {"key": j.key, "purpose": j.purpose, "status": j.status, "qty": j.qty, "filled_qty": q,
            "avg_price": j.avg_price, "order_ids": j.order_ids, "fills": j.fills, "history": j.history,
            "error": j.error, "date": today.isoformat(), "reason": j.meta.get("reason"), **xq}
    log.info("%s %s: %s %d/%d @ %s (mid %s, slippage %s/share)", j.purpose, j.key, j.status, q, j.qty,
             _fmt(j.avg_price), _fmt(xq["mid_price"]), _fmt(xq["slippage"]))
    if persist:
        state.append(state.ORDERS, orow)
        db.insert("options_live_orders", {
            "id": j.order_ids[-1] if j.order_ids else f"none-{j.key}-{today}", "run_id": run_id,
            "spread_id": j.key, "underlying": j.key.split("-")[0], "purpose": j.purpose,
            "qty": j.qty, "filled_qty": q, "limit_prices": j.prices, "avg_price": j.avg_price,
            "status": j.status, "attempts": len(j.order_ids), "history": j.history, "error": j.error,
            "submitted_at": j.history[0]["t"] if j.history else None, **xq}, upsert_on="id")
    if q <= 0:
        return None
    if j.purpose == "open":
        prev = records.get(j.key)
        if prev:                                # same spread already held (should not happen): merge
            n0 = int(prev["qty"])
            credit = (float(prev["entry_credit"]) * n0 + j.avg_price * q) / (n0 + q)
            records[j.key] = {**prev, "qty": n0 + q, "entry_credit": round(credit, 4),
                              "order_ids": list(prev.get("order_ids", [])) + j.order_ids}
            return None
        records[j.key] = {**_template(j), "qty": q, "entry_date": today.isoformat(), "entry_credit": j.avg_price,
                          "order_ids": j.order_ids}
        return None
    r = j.meta["record"]
    width = r["short_strike"] - r["long_strike"]
    pnl = round((float(r["entry_credit"]) - j.avg_price) * 100 * q, 2)
    fill_ids = [o for o in j.order_ids if o in j.fills]
    t = {"id": trade_id(j.key, r["entry_date"], today.isoformat(), fill_ids[-1] if fill_ids else None),
         "spread_id": j.key, "underlying": r["underlying"],
         "expiry": r["expiry"], "short_strike": r["short_strike"], "long_strike": r["long_strike"],
         "qty": q, "opened_at": r["entry_date"], "closed_at": today.isoformat(),
         "entry_credit": r["entry_credit"], "exit_debit": j.avg_price, "pnl": pnl,
         "pnl_pct": round(pnl / max(S.max_loss(width, float(r["entry_credit"]), q), 1e-9), 4),
         "exit_reason": j.meta["reason"]}
    left = int(r["qty"]) - q
    if left > 0:
        records[j.key] = {**r, "qty": left}
    else:
        records.pop(j.key, None)
    if persist:
        state.append(state.TRADES, t)
        db.insert("options_live_trades", t, upsert_on="id")
    return t


def _apply_condor(j, condors: dict, today: dt.date, run_id, persist: bool):
    q = int(round(j.filled_qty))
    xq = _exec_quality(j)
    orow = {"key": j.key, "sleeve": C.SLEEVE_CONDOR, "purpose": j.purpose, "status": j.status, "qty": j.qty,
            "filled_qty": q, "avg_price": j.avg_price, "order_ids": j.order_ids, "fills": j.fills,
            "history": j.history, "error": j.error, "date": today.isoformat(), "reason": j.meta.get("reason"), **xq}
    log.info("condor %s %s: %s %d/%d @ %s (mid %s, slippage %s/share)", j.purpose, j.key, j.status, q, j.qty,
             _fmt(j.avg_price), _fmt(xq["mid_price"]), _fmt(xq["slippage"]))
    if persist:
        state.append(state.ORDERS, orow)
        db.insert("options_live_orders", {
            "id": j.order_ids[-1] if j.order_ids else f"none-{j.key}-{today}", "run_id": run_id,
            "spread_id": j.key, "underlying": j.key.split("-")[0], "purpose": j.purpose, "sleeve": C.SLEEVE_CONDOR,
            "qty": j.qty, "filled_qty": q, "limit_prices": j.prices, "avg_price": j.avg_price,
            "status": j.status, "attempts": len(j.order_ids), "history": j.history, "error": j.error,
            "submitted_at": j.history[0]["t"] if j.history else None, **xq}, upsert_on="id")
    if q <= 0:
        return None
    if j.purpose == "open":
        prev = condors.get(j.key)
        if prev:
            n0 = int(prev["qty"])
            credit = (float(prev["entry_credit"]) * n0 + j.avg_price * q) / (n0 + q)
            condors[j.key] = {**prev, "qty": n0 + q, "entry_credit": round(credit, 4),
                              "order_ids": list(prev.get("order_ids", [])) + j.order_ids}
            return None
        condors[j.key] = {**_template(j), "qty": q, "entry_date": today.isoformat(), "entry_credit": j.avg_price,
                          "order_ids": j.order_ids}
        return None
    r = j.meta["record"]
    credit = float(r["entry_credit"])
    pnl = round((credit - j.avg_price) * 100 * q, 2)
    fill_ids = [o for o in j.order_ids if o in j.fills]
    t = condor_trade_row(j.key, r, q, today, fill_ids[-1] if fill_ids else None, j.avg_price, pnl,
                         S.max_loss(S.condor_width(r), credit, q), j.meta["reason"])
    left = int(r["qty"]) - q
    if left > 0:
        condors[j.key] = {**r, "qty": left}
    else:
        condors.pop(j.key, None)
    if persist:
        state.append(state.TRADES, t)
        db.insert("options_live_trades", t, upsert_on="id")
    return t


# ---------------------------------------------------------------- book: broker positions vs local records
def _recover_entry(broker: Broker, sid: str, short_symbol: str, today: dt.date) -> tuple[str, str] | None:
    """(entry date, source) for a spread held at the broker with no local record:
    the order journal first, then the account's FILL activities."""
    for row in reversed(state.read_jsonl(state.ORDERS)):
        if row.get("key") == sid and row.get("purpose") == "open" and (row.get("filled_qty") or 0) > 0:
            return row["date"], "order journal"
    try:
        fills = broker.fills((today - dt.timedelta(days=45)).isoformat())
    except Exception as e:                      # noqa: BLE001
        log.warning("%s: fills unavailable for entry-date recovery: %s", sid, e)
        return None
    opens = [f for f in fills if f.get("symbol") == short_symbol and f.get("side") in ("sell", "sell_short")]
    if not opens:
        return None
    import pandas as pd
    t = pd.Timestamp(opens[-1]["transaction_time"]).tz_convert(ET).date()
    return t.isoformat(), "fills"


def _leg_exit(acts: list[dict], sym, close_side):
    """(exit price per share, how) of one leg from FILL / OPEXP / OPASN / OPEXC activities."""
    kinds = {a.get("activity_type") for a in acts if a.get("symbol") == sym}
    if "OPASN" in kinds or "OPEXC" in kinds:
        return None, "assigned"
    fl = [a for a in acts if a.get("symbol") == sym and a.get("activity_type") == "FILL"
          and a.get("side") == close_side]
    n = sum(float(a.get("qty") or 0) for a in fl)
    if n > 0:
        return sum(float(a["price"]) * float(a.get("qty") or 0) for a in fl) / n, "closed"
    if "OPEXP" in kinds:
        return 0.0, "expired"
    return None, "unknown"


def _external_close(broker: Broker, v: dict, today: dt.date) -> dict:
    """Best-effort P&L for a spread that left the broker outside the bot (expired,
    assigned, closed by hand): FILL / OPEXP / OPASN / OPEXC activities since entry."""
    qty = int(v.get("qty") or 0)
    reason, exit_debit, pnl = "external", None, None
    try:
        acts = broker.activities(["FILL", "OPEXP", "OPASN", "OPEXC"], v.get("entry_date") or today.isoformat())
    except Exception as e:                      # noqa: BLE001
        log.warning("%s: activities unavailable: %s", v["id"], e)
        acts = []

    s_px, s_how = _leg_exit(acts, v.get("short_symbol"), "buy")
    l_px, l_how = _leg_exit(acts, v.get("long_symbol"), "sell")
    reason = f"external_{s_how}"
    if s_px is not None and l_px is not None and v.get("entry_credit") is not None:
        exit_debit = round(s_px - l_px, 4)
        pnl = round((float(v["entry_credit"]) - exit_debit) * 100 * qty, 2)
    width = float(v.get("short_strike", 0)) - float(v.get("long_strike", 0))
    ml = S.max_loss(width, float(v.get("entry_credit") or 0), qty) if qty else 0
    return {"id": trade_id(v["id"], v.get("entry_date", "?"), today.isoformat(), "external"),
            "spread_id": v["id"], "underlying": v.get("underlying"), "expiry": v.get("expiry"),
            "short_strike": v.get("short_strike"), "long_strike": v.get("long_strike"), "qty": qty,
            "opened_at": v.get("entry_date"), "closed_at": today.isoformat(), "entry_credit": v.get("entry_credit"),
            "exit_debit": exit_debit, "pnl": pnl,
            "pnl_pct": round(pnl / ml, 4) if pnl is not None and ml else None, "exit_reason": reason}


def load_book(broker: Broker, today: dt.date, persist: bool, condors_out: dict | None = None):
    """(dip records, orphan option legs, stock positions, raw positions).
    The condor sleeve's records are written into ``condors_out`` when given.
    Legs are split by sleeve with the state store (S.group_book), so condor legs
    are never paired into dip spreads."""
    pos = broker.positions()
    stocks = [p for p in pos if S.parse_occ(p.get("symbol", "")) is None]
    stored = state.load_positions()
    stored_condors = state.load_condors()
    spreads, condors, orphans = S.group_book([p for p in pos if p not in stocks], stored_condors, stored)
    for o in orphans:
        log.warning("unpaired option leg (not traded automatically): %s qty %s (unmatched %s)",
                    o.get("symbol"), o.get("qty"), o.get("unmatched_qty"))
    for p in stocks:
        log.error("STOCK position %s qty %s in the options account: assignment or exercise", p["symbol"], p["qty"])
    records, adopted, vanished = state.reconcile(spreads, stored, today)
    for a in adopted:
        rec = _recover_entry(broker, a, records[a]["short_symbol"], today)
        if rec:
            records[a]["entry_date"], records[a]["entry_date_source"] = rec
            log.warning("adopted spread %s with no local record; entry date %s from the %s", a, rec[0], rec[1])
        else:
            log.warning("adopted spread %s with no local record; entry date unknown, set to today", a)
    for v in vanished:
        log.warning("spread %s is no longer at the broker (expired/assigned/closed outside the bot)", v["id"])
        if persist:
            t = _external_close(broker, v, today)
            log.warning("   journaled as %s, P&L %s", t["exit_reason"], _fmt(t["pnl"]))
            state.append(state.TRADES, t)
            if t["pnl"] is not None:
                db.insert("options_live_trades", t, upsert_on="id")
    if persist and (adopted or vanished):
        state.save_positions(records)
    crecords = load_condor_book(broker, condors, stored_condors, today, persist)
    if condors_out is not None:
        condors_out.clear()
        condors_out.update(crecords)
    if persist:
        book_alerts(records, crecords, orphans, stocks)
    return records, orphans, stocks, pos


def load_condor_book(broker: Broker, condors, stored: dict, today: dt.date, persist: bool) -> dict:
    """Condor records for the condors at the broker: adopt untracked ones, journal vanished ones."""
    if not condors and not stored:
        return {}
    records, adopted, vanished = state.reconcile_condors(condors, stored, today)
    for a in adopted:
        rec = _recover_entry(broker, a, records[a]["put_short_symbol"], today)
        if rec:
            records[a]["entry_date"], records[a]["entry_date_source"] = rec
        log.warning("adopted condor %s with no local record; entry date %s", a,
                    f"{rec[0]} from the {rec[1]}" if rec else "unknown, set to today")
    for v in vanished:
        log.warning("condor %s is no longer at the broker (expired/assigned/closed outside the bot)", v["id"])
        if persist:
            t = _external_close_condor(broker, v, today)
            log.warning("   journaled as %s, P&L %s", t["exit_reason"], _fmt(t["pnl"]))
            state.append(state.TRADES, t)
            if t["pnl"] is not None:
                db.insert("options_live_trades", t, upsert_on="id")
    if persist and (adopted or vanished):
        state.save_condors(records)
    return records


def _external_close_condor(broker: Broker, v: dict, today: dt.date) -> dict:
    qty = int(v.get("qty") or 0)
    try:
        acts = broker.activities(["FILL", "OPEXP", "OPASN", "OPEXC"], v.get("entry_date") or today.isoformat())
    except Exception as e:                      # noqa: BLE001
        log.warning("%s: activities unavailable: %s", v["id"], e)
        acts = []
    legs = [_leg_exit(acts, v.get(k), side) for k, side in (("put_short_symbol", "buy"), ("put_long_symbol", "sell"),
                                                            ("call_short_symbol", "buy"), ("call_long_symbol", "sell"))]
    hows = [h for _, h in legs]
    how = "assigned" if "assigned" in hows else ("closed" if "closed" in hows else hows[0])
    exit_debit = pnl = None
    if all(px is not None for px, _ in legs) and v.get("entry_credit") is not None:
        exit_debit = round(legs[0][0] - legs[1][0] + legs[2][0] - legs[3][0], 4)
        pnl = round((float(v["entry_credit"]) - exit_debit) * 100 * qty, 2)
    ml = S.max_loss(S.condor_width(v), float(v.get("entry_credit") or 0), qty) if qty else 0
    return condor_trade_row(v["id"], v, qty, today, "external", exit_debit, pnl, ml, f"external_{how}")


CONDOR_TRADE_COLS = ("sleeve", "call_short_strike", "call_long_strike")


def condor_trade_row(cid: str, r: dict, qty: int, today: dt.date, order_id, exit_debit, pnl, ml, reason) -> dict:
    """A trade row for options_live_trades: the put strikes go in short/long_strike,
    the call strikes and the sleeve in the columns migration 006 adds."""
    return {"id": trade_id(cid, r.get("entry_date", "?"), today.isoformat(), order_id),
            "spread_id": cid, "underlying": r.get("underlying"), "expiry": r.get("expiry"),
            "short_strike": r.get("put_short_strike"), "long_strike": r.get("put_long_strike"), "qty": qty,
            "opened_at": r.get("entry_date"), "closed_at": today.isoformat(), "entry_credit": r.get("entry_credit"),
            "exit_debit": exit_debit, "pnl": pnl, "pnl_pct": round(pnl / ml, 4) if pnl is not None and ml else None,
            "exit_reason": reason, "sleeve": C.SLEEVE_CONDOR, "call_short_strike": r.get("call_short_strike"),
            "call_long_strike": r.get("call_long_strike")}


def orphan_qty(o: dict) -> int:
    """Signed quantity of an unpaired leg that is NOT part of a matched spread
    (group_spreads' unmatched_qty; negative = short). Falls back to the raw
    position quantity for legs that were never matched at all."""
    if "unmatched_qty" in o:
        return int(round(float(o["unmatched_qty"])))
    q = int(round(float(o.get("qty") or 0)))
    return -abs(q) if o.get("side") == "short" else q


def flatten_stock(broker: Broker, stocks: list[dict], today: dt.date) -> list[str]:
    """Market orders that close stock left by an assignment. Returns messages."""
    msgs = []
    for p in stocks:
        qty = int(round(abs(float(p["qty"]))))
        if qty == 0:
            continue
        short = p.get("side") == "short" or float(p["qty"]) < 0
        side = "buy" if short else "sell"
        try:
            o = broker.submit_stock(p["symbol"], qty, side, client_order_id_for("assign", p["symbol"], today))
            msgs.append(f"flatten {side} {qty} {p['symbol']} (order {o.get('id')}, {o.get('status')})")
        except Exception as e:                  # noqa: BLE001
            msgs.append(f"FAILED to flatten {qty} {p['symbol']}: {e}")
        log.error("assignment: %s", msgs[-1])
    return msgs


def client_order_id_for(*parts) -> str:
    return client_order_id(*parts)


def _settle(broker: Broker, jobs, records: dict, today: dt.date, run_id, condors: dict | None = None) -> list[dict]:
    """After working orders (or an exception while working them): cancel the
    bot's orders, book every fill, save positions (and the condor records when a
    condor job ran). Never raises."""
    try:
        left = broker.cancel_all(timeout=20) if broker.execute else []
        if left:
            log.error("%d bot order(s) could not be confirmed canceled", len(left))
    except Exception as e:                      # noqa: BLE001
        log.error("cancel sweep failed: %s", e)
    trades = []
    saved = True
    try:
        trades = apply_results(jobs, records, today, run_id, persist=True, condors=condors)
    finally:
        try:
            state.save_positions(records)
        except Exception as e:                  # noqa: BLE001
            saved = False
            log.error("SAVING POSITIONS FAILED: %s", e)
        if condors is not None and any(j.sleeve == C.SLEEVE_CONDOR for j in jobs):
            try:
                state.save_condors(condors)
            except Exception as e:              # noqa: BLE001
                saved = False
                log.error("SAVING CONDOR POSITIONS FAILED: %s", e)
        if saved:
            resilience.finish_inflight()        # every fill is booked: the write-ahead journal can go
    return trades


# ---------------------------------------------------------------- round 3: recovery, resting orders, alerts
def recover_inflight(broker: Broker) -> list[str]:
    """Book what a dead run left (RECOVER_INFLIGHT; execute mode only). Works on the stored
    records, before load_book reconciles them with the broker. Returns warnings."""
    if not C.RECOVER_INFLIGHT:
        return []
    jobs, rec, problems = resilience.recover(broker)
    if rec is None:
        return []
    day = dt.date.fromisoformat(rec["date"])
    records, condors = state.load_positions(), state.load_condors()
    booked = []
    for j in jobs:
        if j.filled_qty <= 0:
            continue
        book = condors if j.sleeve == C.SLEEVE_CONDOR else records
        prev = book.get(j.key)
        if j.purpose == "open":
            if prev and not prev.get("adopted"):
                continue                        # the dead run had booked it already
            book.pop(j.key, None)               # replace a blind adoption with the planned metadata
        else:
            if not prev:
                continue                        # already booked (record gone)
            j.meta["record"] = prev
        booked.append(j)
    trades = apply_results(booked, records, day, None, persist=True, condors=condors)
    state.save_positions(records)
    state.save_condors(condors)
    msg = (f"recovered the {rec.get('kind')} run of {rec.get('date')} (started {rec.get('started_at')}): "
           f"{len(jobs)} job(s), {len(booked)} with unbooked fills"
           + (f" ({', '.join(f'{j.purpose} {j.key} {j.filled_qty:g}x @ {_fmt(j.avg_price)}' for j in booked)})"
              if booked else "") + (f", {len(trades)} trade(s) journaled" if trades else ""))
    if problems:
        msg += "; UNRESOLVED: " + " | ".join(problems) + " (journal kept; next run retries)"
    else:
        resilience.finish_inflight()
    log.warning(msg)
    alerts.raise_alert("inflight_recovered", msg, level="critical" if problems else "warning", dedupe=False)
    return [msg]


def _match_record(symbols: list[str], records: dict, condors: dict) -> tuple[str | None, str | None]:
    want = set(symbols)
    for sid, r in condors.items():
        if want and want == set(S.condor_leg_symbols(r)):
            return sid, C.SLEEVE_CONDOR
    for sid, r in records.items():
        if want and want == {r.get("short_symbol"), r.get("long_symbol")}:
            return sid, C.SLEEVE_DIP
    return None, None


def prepare_resting(broker: Broker, today: dt.date) -> tuple[set, list[str]]:
    """Cancel (and confirm) every resting GTC order before the book is read, booking any fill
    it got as a closed trade. Execute mode only, and only when resting orders are in use.
    Returns (spread ids that must not be traded this run, warnings)."""
    if not resilience.resting_in_use():
        return set(), []
    settled, stuck, problems = resilience.cancel_resting(broker)
    warnings = list(problems)
    records, condors = state.load_positions(), state.load_condors()
    jobs = []
    for e in settled:
        if e["filled_qty"] <= 0:
            continue
        sid, sleeve = (e["sid"], e.get("sleeve")) if e.get("sid") else (None, None)
        if sid is None or (sid not in records and sid not in condors):
            sid, sleeve = _match_record(e["symbols"], records, condors)
        if sid is None:
            warnings.append(f"resting order {e['order_id']} filled {e['filled_qty']:g}x but matches no record; "
                            f"the book reconcile will journal it as external")
            continue
        sleeve = sleeve or (C.SLEEVE_CONDOR if sid in condors else C.SLEEVE_DIP)
        r = (condors if sleeve == C.SLEEVE_CONDOR else records)[sid]
        legs = (execution.condor_close_legs(*S.condor_leg_symbols(r)) if sleeve == C.SLEEVE_CONDOR
                else execution.close_legs(r["short_symbol"], r["long_symbol"]))
        px = e.get("filled_avg_price")
        try:
            px = abs(float(px))
        except (TypeError, ValueError):
            px = None
        j = execution.Job(sid, "close", legs, int(e["filled_qty"]), [px or 0.0], credit=False, sleeve=sleeve,
                          meta={"record": r, "reason": f"{e.get('kind') or 'resting'}_resting", "mid": None})
        j.order_ids.append(e["order_id"])
        j.fills[e["order_id"]] = (float(e["filled_qty"]), px if px is not None else 0.0)
        j.status = "filled" if e["filled_qty"] >= float(r["qty"]) else "partial"
        jobs.append(j)
        warnings.append(f"resting {e.get('kind') or ''} order filled: {sid} {e['filled_qty']:g}x @ {_fmt(px)}")
    if jobs:
        apply_results(jobs, records, today, None, persist=True, condors=condors)
        state.save_positions(records)
        state.save_condors(condors)
    if stuck:
        alerts.raise_alert("resting_order", f"resting order(s) not confirmed canceled: {', '.join(sorted(stuck))}; "
                                            f"those spreads are not traded this run", level="critical")
    else:
        alerts.resolve_cleared("resting_order")
    return stuck, warnings


def stage_resting_after(broker: Broker, records: dict, condors: dict, today: dt.date, sessions,
                        unfilled_expiry: list | None = None) -> list[str]:
    """Stage resting GTC orders (STAGE_TP_ORDERS / REST_UNFILLED_EXPIRY_EXITS) after a settled run."""
    if not (C.STAGE_TP_ORDERS or C.REST_UNFILLED_EXPIRY_EXITS):
        return []
    try:
        return resilience.stage_resting(broker, records, condors, today, sessions, unfilled_expiry)
    except Exception as e:                      # noqa: BLE001
        log.error("staging resting orders failed: %s", e)
        alerts.raise_alert("resting_order", f"staging resting orders failed: {e}", level="warning")
        return [f"resting orders not staged: {e}"]


def book_alerts(records: dict, condors: dict, orphans: list, stocks: list) -> None:
    """Condition alerts that follow the book: assignment stock, unpaired legs, and resolution of
    per-spread alerts whose spread is gone."""
    alerts.sync_condition("assignment", {p["symbol"]: f"stock {p['symbol']} qty {p['qty']} in the options account "
                                                      f"(assignment / exercise); the bot flattens it"
                                         for p in stocks}, level="critical")
    alerts.sync_condition("unpaired_legs", {o.get("symbol"): f"unpaired option leg {o.get('symbol')} unmatched qty "
                                                             f"{o.get('unmatched_qty', o.get('qty'))}; not traded "
                                                             f"automatically (--close-all closes it)"
                                            for o in orphans}, level="critical")
    held = set(records) | set(condors)
    for kind in ("exit_unfilled", "expiry_risk", "condor_deadline", "exdiv_risk"):
        alerts.resolve_cleared(kind, held)


def _lock(timeout: float):
    """The executing-run lock. Waits up to ``timeout`` seconds; None if not acquired."""
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(C.STATE_DIR / ".lock", "w")
    waited = 0.0
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if waited >= timeout:
                fh.close()
                return None
            time.sleep(2)
            waited += 2


class LockBusy(RuntimeError):
    pass


def _take_lock(args, timeout: float):
    if not args.execute:
        return
    fh = _lock(timeout)
    if fh is None:
        raise LockBusy(f"another executing run held the lock for {timeout:.0f}s")
    args._lock = fh


# ---------------------------------------------------------------- modes
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
    if not args.now and (decide - now).total_seconds() > 20 * 60:
        # lets one crontab carry both a 15:45 and a 12:45 (half day) line
        log.info("decision time today is %s ET; this run is too early, exiting", f"{decide:%H:%M}")
        return 0
    _take_lock(args, C.LOCK_TIMEOUT_SECONDS)    # window checked first; wait for an intraday run to finish
    if args.execute and not args.force and state.already_ran(today):
        log.error("already executed today (%s); use --force to run again", today)
        return 1
    acct = broker.account()
    warnings: list[str] = []
    if args.execute:
        lvl = acct.get("options_trading_level")
        if lvl is None or int(lvl) < 3:
            log.error("options trading level %s < 3: spreads need level 3 on the paper account", lvl)
            db.heartbeat("error", f"{today}: options level {lvl} < 3")
            alerts.raise_alert("account", f"options trading level {lvl} < 3: the bot cannot trade spreads",
                               level="critical")
            return 1
        if acct.get("trading_blocked"):
            log.error("account trading is blocked")
            db.heartbeat("error", f"{today}: account trading blocked")
            alerts.raise_alert("account", "the options paper account has trading_blocked set", level="critical")
            return 1
        alerts.resolve_cleared("account")
        left = broker.cancel_all()              # the bot's own leftovers only; waits until none is open
        if left:
            msg = f"{today}: {len(left)} earlier bot order(s) would not cancel; refusing to trade"
            log.error(msg)
            db.heartbeat("error", msg)
            alerts.raise_alert("leftover_orders", msg, level="critical")
            return 1
        alerts.resolve_cleared("leftover_orders")
        warnings += recover_inflight(broker)
    if not args.now and now_et() < decide:
        secs = (decide - now_et()).total_seconds()
        log.info("waiting %.0fs for the decision time (%s ET)", secs, f"{decide:%H:%M}")
        time.sleep(max(secs, 0))
    condor_on = condor_enabled(args)
    condors: dict = {}
    stuck: set = set()
    if args.execute:
        stuck, w = prepare_resting(broker, today)
        warnings += w
    records, orphans, stocks, _ = load_book(broker, today, persist, condors_out=condors)
    if stocks:
        warnings.append(f"assignment: {len(stocks)} stock position(s)")
        if args.execute:
            warnings += flatten_stock(broker, stocks, today)
    if orphans:
        warnings.append(f"{len(orphans)} unpaired option leg(s)")
    ctx = Ctx(broker, today, strict=args.execute or not args.now)
    spy = ctx.hist.setdefault("SPY", broker.daily_bars("SPY", today - dt.timedelta(days=signals.HISTORY_DAYS),
                                                       today - dt.timedelta(days=1)))
    sessions = sessions_list(broker, spy.index, today)
    vix = signals.vix_levels(today, now=now_et())
    kill = S.kill_switch(vix["vix"], vix["vix3m"], fresh=vix["fresh"], stale_reason=vix["stale_reason"])
    exit_jobs, exit_rows = plan_exits(ctx, records, sessions, end_of_day=True,
                                      deadline=condor_deadline(today, close_at))
    late = [r["id"] for r in exit_rows if r["expiry"] <= today]
    if late:
        warnings.append(f"EXPIRING TODAY, past Alpaca's 15:30 expiration handling: {', '.join(late)}")
    closing = {j.key for j in exit_jobs}
    symbols = args.symbols.split(",") if args.symbols else C.universe()
    # condor sleeve: exits whenever a condor is held (even with the flag off), entries only when enabled.
    # It is planned before the dip entries so its max loss is reserved in the shared RISK_TOTAL budget.
    exdiv = condor_exdiv_info(broker, condors, today, sessions)
    cexit_jobs, cexit_rows = plan_condor_exits(ctx, condors, sessions, mode="daily",
                                               deadline=condor_deadline(today, close_at), exdiv=exdiv)
    if stuck:                                   # a resting order there is not confirmed canceled
        exit_jobs = [j for j in exit_jobs if j.key not in stuck]
        cexit_jobs = [j for j in cexit_jobs if j.key not in stuck]
    reserved = condor_open_max_loss(condors)
    centry_jobs, crow = [], None
    dip_acct = acct
    if condor_on:
        centry_jobs, crow = plan_condor_entry(ctx, condors, acct, kill, used=open_max_loss(records) + reserved,
                                              bp=acct.get("options_buying_power"), avoid=dip_symbols(records),
                                              preview=args.preview)
        for j in centry_jobs:
            p = j.meta["pick"]
            reserved += S.max_loss(p.width, j.prices[-1], j.qty)
            if acct.get("options_buying_power") is not None:
                dip_acct = {**acct, "options_buying_power": acct["options_buying_power"] - p.width * 100 * j.qty}
    avoid = condor_symbols(condors) | {s for j in centry_jobs for s in j.meta["pick"].symbols}
    entry_jobs, entry_rows = plan_entries(ctx, records, dip_acct, kill, symbols, preview=args.preview,
                                          reserved=reserved, avoid=avoid or None)
    mode = "EXECUTE" if args.execute else "DRY RUN"
    print_plan(acct, kill, vix, exit_rows, entry_rows, mode, decide)
    if condor_on or condors:
        print_condor(acct, cexit_rows, crow, condor_on)
        log.info("=" * 96)
    if crow is not None:
        entry_rows_db = entry_rows + [crow]
    else:
        entry_rows_db = entry_rows
    warnings += ctx.warnings
    run = db.insert("options_live_runs", {
        "run_date": today, "mode": "execute" if args.execute else "dry_run", "kind": "daily",
        "status": "planned", "started_at": dt.datetime.now(dt.timezone.utc), "equity": acct["equity"],
        "vix": vix["vix"], "vix3m": vix["vix3m"], "kill_switch": kill[0],
        "signals": [{k: v for k, v in r.items() if k not in ("pick",)} for r in entry_rows_db],
        "n_exits": len(exit_jobs) + len(cexit_jobs), "n_entries": len(entry_jobs) + len(centry_jobs),
        "message": f"{len(exit_jobs)} exits, {len(entry_jobs)} entries planned"
                   + (f"; condor {len(cexit_jobs)} exits, {len(centry_jobs)} entries" if condor_on or condors else "")})
    run_id = run[0]["id"] if run else None
    if not args.execute:
        window = (f"from {max(now_et(), decide):%H:%M} to {cutoff:%H:%M} ET" if now_et() < cutoff
                  else f"in the {decide:%H:%M}-{cutoff:%H:%M} ET window (market already closed; prices are the latest available)")
        log.info("DRY RUN: nothing sent. %d exit(s) and %d entr%s would be worked %s.",
                 len(exit_jobs), len(entry_jobs), "y" if len(entry_jobs) == 1 else "ies", window)
        if condor_on or condors:
            log.info("DRY RUN: condor sleeve: %d exit(s), %d entr%s.", len(cexit_jobs), len(centry_jobs),
                     "y" if len(centry_jobs) == 1 else "ies")
        for w in warnings:
            log.warning("warning: %s", w)
        db.heartbeat("ok", f"{today}: dry run, {len(exit_jobs)} exits / {len(entry_jobs)} entries planned")
        return 0
    state.mark_ran(today)
    jobs = exit_jobs + [j for j in entry_jobs if j.key not in closing] + cexit_jobs + centry_jobs
    trades = []
    resilience.start_inflight(jobs, "daily", today)
    try:
        execution.work_orders(broker, jobs, cutoff=cutoff, hard_stop=hard_stop)
    finally:
        trades = _settle(broker, jobs, records, today, run_id, condors=condors)
    for j in entry_jobs + centry_jobs:
        if j.filled_qty <= 0 and j.status != "skipped":
            log.warning("entry %s did not fill by the %s ET cutoff (ladder %s); no entry today, the signal is "
                        "re-evaluated at the next daily run", j.key, f"{cutoff:%H:%M}", j.prices)
    warnings += stage_resting_after(
        broker, records, condors, today, sessions,
        [j for j in exit_jobs + cexit_jobs if j.remaining > 0 and j.meta.get("reason") in ("expiry_exit", "exdiv_exit")])
    n_open = sum(1 for j in entry_jobs if j.filled_qty > 0)
    n_close = sum(1 for j in exit_jobs if j.filled_qty > 0)
    unfilled = [j for j in exit_jobs + cexit_jobs if j.remaining > 0]
    cblocked = [r["id"] for r in cexit_rows + exit_rows if r.get("blocked")]
    urgent = [j.key for j in unfilled if j.meta.get("tdte", 99) <= C.CLOSE_DTE_TRADING_DAYS
              or j.meta.get("reason") == "exdiv_exit"]
    msg = (f"{today}: opened {n_open}, closed {n_close}, {len(records)} open, "
           f"realized ${sum(t['pnl'] for t in trades):,.2f}")
    if unfilled:
        msg += f"; EXITS NOT FILLED: {', '.join(j.key for j in unfilled)}"
    if urgent:
        msg += (f"; EXPIRY EXIT NOT FILLED (tdte<=1): {', '.join(urgent)}: the --sweep run closes them after "
                f"the open tomorrow, before Alpaca's 15:30 expiration handling; check them by hand")
    if cexit_jobs or centry_jobs or condors:
        msg += (f"; condor: opened {sum(1 for j in centry_jobs if j.filled_qty > 0)}, "
                f"closed {sum(1 for j in cexit_jobs if j.filled_qty > 0)}, {len(condors)} open")
    if warnings:
        msg += "; warnings: " + " | ".join(warnings)
    level = "error" if urgent or stocks or cblocked else ("warning" if unfilled or warnings else "ok")
    (log.error if level == "error" else log.warning if level == "warning" else log.info)(msg)
    for j in unfilled:
        alerts.raise_alert("exit_unfilled", f"{j.meta.get('reason')} exit of {j.key} not filled "
                                            f"({j.filled_qty:g}/{j.qty}) at the daily run",
                           ticker=j.key, level="critical" if j.key in urgent else "warning")
    for cid in cblocked:
        alerts.raise_alert("condor_deadline", f"condor {cid} expires today and is still open past the deadline; "
                                              f"close it by hand", ticker=cid, level="critical")
    for r in cexit_rows:
        if r.get("reason") == "exdiv_exit":
            alerts.raise_alert("exdiv_risk", f"condor {r['id']}: {r.get('exdiv')}; closing before the ex-date",
                               ticker=r["id"], level="warning")
    if run_id:
        db.update("options_live_runs", {"id": run_id}, {"status": "executed", "message": msg[:2000]})
    db.heartbeat(level, msg)
    return 0


def _intraday_window(sess: dict, kind: str):
    if kind == "sweep":
        start = sess["open_at"] + dt.timedelta(minutes=C.EXPIRY_SWEEP_MINUTES_AFTER_OPEN)
    else:
        start = sess["open_at"] + dt.timedelta(minutes=C.INTRADAY_START_MINUTES_AFTER_OPEN)
    stop = sess["close_at"] - dt.timedelta(minutes=C.START_MINUTES_BEFORE_CLOSE + 2)
    return start, stop


def run_intraday(args, kind: str = "intraday") -> int:
    """kind "intraday": take-profit / stop, plus spreads expiring today.
    kind "sweep": the mandatory expiry-day morning sweep (spreads expiring today
    or earlier, and stock from assignments), before Alpaca's 15:30 handling."""
    broker = Broker(execute=args.execute)
    now = now_et()
    today = now.date()
    sess = market_session(broker, today)
    if not sess["open"]:
        return 0
    start, stop = _intraday_window(sess, kind)
    if not args.now and not (start <= now <= stop):     # checked before taking the lock
        log.info("%s check outside its window (%s-%s ET)", kind, f"{start:%H:%M}", f"{stop:%H:%M}")
        return 0
    _take_lock(args, 30 if kind == "sweep" else 0)
    warnings = []
    stuck: set = set()
    if args.execute:
        warnings += recover_inflight(broker)
        stuck, w = prepare_resting(broker, today)
        warnings += w
    condors: dict = {}
    records, orphans, stocks, _ = load_book(broker, today, args.execute, condors_out=condors)
    if stocks:
        warnings.append(f"assignment: {len(stocks)} stock position(s)")
        if args.execute:
            warnings += flatten_stock(broker, stocks, today)
    if not records and not condors:
        log.info("%s: no open spreads", kind)
        if warnings and args.execute:
            db.heartbeat("error" if stocks else "warning", f"{today} {kind}: " + " | ".join(warnings))
        elif args.execute:
            db.liveness()
        return 0
    ctx = Ctx(broker, today, strict=args.execute)
    spy = broker.daily_bars("SPY", today - dt.timedelta(days=30), today - dt.timedelta(days=1))
    sessions = sessions_list(broker, spy.index, today)
    deadline = condor_deadline(today, sess["close_at"])
    jobs, rows = plan_exits(ctx, records, sessions, end_of_day=False, mode=kind, deadline=deadline)
    for r in rows:
        log.info("%s %-26s mid %s credit %s -> %s", kind, r["id"], _fmt(r["mid_debit"]),
                 _fmt(float(r["entry_credit"])), r["reason"] or "hold")
    exdiv = condor_exdiv_info(broker, condors, today, sessions) if kind == "intraday" else None
    cjobs, crows = plan_condor_exits(ctx, condors, sessions, mode=kind, deadline=deadline, exdiv=exdiv)
    for r in crows:
        log.info("%s condor %-34s mid %s credit %s -> %s", kind, r["id"], _fmt(r["mid_debit"]),
                 _fmt(float(r["entry_credit"])), r["reason"] or "hold to expiry")
    cblocked = [r["id"] for r in crows if r.get("blocked")] + [r["id"] for r in rows if r.get("blocked")]
    jobs += cjobs
    if stuck:
        jobs = [j for j in jobs if j.key not in stuck]
    for j in jobs:                              # intraday take-profits do not chase
        if j.meta["reason"] == "take_profit":
            j.cutoff_price = None
    warnings += ctx.warnings
    if not jobs or not args.execute:
        if jobs:
            log.info("DRY RUN: %d %s exit(s) not sent", len(jobs), kind)
        if args.execute:
            for cid in cblocked:
                alerts.raise_alert("condor_deadline", f"{cid} expires today and is still open past the deadline; "
                                                      f"close it by hand", ticker=cid, level="critical")
            warnings += stage_resting_after(broker, records, condors, today, sessions)
            if warnings:
                db.heartbeat("error" if cblocked else "warning", f"{today} {kind}: " + " | ".join(warnings))
            else:
                db.liveness()
        return 0
    if any(j.meta["reason"] == "expiry_day_sweep" for j in jobs):
        warnings.append("expiry-day sweep: " + ", ".join(j.key for j in jobs if j.meta["reason"] == "expiry_day_sweep"))
    cutoff = min(now + dt.timedelta(minutes=4), stop)
    hard_stop = cutoff + dt.timedelta(minutes=1)
    dip_today = C.DIP_EXPIRY_DAY_DEADLINE_ENABLED and any(
        j.sleeve == C.SLEEVE_DIP and isinstance(j.meta.get("expiry"), dt.date) and j.meta["expiry"] <= today
        for j in jobs)
    condor_today = any(isinstance(j.meta.get("expiry"), dt.date) and j.meta["expiry"] <= today for j in cjobs)
    if condor_today or dip_today:               # expiry-day orders never work past the deadline
                                                # (an ex-dividend exit the day before is not limited)
        cutoff, hard_stop = min(cutoff, deadline), min(hard_stop, deadline)
    resilience.start_inflight(jobs, kind, today)
    try:
        execution.work_orders(broker, jobs, cutoff=cutoff, hard_stop=hard_stop, step_wait=40)
    finally:
        _settle(broker, jobs, records, today, None, condors=condors)
    warnings += stage_resting_after(broker, records, condors, today, sessions)
    left = [j.key for j in jobs if j.remaining > 0 and j.meta["reason"] == "expiry_day_sweep"]
    if left:
        warnings.append(f"EXPIRING TODAY AND NOT CLOSED: {', '.join(left)}; close by hand before 15:30 ET")
    for k in left:
        alerts.raise_alert("expiry_risk", f"{k} expires today and the {kind} close did not fill; close it by hand "
                                          f"before 15:30 ET", ticker=k, level="critical")
    for k in cblocked:
        alerts.raise_alert("condor_deadline", f"{k} expires today and is still open past the deadline; close it "
                                              f"by hand", ticker=k, level="critical")
    for r in crows:
        if r.get("reason") == "exdiv_exit":
            alerts.raise_alert("exdiv_risk", f"condor {r['id']}: {r.get('exdiv')}; closing before the ex-date",
                               ticker=r["id"], level="warning")
    if warnings:
        db.heartbeat("error" if left or stocks or cblocked else "warning", f"{today} {kind}: " + " | ".join(warnings))
    return 0


def run_close_all(args) -> int:
    broker = Broker(execute=args.execute)
    now = now_et()
    today = now.date()
    _take_lock(args, 60)
    if args.execute:
        clk = broker.clock()
        if not clk or not clk.get("is_open"):
            log.error("market is closed; emergency close needs an open market")
            return 1
        broker.cancel_all()
        recover_inflight(broker)
        prepare_resting(broker, today)
        left = broker.cancel_all(include_resting=True)
        if left:
            log.error("%d bot order(s) (incl. resting) would not cancel", len(left))
    condors: dict = {}
    records, orphans, stocks, _ = load_book(broker, today, args.execute, condors_out=condors)
    ctx = Ctx(broker, today)
    sessions = signals.sessions_from_bars([], today)
    jobs, rows = plan_exits(ctx, records, sessions, end_of_day=True, force_all=True, mode="close_all")
    for r in rows:
        log.info("close-all %-26s x%s mid %s nat %s", r["id"], r["qty"], _fmt(r["mid_debit"]), _fmt(r["natural_debit"]))
    cjobs, crows = plan_condor_exits(ctx, condors, sessions, mode="close_all", force_all=True)
    for r in crows:
        log.info("close-all condor %-34s x%s mid %s nat %s", r["id"], r["qty"], _fmt(r["mid_debit"]),
                 _fmt(r["natural_debit"]))
    jobs += cjobs
    for o in orphans:
        log.info("close-all unpaired leg %s unmatched qty %s", o.get("symbol"), o.get("unmatched_qty", o.get("qty")))
    for p in stocks:
        log.info("close-all stock %s qty %s (assignment)", p["symbol"], p["qty"])
    if not args.execute:
        log.info("DRY RUN: %d spread(s)/condor(s), %d unpaired leg(s) and %d stock position(s) would be closed",
                 len(jobs), len(orphans), len(stocks))
        return 0
    resilience.start_inflight(jobs, "close_all", today)
    try:
        execution.work_orders(broker, jobs, cutoff=now + dt.timedelta(minutes=3),
                              hard_stop=now + dt.timedelta(minutes=5), step_wait=30)
    finally:
        _settle(broker, jobs, records, today, None, condors=condors)
    if orphans:
        quotes = broker.option_quotes([o["symbol"] for o in orphans])
        for o in orphans:
            q = quotes.get(o["symbol"])
            uq = orphan_qty(o)
            qty = abs(uq)
            if not q or qty == 0 or not q.quoted:
                log.error("unpaired %s: no quote, close it manually", o.get("symbol"))
                continue
            short = uq < 0
            px = q.ask + C.EXIT_CUTOFF_EXTRA if short else max(q.bid - C.EXIT_CUTOFF_EXTRA, C.TICK)
            try:
                broker.submit_single(o["symbol"], qty, "buy" if short else "sell",
                                     "buy_to_close" if short else "sell_to_close", px,
                                     client_order_id_for("flat", o["symbol"]))
            except Exception as e:              # noqa: BLE001
                log.error("unpaired %s: close failed: %s", o.get("symbol"), e)
    flatten_stock(broker, stocks, today)
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
        line("trading keys", True, "OPT_APCA_* present, paper flag true, paper URL, not the shorting bot's key")
    except C.ConfigError as e:
        line("trading keys", None, f"{e} (dry runs still work)")
    ident = C.shorting_identity()
    line("shorting acct", True if ident["account_ids"] else None,
         "shorting account id configured for the same-account check" if ident["account_ids"] else
         "set SHORTING_ACCOUNT_ID (or _NUMBER) in Options Bot/.env so the bot can refuse the shorting account "
         "(the key check is always on)")
    try:
        b = Broker(execute=False, env=env)
        line("data keys", True, f"source: {b.data_creds.source}")
    except Exception as e:                      # noqa: BLE001
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
    except Exception as e:                      # noqa: BLE001
        line("market data", False, str(e))
    v = signals.vix_levels(today)
    line("VIX", v["vix"] is not None and v["vix3m"] is not None,
         f"{v['vix']} / {v['vix3m']} ({v['source']}){'' if v['fresh'] else ' NOT FRESH: entries would be blocked'}")
    line("supabase", True if db.enabled() else None, "enabled" if db.enabled() else "not configured (writes are no-ops)")
    rec = state.load_positions()
    line("state", True, f"{len(rec)} stored spreads in {C.STATE_DIR}")
    line("condor sleeve", True, f"{'ENABLED' if C.CONDOR_ENABLED else 'disabled (CONDOR_ENABLED = False)'}; "
                                f"{len(state.load_condors())} stored condor(s)")
    topic, _server = C.ntfy_settings()
    line("alerts", True if topic else None,
         f"alerts rows {'on' if C.ALERTS_ENABLED else 'OFF'}; ntfy push "
         + ("on (topic found)" if topic and C.ALERTS_PUSH else "off (no NTFY_TOPIC in env, Options .env or Shorting .env)")
         + f"; {len(alerts.open_alerts())} open local alert(s)")
    inflight = state.load_inflight()
    line("inflight", None if inflight else True,
         f"journal from the {inflight.get('kind')} run of {inflight.get('date')} is pending: the next executing run "
         f"recovers it" if inflight else "no unfinished run")
    line("resting orders", True, f"STAGE_TP_ORDERS={C.STAGE_TP_ORDERS} REST_UNFILLED_EXPIRY_EXITS="
                                 f"{C.REST_UNFILLED_EXPIRY_EXITS}; {len(state.load_resting())} tracked")
    nxt = S.exdiv_dates(C.CONDOR_UNDERLYING, today, today + dt.timedelta(days=120))
    line("ex-dividend", True, f"guard {'ON' if C.CONDOR_EXDIV_GUARD else 'OFF'}; next expected "
                              f"{C.CONDOR_UNDERLYING} ex-date {nxt[0] if nxt else '-'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="send orders (paper options account only)")
    ap.add_argument("--now", action="store_true", help="skip the wait for the decision time")
    ap.add_argument("--check", action="store_true", help="health check")
    ap.add_argument("--close-all", action="store_true", help="emergency: close every spread")
    ap.add_argument("--intraday", action="store_true", help="take-profit / stop checks only")
    ap.add_argument("--sweep", action="store_true",
                    help="expiry-day morning sweep: close spreads expiring today, flatten assigned stock")
    ap.add_argument("--force", action="store_true", help="allow a second executed daily run today")
    ap.add_argument("--symbols", help="comma-separated override of the universe")
    ap.add_argument("--preview", action="store_true",
                    help="also pick and price the spread for symbols without a signal (display only)")
    ap.add_argument("--condor", action="store_true",
                    help="force the SPY iron condor sleeve on for this run (config CONDOR_ENABLED is the default)")
    ap.add_argument("--dry-run", action="store_true",
                    help="no orders AND no Supabase / alert writes (implies no --execute); safe next to the live bot")
    args = ap.parse_args(argv)
    args._lock = None
    setup_logging()
    if args.dry_run:
        if args.execute:
            log.error("--dry-run and --execute are exclusive")
            return 2
        db.disable()
        C.ALERTS_ENABLED = False
        log.info("--dry-run: no orders, no Supabase writes, no alerts")
    if args.check:
        return health()
    if not args.execute:
        C.ALERTS_ENABLED = False                # alerts come from executing runs only
    kind = "close-all" if args.close_all else "sweep" if args.sweep else "intraday" if args.intraday else "daily"
    try:
        if args.close_all:
            rc = run_close_all(args)
        elif args.sweep:
            rc = run_intraday(args, "sweep")
        elif args.intraday:
            rc = run_intraday(args, "intraday")
        else:
            rc = run_daily(args)
        if rc == 0:                             # this kind of run works again: clear its one-off alerts
            for k in ("run_failed", "refused", "lock_busy"):
                alerts.resolve_one(k, kind)
        return rc
    except LockBusy as e:
        lvl = "warning" if kind == "intraday" else "error"
        log.error("%s run not started: %s", kind, e)
        db.heartbeat(lvl, f"{now_et():%Y-%m-%d %H:%M} {kind} run skipped: {e}")
        alerts.raise_alert("lock_busy", f"{kind} run not started: {e}", ticker=kind,
                           level="warning" if kind == "intraday" else "critical")
        return 1
    except (C.ConfigError, SafetyError) as e:
        log.error("REFUSED: %s", e)
        db.heartbeat("error", f"{kind} refused: {e}")
        alerts.raise_alert("refused", f"{kind} refused: {e}", ticker=kind, level="critical")
        return 2
    except Exception as e:                      # noqa: BLE001
        log.error("run failed: %s\n%s", e, traceback.format_exc())
        db.heartbeat("error", f"{kind} run failed: {e}")
        alerts.raise_alert("run_failed", f"{kind} run failed: {type(e).__name__}: {str(e)[:300]}", ticker=kind,
                           level="warning" if kind == "intraday" else "critical")    # the next check retries
        return 1
    finally:
        if args._lock:
            args._lock.close()


if __name__ == "__main__":
    sys.exit(main())
