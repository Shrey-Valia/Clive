"""Config grids for the stock-backed premium study. Every config = one trial."""
from __future__ import annotations

import itertools

DTES = {  # name: (min, max, target)
    "d14": (14, 21, 17),
    "d21": (21, 30, 25),
    "d30": (30, 45, 38),
    "d45": (45, 60, 52),
}


def dte(name):
    a, b, t = DTES[name]
    return dict(dte_min=a, dte_max=b, dte_target=t)


def _cfg(cid, family, strategy, params):
    return {"id": cid, "family": family, "strategy": strategy, "params": params}


def bench():
    return [_cfg("buy_hold", "bench", "buy_hold", {})]


def stage_A():
    """Wheel core: deltas x DTE x put take-profit."""
    out = bench()
    for pd_, cd in itertools.product([0.15, 0.20, 0.25, 0.30, 0.40], [0.15, 0.20, 0.30, 0.40]):
        out.append(_cfg(f"wh_p{int(pd_*100)}_c{int(cd*100)}_d30_tp50", "wheel_core", "wheel_x",
                        dict(put_delta=pd_, call_delta=cd, **dte("d30"), put_tp=0.5)))
    for dn, tp in itertools.product(DTES, [None, 0.5, 0.75]):
        cid = f"wh_p30_c30_{dn}_tp{int(tp*100) if tp else 0}"
        if any(c["id"] == cid for c in out):
            continue
        out.append(_cfg(cid, "wheel_core", "wheel_x",
                        dict(put_delta=0.30, call_delta=0.30, **dte(dn), put_tp=tp)))
    return out


def stage_C():
    """Covered call, CSP and collar grids (independent of the wheel results)."""
    out = []
    # covered call: delta x DTE x management
    for cd, dn in itertools.product([0.15, 0.20, 0.30, 0.40], ["d14", "d30", "d45"]):
        for mg, extra in [("hold", {}), ("x21", {"call_exit_dte": 21}), ("tp50", {"call_tp": 0.5})]:
            if dn == "d14" and mg == "x21":
                continue
            out.append(_cfg(f"cc_c{int(cd*100)}_{dn}_{mg}", "covered_call", "wheel_x",
                            dict(entry="stock", put_tp=None, call_delta=cd, **dte(dn), **extra)))
    # CSP: delta x DTE x tp
    for pd_, dn, tp in itertools.product([0.15, 0.20, 0.30, 0.40], ["d14", "d30", "d45"], [None, 0.5]):
        out.append(_cfg(f"csp_p{int(pd_*100)}_{dn}_tp{int(tp*100) if tp else 0}", "csp", "wheel_x",
                        dict(on_assign="sell", put_delta=pd_, **dte(dn), put_tp=tp)))
    # collar: same-expiry 30-45 DTE, put x call deltas
    for pd_, cd in itertools.product([0.15, 0.25, 0.35], [0.20, 0.30, 0.40]):
        out.append(_cfg(f"col_p{int(pd_*100)}_c{int(cd*100)}_d30", "collar", "collar_x",
                        dict(put_delta=pd_, call_delta=cd, **dte("d30"))))
    # collar: long-dated put (75-120 DTE, roll at 30) financed by monthly calls
    for pd_, cd in itertools.product([0.15, 0.25], [0.20, 0.30]):
        out.append(_cfg(f"col_p{int(pd_*100)}_c{int(cd*100)}_put90", "collar", "collar_x",
                        dict(put_delta=pd_, call_delta=cd, **dte("d30"), put_dte_min=75,
                             put_dte_max=120, put_dte_target=90, put_roll_dte=30)))
    # zero-cost collar (same expiry, call chosen to finance the put)
    for pd_ in [0.15, 0.25]:
        out.append(_cfg(f"col_zc_p{int(pd_*100)}_d30", "collar", "collar_x",
                        dict(put_delta=pd_, call_delta=0.40, call_delta_min=0.10, zero_cost=True,
                             **dte("d30"))))
    return out


STAGES = {"A": stage_A, "C": stage_C}

try:   # later stages are appended in configs_b.py once stage A is in
    from configs_b import STAGES_B
    STAGES.update(STAGES_B)
except ImportError:
    pass
