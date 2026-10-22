"""Trade tables for every strategy family. Adjusted prices (adjustment=all) for returns,
raw prices only for $ filters and the spread bucket.

Delisting (literature agent, Shumway): when a long's exit bar does not exist because the
symbol's history ends, exit at the last close x (1 - 0.55) for NASDAQ names, x (1 - 0.30) for
NYSE/AMEX. Shorts cover at the last close (no windfall). Multi-day halts: the next available
bar is used (panel k-rows-ahead fields), which is what a trader would get.
Reverse splits: every penny rule excludes names with a reverse split in the prior 20 sessions.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from common import CACHE, LAST_DAY, SCACHE

CAL = list(pd.read_csv(os.path.join(SCACHE, "calendar.csv")).date)
CAL = [d for d in CAL if "2016-01-04" <= d <= LAST_DAY]
NEXT = {d: CAL[i + 1] for i, d in enumerate(CAL[:-1])}
PREV = {v: k for k, v in NEXT.items()}
IDX = {d: i for i, d in enumerate(CAL)}


def fwd_date(d, k):
    i = IDX.get(d)
    return CAL[i + k] if i is not None and i + k < len(CAL) else None


def bucket(px):
    return np.select([px < 5, px < 10], ["$1-5", "$5-10"], "$10+")


def sigma(atr):
    return np.clip(np.asarray(atr, float) * 0.8, 0.01, 0.5)


def delist_mult(x):
    ex = x["exchange"] if "exchange" in x else pd.Series("NASDAQ", index=x.index)
    return np.where(ex.fillna("NASDAQ").values == "NASDAQ", 0.45, 0.70)


def frame(sig_date, symbol, side, gross, px, adv, atr, hold, cal_days, rank, realise, sig_px=None, **extra):
    t = pd.DataFrame({"sig_date": np.asarray(sig_date), "entry_date": np.asarray(sig_date), "symbol": np.asarray(symbol), "side": side,
                      "gross": np.asarray(gross, float), "px": px, "adv": adv, "sig": sigma(atr), "hold": hold,
                      "cal_days": cal_days, "rank": np.asarray(rank), "date": np.asarray(realise, dtype=object),
                      **{k: np.asarray(v) for k, v in extra.items()}})
    t = t[np.isfinite(t.gross) & t.date.notna()]
    t["sig_px"] = t.px if sig_px is None else np.asarray(sig_px, float)[t.index]
    t["bucket"] = bucket(t.sig_px.values)
    return t.reset_index(drop=True)


# ------------------------------------------------------------------ data
def load_open30():
    """09:30-09:59 bars on T+1 for the T top-30 penny gainers, keyed by the signal date T."""
    d0 = os.path.join(CACHE, "minute_open30")
    if not os.path.isdir(d0):
        return None
    parts = []
    for f in sorted(os.listdir(d0)):
        m = pd.read_parquet(os.path.join(d0, f))
        if len(m):
            m["date"] = PREV.get(f[:10])
            parts.append(m)
    return pd.concat(parts, ignore_index=True) if parts else None


def load_scans():
    """Penny scan ($1-$10, my reconstruction) + the bot's scan for >=$10 names (shorting research)."""
    ps = pd.read_parquet(os.path.join(CACHE, "pscan.parquet"))
    ps["src"] = "penny"
    ev = pd.read_parquet(os.path.join(CACHE, "ev_prepped.parquet"))
    ev = ev[ev.p1545_raw >= 10].copy()
    ev["src"] = "bot"
    ev["exclude_bot"] = ev.exclude_bot.fillna("")
    ev["dv0"] = ev.vol1545 * ev.p1545_raw
    ev["n_news"] = ev.get("n_news", np.nan)
    cols = ["symbol", "date", "src", "o", "pc", "p1545", "p1555", "p1545_raw", "chg1545", "o1", "h1", "c1", "c2",
            "c3", "c5", "x1555", "adv20", "atr14", "dv_raw", "short_score", "exclude_bot", "cat_class", "c",
            "rank1545", "rvol", "cir", "dv0", "n_news"]
    s = pd.concat([ps[cols + ["n_after", "rs_recent20", "exchange"]], ev[cols]], ignore_index=True)
    s["n_after"] = s.n_after.fillna(9)
    s["rs_recent20"] = s.rs_recent20.fillna(False).astype(bool)
    s["excl"] = s.exclude_bot.fillna("").str.startswith("squeeze") | s.exclude_bot.fillna("").str.startswith("real")
    return s


def scan_filter(s, lo, hi, adv, min_score=None, excl=True):
    m = (s.p1545_raw >= lo) & (s.p1545_raw < hi) & (s.adv20 >= adv) & ~s.rs_recent20
    if min_score is not None:
        m &= s.short_score >= min_score
    if excl:
        m &= ~s.excl
    return s[m]


