"""Shared setup for the portfolio study. Run scripts from anywhere:
    PY "tuning/portfolio/<script>.py"
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPTIONSBOT_MIN_INTERVAL", "1.0")
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

import numpy as np   # noqa: E402
import pandas as pd  # noqa: E402

from optionsbot import metrics as M   # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)

START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"
TD = 252
SHOCKS = {"Aug-2024 (yen carry)": ("2024-07-16", "2024-08-30"),
          "Jan-2025 (DeepSeek)": ("2025-01-17", "2025-02-14"),
          "Apr-2025 (tariffs)": ("2025-03-25", "2025-05-09")}

BG, AMBER, WHITE, GREEN, RED, GREY = "#000000", "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#5a5a5a"
PALETTE = [AMBER, GREEN, "#4fc3f7", "#ff79c6", "#c792ea", "#f1fa8c", RED, "#9e9e9e", "#80cbc4", "#ffcc80"]


def plt_setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
        "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "xtick.color": WHITE, "ytick.color": WHITE,
        "text.color": WHITE, "font.family": "monospace", "font.size": 9, "axes.grid": True,
        "grid.color": "#222222", "grid.linewidth": 0.6, "axes.titlecolor": AMBER,
        "axes.titleweight": "bold", "legend.facecolor": BG, "legend.edgecolor": GREY,
        "lines.linewidth": 1.5})
    return plt


def clean(x, nd=6):
    if isinstance(x, (float, np.floating, int, np.integer)) and not isinstance(x, bool):
        x = float(x)
        return None if (math.isnan(x) or math.isinf(x)) else round(x, nd)
    return x


def rets(eq: pd.Series) -> pd.Series:
    return eq.pct_change().dropna()


def worst_month(eq: pd.Series) -> float:
    return float(M.monthly_returns(eq).min())


def beta(eq: pd.Series, bench: pd.Series) -> float:
    j = pd.concat([eq.pct_change(), bench.pct_change()], axis=1).dropna()
    return float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())


def seg(eq: pd.Series, lo=None, hi=None) -> pd.Series:
    e = eq[lo:hi] if (lo or hi) else eq
    return e


def full_stats(eq: pd.Series, spy: pd.Series, usage: pd.Series | None = None) -> dict:
    m = M.equity_metrics(eq)
    out = {k: m.get(k) for k in ("cagr", "total_return", "ann_vol", "sharpe", "sortino",
                                 "max_drawdown", "calmar")}
    out["worst_month"] = worst_month(eq)
    out["beta_spy"] = beta(eq, spy.reindex(eq.index).ffill())
    if usage is not None:
        u = usage.reindex(eq.index)
        out["avg_capital_usage"] = float(u.mean())
        out["max_capital_usage"] = float(u.max())
    return out


def window(eq, a, b):
    e = eq[a:b]
    if len(e) < 2:
        return {}
    prev = eq[:a]
    base = prev.iloc[-1] if len(prev) else e.iloc[0]
    e2 = pd.concat([pd.Series([base], index=[e.index[0] - pd.Timedelta(days=1)]), e])
    return {"ret": float(e2.iloc[-1] / e2.iloc[0] - 1), "max_dd": float(M.drawdown(e2).min())}


# ------------------------------------------------------------ statistics
def _norm_ppf(p):
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


def _norm_cdf(x):
    from statistics import NormalDist
    return NormalDist().cdf(x)


def psr(sr_d, n, skew, kurt, sr_star=0.0):
    den = math.sqrt(max(1 - skew * sr_d + (kurt - 1) / 4 * sr_d ** 2, 1e-12))
    return _norm_cdf((sr_d - sr_star) * math.sqrt(n - 1) / den)


def deflated_sharpe(r: pd.Series, n_trials: int, var_sr_d: float) -> tuple[float, float]:
    """Bailey & Lopez de Prado DSR on daily returns. var_sr_d = variance of the daily
    Sharpe across trials. Returns (dsr, sr0_annualized)."""
    r = r.dropna()
    sr = r.mean() / r.std()
    g = 0.5772156649
    N = max(n_trials, 2)
    sr0 = math.sqrt(max(var_sr_d, 1e-12)) * ((1 - g) * _norm_ppf(1 - 1 / N) + g * _norm_ppf(1 - 1 / (N * math.e)))
    return psr(sr, len(r), float(r.skew()), float(r.kurt()) + 3, sr0), sr0 * math.sqrt(TD)


def block_bootstrap(r: pd.Series, stat, n_boot=2000, seed=7, block="W-FRI"):
    """Resample whole calendar weeks with replacement; returns array of statistic values."""
    r = r.dropna()
    groups = [g.values for _, g in r.groupby(pd.Grouper(freq=block)) if len(g)]
    rng = np.random.default_rng(seed)
    k = len(groups)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, k, k)
        x = np.concatenate([groups[i] for i in pick])
        vals.append(stat(x))
    return np.array(vals)


def sharpe_arr(x):
    s = x.std(ddof=1)
    return x.mean() / s * math.sqrt(TD) if s > 0 else np.nan


def cagr_arr(x):
    g = np.prod(1 + x)
    return g ** (TD / len(x)) - 1


def maxdd_arr(x):
    e = np.cumprod(1 + x)
    return float((e / np.maximum.accumulate(e) - 1).min())
