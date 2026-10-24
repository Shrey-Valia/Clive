"""Re-run the LIVE scoring code (shortingbot.scoring) on the historical scan rows with patched weights.

Round 1's events.parquet short_score was computed with the config of the day (FTD weight 10).
score_live() uses the live config as it stands (FTD weight 0); variants patch config values.
"""
from __future__ import annotations

import contextlib
import os
import sys

import numpy as np
import pandas as pd

from r3common import RESEARCH

sys.path.insert(0, os.path.dirname(RESEARCH))
from shortingbot import config as botcfg  # noqa: E402
from shortingbot import scoring  # noqa: E402


@contextlib.contextmanager
def patched(**kw):
    old = {k: getattr(botcfg, k) for k in kw}
    for k, v in kw.items():
        setattr(botcfg, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(botcfg, k, v)


def rows(ev: pd.DataFrame):
    for r in ev.itertuples(index=False):
        yield {"change": 100 * r.chg1545, "rvol": r.rvol if np.isfinite(r.rvol) else None,
               "high": r.hi, "low": r.lo, "close": r.p1545,
               "si_pct_float": r.si_pct if pd.notna(r.si_pct) else None,
               "days_to_cover": r.dtc if pd.notna(r.dtc) else None,
               "ftd_days": int(r.ftd_days) if r.ftd_have else None,
               "ftd_max_shares": r.ftd_max if pd.notna(r.ftd_max) else None,
               "float_shares": r.shares if pd.notna(r.shares) else None,
               "catalyst": r.cat_class, "catalyst_score": r.catalyst_score}


def score(ev: pd.DataFrame, crowding_fn=None, **patch) -> np.ndarray:
    out = np.empty(len(ev))
    orig = scoring.crowding
    with patched(**patch):
        if crowding_fn is not None:
            scoring.crowding = crowding_fn
        try:
            for i, row in enumerate(rows(ev)):
                out[i] = scoring.score_row(row)["short_score"]
        finally:
            scoring.crowding = orig
    return out


def dtc_only_crowding(r: dict):
    """Crowding block = the DTC band score alone (round 1: raw DTC was the consistent penalty)."""
    return scoring._band_score(r.get("days_to_cover"), botcfg.DTC_IDEAL, botcfg.DTC_DANGER, 1.0)
