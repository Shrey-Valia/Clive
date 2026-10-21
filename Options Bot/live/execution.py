"""Working multi-leg limit orders: submit, re-price on a ladder, cancel at the cutoff.

All jobs step together so three underlyings plus exits fit in the decision
window (decision time to cutoff). The broker and the clock are injected, so
tests drive the whole lifecycle with fakes.

Prices in a job are *per-share net* amounts, positive numbers: a credit for
an opening credit spread, a debit for a closing one. They are converted to
Alpaca's mleg sign convention (negative = credit) at submission.

Order-state rules (REVIEW_orders.md #2, #3, #4, #10, #12, #14)
--------------------------------------------------------------
* Fills are kept per order id (``Job.fills``) and the job's filled quantity is
  the sum over every order id it ever had, so a replace that races a fill can
  never lose or double count a fill.
* A working order is re-read (GET) before every re-price. A partially filled
  order is never replaced: it is canceled, settled, and the remainder is
  resubmitted.
* After every replace attempt (successful or not) the old order is re-read:
  ``replaced`` -> follow ``replaced_by``; anything else means the replace did
  not take, and any new order the response named is canceled and settled.
* ``replaced`` is not terminal. Only filled / canceled / expired / rejected /
  done_for_day are final; ``stopped`` / ``suspended`` keep being polled.
* After any submit exception the order is looked up by client_order_id, so a
  timed-out submit that did reach Alpaca is adopted, not duplicated.
* Cancels retry with backoff and are confirmed by polling.
* ``work_orders`` always ends with a sweep (in a ``finally``): every working
  order is canceled and settled, every order id is re-read to recompute
  fills, and any open order carrying one of the jobs' client_order_ids is
  canceled.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from . import config as C
from .broker import client_order_id

log = logging.getLogger("optionslive")
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day"}


class Clock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def sleep(self, s: float) -> None:
        time.sleep(max(s, 0))


@dataclass
class Job:
    key: str                          # spread id
    purpose: str                      # "open" | "close"
    legs: list[dict]                  # [{symbol, side, position_intent, ratio_qty}]
    qty: int
    prices: list[float]               # ladder, positive per-share net amounts
    credit: bool                      # True: prices are credits received
    cutoff_price: float | None = None # exits: aggressive price used at the cutoff
    meta: dict = field(default_factory=dict)
    # ---- runtime
    order_id: str | None = None       # the order currently working (None when settled)
    step: int = 0
    used_cutoff: bool = False
    status: str = "pending"           # pending | working | filled | partial | unfilled | error | skipped
    error: str | None = None
    history: list[dict] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)
    cids: list[str] = field(default_factory=list)
    fills: dict = field(default_factory=dict)      # order id -> (filled qty, avg per-share net price)
    statuses: dict = field(default_factory=dict)   # order id -> last seen status

    @property
    def filled_qty(self) -> float:
        return float(sum(q for q, _ in self.fills.values()))

    @property
    def avg_price(self) -> float | None:
        q = self.filled_qty
        if q <= 0:
            return None
        return round(sum(fq * px for fq, px in self.fills.values()) / q, 4)

    @property
    def remaining(self) -> int:
        return int(round(self.qty - self.filled_qty))

    def signed(self, price: float) -> float:
        return -abs(price) if self.credit else abs(price)


def _note(job: Job, clock: Clock, action: str, **kw):
    job.history.append({"t": clock.now().isoformat(), "action": action, **kw})


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _net_from_legs(job: Job, order: dict) -> float | None:
    """Per-spread net price from the legs' fill prices (credit: sells - buys)."""
    legs = order.get("legs") or []
    tot = 0.0
    for lg in legs:
        px = _f(lg.get("filled_avg_price"))
        if px is None:
            return None
        r = _f(lg.get("ratio_qty")) or 1.0
        tot += (px if lg.get("side") == "sell" else -px) * r
    if not legs:
        return None
    return abs(tot) if job.credit else abs(-tot)


def _absorb(job: Job, order: dict, clock: Clock | None = None) -> str:
    """Record this order's fills (keyed by its id) and return its status."""
    oid = order.get("id")
    st = str(order.get("status", "")).lower()
    if not oid:
        return st
    if oid not in job.order_ids:
        job.order_ids.append(oid)
    job.statuses[oid] = st
    q = _f(order.get("filled_qty")) or 0.0
    if q > 0:
        px = _f(order.get("filled_avg_price"))
        if px is None:
            px = _net_from_legs(job, order)
            src = "legs"
            if px is None:
                px = abs(_f(order.get("limit_price")) or 0.0)
                src = "limit price"
            log.error("%s %s: order %s filled %s with no parent filled_avg_price; using the %s (%.2f)",
                      job.purpose, job.key, oid, q, src, px)
            if clock:
                _note(job, clock, "avg_price_fallback", order_id=oid, source=src, price=px)
        job.fills[oid] = (q, abs(px))
    else:
        job.fills.pop(oid, None)
    return st


