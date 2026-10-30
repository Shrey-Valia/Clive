"""Minute-bar simulator for the T+1 intraday short (and the overnight long sleeve).

A Rule says how one position is entered and exited on T+1. trade_returns(rule) gives one
row per strict-gate event: entry/exit price and minute, exit reason, gross and net short return.
A Book says which events are traded each day and how they are weighted; book() turns
trade returns into a daily equity series. Every Rule/Book evaluated through evaluate() is
logged as a trial for the deflated Sharpe.

Fill conventions (all conservative for the short):
  entry "open"   official opening print (daily bar open, the opening auction)
  entry "mK"     VWAP of the minute bar starting at 09:30+K (a market order sent at 09:3K)
  entry "orbK"   sell-stop at the low of the first K minutes: first later bar whose low breaks it,
                 filled at min(bar open, level); none after `entry_cutoff`
  stop/trail     buy-stop: a bar opening through the level fills at that open (gap / LULD halt),
                 else at the level; plus STOP_SLIP adverse slippage. Checked from the bar after entry
                 (from bar 0 for the open print). Stop beats target when both hit in one bar.
  target         buy-limit at entry x (1 - target), filled at the level
  vwap exit      after `vwap_after` minutes in the trade, the first bar CLOSING above session VWAP
                 (from 09:30) -> cover at the next bar's open
  time exit      open of bar `time_exit` (385 = 15:55); half days shift to close-5m
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd
from scipy import stats as sst

from r3common import IS_END, OOS_START

STOP_SLIP = 0.005
EQUITY_USD = 100_000


@dataclass(frozen=True)
class Rule:
    name: str = "v2_live"
    entry: str = "m1"              # open | mK | orbK
    entry_cutoff: int = 90         # orb: no entry after this minute (11:00)
    max_gap_up: float | None = 0.40     # skip if entry-reference price / 15:45 price - 1 > this
    min_gap: float | None = None        # skip if that gap is below this (e.g. -0.10 = gapped down > 10%)
    gap_ref: str = "open"          # open (official open, as live: price at the open job) | pm (last premarket)
    stop: float | None = 0.35
    trail: float | None = None
    trail_arm: float = 0.0         # trail only after the trade is this far in profit
    target: float | None = None
    time_exit: int = 385
    vwap_exit: bool = False
    vwap_after: int = 30
    checkpoint: tuple | None = None     # (minute, pct): at that bar's open, cover if price > entry x (1 + pct)
    cost_mult: float = 1.0
    stop_slip: float = STOP_SLIP


@dataclass(frozen=True)
class Book:
    name: str = "v2"
    score: str = "short_score"
    min_score: float | None = 70.0
    n: int = 5
    w: float = 0.05                # weight per name (fraction of equity)
    sizing: str = "equal"          # equal | invvol | score
    gross_cap: float = 0.50
    use_excl: bool = True
    filters: tuple = ()            # (col, op, value) on the scan event (decision-time features only)
    regime: tuple = ()             # same, applied to day-level columns


# ------------------------------------------------------------------ costs
def tod_mult(minute: np.ndarray, kind: str) -> np.ndarray:
    """Spread multiplier vs the round-1 base cost by time of day (calibrated in costs.py)."""
    m = np.asarray(minute, float)
    mult = np.select([m < 1, m < 5, m < 15, m < 30, m < 60], TOD[:5], TOD[5])
    if kind == "auction":
        mult = np.where(m < 0.5, TOD_AUCTION, mult)
    return mult


TOD = [2.4, 2.0, 1.6, 1.3, 1.15, 1.0]   # 09:30, 09:31-34, 09:35-44, 09:45-59, 10:00-29, 10:30+ (overwritten by costs.json)
TOD_AUCTION = 1.0                       # opening-auction print: no spread crossed, base cost only
try:
    import os
    from r3common import RES
    _c = json.load(open(os.path.join(RES, "costs.json")))
    TOD, TOD_AUCTION = _c["tod"], _c["auction"]
except Exception:  # noqa: BLE001
    pass


def base_cost(price_raw, adv, chg):
    """Round-1 one-way cost (engine.one_way_cost), in fraction of notional."""
    bps = np.select([price_raw < 5, price_raw < 10, price_raw < 20, price_raw < 50], [30, 20, 12, 8], 5).astype(float)
    bps += np.select([adv < 2e6, adv < 10e6], [20, 10], 0)
    bps += np.where(chg > 0.5, 10, 0)
    return bps / 1e4


FEE_SELL = 0.3e-4      # SEC Section 31 (~$27.80/M) + FINRA TAF, on the sell side only (state: approx.)


# ------------------------------------------------------------------ trade engine
def trade_returns(ev: pd.DataFrame, B: dict, rule: Rule) -> pd.DataFrame:
    O, H, L, C, V, VW = (B[k] for k in ("O", "H", "L", "C", "V", "VW"))
    n, T = O.shape
    idx = np.arange(n)
    o1 = ev.o1.values
    p1545 = ev.p1545.values
    cm = ev.close1_min.values.astype(int)          # 390 on a normal day, 210 on a half day
    # time_exit 385 = close-5m (half days 12:55); 390 = the official close (MOC); < 385 = that minute
    last_bar = np.where(rule.time_exit >= 385, cm - (390 - rule.time_exit), np.minimum(rule.time_exit, cm - 5))

    # ---- entry
    ent = np.full(n, np.nan)
    te = np.full(n, -1, int)
    first_check = np.zeros(n, int)
    if rule.entry == "open":
        ent = o1.copy(); te[:] = 0; first_check[:] = 0
    elif rule.entry.startswith("m"):
        k = int(rule.entry[1:])
        ent = VW[:, k].astype(float)
        bad = ~np.isfinite(ent)
        if bad.any():   # no trade in that minute: next bar with a print, up to 5 minutes later
            for kk in range(k + 1, k + 6):
                fill = bad & np.isfinite(VW[:, kk])
                ent[fill] = VW[fill, kk]; te[fill] = kk; bad &= ~fill
        te[np.isfinite(ent) & (te < 0)] = k
        first_check = te + 1
    elif rule.entry.startswith("orb"):
        k = int(rule.entry[3:])
        with np.errstate(all="ignore"):
            orl = np.where(np.isfinite(L[:, :k]).any(axis=1), np.nanmin(np.where(np.isfinite(L[:, :k]), L[:, :k], np.inf), axis=1), np.nan)
        done = np.zeros(n, bool)
        for j in range(k, min(rule.entry_cutoff + 1, T)):
            hit = ~done & np.isfinite(L[:, j]) & (L[:, j] < orl)
            px = np.fmin(O[:, j], orl)
            ent[hit] = px[hit]; te[hit] = j; done |= hit
        first_check = te + 1
    else:
        raise ValueError(rule.entry)

    # ---- gap filters (reference known at entry decision)
    ref = o1 if rule.gap_ref == "open" else ev.pm_last.values
    gap = ref / p1545 - 1
    ok = np.isfinite(ent) & (ent > 0) & ev.has_day1.values & np.isfinite(o1)
    if rule.max_gap_up is not None:
        ok &= ~(gap > rule.max_gap_up)
    if rule.min_gap is not None:
        ok &= ~(gap < rule.min_gap)

    # ---- walk the bars
    ex = np.full(n, np.nan)
    tx = np.full(n, -1, int)
    why = np.full(n, "", object)
    live = ok.copy()
    minlow = ent.copy()
    cum_pv = np.zeros(n)
    cum_v = np.zeros(n)
    pending_vwap = np.zeros(n, bool)
    for j in range(T):
        vj = np.nan_to_num(V[:, j]); pj = np.nan_to_num(VW[:, j])
        cum_pv += pj * vj; cum_v += vj
        act = live & (j >= first_check) & (j < last_bar)
        o, h, l, c = O[:, j], H[:, j], L[:, j], C[:, j]
        has = act & np.isfinite(o)
        # pending VWAP-reclaim exit: cover at this bar's open
        if rule.vwap_exit:
            hit = has & pending_vwap
            ex[hit] = o[hit]; tx[hit] = j; why[hit] = "vwap"; live &= ~hit; has &= ~hit
        if rule.checkpoint is not None and j == rule.checkpoint[0]:
            hit = has & (o > ent * (1 + rule.checkpoint[1]))
            ex[hit] = o[hit]; tx[hit] = j; why[hit] = "checkpoint"; live &= ~hit; has &= ~hit
        hard = ent * (1 + rule.stop) if rule.stop is not None else np.full(n, np.inf)
        trl = np.full(n, np.inf)
        if rule.trail is not None:
            armed = minlow <= ent * (1 - rule.trail_arm)
            trl = np.where(armed, minlow * (1 + rule.trail), np.inf)
        lvl = np.minimum(hard, trl)
        gap_hit = has & (o >= lvl)
        bar_hit = has & ~gap_hit & (h >= lvl)
        px = np.where(gap_hit, o, lvl) * (1 + rule.stop_slip)
        hit = gap_hit | bar_hit
        ex[hit] = px[hit]; tx[hit] = j
        why[hit] = np.where(trl[hit] < hard[hit], "trail", "stop")
        live &= ~hit; has &= ~hit
        if rule.target is not None:
            tl = ent * (1 - rule.target)
            thit = has & (l <= tl)
            ex[thit] = np.fmin(o, tl)[thit]; tx[thit] = j; why[thit] = "target"
            live &= ~thit; has &= ~thit
        if rule.vwap_exit:
            vwap = np.where(cum_v > 0, cum_pv / np.maximum(cum_v, 1e-9), np.nan)
            pending_vwap = has & (j >= te + rule.vwap_after) & (c > vwap)
        minlow = np.where(has, np.fmin(minlow, l), minlow)
    # ---- time exit at bar last_bar's open (or the last print before it)
    rem = live
    moc = rem & (last_bar >= cm)
    if moc.any():
        ex[moc] = ev.c1.values[moc]; tx[moc] = cm[moc]; why[moc] = "close"
        rem = rem & ~moc
    if rem.any():
        lb = last_bar
        xo = O[idx, np.clip(lb, 0, T - 1)].astype(float)
        miss = rem & ~np.isfinite(xo)
        if miss.any():
            for i in np.where(miss)[0]:
                cc = C[i, :lb[i]]
                f = np.where(np.isfinite(cc))[0]
                xo[i] = cc[f[-1]] if len(f) else np.nan
        ex[rem] = xo[rem]; tx[rem] = lb[rem]; why[rem] = "time"
    ok &= np.isfinite(ex)

    gross = 1 - ex / ent
    bc = base_cost(ev.p1545_raw.values, ev.adv20.values, ev.chg1545.values)
    kind_in = "auction" if rule.entry == "open" else "market"
    c_in = bc * tod_mult(np.maximum(te, 0) + (0 if rule.entry == "open" else 1), kind_in) * rule.cost_mult + FEE_SELL
    c_out = bc * np.where(why == "close", TOD_AUCTION, tod_mult(np.maximum(tx, 0) + 1, "market")) * rule.cost_mult * (ex / ent)
    htb = (ev.si_pct.fillna(0).values > 15) | (ev.dtc.fillna(0).values > 5)
    borrow = np.where(htb, 0.50, 0.15) * 0.3 / 360 * rule.cost_mult       # intraday: little or no fee
    net = gross - c_in - c_out - borrow
    out = pd.DataFrame({"i": idx, "date": ev.date.values, "symbol": ev.symbol.values, "ok": ok,
                        "ent": ent, "ex": ex, "te": te, "tx": tx, "why": why, "gap": gap,
                        "gross": gross, "cost": c_in + c_out + borrow, "net": net})
    return out


# ------------------------------------------------------------------ book
OPS = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal, "==": np.equal, "!=": np.not_equal}


def eligible(ev: pd.DataFrame, bk: Book) -> np.ndarray:
    m = np.ones(len(ev), bool)
    if bk.use_excl:
        ex = ev.exclude_bot.fillna("")
        m &= ~ex.str.startswith("squeeze").values & ~ex.str.startswith("real").values
    if bk.min_score is not None:
        m &= ev[bk.score].values >= bk.min_score
    for col, op, val in tuple(bk.filters) + tuple(bk.regime):
        v = ev[col].values.astype(float)
        m &= OPS[op](v, val) & np.isfinite(v)
    return m


def picks(ev: pd.DataFrame, bk: Book) -> pd.DataFrame:
    """Names chosen at 15:45 on T (before any T+1 information)."""
    e = ev[eligible(ev, bk)]
    e = e.sort_values(["date", bk.score], ascending=[True, False])
    return e.groupby("date", sort=True).head(bk.n)


def weights(sel: pd.DataFrame, bk: Book, ev: pd.DataFrame) -> np.ndarray:
    if bk.sizing == "equal":
        w = np.full(len(sel), bk.w)
    elif bk.sizing == "invvol":
        # ATR14 known at T; scaled so the median strict-gate event gets the base weight
        med = float(np.nanmedian(np.clip(ev.atr14.values, 0.03, None)))
        a = np.clip(np.nan_to_num(sel.atr14.values, nan=med), 0.03, None)
        w = np.clip(bk.w * med / a, 0.5 * bk.w, 2 * bk.w)
    elif bk.sizing == "score":
        s = sel[bk.score].values
        w = bk.w * np.clip(1 + (s - 70) / 20, 0.5, 2.0)
    else:
        raise ValueError(bk.sizing)
    return w


def book(ev: pd.DataFrame, tr: pd.DataFrame, bk: Book, days: list[str]):
    sel = picks(ev, bk)
    t = tr.set_index("i").loc[sel.index]
    t.index.name = "i"
    t = t.assign(**{c: sel[c].values for c in dict.fromkeys((bk.score, "atr14", "p1545_raw"))})
    t["w"] = weights(t, bk, ev)
    t = t[t.ok]   # skipped at the open (gap filter / no fill): the slot stays empty, as live
    # gross cap per day (in rank order)
    t["cumw"] = t.groupby("date").w.cumsum()
    t["w"] = np.clip(bk.gross_cap - (t.cumw - t.w), 0, t.w)
    t["contrib"] = t.w * t.net
    d = t.groupby("date").agg(ret=("contrib", "sum"), n=("symbol", "size"), gross=("w", "sum"))
    d = d.reindex(days).fillna({"ret": 0.0, "n": 0, "gross": 0.0})
    d.index.name = "date"
    return d.reset_index(), t.reset_index()


# ------------------------------------------------------------------ stats / trials
def stats(d: pd.DataFrame, t: pd.DataFrame | None = None) -> dict:
    r = d.ret.values
    if not len(r):
        return {}
    eq = np.cumprod(np.maximum(1 + r, 0))
    yrs = len(r) / 252
    sd = r.std(ddof=1)
    dd = eq / np.maximum.accumulate(eq) - 1
    o = {"sharpe": r.mean() / sd * math.sqrt(252) if sd > 0 else 0.0,
         "cagr": eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0,
         "maxdd": dd.min(), "worst_day": r.min(), "active": int((d.n > 0).sum())}
    if t is not None and len(t):
        x = t.net
        o.update(trades=len(x), mean=x.mean(), median=x.median(), win=(x > 0).mean(),
                 worst=x.min(), p_lt_m10=(x < -0.10).mean())
    else:
        o.update(trades=0)
    return o


def split(d, t):
    return ((d[d.date <= IS_END], t[t.date <= IS_END]), (d[d.date >= OOS_START], t[t.date >= OOS_START]))


TRIALS: list[dict] = []


def evaluate(ev, B, days, rule: Rule, bk: Book, group: str, log: bool = True, tr=None):
    tr = trade_returns(ev, B, rule) if tr is None else tr
    d, t = book(ev, tr, bk, days)
    (di, ti), (do, to) = split(d, t)
    si, so = stats(di, ti), stats(do, to)
    row = {"group": group, "rule": rule.name, "book": bk.name,
           **{f"is_{k}": v for k, v in si.items()}, **{f"oos_{k}": v for k, v in so.items()},
           "cfg": json.dumps({"rule": asdict(rule), "book": asdict(bk)}, default=str)}
    if log:
        TRIALS.append({**row, "_is_daily": di.ret.values})
    return row, d, t


def deflated_sharpe(sr_daily, n_obs, skew, kurt_excess, n_trials, var_sr_trials):
    """Bailey & Lopez de Prado (2014), per-period Sharpe; same function as round 1."""
    g = 0.5772156649
    sr0 = math.sqrt(var_sr_trials) * ((1 - g) * sst.norm.ppf(1 - 1 / n_trials)
                                      + g * sst.norm.ppf(1 - 1 / (n_trials * math.e))) if n_trials > 1 else 0.0
    k = kurt_excess + 3
    den = math.sqrt(max(1e-12, 1 - skew * sr_daily + (k - 1) / 4 * sr_daily ** 2))
    return float(sst.norm.cdf((sr_daily - sr0) * math.sqrt(n_obs - 1) / den)), sr0


def psr(r):
    r = np.asarray(r)
    sr = r.mean() / r.std(ddof=1)
    return deflated_sharpe(sr, len(r), pd.Series(r).skew(), pd.Series(r).kurt(), 1, 0)[0]
