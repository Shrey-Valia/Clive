# Technical Signals for Timing Options Trades on Large-Cap Stocks/ETFs

Universe: SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, GOOGL, META
Input: daily OHLCV (split/dividend-adjusted closes for signals; unadjusted prices for strike selection)
Date: 2026-10-21. Status: research spec. Nothing here has been backtested in this repo yet.

---

## 0. Read this first: how much to trust each claim

The evidence grades used below:

| Grade | Meaning |
|---|---|
| **A** | Peer-reviewed, long sample, replicated out-of-sample and across markets |
| **B** | Peer-reviewed or a careful, transparent backtest, but with one of: short sample, few replications, or documented decay |
| **C** | Practitioner backtests only (Connors, Quantified Strategies, CXO, Crabel). Plausible, but in-sample and often optimized |
| **D** | Folklore. No credible quantitative support |

Some general findings apply to everything below:

1. **Post-publication decay is real.** McLean & Pontiff (2016, *J. Finance*) studied 97 cross-sectional anomalies. Returns were about 26% lower out-of-sample and about 58% lower after publication. Expect a published indicator edge to be about half the size the paper reports.
2. **Classic technical rules faded after they were published.** Brock, Lakonishok & LeBaron (1992, *J. Finance*) found MA and breakout rules worked on the DJIA from 1897 to 1986. Sullivan, Timmermann & White (1999, *J. Finance*) corrected for data snooping and found the best rule did not beat buy-and-hold from 1987 to 1996.
3. **The index switched from momentum to mean reversion at the daily horizon.** Baltussen, van Bekkum & Da (2019, *JFE*, "Indexing and stock market serial dependence around the world") found daily first-order autocorrelation of index returns was positive for decades, turned negative in the 2000s, and has stayed negative. They tie the change to the growth of index products and arbitrage. This is the structural reason short-term mean-reversion signals (RSI(2), IBS) work on SPY/QQQ after 2000 and trend signals at daily horizons do not.
4. **Moving-average timing mostly reduces drawdowns. It does not reliably add return.** Zakamulin (2015/2017) used about 155 years of data and found no statistically significant outperformance in the second half of the sample. Many reported results suffer from look-ahead bias, such as trading on the same close that generates the signal. Faber's 10-month SMA (2007, updated 2013) produced similar CAGR to buy-and-hold on the S&P 500 from 1901 to 2012, with about one-third less volatility and a max drawdown of about -50% versus -83%.
5. **Options change the arithmetic.** A typical mean-reversion edge on SPY is +0.2% to +0.6% over 2 to 5 days. An ATM 30-DTE SPY call has delta of about 0.5 and theta of about 0.1% to 0.15% of the underlying per day, and you also pay the half-spread twice. **Most of an equity edge gets eaten by premium decay and spread unless:**
   (a) the position is short premium, so time decay works for you, or
   (b) it is a vertical spread, which nets out most theta and vega, or
   (c) it is held only 1 to 3 days with tight fills.
   The variance risk premium (implied above realized vol) is the strongest documented options edge and is grade A. Carr & Wu (2009, RFS) and Bondarenko on the CBOE PUT index: from 1986 to 2015, PUT had a gross Sharpe of 0.67 versus 0.47 for the S&P 500 TR, volatility of 10.1% versus 15.3%, but skew of -2.09. **The most robust design is therefore: harvest the VRP with defined-risk short premium, and use technical signals as filters for when and when not to sell, and as triggers for small directional debit spreads.**

---

## 1. Shared helpers (vectorized pandas)

All signals are computed at the close of day *t* and acted on at the next tradable price:
- Next open (t+1), or
- Within the last 10 to 15 minutes of day *t*, using a signal computed from a 15:45 snapshot.

Never fill at the same close that generated the signal. That is the main source of look-ahead bias Zakamulin documents.

```python
import numpy as np
import pandas as pd

# df: DatetimeIndex, columns ['open','high','low','close','volume'] (adjusted)

def sma(s, n):  return s.rolling(n, min_periods=n).mean()
def ema(s, n):  return s.ewm(span=n, adjust=False, min_periods=n).mean()
def wilder(s, n):  # Wilder smoothing = EMA with alpha=1/n
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()

def true_range(df):
    pc = df['close'].shift(1)
    return pd.concat([df['high'] - df['low'],
                      (df['high'] - pc).abs(),
                      (df['low'] - pc).abs()], axis=1).max(axis=1)

def atr(df, n=14):
    return wilder(true_range(df), n)

def rsi(close, n=14):
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0)  # all-up window -> 100

def realized_vol(close, n=20, ann=252):
    return np.log(close).diff().rolling(n).std() * np.sqrt(ann)
```

---

## 2. Trend / regime signals

### 2.1 SMA 200 and the 50/200 cross (regime filter)

- **Formula:**
  - `regime_up = close > SMA(close, 200)`
  - Golden cross: `SMA50 > SMA200`
  - Monthly variant (Faber): `month-end close > 10-month SMA`
- **Evidence (B for risk reduction, C/D for return):**
  - Faber (2007): see section 0, point 4.
  - Zakamulin: no significant outperformance on the post-1950 half of the sample. Return outperformance is concentrated in a few crashes (1929 to 1932, 2000 to 2002, 2008).
  - Whipsaw is frequent. Siegel (*Stocks for the Long Run*) found the 200-day rule's edge on the DJIA disappeared once transaction costs were included after the 1970s.