def _lookup_cid(broker, cid: str):
    fn = getattr(broker, "get_order_by_client_id", None)
    if fn is None:
        return None
    try:
        return fn(cid)
    except Exception as e:                      # noqa: BLE001
        log.error("lookup of client_order_id %s failed: %s", cid, e)
        return None


def _submit(broker, job: Job, price: float, clock: Clock) -> None:
    """Submit the remaining quantity at ``price``. After an exception the order
    is looked up by its client_order_id; it is adopted if it exists."""
    n = job.remaining
    if n <= 0:
        return
    cid = client_order_id(job.purpose, job.key, len(job.cids))
    job.cids.append(cid)
    try:
        o = broker.submit_mleg(job.legs, n, job.signed(price), cid)
    except Exception as e:                      # noqa: BLE001
        _note(job, clock, "submit_failed", error=str(e)[:200], client_order_id=cid)
        o = _lookup_cid(broker, cid)
        if not o:
            raise
        log.warning("%s %s: submit raised (%s) but order %s exists; adopted", job.purpose, job.key, e, o.get("id"))
    job.order_id = o["id"]
    job.status = "working"
    _absorb(job, o, clock)
    _note(job, clock, "submit", price=price, qty=n, order_id=o["id"], client_order_id=cid)


def _get(broker, job: Job, oid: str, clock: Clock):
    try:
        o = broker.get_order(oid)
    except Exception as e:                      # noqa: BLE001
        _note(job, clock, "poll_failed", order_id=oid, error=str(e)[:200])
        return None, "unknown"
    return o, _absorb(job, o, clock)


def _follow(job: Job, order: dict, clock: Clock) -> bool:
    """If ``order`` was replaced, make its replacement the working order."""
    nid = order.get("replaced_by")
    if not nid:
        return False
    job.order_id = nid
    if nid not in job.order_ids:
        job.order_ids.append(nid)
    _note(job, clock, "follow_replaced_by", old=order.get("id"), order_id=nid)
    return True


def _cancel_and_settle(broker, job: Job, clock: Clock, polls: int = 6) -> str:
    """Cancel the working order (retrying with backoff), then poll until it is
    final, following ``replaced_by`` links. Fills it got are recorded. When it
    is final the job has no working order; otherwise ``order_id`` is kept so a
    later sweep can retry."""
    st = "unknown"
    for _hop in range(4):                       # replaced_by chain
        oid = job.order_id
        if not oid:
            return st
        for attempt in range(3):
            try:
                broker.cancel_order(oid)
                break
            except Exception as e:              # noqa: BLE001  (already filled / not cancelable yet)
                _note(job, clock, "cancel_failed", order_id=oid, attempt=attempt, error=str(e)[:200])
                clock.sleep(0.5 * 2 ** attempt)
        o = None
        for _ in range(polls):
            o, st = _get(broker, job, oid, clock)
            if st in TERMINAL or st == "replaced":
                break
            clock.sleep(1)
        if st == "replaced" and o is not None and _follow(job, o, clock):
            continue
        break
    if st in TERMINAL or st == "replaced":
        job.order_id = None
        _note(job, clock, "canceled", status=st, filled=job.filled_qty)
    else:
        _note(job, clock, "cancel_unconfirmed", status=st, order_id=job.order_id)
        log.error("%s %s: order %s not confirmed canceled (status %s)", job.purpose, job.key, job.order_id, st)
    return st


def _resubmit(broker, job: Job, price: float, clock: Clock) -> None:
    if job.order_id is None and job.remaining > 0:
        _submit(broker, job, price, clock)
    if job.order_id is None:
        _finish(job)


