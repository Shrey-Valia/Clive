"""Rank stage-A configs on IN-SAMPLE metrics only, with a neighbour-plateau score."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
df = pd.concat([pd.read_csv(f) for f in sorted(OUT.glob("raw_stage_a*.csv"))], ignore_index=True)
df = df[df.get("error").isna()] if "error" in df else df
p = df["params"].apply(json.loads)
df["st"] = p.apply(lambda x: x["structure"])
df["delta"] = p.apply(lambda x: x["put_delta"])
df["w"] = p.apply(lambda x: x["width_pct"])
df["dte"] = p.apply(lambda x: x["dte_target"])
df["mg"] = df.vid.str.split("|").str[-1]

DEL = [0.10, 0.15, 0.20, 0.25, 0.30]
W = [0.005, 0.01, 0.02, 0.03]
D = [30, 45, 60]

g = df.pivot_table(index=["costs", "st", "mg", "delta", "w", "dte"], columns="sym",
                   values=["is_sharpe", "oos_sharpe", "is_cagr", "is_max_drawdown", "full_sharpe", "is_trades"])
g.columns = [f"{a}_{b}" for a, b in g.columns]
g = g.reset_index()
g["is_sh"] = g[["is_sharpe_SPY", "is_sharpe_QQQ"]].mean(axis=1)
g["is_sh_min"] = g[["is_sharpe_SPY", "is_sharpe_QQQ"]].min(axis=1)
g["oos_sh"] = g[["oos_sharpe_SPY", "oos_sharpe_QQQ"]].mean(axis=1)
key = {tuple(r[["costs", "st", "mg", "delta", "w", "dte"]]): r["is_sh"] for _, r in g.iterrows()}


def nb(r):
    vals = [r["is_sh"]]
    i, j, k = DEL.index(r["delta"]), W.index(r["w"]), D.index(r["dte"])
    for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
        a, b, c = i + di, j + dj, k + dk
        if 0 <= a < len(DEL) and 0 <= b < len(W) and 0 <= c < len(D):
            v = key.get((r["costs"], r["st"], r["mg"], DEL[a], W[b], D[c]))
            if v is not None and np.isfinite(v):
                vals.append(v)
    return np.mean(vals), np.min(vals)


g[["plateau", "plateau_min"]] = g.apply(lambda r: pd.Series(nb(r)), axis=1)

pd.set_option("display.width", 250)
lines = []
for c in ("default",):
    for st in ("pcs", "ic"):
        s = g[(g.costs == c) & (g.st == st)].sort_values("plateau", ascending=False)
        lines.append(f"\n### {c} costs, {st}: top 12 by IS plateau (mean IS Sharpe of cell + 1-step neighbours, SPY/QQQ avg)")
        lines.append(s.head(12)[["mg", "delta", "w", "dte", "is_sh", "is_sh_min", "plateau", "plateau_min",
                                 "is_cagr_SPY", "is_cagr_QQQ", "is_max_drawdown_SPY", "is_trades_SPY"]]
                     .round(3).to_string(index=False))
        # marginal means (IS)
        for dim in ("mg", "delta", "w", "dte"):
            m = s.groupby(dim)["is_sh"].mean().round(3)
            lines.append(f"  marginal IS {dim}: " + "; ".join(f"{i}: {a:+.2f}" for i, a in m.items()))
txt = "\n".join(lines)
(OUT / "stage_a_summary.txt").write_text(txt)
g.to_csv(OUT / "stage_a_table.csv", index=False)
print(txt)
