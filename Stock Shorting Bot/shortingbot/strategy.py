"""Pick the top 5, size them, and turn the target book into orders."""
from __future__ import annotations

import math

from . import config


def size_pct(slot: int, n: int = config.N_POSITIONS) -> float:
    """Slot 0 gets SIZE_PCT_MAX, slot n-1 gets SIZE_PCT_MIN, linear between."""
    if n <= 1:
        return config.SIZE_PCT_MAX
    step = (config.SIZE_PCT_MAX - config.SIZE_PCT_MIN) / (n - 1)
    return round(config.SIZE_PCT_MAX - slot * step, 2)


def select(ranked: list[dict], equity: float) -> list[dict]:
    """Walk the ranking, skipping exclusions, until N names have a size of 1+ share."""
    picks = []
    for r in ranked:
        if len(picks) == config.N_POSITIONS:
            break
        if r.get("exclude_reason") or not r.get("close"):
            continue
        pct = size_pct(len(picks))
        qty = math.floor(equity * pct / 100 / r["close"])
        if qty < 1:
            r["exclude_reason"] = "position too small for one share"
            continue
        r.update(selected=True, target_pct=pct, target_qty=-qty)
        picks.append(r)
    return picks


def plan_orders(held: dict[str, int], target: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Net holdings against the target book.

    Returns (exits, entries). Exits cover or sell what should shrink or go;
    entries open or add to shorts. A name held at the right size gets no order,
    and a name that stays short is resized rather than closed and reopened,
    which would be a wash trade Alpaca rejects.
    """
    exits, entries = [], []
    for t in sorted(set(held) | set(target)):
        cur, want = held.get(t, 0), target.get(t, 0)
        if cur == want:
            continue
        if cur > 0 and want <= 0:                 # long to flat/short: sell the long first
            exits.append({"ticker": t, "side": "sell", "qty": cur, "purpose": "close_long"})
            cur = 0
        elif cur < 0 and want >= 0:               # short to flat
            exits.append({"ticker": t, "side": "buy", "qty": -cur, "purpose": "cover"})
            cur = 0
        if want < cur:                            # need more short
            entries.append({"ticker": t, "side": "sell", "qty": cur - want,
                            "purpose": "short" if cur == 0 else "add_short"})
        elif want > cur:                          # short too big: partial cover
            exits.append({"ticker": t, "side": "buy", "qty": want - cur, "purpose": "trim_short"})
    return exits, entries