def exit_or_delist(x, col):
    return x[col].where(x.n_after >= int(col[-1]) if col[-1].isdigit() else x.n_after >= 1, x.c * delist_mult(x))


# ------------------------------------------------------------------ L1 overnight long
def tt_l1(s, lo=1, hi=10, adv=3e6, min_score=70, rank_by="short_score"):
    """Overnight long of the names the bot would short: buy 15:55 T, sell at the T+1 open."""
    x = scan_filter(s, lo, hi, adv, min_score)
    g = exit_or_delist(x, "o1") / x.p1555 - 1
    return frame(x.date.values, x.symbol.values, 1, g.values, x.p1545_raw.values, x.adv20.values, x.atr14.values,
                 1, 0, -x[rank_by].values, x.date.map(NEXT).values)


def nightowl_filter(s, lo, hi, dvmin, min_chg=0.20):
    x = s[(s.p1545_raw >= lo) & (s.p1545_raw < hi) & ~s.rs_recent20 & (s.chg1545 >= min_chg) & (s.rvol >= 3)
          & (s.cir >= 70) & (s.dv0 >= dvmin) & (s.cat_class.fillna("") != "dilution")]
    return x


def tt_nightowl(s, lo=1, hi=10, dvmin=5e6, exit="open", o30=None):
    """Literature rule set 1: +20% on the day, RVOL>=3, close in the top 30% of the range, no offering news,
    day dollar volume >= dvmin. Buy 15:55, exit at the T+1 open (A) or the 09:31-09:34 average (C)."""
    x = nightowl_filter(s, lo, hi, dvmin).copy()
    ex = exit_or_delist(x, "o1")
    if exit == "c0931" and o30 is not None:
        e = o30[(o30.mso >= 1) & (o30.mso <= 4)].groupby(["symbol", "date"]).c.mean().rename("e931")
        x = x.merge(e, left_on=["symbol", "date"], right_index=True, how="left")
        ex = x.e931.fillna(exit_or_delist(x, "o1"))
    g = ex / x.p1555 - 1
    return frame(x.date.values, x.symbol.values, 1, g.values, x.p1545_raw.values, x.adv20.values, x.atr14.values,
                 1, 0, -x.chg1545.values, x.date.map(NEXT).values)


# ------------------------------------------------------------------ S1 fade short
def tt_s1(s, lo=1, hi=10, adv=3e6, min_score=70, exit_col="c1"):
    """The v2 fade: short at the T+1 open, cover at the T+1 close (v2 backtest convention)."""
    x = scan_filter(s, lo, hi, adv, min_score)
    x = x[x.n_after >= 1]
    g = 1 - x[exit_col] / x.o1
    return frame(x.date.values, x.symbol.values, -1, g.values, x.p1545_raw.values, x.adv20.values, x.atr14.values,
                 1, 0, -x.short_score.values, x.date.map(NEXT).values)


def tt_s1h(s, o30, lo=5, hi=10, adv=3e6, min_score=70):
    """Literature rule set 2 (hardened fade): on T+1 short the next minute's open after a break of the
    5-minute opening-range low (09:35-09:59), skip if already <= -10% vs the prior close (SSR),
    stop = min(max(high so far, entry+3%), entry+8%), filled at the next bar's open if gapped;
    after 10:00 a T+1 high above the stop counts as stopped (at stop +1%). Cover at the close."""
    x = scan_filter(s, lo, hi, adv, min_score)
    x = x[(x.n_after >= 1)][["symbol", "date", "c", "c1", "h1", "p1545_raw", "adv20", "atr14", "short_score"]]
    m = o30[o30.set_index(["symbol", "date"]).index.isin(pd.MultiIndex.from_frame(x[["symbol", "date"]]))]
    rows = []
    for (sym, d), b in m.groupby(["symbol", "date"]):
        b = b.sort_values("mso")
        o5 = b[b.mso < 5]
        a = b[b.mso >= 5].reset_index(drop=True)
        if not len(o5) or len(a) < 2:
            continue
        orl, hod = o5.l.min(), o5.h.max()
        brk = a.index[a.l < orl]
        if not len(brk) or brk[0] + 1 >= len(a):
            continue
        i = brk[0] + 1
        ent = a.o[i]
        hod = max(hod, a.h[:i].max())
        stop = min(max(hod, ent * 1.03), ent * 1.08)
        hit = a[(a.index >= i) & (a.h >= stop)]
        wmax = b.h.max()
        if len(hit):
            j = hit.index[0]
            xp = max(stop, a.o[j]) if j > i else stop
            rows.append((sym, d, ent, xp, True, stop, wmax))
        else:
            rows.append((sym, d, ent, np.nan, False, stop, wmax))
    e = pd.DataFrame(rows, columns=["symbol", "date", "ent", "stop_x", "hit", "stop", "wmax"])
    x = x.merge(e, on=["symbol", "date"])
    x = x[x.ent > 0.9 * x.c]                                           # SSR: skip if already down 10%
    late = ~x.hit & (x.h1 > x.wmax * (1 + 1e-6)) & (x.h1 >= x.stop)    # new high after 10:00 through the stop
    ex = np.where(x.hit, x.stop_x, np.where(late, x.stop * 1.01, x.c1))
    g = 1 - ex / x.ent
    return frame(x.date.values, x.symbol.values, -1, g, x.p1545_raw.values, x.adv20.values, x.atr14.values,
                 1, 0, -x.short_score.values, x.date.map(NEXT).values)


