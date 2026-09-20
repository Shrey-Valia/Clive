"""Step 4 - tiers, 24-hour scores, and archetype classification.

HOLDING PERIOD: enter near the close, exit ~24h later near the next close.
Everything here is calibrated for that and nothing longer.

  long_score    Betting the move continues tomorrow. A day-2 runner: closed
                on its high, real volume behind it, tight float, and enough
                short interest to add fuel.

  short_score   Betting it gives back tomorrow. The move already faded off
                its high into the bell, it is stretched from the mean, and
                crucially there is NOT a crowded short base to squeeze you.

They are not inverses, and a name can score high on both.

Note what is deliberately almost absent: days-to-cover carries 5% and
fails-to-deliver zero. Both are multi-week measures. DTC counts the SESSIONS
shorts need to unwind - a 24h hold captures about a twelfth of a DTC-12
unwind, so ranking on it would be scoring a thesis this strategy never
holds long enough to collect.

Short interest still matters, but in a different role: as day-2 squeeze fuel
in tight-float names (gated on float and volume), and on the short side as
the risk that a crowded name gaps up overnight and takes you out.
"""
from __future__ import annotations

from . import config


def ramp(value, lo, hi):
    """Clamped linear interpolation. Handles inverted ramps (lo > hi)."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if lo == hi:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def tier(value, block: str) -> str | None:
    """Map a raw value onto its named band from config.TIERS."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    for upper, label in config.TIERS[block]:
        if value < upper:
            return label
    return config.TIERS[block][-1][1]


def _components(row: dict) -> dict:
    """Normalise every raw input to 0-1. None means 'no data for this'."""
    r = config.RAMPS
    close, sma20 = row.get("close"), row.get("SMA20")
    hi, lo, vwap = row.get("high"), row.get("low"), row.get("VWAP")
    si = row.get("short_pct_float")
    float_sh = row.get("float_shares") or row.get("float_shares_outstanding")

    sma_ext = (close / sma20 - 1) if (close and sma20 and sma20 > 0) else None

    # Where in today's range the stock finished. The single most useful
    # thing you can know about a spike when you hold it overnight: closing
    # on the high means buyers were still paying up into the bell, closing
    # near the low means the fade already began while you were watching.
    cir = (100 * (close - lo) / (hi - lo)) if (hi and lo and close and hi > lo) else None
    cvw = (100 * (close / vwap - 1)) if (close and vwap and vwap > 0) else None

    # Short interest as day-2 fuel only makes sense with a tight float and
    # real volume. On a big float with ordinary volume it will not move
    # anything inside 24 hours.
    si_gate = ramp(si, *r["si_gate"])
    si_raw = ramp(si, *r["short_pct_float"])
    si_day2 = (si_raw * si_gate) if (si_raw is not None and si_gate is not None) else None

    return {
        # long side
        "close_in_range":      ramp(cir, *r["close_in_range"]),
        "rel_volume":          ramp(row.get("relative_volume_10d_calc"), *r["rel_volume"]),
        "float_tightness":     ramp(float_sh, *r["float_tightness"]),
        "short_pct_float":     si_day2,
        "close_vs_vwap":       ramp(cvw, *r["close_vs_vwap"]),
        "spike":               ramp(row.get("change"), *r["spike"]),
        "days_to_cover":       ramp(row.get("days_to_cover"), *r["days_to_cover"]),
        "borrow_fee":          ramp(row.get("borrow_fee_pct"), *r["borrow_fee"]),
        # short side
        "close_weak_in_range": ramp(cir, *r["close_weak_in_range"]),
        "spike_extension":     ramp(row.get("change"), *r["spike_extension"]),
        "sma_extension":       ramp(sma_ext, *r["sma_extension"]),
        "low_squeeze_risk":    ramp(si, *r["low_squeeze_risk"]),
        "rvol_blowoff":        ramp(row.get("relative_volume_10d_calc"), *r["rvol_blowoff"]),
        "rsi_hot":             ramp(row.get("RSI"), *r["rsi_hot"]),
        "close_below_vwap":    ramp(cvw, *r["close_below_vwap"]),
    }