- **The useful part for options is conditional volatility.**
  - Below the 200DMA, realized volatility is roughly 1.5 to 2 times higher. Daily return autocorrelation is more negative, so moves are bigger and reversals sharper. Left-tail events cluster there.
  - That makes the 200DMA a sizing and structure filter, not an alpha signal.
- **Decay/crowding:** The signal is universal and watched by everyone. It has no alpha left, but its regime information (volatility clustering) is structural and does not decay.
- **Options use:**
  - Above 200DMA: allow short puts and put spreads, and buy-the-dip debit call spreads.
  - Below 200DMA: cut short-premium size by half or more and require wider wings. Prefer defined-risk structures only. No naked short puts.
  - Dip-buying stays allowed below the 200DMA only with spreads and smaller size. Connors's own tests show RSI(2) dips below the 200DMA have higher average return but much fatter loss tails.

```python
def sig_regime(df):
    c = df['close']
    s200 = sma(c, 200); s50 = sma(c, 50)
    return pd.DataFrame({
        'above200': c > s200,
        'golden': s50 > s200,
        'dist200_pct': c / s200 - 1,
        'slope200_20d': s200 / s200.shift(20) - 1,
    })
```

### 2.2 EMA crossovers (e.g. 10/30, 20/50)

- **Evidence: D on its own for large-cap equities.** There is no robust net-of-cost edge beyond what the 200DMA already captures. Faster crosses whipsaw constantly in the post-2000 mean-reverting index regime (see section 0, point 3).
- **Options use:**
  - Use only as a secondary trend-state tag, for example to pick the long-call-spread strike tenor.
  - Do not use as an entry trigger.

```python
def sig_ema_cross(df, fast=20, slow=50):
    f, s = ema(df['close'], fast), ema(df['close'], slow)
    state = (f > s).astype(int)
    return pd.DataFrame({'ema_up': state.astype(bool),
                         'cross_up': state.diff() == 1,
                         'cross_dn': state.diff() == -1})
```

### 2.3 Time-series momentum (TSMOM) over 3, 6 and 12 months

- **Formula:**
  - `TSMOM_k = close_t / close_{t-21k} - 1` (or the excess over T-bills)
  - Signal: `sign(TSMOM_12)`, or the average of the signs over 3, 6 and 12 months
- **Evidence:**
  - **A as a diversified futures strategy.**
    - Moskowitz, Ooi & Pedersen (2012, *JFE*): 58 futures from 1985 to 2009, 12-month lookback with 1-month hold, significant in nearly every contract.
    - Hurst, Ooi & Pedersen (2017, *JPM*): 67 markets from 1880 to 2016, Sharpe about 0.4 net of 2/20 fees, positive in every decade.
  - **B or C for a single equity index.**
    - For SPY alone, 12-month TSMOM roughly matches what the 10-month SMA does. Antonacci's "absolute momentum" is a version of this.
    - Trend returns were weak from 2012 to 2018 (well-documented CTA drawdown) and strong in 2022.
- **Decay:** Moderate. Post-2009 Sharpe is roughly half the pre-2009 Sharpe.
- **Options use:**
  - Regime input that complements the 200DMA.
  - If `TSMOM_12 > 0` and `TSMOM_3 > 0`, the "strong uptrend" state permits higher-delta short puts (25 to 30 delta) and longer-dated call spreads.
  - If both are negative, apply the short-premium restrictions from section 2.4.

```python
def sig_tsmom(df, months=(3, 6, 12)):
    c = df['close']
    out = {f'mom_{m}m': c / c.shift(21 * m) - 1 for m in months}
    out = pd.DataFrame(out)
    out['tsmom_score'] = np.sign(out).mean(axis=1)  # -1..+1
    return out
```

### 2.4 ADX (trend strength, Wilder)

- **Formula:**
  - `+DM = max(H_t - H_{t-1}, 0)` if it exceeds `-DM`, else 0
  - `-DM = max(L_{t-1} - L_t, 0)` if it exceeds `+DM`, else 0
  - `+DI = 100 · wilder(+DM, 14) / ATR14`. `-DI` is defined the same way.
  - `DX = 100 · |+DI - -DI| / (+DI + -DI)`
  - `ADX = wilder(DX, 14)`
- **Evidence: D for return prediction.**
  - I found no peer-reviewed evidence that ADX predicts equity index returns.
  - Its legitimate use is as a regime descriptor. Rising ADX with -DI > +DI means a persistent, directional decline.
  - That is the environment where short puts get run over and mean-reversion entries keep catching knives. Examples: 2008, Q4 2018, 2022.
- **Options use (risk filter only):**
  - `ADX > 25`, ADX rising over 5 days, `-DI > +DI` and `close < SMA200` → **block new short premium** and block mean-reversion longs.
  - This rule has not been validated. Backtest it as a pure filter and keep it only if it reduces tail loss without killing too many trades.

```python
def sig_adx(df, n=14):
    h, l = df['high'], df['low']
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(df, n)
    pdi = 100 * wilder(pdm, n) / a
    ndi = 100 * wilder(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    adx = wilder(dx, n)
    return pd.DataFrame({'adx': adx, 'pdi': pdi, 'ndi': ndi,
                         'adx_rising': adx > adx.shift(5)})

def block_short_premium(df):
    r, a = sig_regime(df), sig_adx(df)
    return (~r['above200']) & (a['adx'] > 25) & a['adx_rising'] & (a['ndi'] > a['pdi'])
```

---

## 3. Mean-reversion signals

