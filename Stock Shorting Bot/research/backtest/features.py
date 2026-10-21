"""Reconstruct the 15:45 scan for each day and attach every checklist input available historically.

Output .cache/scan_top20.parquet (stage 1, before news) and later .cache/events.parquet
(stage 2, with catalyst + bot score). Run:  features.py scan   then   features.py score
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BOT_ROOT, CACHE  # noqa: E402

sys.path.insert(0, BOT_ROOT)
from shortingbot import config as botcfg  # noqa: E402
from shortingbot import scoring  # noqa: E402

SI_LAG_DAYS = 12        # FINRA publishes ~7 business days after settlement
FTD_LAG_DAYS = 15       # SEC posts each half-month file ~2 weeks after it ends


def minute_prices(pool: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, date): 15:45 price, 15:55 entry, partial-day volume/high/low adjustments."""
    rows = []
    for d, g in pool.groupby("date"):
        p = os.path.join(CACHE, "minute", f"{d}.parquet")
        if not os.path.exists(p):
            continue
        m = pd.read_parquet(p)
        m = m[m.symbol.isin(set(g.symbol))]
        for s, b in m.groupby("symbol"):
            pre = b[b.mtc >= 16]
            post = b[b.mtc <= 15]
            at5 = b[b.mtc == 5]
            if len(at5):
                ent = at5.o.iloc[0]
            elif len(b[b.mtc < 5]):
                ent = b[b.mtc < 5].sort_values("mtc", ascending=False).o.iloc[0]
            else:
                ent = b[b.mtc > 5].sort_values("mtc").c.iloc[0]
            rows.append((s, d,
                         pre.sort_values("mtc").c.iloc[0] if len(pre) else np.nan,
                         ent, post.v.sum(), post.h.max() if len(post) else np.nan,
                         post.l.min() if len(post) else np.nan,
                         pre.h.max() if len(pre) else np.nan, pre.l.min() if len(pre) else np.nan,
                         b[b.mtc <= 5].h.max() if len(b[b.mtc <= 5]) else np.nan))
    return pd.DataFrame(rows, columns=["symbol", "date", "p1545", "p1555", "v_post", "h_post", "l_post",
                                       "h_pre", "l_pre", "h_last5"])


def exit_prices(top: pd.DataFrame) -> pd.DataFrame:
    """Price at close-5m on the NEXT trading day (the rotation that covers the short)."""
    cal = list(pd.read_csv(os.path.join(CACHE, "calendar.csv")).date)
    nxt = dict(zip(cal[:-1], cal[1:]))
    rows = []
    for d, g in top.groupby("date"):
        p = os.path.join(CACHE, "minute", f"{nxt.get(d)}.parquet")
        if not os.path.exists(p):
            continue
        m = pd.read_parquet(p)
        m = m[m.symbol.isin(set(g.symbol))]
        for s, b in m.groupby("symbol"):
            at5 = b[b.mtc == 5]
            if len(at5):
                x = at5.o.iloc[0]
            elif len(b[b.mtc < 5]):
                x = b[b.mtc < 5].sort_values("mtc", ascending=False).o.iloc[0]
            else:
                x = b.sort_values("mtc").c.iloc[0]
            rows.append((s, d, x))
    return pd.DataFrame(rows, columns=["symbol", "date", "x1555"])


