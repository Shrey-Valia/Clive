"""Intraday mark-to-market path examples for the 10:00 ATM iron fly (1.0 sigma wings):
the median-P&L day and the worst-MAE day of the sample. Output: out/intraday_path.png"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine0 as e  # noqa: E402
from analyze0 import style, OUT, AMBER, GREEN, RED, WHITE, GREY  # noqa: E402

CFG = ("fly", "1.0x", "10:00")


def main():
    t = pd.read_csv(HERE / "trades_t1.csv")
    t = t[(t.ok == True) & (t.cfg == "fly|1.0x|10:00|hold") & (t["mode"] == "vw") & (t.slip == 1.0)]  # noqa: E712
    med = t.iloc[(t.ret - t.ret.median()).abs().argmin()].date
    worst = t.sort_values("mae_ret").iloc[0].date
    plt = style()
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=False)
    for ax, day, lab in zip(axes, [med, worst], ["MEDIAN-P&L DAY", "WORST-MAE DAY"]):
        d = e.Day(day)
        r = e.run_trade(d, *CFG, "15:25", pt=None, slip=1.0, keep_path=True)
        x = pd.to_datetime([f"{day} {s}" for s in r["path_t"]])
        y = r["path"] * 100
        ax.plot(x, y, color=AMBER, lw=1.4, label="fly MTM P&L, $ per 1-lot (mid marks)")
        ax.fill_between(x, 0, y, where=y >= 0, color=GREEN, alpha=0.25)
        ax.fill_between(x, 0, y, where=y < 0, color=RED, alpha=0.25)
        ax.axhline(0, color=WHITE, lw=0.8)
        ax.axhline(-r["max_risk"] * 100, color=RED, lw=0.8, ls="--", label="max loss")
        ax.axhline(r["credit"] * 100, color=GREEN, lw=0.8, ls="--", label="credit")
        ax2 = ax.twinx()
        i0 = d.idx("10:00")
        ax2.plot(d.grid[i0:d.idx("15:25") + 1].tz_localize(None), d.S[i0:d.idx("15:25") + 1], color=GREY, lw=1)
        ax2.set_ylabel("SPY", color=GREY)
        ax2.grid(False)
        ax.set_title(f"{lab} {day} | strikes {r['strikes']} | credit ${r['credit']*100:.0f} | "
                     f"exit P&L ${r['pnl_usd']:.0f} | MAE ${r['mae']*100:.0f}")
        ax.set_ylabel("$ per 1-lot")
        ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "intraday_path.png", dpi=130)
    print("median day", med, "worst day", worst)


if __name__ == "__main__":
    main()