def _reprice(broker, job: Job, price: float, clock: Clock) -> None:
    """Move the working order to ``price``, safely."""
    old = job.order_id
    if not old:
        return
    o, st = _get(broker, job, old, clock)
    if o is None:
        return                                  # unknown state: leave it, retry next step
    if st == "replaced":
        _follow(job, o, clock)
        return
    if st in TERMINAL or job.remaining <= 0:
        return                                  # the poll marks it filled/ended
    if (job.fills.get(old) or (0, 0))[0] > 0 or st == "partially_filled":
        # never replace a partially filled order: cancel, settle, resubmit the rest
        _note(job, clock, "partial_cancel_resubmit", order_id=old, filled=job.filled_qty)
        st = _cancel_and_settle(broker, job, clock)
        if job.order_id is None:
            _resubmit(broker, job, price, clock)
        return
    new = None
    try:
        new = broker.replace_order(old, job.signed(price))
    except Exception as e:                      # noqa: BLE001
        _note(job, clock, "replace_failed", order_id=old, error=str(e)[:200])
    oo, st_old = _get(broker, job, old, clock)  # always re-read the old order
    if st_old == "replaced":
        nid = (oo or {}).get("replaced_by") or (new or {}).get("id")
        if nid:
            job.order_id = nid
            if nid not in job.order_ids:
                job.order_ids.append(nid)
            if new and new.get("id") == nid:
                _absorb(job, new, clock)
            _note(job, clock, "replace", price=price, order_id=nid, old=old)
            return
    if new is not None and new.get("id") and new.get("id") != old:
        # the response named a new order but the old one was not replaced (it filled
        # or is still pending): cancel the new one so the two cannot both fill
        _note(job, clock, "replace_race", old=old, old_status=st_old, order_id=new["id"])
        log.warning("%s %s: replace of %s did not take (old status %s); canceling %s",
                    job.purpose, job.key, old, st_old, new["id"])
        _absorb(job, new, clock)
        job.order_id = new["id"]
        _cancel_and_settle(broker, job, clock)
        job.order_id = None if st_old in TERMINAL else old
    if job.remaining <= 0:
        job.order_id = None if st_old in TERMINAL else job.order_id
        if job.order_id is not None:
            _cancel_and_settle(broker, job, clock)
        _finish(job)
        return
    if job.order_id is not None and st_old not in TERMINAL:
        _cancel_and_settle(broker, job, clock)  # replace failed: cancel, confirm, resubmit
        if job.order_id is not None:
            return                              # could not cancel: leave it working, retry next step
    else:
        job.order_id = None
    _resubmit(broker, job, price, clock)


def _poll(broker, jobs: list[Job], clock: Clock) -> None:
    for j in jobs:
        if j.status != "working" or not j.order_id:
            continue
        o, st = _get(broker, j, j.order_id, clock)
        if o is None:
            continue
        if st == "replaced":
            _follow(j, o, clock)
            continue
        if j.remaining <= 0:
            if st not in TERMINAL:              # overfill guard: nothing more should work
                _cancel_and_settle(broker, j, clock)
            j.order_id = None
            j.status = "filled"
            _note(j, clock, "filled", avg=j.avg_price, qty=j.filled_qty)
        elif st in TERMINAL:
            j.order_id = None
            j.status = "partial" if j.filled_qty > 0 else "unfilled"
            _note(j, clock, "ended", status=st)


def _wait(broker, jobs, until: dt.datetime, clock: Clock) -> None:
    while True:
        _poll(broker, jobs, clock)
        live = [j for j in jobs if j.status == "working"]
        if not live:
            return
        left = (until - clock.now()).total_seconds()
        if left <= 0:
            return
        clock.sleep(min(C.POLL_SECONDS, left))


def _safe(job: Job, clock: Clock, what: str, fn, *a) -> None:
    try:
        fn(*a)
    except Exception as e:                      # noqa: BLE001
        job.error = f"{what}: {str(e)[:250]}"
        _note(job, clock, f"{what}_error", error=str(e)[:250])
        log.error("%s %s: %s failed: %s", job.purpose, job.key, what, e)
        if job.order_id is None and job.status in ("working", "pending"):
            job.status = "error" if job.filled_qty <= 0 else "partial"


