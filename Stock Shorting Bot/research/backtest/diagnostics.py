"""Post-selection diagnostics for the report (not used for selection).

1. Return decomposition of the baseline hold: overnight leg vs next-day intraday leg.
2. A handful of simple, pre-specified proposal candidates, IS vs OOS, with cost stress.
3. Survivorship: share/returns of trades in names that are inactive today.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402
from analyze import IS_END, OOS_START, psr  # noqa: E402
from common import OUT, RESULTS  # noqa: E402
from engine import Config, eligible, run, stats  # noqa: E402
from plotstyle import AMBER, GREEN, GREY, RED, WHITE, plt  # noqa: E402


def main():
    ev, days = analyze.load()
    base = Config()
    # ---- 1. legs, on the baseline picks and on all eligible top-20 names
    d, t = run(base, ev, days)
    rows = []
    e_all = ev[eligible(ev, base) & ev.x1555.notna() & ev.o1.notna() & ev.c1.notna()]
    for label, x in (("baseline_picks", t), ("all_eligible_top20", e_all)):
        for per, s in (("IS", x[x.date <= IS_END]), ("OOS", x[x.date >= OOS_START])):
            legs = {"overnight_1555_to_open": 1 - s.o1 / s.p1555,
                    "intraday_open_to_1555": 1 - s.x1555 / s.o1,
                    "intraday_open_to_close": 1 - s.c1 / s.o1,
                    "full_1555_to_1555": 1 - s.x1555 / s.p1555}
            for k, v in legs.items():
                v = v.dropna()
                rows.append({"sample": label, "period": per, "leg": k, "n": len(v), "mean": v.mean(),
                             "median": v.median(), "win": (v > 0).mean(), "p_lt_-25": (v < -0.25).mean(),
                             "q01": v.quantile(0.01)})
    legs = pd.DataFrame(rows)
    legs.to_csv(os.path.join(RESULTS, "leg_decomposition.csv"), index=False)
    print(legs.round(4).to_string())

    # ---- 2. simple proposal candidates (few knobs, all motivated by IS results)
    intraday = dict(entry="open1", exit="close1")
    cands = {
        "baseline": base,
        "P0_baseline_hold_small_size": replace(base, sizing="equal", gross=0.5),
        "P1_intraday_top5_eq50": replace(base, **intraday, sizing="equal", gross=0.5),
        "P2_intraday_top5_eq50_stop40": replace(base, **intraday, sizing="equal", gross=0.5, stop=0.40),
        "P3_intraday_top10_atr50_minchg20": replace(base, **intraday, n=10, sizing="atr", gross=0.5,
                                                     max_name=0.075, min_chg=0.20),
        "P4_intraday_top10_atr50_minchg20_stop40": replace(base, **intraday, n=10, sizing="atr", gross=0.5,
                                                            max_name=0.075, min_chg=0.20, stop=0.40),
        "P5_gapup_fade_top5_eq50": replace(base, **intraday, sizing="equal", gross=0.5, filters=(("gap1", ">", 0.0),)),
        "P7_intraday_top5_eq50_min70_strict": replace(base, **intraday, sizing="equal", gross=0.5, min_score=70,
                                                       borrow_gate_price=10, borrow_gate_adv=10e6),
    }
    out, curves = [], {}
    for nm, c in cands.items():
        for stress, cc in (("base", c), ("cost_x2", replace(c, cost_mult=2.0)),
                           ("gate_none", replace(c, borrow_gate_price=0, borrow_gate_adv=0)),
                           ("gate_strict_p10_adv10m", replace(c, borrow_gate_price=10, borrow_gate_adv=10e6))):
            dd, tt = run(replace(cc, name=nm), ev, days)
            (di, ti), (do, to) = analyze.split(dd, tt)
            si, so = stats(di, ti), stats(do, to)
            out.append({"config": nm, "stress": stress,
                        **{f"is_{k}": si.get(k) for k in ("sharpe", "cagr", "maxdd", "worst_day", "trades", "mean_trade", "win", "q01")},
                        **{f"oos_{k}": so.get(k) for k in ("sharpe", "cagr", "maxdd", "worst_day", "trades", "mean_trade", "win", "q01")},
                        "oos_psr": psr(do.ret.values)})
            if stress == "base":
                curves[nm] = dd.set_index("date").ret
                if nm.startswith("P"):
                    tt.to_csv(os.path.join(RESULTS, f"trades_{nm}.csv"), index=False)
    pc = pd.DataFrame(out)
    pc.to_csv(os.path.join(RESULTS, "proposal_candidates.csv"), index=False)
    print(pc.round(3).to_string())
    yr = pd.DataFrame({k: v.groupby(v.index.str[:4]).apply(lambda r: np.prod(1 + r) - 1) for k, v in curves.items()})
    yr.to_csv(os.path.join(RESULTS, "proposal_by_year.csv"))
    print(yr.round(3).to_string())

    fig, ax = plt.subplots(figsize=(12, 5.5))
    cols = {"baseline": RED, "P0_baseline_hold_small_size": GREY, "P1_intraday_top5_eq50": AMBER,
            "P2_intraday_top5_eq50_stop40": GREEN, "P3_intraday_top10_atr50_minchg20": WHITE,
            "P4_intraday_top10_atr50_minchg20_stop40": "#6fa8ff", "P5_gapup_fade_top5_eq50": "#c792ea",
            "P7_intraday_top5_eq50_min70_strict": "#ffe08a"}
    for nm, r in curves.items():
        eq = np.cumprod(np.maximum(1 + r.values, 0))
        ax.plot(pd.to_datetime(r.index), np.maximum(eq, 1e-3), color=cols[nm], lw=2.2 if nm.startswith("P7") else 1.1, label=nm)
    ax.axvline(pd.Timestamp(OOS_START), color=GREY, ls=":")
    ax.set_yscale("log"); ax.set_title("PROPOSAL CANDIDATES vs BASELINE (net, log; floor 0.001 = ruin)")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "proposal_equity.png"), dpi=130); plt.close(fig)

    # leg bars
    lg = legs[legs["sample"] == "all_eligible_top20"]
    fig, ax = plt.subplots(figsize=(10, 4))
    names = ["overnight_1555_to_open", "intraday_open_to_1555", "full_1555_to_1555"]
    x = np.arange(len(names))
    for i, (per, col) in enumerate((("IS", AMBER), ("OOS", WHITE))):
        v = [lg[(lg.period == per) & (lg.leg == n)]["mean"].iloc[0] * 100 for n in names]
        ax.bar(x + (i - 0.5) * 0.38, v, 0.38, color=col, label=per)
    ax.set_xticks(x, ["overnight\n15:55 -> open", "next day\nopen -> 15:55", "bot's hold\n15:55 -> 15:55"])
    ax.axhline(0, color=GREY); ax.set_ylabel("mean gross SHORT return %"); ax.legend()
    ax.set_title("WHERE THE EDGE IS: short-side return by leg (all eligible top-20)")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "leg_decomposition.png"), dpi=130); plt.close(fig)

    # ---- 3. survivorship
    t = t.assign(inactive=t.status == "inactive")
    sv = t.groupby("inactive").agg(n=("net_ret", "size"), mean_net=("net_ret", "mean"), mean_gross=("gross_ret", "mean"))
    sv.to_csv(os.path.join(RESULTS, "survivorship.csv"))
    print(sv)


if __name__ == "__main__":
    main()
