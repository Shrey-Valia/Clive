#!/usr/bin/env python3
"""Re-validate the frozen dip_pcs finalist at the live decision time.

Run from the Options Bot folder:

    python3 tuning/live_timing/run_timing.py

What it does
------------
* Signal snapshots. For each trading day and each decision time T (minutes
  before that day's close: 15, 10, 5, 4, 0=close), today's partial bar is
  rebuilt from Alpaca SIP 1-minute bars (adjustment=split) whose start < T:
  open = first regular-session bar, high/low = extremes so far, close = last
  price before T. RSI(2), IBS, SMA5 and SMA200 are recomputed on the prior
  daily closes plus that partial bar (exactly what live/signals.py does).
  T=close uses the research feature frame unchanged (baseline).
* Backtest. The frozen finalist (DipPCS with the finalists.json params) runs
  in the unchanged optionsbot engine. A subclass swaps the feature frame the
  strategy reads (entry signal, sma5 exit, close) for the T-snapshot frame, so
  entry and end-of-day exit decisions are made with T information.
* Option fills:
    - "close": options filled at the daily close (research fill model).
    - "tproxy": every leg filled at a T-time proxy price: the same Black-Scholes
      model at the leg's close-implied IV, repriced at the T spot,
      P_T = P_close + BS(S_T) - BS(S_close). The take-profit test uses the
      same T marks. Theta over <=15 minutes is ignored.
* Live-feed variant ("iex16"): the free plan sees SIP only up to 16 minutes
  before T; the last 16 minutes come from IEX (the live bot's construction,
  live/signals.live_bar). Last price = last IEX bar close before T.
* Portfolio: equal-weight daily-rebalanced basket of the three $10k ETF
  sleeves (tuning/signals/common.basket), IS to 2025-06-30, OOS after.

Outputs (this folder): results.csv, trade_diff.csv, signal_days.csv,
summary.json, equity.png. Minute bars are cached in .cache/ (gitignored).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tuning" / "signals"))

import common as K                                       # noqa: E402
from optionsbot import indicators as ind                 # noqa: E402
from optionsbot import pricing as px                     # noqa: E402
from optionsbot import strategies_signals as SS          # noqa: E402
from optionsbot.runner import market, run_one            # noqa: E402

CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)
ET = "America/New_York"
SYMS = ["SPY", "QQQ", "IWM"]
PARAMS = dict(signal="dip", rsi_lo=10, ibs_lo=0.2, trend="sma200", structure="pcs", short_delta=0.25,
              long_delta=0.1, dte_min=7, dte_max=14, dte_target=10, tp=0.5, max_days=5, exit_sma5=True)
# Early closes (13:00 ET) in the sample (NYSE calendar).
HALF_DAYS = {"2024-07-03", "2024-11-29", "2024-12-24", "2025-07-03", "2025-11-28", "2025-12-24"}
T_MINUTES = {"15:45": 15, "15:50": 10, "15:55": 5, "15:56": 4, "close": 0}
SIP_DELAY = 16


# ---------------------------------------------------------------- minute bars
def minutes(sym: str, feed: str) -> pd.DataFrame:
    """1-minute bars (split-adjusted), cached. Downloads via the live data client
    (data.alpaca.markets GETs only) when the cache is missing."""
    f = CACHE / f"min_{sym}_{feed}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    from live.broker import Broker
    b = Broker(execute=False)
    rows, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Min", "start": "2024-02-28T13:00:00Z",
             "end": "2026-10-21T00:00:00Z", "feed": feed, "limit": 10000, "adjustment": "split"}
        if tok:
            p["page_token"] = tok
        js = b._data("/v2/stocks/bars", p)
        rows += (js.get("bars") or {}).get(sym, [])
        tok = js.get("next_page_token")
        if not tok:
            break
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"]).dt.tz_convert(ET)
    df = df.set_index("t")[["o", "h", "l", "c"]]
    df.to_pickle(f)
    return df


def close_time(day: dt.date) -> pd.Timestamp:
    h = 13 if str(day) in HALF_DAYS else 16
    return pd.Timestamp(dt.datetime.combine(day, dt.time(h, 0)), tz=ET)


def partial_bars(sym: str, days, variant: str = "sip") -> dict:
    """{T label: DataFrame(open, high, low, close) indexed by day} for T != close."""
    sip = minutes(sym, "sip")
    gs = {k: v for k, v in sip.groupby(sip.index.date)}
    gi = {}
    if variant == "iex16":
        iex = minutes(sym, "iex")
        gi = {k: v for k, v in iex.groupby(iex.index.date)}
    out = {lab: {} for lab in T_MINUTES}
    for d in days:
        day = d.date()
        si = gs.get(day)
        if si is None:
            continue
        t0 = pd.Timestamp(dt.datetime.combine(day, dt.time(9, 30)), tz=ET)
        ct = close_time(day)
        for lab, mb in T_MINUTES.items():
            T = ct - pd.Timedelta(minutes=mb)
            s = si[(si.index >= t0) & (si.index < T)]
            if s.empty:
                continue
            if variant == "sip":
                out[lab][d] = (s.o.iloc[0], s.h.max(), s.l.min(), s.c.iloc[-1])
            else:                                   # live free-plan view
                sd = s[s.index < T - pd.Timedelta(minutes=SIP_DELAY)]
                ie = gi.get(day)
                ie = ie[(ie.index >= t0) & (ie.index < T)] if ie is not None else None
                if ie is None or ie.empty or sd.empty:
                    continue
                last = ie.c.iloc[-1]
                out[lab][d] = (sd.o.iloc[0], max(ie.h.max(), sd.h.max(), last),
                               min(ie.l.min(), sd.l.min(), last), last)
    return {lab: pd.DataFrame.from_dict(v, orient="index", columns=["open", "high", "low", "close"])
            for lab, v in out.items()}


# ---------------------------------------------------------------- T features
def t_features(m, part: pd.DataFrame) -> pd.DataFrame:
    """Copy of the research feature frame with close/rsi2/ibs/sma5/sma200 on each
    day d replaced by values from prior daily bars + the T partial bar.
    Also stores spot_T (raw, unadjusted) for the fill proxy."""
    base = SS.features(m)
    f = base.copy()
    s = m.stock
    k = s["split_close"] / s["close"]
    c_all = s["split_close"].astype(float)
    hist_start = c_all.index.get_loc(pd.Timestamp("2024-03-01")) - 1
    f["spot_T"] = np.nan
    f["has_T"] = False
    vals = c_all.values.copy()
    idx = c_all.index
    for i in range(hist_start + 1, len(idx)):
        d = idx[i]
        if d not in part.index:
            continue
        o, h, l, c = part.loc[d]
        h, l = max(h, c), min(l, c)
        ser = pd.Series(np.append(vals[:i], c), index=idx[:i + 1])
        f.at[d, "close"] = c
        f.at[d, "rsi2"] = float(ind.rsi(ser, 2).iloc[-1])
        f.at[d, "ibs"] = (c - l) / (h - l) if h > l else 0.5
        f.at[d, "sma5"] = float(ser.iloc[-5:].mean())
        f.at[d, "sma200"] = float(ser.iloc[-200:].mean())
        f.at[d, "spot_T"] = c / float(k.loc[d])
        f.at[d, "has_T"] = True
    return f


# ---------------------------------------------------------------- strategy
class DipPCSAtT(SS.DipPCS):
    """DipPCS reading a substitute feature frame; optional T-time fill proxy."""
    id = "dip_pcs_at_t"
    FRAMES: dict = {}          # symbol -> feature frame
    FILL = "close"             # "close" | "tproxy"

    def on_day(self, eng, d):
        frame = self.FRAMES.get(eng.m.symbol)
        orig = SS.features
        if frame is not None:
            SS.features = lambda m, _f=frame: _f
        try:
            if self.FILL == "tproxy" and frame is not None and bool(frame.at[d, "has_T"]):
                self._on_day_tproxy(eng, d, float(frame.at[d, "spot_T"]))
            else:
                super().on_day(eng, d)
        finally:
            SS.features = orig

    # -- T fill proxy: legs priced at S_T with their close IV
    @staticmethod
    def _shift(m, d, K, exp, cp, iv, S_T, p_close):
        if iv is None or not np.isfinite(iv) or iv <= 0:
            return p_close
        T = m.T(d, exp)
        if T <= 0:
            return p_close
        S0 = m.spot(d)
        a = px.bs_price(S_T, K, T, m.r(d), m.q, iv, cp)
        b = px.bs_price(S0, K, T, m.r(d), m.q, iv, cp)
        intrinsic = max(0.0, K - S_T) if cp == "P" else max(0.0, S_T - K)
        return max(p_close + a - b, intrinsic, 0.01)

    def _on_day_tproxy(self, eng, d, S_T):
        m = eng.m
        saved = {}
        for pos in eng.positions:                    # T marks for TP test and exit fills
            for l in pos.legs:
                if l.open:
                    saved[id(l)] = l.mark
                    l.mark = self._shift(m, d, l.strike, l.expiry, l.cp, l.iv, S_T, l.mark)
        orig_open = eng.open
        opened = {}

        def open_at_t(legs, kind, reserve=0.0, **kw):
            new = []
            for q, n in legs:
                if q is None:
                    return None
                p_t = self._shift(m, d, q.strike, q.expiry, q.cp, q.iv, S_T, q.price)
                new.append((dataclasses.replace(q, price=p_t), n))
                opened[q.symbol] = q.price
            if kind in ("pcs",):
                w = abs(legs[0][0].strike - legs[1][0].strike)
                cr = new[0][0].price - new[1][0].price
                if cr <= 0.05 or cr > self.p["max_credit_frac"] * w:
                    return None
            return orig_open(new, kind, reserve=reserve, **kw)

        eng.open = open_at_t
        try:
            super().on_day(eng, d)
        finally:
            del eng.open
        for pos in eng.positions:                    # restore close marks for the equity record
            for l in pos.legs:
                if id(l) in saved:
                    l.mark = saved[id(l)]
                elif l.symbol in opened and pos.open_date == d:
                    l.mark = opened[l.symbol]


# ---------------------------------------------------------------- runner
def run_variant(label: str, frames: dict | None, fill: str) -> dict:
    res = {}
    for sym in SYMS:
        if frames is None and fill == "close":
            r = K.run_cached("dip_pcs", PARAMS, sym)
            res[sym] = {"equity": r["equity"], "trades": r["trades"]}
            continue
        DipPCSAtT.FRAMES = {sym: frames[sym]} if frames else {}
        DipPCSAtT.FILL = fill
        r = run_one(DipPCSAtT, PARAMS, sym, K.START, K.END, K.CAPITAL)
        res[sym] = {"equity": r["equity"], "trades": r["trades"]}
    return res


def metrics_row(label, fill, res, ref_trades: pd.DataFrame | None):
    eqs = {s: r["equity"]["equity"] for s, r in res.items()}
    b = K.basket(eqs)
    tr = pd.concat([K.trade_returns(r["trades"]) for r in res.values()], ignore_index=True)
    row = {"T": label, "fill": fill}
    for tag, (a, z) in {"is": (None, K.IS_END), "oos": (K.OOS_START, None), "full": (None, None)}.items():
        mm = K.em(b, a, z)
        row[f"{tag}_sharpe"] = mm.get("sharpe")
        row[f"{tag}_cagr"] = mm.get("cagr")
        row[f"{tag}_maxdd"] = mm.get("max_drawdown")
        t = tr if tag == "full" else (tr[tr.open_date <= K.IS_END] if tag == "is" else tr[tr.open_date >= K.OOS_START])
        row[f"{tag}_trades"] = int(len(t))
        row[f"{tag}_win"] = float((t.pnl > 0).mean()) if len(t) else float("nan")
        row[f"{tag}_pnl"] = float(t.pnl.sum())
        row[f"{tag}_ret_mean"] = float(t.ret.mean()) if len(t) else float("nan")
    for s, e in eqs.items():
        row[f"sh_{s}_is"] = K.em(e, None, K.IS_END).get("sharpe")
        row[f"sh_{s}_oos"] = K.em(e, K.OOS_START, None).get("sharpe")
    diff = None
    if ref_trades is not None:
        a = tr.assign(key=tr.underlying + "|" + tr.open_date.dt.strftime("%Y-%m-%d"))
        r0 = ref_trades.assign(key=ref_trades.underlying + "|" + ref_trades.open_date.dt.strftime("%Y-%m-%d"))
        ka, kr = set(a.key), set(r0.key)
        kept, missed, added = ka & kr, kr - ka, ka - kr
        row.update(kept=len(kept), missed=len(missed), added=len(added),
                   missed_ref_pnl=float(r0[r0.key.isin(missed)].pnl.sum()),
                   added_pnl=float(a[a.key.isin(added)].pnl.sum()),
                   kept_pnl=float(a[a.key.isin(kept)].pnl.sum()),
                   kept_ref_pnl=float(r0[r0.key.isin(kept)].pnl.sum()),
                   added_win=float((a[a.key.isin(added)].pnl > 0).mean()) if added else float("nan"))
        diff = pd.concat([
            r0[r0.key.isin(missed)].assign(status="missed"),
            a[a.key.isin(added)].assign(status="added"),
        ]).assign(T=label, fill=fill)
    return row, b, tr, diff


def main():
    days = {s: SS.features(market(s)).loc[K.START:K.END].index for s in SYMS}
    partial = {v: {s: partial_bars(s, days[s], v) for s in SYMS} for v in ("sip", "iex16")}

    # sanity: minute rebuild at the close vs the daily bar
    chk = []
    for s in SYMS:
        m = market(s)
        st = m.stock
        k = st["split_close"] / st["close"]
        p = partial["sip"][s]["close"]
        dd = st.loc[p.index]
        chk.append({"sym": s, "days": len(p),
                    "close_abs_bp_median": float((p.close / dd.split_close - 1).abs().median() * 1e4),
                    "high_match": float(np.isclose(p.high, dd.high * k.loc[p.index], rtol=2e-4).mean()),
                    "low_match": float(np.isclose(p.low, dd.low * k.loc[p.index], rtol=2e-4).mean())})
    print("minute-vs-daily check:", chk)

    runs = []                     # (label, fill, frames)
    runs.append(("close", "close", None))
    frames = {}
    for v in ("sip", "iex16"):
        for lab in T_MINUTES:
            if lab == "close":
                continue
            frames[(v, lab)] = {s: t_features(market(s), partial[v][s][lab]) for s in SYMS}
    frames[("sip", "close-minute")] = {s: t_features(market(s), partial["sip"][s]["close"]) for s in SYMS}
    for lab in ["15:45", "15:50", "15:55", "15:56"]:
        runs.append((lab, "close", frames[("sip", lab)]))
        runs.append((lab, "tproxy", frames[("sip", lab)]))
    runs.append(("close-minute", "close", frames[("sip", "close-minute")]))
    for lab in ["15:50", "15:55", "15:56"]:
        runs.append((lab + " iex16", "close", frames[("iex16", lab)]))
        runs.append((lab + " iex16", "tproxy", frames[("iex16", lab)]))

    rows, diffs, curves = [], [], {}
    ref = None
    for lab, fill, fr in runs:
        res = run_variant(lab, fr, fill)
        row, b, tr, diff = metrics_row(lab, fill, res, ref)
        if ref is None:
            ref = tr
        rows.append(row)
        curves[f"{lab} / {fill}"] = b
        if diff is not None and len(diff):
            diffs.append(diff)
        print(f"{lab:>16} {fill:6}  IS {row['is_sharpe']:.2f}  OOS {row['oos_sharpe']:.2f}  "
              f"trades {row['full_trades']}  kept {row.get('kept')} missed {row.get('missed')} added {row.get('added')}")

    # signal-day fidelity (entry signal on/off by day) vs the research close signal
    sig = []
    for s in SYMS:
        base = SS.features(market(s)).loc[K.START:K.END]
        on0 = (base.close > base.sma200) & (base.rsi2 < 10) & (base.ibs < 0.2)
        for (v, lab), fr in frames.items():
            g = fr[s].loc[K.START:K.END]
            on = (g.close > g.sma200) & (g.rsi2 < 10) & (g.ibs < 0.2)
            ex0 = base.close > base.sma5
            ex = g.close > g.sma5
            sig.append({"sym": s, "variant": v, "T": lab, "signal_days_close": int(on0.sum()),
                        "signal_days_T": int(on.sum()), "both": int((on & on0).sum()),
                        "T_only": int((on & ~on0).sum()), "close_only": int((~on & on0).sum()),
                        "sma5_flag_disagree": int((ex != ex0).sum())})
    sig = pd.DataFrame(sig)
    res_df = pd.DataFrame(rows)
    res_df.to_csv(HERE / "results.csv", index=False)
    if diffs:
        pd.concat(diffs).drop(columns=["key"], errors="ignore").to_csv(HERE / "trade_diff.csv", index=False)
    sig.to_csv(HERE / "signal_days.csv", index=False)
    (HERE / "summary.json").write_text(json.dumps({
        "params": PARAMS, "is_end": K.IS_END, "oos_start": K.OOS_START, "minute_vs_daily": chk,
        "results": res_df.to_dict(orient="records"),
        "signal_days": sig.groupby(["variant", "T"]).sum(numeric_only=True).reset_index().to_dict(orient="records"),
    }, indent=1, default=float))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5))
        for name in ["close / close", "15:45 / close", "15:50 / close", "15:55 / close", "15:56 / close",
                     "15:56 / tproxy", "15:55 iex16 / tproxy"]:
            if name in curves:
                ax.plot(curves[name].index, curves[name].values, label=name, lw=1.2)
        ax.axvline(pd.Timestamp(K.OOS_START), color="grey", ls="--", lw=0.8)
        ax.set_title("dip_pcs SPY/QQQ/IWM basket by decision time")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(HERE / "equity.png", dpi=110)
    except Exception as e:                       # plotting is optional
        print("plot skipped:", e)
    print(res_df.round(3).to_string())
    print(sig.groupby(["variant", "T"]).sum(numeric_only=True))


if __name__ == "__main__":
    main()