# ------------------------------------------------------------------ L2 continuation
def tt_l2(s, lo=1, hi=10, adv=3e6, gap=0.0, hold=1):
    """Day-2 continuation (brief's version): T is a top gainer with real-catalyst news (the names the bot
    refuses to short); buy at the T+1 open if it gaps up by >= gap; exit at the close of T+hold."""
    x = s[(s.p1545_raw >= lo) & (s.p1545_raw < hi) & (s.adv20 >= adv) & (s.cat_class == "real_catalyst") & ~s.rs_recent20]
    x = x[x.n_after >= 1].copy()
    x = x[(x.o1 / x.c - 1) >= gap]
    ex = exit_or_delist(x, f"c{hold}")
    g = ex / x.o1 - 1
    real = [fwd_date(d, hold) for d in x.date]
    return frame(x.date.values, x.symbol.values, 1, g.values, x.p1545_raw.values * x.o1.values / x.p1545.values,
                 x.adv20.values, x.atr14.values, hold, 0, -x.chg1545.values, real, sig_px=x.p1545_raw.values)


def tt_l2b(s, news=True, hold=1, lo=3, hi=10):
    """Literature rule set 3: day-1 gap >= +10% at the open, close >= open and in the top 25% of the range,
    RVOL >= 5, $3-10; with company news that is not an offering (news=True) or no news at all (control).
    Buy at 15:55 on day 1, exit at the close of day 1+hold. Overnight holds (no day trade)."""
    x = s[(s.p1545_raw >= lo) & (s.p1545_raw < hi) & ~s.rs_recent20 & ((s.o / s.pc - 1) >= 0.10) & (s.p1545 >= s.o)
          & (s.cir >= 75) & (s.rvol >= 5) & (s.src == "penny")].copy()
    if news:
        x = x[x.cat_class.isin(["real_catalyst", "hype", "unclear"])]
    else:
        x = x[x.cat_class == "no_news"]
    ex = exit_or_delist(x, f"c{hold}")
    g = ex / x.p1555 - 1
    real = [fwd_date(d, hold) for d in x.date]
    return frame(x.date.values, x.symbol.values, 1, g.values, x.p1545_raw.values, x.adv20.values, x.atr14.values,
                 hold, 0, -x.rvol.values, real)


# ------------------------------------------------------------------ L3 reversal
def tt_l3(pp_l, k=3, hold=2, adv=3e6, news=True, lo=1, hi=10):
    """Short-term reversal (brief's version): rank by k-day return at the T close, buy the 5 worst at the
    T+1 open, exit at the close of T+hold (hold=1 is intraday). Excludes dilution/negative news in the 72h
    before the scan (unknown news = kept) and reverse splits in the prior 20 sessions."""
    x = pp_l[(pp_l.c_raw >= lo) & (pp_l.c_raw < hi) & (pp_l.adv20 >= adv) & ~pp_l.rs_recent20].copy()
    if news:
        x = x[~x.ncls.isin(["dilution", "negative"])]
    x = x[(x.n_after >= 1) & (x[f"r{k}"] < 0)]
    ex = exit_or_delist(x, f"c{hold}")
    g = ex / x.o1 - 1
    real = [fwd_date(d, hold) for d in x.date]
    return frame(x.date.values, x.symbol.values, 1, g.values, x.c_raw.values * x.o1.values / x.c.values,
                 x.adv20.values, x.atr14.values, hold, 0, x[f"r{k}"].values, real, sig_px=x.c_raw.values)


