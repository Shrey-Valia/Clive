#!/usr/bin/env python3
"""Rule 4 flags: session-level news / option-volume vetoes for SPY, QQQ, IWM.

Headlines: Alpaca news tagged SPY/QQQ/IWM/DIA (market-wide and macro stories), fetched by
fetch_news.py. created_at (first publication) is mapped to the session whose 15:55 ET
decision it can influence: created on day D at or before 15:55 ET (12:55 on half days)
-> session D; later, or on a non-trading day -> the next session.

Flags (one set of session dates per name; market-wide news applies to all three ETFs):
  neg     fresh negative UNSCHEDULED macro headlines: count of headlines matching NEG and not
          SCHED >= max(3, 2 x the trailing 20-session median count)
  cat     the shorting bot's classifier (shortingbot.news STRONG/FLUFF/DILUTION decision tree,
          imported read-only) over the session's market-wide headlines (<= 4 tagged symbols)
          returns "real_catalyst" or "dilution"
  otm     abnormal OTM option volume (otm_volume.py): 2-10% OTM put volume, or call volume,
          > 2.5 x its trailing 20-session median (same-day bar; *_lag1 uses the prior session)
  any     neg | otm  (cat reported separately: see the frequency printout)
Output: .cache/news_flags.pkl  {name: {symbol: set(Timestamp)}} and .cache/news_sessions.csv
"""
from __future__ import annotations

import datetime as dt
import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L                                                          # noqa: E402

sys.path.insert(0, str(HERE.parents[2] / "Stock Shorting Bot"))
from shortingbot.news import DILUTION, FLUFF, STRONG                    # noqa: E402  (read-only)

ET = "America/New_York"
HALF_DAYS = {"2024-07-03", "2024-11-29", "2024-12-24", "2025-07-03", "2025-11-28", "2025-12-24",
             "2026-07-02", "2026-11-27", "2026-12-24"}
NEG = [r"\btariff", r"\btrade war\b", r"\bwar\b", r"\battack", r"\binvasion\b", r"\binvade", r"\bmissile",
       r"\bairstrike", r"\bstrikes? on\b", r"\bretaliat", r"\bsanction", r"\bescalat", r"\bcrash",
       r"\bplunge", r"\bplummet", r"\btumble", r"\bsell-?off\b", r"\brout\b", r"\bpanic", r"\bturmoil",
       r"\bcollapse", r"\bcrisis\b", r"\bcontagion\b", r"\brecession fears?\b", r"\bdowngrade[sd]? (the )?(us|u\.s\.)\b",
       r"\bcredit rating\b", r"\bdefault\b", r"\bbank (failure|run|collapse)", r"\bshutdown\b", r"\bemergency\b",
       r"\bcircuit breaker\b", r"\bworst (day|week|session)\b", r"\bbloodbath\b", r"\bfreefall\b",
       r"\bfears?\b", r"\bslump", r"\bsink", r"\bslide", r"\bvix (spike|jump|surge|soar)", r"\bvolatility (spike|surge)"]
SCHED = [r"\bcpi\b", r"\bppi\b", r"\binflation (data|report)\b", r"\bpayrolls?\b", r"\bjobs report\b",
         r"\bjobless claims\b", r"\bfomc\b", r"\bfed (decision|meeting|minutes)\b", r"\bminutes\b", r"\bgdp\b",
         r"\bpmi\b", r"\bretail sales\b", r"\bconsumer (confidence|sentiment)\b", r"\bredbook\b",
         r"\bearnings\b", r"\bq[1-4]\b", r"\bresults\b", r"\bguidance\b", r"\best\.", r"\bvs\.? .*\b(est|prior)\b",
         r"\bhousing starts\b", r"\bdurable goods\b", r"\bism\b", r"\bjolts\b", r"\bpce\b"]


