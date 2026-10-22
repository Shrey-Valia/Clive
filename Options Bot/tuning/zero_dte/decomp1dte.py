"""Test 4: decompose the earlier 1DTE iron-condor result (tuning/events/trades_1dte.csv).

Part A (no new data): the headline -3.8% is pooled SPY+QQQ at the 1x cost of that engine
(max($0.03, 2% of price) per leg at entry, settle at intrinsic). Split by ticker, cost, event.
Part B (1-minute bars, SPY Z1_ic_w5): split the zero-cost P&L into
  overnight  = credit(T close) - V(T+1 09:45)
  intraday   = V(T+1 09:45) - V(T+1 15:25)
  last 35min = V(T+1 15:25) - intrinsic at the T+1 close
and into put side vs call side. V uses the last 1-min print at/before the checkpoint,
floored at intrinsic (SPY minute close); entry uses the T 15:45-16:00 last print.
Outputs: trades_1dte_decomp.csv, decomp_summary.csv, out/decomp_1dte.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zdata as z  # noqa: E402
from analyze0 import stats, style, OUT, AMBER, GREEN, RED, WHITE  # noqa: E402
from fetch import NFP  # noqa: E402
from fetch1dte import trades  # noqa: E402

NFPS = set(NFP)


def part_a():
    t = pd.read_csv(HERE.parent / "events" / "trades_1dte.csv")
    t = t[t.ok == True].copy()  # noqa: E712
    t["date"] = t.D
    t["front_nfp"] = t.front.isin(NFPS)
    t["ev"] = np.where(t.event.fillna("") != "", t.event.fillna(""), np.where(t.front_nfp, "NFP", "none"))
    rows = []
    for v in ("Z1_ic_w5", "Z1_ic_w2x", "Z1_fly_w1x", "Z1_straddle"):
        for tk in ("SPY", "QQQ", "both"):
            for sl in (0.0, 1.0, 2.0):
                for ev in ("all", "none", "FOMC", "CPI", "NFP"):
                    c = t[(t.variant == v) & (t.slip_mult == sl)]
                    if tk != "both":
                        c = c[c.ticker == tk]
                    if ev != "all":
                        c = c[c.ev == ev]
                    rows.append({"part": "A", "variant": v, "ticker": tk, "slip": sl, "event": ev, **stats(c)})
    return pd.DataFrame(rows)


def leg_series(bars):
    if not bars:
        return pd.Series(dtype=float)
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df.t, utc=True).dt.tz_convert("America/New_York")
    return df.set_index("t").c.sort_index()


def last_at(s: pd.Series, t0, t1):
    w = s[(s.index >= t0) & (s.index <= t1)]
    return (float(w.iloc[-1]), (t1 - w.index[-1]).total_seconds() / 60) if len(w) else (np.nan, np.nan)


def part_b():
    spy = z.spy_all()
    t = trades()
    out = []
    for _, r in t.iterrows():
        f = z.CACHE / "d1" / f"{r.D}.json"
        if not f.exists():
            continue
        js = json.loads(f.read_text())
        sp, lp, sc, lc = [float(x) for x in r.strikes.split("/")]
        K = {"sp": sp, "lp": lp, "sc": sc, "lc": lc}
        cp = {"sp": "P", "lp": "P", "sc": "C", "lc": "C"}
        q = {"sp": -1, "lp": 1, "sc": -1, "lc": 1}
        T, T1 = pd.Timestamp(r.D), pd.Timestamp(r.front)
        tz = "America/New_York"
        ck = {"entry": (T.tz_localize(tz) + pd.Timedelta("15:45:00"), T.tz_localize(tz) + pd.Timedelta("15:59:00")),
              "open": (T1.tz_localize(tz) + pd.Timedelta("09:30:00"), T1.tz_localize(tz) + pd.Timedelta("09:45:00")),
              "x1525": (T1.tz_localize(tz) + pd.Timedelta("09:30:00"), T1.tz_localize(tz) + pd.Timedelta("15:25:00"))}
        day1 = spy.loc[r.front].between_time("09:30", "15:59")
        if len(day1) == 0:
            continue
        S_at = {"open": float(day1.between_time("09:30", "09:45").c.iloc[-1]),
                "x1525": float(day1.between_time("09:30", "15:25").c.iloc[-1])}
        S_close = float(day1.c.iloc[-1])
        S_T = float(spy.loc[r.D].between_time("09:30", "15:59").c.iloc[-1])
        val = {k: {} for k in ("entry", "open", "x1525", "settle")}
        ages = {}
        for leg in K:
            s = leg_series(js.get(leg))
            for c, (a, b) in ck.items():
                p, age = last_at(s, a, b)
                if c != "entry":
                    S = S_at[c]
                    intr = max(S - K[leg], 0) if cp[leg] == "C" else max(K[leg] - S, 0)
                    p = intr if not np.isfinite(p) else max(p, intr)
                val[c][leg] = p
                ages[(c, leg)] = age
            val["settle"][leg] = max(S_close - K[leg], 0) if cp[leg] == "C" else max(K[leg] - S_close, 0)
        if not all(np.isfinite(val["entry"][k]) for k in K):
            # fall back to the daily-bar credit used by the original backtest
            ent_ok = False
        else:
            ent_ok = True

        def side(c, legs):
            return sum(-q[k] * val[c][k] for k in legs)       # value owed on short side (+ = credit)
        row = {"date": r.D, "front": r.front, "event": r.event if isinstance(r.event, str) else "",
               "nfp": r.front in NFPS, "max_risk": r.max_risk, "credit_daily": r.credit,
               "pnl_daily_mid": r.pnl_mid, "entry_from_minute": ent_ok,
               "on_move": S_at["open"] / S_T - 1, "id_move": S_close / S_at["open"] - 1,
               "im": r.im, "max_entry_age": max(ages[("entry", k)] for k in K) if ent_ok else np.nan}
        for nm, legs in (("put", ("sp", "lp")), ("call", ("sc", "lc")), ("all", tuple(K))):
            c0 = side("entry", legs) if ent_ok else np.nan
            v_o, v_x, v_s = side("open", legs), side("x1525", legs), side("settle", legs)
            row[f"{nm}_credit"] = c0
            row[f"{nm}_overnight"] = c0 - v_o
            row[f"{nm}_intraday"] = v_o - v_x
            row[f"{nm}_last35"] = v_x - v_s
            row[f"{nm}_total"] = c0 - v_s
        out.append(row)
    d = pd.DataFrame(out)
    d.to_csv(HERE / "trades_1dte_decomp.csv", index=False)
    return d


def summarize_b(d):
    d = d[d.entry_from_minute].copy()
    d["evtag"] = np.where(d.event != "", d.event, np.where(d.nfp, "NFP", "none"))
    rows = []
    comps = [f"{s}_{c}" for s in ("all", "put", "call") for c in ("overnight", "intraday", "last35", "total")]
    for ev in ("all", "none", "FOMC", "CPI", "NFP"):
        for per in ("ALL", "IS", "OOS"):
            c = d if ev == "all" else d[d.evtag == ev]
            if per != "ALL":
                c = c[(c.date >= "2025-07-01") == (per == "OOS")]
            for comp in comps:
                c2 = c.assign(ret=c[comp] / c.max_risk)
                rows.append({"part": "B", "variant": "Z1_ic_w5", "ticker": "SPY", "slip": 0.0, "event": ev,
                             "period": per, "component": comp, **stats(c2)})
    return pd.DataFrame(rows)


def chart(sb):
    plt = style()
    s = sb[(sb.event == "all") & (sb.period == "ALL")].set_index("component")
    fig, ax = plt.subplots(figsize=(10, 5))
    comps = ["overnight", "intraday", "last35", "total"]
    xs = np.arange(len(comps))
    for off, side, col in ((-0.27, "put", RED), (0.0, "call", GREEN), (0.27, "all", AMBER)):
        m = [s.loc[f"{side}_{c}", "mean"] * 100 for c in comps]
        lo = [(s.loc[f"{side}_{c}", "mean"] - s.loc[f"{side}_{c}", "ci_lo"]) * 100 for c in comps]
        hi = [(s.loc[f"{side}_{c}", "ci_hi"] - s.loc[f"{side}_{c}", "mean"]) * 100 for c in comps]
        ax.bar(xs + off, m, 0.26, color=col, label="both sides" if side == "all" else f"{side} side")
        ax.errorbar(xs + off, m, yerr=[lo, hi], fmt="none", ecolor=WHITE, capsize=3)
    ax.axhline(0, color=WHITE, lw=0.8)
    ax.set_xticks(xs, ["T close -> T+1 09:45", "09:45 -> 15:25", "15:25 -> settle", "total"])
    ax.set_ylabel("mean P&L, % of max risk (zero cost)")
    ax.set_title("SPY 1DTE IRON CONDOR (+-1 IM, $5 WINGS): WHERE THE P&L COMES FROM")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "decomp_1dte.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    a = part_a()
    d = part_b()
    b = summarize_b(d)
    pd.concat([a, b], ignore_index=True).to_csv(HERE / "decomp_summary.csv", index=False)
    chart(b)
    print("part B trades", len(d), "with minute entry", int(d.entry_from_minute.sum()))