def _weighted(comps: dict, weights: dict) -> tuple[float, float]:
    """Score 0-100 over available components, plus % of weight covered.

    Missing inputs drop out and the rest renormalise, so a stock is never
    pushed down purely for a data gap - coverage reports the gap separately.
    """
    earned = available = 0.0
    for key, weight in weights.items():
        value = comps.get(key)
        if value is None:
            continue
        available += weight
        earned += value * weight
    if available == 0:
        return 0.0, 0.0
    return round(earned / available * 100, 1), round(available, 1)


def classify(row: dict) -> str:
    """Archetype from the interaction between blocks, not any single number.

    Ordered most-specific first; the first match wins.
    """
    si = row.get("short_pct_float")
    dtc = row.get("days_to_cover")
    sv = row.get("short_vol_ratio_avg")
    borrow = row.get("borrow_fee_pct")
    chg = row.get("change")

    if si is None:
        return "UNKNOWN_NO_SI"

    # Deep structural short: serious fuel AND exits that take days.
    if si >= 20 and (dtc or 0) >= 5:
        return "STRUCTURAL_SQUEEZE"

    # Real crowding with covering that would already hurt.
    if si >= 10 and (dtc or 0) >= 2:
        return "CROWDED_SHORT"

    # Expensive to borrow but almost nobody actually short: hype/HTB microcap,
    # not a deep squeeze.
    if borrow is not None and borrow >= 100 and si < 5:
        return "HTB_MICROCAP"

    # Most of the flow is short-side, but there is no structural base behind
    # it - shorts churning around a small position, not trapped.
    if sv is not None and sv >= 50 and si < 5:
        return "CHURN_NOT_TRAPPED"

    # Big move, nobody trapped: news or momentum, not a squeeze.
    if (chg or 0) >= 20 and si < 5 and (dtc or 0) < 1:
        return "MOMENTUM_NEWS"

    if si < 5:
        return "NO_SQUEEZE_FUEL"

    return "MODERATE"


def _overnight_risk(call: str, row: dict) -> str:
    """Gap risk across the one night this strategy is exposed to.

    Entering near the close and exiting near the next close means a full
    overnight hold with no ability to react. The asymmetric danger is a
    SHORT in a low-float, heavily-shorted name that closed strong: those
    gap up violently on any news, and there is no stop that protects you
    through a halt or a pre-market print.
    """
    si = row.get("short_pct_float")
    float_sh = row.get("float_shares") or row.get("float_shares_outstanding")
    rvol = row.get("relative_volume_10d_calc")
    hi, lo, close = row.get("high"), row.get("low"), row.get("close")
    cir = (100 * (close - lo) / (hi - lo)) if (hi and lo and close and hi > lo) else None

    if call == "SHORT":
        if si is None or float_sh is None:
            return "UNKNOWN"
        tight = float_sh < 20e6
        crowded = si >= 15
        hot = (rvol or 0) >= 5
        strong_close = (cir or 0) >= 70
        if tight and crowded and (hot or strong_close):
            return "EXTREME"
        if (tight and crowded) or (crowded and strong_close):
            return "HIGH"
        if si >= 10 or tight:
            return "ELEVATED"
        return "NORMAL"

    if call == "LONG":
        # The long risk is the opposite: a weak close into an extended move.
        if cir is not None and cir < 40 and (row.get("change") or 0) > 25:
            return "ELEVATED"
        return "NORMAL"

    return "UNKNOWN"


def _call(long_s: float, short_s: float, has_short_data: bool) -> tuple[str, str]:
    """Direction plus how much to trust it.

    Direction is always computable - whichever side scores higher. Conviction
    is the honest part: it needs a decent winning score AND a decent margin
    over the other side, because 52-vs-50 is a coin flip dressed up as a
    signal.
    """
    if long_s == 0 and short_s == 0:
        return "PASS", "NONE"

    call = "LONG" if long_s >= short_s else "SHORT"
    top = max(long_s, short_s)
    margin = abs(long_s - short_s)

    conviction = "LOW"
    for level in ("HIGH", "MEDIUM"):
        bar = config.CONVICTION[level]
        if top >= bar["min_score"] and margin >= bar["min_margin"]:
            conviction = level
            break

    # A LONG with no short interest behind it is an untestable thesis.
    if (call == "LONG" and not has_short_data
            and config.CAP_LONG_CONVICTION_WITHOUT_SI):
        conviction = "LOW"

    return call, conviction


