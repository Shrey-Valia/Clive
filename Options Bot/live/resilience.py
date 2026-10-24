"""Host-death resilience (docs/ENGINEERING_ROUND3.md).

What a dead host can leave behind, and what this module does about it:

* Half a spread: impossible by construction. Every spread / condor order is ONE Alpaca mleg
  order, which fills as a unit ("ensures they fill together or not at all", Alpaca, Options
  Level 3 Trading); partial fills are in whole strategy units. The bot never legs in or out.
* A DAY order still working: it keeps working at Alpaca until it fills or the session ends. The
  next run's leftover cancel (``Broker.cancel_all``) removes it.
* Fills nobody booked: the write-ahead inflight journal (``state/inflight.json``) holds every
  job's client_order_ids and planned metadata BEFORE its first submit. ``recover`` turns a
  journal left by a dead run into settled ``execution.Job``s (orders looked up by
  client_order_id, ``replaced_by`` chains followed, anything still open canceled and confirmed),
  which run.py books with the entry mid / signal / exit reason the dead run planned.
* Exits that depend on this host: optional resting GTC mleg limit orders (``stage_resting``):
  a take-profit staged after entry (STAGE_TP_ORDERS) and an unfilled expiry exit left working
  overnight (REST_UNFILLED_EXPIRY_EXITS). Alpaca allows only market/limit for mleg (no stop), so
  a stop can never rest at the broker for a spread.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from . import config as C
from . import execution, state
from . import strategy as S
from .broker import BrokerError, client_order_id, is_resting_order

log = logging.getLogger("optionslive")
_META_KEYS = ("template", "record", "reason", "mid", "natural", "expiry", "tdte", "signal")


# ================================================================ inflight journal
def _job_entry(j: execution.Job) -> dict:
    return {"key": j.key, "purpose": j.purpose, "sleeve": j.sleeve, "qty": j.qty, "credit": j.credit,
            "prices": j.prices, "cutoff_price": j.cutoff_price, "legs": j.legs, "cids": list(j.cids),
            "meta": {k: j.meta[k] for k in _META_KEYS if k in j.meta}}


def start_inflight(jobs: list[execution.Job], kind: str, today: dt.date) -> None:
    """Journal every job (no cids yet) and hook each submit, so the journal always names every
    client_order_id that could exist at Alpaca. No-op when RECOVER_INFLIGHT is off."""
    if not C.RECOVER_INFLIGHT or not jobs:
        return
    rec = {"kind": kind, "date": today.isoformat(),
           "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "jobs": [_job_entry(j) for j in jobs]}
    state.save_inflight(rec)

    def hook(job, cid):
        for e in rec["jobs"]:
            if e["key"] == job.key and e["purpose"] == job.purpose and e["sleeve"] == job.sleeve:
                if cid not in e["cids"]:
                    e["cids"].append(cid)
        state.save_inflight(rec)
    for j in jobs:
        j.on_submit = hook


def finish_inflight() -> None:
    """Every fill of this run is booked and saved: the journal is no longer needed."""
    try:
        state.clear_inflight()
    except OSError as e:
        log.error("could not clear the inflight journal: %s", e)


def recover(broker, clock: execution.Clock | None = None) -> tuple[list[execution.Job], dict | None, list[str]]:
    """(settled jobs, journal, problems) from a journal left by a run that never finished.

    Every client_order_id is looked up; ``replaced_by`` links are followed; any order still open
    is canceled and confirmed (it is the bot's own: client_order_id 'options-...'). Jobs are
    returned with their fills, ready for run.py to book. ``problems`` lists what could not be
    resolved: the journal is then kept (run.py does not clear it) so a later run retries."""
    rec = state.load_inflight()
    if not rec or not rec.get("jobs"):
        return [], None, []
    clock = clock or execution.Clock()
    jobs, problems = [], []
    for e in rec["jobs"]:
        j = execution.Job(e["key"], e["purpose"], e.get("legs") or [], int(e["qty"]), e.get("prices") or [0.0],
                          credit=bool(e["credit"]), cutoff_price=e.get("cutoff_price"),
                          meta=dict(e.get("meta") or {}), sleeve=e.get("sleeve") or C.SLEEVE_DIP)
        j.cids = list(e.get("cids") or [])
        for cid in j.cids:
            try:
                o = broker.get_order_by_client_id(cid)
            except Exception as ex:             # noqa: BLE001
                problems.append(f"{j.key}: lookup of {cid} failed: {ex}")
                continue
            if not o:
                continue                        # never reached Alpaca (died before / during the POST)
            seen = set()
            while o is not None and o.get("id") not in seen:
                seen.add(o.get("id"))
                st = execution._absorb(j, o, clock)
                if st not in execution.TERMINAL and st != "replaced":
                    j.order_id = o["id"]
                    execution._cancel_and_settle(broker, j, clock)
                    if j.order_id is not None:
                        problems.append(f"{j.key}: order {o.get('id')} would not cancel")
                    break
                nid = o.get("replaced_by")
                if st != "replaced" or not nid:
                    break
                try:
                    o = broker.get_order(nid)
                except Exception as ex:         # noqa: BLE001
                    problems.append(f"{j.key}: replaced_by {nid} unreadable: {ex}")
                    break
        execution._finish(j)
        jobs.append(j)
    return jobs, rec, problems


# ================================================================ resting GTC orders
def _resting_open(broker) -> list[dict]:
    try:
        return [o for o in (broker.open_orders() or []) if is_resting_order(o)]
    except Exception as e:                      # noqa: BLE001
        log.error("open orders unavailable for the resting-order check: %s", e)
        raise


def resting_in_use() -> bool:
    return bool(C.STAGE_TP_ORDERS or C.REST_UNFILLED_EXPIRY_EXITS or state.load_resting())


def _leg_symbols(o: dict) -> set[str]:
    return {lg.get("symbol") for lg in (o.get("legs") or []) if lg.get("symbol")}


def cancel_resting(broker, clock: execution.Clock | None = None, polls: int = 8) -> tuple[list[dict], set, list[str]]:
    """Cancel every resting order (client_order_id 'options-rest-...') and confirm it is final,
    BEFORE a run reads the book: a resting close holds the position's quantity (the bot's own exit
    would be rejected), and a resting order that filled must be booked before the book is read.

    Returns (settled, stuck, problems):
    * settled: one dict per final resting order {sid (None if unknown), order_id, kind, sleeve,
      symbols, status, filled_qty, filled_avg_price}; run.py books the fills;
    * stuck: spread ids (or client_order_ids) whose resting order is NOT confirmed final: the run
      must not trade them;
    * problems: messages.
    state/resting.json keeps only the stuck ones afterwards."""
    clock = clock or execution.Clock()
    book = state.load_resting()
    by_oid = {v.get("order_id"): k for k, v in book.items() if v.get("order_id")}
    by_cid = {v.get("cid"): k for k, v in book.items() if v.get("cid")}
    problems, settled, stuck = [], [], set()
    try:
        open_ = _resting_open(broker)
    except Exception as e:                      # noqa: BLE001
        return [], set(book) or {"*"}, [f"open orders unavailable: {e}"]
    targets = {o["id"]: o for o in open_}
    for sid, v in book.items():                 # the ones we staged may have filled meanwhile
        oid = v.get("order_id")
        if oid and oid not in targets:
            try:
                o = broker.get_order(oid)
                if o:
                    targets[o["id"]] = o
            except Exception as e:              # noqa: BLE001
                problems.append(f"{sid}: resting order {oid} unreadable: {e}")
                stuck.add(sid)
    for oid, o in targets.items():
        sid = by_oid.get(oid) or by_cid.get(o.get("client_order_id"))
        st = str(o.get("status", "")).lower()
        if st not in execution.TERMINAL:
            try:
                broker.cancel_order(oid)
            except Exception as e:              # noqa: BLE001  (may have just filled)
                log.warning("cancel of resting order %s failed: %s", oid, e)
            for _ in range(polls):
                try:
                    o2 = broker.get_order(oid)
                except Exception:               # noqa: BLE001
                    o2 = None
                if o2 is not None:
                    o = o2
                st = str(o.get("status", "")).lower()
                if st in execution.TERMINAL:
                    break
                clock.sleep(1)
            if st not in execution.TERMINAL:
                problems.append(f"{sid or o.get('client_order_id')}: resting order {oid} not confirmed canceled "
                                f"(status {st or 'unknown'})")
                stuck.add(sid or o.get("client_order_id"))
                continue
        v = book.get(sid) or {}
        settled.append({"sid": sid, "order_id": oid, "kind": v.get("kind"), "sleeve": v.get("sleeve"),
                        "symbols": sorted(_leg_symbols(o)) or v.get("symbols") or [], "status": st,
                        "filled_qty": float(o.get("filled_qty") or 0),
                        "filled_avg_price": o.get("filled_avg_price"), "legs": o.get("legs") or []})
    state.save_resting({k: v for k, v in book.items() if k in stuck})
    return settled, stuck, problems


def _submit_resting(broker, sid: str, sleeve: str, kind: str, legs: list[dict], qty: int, price: float,
                    today: dt.date) -> dict | None:
    cid = client_order_id("rest", kind, sid)
    assert cid.startswith(C.REST_ORDER_PREFIX)
    try:
        o = broker.submit_mleg(legs, qty, abs(price), cid, time_in_force="gtc")    # debit: positive limit
    except Exception as e:                      # noqa: BLE001
        log.error("resting %s order for %s failed: %s", kind, sid, e)
        return None
    log.warning("resting GTC %s close for %s: %dx at %.2f debit (order %s)", kind, sid, qty, price, o.get("id"))
    return {"order_id": o.get("id"), "cid": cid, "kind": kind, "sleeve": sleeve, "qty": qty, "price": price,
            "date": today.isoformat(), "status": str(o.get("status", "new")), "symbols": sorted(l["symbol"] for l in legs)}


def stage_resting(broker, records: dict, condors: dict, today: dt.date, sessions: list[dt.date],
                  unfilled_expiry_jobs: list[execution.Job] | None = None) -> list[str]:
    """Stage the resting GTC orders the flags ask for (after a run settled, book saved).

    * STAGE_TP_ORDERS: every dip spread entered BEFORE today (no same-day take-profit: the
      research never took profit on the entry day, and an entry-day close would be a day trade on
      a sub-$25k account) with more than CLOSE_DTE_TRADING_DAYS sessions to expiry gets a GTC
      close at the research take-profit price (entry mid * (1 - TAKE_PROFIT)).
    * REST_UNFILLED_EXPIRY_EXITS: each expiry-exit job that did not fill rests at its cutoff
      price for its remaining quantity, so it works from the next open even if no run happens.
    Returns messages for the heartbeat."""
    msgs = []
    book = state.load_resting()
    if C.STAGE_TP_ORDERS:
        for sid, r in sorted(records.items()):
            if sid in book:
                continue
            exp = dt.date.fromisoformat(r["expiry"])
            if r.get("entry_date", today.isoformat()) >= today.isoformat():
                continue
            if S.trading_days_until(exp, today, sessions) <= C.CLOSE_DTE_TRADING_DAYS:
                continue
            basis = float(r.get("entry_mid") or r["entry_credit"])
            px = S.take_profit_debit(basis)
            if not (px > 0 and math.isfinite(px)):
                continue
            e = _submit_resting(broker, sid, C.SLEEVE_DIP, "tp",
                                execution.close_legs(r["short_symbol"], r["long_symbol"]), int(r["qty"]), px, today)
            if e:
                book[sid] = e
                msgs.append(f"resting TP {sid} {int(r['qty'])}x @ {px:.2f}")
            else:
                msgs.append(f"RESTING TP FAILED {sid}")
    if C.REST_UNFILLED_EXPIRY_EXITS:
        for j in unfilled_expiry_jobs or []:
            if j.remaining <= 0 or j.key in book or j.cutoff_price is None:
                continue
            held = (condors if j.sleeve == C.SLEEVE_CONDOR else records).get(j.key)
            if not held:
                continue
            qty = min(j.remaining, int(held["qty"]))
            e = _submit_resting(broker, j.key, j.sleeve, "expiry", j.legs, qty, float(j.cutoff_price), today)
            if e:
                book[j.key] = e
                msgs.append(f"resting expiry exit {j.key} {qty}x @ {j.cutoff_price:.2f} (works from the next open)")
            else:
                msgs.append(f"RESTING EXPIRY EXIT FAILED {j.key}")
    state.save_resting(book)
    return msgs


# ================================================================ ex-dividend information
def exdiv_info(broker, symbol: str, today: dt.date, end: dt.date, sessions: list[dt.date]) -> dict:
    """{"dates": expected ex-dates in [today, end], "dividend": $/share, "source"}.
    Dates: the quarterly rule (strategy.exdiv_dates) with any date Alpaca already announced;
    dividend: the latest Alpaca cash dividend in the last 200 days, else DIVIDEND_ESTIMATE."""
    known, rate, src = [], None, "rule"
    fn = getattr(broker, "cash_dividends", None)
    if fn is not None:
        try:
            rows = fn(symbol, today - dt.timedelta(days=200), end)
            known = [r["ex_date"] for r in rows if r["ex_date"] >= today]
            past = [r for r in rows if r["ex_date"] < today]
            if past:
                rate, src = past[-1]["rate"], f"alpaca {past[-1]['ex_date']}"
        except (BrokerError, Exception) as e:   # noqa: BLE001
            log.warning("dividends for %s unavailable: %s (using the quarterly rule)", symbol, e)
    div = max(rate or 0.0, C.DIVIDEND_ESTIMATE.get(symbol, 0.0))     # the larger: conservative
    return {"dates": S.exdiv_dates(symbol, today, end, sessions, known=known), "dividend": div, "source": src}
