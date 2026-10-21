"""Markdown tables for REPORT.md (written to tables.md; pasted/edited into the report)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def pct(x, d=1):
    return "" if pd.isna(x) else f"{x * 100:+.{d}f}%"


def main():
    out = []
    e1 = pd.read_csv(HERE / "e1_summary.csv")
    t = e1[e1.period == "ALL"].copy()
    out.append("### E1 per ticker (all 88 reports)\n")
    out.append("| ticker | n | mean IM | mean RM | mean RM/IM | median RM/IM | RM>IM | p90 RM/IM | max RM/IM | mean IM-RM | mean gate (IM/med8 RM) |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in t.iterrows():
        out.append(f"| {r.ticker} | {r.n} | {r.mean_im*100:.1f}% | {r.mean_rm*100:.1f}% | {r.mean_ratio:.2f} | "
                   f"{r.median_ratio:.2f} | {r.pct_rm_gt_im:.0%} | {r.p90_ratio:.2f} | {r.max_ratio:.2f} | "
                   f"{r.mean_im_minus_rm*100:+.1f}pp | {r.mean_gate_ratio:.2f} |")
    p = e1[e1.ticker == "POOLED"]
    out.append("\n| pooled | n | mean IM | mean RM | mean RM/IM | median | RM>IM | p90 |")
    out.append("|---|---|---|---|---|---|---|---|")
    for _, r in p.iterrows():
        out.append(f"| {r.period} | {r.n} | {r.mean_im*100:.1f}% | {r.mean_rm*100:.1f}% | {r.mean_ratio:.2f} | "
                   f"{r.median_ratio:.2f} | {r.pct_rm_gt_im:.0%} | {r.p90_ratio:.2f} |")

    res = pd.read_csv(HERE / "results.csv")
    s = res[(res["mode"] == "strict") & (res.slip_mult == 1.0)]
    out.append("\n### Strategy stats, base costs, strict fills (ret = P&L / max risk per trade)\n")
    out.append("| strategy | filter | subset | n | mean | median | t | boot 95% CI | win | worst | best |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    order = ["ALL", "IS", "OOS"]
    for (strat, gate), g in s[s.subset.isin(order)].groupby(["strategy", "gate"], sort=False):
        if "|" in strat and not strat.endswith(("|BOTH", "|SPY", "|QQQ")):
            continue
        for sub in order:
            r = g[g.subset == sub]
            if not len(r) or r.n.iloc[0] == 0:
                continue
            r = r.iloc[0]
            out.append(f"| {strat} | {gate} | {sub} | {int(r.n)} | {pct(r['mean'])} | {pct(r['median'])} | "
                       f"{r.t:+.2f} | [{pct(r.ci_lo)}, {pct(r.ci_hi)}] | {r.win:.0%} | {pct(r.worst, 0)} | "
                       f"{pct(r.best, 0)} |")
    # cost sensitivity
    c = res[(res["mode"] == "strict") & (res.subset == "ALL") & (res.gate == "all")]
    pv = c.pivot_table(index="strategy", columns="slip_mult", values="mean")
    out.append("\n### Cost sensitivity: mean ret per trade by slippage multiple (ALL, no filter)\n")
    out.append("| strategy | " + " | ".join(f"x{c:g}" for c in pv.columns) + " |")
    out.append("|---|" + "---|" * len(pv.columns))
    for k, r in pv.iterrows():
        out.append(f"| {k} | " + " | ".join(pct(v) for v in r.values) + " |")
    # per ticker
    tk = s[s.subset.str.startswith("ticker:") & (s.gate == "all")].copy()
    tk["tk"] = tk.subset.str[7:]
    for m, f in (("mean", pct), ("win", lambda v: f"{v:.0%}"), ("worst", lambda v: pct(v, 0))):
        pv = tk.pivot(index="strategy", columns="tk", values=m)
        out.append(f"\n#### Per-ticker {m} (base costs, n≈11 each)\n")
        out.append("| strategy | " + " | ".join(pv.columns) + " |")
        out.append("|---|" + "---|" * len(pv.columns))
        for k, r in pv.iterrows():
            out.append(f"| {k} | " + " | ".join(f(v) for v in r.values) + " |")
    # macro eves
    ev = s[s.subset.str.startswith(("eve:", "non_event"))]
    if len(ev):
        out.append("\n### 1DTE on FOMC / CPI eves vs other days (base costs)\n")
        out.append("| strategy | subset | n | mean | t | win | worst |")
        out.append("|---|---|---|---|---|---|---|")
        for _, r in ev.iterrows():
            if r.n == 0:
                continue
            out.append(f"| {r.strategy} | {r.subset} | {int(r.n)} | {pct(r['mean'])} | {r.t:+.2f} | "
                       f"{r.win:.0%} | {pct(r.worst, 0)} |")
    sim = pd.read_csv(HERE / "sizing_sim.csv")
    out.append("\n### Account simulation (fixed fraction of equity as max risk per trade)\n")
    out.append("| stream | capital | risk | trades | skipped (<1 lot) | total | CAGR | Sharpe | maxDD | IS ret | OOS ret |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in sim.iterrows():
        out.append(f"| {r.stream} | ${r.capital:,.0f} | {r.risk_frac:.0%} | {r.taken} | {r.skipped_too_small} | "
                   f"{pct(r.total_return)} | {pct(r.cagr)} | {r.sharpe:.2f} | {pct(r.max_drawdown)} | "
                   f"{pct(r.is_return)} | {pct(r.oos_return)} |")
    (HERE / "tables.md").write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
