"""Stage 0: why does the baseline PCS lose to costs? Decompose P&L by exit reason,
slippage as a share of credit, stop-loss overshoot and stale marks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa

from optionsbot.runner import run_one

BASE = dict(structure="pcs", put_delta=0.25, wing="delta", long_delta=0.10, dte_min=30, dte_max=45,
            dte_target=38, tp=0.5, sl_mult=2.0, close_dte=21, risk_frac=0.25)
IC = dict(structure="ic", put_delta=0.16, call_delta=0.16, wing="delta", long_delta=0.05, dte_min=38,
          dte_max=52, dte_target=45, tp=0.5, sl_mult=None, close_dte=21, risk_frac=0.25)

out = {}
lines = []
for name, params in (("pcs_default", BASE), ("ic_default", IC)):
    for sym in ("SPY", "QQQ"):
        r = run_one(SpreadBook, params, sym, START, END, 100_000, costs=COSTS["default"])
        eng = r["engine"]
        rows = []
        for p in eng.closed:
            credit = -p.entry_mark_value
            ent_slip = sum(abs(l.qty) * 100 * abs(l.entry_price - l.entry_mark) for l in p.legs)
            ex_slip = 0.0
            if p.reason not in ("expired", "expired_itm"):
                ex_slip = sum(abs(l.qty) * 100 * eng.costs.slip(l.mark) for l in p.legs)
            pnl = p.entry_cash + p.exit_cash
            width = max(abs(a.strike - b.strike) for a in p.legs for b in p.legs if a.cp == b.cp)
            n = max(abs(l.qty) for l in p.legs)
            S0, S1 = eng.m.spot(p.open_date), eng.m.spot(p.close_date)
            rows.append(dict(open=p.open_date.date(), close=p.close_date.date(), reason=p.reason, n=n,
                             credit=credit, width=width * 100 * n, slip=ent_slip + ex_slip,
                             pnl=pnl, gross=pnl + ent_slip + ex_slip, stale=p.stale_days,
                             mv=S1 / S0 - 1, eq0=None))
        t = pd.DataFrame(rows)
        key = f"{name}_{sym}"
        g = t.groupby("reason").agg(n=("pnl", "size"), pnl=("pnl", "sum"), gross=("gross", "sum"),
                                    slip=("slip", "sum"), credit=("credit", "sum"))
        tot = dict(trades=len(t), credit=t.credit.sum(), slip=t.slip.sum(), gross=t.gross.sum(),
                   net=t.pnl.sum(), slip_pct_credit=t.slip.sum() / t.credit.sum(),
                   avg_credit_to_width=(t.credit / t.width).mean(),
                   stop_loss_in_credits=(-t[t.reason == "stop_loss"].gross / t[t.reason == "stop_loss"].credit).mean()
                   if (t.reason == "stop_loss").any() else None,
                   stops_with_stale=int(((t.reason == "stop_loss") & (t.stale > 0)).sum()))
        out[key] = {"totals": tot, "by_reason": g.round(0).to_dict(orient="index")}
        lines.append(f"\n## {key}\n" + json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in tot.items()}) +
                     "\n" + g.round(0).to_string())
        if name == "pcs_default":
            st = t[t.reason == "stop_loss"][["open", "close", "credit", "gross", "mv", "stale"]]
            lines.append("stop-loss trades (gross P&L, underlying move open->close):\n" + st.round(3).to_string())

# cadence & sizing decomposition on SPY: one lever at a time vs baseline (default costs)
var = {
    "baseline": {},
    "no_stop": {"sl_mult": None},
    "risk_10pct": {"risk_frac": 0.10},
    "width_$5": {"wing": "abs", "width_abs": 5},
    "no_tp": {"tp": None},
    "calib_costs": "calib",
    "zero_costs": "zero",
}
dec = []
for sym in ("SPY", "QQQ"):
    for k, v in var.items():
        prm = dict(BASE)
        c = "default"
        if isinstance(v, dict):
            prm.update(v)
        else:
            c = v
        eq, tr, x = run_variant(prm, sym, 100_000, c)
        e = evaluate(eq, tr, x)
        dec.append(dict(sym=sym, variant=k, cagr=e["full_cagr"], sharpe=e["full_sharpe"], mdd=e["full_max_drawdown"],
                        trades=e["full_trades"], win=e["full_win_rate"], slippage=x["slippage"]))
dec = pd.DataFrame(dec)
lines.append("\n## one-lever decomposition (100k, full period)\n" + dec.round(3).to_string())
(OUT / "stage0_costs.txt").write_text("\n".join(lines))
print("\n".join(lines))
