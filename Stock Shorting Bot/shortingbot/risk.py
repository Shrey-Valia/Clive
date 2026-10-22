"""Account-level halts (config.DAILY_LOSS_HALT_PCT, DRAWDOWN_HALT_PCT), both off by default.

  logs/halt.json         {"date", "reason"}: no new entries for the rest of that day
  logs/equity_peak.json  {"peak", "at"}: highest equity seen; delete the file to reset it
  logs/pdt_detected.json an order was rejected for a PDT reason: PDT_ENFORCE="auto" turns the cap on
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

from . import config

log = logging.getLogger("shortingbot")


def _path(name: str) -> str:
    return os.path.join(config.LOG_DIR, name)


def _read(name: str) -> dict | None:
    try:
        return json.load(open(_path(name)))
    except (OSError, ValueError):
        return None


def _write(name: str, data: dict) -> None:
    """tmp + rename, so a crash mid-write never leaves half a file."""
    try:
        tmp = _path(name) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, _path(name))
    except OSError as e:
        log.warning("could not write %s: %s", name, e)


def halted_today(today: str) -> str | None:
    h = _read("halt.json")
    return h.get("reason") if h and h.get("date") == today else None


def set_halt(today: str, reason: str) -> None:
    _write("halt.json", {"date": today, "reason": reason, "at": dt.datetime.now(dt.timezone.utc).isoformat()})


def update_peak(equity: float) -> float:
    """Record a new equity high. Returns the peak (equity itself the first time)."""
    p = _read("equity_peak.json")
    peak = float(p["peak"]) if p and p.get("peak") else 0.0
    if equity > peak:
        _write("equity_peak.json", {"peak": equity, "at": dt.datetime.now(dt.timezone.utc).isoformat()})
        return equity
    return peak


def pdt_detected() -> bool:
    return _read("pdt_detected.json") is not None


def set_pdt_detected(message: str) -> None:
    _write("pdt_detected.json", {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "message": message[:300]})
