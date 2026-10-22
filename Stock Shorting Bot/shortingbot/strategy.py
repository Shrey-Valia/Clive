"""Pick the names, size them, and turn the target book into orders.

v2 (short_intraday) uses select -> save picks -> recheck_at_open -> plan_cover.
v1 (short_overnight) uses select -> plan_orders.
"""
from __future__ import annotations

import math

from . import config


def size_pct(slot: int, n: int = config.N_POSITIONS) -> float:
    """SIZE_PCT_PER_NAME for every slot (v2). With it None: slot 0 gets SIZE_PCT_MAX,
    slot n-1 gets SIZE_PCT_MIN, linear between (v1)."""
    if config.SIZE_PCT_PER_NAME is not None:
        return config.SIZE_PCT_PER_NAME
    if n <= 1:
        return config.SIZE_PCT_MAX
    step = (config.SIZE_PCT_MAX - config.SIZE_PCT_MIN) / (n - 1)
    return round(config.SIZE_PCT_MAX - slot * step, 2)


def gate_reason(r: dict) -> str | None:
    """The proposal's score and liquidity gates, on top of scoring.exclude_reason. 0 disables one."""
    if config.MIN_SHORT_SCORE and (r.get("short_score") or 0) < config.MIN_SHORT_SCORE:
        return f"score below {config.MIN_SHORT_SCORE:g}"
    if config.MIN_PRICE_AT_SCAN and (r.get("close") or 0) < config.MIN_PRICE_AT_SCAN:
        return f"price below ${config.MIN_PRICE_AT_SCAN:g}"
    if config.MIN_ADV20_USD:
        adv = r.get("adv20_usd")
        if adv is None:
            return "no ADV20 data"
        if adv < config.MIN_ADV20_USD:
            return f"ADV20 ${adv / 1e6:.1f}M below ${config.MIN_ADV20_USD / 1e6:g}M"
    return None


def select(ranked: list[dict], equity: float) -> list[dict]:
    """Walk the ranking, skipping exclusions and gated names, until N names have a size of
    1+ share or the gross cap is used up. Fewer than N, or none, is a valid answer."""
    picks, gross = [], 0.0
    for r in ranked:
        if len(picks) == config.N_POSITIONS:
            break
        if r.get("exclude_reason") or not r.get("close"):
            continue
        gated = gate_reason(r)
        if gated:
            r["exclude_reason"] = gated
            continue
        pct = min(size_pct(len(picks)), config.MAX_GROSS_PCT - gross)
        qty = math.floor(equity * pct / 100 / r["close"])
        if qty < 1:
            r["exclude_reason"] = "position too small for one share" if pct > 0 else "gross cap reached"
            continue
        gross += pct
        r.update(selected=True, target_pct=pct, target_qty=-qty)
        picks.append(r)
    return picks


def plan_cover(held: dict[str, int]) -> list[dict]:
    """v2 15:55: buy back every short. Longs are left alone for whichever strategy owns them."""
    return [{"ticker": t, "side": "buy", "qty": -q, "purpose": "cover"}
            for t, q in sorted(held.items()) if q < 0]


