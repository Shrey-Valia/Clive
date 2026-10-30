"""Local state: open-spread metadata, trade/order journals, the once-per-day guard.

The broker is the source of truth for what is held. This file only adds what
the broker does not know: entry date, entry credit (fill), and the signal
that opened the spread. Written only in --execute mode.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from . import config as C

log = logging.getLogger("optionslive")

POSITIONS = "positions.json"
CONDORS = "condors.json"        # condor sleeve records (sleeve "condor"), kept apart from the dip spreads
TRADES = "trades.jsonl"
ORDERS = "orders.jsonl"
GUARD = "last_execute.json"
INFLIGHT = "inflight.json"      # write-ahead journal of orders a run is working (cleared when booked)
RESTING = "resting.json"        # resting GTC orders (take-profit / overnight expiry exit) by spread id


def _dir(d: Path | None) -> Path:
    d = Path(d or C.STATE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_positions(d: Path | None = None) -> dict:
    f = _dir(d) / POSITIONS
    return json.loads(f.read_text()) if f.exists() else {}


def save_positions(pos: dict, d: Path | None = None) -> None:
    _atomic(_dir(d) / POSITIONS, json.dumps(pos, indent=1, default=str, sort_keys=True))


def load_condors(d: Path | None = None) -> dict:
    f = _dir(d) / CONDORS
    return json.loads(f.read_text()) if f.exists() else {}


def save_condors(pos: dict, d: Path | None = None) -> None:
    _atomic(_dir(d) / CONDORS, json.dumps(pos, indent=1, default=str, sort_keys=True))


def append(name: str, rec: dict, d: Path | None = None) -> None:
    with open(_dir(d) / name, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def read_jsonl(name: str, d: Path | None = None) -> list[dict]:
    """Every parseable line. A host that dies mid-append leaves a truncated last line; it is
    skipped (with a warning) instead of breaking every later reader (sync, entry recovery)."""
    f = _dir(d) / name
    if not f.exists():
        return []
    out = []
    for i, x in enumerate(f.read_text().splitlines()):
        if not x.strip():
            continue
        try:
            out.append(json.loads(x))
        except ValueError:
            log.warning("%s line %d is not valid JSON (truncated write?); skipped",
                                                     name, i + 1)
    return out


def _load_json(name: str, d: Path | None = None) -> dict:
    f = _dir(d) / name
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except ValueError:
        log.error("%s is not valid JSON; treated as empty", f)
        return {}


def load_inflight(d: Path | None = None) -> dict:
    return _load_json(INFLIGHT, d)


def save_inflight(rec: dict, d: Path | None = None) -> None:
    _atomic(_dir(d) / INFLIGHT, json.dumps(rec, indent=1, default=str, sort_keys=True))


def clear_inflight(d: Path | None = None) -> None:
    f = _dir(d) / INFLIGHT
    if f.exists():
        f.unlink()


def load_resting(d: Path | None = None) -> dict:
    return _load_json(RESTING, d)


def save_resting(rec: dict, d: Path | None = None) -> None:
    _atomic(_dir(d) / RESTING, json.dumps(rec, indent=1, default=str, sort_keys=True))


def already_ran(today: dt.date, kind: str = "daily", d: Path | None = None) -> bool:
    f = _dir(d) / GUARD
    if not f.exists():
        return False
    return json.loads(f.read_text()).get(kind) == today.isoformat()


def mark_ran(today: dt.date, kind: str = "daily", d: Path | None = None) -> None:
    f = _dir(d) / GUARD
    cur = json.loads(f.read_text()) if f.exists() else {}
    cur[kind] = today.isoformat()
    _atomic(f, json.dumps(cur))


def reconcile(spreads, records: dict, today: dt.date) -> tuple[dict, list[str], list[dict]]:
    """Match broker spreads to stored records.

    Returns (records for every broker spread, ids adopted without a record,
    records whose spread is no longer at the broker). Adopted spreads get the
    broker's average prices as the entry credit and today as the entry date
    (logged loudly so the owner can fix the file)."""
    out, adopted = {}, []
    for s in spreads:
        r = records.get(s.id)
        if r is None:
            r = {"underlying": s.underlying, "expiry": s.expiry.isoformat(), "short_symbol": s.short_symbol,
                 "long_symbol": s.long_symbol, "short_strike": s.short_strike, "long_strike": s.long_strike,
                 "qty": s.qty, "entry_date": today.isoformat(), "entry_credit": s.broker_credit,
                 "adopted": True}
            adopted.append(s.id)
        else:
            r = {**r, "qty": s.qty}
        out[s.id] = r
    vanished = [{"id": k, **v} for k, v in records.items() if k not in out]
    return out, adopted, vanished


def reconcile_condors(condors, records: dict, today: dt.date) -> tuple[dict, list[str], list[dict]]:
    """``reconcile`` for the condor sleeve: (records for every broker condor,
    ids adopted without a record, records no longer at the broker)."""
    out, adopted = {}, []
    for c in condors:
        r = records.get(c.id)
        if r is None:
            r = {"sleeve": C.SLEEVE_CONDOR, "underlying": c.underlying, "expiry": c.expiry.isoformat(),
                 "put_short_symbol": c.put_short_symbol, "put_long_symbol": c.put_long_symbol,
                 "call_short_symbol": c.call_short_symbol, "call_long_symbol": c.call_long_symbol,
                 "put_short_strike": c.put_short_strike, "put_long_strike": c.put_long_strike,
                 "call_short_strike": c.call_short_strike, "call_long_strike": c.call_long_strike,
                 "qty": c.qty, "entry_date": today.isoformat(), "entry_credit": c.broker_credit, "adopted": True}
            adopted.append(c.id)
        else:
            r = {**r, "qty": c.qty}
        out[c.id] = r
    vanished = [{"id": k, **v} for k, v in records.items() if k not in out]
    return out, adopted, vanished
