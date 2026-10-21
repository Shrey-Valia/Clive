"""Defined-risk premium-selling strategies for the spreads tuning study
(tuning/spreads/). Kept separate from the shared REGISTRY on purpose.

SpreadBook generalises PutCreditSpread / IronCondor with the levers studied:

  structure     "pcs" (put credit spread), "ic" (iron condor), "ccs" (call credit spread)
  put_delta / call_delta     short-strike |delta| per side (skewed condors allowed)
  wing          "abs" ($ width), "pct" (width as % of spot), "delta" (long-leg |delta|)
  width_abs / width_pct / long_delta
  dte_min/dte_max/dte_target, monthly_only
  tp            take profit as fraction of the entry credit (None = hold)
  sl_mult       stop when loss >= sl_mult * credit (None = off)
  breach        stop when the underlying closes beyond the short strike
                (+ breach_buf as a fraction of spot; >0 = stop *before* touching)
  close_dte     exit when DTE <= close_dte (None = hold to expiry)
  max_positions concurrent positions (laddering when > 1)
  entry_gap     min calendar days between new entries (7 = weekly ladder)
  risk_frac     max loss per position as a fraction of equity
  risk_cap      total max loss of all open positions as a fraction of equity
  min_credit_frac  skip entries whose credit < this fraction of the width
  max_credit_frac  skip entries whose credit > this fraction of the width (default 0.60; guard
                   against asynchronous-print credits, tuning/signals/BUGS.md #7). None = off.
  kill_switch   inherited intramonth kill switch (VIX/VIX3M>1.15, VIX>40, loss>8% notional)
  filter        inherited entry filters ("sma200", "vix", ...)
  rsi2_ccs      also open a call credit spread when RSI(2) > rsi2_hi (structure "pcs" only)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .strategies import Strategy


def rsi2(close: pd.Series, n: int = 2) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


class SpreadBook(Strategy):
    id, name = "spread_book", "Defined-risk spread book (tunable)"
    defaults = dict(structure="pcs", put_delta=0.20, call_delta=0.16,
                    wing="abs", width_abs=5.0, width_pct=None, long_delta=0.10,
                    dte_min=30, dte_max=52, dte_target=45, monthly_only=False,
                    tp=0.5, sl_mult=2.0, breach=False, breach_buf=0.0, close_dte=21,
                    max_positions=1, entry_gap=0, risk_frac=0.10, risk_cap=None,
                    min_credit_frac=0.0, max_credit_frac=0.60, kill_switch=False, filter=None,
                    rsi2_ccs=False, rsi2_hi=90.0, ccs_delta=0.20, ccs_dte_min=14,
                    ccs_dte_max=35, ccs_dte_target=28, ccs_tp=0.5, ccs_close_dte=7)

    # ---------------------------------------------------------------- legs
    def _wing(self, eng, d, short, cp):
        p = self.p
        sgn = -1 if cp == "P" else 1
        if p["wing"] == "abs":
            k = short.strike + sgn * p["width_abs"]
        elif p["wing"] == "pct":
            k = short.strike + sgn * p["width_pct"] * eng.spot()
        else:
            q = eng.m.select(d, cp, delta=p["long_delta"], expiry=short.expiry, iv_hint=short.iv)
            if q is None or (cp == "P" and q.strike >= short.strike) or (cp == "C" and q.strike <= short.strike):
                return None
            return q
        q = eng.m.select(d, cp, strike=k, expiry=short.expiry, iv_hint=short.iv)
        if q is None or (cp == "P" and q.strike >= short.strike) or (cp == "C" and q.strike <= short.strike):
            return None
        return q

    def _open_risk(self, eng):
        tot = 0.0
        for pos in eng.positions:
            tot += pos.meta.get("max_loss", pos.reserve)
        return tot

    def _size(self, eng, max_loss_per):
        p = self.p
        if max_loss_per <= 0:
            return 0
        budget = eng.equity() * p["risk_frac"]
        if p.get("risk_cap") is not None:
            budget = min(budget, eng.equity() * p["risk_cap"] - self._open_risk(eng))
        budget = min(budget, eng.free_cash())
        return max(int(budget // max_loss_per), 0)

    # ------------------------------------------------------------- manage
    def _manage(self, eng, d):
        p = self.p
        S = eng.spot()
        for pos in list(eng.positions):
            if pos.kind == "ccs_overlay":
                r = self.credit_exit(eng, pos, d, p["ccs_tp"], p["sl_mult"], p["ccs_close_dte"])
            else:
                r = self.credit_exit(eng, pos, d, p["tp"], p["sl_mult"], p["close_dte"])
            if r is None and p["breach"]:
                for l in pos.legs:
                    if not l.open or l.qty >= 0:
                        continue
                    if l.cp == "P" and S <= l.strike * (1 + p["breach_buf"]):
                        r = "breach_stop"
                    if l.cp == "C" and S >= l.strike * (1 - p["breach_buf"]):
                        r = "breach_stop"
            if r:
                eng.close(pos, r)

    def _vertical(self, eng, d, cp, delta, dte_kw):
        short = eng.m.select(d, cp, delta=delta, **dte_kw)
        if short is None:
            return None
        long = self._wing(eng, d, short, cp)
        if long is None:
            return None
        return short, long

    def on_day(self, eng, d):
        p = self.p
        self._manage(eng, d)
        blocked = self.kill_switch(eng, d)
        if blocked:
            return
        if p.get("rsi2_ccs") and p["structure"] == "pcs":
            self._maybe_ccs(eng, d)
        core = [x for x in eng.positions if x.kind != "ccs_overlay"]
        if len(core) >= p["max_positions"]:
            return
        last = getattr(self, "_last_entry", None)
        if p["entry_gap"] and last is not None and (d - last).days < p["entry_gap"]:
            return
        if not self.entry_filter_ok(eng, d):
            return
        if p["close_dte"] is not None and p["dte_min"] <= p["close_dte"]:
            return
        kw = self.dte_kw()
        legs, credit_mark, width = [], 0.0, 0.0
        st = p["structure"]
        if st in ("pcs", "ic"):
            v = self._vertical(eng, d, "P", p["put_delta"], kw)
            if v is None:
                return
            sp, lp = v
            legs += [(sp, -1), (lp, 1)]
            credit_mark += sp.price - lp.price
            width = max(width, sp.strike - lp.strike)
        if st in ("ccs", "ic"):
            kwc = dict(expiry=legs[0][0].expiry) if legs else kw
            if legs:
                sc = eng.m.select(d, "C", delta=p["call_delta"], iv_hint=legs[0][0].iv, **kwc)
                if sc is None:
                    return
                lc = self._wing(eng, d, sc, "C")
                if lc is None:
                    return
            else:
                v = self._vertical(eng, d, "C", p["call_delta"], kw)
                if v is None:
                    return
                sc, lc = v
            legs += [(sc, -1), (lc, 1)]
            credit_mark += sc.price - lc.price
            width = max(width, lc.strike - sc.strike)
        min_cr = 0.10 if st == "ic" else 0.05
        if credit_mark <= min_cr or credit_mark < p["min_credit_frac"] * width:
            return
        # guard (tuning/signals/BUGS.md #7): leg closes printed at different times can imply an
        # impossible credit (e.g. $1.81 on a $2 width) and a huge contract count. Skip such entries.
        if p.get("max_credit_frac") is not None and credit_mark > p["max_credit_frac"] * width:
            eng.note("skipped entry: credit > max_credit_frac x width (asynchronous leg prints)")
            return
        max_loss = (width - credit_mark) * 100
        n = self._size(eng, max_loss)
        if n == 0:
            eng.blocked("skipped entries: one spread's max loss exceeds risk budget")
            return
        pos = eng.open([(q, s * n) for q, s in legs], st, reserve=width * 100 * n,
                       meta={"max_loss": max_loss * n, "spot0": eng.spot()})
        if pos is not None:
            self._last_entry = d

    def _maybe_ccs(self, eng, d):
        p = self.p
        m = eng.m
        if "rsi2" not in m.stock.columns:
            m.stock["rsi2"] = rsi2(m.stock["adj_close"])
        if any(x.kind == "ccs_overlay" for x in eng.positions):
            return
        if not (m.stock.at[d, "rsi2"] > p["rsi2_hi"]):
            return
        kw = dict(dte_min=p["ccs_dte_min"], dte_max=p["ccs_dte_max"], dte_target=p["ccs_dte_target"])
        v = self._vertical(eng, d, "C", p["ccs_delta"], kw)
        if v is None:
            return
        sc, lc = v
        width = lc.strike - sc.strike
        cr = sc.price - lc.price
        if cr <= 0.05:
            return
        if p.get("max_credit_frac") is not None and cr > p["max_credit_frac"] * width:
            return
        n = self._size(eng, (width - cr) * 100)
        if n == 0:
            return
        eng.open([(sc, -n), (lc, n)], "ccs_overlay", reserve=width * 100 * n,
                 meta={"max_loss": (width - cr) * 100 * n})


SPREAD_REGISTRY = {SpreadBook.id: SpreadBook}
