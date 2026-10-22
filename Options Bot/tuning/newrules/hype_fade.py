#!/usr/bin/env python3
"""Rule 5: hype fade with a bear call credit spread on liquid single names.

    python3 tuning/newrules/hype_fade.py fetch      # option contracts + bars (GET only, ~1 req/s, cached)
    python3 tuning/newrules/hype_fade.py sim        # simulate all variants -> .cache/hype_results.pkl

Events: .cache/hype_events.parquet (hype_events.py). Spike day t = a +5% move at the 15:45 scan
(price > $10, 20-day dollar volume >= $50M). Headlines are those created before the scan.

Trade (per event):
  * entry "open": the next session's open (t+1), legs priced at the option bar's OPEN print
    (literature: never fill at the close of the news day). entry "close": the spike day's close.
  * expiry 14-30 DTE (monthly preferred, else nearest to 21 DTE); short call closest to the
    target delta (0.20 / 0.25 / 0.30, IV inverted from the entry print vs the underlying print
    at the same time), long call closest to 0.10 delta above it. Both legs must print at entry.
  * guards: credit > $0.05, credit <= 60% of width (tuning/signals/BUGS.md #7).
  * exit: at a close when the spread can be bought back for <= 50% of the credit, else at the
    close of session t+5. Marks: option close print, else Black-Scholes at the leg's last IV.
  * costs: engine default (slip max($0.03, 2%) per leg, cap $0.25, $0.03/contract) and double.
Portfolio: $100k, each trade sized to max loss <= 2% of equity, total open max loss <= 50%.
Controls on the same names:
  * "nonfade":   +5% spikes on the same universe whose headlines are a real catalyst / earnings /
                 dilution or other specific news (a random 500 of 1,319; fetched after the fade set).
  * "random":    the same names on random non-spike dates (one per fade event, fixed seeds).
"""
from __future__ import annotations

import glob
import json
import math
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from optionsbot import pricing as px                       # noqa: E402
from optionsbot.data import AlpacaData                       # noqa: E402

SC = HERE.parents[2] / "Stock Shorting Bot" / "research" / ".cache"
C = HERE / ".cache"
OPT = C / "hype_opt"
OPT.mkdir(parents=True, exist_ok=True)
TODAY = pd.Timestamp("2026-10-21")
CAL = pd.to_datetime(pd.read_csv(SC / "calendar.csv")["date"])
CAL = CAL[(CAL >= "2024-01-01") & (CAL <= "2026-10-20")].reset_index(drop=True)
CAL_IDX = {d: i for i, d in enumerate(CAL)}
IS_END, OOS_START = pd.Timestamp("2025-06-30"), pd.Timestamp("2025-07-01")


def daily_bars(symbols: set) -> dict:
    f = C / "hype_daily.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    out = {}
    for p in sorted(glob.glob(str(SC / "daily_raw" / "*.parquet"))):
        d = pd.read_parquet(p, columns=["symbol", "date", "o", "h", "l", "c", "v"])
        d = d[d.symbol.isin(symbols) & (d.date >= "2024-01-01")]
        for s, g in d.groupby("symbol"):
            g = g.set_index(pd.to_datetime(g.date))[["o", "h", "l", "c", "v"]].sort_index()
            out[s] = pd.concat([out[s], g]) if s in out else g
    f.write_bytes(pickle.dumps(out))
    return out


