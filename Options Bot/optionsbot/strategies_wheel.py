"""Stock-backed premium strategies for tuning (wheel / CSP / covered call / collar).

Own registry (WHEEL_REGISTRY); does not modify optionsbot/strategies.py.
All decisions at the daily close, through the public Engine API
(eng.open / eng.close / eng.buy_shares / eng.sell_shares / eng.m.select).

WheelX unifies four stock-backed structures through `entry` / `on_assign`:
  entry="put",   on_assign="wheel"  -> the wheel (CSP -> shares -> covered calls)
  entry="put",   on_assign="sell"   -> cash-secured put (assigned shares sold next close)
  entry="stock", on_assign="wheel"  -> covered call (re-buy lots after call-away)
CollarX: long shares + long put (own DTE) + short call, optional zero-cost.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .strategies import Strategy


# ---------------------------------------------------------------- indicators
_IND_CACHE: dict = {}


def indicators(m) -> pd.DataFrame:
    """RSI(2), 1d return, 12-1 momentum, VIX 1y percentile; computed once per Market."""
    key = id(m)
    if key in _IND_CACHE:
        return _IND_CACHE[key]
    s = m.stock
    c = s["adj_close"]
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    rsi2 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    rsi2 = rsi2.fillna(100.0)
    vix = s["vix"]
    vix_pct = vix.rolling(252, min_periods=120).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    vix_lo, vix_hi = vix.rolling(252, min_periods=120).min(), vix.rolling(252, min_periods=120).max()
    vix_rank = (vix - vix_lo) / (vix_hi - vix_lo)
    out = pd.DataFrame({
        "rsi2": rsi2,
        "ret1": c.pct_change(),
        "mom12_1": c.shift(21) / c.shift(252) - 1,
        "vix_pct": vix_pct,
        "vix_rank": vix_rank,
        "rv20": s["rv20"],
        "above200": s["close"] > s["sma200"],
    }, index=s.index)
    _IND_CACHE[key] = out
    return out


def _next_div(eng, d, k=2):
    """Dividend amount on any of the next k trading days (0 if none)."""
    idx = eng.m.stock.index
    nxt = idx[idx > d][:k]
    if not len(nxt):
        return 0.0
    return float(eng.m.stock.loc[nxt, "dividend"].max())


# ---------------------------------------------------------------- WheelX
class WheelX(Strategy):
    id, name = "wheel_x", "Wheel (tunable)"
    defaults = dict(
        entry="put", on_assign="wheel",
        put_delta=0.30, call_delta=0.30,
        dte_min=30, dte_max=45, dte_target=38,
        call_dte_min=None, call_dte_max=None, call_dte_target=None,
        put_tp=0.5, call_tp=None,
        put_exit_dte=None,          # close an OTM put at <= N DTE (then re-sell)
        call_exit_dte=None,         # close an OTM call at <= N DTE (then re-sell)
        put_roll_tested=False,      # ITM put at <= roll_trigger_dte: roll out (same strike) for credit
        call_roll_tested=False,     # ITM call at <= roll_trigger_dte: roll up/out for credit
        roll_trigger_dte=7, max_rolls=3,
        call_basis="engine",        # engine | strict | none | net
        call_gate=None,             # None | rsi2_skip_low | rsi2_high | up_day | above200
        put_gate=None,              # None | rsi2_low | down_day | above200 | vrp | vix_pct
        put_gate_x=None,            # threshold for vrp (iv/rv) or vix_pct
        exdiv_roll=False,           # roll an ITM call ahead of ex-div when extrinsic < div
        kill_switch=False, kill_ratio=1.15, kill_vix=40.0, kill_loss=0.08,
        kill_shares=False,          # on kill also close calls and sell shares
        alloc=1.0,
    )

    # ------------------------------------------------------------ helpers
    def _ind(self, eng, d):
        return indicators(eng.m).loc[d]

    def call_dte_kw(self):
        p = self.p
        if p["call_dte_min"] is None:
            return self.dte_kw()
        return dict(dte_min=p["call_dte_min"], dte_max=p["call_dte_max"],
                    dte_target=p["call_dte_target"], monthly_only=False)

    def _st(self):
        return self.__dict__.setdefault("_s", {"premium": 0.0, "kill_pending": False,
                                               "kill_until": None, "kills": 0, "rolls": {}})

    def _gate_call(self, eng, d) -> bool:
        g = self.p["call_gate"]
        if not g:
            return True
        x = self._ind(eng, d)
        if g == "rsi2_skip_low":
            return bool(x["rsi2"] >= 10)
        if g == "rsi2_high":
            return bool(x["rsi2"] > 90)
        if g == "up_day":
            return bool(x["ret1"] > 0)
        if g == "above200":           # only overwrite in downtrends (let winners run)
            return not bool(x["above200"])
        raise ValueError(g)

    def _gate_put(self, eng, d, q=None) -> bool:
        g = self.p["put_gate"]
        if not g:
            return True
        x = self._ind(eng, d)
        if g == "rsi2_low":
            return bool(x["rsi2"] < 30)
        if g == "down_day":
            return bool(x["ret1"] < 0)
        if g == "above200":
            return bool(x["above200"])
        if g == "vix_pct":
            v = x["vix_pct"]
            return bool(np.isfinite(v) and v >= (self.p["put_gate_x"] or 0.3))
        if g == "vrp":
            if q is None:
                return True
            rv = x["rv20"]
            return bool(np.isfinite(rv) and rv > 0 and q.iv / rv >= (self.p["put_gate_x"] or 1.1))
        raise ValueError(g)

    # kill switch (VIX term-structure / level / position loss) for put phase
    def _kill(self, eng, d) -> bool:
        p = self.p
        if not p["kill_switch"]:
            return False
        st = self._st()
        row = eng.m.stock.loc[d]
        ratio, vix = row.get("vix_ratio", np.nan), row.get("vix", np.nan)
        stress = bool((np.isfinite(ratio) and ratio > p["kill_ratio"]) or
                      (np.isfinite(vix) and vix > p["kill_vix"]))
        if st["kill_pending"]:
            for pos in list(eng.positions):
                if pos.kind == "wx_put" or p["kill_shares"]:
                    eng.close(pos, "kill_switch")
            if p["kill_shares"] and eng.shares > 0:
                eng.sell_shares(eng.shares, "kill_switch")
            st["kill_pending"] = False
            nxt = [e for e in eng.m.expiries["P"] if e > d and eng.m.is_monthly(e)]
            st["kill_until"] = nxt[0] if nxt else d
            st["kills"] += 1
            eng.kill_events = st["kills"]
            return True
        loss_hit = False
        for pos in eng.positions:
            if pos.kind != "wx_put":
                continue
            notional = sum(-l.qty * 100 * l.strike for l in pos.legs if l.qty < 0 and l.open)
            pnl = pos.entry_cash + pos.exit_cash + pos.value()
            if notional > 0 and pnl < -p["kill_loss"] * notional:
                loss_hit = True
        exposed = any(pos.kind == "wx_put" for pos in eng.positions) or \
            (p["kill_shares"] and (eng.shares > 0))
        if exposed and (stress or loss_hit):
            st["kill_pending"] = True
        if st["kill_until"] is not None:
            if d < st["kill_until"] or stress:
                return True
            st["kill_until"] = None
        return False

    def _net_credit_ok(self, eng, old_leg, q):
        cost_close = old_leg.mark + eng.costs.slip(old_leg.mark)
        new_credit = q.price - eng.costs.slip(q.price)
        return new_credit - cost_close >= 0.0

    # ------------------------------------------------------------ management
    def _manage(self, eng, d):
        p = self.p
        st = self._st()
        S = eng.spot()
        for pos in list(eng.positions):
            leg = pos.legs[0]
            dte = pos.min_dte(d)
            if pos.kind == "wx_put":
                r = self.credit_exit(eng, pos, d, p["put_tp"], None, None)
                if r:
                    eng.close(pos, r)
                    continue
                itm = S < leg.strike
                if itm and p["put_roll_tested"] and dte <= p["roll_trigger_dte"] \
                        and st["rolls"].get("put", 0) < p["max_rolls"]:
                    q = eng.m.select(d, "P", strike=leg.strike, **self.dte_kw())
                    if q is not None and q.expiry > leg.expiry and self._net_credit_ok(eng, leg, q):
                        n = -leg.qty
                        eng.close(pos, "roll_tested")
                        if eng.open([(q, -n)], "wx_put", reserve=q.strike * 100 * n,
                                    settle="physical") is not None:
                            st["rolls"]["put"] = st["rolls"].get("put", 0) + 1
                        continue
                if (not itm) and p["put_exit_dte"] is not None and dte <= p["put_exit_dte"]:
                    eng.close(pos, "dte_exit")
            elif pos.kind == "wx_call":
                r = self.credit_exit(eng, pos, d, p["call_tp"], None, None)
                if r:
                    eng.close(pos, r)
                    continue
                itm = S > leg.strike
                n = -leg.qty
                if itm and p["exdiv_roll"]:
                    div = _next_div(eng, d, 2)
                    if div > 0 and leg.mark - (S - leg.strike) < div * 1.5:
                        q = eng.m.select(d, "C", delta=p["call_delta"], **self.call_dte_kw())
                        if q is not None and q.expiry > leg.expiry and q.strike >= leg.strike:
                            eng.close(pos, "exdiv_roll")
                            eng.open([(q, -n)], "wx_call", reserve=0.0, settle="physical")
                            continue
                if itm and p["call_roll_tested"] and dte <= p["roll_trigger_dte"] \
                        and st["rolls"].get("call", 0) < p["max_rolls"]:
                    q = eng.m.select(d, "C", delta=p["call_delta"], **self.call_dte_kw())
                    ok = q is not None and q.expiry > leg.expiry and q.strike >= leg.strike \
                        and self._net_credit_ok(eng, leg, q)
                    if not ok:
                        q = eng.m.select(d, "C", strike=leg.strike, **self.call_dte_kw())
                        ok = q is not None and q.expiry > leg.expiry and self._net_credit_ok(eng, leg, q)
                    if ok:
                        eng.close(pos, "roll_tested")
                        eng.open([(q, -n)], "wx_call", reserve=0.0, settle="physical")
                        st["rolls"]["call"] = st["rolls"].get("call", 0) + 1
                        continue
                if (not itm) and p["call_exit_dte"] is not None and dte <= p["call_exit_dte"]:
                    eng.close(pos, "dte_exit")

    # ------------------------------------------------------------ entries
    def _sell_put(self, eng, d, blocked):
        p = self.p
        if blocked or not self._gate_put(eng, d):
            return
        if eng.free_cash() * p["alloc"] < 0.5 * eng.spot() * 100:
            eng.blocked("infeasible: securing 1 put exceeds available cash")
            return
        q = eng.m.select(d, "P", delta=p["put_delta"], **self.dte_kw())
        if q is None or not self._gate_put(eng, d, q):
            return
        n = int(eng.free_cash() * p["alloc"] // (q.strike * 100))
        if n == 0:
            eng.blocked("infeasible: securing 1 put exceeds available cash")
            return
        if eng.open([(q, -n)], "wx_put", reserve=q.strike * 100 * n, settle="physical") is not None:
            self._st()["rolls"]["put"] = 0

    def _buy_lots(self, eng):
        p = self.p
        n = int(min(eng.equity() * p["alloc"], eng.cash) // (100 * eng.spot() * 1.001))
        if n == 0:
            eng.blocked("infeasible: 100 shares exceed capital")
            return
        eng.buy_shares(100 * n, "buy_lots")

    def _sell_call(self, eng, d, lots):
        p = self.p
        if not self._gate_call(eng, d):
            return
        q = eng.m.select(d, "C", delta=p["call_delta"], **self.call_dte_kw())
        if q is None:
            return
        mode = p["call_basis"]
        if mode in ("strict", "engine", "net"):
            basis = eng.share_basis
            if mode == "net":
                basis -= self._st()["premium"] / max(eng.shares, 1)
            if q.strike < basis:
                g = eng.m._by_exp[(q.expiry, "C")]
                ks = g[g.strike_price >= basis].strike_price
                q2 = None
                if len(ks):
                    q2 = eng.m.select(d, "C", strike=float(ks.iloc[0]), expiry=q.expiry, iv_hint=q.iv)
                if q2 is not None and q2.price >= 0.05:
                    q = q2
                elif mode in ("strict", "net"):
                    return            # hold shares naked rather than sell below basis
        if eng.open([(q, -lots)], "wx_call", reserve=0.0, settle="physical") is not None:
            self._st()["rolls"]["call"] = 0

    def _track_premium(self, eng):
        """Net premium collected since the last call-away (for call_basis='net')."""
        st = self._st()
        k = st.setdefault("k", 0)
        prev = st.setdefault("prev_shares", 0.0)
        new = eng.closed[k:]
        st["k"] = len(eng.closed)
        if prev > 0 and eng.shares == 0:
            st["premium"] = 0.0
        else:
            for pos in new:
                if pos.settle == "physical" and pos.meta.get("assigned"):
                    st["premium"] += pos.entry_cash
                else:
                    st["premium"] += pos.entry_cash + pos.exit_cash
        st["prev_shares"] = eng.shares

    def on_day(self, eng, d):
        p = self.p
        self._track_premium(eng)
        self._manage(eng, d)
        blocked = self._kill(eng, d)
        # CSP mode: dump assigned shares
        if p["on_assign"] == "sell" and eng.shares > 0 and not any(
                pos.kind == "wx_call" for pos in eng.positions):
            eng.sell_shares(eng.shares, "csp_assigned")
        if eng.positions:
            return
        lots = int(round(eng.shares / 100))
        if lots == 0:
            if p["entry"] == "stock":
                if not blocked:
                    self._buy_lots(eng)
                    lots = int(round(eng.shares / 100))
                    if lots:
                        self._sell_call(eng, d, lots)
            else:
                self._sell_put(eng, d, blocked)
        else:
            if eng.shares % 100:
                eng.sell_shares(eng.shares % 100, "odd_lot")
            self._sell_call(eng, d, lots)


# ---------------------------------------------------------------- CollarX
class CollarX(Strategy):
    """Long 100-share lots + long put (own tenor, rolled) + short call.
    zero_cost=True: put and call on the same expiry; call strike is the
    lowest-delta call whose premium covers the put (floor call_delta_min)."""
    id, name = "collar_x", "Collar (tunable)"
    defaults = dict(put_delta=0.25, call_delta=0.30, dte_min=30, dte_max=45, dte_target=38,
                    put_dte_min=None, put_dte_max=None, put_dte_target=None, put_roll_dte=21,
                    call_roll_dte=21, call_tp=None, zero_cost=False, call_delta_min=0.10,
                    call_gate=None, alloc=1.0)

    def put_kw(self):
        p = self.p
        if p["put_dte_min"] is None:
            return self.dte_kw()
        return dict(dte_min=p["put_dte_min"], dte_max=p["put_dte_max"],
                    dte_target=p["put_dte_target"], monthly_only=False)

    def on_day(self, eng, d):
        p = self.p
        for pos in list(eng.positions):
            dte = pos.min_dte(d)
            if pos.kind == "cx_call":
                if p["call_roll_dte"] is not None and dte <= p["call_roll_dte"]:
                    eng.close(pos, "roll")
                elif p["call_tp"] is not None:
                    l = pos.legs[0]
                    if l.mark <= l.entry_mark * (1 - p["call_tp"]):
                        eng.close(pos, "take_profit")
            elif pos.kind == "cx_put":
                if p["put_roll_dte"] is not None and dte <= p["put_roll_dte"]:
                    eng.close(pos, "roll")
            elif pos.kind == "cx_both":
                if p["call_roll_dte"] is not None and dte <= p["call_roll_dte"]:
                    eng.close(pos, "roll")
        # shares
        while eng.cash < 0 and eng.shares >= 100:
            eng.sell_shares(100, "raise_cash")
        have = int(round(eng.shares / 100))
        if have == 0 and not eng.positions:
            n = int(min(eng.equity() * p["alloc"] * 0.985, eng.cash) // (100 * eng.spot() * 1.001))
            if n == 0:
                eng.blocked("infeasible: 100 shares exceed capital")
                return
            eng.buy_shares(100 * n, "buy_lots")
            have = n
        if have == 0:
            return
        kinds = {pos.kind for pos in eng.positions}
        if p["zero_cost"]:
            if "cx_both" in kinds:
                return
            put = eng.m.select(d, "P", delta=p["put_delta"], **self.dte_kw())
            if put is None:
                return
            call = None
            for cd in np.arange(p["call_delta"], p["call_delta_min"] - 1e-9, -0.05):
                c = eng.m.select(d, "C", delta=float(cd), expiry=put.expiry, iv_hint=put.iv)
                if c is None:
                    continue
                if c.price >= put.price or call is None:
                    call = c
                if c.price < put.price:
                    break
            if call is None:
                return
            eng.open([(call, -have), (put, have)], "cx_both", reserve=0.0)
            return
        if "cx_put" not in kinds:
            put = eng.m.select(d, "P", delta=p["put_delta"], **self.put_kw())
            if put is not None:
                cost = (put.price + eng.costs.slip(put.price)) * 100 * have
                if eng.free_cash() >= cost:
                    eng.open([(put, have)], "cx_put", reserve=0.0)
        if "cx_call" not in kinds:
            if p["call_gate"] == "rsi2_skip_low" and indicators(eng.m).loc[d, "rsi2"] < 10:
                return
            call = eng.m.select(d, "C", delta=p["call_delta"], **self.dte_kw())
            if call is not None:
                eng.open([(call, -have)], "cx_call", reserve=0.0)


WHEEL_REGISTRY = {c.id: c for c in [WheelX, CollarX]}