These are the strongest technical edges on US index ETFs after 2000, for the structural reason in section 0, point 3. They are weaker on single mega caps, which have more idiosyncratic momentum and earnings gaps.

### 3.1 RSI(2), Connors

- **Formula:**
  - Entry: `RSI(close, 2) < 10` (aggressive version `< 5`) with `close > SMA200`
  - Exit: `close > SMA5`, or `RSI2 > 70`, or N days have passed
- **Evidence: C, close to B for SPY.**
  - Connors & Alvarez (*Short Term Trading Strategies That Work*, 2008) report about 75 to 80% win rates and average gains of about 0.5% to 1% per trade on SPY/QQQ over roughly 1993 to 2007.
  - This is consistent with the negative index autocorrelation in Baltussen et al. (2019), which gives it a structural basis.
  - Independent practitioner replications (Quantified Strategies, CXO, Quantifiable Edges) say the edge persisted after 2008 but at lower magnitude. Win rates of about 65 to 75% are commonly cited. "Slightly less effective than before 2009" for Connors-style RSI variants.
  - I found no clean pre/post-2010 out-of-sample test. **Do one yourself.** Fit parameters on 1993 to 2009 and evaluate 2010 to present unchanged.
- **Known failure mode:** The exit has no stop, so the loss tail is long. In October 2008 and March 2020, multi-day sequences of RSI(2) < 5 kept falling.
- **Crowding:** Heavily published and probably partly arbitraged. The structural driver (index arbitrage and liquidity provision) persists.
- **Single stocks:** Weaker. Earnings gaps break the pattern. Exclude any entry with earnings inside the holding window.

```python
def sig_rsi2(df, entry=10, exit_rsi=70):
    c = df['close']
    r2 = rsi(c, 2)
    return pd.DataFrame({
        'rsi2': r2,
        'long_entry': (r2 < entry) & (c > sma(c, 200)),
        'long_exit': (c > sma(c, 5)) | (r2 > exit_rsi),
        'overbought': r2 > 90,
    })
```

**Options rule to backtest (R1):**
- **Entry:** Next open after the signal. Buy a call debit spread.
  - Long leg about 50 delta (ATM), short leg about 25 to 30 delta.
  - 14 to 30 DTE. Pick the expiry nearest 21 DTE.
  - Size so the debit is ≤ 1% of equity.
- **Exit:** First of these:
  - the underlying exit signal (`close > SMA5` or `RSI2 > 70`)
  - 5 trading days have passed
  - the spread reaches 60% of its maximum value
  - the spread loses 50% of the debit
- **Variant R1b:** Sell a put credit spread instead.
  - Short leg 20 to 30 delta, long leg 5 to 10 points lower on SPY, 7 to 21 DTE.
  - Take profit at 50% of credit. Exit on the same underlying signal or at 2 times the credit as a stop.
- **Rationale:** After a short-term dip, IV is elevated relative to RV. VIX tends to rise when SPY drops. The credit spread therefore collects mean reversion plus vol mean reversion (vega). The debit spread collects only delta.
- **Hypothesis to test:** R1b beats R1 on SPY/QQQ. Also check whether R1 beats R1b for NVDA/META, which have higher drift and bigger rebounds.

### 3.2 IBS (internal bar strength)

- **Formula:**
  - `IBS = (close - low) / (high - low)`, in [0, 1]
  - Long when IBS < 0.2. Exit at the next close, or when IBS > 0.8.
- **Evidence: C, close to B.**
  - Pagonidis (NAAIM paper, about 2013) found low IBS predicts next-day gains, and high IBS next-day losses, across many equity ETFs from inception to 2013.
  - Pandey & Joshi (2023, arXiv 2306.12434) replicated the effect on country ETFs over 10 years.
  - Practitioner tests on SPY (IBS < 0.2 entry, exit at next close above the prior close) show average next-day returns of about +0.2% to +0.4% versus about +0.04% unconditional, with most of the edge overnight.
  - IBS is a close-location version of 1-day reversal, consistent with Baltussen et al.
  - It works better combined: IBS < 0.2 and RSI(2) < 10, or IBS < 0.25 and close below the 10-day low band.
- **Decay:** Known since about 2010 to 2012. Reported to have weakened on SPY. Holds up better on QQQ.
- **Options use:**
  - The holding period is 1 to 2 days, so theta and spread costs dominate.
  - Use IBS mainly as an **entry-timing refinement** for R1/R1b: enter the spread at the close of the low-IBS day instead of on any RSI2 day. Avoid buying single-day options outright.
  - Better still, use it to time short puts. Sell the 0 to 7 DTE put spread late on a low-IBS day, when intraday IV is elevated.

```python
def sig_ibs(df, lo=0.2, hi=0.8):
    rng = (df['high'] - df['low']).replace(0, np.nan)
    ibs = (df['close'] - df['low']) / rng
    return pd.DataFrame({'ibs': ibs, 'ibs_low': ibs < lo, 'ibs_high': ibs > hi})
```

### 3.3 Bollinger %B

- **Formula:**
  - Bands: `mid = SMA20`, `upper/lower = mid ± 2·std20`
  - `%B = (close - lower) / (upper - lower)`
  - Long when %B < 0 and the close is above the 200DMA
- **Evidence: C/D.**
  - Lento, Gradojevic & Wright (2007, *Applied Economics Letters*) found the standard Bollinger breakout rule unprofitable after costs on major indices. Contrarian versions did somewhat better.
  - On SPY, %B < 0 is mostly a noisier, slower duplicate of RSI(2) < 10. The two are highly correlated.
