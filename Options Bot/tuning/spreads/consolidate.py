"""Merge every raw stage file into results.csv (one row per evaluated variant x underlying x
capital x cost model). The row count is the trial count used for the deflated Sharpe."""
import sys
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent
frames = []
for f in sorted(OUT.glob("raw_stage_*.csv")):
    d = pd.read_csv(f)
    frames.append(d)
df = pd.concat(frames, ignore_index=True)
if "error" in df:
    bad = df[df["error"].notna()]
    if len(bad):
        print("errors:", len(bad), bad["error"].unique()[:5])
lead = ["stage", "vid", "sym", "capital", "costs"]
metr = [c for c in df.columns if c.startswith(("is_", "oos_", "full_"))]
rest = [c for c in df.columns if c not in lead + metr]
df = df[lead + sorted(metr, key=lambda c: (c.split("_")[0] != "is", c.split("_")[0] != "oos", c)) + rest]
df.insert(0, "trial", range(1, len(df) + 1))
df.to_csv(OUT / "results.csv", index=False)
print("trials:", len(df), "| distinct parameter sets:", df["params"].nunique(),
      "| by stage:", df.groupby("stage").size().to_dict())