def recheck_at_open(picks: list[dict], borrow: dict[str, dict], prices: dict[str, float | None],
                    held: dict[str, int], equity: float,
                    existing_gross_pct: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Re-check yesterday's saved picks at the open and size the entries.

    Returns (entries, skips). A pick is skipped when it is no longer shortable / easy to
    borrow, has no price, trades under MIN_PRICE_AT_SCAN, gapped up more than MAX_GAP_UP_PCT
    over its scan price, or is already held. Entries are sized on today's price and equity,
    equal SIZE_PCT each, and gross short (existing shorts included) stays under MAX_GROSS_PCT.
    """
    entries, skips = [], []
    gross = existing_gross_pct
    for slot, p in enumerate(picks):
        t = p["ticker"]
        b, px, scan_px = borrow.get(t, {}), prices.get(t), p.get("scan_price")
        why = None
        if not b.get("shortable"):
            why = "not shortable on Alpaca at the open"
        elif not b.get("easy_to_borrow"):
            why = "hard to borrow on Alpaca at the open"
        elif not px:
            why = "no price at the open"
        elif config.MIN_PRICE_AT_SCAN and px < config.MIN_PRICE_AT_SCAN:
            why = f"open price ${px:.2f} below ${config.MIN_PRICE_AT_SCAN:g}"
        elif scan_px and 100 * (px / scan_px - 1) > config.MAX_GAP_UP_PCT:
            why = f"gapped up {100 * (px / scan_px - 1):.0f}% over scan price ${scan_px:.2f}"
        elif held.get(t):
            why = f"already held ({held[t]})"
        if why is None:
            pct = min(size_pct(slot), config.MAX_GROSS_PCT - gross)
            qty = math.floor(equity * pct / 100 / px) if pct > 0 else 0
            if qty < 1:
                why = "gross cap reached" if pct <= 0 else "position too small for one share"
            else:
                gross += qty * px / equity * 100
                entries.append({"ticker": t, "side": "sell", "qty": qty, "purpose": "short",
                                "price": px, "target_pct": round(qty * px / equity * 100, 2)})
        if why:
            skips.append({"ticker": t, "reason": why})
    return entries, skips


def pdt_applies(acct: dict, detected: bool = False) -> bool:
    """Whether the 3-in-5 day trade cap is in force (config.PDT_ENFORCE)."""
    if config.PDT_ENFORCE == "never":
        return False
    if config.PDT_ENFORCE == "always":
        return True
    under = (acct.get("equity") or 0) < config.PDT_MIN_EQUITY
    dtbp = acct.get("daytrading_buying_power")
    return bool(detected or acct.get("pattern_day_trader")
                or (under and acct.get("daytrade_count") is not None)
                or (dtbp is not None and dtbp <= 0))


def pdt_allowance(acct: dict, day_trades: int | None) -> int | None:
    """New day trades the account may take today. None = no cap (equity >= PDT_MIN_EQUITY).
    0 when the count is unknown: never open a short that can't be covered the same day."""
    if (acct.get("equity") or 0) >= config.PDT_MIN_EQUITY:
        return None
    if day_trades is None:
        return 0
    return max(0, config.PDT_MAX_DAY_TRADES - day_trades)


def cap_entries(entries: list[dict], allowance: int | None) -> tuple[list[dict], list[dict]]:
    """Keep the first `allowance` entries (rank order). Returns (kept, skips)."""
    if allowance is None or len(entries) <= allowance:
        return entries, []
    return entries[:allowance], [{"ticker": e["ticker"], "reason": f"PDT limit ({allowance} day trades left)"}
                                 for e in entries[allowance:]]


def stop_price(fill_price: float, pct: float) -> float:
    """Buy-stop level for a short filled at fill_price: pct above, on the penny."""
    return round(fill_price * (1 + pct / 100), 2)


def halt_reason(acct: dict, peak: float | None) -> tuple[str | None, str | None]:
    """(daily-loss reason, drawdown reason) for the configured halts, None when clear or off."""
    daily = dd = None
    eq, last = acct.get("equity") or 0, acct.get("last_equity") or 0
    if config.DAILY_LOSS_HALT_PCT is not None and last > 0:
        down = 100 * (last - eq) / last
        if down >= config.DAILY_LOSS_HALT_PCT:
            daily = f"account down {down:.1f}% on the day (halt at {config.DAILY_LOSS_HALT_PCT:g}%)"
    if config.DRAWDOWN_HALT_PCT is not None and peak:
        down = 100 * (peak - eq) / peak
        if down >= config.DRAWDOWN_HALT_PCT:
            dd = f"equity ${eq:,.0f} is {down:.1f}% under its ${peak:,.0f} peak (halt at {config.DRAWDOWN_HALT_PCT:g}%)"
    return daily, dd


def pdt_block(acct: dict, n_day_trades: int) -> str | None:
    """Why the account can't take n more day trades today, or None.

    Covers a flagged pattern day trader. The 3-in-5 count is pdt_allowance's job. A
    rejected order is still caught where it is sent (daily.is_pdt_error).
    """
    if n_day_trades <= 0:
        return None
    equity = acct.get("equity") or 0
    if equity >= config.PDT_MIN_EQUITY:
        dtbp = acct.get("daytrading_buying_power")
        if acct.get("pattern_day_trader") and dtbp is not None and dtbp <= 0:
            return "pattern day trader with no day-trading buying power left"
        return None
    if acct.get("pattern_day_trader"):
        return f"flagged pattern day trader with equity ${acct['equity']:,.0f} < ${config.PDT_MIN_EQUITY:,}"
    return None


def plan_orders(held: dict[str, int], target: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """v1: net holdings against the target book.

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
