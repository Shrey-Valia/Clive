"""Load read-only Alpaca market-data keys into os.environ (never printed).

Order: OPT_* keys from the main checkout's Options Bot/.env, else APCA_* from the shorting bot's
.env (data.alpaca.markets only; optionsbot/data.py asserts no order/position endpoints)."""
from __future__ import annotations

import os
from pathlib import Path

MAIN = Path("/Users/shreyvalia/Desktop/Clive-Trading Bot")


def _read(p: Path) -> dict:
    env = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load() -> str:
    opt = _read(MAIN / "Options Bot" / ".env")
    if opt.get("OPT_APCA_API_KEY_ID") and "..." not in opt["OPT_APCA_API_KEY_ID"]:
        os.environ["APCA_API_KEY_ID"] = opt["OPT_APCA_API_KEY_ID"]
        os.environ["APCA_API_SECRET_KEY"] = opt["OPT_APCA_API_SECRET_KEY"]
        return "options"
    sh = _read(MAIN / "Stock Shorting Bot" / ".env")
    os.environ["APCA_API_KEY_ID"] = sh.get("APCA_API_KEY_ID", "")
    os.environ["APCA_API_SECRET_KEY"] = sh.get("APCA_API_SECRET_KEY", "")
    return "shorting-readonly"
