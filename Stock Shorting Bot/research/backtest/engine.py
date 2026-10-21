"""Daily event backtester for the spike-short book.

A Config describes one strategy variant. run(cfg, ev) returns (daily, trades):
  daily  : one row per trading day with the book's net return (fraction of equity)
  trades : one row per position with gross/net short return
Returns compound on equity; multi-day holds use h staggered sub-accounts.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

from common import CACHE

EQUITY_USD = 100_000          # paper account scale, for the impact term only


@dataclass(frozen=True)
class Config:
    name: str = "baseline"
    score: str = "short_score"         # column used to rank
    n: int = 5
    min_score: float | None = None     # skip names below this
    sizing: str = "bot"                # bot | equal | atr
    gross: float = 0.85                # for equal / atr
    max_name: float = 0.19             # cap per name (atr)
    entry: str = "1555"                # 1555 | close | open1 | x1555 (T+1 15:55)
    exit: str = "1555n"                # 1555n | open1 | close1 | close2..close5
    stop: float | None = None          # e.g. 0.25 = cover if +25% against
    max_chg: float | None = None       # skip names up more than this at 15:45
    min_chg: float | None = None
    skip_weekend: bool = False         # no new book on Fridays / pre-holiday
    hedge: float = 0.0                 # long IWM as fraction of gross short
    regime: str | None = None          # None | iwm_below_sma50 | iwm_above_sma50 | vix_above_20 | vix_below_30
    borrow_gate_price: float = 5.0     # shortability proxy: raw price >= this
    borrow_gate_adv: float = 3e6       # and 20d ADV$ >= this
    use_bot_exclusions: bool = True    # squeeze trap / real catalyst exclusions
    borrow_base: float = 0.15          # %/yr, easy names
    borrow_htb: float = 0.50           # %/yr, haircut names (price<10 or crowded)
    cost_mult: float = 1.0
    filters: tuple = field(default_factory=tuple)   # extra (col, op, value) gates


def one_way_cost(price_raw, adv, chg):
    bps = np.select([price_raw < 5, price_raw < 10, price_raw < 20, price_raw < 50], [30, 20, 12, 8], 5).astype(float)
    bps += np.select([adv < 2e6, adv < 10e6], [20, 10], 0)
    bps += np.where(chg > 0.5, 10, 0)
    return bps / 1e4


def prep(ev: pd.DataFrame) -> pd.DataFrame:
    """Precompute per-event exit prices, path highs and calendar facts."""
    ev = ev.copy()
    cal = pd.read_csv(os.path.join(CACHE, "calendar.csv"))
    days = pd.to_datetime(cal.date)
    nxt_gap = (days.shift(-1) - days).dt.days
    ev["cal_gap"] = ev.ti.map(dict(enumerate(nxt_gap))).fillna(1)
    ev["weekday"] = pd.to_datetime(ev.date).dt.weekday
    for k in range(1, 6):
        ev[f"ok{k}"] = (ev[f"ti{k}"] - ev.ti) == k
    etf = pd.read_parquet(os.path.join(CACHE, "etf_daily.parquet"))
    iwm = etf[etf.symbol == "IWM"].set_index("date").c
    ev["iwm_ret1"] = ev.date.map((iwm.shift(-1) / iwm - 1).to_dict())
    ev["iwm_sma50"] = ev.date.map((iwm / iwm.rolling(50).mean() - 1).to_dict())
    vix = pd.read_csv(os.path.join(CACHE, "vix.csv"))
    vix["date"] = pd.to_datetime(vix.DATE).dt.strftime("%Y-%m-%d")
    ev["vix"] = ev.date.map(vix.set_index("date").CLOSE.shift(1).to_dict())   # prior close: no look-ahead
    return ev


def entry_exit(ev: pd.DataFrame, cfg: Config):
    """Entry price, exit price, exit day k, validity, stop flag.

    Entry day 0 = the scan day T (15:55 or close); entry day 1 = T+1 (open or 15:55).
    A stop is checked on daily bars after entry: a gap open through the level fills
    at the open, otherwise at the level (+1% slippage). Same-day post-entry highs use
    the 15:55-16:00 minute bars (entry at 15:55 on T) or the T+1 day high (open entry).
    """
    ent = {"1555": ev.p1555, "close": ev.c, "open1": ev.o1, "x1555": ev.x1555}[cfg.entry].values
    e_day = 1 if cfg.entry in ("open1", "x1555") else 0
    if cfg.exit == "1555n":
        ex, k = ev.x1555.values, 1
    elif cfg.exit == "open1":
        ex, k = ev.o1.values, 1
    else:
        k = int(cfg.exit[-1])
        ex = ev[f"c{k}"].values
    intraday_ok = cfg.entry == "open1" and cfg.exit.startswith("close")
    valid = np.isfinite(ent) & np.isfinite(ex) & (ent > 0) & ((k > e_day) or intraday_ok)
    valid &= ev[f"ok{k}"].values
    stopped = np.zeros(len(ev), bool)
    if cfg.stop is not None:
        lvl = ent * (1 + cfg.stop)
        out = ex.copy()
        done = np.zeros(len(ev), bool)
        segs = []
        if cfg.entry == "1555":
            segs.append((None, ev.h_last5.fillna(0).values))
        if cfg.entry == "open1":
            segs.append((None, ev.h1.values))
        for j in range(e_day + 1, k + 1):
            if cfg.exit == "open1":
                segs.append((ev.o1.values, None))
            else:
                segs.append((ev[f"o{j}"].values, ev[f"h{j}"].values))
        for o, h in segs:
            if o is not None:
                gap = (o >= lvl) & ~done
                out = np.where(gap, o, out); done |= gap
            if h is not None:
                hit = (h >= lvl) & ~done
                out = np.where(hit, lvl, out); done |= hit
        stopped = done
        ex = np.where(done, out * 1.01, ex)
    return ent, ex, k, e_day, valid, stopped


def eligible(ev: pd.DataFrame, cfg: Config) -> np.ndarray:
    m = np.ones(len(ev), bool)
    m &= (ev.p1545_raw.values >= cfg.borrow_gate_price) & (ev.adv20.values >= cfg.borrow_gate_adv)
    if cfg.use_bot_exclusions:
        ex = ev.exclude_bot.fillna("")
        m &= ~ex.str.startswith("squeeze").values & ~ex.str.startswith("real").values
    if cfg.max_chg is not None:
        m &= ev.chg1545.values <= cfg.max_chg
    if cfg.min_chg is not None:
        m &= ev.chg1545.values >= cfg.min_chg
    if cfg.min_score is not None:
        m &= ev[cfg.score].values >= cfg.min_score
    for col, op, val in cfg.filters:
        v = ev[col].values
        m &= {"<": v < val, "<=": v <= val, ">": v > val, ">=": v >= val, "==": v == val, "!=": v != val}[op]
    if cfg.skip_weekend:
        m &= ev.cal_gap.values <= 1
    if cfg.regime == "iwm_below_sma50":
        m &= ev.iwm_sma50.values < 0
    elif cfg.regime == "iwm_above_sma50":
        m &= ev.iwm_sma50.values > 0
    elif cfg.regime == "vix_above_20":
        m &= ev.vix.values > 20
    elif cfg.regime == "vix_below_30":
        m &= ev.vix.values < 30
    return m


def weights(sel: pd.DataFrame, cfg: Config) -> np.ndarray:
    n = len(sel)
    slot = np.arange(n)
    if cfg.sizing == "bot":
        top, bot = 0.19, 0.15
        step = (top - bot) / (cfg.n - 1) if cfg.n > 1 else 0
        # keep the live book's ~85% gross when n != 5 by scaling
        w = top - slot * step
        w = w * (0.85 / sum(top - np.arange(cfg.n) * step))
        return w
    if cfg.sizing == "equal":
        return np.full(n, cfg.gross / cfg.n)
    if cfg.sizing == "atr":
        inv = 1 / np.clip(sel.atr14.fillna(sel.atr14.median() if n else 0.1).values, 0.02, None)
        w = cfg.gross / cfg.n * inv / np.mean(inv) if n else inv
        w = np.minimum(w, cfg.max_name)
        tot = w.sum()
        return w if tot <= cfg.gross else w * cfg.gross / tot
    raise ValueError(cfg.sizing)


def run(cfg: Config, ev: pd.DataFrame, all_days: list[str] | None = None):
    ent, ex, k, e_day, valid, stopped = entry_exit(ev, cfg)
    e = ev.assign(ent=ent, ex=ex, valid=valid, stopped=stopped)
    e = e[eligible(ev, cfg) & valid]
    e = e.sort_values(["date", cfg.score], ascending=[True, False])
    sel = e.groupby("date", sort=True).head(cfg.n).copy()
    sel["slot"] = sel.groupby("date").cumcount()
    w = np.zeros(len(sel))
    for d, idx in sel.groupby("date").indices.items():
        w[idx] = weights(sel.iloc[idx], cfg)
    sel["w"] = w
    gross_ret = 1 - sel.ex / sel.ent
    c_in = one_way_cost(sel.p1545_raw.values, sel.adv20.values, sel.chg1545.values) * cfg.cost_mult
    imp = 0.01 * np.sqrt(np.clip(sel.w.values * EQUITY_USD / np.maximum(sel.dv_raw.values, 1), 0, 1))
    c_in = c_in + imp
    c_out = c_in * (sel.ex / sel.ent)
    if e_day == 0 and k == 1:
        hold_days = sel.cal_gap.values.astype(float)
    elif cfg.entry == "open1" and k == 1:
        hold_days = np.full(len(sel), 0.3)      # intraday: shorts held only intraday pay little or no borrow
    else:
        hold_days = np.full(len(sel), (k - e_day) * 1.4)
    htb = (sel.p1545_raw.values < 10) | (sel.si_pct.fillna(0).values > 15) | (sel.dtc.fillna(0).values > 5)
    rate = np.where(htb, cfg.borrow_htb, cfg.borrow_base)
    borrow = rate * hold_days / 360
    sel["gross_ret"] = gross_ret
    sel["net_ret"] = gross_ret - c_in - c_out - borrow
    sel["cost"] = c_in + c_out + borrow
    sel["contrib"] = sel.w * sel.net_ret
    book = sel.groupby("date").agg(ret=("contrib", "sum"), n=("symbol", "size"), gross=("w", "sum"))
    if cfg.hedge:
        hed = sel.groupby("date").iwm_ret1.first() * cfg.hedge * book.gross
        book["ret"] = book.ret + hed.fillna(0) - cfg.hedge * book.gross * 2e-4
    days = all_days or sorted(ev.date.unique())
    book = book.reindex(days).fillna({"ret": 0.0, "n": 0, "gross": 0.0})
    hold = max(k - e_day, 1) if cfg.entry != "open1" else max(k, 1)
    if hold > 1:
        # h staggered sub-accounts, each trading every h-th day
        r = book.ret.values
        eq = np.ones(hold)
        curve = []
        for i, x in enumerate(r):
            j = i % hold
            eq[j] *= 1 + x
            curve.append(eq.mean())
        curve = np.array(curve)
        book["ret"] = np.r_[curve[0] - 1, curve[1:] / curve[:-1] - 1]
    book.index.name = "date"
    return book.reset_index(), sel


def stats(daily: pd.DataFrame, trades: pd.DataFrame | None = None) -> dict:
    r = daily.ret.values
    if len(r) == 0:
        return {}
    eq = np.cumprod(np.maximum(1 + r, 0))          # a day losing >100% of equity is ruin; equity stays 0
    yrs = len(r) / 252
    sd = r.std(ddof=1)
    dd = eq / np.maximum(np.maximum.accumulate(eq), 1e-300) - 1
    ruined = bool((eq <= 0).any())
    out = {"days": len(r), "active_days": int((daily.n > 0).sum()),
           "cagr": eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0,
           "total": eq[-1] - 1, "vol": sd * math.sqrt(252),
           "sharpe": r.mean() / sd * math.sqrt(252) if sd > 0 else 0.0,
           "maxdd": dd.min(), "worst_day": r.min(), "best_day": r.max(),
           "skew": pd.Series(r).skew(), "kurt": pd.Series(r).kurt(), "ruined": ruined,
           "ann_mean": r.mean() * 252}
    if trades is not None and len(trades):
        t = trades.net_ret
        out.update({"trades": len(t), "mean_trade": t.mean(), "median_trade": t.median(),
                    "win": (t > 0).mean(), "p_lt_-10": (t < -0.10).mean(), "p_lt_-25": (t < -0.25).mean(),
                    "q01": t.quantile(0.01), "worst_trade": t.min(), "mean_gross": trades.gross_ret.mean(),
                    "mean_cost": trades.cost.mean()})
    return out
