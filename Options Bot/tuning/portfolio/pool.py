"""Shared-cash dip-PCS portfolio on several ETFs with a total risk cap, plus an idle-cash
policy (cash / ETF sweep / static core ETF) and optional T-bill interest.

One Engine per underlying is stepped in lock-step using the Engine's own daily steps
(same order as Engine.run, as in tuning/wheel/portfolio.py). Cash lives in a pool:

* Every day, flat sleeves are swept back to the pool. Sleeves with an open spread keep
  exactly their reserved collateral (width x 100 x n); everything else returns to the pool.
* Open sleeves are managed first. Then, in fixed order (SPY, QQQ, IWM), each flat sleeve
  is offered collateral = pool cash (+ sellable idle ETF in 'sweep' mode). Its contract
  count is set by PooledDipPCS.size() via risk_budget():
        min(per_trade * E,  cap * E - sum(open max loss),  collateral offered)
  where E = total portfolio equity at today's close (before the new trade).
* idle='cash'   : pool earns nothing (or T-bill if cash_yield).
  idle='SPY'/'QQQ' (sweep): pool cash above a $250 / 1% buffer is held in the ETF (fractional
  shares, total-return via adj_close, 1 bp per trade); sold first-come when collateral is
  needed. Collateral itself is always cash (Alpaca cash-secured treatment).
  core=x : x of equity held in SPY, rebalanced to x on the first trading day of each month
  using pool cash only (never collateral); the rest is the spread collateral pool.
All decisions use data known at the day's close. No engine source is modified.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import START, END  # noqa: F401
from optionsbot import runner
from optionsbot.engine import Engine
from optionsbot.strategies_portfolio import PooledDipPCS, PooledIronCondor, position_max_loss

SYMS = ["SPY", "QQQ", "IWM"]
STOCK_BPS = 1.0


class DipPool:
    def __init__(self, capital, cap=0.5, per_trade=0.25, idle="cash", core=0.0, core_sym="SPY",
                 cash_yield=False, symbols=SYMS, start=START, end=END, params=None, costs=None,
                 with_ic=False):
        self.capital = float(capital)
        self.cap, self.per_trade = cap, per_trade
        self.idle, self.core, self.core_sym = idle, core, core_sym
        self.cash_yield = cash_yield
        self.symbols = (["IC_SPY"] if with_ic else []) + list(symbols)   # IC offered first
        self.pool = float(capital)
        self.units = 0.0                  # idle ETF units (sweep) or core units
        self.etf = core_sym if core > 0 else (idle if idle in ("SPY", "QQQ") else None)
        self.eng = {}
        for s in self.symbols:
            if s == "IC_SPY":
                strat, mkt = PooledIronCondor(), runner.market("SPY")
            else:
                strat, mkt = PooledDipPCS(**(params or {})), runner.market(s)
                if per_trade is not None:
                    strat.p["risk_frac"] = per_trade
            strat.controller = self
            self.eng[s] = Engine(mkt, strat, 0.0, start, end, costs=costs, cash_yield=cash_yield)
        self.dates = self.eng[self.symbols[0]].dates
        for s in self.symbols:
            assert self.eng[s].dates.equals(self.dates), s
        if self.etf:
            a = runner.market(self.etf).stock["adj_close"]
            self.px = a.reindex(self.dates).ffill()
        self.records = []
        self.etf_cost = 0.0
        self.interest = 0.0
        self._offer = None

    # ------------------------------------------------------------ helpers
    @staticmethod
    def flat(e):
        return not e.positions

    def etf_value(self, d):
        return self.units * float(self.px.at[d]) if self.etf else 0.0

    def equity(self, d):
        return self.pool + self.etf_value(d) + sum(e.equity() for e in self.eng.values())

    def open_risk(self):
        return sum(position_max_loss(p) for e in self.eng.values() for p in e.positions)

    def risk_budget(self, eng):
        E = self._E
        per = eng.strategy.p["risk_frac"]
        return max(min(per * E, self.cap * E - self.open_risk()), 0.0)

    def _buy(self, d, dollars):
        if dollars <= 0:
            return
        p = float(self.px.at[d])
        cost = dollars * STOCK_BPS / 1e4
        self.units += (dollars - cost) / p
        self.pool -= dollars
        self.etf_cost += cost

    def _sell(self, d, dollars):
        if dollars <= 0 or self.units <= 0:
            return
        p = float(self.px.at[d])
        dollars = min(dollars, self.units * p)
        cost = dollars * (STOCK_BPS / 1e4 + 27.8e-6)
        self.units -= dollars / p
        self.pool += dollars - cost
        self.etf_cost += cost

    def _sweep_back(self, e):
        keep = e.reserved() if e.positions else 0.0
        extra = e.cash - keep
        if abs(extra) > 1e-9:
            self.pool += extra
            e.cash = keep

    # ---------------------------------------------------------------- run
    def run(self):
        idx = self.dates
        last_month = None
        for i, d in enumerate(idx):
            for s, e in self.eng.items():
                e.d = d
                if i > 0:
                    e._accrue(idx[i - 1], d)
                    e._corporate_actions(d)
                e._mark(d)
                e._expire(d)
            if i > 0 and self.cash_yield and self.pool > 0:
                amt = self.pool * runner.market("SPY").r(idx[i - 1]) * (d - idx[i - 1]).days / 365
                self.pool += amt
                self.interest += amt
            for e in self.eng.values():
                self._sweep_back(e)
            # manage open sleeves (exits), sweep freed collateral
            for e in self.eng.values():
                if not self.flat(e):
                    e.strategy.on_day(e, d)
                    self._sweep_back(e)
            # core rebalance (monthly, pool cash only)
            E = self.equity(d)
            if self.core > 0:
                m = d.strftime("%Y-%m")
                if m != last_month:
                    last_month = m
                    tgt = self.core * E
                    cur = self.etf_value(d)
                    if cur < tgt:
                        self._buy(d, min(tgt - cur, max(self.pool, 0.0)))
                    elif cur > tgt * 1.0:
                        self._sell(d, cur - tgt)
                    E = self.equity(d)
            # entries
            self._E = E
            for s in self.symbols:
                e = self.eng[s]
                if not self.flat(e):
                    continue
                if self.risk_budget(e) < 50:
                    continue
                sweep = self.idle in ("SPY", "QQQ") and self.core == 0
                offer = max(self.pool, 0.0) + (self.etf_value(d) * 0.999 if sweep else 0.0)
                if offer < 100:
                    break
                e.cash = offer
                self.pool -= offer
                e.strategy.on_day(e, d)
                self._sweep_back(e)              # pool may now be negative if ETF must be sold
                if self.pool < 0 and sweep:
                    p = float(self.px.at[d])
                    need = -self.pool
                    gross = need / (1 - STOCK_BPS / 1e4 - 27.8e-6)
                    self._sell(d, gross)
                    if self.pool < 0 and self.pool > -1e-6:
                        self.pool = 0.0
            # idle sweep into ETF
            if self.idle in ("SPY", "QQQ") and self.core == 0:
                E = self.equity(d)
                buf = max(250.0, 0.01 * E)
                if self.pool > buf + 100:
                    self._buy(d, self.pool - buf)
            for e in self.eng.values():
                e._record(d)
            res = sum(e.reserved() for e in self.eng.values())
            E = self.equity(d)
            self.records.append({"date": d, "equity": E, "pool": self.pool,
                                 "etf_value": self.etf_value(d), "reserved": res,
                                 "open_max_loss": self.open_risk(),
                                 "n_open": sum(len(e.positions) for e in self.eng.values())})
        return self

    def equity_frame(self):
        return pd.DataFrame(self.records).set_index("date")

    def trades_frame(self):
        fr = []
        for s, e in self.eng.items():
            t = e.trades_frame()
            if len(t):
                t.insert(0, "underlying", s)
                fr.append(t)
        return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()

    def usage(self):
        """Capital usage = (collateral reserved + ETF value) / equity."""
        f = self.equity_frame()
        return (f["reserved"] + f["etf_value"]) / f["equity"]
