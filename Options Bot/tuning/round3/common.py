"""Shared setup for tuning/round3 (longer history, condor management, dip PCS, tail hedge, sizing).

Data
----
* Real-chain runs use the optionsbot engine on a *copy* of the main checkout's Alpaca cache
  (R3_CACHE, default: the session scratchpad clone). The engine client is opened offline unless
  R3_ONLINE=1, so nothing is fetched and nothing is written into the main checkout.
* Long-history runs use Cboe index CSVs (VIX, VIX9D, VIX3M, VXN, RVX, SPX, CNDR, PUT ...) in
  R3_LHCACHE and daily OHLC for the ETFs (R3_LHCACHE/ohlc).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # "Options Bot/"
sys.path.insert(0, str(ROOT))
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

_SP = "/private/tmp/claude-501/-Users-shreyvalia-Desktop-Clive-Trading-Bot/0599da58-f919-445c-8527-b80ba8eb6e90/scratchpad"
CACHE = Path(os.environ.get("R3_CACHE", f"{_SP}/cache"))
LHCACHE = Path(os.environ.get("R3_LHCACHE", f"{_SP}/lhcache"))
WORK = Path(os.environ.get("R3_WORK", f"{_SP}/r3work"))
WORK.mkdir(parents=True, exist_ok=True)

START, END = "2024-03-01", "2026-10-20"
IS_END, OOS_START = "2025-06-30", "2025-07-01"

DIP = dict(signal='dip', rsi_lo=10, ibs_lo=0.2, trend='sma200', structure='pcs', short_delta=0.25,
           long_delta=0.1, dte_min=7, dte_max=14, dte_target=10, tp=0.5, max_days=5, exit_sma5=True)
F1 = dict(structure="ic", put_delta=0.1, call_delta=0.1, wing="pct", width_pct=0.03, dte_min=50,
          dte_max=70, dte_target=60, tp=None, sl_mult=None, breach=False, close_dte=None,
          risk_frac=0.2, max_positions=1)


def engine_client():
    """Install an AlpacaData client on the scratch cache into optionsbot.runner."""
    from optionsbot import runner
    from optionsbot.data import AlpacaData
    if runner._client is None:
        runner._client = AlpacaData(cache_dir=CACHE, offline=os.environ.get("R3_ONLINE") != "1")
    return runner._client