def tt_l3b(pp_l, lo=5, hi=10, hold=1):
    """Literature rule set 5 (cost-model control): day-0 return <= -15%, no headline in the prior 72h,
    $lo-hi at the close, day-0 dollar volume >= $5M; buy at the close, exit at the close of day hold."""
    x = pp_l[(pp_l.c_raw >= lo) & (pp_l.c_raw < hi) & ~pp_l.rs_recent20 & (pp_l.r1 <= -0.15)
             & (pp_l.ncls == "no_news") & (pp_l.c_raw * pp_l.v_raw >= 5e6)].copy()
    ex = exit_or_delist(x, f"c{hold}")
    g = ex / x.c - 1
    real = [fwd_date(d, hold) for d in x.date]
    return frame(x.date.values, x.symbol.values, 1, g.values, x.c_raw.values, x.adv20.values, x.atr14.values,
                 hold, 0, x.r1.values, real)


# ------------------------------------------------------------------ L4 ORB
OR_SHARE = {5: 0.06, 15: 0.12, 30: 0.18}   # typical share of daily volume traded in the first N minutes


def orb_events(pp):
    """Per (symbol, date, N) long-ORB facts from the 09:30-10:59 minute bars. Entry = next minute's open
    after the first bar whose high breaks the range high (literature fill rule). Two stops:
    'orl' = range low, 'atr10' = entry - 10% of ATR14 (in price). Stop fills at the next bar's open after the
    trigger bar (or the stop if that open is better). Hits after 10:59 are inferred from the daily low."""
    d0 = os.path.join(CACHE, "minute_orb")
    atr = pp.set_index(["symbol", "date"]).atr14
    rows = []
    for f in sorted(os.listdir(d0)):
        d = f[:10]
        m = pd.read_parquet(os.path.join(d0, f))
        if m.empty:
            continue
        for s, b in m.groupby("symbol"):
            b = b.sort_values("mso").reset_index(drop=True)
            a14 = atr.get((s, d), np.nan)
            for N in (5, 15, 30):
                o = b[b.mso < N]
                a = b[b.mso >= N].reset_index(drop=True)
                if len(o) == 0 or len(a) < 2:
                    continue
                orh, orl = o.h.max(), o.l.min()
                green = o.c.iloc[-1] > o.o.iloc[0]
                brk = a.index[a.h > orh]
                base = (s, d, N, orh, orl, o.v.sum(), green, o.o.iloc[0])
                if not len(brk) or brk[0] + 1 >= len(a):
                    rows.append(base + (np.nan,) * 5); continue
                i = brk[0] + 1
                ent = a.o[i]
                res = []
                for lvl in (orl, ent * (1 - 0.10 * a14) if np.isfinite(a14) else np.nan):
                    if not np.isfinite(lvl) or ent <= lvl:
                        res += [lvl, np.nan]; continue
                    hit = a.index[(a.index >= i) & (a.l <= lvl)]
                    if len(hit):
                        j = hit[0]
                        xp = min(lvl, a.o[j + 1]) if j + 1 < len(a) else lvl
                        res += [lvl, xp]
                    else:
                        res += [lvl, np.nan]
                rows.append(base + (ent, *res))
    return pd.DataFrame(rows, columns=["symbol", "date", "N", "orh", "orl", "vol_or", "green", "or_open", "ent",
                                       "lvl_orl", "x_orl", "lvl_atr", "x_atr"])


def tt_l4(oe, pp, N=5, stop="orl", adv=1e6, min_rvol=1.0, top=20):
    """Literature rule set 4 (long side): open $2-10, avg volume >= 1M sh, ATR14 >= 5% of price, first bar green,
    stocks in play = opening-N-minute volume / (avg daily volume x typical opening share) >= min_rvol, top 20."""
    x = oe[oe.N == N].merge(pp[["symbol", "date", "l", "c", "ratio", "adv20", "atr14", "avgv10", "o_raw", "exchange"]],
                            on=["symbol", "date"], how="left")
    x["rvol"] = x.vol_or / (x.avgv10 * OR_SHARE[N])
    x = x[(x.avgv10 >= 1e6) & (x.atr14 >= 0.05) & (x.adv20 >= adv) & (x.o_raw >= 2) & (x.o_raw < 10) & x.green
          & (x.rvol >= min_rvol)]
    x = x[x.groupby("date").rvol.rank(ascending=False, method="first") <= top]
    x = x[x.ent.notna()]
    lvl, xw = x[f"lvl_{stop}"], x[f"x_{stop}"]
    late = xw.isna() & (x.l < lvl * (1 - 1e-6))
    ex = np.where(xw.notna(), xw, np.where(late, lvl * 0.995, x.c))
    g = ex / x.ent - 1
    return frame(x.date.values, x.symbol.values, 1, g, x.ent.values * x.ratio.values, x.adv20.values,
                 x.atr14.values, 1, 0, -x.rvol.values, x.date.values, sig_px=x.o_raw.values,
                 stopped=(xw.notna() | late).values)
