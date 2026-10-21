"""Shared-cash multi-name wheel portfolio built on top of the single-name Engine.

The core Engine simulates one underlying. Here one Engine per name ("sleeve")
is stepped in lock-step, day by day, using the Engine's own daily steps
(_accrue, _corporate_actions, _mark, _expire, _early_assign_calls, _record)
in the same order as Engine.run(). Cash lives in a shared pool:

* A sleeve that is flat (no option position, no shares) holds no cash; its
  cash is swept back to the pool every day.
* Non-flat sleeves are managed first (take-profits, rolls, covered calls on
  assigned shares). Then flat sleeves are offered new capital in a mechanical
  rank order: budget = min(cap_frac * portfolio equity, pool cash), subject to
  max_names concurrent sleeves. If the sleeve's strategy does not open
  anything with that budget (e.g. one contract is too expensive), the cash
  goes back to the pool the same day.

Ranking rules are mechanical and use only data known at the close of day d.
No engine source is modified.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from optionsbot import metrics as M
from optionsbot import runner
from optionsbot.engine import Engine
from optionsbot.strategies_wheel import WheelX, indicators

UNIVERSE = ["QQQ", "IWM", "AAPL", "AMZN", "GOOGL", "META", "NVDA", "MSFT"]


class Portfolio:
    def __init__(self, symbols, params, capital, start="2024-03-01", end="2026-10-20",
                 cap_frac=0.34, max_names=None, rank="fixed", cash_yield=False, costs=None,
                 strategy_cls=WheelX):
        self.symbols = list(symbols)
        self.capital = float(capital)
        self.cap_frac, self.max_names, self.rank = cap_frac, max_names, rank
        self.cash_yield = cash_yield
        self.pool = float(capital)
        self.interest = 0.0
        self.eng = {}
        for s in self.symbols:
            e = Engine(runner.market(s), strategy_cls(**params), 0.0, start, end, costs=costs,
                       cash_yield=cash_yield)
            self.eng[s] = e
        self.dates = self.eng[self.symbols[0]].dates
        for s in self.symbols:
            assert self.eng[s].dates.equals(self.dates), s
        self.records = []
        self.last_used = {}

    # --------------------------------------------------------------- ranking
    def order(self, d):
        syms = list(self.symbols)
        if self.rank == "fixed":
            return syms
        if self.rank == "rotate":          # round robin: longest-idle name first
            return sorted(syms, key=lambda s: (self.last_used.get(s, pd.Timestamp(0)), syms.index(s)))
        ind = {s: indicators(self.eng[s].m).loc[d] for s in syms}
        if self.rank == "trend":           # fixed order, only names above their 200DMA
            return [s for s in syms if bool(ind[s]["above200"])]
        if self.rank == "mom":             # 12-1 momentum, positive only
            ok = [s for s in syms if np.isfinite(ind[s]["mom12_1"]) and ind[s]["mom12_1"] > 0]
            return sorted(ok, key=lambda s: -ind[s]["mom12_1"])
        if self.rank == "rv_high":         # richest premium first (20d realized vol)
            return sorted(syms, key=lambda s: -(ind[s]["rv20"] if np.isfinite(ind[s]["rv20"]) else 0))
        if self.rank == "rv_low":          # calmest first
            return sorted(syms, key=lambda s: (ind[s]["rv20"] if np.isfinite(ind[s]["rv20"]) else 9))
        raise ValueError(self.rank)

    @staticmethod
    def flat(e):
        return not e.positions and abs(e.shares) < 1e-9

    def equity(self):
        return self.pool + sum(e.equity() for e in self.eng.values())

    # ------------------------------------------------------------------ run
    def run(self):
        idx = self.dates
        for i, d in enumerate(idx):
            for s, e in self.eng.items():
                e.d = d
                if i > 0:
                    e._accrue(idx[i - 1], d)
                    e._corporate_actions(d)
                e._mark(d)
                e._expire(d)
                if e.early_assign:
                    full = e.m.stock.index
                    nxt = full[full > d]
                    if len(nxt) and e.m.stock.at[nxt[0], "dividend"] > 0:
                        e._early_assign_calls(d, float(e.m.stock.at[nxt[0], "dividend"]))
            if i > 0 and self.cash_yield and self.pool > 0:
                amt = self.pool * runner.market(self.symbols[0]).r(idx[i - 1]) * (d - idx[i - 1]).days / 365
                self.pool += amt
                self.interest += amt
            # sweep flat sleeves
            for e in self.eng.values():
                if self.flat(e) and e.cash != 0:
                    self.pool += e.cash
                    e.cash = 0.0
            E = self.equity()
            active = 0
            for s, e in self.eng.items():          # manage open sleeves first
                if not self.flat(e):
                    e.strategy.on_day(e, d)
                    if self.flat(e):
                        self.pool += e.cash
                        e.cash = 0.0
                        self.last_used[s] = d
                    else:
                        active += 1
            for s in self.order(d):                # then offer capital to flat sleeves
                e = self.eng[s]
                if not self.flat(e):
                    continue
                if self.max_names and active >= self.max_names:
                    break
                budget = min(self.cap_frac * E, self.pool)
                if budget < 1000:
                    break
                e.cash = budget
                self.pool -= budget
                e.strategy.on_day(e, d)
                if self.flat(e):
                    self.pool += e.cash
                    e.cash = 0.0
                else:
                    active += 1
                    self.last_used[s] = d
            for e in self.eng.values():
                e._record(d)
            self.records.append({"date": d, "equity": self.equity(), "pool": self.pool,
                                 "active": active,
                                 "stock_value": sum(e.shares * e.spot() for e in self.eng.values()),
                                 "reserved": sum(e.reserved() for e in self.eng.values())})
        return self

    # --------------------------------------------------------------- output
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

    def kill_events(self):
        return sum(e.kill_events for e in self.eng.values())


def equal_weight_bh(symbols, start="2024-03-01", end="2026-10-20", capital=100_000):
    """Static equal-weight buy & hold (dividends reinvested via adj_close), no rebalancing."""
    cols = {}
    for s in symbols:
        st = runner.market(s).stock
        a = st.loc[start:end, "adj_close"]
        cols[s] = a / a.iloc[0]
    df = pd.DataFrame(cols)
    return df.mean(axis=1) * capital
