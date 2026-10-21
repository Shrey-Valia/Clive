"""Unit tests for optionsbot.indicators against hand-computed values.

Run:  python3 -m pytest tests/test_indicators.py -q
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from optionsbot import indicators as ind  # noqa: E402

A = 1e-6


def S(vals, start="2024-01-01"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def bars(rows, start="2024-01-01"):
    """rows: list of (open, high, low, close)."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], dtype=float)
    df.index = pd.bdate_range(start, periods=len(rows))
    return df


def close_list(s):
    return [None if pd.isna(x) else round(float(x), 6) for x in s]


# -------------------------------------------------------------- averages
def test_sma():
    assert close_list(ind.sma(S([1, 2, 3, 4, 5]), 3)) == [None, None, 2, 3, 4]


def test_ema_default_and_seeded():
    # alpha = 2/(3+1) = 0.5 ; 1 -> 1.5 -> 2.25
    assert np.allclose(ind.ema(S([1, 2, 3]), 3), [1, 1.5, 2.25])
    # seeded: idx2 = mean(1,2,3)=2 ; idx3 = 2 + .5*(4-2) = 3
    e = ind.ema(S([1, 2, 3, 4]), 3, seed_sma=True)
    assert close_list(e) == [None, None, 2, 3]


def test_wilder():
    w = ind.wilder(S([1, 2, 3, 4, 5]), 3)
    # seed 2 ; 2 + (4-2)/3 = 2.666667 ; 2.666667 + (5-2.666667)/3 = 3.444444
    assert close_list(w) == [None, None, 2.0, 2.666667, 3.444444]


# -------------------------------------------------------------- oscillators
def test_rsi2_hand():
    # deltas +1, -.5, +1, -.5
    r = ind.rsi(S([10, 11, 10.5, 11.5, 11]), 2)
    # idx2: g=.5 l=.25 RS=2 -> 66.6667 ; idx3: g=.75 l=.125 RS=6 -> 85.7143
    # idx4: g=.375 l=.3125 RS=1.2 -> 54.5455
    assert close_list(r)[:2] == [None, None]
    assert r.iloc[2] == pytest.approx(200 / 3, abs=1e-4)
    assert r.iloc[3] == pytest.approx(600 / 7, abs=1e-4)
    assert r.iloc[4] == pytest.approx(100 - 100 / 2.2, abs=1e-4)


def test_rsi_edge_cases():
    assert ind.rsi(S([1, 2, 3, 4, 5]), 2).dropna().eq(100).all()
    assert ind.rsi(S([5, 4, 3, 2, 1]), 2).dropna().eq(0).all()
    assert ind.rsi(S([3, 3, 3, 3]), 2).dropna().eq(50).all()


def test_macd_small():
    m = ind.macd(S([1, 2, 3]), fast=1, slow=2, signal=1)
    # ema1 = s ; ema2 (alpha 2/3) = 1, 1.666667, 2.555556
    assert np.allclose(m["macd"], [0, 1 / 3, 3 - 23 / 9], atol=A)
    assert np.allclose(m["hist"], 0, atol=A)  # signal(1) == macd


def test_momentum_zscore():
    assert close_list(ind.momentum(S([100, 110, 121]), 1)) == [None, 0.1, 0.1]
    z = ind.zscore(S([1, 2, 3]), 3)  # mean 2, sample std 1
    assert z.iloc[-1] == pytest.approx(1.0)


# -------------------------------------------------------------- range / trend
ADX_BARS = bars([
    (9, 10, 8, 9),
    (10, 11, 9, 10.5),
    (11, 12, 10, 11),
    (11, 11.5, 9, 9.5),
    (10, 11, 8.5, 10),
])


def test_true_range_atr():
    tr = ind.true_range(ADX_BARS)
    assert close_list(tr) == [2, 2, 2, 2.5, 2.5]
    a = ind.atr(ADX_BARS, 2)
    # seed mean(2,2)=2 ; 2+(2-2)/2=2 ; 2+(2.5-2)/2=2.25 ; 2.25+(2.5-2.25)/2=2.375
    assert close_list(a) == [None, 2, 2, 2.25, 2.375]


def test_adx_hand():
    d = ind.adx(ADX_BARS, 2)
    # +DM: -,1,1,0,0 ; -DM: -,0,0,1,.5 ; TR: -,2,2,2.5,2.5
    # ATR: idx2 2, idx3 2.25, idx4 2.375 ; +DMs 1,.5,.25 ; -DMs 0,.5,.5
    assert d["plus_di"].iloc[2] == pytest.approx(50)
    assert d["minus_di"].iloc[2] == pytest.approx(0)
    assert d["plus_di"].iloc[3] == pytest.approx(100 * 0.5 / 2.25)
    assert d["minus_di"].iloc[4] == pytest.approx(100 * 0.5 / 2.375)
    assert d["dx"].iloc[2] == pytest.approx(100)
    assert d["dx"].iloc[3] == pytest.approx(0)
    assert d["dx"].iloc[4] == pytest.approx(100 / 3)
    # ADX seed mean(100, 0) = 50 at idx3 ; idx4 = 50 + (33.333-50)/2 = 41.6667
    assert pd.isna(d["adx"].iloc[2])
    assert d["adx"].iloc[3] == pytest.approx(50)
    assert d["adx"].iloc[4] == pytest.approx(125 / 3)


