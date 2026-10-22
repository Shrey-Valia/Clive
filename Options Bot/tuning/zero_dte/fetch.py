"""Build the day sample and pull/cache all data (GET only, ~60 req/min, 1 worker).

Sample: every FOMC / CPI / NFP day with 1-min option bars + 150 seeded ordinary days.
Contracts: same-day-expiry SPY calls and puts with strikes within +-5% of the 09:30 open (the +-3% core plus outer wings for $10 condors on volatile days).

Usage: python fetch.py [--spy-only]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zdata as z  # noqa: E402

FIRST_OPT_DAY = "2024-01-18"   # first session with SPY 1-min option bars on Alpaca (probed)
LAST_DAY = "2026-10-21"
SEED = 20261022
N_ORDINARY = 150

# BLS Employment Situation release dates (08:30 ET). 2025-10-03 and 2025-11-07 were not
# released on schedule (federal shutdown): Sept data came 2025-11-20, Oct+Nov on 2025-12-16.
# 2026 dates are from the BLS schedule as known; 2026-04-03 is Good Friday (no SPY session,
# dropped automatically).
NFP = """2024-02-02 2024-03-08 2024-04-05 2024-05-03 2024-06-07 2024-07-05 2024-08-02 2024-09-06
2024-10-04 2024-11-01 2024-12-06 2025-01-10 2025-02-07 2025-03-07 2025-04-04 2025-05-02 2025-06-06
2025-07-03 2025-08-01 2025-09-05 2025-11-20 2025-12-16 2026-01-09 2026-02-11 2026-03-06 2026-04-03
2026-05-08 2026-06-05 2026-07-02 2026-08-07 2026-09-04 2026-10-02""".split()


def sessions(spy: pd.DataFrame) -> pd.DataFrame:
    """Per-day regular-session summary; flags half days."""
    rth = spy.between_time("09:30", "15:59")
    g = rth.groupby(rth.index.date)
    d = pd.DataFrame({"open": g["o"].first(), "close": g["c"].last(), "n": g.size(),
                      "last_t": g.apply(lambda x: x.index[-1].strftime("%H:%M"))})
    d.index = pd.to_datetime(d.index)
    d["half"] = d["last_t"] < "15:50"
    return d


def event_table(days: pd.DatetimeIndex) -> pd.DataFrame:
    macro = pd.read_csv(HERE.parent / "events" / "macro_dates.csv", parse_dates=["date"])
    ev = {}
    for _, r in macro.iterrows():
        ev.setdefault(r.date, []).append(r.event)
    for s in NFP:
        ev.setdefault(pd.Timestamp(s), []).append("NFP")
    return pd.Series({d: "+".join(sorted(ev[d])) if d in ev else "" for d in days}, name="event")


def build_sample():
    spy = z.spy_minutes_range("2023-09-01", LAST_DAY)
    ses = sessions(spy)
    ses = ses[~ses.half]
    ev = event_table(ses.index)
    ses["event"] = ev
    pool = ses[(ses.index >= FIRST_OPT_DAY) & (ses.index <= LAST_DAY)]
    events = pool[pool.event != ""]
    ordinary = pool[pool.event == ""]
    rng = np.random.default_rng(SEED)
    pick = np.sort(rng.choice(len(ordinary), size=N_ORDINARY, replace=False))
    sample = pd.concat([events, ordinary.iloc[pick]]).sort_index()
    sample["kind"] = np.where(sample.event != "", "event", "ordinary")
    sample.index.name = "date"
    sample[["open", "close", "event", "kind"]].to_csv(HERE / "sample_days.csv")
    ses.to_csv(HERE / ".cache" / "sessions.csv")
    return sample


def fetch_options(sample: pd.DataFrame):
    cov = {}
    for i, (d, r) in enumerate(sample.iterrows()):
        day = d.strftime("%Y-%m-%d")
        lo, hi = int(np.ceil(r.open * 0.95)), int(np.floor(r.open * 1.05))
        syms = [z.occ(d, cp, k) for k in range(lo, hi + 1) for cp in "CP"]
        bars = z.option_minutes(day, syms)
        cov[day] = {"n_contracts": len(syms), "n_with_bars": sum(len(b) > 0 for b in bars.values()),
                    "n_bars": int(sum(len(b) for b in bars.values()))}
        if i % 10 == 0:
            print(i, day, cov[day], "req", z.client().n, flush=True)
    (HERE / ".cache" / "coverage.json").write_text(json.dumps(cov, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spy-only", action="store_true")
    a = ap.parse_args()
    s = build_sample()
    print("sample", len(s), s.kind.value_counts().to_dict(), "events", s.event.value_counts().to_dict())
    if not a.spy_only:
        fetch_options(s)
    print("requests", z.client().n)
