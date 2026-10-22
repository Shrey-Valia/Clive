"""Reconstruct the 15:45 scan for penny names ($1-$10 raw at 15:45) and attach the bot's checklist.

Same logic as the shorting research's features.py (imported bot scoring, read-only), applied to
the penny candidate pool; prev close floor $0.75 instead of $3 and no $25M market-cap screen
(ADV gates are applied per strategy). FTD is left out (weight 0 in the proposal; no signal).

Output .cache/pscan.parquet : top-20 by 15:45 change among names priced $1-$10 at 15:45.
"""
from __future__ import annotations

import os
import re
import sys

import numpy as np
import pandas as pd

from common import CACHE, SBOT, SCACHE

sys.path.insert(0, SBOT)
from shortingbot import scoring  # noqa: E402
from shortingbot.news import DILUTION, FLUFF, STRONG  # noqa: E402

SI_LAG_DAYS = 12
NEG = [r"\bbankrupt", r"chapter 11", r"\bdelist", r"going concern", r"complete response letter", r"\bcrl\b",
       r"fail(?:s|ed)? to meet", r"did not meet", r"discontinu", r"lowers? (?:guidance|outlook)", r"cuts? (?:guidance|outlook)",
       r"sec (?:investigation|subpoena|charges)", r"restat", r"class action", r"default", r"wind[- ]down",
       r"halts? (?:trial|study|enrollment)", r"clinical hold", r"misses", r"downgrade", r"resign"]


def classify(items):
    if not items:
        return "no_news", 0.75
    text = " ".join(items).lower()
    s = any(re.search(p, text) for p in STRONG)
    f = any(re.search(p, text) for p in FLUFF)
    d = any(re.search(p, text) for p in DILUTION)
    if s and not d:
        return "real_catalyst", 0.0
    if d:
        return "dilution", 1.0
    if f:
        return "hype", 0.9
    return "unclear", 0.5


def classify_neg(items):
    """Negative real news for the reversal long: dilution/offering or the NEG list."""
    if not items:
        return "no_news"
    text = " ".join(items).lower()
    if any(re.search(p, text) for p in DILUTION):
        return "dilution"
    if any(re.search(p, text) for p in NEG):
        return "negative"
    return "other"


def news_class(kind, frame, func, requested=None):
    """frame: symbol,date. Uses .cache/news_{kind}/{date}.parquet. Symbols not in the request set
    for that day (requested: set of (symbol, date)) get cls None (unknown), not 'no_news'."""
    rows = []
    d0 = os.path.join(CACHE, f"news_{kind}")
    for d, g in frame.groupby("date"):
        p = os.path.join(d0, f"{d}.parquet")
        if not os.path.exists(p):
            for s in g.symbol:
                rows.append((s, d, None, 0, False))
            continue
        nw = pd.read_parquet(p)
        trunc = bool(nw.truncated.any()) if len(nw) else False
        sets = [set(x.split(",")) for x in nw.symbols.astype(str)] if len(nw) else []
        txt = [f"{h} {s}" for h, s in zip(nw.headline, nw.summary)] if len(nw) else []
        for s in g.symbol:
            if requested is not None and (s, d) not in requested:
                rows.append((s, d, None, 0, False)); continue
            items = [t for t, ss in zip(txt, sets) if s in ss]
            rows.append((s, d, func(items), len(items), trunc))
    return pd.DataFrame(rows, columns=["symbol", "date", "cls", "n_news", "news_trunc"])


def minute_file(d):
    out = []
    for p in (os.path.join(SCACHE, "minute", f"{d}.parquet"), os.path.join(CACHE, "minute_late", f"{d}.parquet")):
        if os.path.exists(p):
            out.append(pd.read_parquet(p))
    return pd.concat(out, ignore_index=True).drop_duplicates(["symbol", "mtc"]) if out else None


def px_at5(b):
    at5 = b[b.mtc == 5]
    if len(at5):
        return at5.o.iloc[0]
    if len(b[b.mtc < 5]):
        return b[b.mtc < 5].sort_values("mtc", ascending=False).o.iloc[0]
    return b.sort_values("mtc").c.iloc[0]


