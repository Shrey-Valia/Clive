"""Working multi-leg limit orders: submit, re-price on a ladder, cancel at the cutoff.

All jobs step together so three underlyings plus exits fit in the
15:50-15:58 window. The broker and the clock are injected, so tests drive the
whole lifecycle with fakes.

Prices in a job are *per-share net* amounts, positive numbers: a credit for
an opening credit spread, a debit for a closing one. They are converted to
Alpaca's mleg sign convention (negative = credit) at submission.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from . import config as C

log = logging.getLogger("optionslive")
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced", "stopped", "suspended"}


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
    order_id: str | None = None
    step: int = 0
    used_cutoff: bool = False
    retired_qty: float = 0.0          # filled on orders we moved away from
    retired_value: float = 0.0
    cur_filled: float = 0.0
    cur_avg: float = 0.0
    status: str = "pending"           # pending | working | filled | partial | unfilled | error | skipped
    error: str | None = None
    history: list[dict] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)

    @property
    def filled_qty(self) -> float:
        return self.retired_qty + self.cur_filled

    @property
    def avg_price(self) -> float | None:
        q = self.filled_qty
        if q <= 0:
            return None
        return round((self.retired_value + self.cur_filled * self.cur_avg) / q, 4)

    @property
    def remaining(self) -> int:
        return int(round(self.qty - self.filled_qty))

    def signed(self, price: float) -> float:
        return -abs(price) if self.credit else abs(price)


def _note(job: Job, clock: Clock, action: str, **kw):
    job.history.append({"t": clock.now().isoformat(), "action": action, **kw})


def _absorb(job: Job, order: dict) -> str:
    """Update fill state from an order payload; returns its status."""
    st = str(order.get("status", "")).lower()
    job.cur_filled = float(order.get("filled_qty") or 0)
    if order.get("filled_avg_price") not in (None, ""):
        job.cur_avg = abs(float(order["filled_avg_price"]))
    return st


def _retire(job: Job) -> None:
    job.retired_qty += job.cur_filled
    job.retired_value += job.cur_filled * job.cur_avg
    job.cur_filled, job.cur_avg = 0.0, 0.0


def _submit(broker, job: Job, price: float, clock: Clock) -> None:
    n = job.remaining
    if n <= 0:
        return
    cid = f"{C.BOT_ID}-{job.purpose}-{job.key}-{len(job.order_ids)}-{int(clock.now().timestamp())}"
    o = broker.submit_mleg(job.legs, n, job.signed(price), cid.replace("/", "_"))
    job.order_id = o["id"]
    job.order_ids.append(o["id"])
    job.status = "working"
    _absorb(job, o)
    _note(job, clock, "submit", price=price, qty=n, order_id=o["id"])


def _cancel_and_settle(broker, job: Job, clock: Clock) -> str:
    """Cancel the working order and read its final state (it may have filled)."""
    try:
        broker.cancel_order(job.order_id)
    except Exception as e:                      # already filled / not cancelable yet
        _note(job, clock, "cancel_failed", error=str(e)[:200])
    st = "unknown"
    for _ in range(5):
        try:
            st = _absorb(job, broker.get_order(job.order_id))
        except Exception as e:
            _note(job, clock, "poll_failed", error=str(e)[:200])
        if st in TERMINAL:
            break
        clock.sleep(1)
    _note(job, clock, "canceled", status=st, filled=job.filled_qty)
    return st


def _reprice(broker, job: Job, price: float, clock: Clock) -> None:
    try:
        o = broker.replace_order(job.order_id, job.signed(price))
        _retire(job)                            # old order's fills stay counted
        job.order_id = o["id"]
        job.order_ids.append(o["id"])
        _absorb(job, o)
        _note(job, clock, "replace", price=price, order_id=o["id"])
        return
    except Exception as e:
        _note(job, clock, "replace_failed", error=str(e)[:200])
    st = _cancel_and_settle(broker, job, clock)
    if st == "filled":
        return
    if st not in TERMINAL:                      # could not cancel: leave it working, retry next step
        return
    _retire(job)
    _submit(broker, job, price, clock)


def _poll(broker, jobs: list[Job], clock: Clock) -> None:
    for j in jobs:
        if j.status != "working" or not j.order_id:
            continue
        try:
            st = _absorb(j, broker.get_order(j.order_id))
        except Exception as e:
            _note(j, clock, "poll_failed", error=str(e)[:200])
            continue
        if j.remaining <= 0:
            j.status = "filled"
            _note(j, clock, "filled", avg=j.avg_price, qty=j.filled_qty)
        elif st in TERMINAL and st != "replaced":
            _retire(j)
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


def work_orders(broker, jobs: list[Job], cutoff: dt.datetime, hard_stop: dt.datetime | None = None,
                clock: Clock | None = None, step_wait: float | None = None) -> list[Job]:
    """Run every job to completion or the cutoff.

    * Each job starts at prices[0] and steps through its ladder, one step per
      ``step_wait`` seconds (shrunk so the whole ladder fits before ``cutoff``).
    * When every ladder is exhausted (last price worked for a full step) or
      the cutoff arrives, whichever is first, jobs with a ``cutoff_price`` (exits) are re-priced to it
      and left until ``hard_stop``; everything else is canceled.
    """
    clock = clock or Clock()
    hard_stop = hard_stop or cutoff
    now = clock.now()
    if now >= cutoff:
        for j in jobs:
            j.status, j.error = "skipped", "past cutoff"
        return jobs
    steps = max((len(j.prices) for j in jobs), default=1)
    budget = (cutoff - now).total_seconds() / max(steps, 1)
    step_wait = min(step_wait or C.STEP_WAIT_SECONDS, budget)
    for j in jobs:
        try:
            _submit(broker, j, j.prices[0], clock)
        except Exception as e:
            j.status, j.error = "error", str(e)[:300]
            log.error("%s %s: submit failed: %s", j.purpose, j.key, j.error)
    t_next = clock.now() + dt.timedelta(seconds=step_wait)
    while True:
        _wait(broker, jobs, min(t_next, cutoff), clock)
        live = [j for j in jobs if j.status == "working"]
        if not live:
            break
        if clock.now() >= cutoff:
            break
        advanced = False
        for j in live:
            if j.step + 1 < len(j.prices):
                j.step += 1
                advanced = True
                _reprice(broker, j, j.prices[j.step], clock)
        if not advanced:                        # every ladder exhausted and its last price had a full step
            break
        t_next = clock.now() + dt.timedelta(seconds=step_wait)
    # ---- cutoff
    live = [j for j in jobs if j.status == "working"]
    fallback = [j for j in live if j.cutoff_price is not None]
    for j in live:
        if j in fallback:
            j.used_cutoff = True
            _reprice(broker, j, j.cutoff_price, clock)
            log.warning("%s %s: cutoff, aggressive price %.2f", j.purpose, j.key, j.cutoff_price)
        else:
            _cancel_and_settle(broker, j, clock)
            _finish(j)
    if fallback:
        _wait(broker, fallback, hard_stop, clock)
        for j in fallback:
            if j.status == "working":
                _cancel_and_settle(broker, j, clock)
                _finish(j)
    return jobs


def _finish(j: Job) -> None:
    if j.remaining <= 0:
        j.status = "filled"
    else:
        j.status = "partial" if j.filled_qty > 0 else "unfilled"


# ---------------------------------------------------------------- leg builders
def open_legs(short_symbol: str, long_symbol: str) -> list[dict]:
    return [{"symbol": short_symbol, "side": "sell", "position_intent": "sell_to_open", "ratio_qty": 1},
            {"symbol": long_symbol, "side": "buy", "position_intent": "buy_to_open", "ratio_qty": 1}]


def close_legs(short_symbol: str, long_symbol: str) -> list[dict]:
    return [{"symbol": short_symbol, "side": "buy", "position_intent": "buy_to_close", "ratio_qty": 1},
            {"symbol": long_symbol, "side": "sell", "position_intent": "sell_to_close", "ratio_qty": 1}]