def work_orders(broker, jobs: list[Job], cutoff: dt.datetime, hard_stop: dt.datetime | None = None,
                clock: Clock | None = None, step_wait: float | None = None) -> list[Job]:
    """Run every job to completion or the cutoff.

    * Each job starts at prices[0] and steps through its ladder, one step per
      ``step_wait`` seconds (shrunk so the whole ladder fits before ``cutoff``).
    * When every ladder is exhausted (last price worked for a full step) or
      the cutoff arrives, whichever is first, jobs with a ``cutoff_price`` (exits)
      are re-priced to it and left until ``hard_stop``; everything else is canceled.
    * Whatever happens (including an exception), the final sweep runs.
    """
    clock = clock or Clock()
    hard_stop = hard_stop or cutoff
    now = clock.now()
    if now >= cutoff:
        for j in jobs:
            j.status, j.error = "skipped", "past cutoff"
        return jobs
    try:
        steps = max((len(j.prices) for j in jobs), default=1)
        budget = (cutoff - now).total_seconds() / max(steps, 1)
        step_wait = min(step_wait or C.STEP_WAIT_SECONDS, budget)
        for j in jobs:
            try:
                _submit(broker, j, j.prices[0], clock)
            except Exception as e:              # noqa: BLE001
                j.status, j.error = "error", str(e)[:300]
                log.error("%s %s: submit failed: %s", j.purpose, j.key, j.error)
        t_next = clock.now() + dt.timedelta(seconds=step_wait)
        while True:
            _wait(broker, jobs, min(t_next, cutoff), clock)
            live = [j for j in jobs if j.status == "working"]
            if not live or clock.now() >= cutoff:
                break
            advanced = False
            for j in live:
                if j.step + 1 < len(j.prices):
                    j.step += 1
                    advanced = True
                    _safe(j, clock, "reprice", _reprice, broker, j, j.prices[j.step], clock)
            if not advanced:                    # every ladder exhausted and its last price had a full step
                break
            t_next = clock.now() + dt.timedelta(seconds=step_wait)
        # ---- cutoff
        live = [j for j in jobs if j.status == "working"]
        fallback = [j for j in live if j.cutoff_price is not None]
        for j in live:
            if j in fallback:
                j.used_cutoff = True
                log.warning("%s %s: cutoff, aggressive price %.2f", j.purpose, j.key, j.cutoff_price)
                _safe(j, clock, "reprice", _reprice, broker, j, j.cutoff_price, clock)
            else:
                _safe(j, clock, "cancel", _cancel_and_settle, broker, j, clock)
                if j.order_id is None:
                    _finish(j)
        if fallback:
            _wait(broker, fallback, hard_stop, clock)
    finally:
        final_sweep(broker, jobs, clock)
    return jobs


def final_sweep(broker, jobs: list[Job], clock: Clock) -> None:
    """Cancel and settle anything still working, recompute fills from every
    order id, and cancel any open order carrying one of the jobs' client ids."""
    for j in jobs:
        if j.order_id:
            try:
                _cancel_and_settle(broker, j, clock)
            except Exception as e:              # noqa: BLE001
                log.error("%s %s: final cancel failed: %s", j.purpose, j.key, e)
    for j in jobs:                              # fills from every id, not from the last poll
        for oid in list(j.order_ids):
            if j.statuses.get(oid) in TERMINAL:
                continue
            o, st = _get(broker, j, oid, clock)
            if o is not None and st not in TERMINAL and st != "replaced":
                j.order_id = oid
                try:
                    _cancel_and_settle(broker, j, clock)
                except Exception as e:          # noqa: BLE001
                    log.error("%s %s: sweep cancel of %s failed: %s", j.purpose, j.key, oid, e)
    lister = getattr(broker, "open_orders", None)
    if lister is not None:
        try:
            open_ = lister() or []
        except Exception as e:                  # noqa: BLE001
            log.error("final sweep: open orders unavailable: %s", e)
            open_ = []
        by_cid = {c: j for j in jobs for c in j.cids}
        by_id = {o: j for j in jobs for o in j.order_ids}
        for o in open_:
            j = by_cid.get(o.get("client_order_id")) or by_id.get(o.get("id"))
            if j is None:
                continue
            log.error("final sweep: %s %s order %s still open; canceling", j.purpose, j.key, o.get("id"))
            _absorb(j, o, clock)
            j.order_id = o.get("id")
            try:
                _cancel_and_settle(broker, j, clock)
            except Exception as e:              # noqa: BLE001
                log.error("final sweep cancel failed: %s", e)
    for j in jobs:
        if j.status in ("working", "pending") or (j.status == "error" and j.filled_qty > 0):
            if j.order_id is None:
                _finish(j)
        elif j.status in ("partial", "unfilled", "filled"):
            _finish(j)


def _finish(j: Job) -> None:
    if j.status == "skipped":
        return
    if j.remaining <= 0 and j.filled_qty > 0:
        j.status = "filled"
    elif j.filled_qty > 0:
        j.status = "partial"
    elif j.status != "error":
        j.status = "unfilled"


# ---------------------------------------------------------------- leg builders
def open_legs(short_symbol: str, long_symbol: str) -> list[dict]:
    return [{"symbol": short_symbol, "side": "sell", "position_intent": "sell_to_open", "ratio_qty": 1},
            {"symbol": long_symbol, "side": "buy", "position_intent": "buy_to_open", "ratio_qty": 1}]


def close_legs(short_symbol: str, long_symbol: str) -> list[dict]:
    return [{"symbol": short_symbol, "side": "buy", "position_intent": "buy_to_close", "ratio_qty": 1},
            {"symbol": long_symbol, "side": "sell", "position_intent": "sell_to_close", "ratio_qty": 1}]
