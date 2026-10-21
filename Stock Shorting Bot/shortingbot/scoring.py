"""The short checklist, one block per item.

  1. Move & volume   up 20-100% on 5-10x volume, upper wick, weak close  (weighted)
  2. Crowding        DTC 1-5 / SI 5-15% ideal; DTC>10 and SI>20% excluded (weighted + gate)
  3. Borrow          Alpaca shortable and easy to borrow                  (gate)
  4. FTD             repeated giant fails relative to float are bad       (weighted)
  5. Catalyst        real de-risking news is excluded, hype is rewarded   (weighted + gate)

Each block scores 0-1 (1 = best short). A block with no data drops out and
the remaining weights renormalise, so the score is always on 0-100.
"""
from __future__ import annotations

from . import config


def ramp(value, lo, hi):
    """0 at lo, 1 at hi, clamped. lo > hi gives an inverted ramp."""
    if value is None:
        return None
    if lo == hi:
        return 1.0 if value >= hi else 0.0
    t = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def _weighted(parts: dict, weights: dict):
    have = {k: v for k, v in parts.items() if v is not None}
    total = sum(weights[k] for k in have)
    if not total:
        return None
    return sum(v * weights[k] for k, v in have.items()) / total


def move_volume(r: dict) -> tuple[float | None, dict]:
    hi, lo, close = r.get("high"), r.get("low"), r.get("close")
    rng = (hi - lo) if hi is not None and lo is not None else None
    wick = (hi - close) / rng if rng and close is not None else None
    cir = 100 * (close - lo) / rng if rng and close is not None else None
    change = min(r.get("change") or 0, config.CHANGE_CAP) if r.get("change") is not None else None
    parts = {
        "change": ramp(change, *config.CHANGE_RAMP),
        "rvol": ramp(r.get("rvol"), *config.RVOL_RAMP),
        "wick": ramp(wick, *config.UPPER_WICK_RAMP),
        "close_in_range": ramp(cir, *config.CLOSE_IN_RANGE_RAMP),
    }
    detail = {"upper_wick": round(wick, 3) if wick is not None else None,
              "close_in_range": round(cir, 1) if cir is not None else None}
    return _weighted(parts, config.MOVE_SUBWEIGHTS), detail


def _band_score(value, ideal, danger, below_score):
    """1 inside the ideal band, below_score under it, falling to 0 at danger above it."""
    if value is None:
        return None
    lo, hi = ideal
    if value < lo:
        return below_score
    if value <= hi:
        return 1.0
    return ramp(value, danger, hi)


def crowding(r: dict) -> float | None:
    si = _band_score(r.get("si_pct_float"), config.SI_IDEAL, config.SI_DANGER, config.TINY_SI_SCORE)
    dtc = _band_score(r.get("days_to_cover"), config.DTC_IDEAL, config.DTC_DANGER, 1.0)
    have = [v for v in (si, dtc) if v is not None]
    return sum(have) / len(have) if have else None


def ftd(r: dict) -> float | None:
    days, peak, flt = r.get("ftd_days"), r.get("ftd_max_shares"), r.get("float_shares") or r.get("float_shares_outstanding")
    if days is None:
        return None
    if days == 0:
        return 1.0
    pct = 100 * peak / flt if peak and flt else None
    bad = [ramp(pct, *config.FTD_MAX_PCT_FLOAT_RAMP), ramp(days, *config.FTD_DAYS_RAMP)]
    bad = [b for b in bad if b is not None]
    return 1.0 - max(bad) if bad else None


def exclude_reason(r: dict) -> str | None:
    if r.get("shortable") is False:
        return "not shortable on Alpaca"
    if r.get("easy_to_borrow") is False:
        return "hard to borrow on Alpaca"
    si, dtc = r.get("si_pct_float"), r.get("days_to_cover")
    if si is not None and dtc is not None and si > config.SQUEEZE_EXCLUDE_SI and dtc > config.SQUEEZE_EXCLUDE_DTC:
        return f"squeeze trap: SI {si:.1f}% / DTC {dtc:.1f}"
    if config.CATALYST_EXCLUDE and r.get("catalyst") == "real_catalyst":
        return "real de-risking catalyst"
    if (r.get("change") or 0) <= 0:
        return "not up on the day"
    return None


def flags(r: dict) -> list[str]:
    out = []
    if r.get("si_pct_float") is None:
        out.append("NO_SI")
    elif r["si_pct_float"] < config.SI_IDEAL[0]:
        out.append("LOW_SI_NO_FORCED_SELLERS")
    if (r.get("si_age_days") or 0) > 30:
        out.append(f"STALE_SI_{r['si_age_days']}d")
    if (r.get("change") or 0) < 20:
        out.append("SMALL_MOVE")
    if (r.get("rvol") or 0) < 3:
        out.append("LOW_RVOL")
    if (r.get("close_in_range") or 0) >= 85:
        out.append("CLOSING_ON_HIGHS")
    if (r.get("short_vol_ratio_latest") or 0) >= 60:
        out.append("HEAVY_SHORT_VOLUME")
    return out


def score_row(r: dict) -> dict:
    mv, detail = move_volume(r)
    r.update(detail)
    comps = {"move_volume": mv, "crowding": crowding(r), "ftd": ftd(r),
             "catalyst": r.get("catalyst_score")}
    total = _weighted(comps, config.WEIGHTS)
    r["components"] = {k: round(v, 3) if v is not None else None for k, v in comps.items()}
    r["coverage"] = round(sum(config.WEIGHTS[k] for k, v in comps.items() if v is not None)
                          / sum(config.WEIGHTS.values()), 2)
    r["short_score"] = round(100 * total, 1) if total is not None else 0.0
    r["exclude_reason"] = exclude_reason(r)
    r["flags"] = flags(r)
    return r


def rank(rows: list[dict]) -> list[dict]:
    """Score all rows, eligible names first by score, excluded names after."""
    for r in rows:
        score_row(r)
    rows.sort(key=lambda r: (r["exclude_reason"] is not None, -r["short_score"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
