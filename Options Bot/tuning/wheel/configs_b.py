"""Stage B: wheel management levers, applied one at a time on top of two cores:
the default core (0.30/0.30, 30-45 DTE, put TP 50%) and the IS-chosen core from
stage A (written to core_choice.json after stage A, IS data only)."""
from __future__ import annotations

import json
from pathlib import Path

from configs import _cfg, dte

HERE = Path(__file__).resolve().parent

LEVERS = [
    ("ctp50", {"call_tp": 0.5}),
    ("ctp75", {"call_tp": 0.75}),
    ("basis_strict", {"call_basis": "strict"}),
    ("basis_none", {"call_basis": "none"}),
    ("basis_net", {"call_basis": "net"}),
    ("px21", {"put_exit_dte": 21}),
    ("cx21", {"call_exit_dte": 21}),
    ("proll", {"put_roll_tested": True}),
    ("croll", {"call_roll_tested": True}),
    ("pcroll", {"put_roll_tested": True, "call_roll_tested": True}),
    ("cg_rsi2skip", {"call_gate": "rsi2_skip_low"}),
    ("cg_rsi2hi", {"call_gate": "rsi2_high"}),
    ("cg_upday", {"call_gate": "up_day"}),
    ("cg_below200", {"call_gate": "above200"}),
    ("pg_rsi2low", {"put_gate": "rsi2_low"}),
    ("pg_downday", {"put_gate": "down_day"}),
    ("pg_above200", {"put_gate": "above200"}),
    ("pg_vrp11", {"put_gate": "vrp", "put_gate_x": 1.1}),
    ("pg_vix30", {"put_gate": "vix_pct", "put_gate_x": 0.3}),
    ("kill", {"kill_switch": True}),
    ("kill_sh", {"kill_switch": True, "kill_shares": True}),
    ("exdiv", {"exdiv_roll": True}),
    ("cdte14", {"call_dte_min": 14, "call_dte_max": 21, "call_dte_target": 17}),
    ("cdte45", {"call_dte_min": 45, "call_dte_max": 60, "call_dte_target": 52}),
]


def cores():
    out = {"def": dict(put_delta=0.30, call_delta=0.30, **dte("d30"), put_tp=0.5)}
    f = HERE / "core_choice.json"
    if f.exists():
        c = json.loads(f.read_text())
        out["is"] = c["params"]
    return out


def stage_B():
    out = []
    for cn, core in cores().items():
        for ln, lv in LEVERS:
            out.append(_cfg(f"whB_{cn}_{ln}", "wheel_mgmt", "wheel_x", {**core, **lv}))
    return out


def stage_D():
    """Combination stage: filled from stage-B IS results (combo_choice.json)."""
    f = HERE / "combo_choice.json"
    if not f.exists():
        return []
    return [_cfg(c["id"], c["family"], c["strategy"], c["params"]) for c in json.loads(f.read_text())]


STAGES_B = {"B": stage_B, "D": stage_D}
