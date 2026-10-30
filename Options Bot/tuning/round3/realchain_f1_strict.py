"""Real-chain F1 with the engine's strict delta search (bug R3-1 fixed), offline cache only.
Shows how much of F1's published 15/15 record came from selecting ~0.03-delta calls."""
import os
import pickle

os.environ["OPTIONSBOT_STRICT_DELTA"] = "1"
import pandas as pd  # noqa: E402

from common import F1, OUT, WORK, engine_client  # noqa: E402
from optionsbot import engine as E, runner  # noqa: E402
from optionsbot.strategies_spreads import SpreadBook  # noqa: E402

assert E.STRICT_DELTA_SEARCH
engine_client()
rows = []
res = {}
for cap in (10_000, 15_000):
    r = runner.run_one(SpreadBook, F1, "SPY", "2024-03-01", "2026-10-20", cap)
    t = r["trades"]
    res[cap] = r["trades"]
    m = r["metrics"]
    rows.append({"capital": cap, "cagr": m["cagr"], "sharpe": m["sharpe"], "max_dd": m["max_drawdown"],
                 "trades": m["n_trades"], "win": m.get("win_rate"), "worst": m.get("worst_trade"),
                 "is_sharpe": m["in_sample"].get("sharpe"), "oos_sharpe": m["out_of_sample"].get("sharpe"),
                 "n_theo": m.get("n_theo_selections")})
    print(rows[-1])
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 120)
print(res[10_000][["open_date", "close_date", "legs", "entry_cash", "pnl", "reason"]].to_string())
pd.DataFrame(rows).to_csv(OUT / "realchain_f1_strict.csv", index=False)
res[10_000].to_csv(OUT / "realchain_f1_strict_trades.csv", index=False)