def load_news() -> pd.DataFrame:
    rows = []
    for f in sorted((L.CACHE / "news").glob("index_*.json")):
        rows += json.loads(f.read_text())
    df = pd.DataFrame(rows).drop_duplicates("id")
    df["created"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(ET)
    df["nsym"] = df["symbols"].apply(len)
    return df.sort_values("created").reset_index(drop=True)


def sessions() -> pd.DatetimeIndex:
    return L.market("SPY").stock.loc["2024-01-02":"2026-10-20"].index


def assign_session(created: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    cut = []
    for d in days:
        h, m = (12, 55) if str(d.date()) in HALF_DAYS else (15, 55)
        cut.append(pd.Timestamp(dt.datetime.combine(d.date(), dt.time(h, m)), tz=ET))
    cut = pd.DatetimeIndex(cut).tz_convert("UTC").tz_localize(None)
    c = pd.DatetimeIndex(created).tz_convert("UTC").tz_localize(None)
    pos = cut.searchsorted(c, side="left")                   # first cutoff >= created
    vals = [days[p] if p < len(days) else pd.NaT for p in pos]
    return pd.Series(vals, index=created.index)


def has(pats, text):
    return any(re.search(p, text) for p in pats)


def classify_like_bot(texts: list[str]) -> str:
    """shortingbot.news.classify decision tree on already-fetched text."""
    if not texts:
        return "no_news"
    text = " ".join(texts).lower()
    s = [p for p in STRONG if re.search(p, text)]
    f = [p for p in FLUFF if re.search(p, text)]
    d = [p for p in DILUTION if re.search(p, text)]
    if s and not d:
        return "real_catalyst"
    if d:
        return "dilution"
    if f:
        return "hype"
    return "unclear"


def main():
    df = load_news()
    days = sessions()
    df["session"] = assign_session(df["created"], days)
    df = df.dropna(subset=["session"])
    hl = (df["headline"].fillna("") + " " + df["summary"].fillna("")).str.lower()
    df["neg"] = hl.apply(lambda t: has(NEG, t)) & ~df["headline"].fillna("").str.lower().apply(lambda t: has(SCHED, t))
    g = df.groupby("session")
    ses = pd.DataFrame(index=days)
    ses["n"] = g.size().reindex(days).fillna(0)
    ses["neg"] = g["neg"].sum().reindex(days).fillna(0)
    mw = df[df.nsym <= 4]
    lab = mw.groupby("session").apply(lambda x: classify_like_bot(
        list((x["headline"].fillna("") + " " + x["summary"].fillna("")).values)))
    ses["cat_label"] = lab.reindex(days).fillna("no_news")
    med = ses["neg"].shift(1).rolling(20, min_periods=10).median()
    ses["flag_neg"] = ses["neg"] >= np.maximum(3, 2 * med.fillna(ses["neg"].median()))
    ses["flag_cat"] = ses["cat_label"].isin(["real_catalyst", "dilution"])

    ov = pd.read_parquet(L.CACHE / "otm_volume.parquet")
    flags = {k: {} for k in ("neg", "cat", "otm", "otm_lag1", "any", "any_lag1")}
    allv = []
    for s in L.ETFS:
        v = ov[ov.symbol == s].set_index("date").sort_index()
        v.index = pd.to_datetime(v.index)
        rp = v["put_vol"] / v["put_vol"].shift(1).rolling(20, min_periods=10).median()
        rc = v["call_vol"] / v["call_vol"].shift(1).rolling(20, min_periods=10).median()
        otm = ((rp > 2.5) | (rc > 2.5)).reindex(days).fillna(False)
        otm1 = otm.shift(1).fillna(False)
        allv.append(pd.DataFrame({f"{s}_put_ratio": rp, f"{s}_call_ratio": rc}))
        neg = ses["flag_neg"]
        flags["neg"][s] = set(days[neg.values])
        flags["cat"][s] = set(days[ses["flag_cat"].values])
        flags["otm"][s] = set(days[otm.values])
        flags["otm_lag1"][s] = set(days[otm1.values])
        flags["any"][s] = set(days[(neg | otm).values])
        flags["any_lag1"][s] = set(days[(neg | otm1).values])
    (L.CACHE / "news_flags.pkl").write_bytes(pickle.dumps(flags))
    out = ses.join(pd.concat(allv, axis=1), how="left")
    out.to_csv(HERE / ".cache" / "news_sessions.csv")
    sub = ses.loc[L.START:L.END]
    print("sessions", len(sub), "headlines/session median", sub.n.median())
    print("flag rates:", {k: round(len([d for d in v["SPY"] if L.START <= str(d.date()) <= L.END]) / len(sub), 3)
                          for k, v in flags.items()})
    print(sub.cat_label.value_counts(normalize=True).round(3))
    print(sub[sub.flag_neg].neg.describe())


if __name__ == "__main__":
    main()
