"""Historical base rate for each call.

Every LONG/SHORT the scanner prints gets matched against comparable
historical spikes - same rough spike size, same rough extension above the
20-day - and reports what actually happened the next day.

This exists because the scores are reasoned, not fitted. The base rate is
the reality check: it is measured, and it is the number that should carry
weight when the two disagree.

Requires output/backtest/events.csv (run `python3 backtest.py` once).
"""
from __future__ import annotations

import os

import pandas as pd

EVENTS = os.path.join("output", "backtest", "events.csv")

SPIKE_BINS = [(15, 25), (25, 40), (40, 60), (60, 1e4)]
EXT_BINS = [(-1e4, 10), (10, 25), (25, 50), (50, 1e4)]
MIN_SAMPLE = 30

_cache: pd.DataFrame | None = None


def _load() -> pd.DataFrame | None:
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(EVENTS):
        return None
    try:
        _cache = pd.read_csv(EVENTS)
    except Exception:
        return None
    return _cache


def _bin(value, bins):
    for lo, hi in bins:
        if lo <= value < hi:
            return lo, hi
    return None


def lookup(change_pct, ext_pct, call: str) -> dict:
    """Base rate for this setup and direction. Widens the match if too thin."""
    blank = {"exp_n": None, "exp_avg_pct": None, "exp_win_pct": None,
             "exp_worst1_pct": None, "exp_basis": None}

    e = _load()
    if e is None or change_pct is None or ext_pct is None or call not in ("LONG", "SHORT"):
        return blank

    sb, xb = _bin(float(change_pct), SPIKE_BINS), _bin(float(ext_pct), EXT_BINS)
    if sb is None or xb is None:
        return blank

    # Narrow match first; fall back to spike-only, then all spikes, so a
    # thin cell reports a wider base rate rather than a noisy one.
    attempts = [
        (e[(e.chg >= sb[0]) & (e.chg < sb[1]) & (e.ext >= xb[0]) & (e.ext < xb[1])],
         f"spike {sb[0]:.0f}-{sb[1]:.0f}%, ext {xb[0]:.0f}-{xb[1]:.0f}%"),
        (e[(e.chg >= sb[0]) & (e.chg < sb[1])], f"spike {sb[0]:.0f}-{sb[1]:.0f}%"),
        (e, "all spikes"),
    ]
    for subset, basis in attempts:
        if len(subset) >= MIN_SAMPLE:
            pnl = subset.nxt if call == "LONG" else -subset.nxt
            return {
                "exp_n": len(subset),
                "exp_avg_pct": round(pnl.mean(), 2),
                "exp_win_pct": round(100 * (pnl > 0).mean(), 1),
                "exp_worst1_pct": round(pnl.quantile(0.01), 1),
                "exp_basis": basis,
            }
    return blank


def enrich(rows: list[dict]) -> None:
    """Attach base-rate fields to each scored row, in place."""
    for r in rows:
        close, sma20 = r.get("close"), r.get("SMA20")
        ext = (100 * (close / sma20 - 1)) if (close and sma20 and sma20 > 0) else None
        r.update(lookup(r.get("change"), ext, r.get("call")))
