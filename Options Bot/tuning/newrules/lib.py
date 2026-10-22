"""Shared harness for tuning/newrules/ (run from the Options Bot folder).

* 15:55 ET decision frames for SPY/QQQ/IWM come from tuning/live_timing (its
  cached SIP minute bars; nothing is re-downloaded).
* Engine runs are cached on disk (.cache/runs) keyed by class + params.
* Basket = equal-weight daily-rebalanced SPY/QQQ/IWM $10k sleeves (same as the
  signals study), IS 2024-03-01..2025-06-30, OOS 2025-07-01..2026-10-20.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import sys
from pathlib import Path

os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "1.0")     # <= 60 req/min, one worker
os.environ.setdefault("no_proxy", "*")

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT, ROOT / "tuning" / "signals", ROOT / "tuning" / "live_timing"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import common as K                                           # noqa: E402  (tuning/signals)
from optionsbot import metrics as M                          # noqa: E402
from optionsbot import strategies_newrules as NR             # noqa: E402
from optionsbot import strategies_signals as SS              # noqa: E402
from optionsbot.runner import market, run_one                # noqa: E402

CACHE = HERE / ".cache"
RUNS = CACHE / "runs"
RUNS.mkdir(parents=True, exist_ok=True)
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"
ETFS = ["SPY", "QQQ", "IWM"]
CAP10 = 10_000

FINALIST = dict(signal="dip", rsi_lo=10, ibs_lo=0.2, trend="sma200", structure="pcs", short_delta=0.25,
                long_delta=0.1, dte_min=7, dte_max=14, dte_target=10, tp=0.5, max_days=5, exit_sma5=True)
CONDOR = dict(structure="ic", put_delta=0.1, call_delta=0.1, wing="pct", width_pct=0.03, dte_min=50,
              dte_max=70, dte_target=60, tp=None, sl_mult=None, breach=False, close_dte=None,
              risk_frac=0.2, max_positions=1)

_frames_ready = False


def frames_1555():
    """Install the 15:55 SIP decision frames (tuning/live_timing method) into NR.FRAMES."""
    global _frames_ready
    if _frames_ready:
        return
    f = CACHE / "frames_1555.pkl"
    if f.exists():
        fr = pickle.loads(f.read_bytes())
    else:
        import run_timing as RT
        fr = {}
        for s in ETFS:
            days = SS.features(market(s)).loc[START:END].index
            part = RT.partial_bars(s, days, "sip")["15:55"]
            fr[s] = RT.t_features(market(s), part)
        f.write_bytes(pickle.dumps(fr))
    NR.FRAMES.update(fr)
    _frames_ready = True


def core_hash() -> str:
    """Hash of the core engine files: the run cache is invalidated when another agent edits them."""
    h = hashlib.sha1()
    for f in ("engine.py", "strategies.py", "strategies_signals.py", "strategies_spreads.py", "pricing.py",
              "strategies_newrules.py", "data.py", "metrics.py"):
        h.update((ROOT / "optionsbot" / f).read_bytes())
    return h.hexdigest()[:12]


CORE = core_hash()


def _key(cls, params, sym, capital, extra=""):
    s = json.dumps([cls.__name__, params, sym, capital, START, END, extra, CORE], sort_keys=True, default=str)
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def run_cached(cls, params, sym, capital=CAP10, extra="", costs=None, lag=0):
    """`extra` must change whenever external state (news flags) changes."""
    f = RUNS / f"{_key(cls, params, sym, capital, extra + str(costs) + f'|lag{lag}')}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    kw = {"signal_lag": lag} if lag else {}
    r = run_one(cls, params, sym, START, END, capital, costs=costs, **kw)
    st = r["engine"].strategy.__dict__
    out = {"equity": r["equity"][["equity", "spot", "n_positions", "reserved"]], "trades": r["trades"],
           "notes": r["notes"], "infeasible": r["infeasible"],
           "n_open_print": st.get("_n_open_print", 0), "n_open_model": st.get("_n_open_model", 0)}
    f.write_bytes(pickle.dumps(out))
    return out


def basket(eqs: dict, capital=CAP10) -> pd.Series:
    rets = pd.DataFrame({k: v.pct_change() for k, v in eqs.items()}).dropna(how="all").fillna(0.0)
    eq = (1 + rets.mean(axis=1)).cumprod() * capital
    first = next(iter(eqs.values())).index[0]
    return pd.concat([pd.Series([capital], index=[first]), eq])


def em(eq, a=None, b=None):
    e = eq[a:b] if (a or b) else eq
    return M.equity_metrics(e)


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr is None or tr.empty:
        return {"trades": 0, "win_rate": float("nan"), "profit_factor": float("nan"), "pnl": 0.0,
                "ret_mean": float("nan"), "worst": float("nan")}
    t = K.trade_returns(tr)
    win = t[t.pnl > 0].pnl.sum()
    loss = -t[t.pnl < 0].pnl.sum()
    return {"trades": int(len(t)), "win_rate": float((t.pnl > 0).mean()) if len(t) else float("nan"),
            "profit_factor": float(win / loss) if loss > 0 else None, "pnl": float(t.pnl.sum()),
            "ret_mean": float(t.ret.mean()) if len(t) else float("nan"),
            "worst": float(t.ret.min()) if len(t) else float("nan")}


def evaluate(rule, variant, cls, params, syms=ETFS, capital=CAP10, extra="", costs=None, counted=True,
             note="", lag=0):
    """Run on syms, return (row, basket_equity, trades)."""
    res = {s: run_cached(cls, params, s, capital, extra, costs, lag) for s in syms}
    eqs = {s: r["equity"]["equity"] for s, r in res.items()}
    b = basket(eqs, capital) if len(syms) > 1 else eqs[syms[0]]
    tr = pd.concat([r["trades"] for r in res.values() if len(r["trades"])], ignore_index=True) \
        if any(len(r["trades"]) for r in res.values()) else pd.DataFrame()
    row = {"rule": rule, "variant": variant, "strategy": cls.id, "underlyings": ",".join(syms),
           "capital": capital, "counted_trial": counted, "note": note, "lag": lag, "core": CORE,
           "params": json.dumps({k: v for k, v in params.items()}, sort_keys=True, default=str)}
    if len(tr):
        tr["open_date"] = pd.to_datetime(tr["open_date"])
    for tag, (a, z) in {"full": (None, None), "is": (None, IS_END), "oos": (OOS_START, None)}.items():
        m = em(b, a, z)
        row.update({f"{tag}_cagr": m.get("cagr"), f"{tag}_sharpe": m.get("sharpe"),
                    f"{tag}_sortino": m.get("sortino"), f"{tag}_maxdd": m.get("max_drawdown"),
                    f"{tag}_ret": m.get("total_return")})
        t = tr if tag == "full" or not len(tr) else (tr[tr.open_date <= IS_END] if tag == "is"
                                                      else tr[tr.open_date >= OOS_START])
        ts = trade_stats(t)
        row.update({f"{tag}_trades": ts["trades"], f"{tag}_win": ts["win_rate"], f"{tag}_pf": ts["profit_factor"],
                    f"{tag}_pnl": ts["pnl"], f"{tag}_tr_mean": ts["ret_mean"], f"{tag}_worst_tr": ts["worst"]})
    row["n_open_print"] = sum(r.get("n_open_print", 0) for r in res.values())
    row["n_open_model"] = sum(r.get("n_open_model", 0) for r in res.values())
    return row, b, tr


# ------------------------------------------------------------------ deflated Sharpe
def dsr(daily_ret: pd.Series, sr_trials_daily: np.ndarray, n_trials: int) -> dict:
    """Bailey & Lopez de Prado (2014) deflated Sharpe on daily returns.
    sr_trials_daily: daily (non-annualized) Sharpe of every trial in the same window."""
    r = daily_ret.dropna()
    T = len(r)
    sr = r.mean() / r.std(ddof=1) if r.std(ddof=1) > 0 else 0.0
    g3 = float(stats.skew(r))
    g4 = float(stats.kurtosis(r, fisher=False))
    v = float(np.var(sr_trials_daily, ddof=1)) if len(sr_trials_daily) > 1 else 0.0
    emc = 0.5772156649
    N = max(int(n_trials), 2)
    sr0 = math.sqrt(v) * ((1 - emc) * stats.norm.ppf(1 - 1 / N) + emc * stats.norm.ppf(1 - 1 / (N * math.e)))
    den = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2, 1e-12))
    z = (sr - sr0) * math.sqrt(T - 1) / den
    psr0 = (sr - 0) * math.sqrt(T - 1) / den
    return {"sr_daily": sr, "sr_ann": sr * math.sqrt(252), "sr0_ann": sr0 * math.sqrt(252), "skew": g3,
            "kurt": g4, "T": T, "N": N, "dsr": float(stats.norm.cdf(z)), "psr_vs0": float(stats.norm.cdf(psr0))}


def daily_sr(eq: pd.Series) -> float:
    r = eq.pct_change().dropna()
    return float(r.mean() / r.std(ddof=1)) if r.std(ddof=1) > 0 else 0.0
