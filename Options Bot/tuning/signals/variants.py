"""Variant grids (every entry counts as one trial for the multiple-testing haircut)."""
from __future__ import annotations

import itertools

from common import ETFS, MEGA, UNIV

PMCC_UNIV = ["IWM", "AAPL", "NVDA", "AMZN", "GOOGL"]   # LEAPS fit in 60% of $10k
PMCC_ALLOC = 0.6


def s1_dip_pcs():
    out = []
    for rsi, ibs, sd, dte, ex in itertools.product(
            (5, 10), (None, 0.2), (0.25, 0.30), ((7, 14, 10), (14, 21, 17)), ("sma5", "nosma5")):
        p = dict(signal="dip", rsi_lo=rsi, ibs_lo=ibs, trend="sma200", structure="pcs",
                 short_delta=sd, long_delta=round(sd - 0.15, 2), dte_min=dte[0], dte_max=dte[1],
                 dte_target=dte[2], tp=0.5, max_days=5, exit_sma5=(ex == "sma5"))
        lab = f"S1 rsi<{rsi}{' ibs<.2' if ibs else ''} d{sd} dte{dte[0]}-{dte[1]} {ex}"
        out.append(("S1_dip_pcs", lab, "dip_pcs", p, UNIV))
    return out


def s2_dip_cds():
    out = []
    for rsi, ibs, ivr, md, ex in itertools.product((5, 10), (None, 0.2), (None, 50.0), (5, 10),
                                                   (True, False)):
        p = dict(signal="dip", rsi_lo=rsi, ibs_lo=ibs, trend="sma200", structure="cds",
                 long_delta=0.50, short_delta=0.25, dte_min=21, dte_max=35, dte_target=28,
                 ivr_max=ivr, max_days=md, exit_sma5=ex, debit_tp=0.5, debit_sl=0.5,
                 risk_frac=0.10)
        lab = (f"S2 rsi<{rsi}{' ibs<.2' if ibs else ''}{' ivr<50' if ivr else ''} "
               f"{md}d {'sma5' if ex else 'nosma5'}")
        out.append(("S2_dip_cds", lab, "dip_cds", p, UNIV))
    return out


def s3_ob_ccs():
    out = []
    for k, rh, ivr, sd in itertools.product((None, 1.5, 2.0), (90, 95), (None, 50.0), (0.20, 0.30)):
        p = dict(signal="overbought", rsi_hi=rh, atr_k=k, trend=None, ivr_min=ivr,
                 structure="ccs", short_delta=sd, long_delta=0.10 if sd == 0.20 else 0.15,
                 dte_min=7, dte_max=21, dte_target=14, tp=0.5, max_days=5, exit_sma5=True)
        lab = f"S3 rsi>{rh}{f' >20dma+{k}atr' if k else ''}{' ivr>50' if ivr else ''} d{sd}"
        out.append(("S3_ob_ccs", lab, "ob_ccs", p, UNIV))
    return out


def s4_vol_spike():
    out = []
    trig = {"ratio>1": dict(vix_ratio_hi=1.0, vix_hi=None),
            "vix>30": dict(vix_ratio_hi=99.0, vix_hi=30.0),
            "either": dict(vix_ratio_hi=1.0, vix_hi=30.0)}
    for (tn, tp_), st, md in itertools.product(trig.items(), ("pcs", "cds"), (10, 21)):
        p = dict(signal="volspike", trend=None, structure=st, dte_min=21, dte_max=45,
                 dte_target=30, max_days=md, exit_sma5=False, **tp_)
        if st == "pcs":
            p.update(short_delta=0.25, long_delta=0.10, tp=0.5, risk_frac=0.25)
        else:
            p.update(long_delta=0.50, short_delta=0.25, debit_tp=0.5, debit_sl=0.5, risk_frac=0.10)
        out.append(("S4_vol_spike", f"S4 {tn} {st} {md}d", "vol_spike", p, ETFS))
    return out


def s5_pmcc():
    out = []
    sig = {"rsi2>90": dict(sell_signal="rsi2", sell_rsi=90.0),
           "rsi2>70": dict(sell_signal="rsi2", sell_rsi=70.0),
           "up2": dict(sell_signal="streak", sell_streak=2),
           "up3": dict(sell_signal="streak", sell_streak=3)}
    for (sn, sp), cd, dte in itertools.product(sig.items(), (0.30, 0.20),
                                               ((30, 45, 38, 21), (14, 30, 21, 7))):
        p = dict(alloc=PMCC_ALLOC, call_delta=cd, dte_min=dte[0], dte_max=dte[1],
                 dte_target=dte[2], roll_dte=dte[3], **sp)
        out.append(("S5_pmcc_signal", f"S5 {sn} d{cd} dte{dte[0]}-{dte[1]}", "pmcc_signal", p,
                    PMCC_UNIV))
    return out


def baselines():
    return [("B_pmcc", "pmcc (alloc .6)", "pmcc", dict(alloc=PMCC_ALLOC), PMCC_UNIV),
            ("B_pmcc", "pmcc_sma50 (alloc .6)", "pmcc_sma50", dict(alloc=PMCC_ALLOC), PMCC_UNIV),
            ("B_bh", "buy_hold", "buy_hold", {}, UNIV)]


# RS routing structures (S6); `dip` slot filled with the frozen S1 winner at runtime
RS_STRUCT = {
    "pcs30-45": dict(signal="always", trend=None, structure="pcs", short_delta=0.25,
                     long_delta=0.10, dte_min=30, dte_max=45, dte_target=38, tp=0.5,
                     sl_mult=2.0, close_dte=21, max_days=None, exit_sma5=False),
    "cds30-45": dict(signal="always", trend=None, structure="cds", long_delta=0.50,
                     short_delta=0.25, dte_min=30, dte_max=45, dte_target=38, debit_tp=0.5,
                     debit_sl=0.5, close_dte=14, max_days=None, exit_sma5=False,
                     risk_frac=0.30),
}

GRID = s1_dip_pcs() + s2_dip_cds() + s3_ob_ccs() + s4_vol_spike() + s5_pmcc()