- **Options use:** Treat as a substitute for RSI(2), not an additional signal. Do not stack it with RSI(2) and count it as independent confirmation.

```python
def sig_bbands(df, n=20, k=2.0):
    c = df['close']; m = sma(c, n); sd = c.rolling(n).std(ddof=0)
    up, lo = m + k * sd, m - k * sd
    return pd.DataFrame({'pctb': (c - lo) / (up - lo),
                         'bandwidth': (up - lo) / m})
```

### 3.4 Consecutive down days

- **Formula:** `down_streak ≥ 3` (closes lower 3 days in a row) and close above SMA200.
- **Evidence: C.**
  - Connors and others: 3 or more down closes on SPY above the 200DMA are followed by above-average 1 to 5 day returns. Same mechanism and the same caveats as RSI(2).
  - Quantified Strategies, CXO and Quantifiable Edges show similar results, typically with sample sizes in the tens to low hundreds of trades.
- **Options use:** Alternative trigger for R1/R1b. Useful as a robustness check: if RSI(2), %B and down-streak all "work", the effect is real. If only one works, suspect overfitting.

```python
def sig_down_streak(df):
    down = (df['close'] < df['close'].shift(1)).astype(int)
    grp = (down == 0).cumsum()
    streak = down.groupby(grp).cumsum()
    return pd.DataFrame({'down_streak': streak})
```

### 3.5 Distance from the 20DMA in ATR units (stretch)

- **Formula:** `z_atr = (close - SMA20) / ATR14`. Long when `z_atr < -2` (oversold stretch) and above SMA200. A reading above +2.5 is overextended.
- **Evidence: C.** There is no academic paper on this exact form. It is a volatility-normalized version of short-term reversal.
  - Its advantage is **portability across tickers**. NVDA and SPY get comparable thresholds, which RSI does not provide as cleanly.
- **Options use:**
  - Choose the short-put strike in ATR units. For example: short strike = `close - 1.5·ATR14·sqrt(DTE/5)`.
  - Use `z_atr < -2` as the trigger for R1b on single names.

```python
def sig_stretch(df, n=20, a=14):
    return ((df['close'] - sma(df['close'], n)) / atr(df, a)).rename('z_atr')
```

---

## 4. Volatility signals

Volatility itself is highly predictable: clustering, the HAR models of Corsi (2009), and volatility-managed portfolios (Moreira & Muir 2017, *J. Finance*) all show it. **Grade A for forecasting volatility.** Return direction is a different matter. These signals mostly decide whether to buy or sell premium and how far out of the money to go.

### 4.1 ATR / realized vol vs implied vol (VRP gauge)

- **Formula:** `vrp_proxy = IV30 - RV20`. For SPY use VIX, for QQQ use VXN, for IWM use RVX. For single stocks use ATM IV30 from the chain.
- **Evidence:**
  - **A that IV is above RV on average for index options.** Carr & Wu (2009). The CBOE PUT index's Sharpe advantage.
  - **B that IV minus RV sorts option returns cross-sectionally.** Goyal & Saretto (2009, *JFE*) sorted on HV minus IV. Long-short delta-hedged straddle portfolios earned significant monthly returns, robust across subperiods. Do, Foster & Gray (2016) questioned profitability after transaction costs.
  - **Caveat on timing:** Conditioning on VIX spikes adds little. One variance-swap study found shorting after big VIX increases only slightly beats shorting unconditionally, and the improvement was not significant. The VRP can turn negative inside crises (2008, March 2020).
  - So: **sell after a spike once realized vol starts falling, not into a rising RV.**
- **Options use (gating rule):**
  - Sell premium only when `IV30 > RV10` and `RV10 < RV20`, meaning realized vol is rolling over.
  - Buy debit spreads instead when `IV30 < RV20`, meaning premium is cheap.

```python
def sig_vol(df, iv30=None):
    rv10, rv20 = realized_vol(df['close'], 10), realized_vol(df['close'], 20)
    out = pd.DataFrame({'rv10': rv10, 'rv20': rv20, 'atr14_pct': atr(df) / df['close']})
    if iv30 is not None:                       # iv30 in decimals, aligned index
        iv = iv30.reindex(df.index).ffill()
        out['vrp'] = iv - rv20
        out['sell_ok'] = (iv > rv10) & (rv10 < rv20)
    return out
```

### 4.2 Bollinger bandwidth squeeze and Keltner squeeze

