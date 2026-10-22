"""New-rule variants of the existing finalists (research code for tuning/newrules/).

Built on the core Strategy / Engine API; nothing in engine.py, strategies.py,
strategies_signals.py or strategies_spreads.py is modified. Own registry:
NEWRULES_REGISTRY.

Rules (research/papers_technical_longdated.md section 8, research/papers_news.md 5.2)
--------------------------------------------------------------------------------------
DipPCSNR extends the frozen dip put-credit-spread finalist (DipPCS):
  R1 volatility-premium gate   vrp_k: enter only if IV_index - RV10*100 > k
                               (IV_index = VIX for SPY, VXN for QQQ, RVX for IWM when
                               vrp_index="own"; Cboe daily closes). vrp_soft: keep every
                               entry, scale the risk budget by clip((IV-RV10)/5, .5, 1.5).
  R2 term-structure expiry     ts_rule="vix9d": VIX9D/VIX > 1 -> sell the long_dte window
                               (30-45 DTE) at the same delta, else 7-14 DTE as now.
                               ts_rule="vix3m": same with VIX/VIX3M. long_exits="same" keeps
                               the finalist exits; "hold" uses TP 50% / close at 21 DTE only.
  R3 timing                    exit_at="open": exit decisions taken at the close (TP, close>5DMA,
                               time) are executed at the NEXT open, at the option bar's open
                               print (Alpaca daily bars carry o). open_tp: also take profit when
                               the open marks reach the TP. entry_at="next_open": the legs picked
                               at the signal close are filled at the next open. entry_day=
                               "thu_fri"/"fri": a Mon-Wed (Mon-Thu) signal waits for the first
                               allowed weekday close (cancelled if the dip has already reverted
                               above the 5DMA or after entry_wait sessions).
  R4 news filter               news_flag=<name>: skip entries on flagged sessions;
                               news_action="skip_close" also closes open positions at that
                               session's decision time. Flags live in NEWS_FLAGS[name][symbol]
                               (sets of session dates), built by tuning/newrules/news.py.

SpreadBookNR extends SpreadBook (held-to-expiry SPY condor finalist) with the R4 news filter.

Decision time: FRAMES[symbol] can hold the 15:55 ET feature frame built by
tuning/live_timing/run_timing.t_features (the live bot decides at 15:55); when
set, the strategy reads it instead of the daily-close frame.
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind
from . import pricing as px
from . import strategies_signals as SS
from .strategies_signals import DipPCS
from .strategies_spreads import SpreadBook

ROOT = Path(__file__).resolve().parent.parent
NR_CACHE = ROOT / "tuning" / "newrules" / ".cache"
VOL_INDEX = {"SPY": "VIX", "QQQ": "VXN", "IWM": "RVX"}

FRAMES: dict = {}            # symbol -> substitute feature frame (15:55 snapshot)
NEWS_FLAGS: dict = {}        # flag name -> {symbol -> set(pd.Timestamp)}
_CBOE: dict = {}


# --------------------------------------------------------------------------- data
def cboe(sym: str) -> pd.Series | None:
    """Cboe daily close for an index (VIX, VXN, RVX, VIX9D, VIX3M)."""
    if sym in _CBOE:
        return _CBOE[sym]
    for f in (NR_CACHE / "cboe" / f"{sym}_History.csv", ROOT / ".cache" / "charts" / f"{sym}_History.csv"):
        if f.exists():
            df = pd.read_csv(f)
            s = pd.Series(df["CLOSE"].values, index=pd.to_datetime(df["DATE"], format="%m/%d/%Y")).sort_index()
            _CBOE[sym] = s[~s.index.duplicated()]
            return _CBOE[sym]
    _CBOE[sym] = None
    return None


class OpenBars:
    """Option daily bars WITH the open print (o, h, l, c, v), GET only, cached under
    tuning/newrules/.cache/obars. One shared instance; ~1 request/second."""

    def __init__(self):
        self.dir = NR_CACHE / "obars"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict = {}
        self._client = None
        self.n_requests = 0

    def client(self):
        if self._client is None:
            from .data import AlpacaData
            self._client = AlpacaData()
            self._client.min_interval = float(os.environ.get("NEWRULES_MIN_INTERVAL", "1.0"))
        return self._client

    @staticmethod
    def window(exp: pd.Timestamp) -> tuple[str, str]:
        lo = max(pd.Timestamp("2024-01-01"), exp - pd.Timedelta(days=75))
        hi = min(exp, pd.Timestamp("2026-10-21"))
        return lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")

    def _path(self, sym, a, b):
        import hashlib
        return self.dir / (hashlib.sha1(f"{sym}|{a}|{b}".encode()).hexdigest()[:16] + ".json")

    def get(self, symbols: list[str], exp: pd.Timestamp) -> dict:
        a, b = self.window(exp)
        out, todo = {}, []
        for s in symbols:
            if (s, a, b) in self._mem:
                out[s] = self._mem[(s, a, b)]
                continue
            p = self._path(s, a, b)
            if p.exists():
                out[s] = self._mem[(s, a, b)] = _bars_df(json.loads(p.read_text()))
            else:
                todo.append(s)
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            got = {s: [] for s in batch}
            params = {"symbols": ",".join(batch), "timeframe": "1Day", "start": a, "end": b, "limit": 10000}
            while True:
                js = self.client()._get("https://data.alpaca.markets/v1beta1/options/bars", params)
                self.n_requests += 1
                for s, bars in (js.get("bars") or {}).items():
                    got.setdefault(s, []).extend(bars)
                tok = js.get("next_page_token")
                if not tok:
                    break
                params["page_token"] = tok
            for s in batch:
                slim = [{"t": x["t"][:10], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"],
                         "v": x.get("v", 0)} for x in got.get(s, [])]
                tmp = self._path(s, a, b).with_suffix(".tmp")
                tmp.write_text(json.dumps(slim))
                os.replace(tmp, self._path(s, a, b))
                out[s] = self._mem[(s, a, b)] = _bars_df(slim)
        return out

    def open_price(self, sym: str, exp: pd.Timestamp, d: pd.Timestamp):
        df = self.get([sym], exp)[sym]
        if d in df.index:
            v = float(df.at[d, "o"])
            return v if v > 0 else None
        return None


def _bars_df(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v"])
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("t"))
    return df.sort_index()


OBARS = OpenBars()


# --------------------------------------------------------------------------- helpers
def _flagged(name, sym, d) -> bool:
    if not name:
        return False
    return d in NEWS_FLAGS.get(name, {}).get(sym, set())


class _FrameSwap:
    """Context manager: make strategies_signals.features() return FRAMES[sym]."""

    def __init__(self, sym, use):
        self.frame = FRAMES.get(sym) if use else None

    def __enter__(self):
        self.orig = SS.features
        if self.frame is not None:
            SS.features = lambda m, _f=self.frame: _f
        return self

    def __exit__(self, *a):
        SS.features = self.orig


# --------------------------------------------------------------------------- R1-R4 on the dip PCS
class DipPCSNR(DipPCS):
    id, name = "dip_pcs_nr", "Dip put credit spread + new rules (VRP / term structure / timing / news)"
    defaults = {**DipPCS.defaults,
                "signal": "dip", "rsi_lo": 10, "ibs_lo": 0.2, "trend": "sma200", "structure": "pcs",
                "short_delta": 0.25, "long_delta": 0.10, "dte_min": 7, "dte_max": 14, "dte_target": 10,
                "tp": 0.5, "max_days": 5, "exit_sma5": True,
                "frame": "1555",                 # "1555" (live decision time) | "close"
                # R1
                "vrp_k": None, "vrp_index": "own", "vrp_soft": False, "vrp_lag": 0,
                # R2
                "ts_rule": None, "ts_lag": 0, "long_dte_min": 30, "long_dte_max": 45, "long_dte_target": 38,
                "long_exits": "same", "long_tp": 0.5, "long_close_dte": 21,
                # R3
                "exit_at": "close", "open_tp": False, "entry_at": "close",
                "entry_day": None, "entry_wait": 4,
                # R4
                "news_flag": None, "news_action": "skip"}

    # ---------------------------------------------------------------- data
    def _vol_index(self, sym):
        name = VOL_INDEX.get(sym, "VIX") if self.p["vrp_index"] == "own" else self.p["vrp_index"]
        return cboe(name)

    def vrp(self, eng, d) -> float:
        key = "_vrp_ser"
        ser = self.__dict__.get(key)
        if ser is None:
            s = eng.m.stock
            rv10 = ind.rv_close(s["split_close"].astype(float), 10) * 100
            iv = self._vol_index(eng.m.symbol)
            iv = iv.reindex(s.index).ffill(limit=5) if iv is not None else pd.Series(np.nan, index=s.index)
            ser = (iv - rv10).shift(self.p["vrp_lag"])
            self.__dict__[key] = ser
        v = ser.get(d, np.nan)
        return float(v) if v is not None else np.nan

    def ts_ratio(self, d) -> float:
        rule = self.p["ts_rule"]
        if rule == "vix9d":
            a, b = cboe("VIX9D"), cboe("VIX")
        else:
            a, b = cboe("VIX"), cboe("VIX3M")
        if a is None or b is None:
            return np.nan
        try:
            r = (a / b).dropna().loc[:d]
            if self.p.get("ts_lag"):
                r = r[r.index < d]          # prior session's closes only
            return float(r.iloc[-1]) if len(r) else np.nan
        except Exception:
            return np.nan

    # ------------------------------------------------------------- overrides
    def dte_kw(self, prefix=""):
        if prefix == "" and self.__dict__.get("_long_today"):
            p = self.p
            return dict(dte_min=p["long_dte_min"], dte_max=p["long_dte_max"],
                        dte_target=p["long_dte_target"], monthly_only=False)
        return super().dte_kw(prefix)

    def size(self, eng, max_loss_per):
        if max_loss_per <= 0:
            return 0
        scale = self.__dict__.get("_size_scale", 1.0)
        budget = min(eng.equity() * self.p["risk_frac"] * scale, eng.free_cash())
        return int(budget // max_loss_per)

    def signal_ok(self, eng, d) -> bool:
        p = self.p
        base = super().signal_ok(eng, d)
        ed = p["entry_day"]
        if ed:
            allowed = d.weekday() in ((3, 4) if ed == "thu_fri" else (4,))
            f = SS.features(eng.m)
            i = int(f.at[d, "pos"])
            if base and allowed:
                self._pend = None
                ok = True
            elif base:
                self._pend = i
                ok = False
            elif self.__dict__.get("_pend") is not None and allowed:
                r = f.loc[d]
                fresh = i - self._pend <= p["entry_wait"]
                trend = np.isfinite(r["sma200"]) and r["close"] > r["sma200"]
                still_dip = not (np.isfinite(r["sma5"]) and r["close"] > r["sma5"])
                ok = bool(fresh and trend and still_dip)
                self._pend = None
            else:
                if self.__dict__.get("_pend") is not None and i - self._pend > p["entry_wait"]:
                    self._pend = None
                ok = False
        else:
            ok = base
        if not ok:
            return False
        if _flagged(p["news_flag"], eng.m.symbol, d):
            eng.note("skipped entry: news flag")
            return False
        if p["vrp_k"] is not None:
            v = self.vrp(eng, d)
            if not (np.isfinite(v) and v > p["vrp_k"]):
                return False
        self._size_scale = 1.0
        if p["vrp_soft"]:
            v = self.vrp(eng, d)
            self._size_scale = float(np.clip(v / 5.0, 0.5, 1.5)) if np.isfinite(v) else 1.0
        if p["ts_rule"]:
            r = self.ts_ratio(d)
            self._long_today = bool(np.isfinite(r) and r > 1.0)
        return True

    def manage(self, eng, d):
        p = self.p
        longs = [x for x in eng.positions if x.meta.get("long") and p["long_exits"] == "hold"]
        if longs:
            for pos in longs:
                r = self.credit_exit(eng, pos, d, p["long_tp"], p["sl_mult"], p["long_close_dte"])
                if r:
                    self._exit(eng, pos, r)
            long_ids = {x.id for x in longs}
            keep = [x for x in eng.positions]          # after the long exits above
            eng.positions = [x for x in keep if x.id not in long_ids]
            try:
                self._manage_core(eng, d)
            finally:
                closed = {x.id for x in eng.closed}
                eng.positions = [x for x in keep if x.id not in closed]
        else:
            self._manage_core(eng, d)

    def _manage_core(self, eng, d):
        if self.p["exit_at"] != "open":
            return super().manage(eng, d)
        orig = eng.close
        eng.close = lambda pos, reason: pos.meta.setdefault("pending_exit", reason)
        try:
            super().manage(eng, d)
        finally:
            del eng.close

    def _exit(self, eng, pos, reason):
        if self.p["exit_at"] == "open":
            pos.meta.setdefault("pending_exit", reason)
        else:
            eng.close(pos, reason)

    # ---------------------------------------------------------- open-time fills
    @staticmethod
    def _open_spot(eng, d) -> float:
        return float(eng.m.stock.at[d, "open"])

    def _leg_open_price(self, eng, d, sym, K, exp, cp, iv):
        S_o = self._open_spot(eng, d)
        intrinsic = max(0.0, K - S_o) if cp == "P" else max(0.0, S_o - K)
        po = OBARS.open_price(sym, exp, d)
        if po is None:
            self.__dict__["_n_open_model"] = self.__dict__.get("_n_open_model", 0) + 1
            T = max((exp - d).days, 0) / 365.0 + 6.5 / (24 * 365)   # open: ~6.5h more time value
            po = px.bs_price(S_o, K, T, eng.m.r(d), eng.m.q, iv, cp) if iv and T > 0 else intrinsic
        else:
            self.__dict__["_n_open_print"] = self.__dict__.get("_n_open_print", 0) + 1
        return max(float(po), intrinsic)

    def _close_at_open(self, eng, pos, d, reason):
        for l in pos.legs:
            if l.open:
                l.mark = self._leg_open_price(eng, d, l.symbol, l.strike, l.expiry, l.cp, l.iv)
        eng.close(pos, reason + "@open")

    def _open_phase(self, eng, d):
        p = self.p
        for pos in list(eng.positions):
            if pos.open_date >= d:
                continue
            if pos.meta.get("pending_exit"):
                self._close_at_open(eng, pos, d, pos.meta["pending_exit"])
                continue
            if p["open_tp"] and pos.credit() > 0:
                tpv = p["long_tp"] if pos.meta.get("long") else p["tp"]
                cost = 0.0
                for l in pos.legs:
                    if l.open:
                        cost -= l.qty * 100 * self._leg_open_price(eng, d, l.symbol, l.strike, l.expiry, l.cp, l.iv)
                if tpv is not None and cost <= pos.credit() * (1 - tpv):
                    self._close_at_open(eng, pos, d, "take_profit")
        pe = self.__dict__.pop("_pending_entry", None)
        if pe is not None and len(eng.positions) < p["max_positions"]:
            legs, kind, long_flag = pe
            new = []
            for q, n in legs:
                po = self._leg_open_price(eng, d, q.symbol, q.strike, q.expiry, q.cp, q.iv)
                new.append(dataclasses.replace(q, price=po))
            sq, lq = new[0], new[1]
            width = abs(sq.strike - lq.strike)
            credit = sq.price - lq.price
            if credit <= 0.05 or credit > p["max_credit_frac"] * width:
                eng.note("skipped next-open entry: credit guard")
                return
            n = self.size(eng, (width - credit) * 100)
            if n == 0:
                return
            pos = eng.open([(sq, -n), (lq, n)], kind, reserve=width * 100 * n)
            if pos is not None and long_flag:
                pos.meta["long"] = True

    # ------------------------------------------------------------------ day
    def on_day(self, eng, d):
        with _FrameSwap(eng.m.symbol, self.p["frame"] == "1555"):
            self._day(eng, d)

    def _day(self, eng, d):
        p = self.p
        self._long_today = False
        self._size_scale = 1.0
        if p["exit_at"] == "open" or p["open_tp"] or p["entry_at"] == "next_open":
            self._open_phase(eng, d)
        if p["news_action"] == "skip_close" and _flagged(p["news_flag"], eng.m.symbol, d):
            for pos in list(eng.positions):
                eng.close(pos, "news_exit")
        n0 = {x.id for x in eng.positions}
        if p["entry_at"] == "next_open":
            orig = eng.open

            def capture(legs, kind, reserve=0.0, **kw):
                self._pending_entry = (legs, kind, self._long_today)
                return None
            eng.open = capture
            try:
                super().on_day(eng, d)
            finally:
                del eng.open
        else:
            super().on_day(eng, d)
        if self._long_today:
            for x in eng.positions:
                if x.id not in n0 and x.open_date == d:
                    x.meta["long"] = True


# --------------------------------------------------------------------------- R4 on the held condor
class SpreadBookNR(SpreadBook):
    id, name = "spread_book_nr", "Spread book + news filter (skip / close on flagged sessions)"
    defaults = {**SpreadBook.defaults, "news_flag": None, "news_action": "skip"}

    def on_day(self, eng, d):
        p = self.p
        if _flagged(p["news_flag"], eng.m.symbol, d):
            self._manage(eng, d)
            if p["news_action"] == "skip_close":
                for pos in list(eng.positions):
                    eng.close(pos, "news_exit")
            eng.note("skipped entry: news flag")
            return
        super().on_day(eng, d)


NEWRULES_REGISTRY = {c.id: c for c in [DipPCSNR, SpreadBookNR]}