def random_events(ev: pd.DataFrame, bars: dict, n_per=1, seed=11) -> pd.DataFrame:
    """Same names, random dates at least 5 sessions away from any spike of that name."""
    rng = np.random.default_rng(seed)
    allspk = ev.groupby("symbol").date.apply(lambda x: set(pd.to_datetime(x)))
    rows = []
    fade = ev[ev.label.isin(["hype", "stale"])]
    for _, r in fade.iterrows():
        b = bars.get(r.symbol)
        if b is None:
            continue
        days = b.loc["2024-03-01":"2026-10-12"].index
        spk = allspk.get(r.symbol, set())
        bad = set()
        for s in spk:
            i = CAL_IDX.get(s)
            if i is not None:
                bad.update(CAL[max(0, i - 5):i + 6])
        ok = [d for d in days if d not in bad and d in CAL_IDX]
        for _ in range(n_per):
            if ok:
                rows.append({"symbol": r.symbol, "date": ok[int(rng.integers(0, len(ok)))].strftime("%Y-%m-%d"),
                             "label": "random", "chg1545": np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ data fetch
class Fetch:
    def __init__(self):
        self.c = AlpacaData()
        self.c.min_interval = 1.0

    def contracts(self, sym, d0: pd.Timestamp, S: float) -> list[dict]:
        lo, hi = d0 + pd.Timedelta(days=13), d0 + pd.Timedelta(days=32)
        f = OPT / f"k_{sym}_{d0:%Y%m%d}.json"
        if f.exists():
            return json.loads(f.read_text())
        rows = []
        for status in (["inactive", "active"] if hi >= TODAY - pd.Timedelta(days=3) else ["inactive"]):
            params = {"underlying_symbols": sym, "type": "call", "status": status, "limit": 10000,
                      "expiration_date_gte": lo.strftime("%Y-%m-%d"), "expiration_date_lte": hi.strftime("%Y-%m-%d"),
                      "strike_price_gte": f"{S * 0.98:.2f}", "strike_price_lte": f"{S * 1.8:.2f}"}
            while True:
                js = self.c._get("https://paper-api.alpaca.markets/v2/options/contracts", params)
                for k in js.get("option_contracts", []) or []:
                    if k.get("root_symbol") == sym and float(k.get("size") or 100) == 100:
                        rows.append({"symbol": k["symbol"], "exp": k["expiration_date"], "K": float(k["strike_price"])})
                tok = js.get("next_page_token")
                if not tok:
                    break
                params["page_token"] = tok
        f.write_text(json.dumps(rows))
        return rows

    def bars(self, sym, d0, symbols: list[str], a: str, b: str) -> dict:
        f = OPT / f"b_{sym}_{d0:%Y%m%d}.json"
        if f.exists():
            return json.loads(f.read_text())
        got = {s: [] for s in symbols}
        for i in range(0, len(symbols), 100):
            params = {"symbols": ",".join(symbols[i:i + 100]), "timeframe": "1Day", "start": a, "end": b,
                      "limit": 10000}
            while True:
                js = self.c._get("https://data.alpaca.markets/v1beta1/options/bars", params)
                for s, bb in (js.get("bars") or {}).items():
                    got.setdefault(s, []).extend({"t": x["t"][:10], "o": x["o"], "c": x["c"], "v": x.get("v", 0)}
                                                 for x in bb)
                tok = js.get("next_page_token")
                if not tok:
                    break
                params["page_token"] = tok
        f.write_text(json.dumps(got))
        return got


def is_monthly(e):
    return e.weekday() == 4 and 15 <= e.day <= 21


def pick_chain(rows, d0, S):
    """Expiries 14-30 DTE from d0, best first (monthly, then nearest to 21 DTE); <= 2 expiries,
    strikes from spot to +60%, at most 40 per expiry (nearest to spot)."""
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    df["exp"] = pd.to_datetime(df["exp"])
    df["dte"] = (df["exp"] - d0).dt.days
    df = df[(df.dte >= 14) & (df.dte <= 30) & (df.K >= S * 1.0)]
    if df.empty:
        return {}
    exps = sorted(df.exp.unique(), key=lambda e: (0 if is_monthly(pd.Timestamp(e)) else 1, abs((pd.Timestamp(e) - d0).days - 21)))[:2]
    out = {}
    for e in exps:
        g = df[(df.exp == e) & (df.K <= S * 1.6)].sort_values("K").drop_duplicates("K")
        if len(g) > 40:   # dense near spot, sparse far out: x_i = 0.6 (i/39)^1.7
            want = S * (1 + 0.6 * (np.arange(40) / 39) ** 1.7)
            idx = sorted({int(np.abs(g.K.values - w).argmin()) for w in want})
            g = g.iloc[idx]
        out[pd.Timestamp(e)] = list(zip(g.symbol, g.K))
    return out


def fetch_all():
    ev = pd.read_parquet(C / "hype_events.parquet")
    bars = daily_bars(set(ev.symbol))
    rnd = random_events(ev, bars)
    rnd.to_parquet(C / "hype_random.parquet")
    jobs = pd.concat([ev[["symbol", "date", "label"]], rnd[["symbol", "date", "label"]]], ignore_index=True)
    # fade set and random controls first, then the rest of the spikes
    jobs["pri"] = jobs.label.map({"hype": 0, "stale": 0, "random": 1}).fillna(2)
    jobs["rk"] = np.random.default_rng(5).random(len(jobs))     # random order inside each priority
    jobs = jobs.sort_values(["pri", "rk"])
    jobs = pd.concat([jobs[jobs.pri < 2], jobs[jobs.pri == 2].head(int(os.environ.get("HYPE_MAX_OTHER", "500")))])
    F = Fetch()
    n = 0
    for _, r in jobs.iterrows():
        t = pd.Timestamp(r.date)
        b = bars.get(r.symbol)
        if b is None or t not in b.index or t not in CAL_IDX:
            continue
        S = float(b.at[t, "c"])
        try:
            rows = F.contracts(r.symbol, t, S)
            ch = pick_chain(rows, t, S)
            syms = [s for v in ch.values() for s, _ in v]
            if not syms:
                continue
            i = CAL_IDX[t]
            a = CAL[max(0, i - 1)].strftime("%Y-%m-%d")
            z = CAL[min(len(CAL) - 1, i + 7)].strftime("%Y-%m-%d")
            F.bars(r.symbol, t, syms, a, z)
        except Exception as e:                         # keep going; a failed event is just skipped
            print("fail", r.symbol, r.date, str(e)[:120], flush=True)
        n += 1
        if n % 50 == 0:
            print(f"{n}/{len(jobs)} events, {F.c.n_requests} requests", flush=True)
    print("done", n, F.c.n_requests)


# ------------------------------------------------------------------ simulation
def costs(mult):
    return dict(slip_abs=0.03 * mult, slip_pct=0.02 * mult, slip_cap=0.25 * mult, fee=0.03)


def slip(price, c):
    return min(c["slip_cap"], max(c["slip_abs"], c["slip_pct"] * price))


def iv_delta(price, S, K, T, r):
    iv = px.implied_vol(price, S, K, T, r, 0.0, "C")
    if iv is None or not (0.05 <= iv <= 4.0):
        return None, None
    return iv, px.bs_delta(S, K, T, r, 0.0, iv, "C")


def simulate_event(sym, t, bars_u, ch, ob, entry, short_delta, long_delta=0.10):
    """Returns dict(trade) or None (no fill)."""
    i = CAL_IDX[t]
    if i + 5 >= len(CAL):
        return None
    if entry == "open":
        de = CAL[i + 1]
        if de not in bars_u.index:
            return None
        S0 = float(bars_u.at[de, "o"])
        fld = "o"
        T_extra = 6.5 / (24 * 365)
    else:
        de = t
        S0 = float(bars_u.at[t, "c"])
        fld = "c"
        T_extra = 0.0
    r = px.risk_free(de)
    for exp, legs in ch.items():
        T = (exp - de).days / 365.0 + T_extra
        if T <= 0:
            continue
        cand = []
        for s, K in legs:
            bb = ob.get(s, {})
            p = bb.get(de.strftime("%Y-%m-%d"))
            if p is None or p.get(fld) is None or p[fld] <= 0:
                continue
            iv, dl = iv_delta(float(p[fld]), S0, K, T, r)
            if iv is None:
                continue
            cand.append((s, K, float(p[fld]), iv, dl))
        if not cand:
            continue
        sh = min(cand, key=lambda x: abs(x[4] - short_delta))
        if abs(sh[4] - short_delta) > 0.08:
            continue
        lo = [x for x in cand if x[1] > sh[1]]
        if not lo:
            continue
        lg = min(lo, key=lambda x: abs(x[4] - long_delta))
        width = lg[1] - sh[1]
        credit = sh[2] - lg[2]
        if credit <= 0.05 or credit > 0.60 * width:
            continue
        # path of closes from entry day (if close entry) / t+1 .. t+5
        path = []
        ivs = {sh[0]: sh[3], lg[0]: lg[3]}
        for k in range(1 if entry == "close" else 1, 6):
            dk = CAL[i + k]
            if dk not in bars_u.index:
                continue
            Sk = float(bars_u.at[dk, "c"])
            Tk = max((exp - dk).days, 0) / 365.0
            m = {}
            stale = False
            for s, K in ((sh[0], sh[1]), (lg[0], lg[1])):
                p = ob.get(s, {}).get(dk.strftime("%Y-%m-%d"))
                intr = max(0.0, Sk - K)
                if p is not None and p.get("c") and p["c"] > 0:
                    m[s] = max(float(p["c"]), intr)
                    iv, _ = iv_delta(m[s], Sk, K, Tk, px.risk_free(dk)) if Tk > 0 else (None, None)
                    if iv:
                        ivs[s] = iv
                else:
                    stale = True
                    m[s] = max(px.bs_price(Sk, K, Tk, px.risk_free(dk), 0.0, ivs[s], "C"), intr) if Tk > 0 else intr
            path.append((dk, m[sh[0]], m[lg[0]], stale, Sk))
        if len(path) < 1:
            return None
        return {"symbol": sym, "t": t, "entry_date": de, "exp": exp, "short": sh[0], "long": lg[0],
                "Ks": sh[1], "Kl": lg[1], "width": width, "ps0": sh[2], "pl0": lg[2], "credit": credit,
                "short_delta": sh[4], "S0": S0, "path": path}
    return None


def trade_pnl(tr, c, tp=0.5):
    """Per-contract P&L path ($) with costs; exit at TP or last path day."""
    fs = tr["ps0"] - slip(tr["ps0"], c)
    fl = tr["pl0"] + slip(tr["pl0"], c)
    cash0 = (fs - fl) * 100 - 2 * c["fee"]
    maxloss = tr["width"] * 100 - cash0
    marks = []
    exit_k, reason = len(tr["path"]) - 1, "time"
    for k, (dk, ms, ml, stale, Sk) in enumerate(tr["path"]):
        cost_mid = ms - ml
        marks.append((dk, cash0 - cost_mid * 100))
        if cost_mid <= tr["credit"] * (1 - tp):
            exit_k, reason = k, "tp"
            break
    dk, ms, ml, stale, Sk = tr["path"][exit_k]
    close_cost = (ms + slip(ms, c)) - max(ml - slip(ml, c), 0.0)
    pnl = cash0 - close_cost * 100 - 2 * c["fee"]
    marks = marks[:exit_k] + [(dk, pnl)]
    return {"pnl": pnl, "maxloss": maxloss, "ret": pnl / maxloss if maxloss > 0 else np.nan,
            "exit_date": dk, "reason": reason, "marks": marks}


def portfolio(trades: list[dict], c, capital=100_000.0, risk=0.02, cap=0.50):
    """Daily equity of a book that sizes each trade to max loss <= risk * equity."""
    days = CAL[(CAL >= "2024-03-01") & (CAL <= "2026-10-20")]
    by_entry = {}
    for tr in trades:
        by_entry.setdefault(tr["entry_date"], []).append(tr)
    cash, open_ = capital, []
    eq, rows = [], []
    for d in days:
        # realize exits
        still = []
        for o in open_:
            if o["exit_date"] == d:
                cash += o["n"] * o["pnl"]
                rows.append({**{k: o[k] for k in ("symbol", "t", "entry_date", "exit_date", "reason", "n")},
                             "pnl": o["n"] * o["pnl"], "risk": o["n"] * o["maxloss"], "ret": o["ret"]})
            else:
                still.append(o)
        open_ = still
        mtm = sum(o["n"] * dict(o["marks"]).get(d, o.get("last", 0.0)) for o in open_)
        for o in open_:
            if d in dict(o["marks"]):
                o["last"] = dict(o["marks"])[d]
        E = cash + mtm
        for tr in sorted(by_entry.get(d, []), key=lambda x: x["symbol"]):
            p = trade_pnl(tr, c)
            if not (p["maxloss"] > 0):
                continue
            used = sum(o["n"] * o["maxloss"] for o in open_)
            n = int(min(risk * E, cap * E - used) // p["maxloss"])
            if n <= 0:
                continue
            o = {**tr, **p, "n": n, "last": dict(p["marks"]).get(d, 0.0)}
            if o["exit_date"] == d:
                cash += n * p["pnl"]
                rows.append({**{k: o[k] for k in ("symbol", "t", "entry_date", "exit_date", "reason", "n")},
                             "pnl": n * p["pnl"], "risk": n * p["maxloss"], "ret": p["ret"]})
            else:
                open_.append(o)
                # entry-day mark: credit received minus mid cost (close entry) ~ -spread costs
        mtm = sum(o["n"] * o.get("last", 0.0) for o in open_)
        eq.append((d, cash + mtm))
    return pd.Series(dict(eq)), pd.DataFrame(rows)


def sim():
    ev = pd.read_parquet(C / "hype_events.parquet")
    rnd = pd.read_parquet(C / "hype_random.parquet")
    bars = daily_bars(set(ev.symbol))
    allev = pd.concat([ev[["symbol", "date", "label", "chg1545"]], rnd], ignore_index=True)
    base = {}
    stats = {"events": 0, "no_data": 0}
    for entry in ("open", "close"):
        for dl in (0.20, 0.25, 0.30):
            lst = []
            for _, r in allev.iterrows():
                t = pd.Timestamp(r.date)
                fk = OPT / f"k_{r.symbol}_{t:%Y%m%d}.json"
                fb = OPT / f"b_{r.symbol}_{t:%Y%m%d}.json"
                if not (fk.exists() and fb.exists()) or r.symbol not in bars or t not in CAL_IDX:
                    continue
                S = float(bars[r.symbol].at[t, "c"]) if t in bars[r.symbol].index else None
                if S is None:
                    continue
                ch = pick_chain(json.loads(fk.read_text()), t, S)
                raw = json.loads(fb.read_text())
                ob = {s: {x["t"]: x for x in v} for s, v in raw.items()}
                tr = simulate_event(r.symbol, t, bars[r.symbol], ch, ob, entry, dl)
                if tr is not None:
                    tr["label"] = r.label
                    lst.append(tr)
            base[(entry, dl)] = lst
            print(entry, dl, len(lst), flush=True)
    (C / "hype_trades.pkl").write_bytes(pickle.dumps(base))


if __name__ == "__main__":
    {"fetch": fetch_all, "sim": sim}[sys.argv[1]]()