def stage_scan():
    pool = pd.read_parquet(os.path.join(CACHE, "pool.parquet"))
    mp = minute_prices(pool)
    ev = pool.merge(mp, on=["symbol", "date"], how="left")
    ev["no_minute"] = ev.p1545.isna()
    ev["p1545"] = ev.p1545.fillna(ev.c)
    ev["chg1545"] = ev.p1545 / ev.pc - 1
    # high / low as of 15:45: the daily extreme unless it was printed after 15:45
    ev["hi"] = np.where(ev.h_post.fillna(-1) >= ev.h - 1e-9, np.fmax(ev[["o", "h_pre", "p1545"]].max(axis=1), ev.p1545), ev.h)
    ev["lo"] = np.where(ev.l_post.fillna(1e18) <= ev.l + 1e-9, np.fmin(ev[["o", "l_pre", "p1545"]].min(axis=1), ev.p1545), ev.l)
    ev["vol1545"] = (ev.v - ev.v_post.fillna(0)).clip(lower=0)
    ev["rvol"] = ev.vol1545 / ev.avgv10
    ev["adj_ratio"] = ev.c_raw / ev.c
    ev["p1545_raw"] = ev.p1545 * ev.adj_ratio

    # ---- market cap proxy: raw price x latest SEC shares outstanding (cover-page date <= T)
    sh = pd.read_parquet(os.path.join(CACHE, "shares_out.parquet")).sort_values("end")
    sh["end"] = pd.to_datetime(sh.end)
    ev["dt"] = pd.to_datetime(ev.date)
    ev = ev.sort_values("dt")
    ev = pd.merge_asof(ev, sh.rename(columns={"end": "sh_end"}), left_on="dt", right_on="sh_end",
                       by="symbol", direction="backward", tolerance=pd.Timedelta(days=400))
    ev["mcap"] = ev.p1545_raw * ev.shares
    # ---- short interest (FINRA), known SI_LAG_DAYS after settlement
    si = pd.read_parquet(os.path.join(CACHE, "finra_si.parquet"))
    si = si.rename(columns={"symbolCode": "symbol", "currentShortPositionQuantity": "shares_short",
                            "daysToCoverQuantity": "dtc"})
    si["avail"] = pd.to_datetime(si.settlementDate) + pd.Timedelta(days=SI_LAG_DAYS)
    si = si[["symbol", "avail", "settlementDate", "shares_short", "dtc"]].dropna(subset=["symbol"]).sort_values("avail")
    ev = pd.merge_asof(ev.sort_values("dt"), si, left_on="dt", right_on="avail", by="symbol",
                       direction="backward", tolerance=pd.Timedelta(days=45))
    ev["si_pct"] = 100 * ev.shares_short / ev.shares
    ev["si_age"] = (ev.dt - pd.to_datetime(ev.settlementDate)).dt.days
    # ---- fails to deliver: 4 latest half-month files available at T
    ftd = pd.read_parquet(os.path.join(CACHE, "ftd.parquet"))
    agg = ftd.groupby(["period", "symbol"]).qty.agg(["count", "max"]).reset_index()
    periods = sorted(agg.period.unique())
    pend = {}
    for p in periods:
        y, m, half = int(p[:4]), int(p[4:6]), p[6]
        end = pd.Timestamp(y, m, 15) if half == "a" else pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)
        pend[p] = end + pd.Timedelta(days=FTD_LAG_DAYS)
    agg_idx = agg.set_index(["period", "symbol"])
    out_days, out_max, out_have = [], [], []
    avail_sorted = sorted(periods, key=lambda p: pend[p])
    for dtv, s in zip(ev.dt, ev.symbol):
        av = [p for p in avail_sorted if pend[p] <= dtv][-4:]
        if len(av) < 2:
            out_days.append(np.nan); out_max.append(np.nan); out_have.append(False); continue
        cnt, mx = 0, np.nan
        for p in av:
            if (p, s) in agg_idx.index:
                r = agg_idx.loc[(p, s)]
                cnt += r["count"]; mx = np.nanmax([mx, r["max"]])
        out_days.append(cnt); out_max.append(mx); out_have.append(True)
    ev["ftd_days"], ev["ftd_max"], ev["ftd_have"] = out_days, out_max, out_have

    # ---- the live screen: prev close > $3, market cap > $25M (proxy), up on the day, top 20 by change
    u = pd.read_csv(os.path.join(CACHE, "universe.csv"))[["symbol", "status", "shortable", "easy_to_borrow"]]
    ev = ev.merge(u.rename(columns={"shortable": "shortable_now", "easy_to_borrow": "etb_now"}), on="symbol", how="left")
    cap_ok = np.where(ev.mcap.notna(), ev.mcap > botcfg.MIN_MARKET_CAP, ev.adv20 >= 500_000)
    ev["screen"] = (ev.pc_raw > botcfg.MIN_PREV_CLOSE) & cap_ok & (ev.chg1545 > 0)
    sc = ev[ev.screen].copy()
    sc["rank1545"] = sc.groupby("date").chg1545.rank(ascending=False, method="first")
    sc["rank_close"] = sc.groupby("date").chg.rank(ascending=False, method="first")
    sc.to_parquet(os.path.join(CACHE, "scan_all.parquet"))
    top = sc[sc.rank1545 <= botcfg.TOP_N].copy()
    top = top.merge(exit_prices(top), on=["symbol", "date"], how="left")
    top.drop(columns=["dt"]).to_parquet(os.path.join(CACHE, "scan_top20.parquet"))
    ov = sc.groupby("date").apply(lambda g: len(set(g[g.rank1545 <= 20].symbol) & set(g[g.rank_close <= 20].symbol)) / max(1, min(20, len(g))))
    print("scan rows", len(top), "days", top.date.nunique(), "| top-20 overlap 15:45 vs close: mean %.3f" % ov.mean())
    print("mcap known %.2f | SI known %.2f | FTD known %.2f | no minute %.3f" % (
        top.mcap.notna().mean(), top.dtc.notna().mean(), top.ftd_have.mean(), top.no_minute.mean()))


def stage_score():
    top = pd.read_parquet(os.path.join(CACHE, "scan_top20.parquet"))
    cat = pd.read_parquet(os.path.join(CACHE, "catalyst.parquet"))
    ev = top.merge(cat.rename(columns={"catalyst": "cat_class"}), on=["symbol", "date"], how="left")
    comps = []
    for r in ev.itertuples(index=False):
        row = {"change": 100 * r.chg1545, "rvol": r.rvol if np.isfinite(r.rvol) else None,
               "high": r.hi, "low": r.lo, "close": r.p1545,
               "si_pct_float": r.si_pct if pd.notna(r.si_pct) else None,
               "days_to_cover": r.dtc if pd.notna(r.dtc) else None,
               "ftd_days": int(r.ftd_days) if r.ftd_have else None,
               "ftd_max_shares": r.ftd_max if pd.notna(r.ftd_max) else None,
               "float_shares": r.shares if pd.notna(r.shares) else None,
               "catalyst": r.cat_class, "catalyst_score": r.catalyst_score}
        scoring.score_row(row)
        mv_parts = {}
        rng = r.hi - r.lo
        comps.append({**row["components"], "short_score": row["short_score"], "coverage": row["coverage"],
                      "exclude_bot": row["exclude_reason"],
                      "wick": (r.hi - r.p1545) / rng if rng > 0 else np.nan,
                      "cir": 100 * (r.p1545 - r.lo) / rng if rng > 0 else np.nan})
    ev = pd.concat([ev.reset_index(drop=True), pd.DataFrame(comps)], axis=1)
    ev.to_parquet(os.path.join(CACHE, "events.parquet"))
    print(len(ev), ev.exclude_bot.value_counts(dropna=False).to_dict())


if __name__ == "__main__":
    {"scan": stage_scan, "score": stage_score}[sys.argv[1]]()