def main():
    pp = pd.read_parquet(os.path.join(CACHE, "pp.parquet"))
    cg = pd.read_parquet(os.path.join(CACHE, "cand_gain.parquet"))[["symbol", "date"]]
    ev = cg.merge(pp, on=["symbol", "date"], how="left")
    cal = list(pd.read_csv(os.path.join(SCACHE, "calendar.csv")).date)
    nxt = dict(zip(cal[:-1], cal[1:]))
    rows, xrows = [], []
    for d, g in ev.groupby("date"):
        m = minute_file(d)
        if m is not None:
            m = m[m.symbol.isin(set(g.symbol))]
            for s, b in m.groupby("symbol"):
                pre, post = b[b.mtc >= 16], b[b.mtc <= 15]
                rows.append((s, d, pre.sort_values("mtc").c.iloc[0] if len(pre) else np.nan, px_at5(b),
                             post.v.sum(), post.h.max() if len(post) else np.nan, post.l.min() if len(post) else np.nan,
                             pre.h.max() if len(pre) else np.nan, pre.l.min() if len(pre) else np.nan,
                             b[b.mtc <= 5].h.max() if len(b[b.mtc <= 5]) else np.nan, len(b)))
        m2 = minute_file(nxt.get(d, ""))
        if m2 is not None:
            m2 = m2[m2.symbol.isin(set(g.symbol))]
            for s, b in m2.groupby("symbol"):
                xrows.append((s, d, px_at5(b)))
    mp = pd.DataFrame(rows, columns=["symbol", "date", "p1545", "p1555", "v_post", "h_post", "l_post", "h_pre",
                                     "l_pre", "h_last5", "n_min"])
    xp = pd.DataFrame(xrows, columns=["symbol", "date", "x1555"])
    ev = ev.merge(mp, on=["symbol", "date"], how="left").merge(xp, on=["symbol", "date"], how="left")
    ev["no_minute"] = ev.p1545.isna()
    ev["p1545"] = ev.p1545.fillna(ev.c)
    ev["p1555"] = ev.p1555.fillna(ev.c)
    ev["chg1545"] = ev.p1545 / ev.pc - 1
    ev["hi"] = np.where(ev.h_post.fillna(-1) >= ev.h - 1e-9, np.fmax(ev[["o", "h_pre", "p1545"]].max(axis=1), ev.p1545), ev.h)
    ev["lo"] = np.where(ev.l_post.fillna(1e18) <= ev.l + 1e-9, np.fmin(ev[["o", "l_pre", "p1545"]].min(axis=1), ev.p1545), ev.l)
    ev["vol1545"] = (ev.v - ev.v_post.fillna(0)).clip(lower=0)
    ev["rvol"] = ev.vol1545 / ev.avgv10
    ev["p1545_raw"] = ev.p1545 * ev.ratio
    rng = ev.hi - ev.lo
    ev["cir"] = np.where(rng > 0, 100 * (ev.p1545 - ev.lo) / rng, np.nan)
    ev["dv0"] = ev.vol1545 * ev.p1545_raw
    # shares outstanding and short interest
    sh = pd.read_parquet(os.path.join(SCACHE, "shares_out.parquet")).sort_values("end")
    sh["end"] = pd.to_datetime(sh.end)
    ev["dt"] = pd.to_datetime(ev.date)
    ev = pd.merge_asof(ev.sort_values("dt"), sh.rename(columns={"end": "sh_end"}), left_on="dt", right_on="sh_end",
                       by="symbol", direction="backward", tolerance=pd.Timedelta(days=400))
    ev["mcap"] = ev.p1545_raw * ev.shares
    si = pd.read_parquet(os.path.join(SCACHE, "finra_si.parquet"))
    si = si.rename(columns={"symbolCode": "symbol", "currentShortPositionQuantity": "shares_short", "daysToCoverQuantity": "dtc"})
    si["avail"] = pd.to_datetime(si.settlementDate) + pd.Timedelta(days=SI_LAG_DAYS)
    si = si[["symbol", "avail", "shares_short", "dtc"]].dropna(subset=["symbol"]).sort_values("avail")
    ev = pd.merge_asof(ev.sort_values("dt"), si, left_on="dt", right_on="avail", by="symbol",
                       direction="backward", tolerance=pd.Timedelta(days=45))
    ev["si_pct"] = 100 * ev.shares_short / ev.shares
    ev = ev.drop(columns=["dt"])
    # the penny scan: $1-$10 at 15:45, up on the day, top 20 by 15:45 change
    sc = ev[(ev.p1545_raw >= 1) & (ev.p1545_raw <= 10) & (ev.chg1545 > 0)].copy()
    sc["rank1545"] = sc.groupby("date").chg1545.rank(ascending=False, method="first")
    sc = sc[sc.rank1545 <= 20].copy()
    cg = pd.read_parquet(os.path.join(CACHE, "cand_gain.parquet"))
    cg = cg[cg.groupby("date").chg.rank(ascending=False, method="first") <= 25]
    nc = news_class("g", sc[["symbol", "date"]], lambda it: classify(it)[0], set(zip(cg.symbol, cg.date)))
    sc = sc.merge(nc.rename(columns={"cls": "cat_class"}), on=["symbol", "date"], how="left")
    cs = {"no_news": 0.75, "real_catalyst": 0.0, "dilution": 1.0, "hype": 0.9, "unclear": 0.5}
    sc["catalyst_score"] = sc.cat_class.map(cs)
    comps = []
    for r in sc.itertuples(index=False):
        row = {"change": 100 * r.chg1545, "rvol": r.rvol if np.isfinite(r.rvol) else None,
               "high": r.hi, "low": r.lo, "close": r.p1545,
               "si_pct_float": r.si_pct if pd.notna(r.si_pct) else None,
               "days_to_cover": r.dtc if pd.notna(r.dtc) else None, "ftd_days": None,
               "float_shares": r.shares if pd.notna(r.shares) else None,
               "catalyst": r.cat_class, "catalyst_score": r.catalyst_score if pd.notna(r.catalyst_score) else None}
        scoring.score_row(row)
        comps.append({"short_score": row["short_score"], "exclude_bot": row["exclude_reason"] or ""})
    sc = pd.concat([sc.reset_index(drop=True), pd.DataFrame(comps)], axis=1)
    for c in ("cat_class", "exclude_bot"):
        sc[c] = sc[c].astype("string")
    sc.to_parquet(os.path.join(CACHE, "pscan.parquet"))
    print("penny scan rows", len(sc), "days", sc.date.nunique(), "| no-minute %.3f | x1555 known %.3f | news known %.3f"
          % (sc.no_minute.mean(), sc.x1555.notna().mean(), sc.cat_class.notna().mean()))
    print(sc.cat_class.value_counts(dropna=False).to_dict())


if __name__ == "__main__":
    main()