def test_bollinger():
    b = ind.bollinger(S([1, 2, 3]), 3, 2)
    sd = math.sqrt(2 / 3)
    assert b["mid"].iloc[-1] == pytest.approx(2)
    assert b["upper"].iloc[-1] == pytest.approx(2 + 2 * sd)
    assert b["lower"].iloc[-1] == pytest.approx(2 - 2 * sd)
    assert b["pct_b"].iloc[-1] == pytest.approx((3 - (2 - 2 * sd)) / (4 * sd))
    assert b["bandwidth"].iloc[-1] == pytest.approx(4 * sd / 2)


def test_keltner_and_squeeze():
    flat = bars([(100, 101, 99, 100)] * 30)
    k = ind.keltner(flat, 20, 10, 2)
    assert k["mid"].iloc[-1] == pytest.approx(100)
    assert k["upper"].iloc[-1] == pytest.approx(104)   # ATR = 2
    sq = ind.squeeze(flat)
    assert sq.dtype == bool
    assert not sq.iloc[:18].any()          # undefined -> False
    assert sq.iloc[-1]                     # BB width 0 inside KC
    # steady trend with tiny true ranges: BB std (~5.8) >> ATR (~1) -> no squeeze
    rows = [(c - 1, c + .01, c - 1, c) for c in range(100, 130)]
    assert not ind.squeeze(bars(rows)).iloc[-1]


def test_ibs_donchian():
    df = bars([(9, 10, 8, 9.5), (9, 9, 9, 9), (9, 12, 7, 8)])
    assert close_list(ind.ibs(df)) == [0.75, None, 0.2]
    d = ind.donchian(df, 2)
    assert close_list(d["upper"]) == [None, 10, 12]
    assert close_list(d["lower"]) == [None, 8, 7]
    assert close_list(d["mid"]) == [None, 9, 9.5]


# -------------------------------------------------------------- realized vol
def test_rv_close():
    up = S(np.exp(0.01 * np.arange(10)))
    assert ind.rv_close(up, 5).dropna().abs().max() < 1e-12                    # demeaned: 0
    assert ind.rv_close(up, 5, demean=False).iloc[-1] == pytest.approx(0.01 * math.sqrt(252))
    alt = S(np.exp(np.array([0, .01, 0, .01, 0])))                              # +.01,-.01,...
    assert ind.rv_close(alt, 2).iloc[-1] == pytest.approx(math.sqrt(2e-4) * math.sqrt(252))


def test_rv_range_estimators():
    hl = math.exp(0.02)
    df = bars([(100, 100 * hl, 100, 100)] * 6)
    assert ind.rv_parkinson(df, 3).iloc[-1] == pytest.approx(math.sqrt(4e-4 / (4 * math.log(2)) * 252))
    assert ind.rv_garman_klass(df, 3).iloc[-1] == pytest.approx(math.sqrt(0.5 * 4e-4 * 252))
    yz_df = bars([(100, 101, 99, 100)] * 6)
    k = 0.34 / (1.34 + 4 / 2)
    rs = math.log(1.01) ** 2 + math.log(0.99) ** 2
    assert ind.rv_yang_zhang(yz_df, 3).iloc[-1] == pytest.approx(math.sqrt((1 - k) * rs * 252))


def test_forward_labels():
    c = S([100, 110, 99, 121])
    assert close_list(ind.forward_return(c, 1)) == [0.1, -0.1, round(121 / 99 - 1, 6), None]
    up = S(np.exp(0.01 * np.arange(6)))
    fv = ind.forward_realized_vol(up, 2)
    assert fv.iloc[0] == pytest.approx(0.01 * math.sqrt(252))
    assert fv.iloc[-2:].isna().all()


# -------------------------------------------------------------- IV helpers
def test_iv_rank_percentile():
    iv = S([10, 20, 30, 15])
    assert ind.iv_rank(iv, 4).iloc[-1] == pytest.approx(25)        # (15-10)/(30-10)
    assert ind.iv_percentile(iv, 4).iloc[-1] == pytest.approx(100 / 3)  # 1 of 3 below


def test_vix_term_ratio_aligns():
    f = S([20, 30, 15])
    b = S([25, 25], start="2024-01-02")
    r = ind.vix_term_ratio(f, b)
    assert list(r.round(6)) == [1.2, 0.6]


# -------------------------------------------------------------- path / calendar
def test_drawdown_streak():
    assert close_list(ind.drawdown(S([100, 110, 99, 121]))) == [0, 0, -0.1, 0]
    assert list(ind.streak(S([1, 2, 3, 2, 1, 1, 2]))) == [0, 1, 2, -1, -2, 0, 1]


def test_turn_of_month():
    idx = pd.bdate_range("2024-01-02", "2024-02-08")   # full Jan ; Feb 1..8
    tom = ind.turn_of_month(idx, before=1, after=3)
    flagged = [d.strftime("%m-%d") for d, f in zip(idx, tom) if f]
    # first 3 + last 1 of Jan, first 3 of Feb ; Feb 8 is last in index (incomplete month)
    assert flagged == ["01-02", "01-03", "01-04", "01-31", "02-01", "02-02", "02-05", "02-08"]


def test_regime():
    c = S(list(range(1, 11)) + [5])
    v = S([10] * 10 + [30])
    r = ind.regime(c, v, trend_n=3, vol_threshold=20)
    assert r.iloc[:2].isna().all()
    assert r.iloc[5] == "up/low"
    assert r.iloc[-1] == "down/high"