def score_row(row: dict) -> dict:
    comps = _components(row)
    long_s, long_cov = _weighted(comps, config.LONG_WEIGHTS)
    short_s, short_cov = _weighted(comps, config.SHORT_WEIGHTS)

    has_short_data = row.get("short_pct_float") is not None

    # Short interest feeds the long side, so without it that read is
    # partly unverified - cap rather than let price action manufacture
    # confidence. Much smaller effect than at multi-week horizons, since
    # most of the long weight is now intraday price action.
    if not has_short_data:
        long_s = min(long_s, config.NO_SHORT_DATA_CONFIDENCE_CAP)

    age = row.get("short_data_age_days")
    if age and age > config.STALE_SHORT_DATA_DAYS:
        long_s = round(long_s * 0.9, 1)

    call, conviction = _call(long_s, short_s, has_short_data)

    # Downgrade calls whose supporting data cannot bear the weight.
    # Severity is matched to the defect: short interest measured before a
    # huge run is simply wrong, so that drops to LOW. A thin or missing base
    # rate means the call is unverified rather than contradicted, so that
    # steps down one level.
    mv = row.get("move_since_si_pct")
    exp_n = row.get("exp_n")
    _step = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW", "NONE": "NONE"}
    if mv is not None and abs(mv) >= config.SI_MEANINGLESS_MOVE_PCT:
        conviction = "LOW"
    elif exp_n is None or exp_n < config.MIN_EXPECTANCY_SAMPLE:
        conviction = _step[conviction]
    overnight_risk = _overnight_risk(call, row)
    archetype = classify(row)

    flags = []
    if not has_short_data:
        flags.append("NO_SHORT_DATA")
    if age and age > config.STALE_SHORT_DATA_DAYS:
        flags.append(f"STALE_SI_{age}D")
    if row.get("borrow_fee_pct") is None:
        flags.append("NO_BORROW_FEE")
    mv = row.get("move_since_si_pct")
    if mv is not None and mv >= config.SI_LIKELY_COVERED_MOVE_PCT:
        flags.append(f"RAN_{mv:+.0f}%_SINCE_SI")
    if mv is not None and abs(mv) >= config.SI_MEANINGLESS_MOVE_PCT:
        flags.append(f"SI_MEANINGLESS_RAN_{mv:+.0f}%")
    if exp_n is None:
        flags.append("NO_BASE_RATE")
    elif exp_n < config.MIN_EXPECTANCY_SAMPLE:
        flags.append(f"THIN_BASE_RATE_n{exp_n}")
    if archetype == "CHURN_NOT_TRAPPED":
        flags.append("SHORT_VOL_WITHOUT_BASE")
    if long_s >= 60 and short_s >= 60:
        flags.append("BOTH_SIDES_HOT")

    return {
        "long_score": long_s,
        "short_score": short_s,
        "archetype": archetype,
        "call": call,
        "conviction": conviction,
        "overnight_risk": overnight_risk,
        "score_margin": round(abs(long_s - short_s), 1),
        "si_tier": tier(row.get("short_pct_float"), "short_pct_float"),
        "dtc_tier": tier(row.get("days_to_cover"), "days_to_cover"),
        "borrow_tier": tier(row.get("borrow_fee_pct"), "borrow_fee_pct"),
        "short_vol_tier": tier(row.get("short_vol_ratio_avg"), "short_vol_ratio"),
        "long_data_coverage_pct": long_cov,
        "short_data_coverage_pct": short_cov,
        "flags": ";".join(flags),
        **{f"c_{k}": (round(v, 3) if v is not None else None)
           for k, v in comps.items()},
    }


CONVICTION_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def score_all(rows: list[dict]) -> list[dict]:
    """Score every row and rank it. Conviction leads, then the winning score.

    Sorting on raw score alone buries a clean, well-separated call under a
    higher-scoring coin flip - the opposite of useful on a board you read
    top-down.
    """
    scored = [{**r, **score_row(r)} for r in rows]
    scored.sort(
        key=lambda r: (CONVICTION_ORDER.get(r["conviction"], 0),
                       max(r["long_score"], r["short_score"])),
        reverse=True,
    )
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    return scored