- **Formulas:**
  - `BBW = (upper - lower) / mid`. Squeeze when BBW is at its lowest 10th percentile of the trailing 126 days.
  - Keltner squeeze (Carter's TTM squeeze): Bollinger(20,2) bands fully inside Keltner(20, EMA20 ± 1.5·ATR20).
- **Evidence:**
  - **C for direction:** no reliable evidence that the breakout direction after a squeeze is predictable.
  - **B for volatility expansion:** low realized vol mean-reverts upward, as volatility clustering and mean reversion in the HAR literature show.
  - IV is usually also low at those times, so buying the move can be priced fairly. The edge exists only if IV is cheap relative to the forecast RV.
- **Options use:** A squeeze plus IV30 in the bottom 20% of its 1-year range triggers **long premium**: buy a long straddle or strangle, or a calendar. A squeeze is also a weak "don't sell premium cheap" filter.

```python
def sig_squeeze(df, n=20, k_bb=2.0, k_kc=1.5, lookback=126):
    c = df['close']; m = sma(c, n); sd = c.rolling(n).std(ddof=0)
    bb_up, bb_lo = m + k_bb * sd, m - k_bb * sd
    e = ema(c, n); a = atr(df, n)
    kc_up, kc_lo = e + k_kc * a, e - k_kc * a
    bbw = (bb_up - bb_lo) / m
    return pd.DataFrame({
        'bbw': bbw,
        'bbw_pctile': bbw.rolling(lookback).rank(pct=True),
        'ttm_squeeze': (bb_up < kc_up) & (bb_lo > kc_lo),
    })
```

### 4.3 NR7 (narrowest range of the last 7 days, Crabel)

- **Formula:** `NR7 = (H - L) == min over 7 days of (H - L)`. Inside-day combination (NR7 plus inside bar) is the stronger version.
- **Evidence: C.**
  - Crabel (1990) and later practitioner tests show range expansion follows NR7 days, which is consistent with volatility mean reversion.
  - No academic support for direction.
- **Options use:**
  - Do not open short-premium positions with very short DTE (0 to 2 DTE) on NR7/inside days.
  - Optionally buy a 1 to 5 DTE strangle when IV is low. Expect this to be marginal after costs.

```python
def sig_nr7(df):
    rng = df['high'] - df['low']
    nr7 = rng == rng.rolling(7).min()
    inside = (df['high'] <= df['high'].shift(1)) & (df['low'] >= df['low'].shift(1))
    return pd.DataFrame({'nr7': nr7, 'nr7_inside': nr7 & inside})
```

---

## 5. Breadth and market regime

### 5.1 SPY above 200DMA (index-level regime)

This is the signal from section 2.1 applied to SPY and used as a **global switch for every ticker**. Single-stock short puts in a sub-200DMA SPY regime are hit by correlated drawdowns: correlations rise toward 1 in selloffs. Grade B as a risk filter.

### 5.2 Percentage of Nasdaq-100 (or of the 9-name universe) above the 50DMA

- **Computability:**
  - The true Nasdaq-100 measure needs about 100 tickers with survivorship-correct membership, which is expensive. Alpaca bars can supply the prices, but historical membership has to come from somewhere else.
  - A cheap proxy is the 9-name universe or a broader list of about 30 to 50 mega caps. **Survivorship-bias warning:** today's mega caps were selected because they went up.
- **Evidence: C.**
  - Breadth thrusts, such as the Zweig thrust from below 40% to above 61.5% advancing in 10 days, have strong forward returns in tiny samples (about 15 to 20 events since 1945).
  - Low breadth (< 20% above the 50DMA) inside a long-term uptrend acts as a dip signal, like RSI(2) at the index level.
  - Divergence (index at highs, breadth falling) is folklore-grade as a timing signal.
- **Options use:**
  - `pct_above50 < 20%` and SPY above its 200DMA is an index-level oversold reading. It is a trigger for 30 to 45 DTE SPY put credit spreads.
  - `pct_above50 > 90%` is a weak "reduce new short calls" signal. Do not short calls on strength alone, since momentum persists.

```python
def breadth_pct_above(closes: pd.DataFrame, n=50):
    """closes: wide DataFrame, one column per ticker."""
    ma = closes.rolling(n, min_periods=n).mean()
    valid = ma.notna()
    return ((closes > ma) & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)
```

---

## 6. Seasonality / calendar

### 6.1 Turn of month (TOM)

- **Window:** Trading days -1 to +3 around month-end, from the last trading day of the month through the third trading day of the next.
- **Evidence: A for existence, B for current strength.**
  - Lakonishok & Smidt (1988, *RFS*): DJIA 1897 to 1986. The 4-day TOM window averages about 0.47%, versus about 0.06% for an average 4-day period.
  - McConnell & Xu (2008, *FAJ*): from 1926 to 2005, **investors earned no market risk premium outside TOM days on average.** The effect appears in 31 of 35 countries and is not confined to small caps.
  - Etula, Rinne, Suominen & Vaittinen (2020, *RFS*, "Dash for Cash") give a mechanism: institutional month-end liquidity needs push prices down around T-3/T-4 before month-end, then prices recover over TOM. The reversal is stronger where mutual-fund ownership is larger and has *grown* as fund ownership grew. A mechanism that strengthens over time is a better sign than most anomalies show.
  - Recent practitioner checks (CXO, Quantpedia) find that TOM on SPY still has positive excess returns after 2000 but at lower magnitude, noisy year to year.
- **Options use (C1):**
  - **Entry:** At the close of trading day -2, or the open of -1. Buy a 1 to 2% wide call debit spread, ATM/+2%, with the expiry nearest 7 to 10 calendar days.
  - **Exit:** Close of trading day +3.
  - **Alternative:** Sell a 7 to 10 DTE 20-delta put spread at day -2 and let theta plus drift work.
  - **Filters:** SPY above its 200DMA. Skip when an FOMC or CPI date falls inside the window, or test the event both ways.
  - Expect about 12 trades per year per ticker. Run it on SPY and QQQ only to avoid putting the same trade on in correlated tickers.

```python
def sig_turn_of_month(idx: pd.DatetimeIndex, before=1, after=3):
    s = pd.Series(idx, index=idx)
    ym = idx.to_period('M')
    pos = s.groupby(ym).cumcount() + 1                         # 1 = first day of month
    neg = s.groupby(ym).cumcount(ascending=False) + 1          # 1 = last day of month
    tom = (neg <= before) | (pos <= after)
    return pd.DataFrame({'tom': tom, 'tdom': pos, 'tdom_from_end': neg})
```

### 6.2 Pre-holiday

- **Evidence: A historically, B or C now.**
  - Ariel (1990, *J. Finance*) found pre-holiday returns about 9 to 14 times the average day's return from 1963 to 1982.
  - Lakonishok & Smidt (1988) found about 0.22% versus about 0.01% on normal days.
  - Later studies show the effect has weakened in US large caps since about 1990 but is still positive on average. It is strongest before July 4, Thanksgiving, and Christmas/New Year.
- **Options use:**
  - Too small for debit options: about +0.1 to 0.2% on one day, against theta and spread.
  - Use it only as a **don't-fight-the-calendar filter**. Do not open new short calls or bearish trades the day before a holiday.
  - Short puts held over a holiday collect the extra calendar-day theta. Weekend and holiday theta is mostly priced in, so the edge is small.

```python
def sig_pre_holiday(idx: pd.DatetimeIndex):
    # A trading day is pre-holiday if the next trading day is > 1 business day later
    nxt = pd.Series(idx[1:].append(pd.DatetimeIndex([pd.NaT])), index=idx)
    gap_bd = pd.Series([np.busday_count(a.date(), b.date()) if pd.notna(b) else 1
                        for a, b in zip(idx, nxt)], index=idx)
    return (gap_bd > 1).rename('pre_holiday')   # weekends excluded by busday_count
```

### 6.3 Overnight vs intraday returns

- **Evidence: A.**
  - Cliff, Cooper & Gulen (2008): from 1993 to 2006, essentially all of the US equity premium was earned overnight (close to open). Intraday returns were about zero or negative.
  - Lou, Polk & Skouras (2019, *JFE*, "A Tug of War"): momentum profits are earned entirely overnight and reversal profits entirely intraday, for 14 strategies. The pattern is stronger for large caps.
  - Boyarchenko, Larsen & Whelan (NY Fed, 2023, "The Overnight Drift") concentrate the drift around the European open and link it to dealer inventory.
- **Options use:**
  - This matters most for **timing fills**. Enter bullish structures near the close and exit bullish trades near or after the open, rather than the reverse.
  - Do not try to trade the overnight premium with long calls. Overnight theta is charged in full while the expected overnight move is a few basis points.
  - Short puts opened at 15:45 and held overnight align with the drift.
  - When backtesting, **compute fills at the actual time of day.** Daily-close backtests silently assign overnight returns to signals that would really be executed intraday.

```python
def split_returns(df):
    on = df['open'] / df['close'].shift(1) - 1      # overnight
    id_ = df['close'] / df['open'] - 1              # intraday
    return pd.DataFrame({'ret_overnight': on, 'ret_intraday': id_})
```

### 6.4 Sell in May / Halloween

- **Evidence: B.**
  - Bouman & Jacobsen (2002, *AER*) found the effect in 36 of 37 markets.
  - Jacobsen & Zhang found November to April returns are about 4.5 percentage points higher than May to October (t ≈ 9.7) across 108 markets and about 319 years. The gap is about 6.25 pp over the last 50 years.
  - On the S&P 500, May to October returns are still positive on average, just lower. As a timing rule it trades very rarely (one round trip per year). Statistical power for a single index is weak, and the UK early sample contains very few stocks.
- **Options use:**
  - As a weak tilt only. During May to October, use smaller short-put deltas (for example 16 delta instead of 25) and slightly fewer dip entries.
  - The cost of being wrong is small because this only changes parameters.

```python
def sig_halloween(idx):
    return pd.Series(idx.month.isin([11, 12, 1, 2, 3, 4]), index=idx, name='winter')
```

### 6.5 Pre-FOMC drift (related; listed for completeness)

- Lucca & Moench (2015, *J. Finance*): S&P 500 rose about 49 bp in the 24 hours before FOMC announcements from 1994 to 2011.
- Kurov, Wolfe & Gilbert (2021, *FRL*): the drift "essentially disappeared after 2015."
- Later work says it persists in high-uncertainty periods: about 109 bp when IV is above its median versus about 10 bp below it.
- **Grade B, decayed.** Use only conditionally (VIX above its 1-year median) if at all.

---

## 7. Relative-strength rotation among mega caps

- **Formula:**
  - `RS_k = close_t / close_{t-21k} - 1`, with `k ∈ {3, 6, 12}`
  - Skip the most recent month: `close_{t-21} / close_{t-252} - 1`, the classic 12-1 measure
  - Rank the 6 single names (AAPL, MSFT, NVDA, AMZN, GOOGL, META) monthly. Hold the top 1 or 2 if absolute momentum is positive (Antonacci's dual momentum).
- **Evidence:**
  - **A for cross-sectional momentum in large, diversified universes.** Jegadeesh & Titman (1993); Asness, Moskowitz & Pedersen (2013, *J. Finance*), "Value and momentum everywhere".
  - **C for a 6-stock universe.** Ranking only 6 highly correlated stocks gives very little breadth, and dispersion is dominated by idiosyncratic events like earnings and AI-capex news. Results will depend heavily on the start date: NVDA from 2023 to 2024 drives any backtest.
  - **Momentum crashes:** Daniel & Moskowitz (2016, *JFE*) show momentum crashes sharply after bear-market rebounds, for example in 2009.
  - Lou, Polk & Skouras (2019) show momentum profits come overnight, so hold through closes.
- **Options use:**
  - Use rank to **allocate which name gets the bullish structure.** Run R1/R1b dips only on the top-2 RS names, and covered calls or short calls only on the bottom-ranked names.
  - Do not run a standalone long-call rotation. Monthly long calls pay the full volatility premium every month.

```python
def rs_rank(closes: pd.DataFrame, lookback=252, skip=21):
    mom = closes.shift(skip) / closes.shift(lookback) - 1
    month_end = closes.groupby(closes.index.to_period('M')).tail(1).index
    rank = mom.loc[month_end].rank(axis=1, ascending=False)
    return rank.reindex(closes.index).ffill(), mom
```

---

## 8. Covered calls only when RSI > 70? (sanity check on the proposed rule)

- **Evidence against the premise: B.**
  - At the index level, high RSI(14) or high 3 to 12 month momentum predicts *continuation* on average (TSMOM), not reversal.
  - Only extreme short-term overbought readings show mild next-few-day reversal: RSI(2) > 95 or IBS > 0.8 on several consecutive days. That is consistent with negative daily autocorrelation, but the effect is smaller than on the oversold side, because the equity drift works against it.
  - Israelov & Nielsen (2015, *FAJ*, "Covered Calls Uncovered") decompose covered-call returns into equity exposure, short volatility, and a negative "equity timing" component. Mechanically overwriting gives up upside convexity in strong rallies.
- **Recommendation:** Do not gate covered calls on RSI(14) > 70.
  - If overwriting, gate on **IV rank** (sell calls when IV30 is in the top 50% of its 1-year range) and **short-term overbought** (RSI(2) > 90 *and* `z_atr > +2`).
  - Use 30 to 45 DTE, 20 to 30 delta, and roll up when tested in an uptrend (TSMOM > 0).
  - Expect the improvement over unconditional overwriting to be small. Validate it before relying on it.

---

## 9. Ranked signal and options combinations (what to backtest first)

These are ranked by **evidence quality** for the combined (signal plus options) trade, not by backtested return. Anything ranked here still needs your own out-of-sample test on real chain data.

| # | Combination | Signal grade | Options-leg logic grade | Notes |
|---|---|---|---|---|
| 1 | **Regime-filtered VRP harvest:** sell 30 to 45 DTE, 16 to 25 delta SPY/QQQ put credit spreads. Only when `SPY > SMA200` and `IV30 > RV10` and RV10 < RV20. Block when the ADX/downtrend filter fires. | VRP A, regime filter B | A (PUT index, Carr–Wu) | The core book. Technical signals here mainly avoid the worst periods. |
| 2 | **Mean-reversion dip → put credit spread (R1b):** RSI(2) < 10 (or IBS < 0.2 combined with RSI(2) < 10) with close above SMA200 on SPY/QQQ. Sell a 20 to 30 delta, 7 to 21 DTE put spread. Exit at 50% of credit, on `close > SMA5`, or at 5 days. | B/C (structural via Baltussen et al.) | B (dip + elevated IV) | Best expected fit of signal and structure. |
| 3 | **Turn-of-month (C1):** at trading day -2, buy a 7 to 10 DTE ATM/+2% SPY call spread or sell a 20-delta put spread. Exit at day +3. SPY above SMA200. | A existence (McConnell–Xu, Etula et al.), B current magnitude | B | Independent of #2, so it diversifies. About 12 trades per year. |
| 4 | **RSI(2) dip → call debit spread (R1):** same trigger as #2. Buy a 50/25 delta, about 21 DTE call spread. Exit on `close > SMA5`, 5 days, +60% of max, or -50%. Mega caps: only the top-2 relative-strength names, no earnings in the window. | B/C | C (delta only, theta-neutral-ish) | Use when IV is *not* elevated, where #2 pays too little. |
| 5 | **Volatility-gated structure selection:** if `IV30 < RV20`, or a TTM squeeze with IV rank below 20%, buy premium (debit spreads or strangles). Otherwise sell premium. | A for vol forecasting, B for IV vs RV sorting (Goyal–Saretto) | B | A meta-rule that improves every other combination. |
| 6 | **Downtrend short-premium kill-switch:** no new short puts when `close < SMA200`, ADX > 25 and rising, -DI > +DI. Halve the size of everything when SPY < SMA200. | B as a vol/regime filter; ADX itself D | B (tail-risk reduction) | Judge by drawdown and CVaR, not mean return. |
| 7 | **Relative-strength allocation:** route bullish structures to the top-2 12-1 momentum mega caps and overwriting to the bottom-2. | A in general, C in a 6-name universe | C | Mostly a routing rule. Watch for momentum crashes after bear rebounds. |
| 8 | **Overbought overwrite:** covered calls or call credit spreads only when RSI(2) > 90, `z_atr > 2`, and IV rank above 50%, on 30 to 45 DTE 20 to 30 delta calls. Avoid when TSMOM_3 and TSMOM_12 are both strongly positive. | C | B (VRP on calls is smaller than on puts) | Replaces the "covered calls when RSI > 70" idea, which the evidence does not support. |

**Ideas tested and not recommended:**
- EMA crossover entries (D)
- Bollinger %B as a separate confirmation (redundant with RSI(2))
- NR7 directional trades
- Pre-holiday debit options (edge smaller than costs)
- Sell-in-May as an on/off switch (use only as a parameter tilt)
- Pre-FOMC drift after 2015, unless conditioned on high IV

---

## 10. Backtest protocol for the options legs

1. **Data.**
   - Alpaca historical options bars only start around February 2024, which is too short to validate anything.
   - For 2005 to present, use one of:
     - (a) a real EOD chain vendor (ORATS, CBOE DataShop, OptionMetrics/IvyDB via WRDS, Polygon/Massive), or
     - (b) a **synthetic chain**: Black-Scholes (`optionsbot/pricing.py`) with IV from VIX/VXN/RVX for the ETFs, plus a skew adjustment. For example, put IV = ATM IV × (1 + 0.8·(moneyness shortfall)), calibrated on the 2024+ Alpaca data.
   - Synthetic results overstate the edge in crashes because skew steepens. Treat them as an upper bound.
2. **Fills.**
   - Fill at mid ± 50% of the quoted half-spread at minimum.
   - Add a $0.65 per contract commission.
   - Single-name options on NVDA and META have wider spreads than SPY. Model them per ticker.
3. **Timing.**
   - Signal at the close of day t, using data up to t.
   - Fill at the next open, or at a 15:45 snapshot for close-of-day entries. Never fill at the close that generated the signal.
4. **Out-of-sample split.**
   - Freeze parameters using data up to 2014 at the latest.
   - Report 2015 to 2026 separately. Also report 2008, 2018 Q4, 2020 Q1 and 2022 as stress windows.
5. **Report.**
   - Trade count, win rate, mean P&L per unit of risk (credit spreads: per unit of max loss), CVaR 5%, max drawdown, and P&L excluding the single best year.
   - Also report the **underlying-only version** of each signal, so you can tell whether the options structure adds or destroys value.
6. **Multiple testing.**
   - About 8 combinations × 9 tickers × parameter grids means 1,000+ tests.
   - Use the Deflated Sharpe Ratio (Bailey & López de Prado 2014) or White's Reality Check (as in Sullivan et al. 1999) before believing anything with t < 3.

---

## Sources

- Baltussen, van Bekkum & Da (2019), Indexing and stock market serial dependence around the world, JFE — summary via search; https://www3.nd.edu/~zda/Indexing.pdf
- McConnell & Xu (2008), Equity Returns at the Turn of the Month, FAJ — https://rpc.cfainstitute.org/research/financial-analysts-journal/2008/equity-returns-at-the-turn-of-the-month
- Etula, Rinne, Suominen & Vaittinen (2020), Dash for Cash, RFS — https://academic.oup.com/rfs/article/33/1/75/5494694 ; WP: https://orbilu.uni.lu/bitstream/10993/21145/1/Dash4Cash.pdf
- Lou, Polk & Skouras (2019), A Tug of War, JFE — https://personal.lse.ac.uk/polk/research/TugOfWar.pdf
- Jacobsen & Zhang, The Halloween Indicator everywhere and all the time — https://www.cxoadvisory.com/calendar-effects/three-centuries-of-calendar-effects
- Zakamulin, A Comprehensive Look at the Empirical Performance of MA Trading Strategies — https://papers.ssrn.com/abstract=2677212
- Hurst, Ooi & Pedersen (2017), A Century of Evidence on Trend-Following — https://www.TrendFollowing.com/whitepaper/Century_Evidence_Trend_Following.pdf
- Bondarenko, An Analysis of Index Option Writing (PUT/WPUT) — https://www.indexologyblog.com/2016/02/18/paper-by-professor-bondarenko-has-intriguing-new-analysis-of-put-and-wput-indexes/ ; https://www.cxoadvisory.com/equity-options/performance-of-cboe-putwrite-indexes
- Goyal & Saretto (2009), Cross-section of option returns and volatility, JFE — https://docs.lib.purdue.edu/ciberwp/55
- Kurov, Wolfe & Gilbert (2021), The Disappearing Pre-FOMC Announcement Drift, FRL — https://www.skidmore.edu/economics/documents/KurovWolfeGilbert-TheDisappearingPre-FOMC-Announce-Drift-200914.pdf ; conditional drift: https://quantpedia.com/fomc-equity-drift-occurs-in-periods-of-high-uncertainty/
- Pandey & Joshi (2023), IBS on country ETFs (builds on Pagonidis) — https://arxiv.org/abs/2306.12434v1
- Variance-swap timing after VIX moves — https://arxiv.org/pdf/physics/0608242 (the result as summarized in search; verify before relying on it)
- Practitioner (grade C): Connors & Alvarez (2008); Quantified Strategies RSI/IBS posts (https://quantifiedstrategies.substack.com/p/larry-connors-r3-strategy-it-still-795 ; https://quantifiedstrategies.substack.com/p/ibs-internal-bar-strength-trading); CXO TOM page (https://www.cxoadvisory.com/calendar-effects/the-turn-of-the-month-effect/)
- Cited from standard literature, not re-fetched in this session (verify exact figures): Brock, Lakonishok & LeBaron (1992); Sullivan, Timmermann & White (1999); Moskowitz, Ooi & Pedersen (2012); Faber (2007); Lakonishok & Smidt (1988); Ariel (1990); Cliff, Cooper & Gulen (2008); Bouman & Jacobsen (2002); Lucca & Moench (2015); McLean & Pontiff (2016); Carr & Wu (2009); Moreira & Muir (2017); Corsi (2009); Daniel & Moskowitz (2016); Asness, Moskowitz & Pedersen (2013); Israelov & Nielsen (2015); Lento, Gradojevic & Wright (2007); Boyarchenko, Larsen & Whelan (2023); Bailey & López de Prado (2014).
