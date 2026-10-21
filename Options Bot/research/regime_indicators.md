# Regime Indicators for Selling (or Buying) Options Premium on Large Caps / Index ETFs

Researched 2026-10-21. Scope: SPY/SPX, QQQ, mega-cap single names. Research only, no code was changed.

**How to read this.** Each indicator gets a definition and formula, the data source it needs (Alpaca or free), what the evidence says (with numbers), and a concrete rule to test. Two kinds of evidence appear. (a) Published academic or practitioner results, cited in the Sources list. (b) A quick in-house check I ran on the free CBOE CSVs (marked **[in-house]**). Those checks are in-sample and use overlapping 21-day windows; treat them as sanity checks, not proof. They are reproducible with the URLs in section 0.

---

## 0. Data sources (verified working on 2026-10-21)

### Free daily CSVs (CBOE, no auth; `curl` works)
Format is `DATE,OPEN,HIGH,LOW,CLOSE` with dates as `MM/DD/YYYY`, except where noted.

| Series | URL | Starts | Notes |
|---|---|---|---|
| VIX (30d SPX IV) | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv | 1990-01-02 | OHLC |
| VIX3M (93d) | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv | 2009-09-18 | OHLC |
| VIX6M | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX6M_History.csv | 2008-01-02 | OHLC |
| VIX9D | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv | 2011-01-04 | OHLC |
| VIX1D | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX1D_History.csv | 2022-05-13 | OHLC; noisy, intraday-reset |
| VVIX | https://cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv | 2006-03-06 | `DATE,VVIX` only. **Contains bad prints** (e.g. 2006-03-15 = 15.71). Filter out values < 50. |
| SKEW | https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv | 1990-01-02 | `DATE,SKEW` |
| SPX close | https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv | 1975-01-02 | close only |
| PUT (ATM SPX put-write) | https://cdn.cboe.com/api/global/us_indices/daily_prices/PUT_History.csv | daily from 2007 | only a few scattered points before 2007 |
| WPUT (weekly put-write) | https://cdn.cboe.com/api/global/us_indices/daily_prices/WPUT_History.csv | 2006-01-31 | |
| BXM (buy-write) | https://cdn.cboe.com/api/global/us_indices/daily_prices/BXM_History.csv | 2002-03-22 | |
| CNDR (iron condor) | https://cdn.cboe.com/api/global/us_indices/daily_prices/CNDR_History.csv | 1986 | |
| BFLY (iron butterfly) | https://cdn.cboe.com/api/global/us_indices/daily_prices/BFLY_History.csv | 1986 | |
| PPUT (protective put) | https://cdn.cboe.com/api/global/us_indices/daily_prices/PPUT_History.csv | 1986 | |
| FRED VIXCLS | https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS | 1990-01-02 | `observation_date,VIXCLS`; lags CBOE by about 1 day; `.` = missing |

The pattern is `https://cdn.cboe.com/api/global/us_indices/daily_prices/<SYMBOL>_History.csv`. `VXST` is retired (AccessDenied); use VIX9D.

The CBOE strategy indices (PUT, BXM, CNDR, ...) are the cheapest way to backtest *regime filters* over 2007 to 2026 without option-chain data: apply the filter as a "hold index vs hold cash" switch.

### Alpaca
- Daily stock bars (SPY, QQQ, single names): long history. These give OHLC, which is needed for the Parkinson, Garman-Klass and Yang-Zhang estimators and for trend filters.
- Option bars: `GET https://data.alpaca.markets/v1beta1/options/bars`. **History only from February 2024.** The free tier is the "indicative" feed; OPRA requires a subscription. Historical option *quotes* are reportedly unreliable for expired contracts (forum report), so mark-to-market from bars (trade-based closes) will be noisy for illiquid strikes.
- Contracts list: `GET /v2/options/contracts` (strike, expiry, type; includes expired contracts with `status=inactive`).
- Implication: on Alpaca you can compute **per-name IV only from 2024-02 onward**, by back-solving Black-Scholes from option bar closes. That is about 2.7 years, which covers one mild correction (Aug 2024) and one sharp one (Apr 2025). That is far too short to validate a regime filter on its own. **Validate the regime logic on the CBOE index data (1990 to 2026, or 2007 to 2026 for PUT), then use the Alpaca window only to check execution and fills on the actual instrument.**

---

## 1. Volatility risk premium (VRP): the reason premium selling works at all

**Definition.** VRP = implied vol minus subsequently realized vol, over the same horizon. In variance terms, `VRP_t = IV_t^2 - RV_{t,t+h}^2`. This is the variance-swap payoff a seller earns (Carr & Wu 2009).

**Evidence.**
- Carr & Wu (2009, RFS): variance risk premia are strongly negative for index options (sellers earn), and the premium is economically large. Bollerslev, Tauchen & Zhou (2009, RFS): the *ex-ante* VRP (IV^2 minus expected RV^2) predicts equity returns at a 1 to 4 month horizon.
- CBOE / Bondarenko (1990 to 2018): average VIX 19.3% vs average SPX realized 15.1%.
- **[in-house]** 1990-01 to 2026-09, VIX vs the next 21 trading days of SPX close-to-close realized vol:
  - VIX > forward RV on **85.9%** of days. Mean spread +4.10 vol points, median +4.71. Mean VIX^2 - RV^2 = +113 (variance points).
  - Non-overlapping (every 21st day, n=441): **84.6%**, mean +3.98. By decade: 1990s 90.1%; 2000s 81.5%; 2010s 84.2%; 2020 to 2026 81.5%.
  - The left tail is the problem. The 5th percentile spread is -4.9, the 1st percentile -19.6, and the worst is **-72 vol points (entry 2020-02-20)**. The premium is a small, frequent gain paid for by a rare, huge loss.
- Single stocks: the VRP is much smaller and less reliable. Driessen, Maenhout & Vilkov (2009, JF) attribute most of the index VRP to a **correlation risk premium**. The working-paper version reports no significant VRP in individual options. Bakshi & Kapadia (2003) find individual-stock vol premia far smaller than the index. di Pietro & Vainberg (2006): index options are systematically richer than single-stock options (a dispersion trade had Sharpe 0.63/month after costs). I found no clean published "% of time IV > RV" figure for single stocks. Treat **"IV > RV ~85% of the time" as an index fact, not a single-name fact.** Earnings gaps make single names worse (see section 11).
- Israelov & Nielsen (2015, FAJ; AQR): decomposing covered calls, the short-vol leg had a Sharpe ratio near 1.0 but supplied only about 10% of the risk. Equity beta drove most of the P&L. **A put-selling backtest is mostly a levered beta bet unless it is delta-hedged.**
- PUT index: July 1986 to August 2023, annualized +9.40% vs S&P 500 +9.91%, with standard deviation 10.26% vs 15.38% (Cboe). **[in-house]** 2008-01 to 2026-10: PUT CAGR 7.22%, vol 13.9%, max drawdown -37.1%; SPX price-only CAGR 9.32%, vol 19.8%, max drawdown -53.3%.

**Rule (baseline, everything else is compared to it).** Sell a 30 to 45 DTE SPY put (or put spread) monthly, unconditionally. Every filter below must beat this on Sharpe, drawdown and CVaR, not on win rate.

---

## 2. IV - RV spread (realized-vol-conditioned VRP)

**Definition.** `Spread_t = IV_t - RV_{t-h,t}`, using trailing RV because forward RV is unknown at entry. For SPX use VIX - RV21 (close-to-close, annualized). For an ETF or stock on Alpaca: ATM 30-day IV interpolated from option bars, minus trailing RV20 from stock bars.

**Evidence.**
- Goyal & Saretto (2009, JFE): sorting stocks on (historical RV - ATM IV), long the straddles of stocks where RV >> IV and short those where IV >> RV earns economically and statistically large monthly returns, robust to risk factors. This is the single best-documented cross-sectional option signal. In practice: **sell where IV is rich relative to trailing RV, buy where IV is cheap.**
- **[in-house], SPX, VIX - RV21 quintiles, PUT-index forward 21d return (2008 to 2026):**

  | VIX - RV21 quintile | mean fwd PUT 21d | % negative | 5th pct |
  |---|---|---|---|
  | Q1 (< 0.9, IV cheap) | +0.32% | 28.3% | -6.5% |
  | Q2 | +0.34% | 24.9% | -4.5% |
  | Q3 | +0.49% | 26.7% | -3.5% |
  | Q4 | +0.53% | 28.4% | -5.0% |
  | Q5 (> 6.9, IV rich) | **+1.52%** | **19.6%** | -3.9% |

- **[in-house]** Switch: hold PUT only when yesterday's VIX > RV21, else cash. CAGR **9.45% vs 7.22%** buy-and-hold, vol 11.6% vs 13.9%, max drawdown -18.3% vs -37.1%, invested 84% of days. This was the best single filter tested.

**Rule.** Sell 30 to 45 DTE puts or put spreads only when `IV30 - RV20 > 0` (VIX - RV21 for SPY). Size up when the spread is in the top quintile (> about 7 vol points). **Buy** premium (long straddle or calendar) when `IV30 - RV20 < -3` on liquid names (Goyal-Saretto direction).

---

## 3. IV Rank / IV Percentile

**Definition.**
- `IVR = (IV_t - min_252(IV)) / (max_252(IV) - min_252(IV)) * 100`
- `IVP = % of the last 252 days with IV < IV_t`

For SPY use VIX as the IV proxy. For single names, use Alpaca-derived IV (one year of lookback needs data from 2025-02 onward).

**Evidence.**
- Practitioner only (tastytrade/tastylive). 45 DTE 30-delta SPY strangles entered at IVR > 50 vs IVR 30 to 50: about 10% more total P&L on about 10% fewer trades, credit about 20 to 23% higher, win rate slightly *lower*. These are not peer-reviewed, have no drawdown or CVaR reported, and use small samples (73 high-IVR entries out of 314 in a five-symbol study).
- **[in-house], VIX IV-rank vs PUT forward 21d:**

  | IVR bucket | VIX > fwd RV | mean fwd PUT 21d | 5th pct |
  |---|---|---|---|
  | 0 to 20 | 87.4% | +0.67% | -3.0% |
  | 20 to 40 | 82.3% | +0.78% | -5.4% |
  | 40 to 60 | 76.6% | +0.63% | -7.8% |
  | 60 to 80 | 75.4% | +0.74% | -8.3% |
  | **80 to 100** | **70.2%** | **-0.78%** | **-20.3%** |

  **High IVR is not a free lunch.** The top bucket is where crashes are still unfolding. IVR measures how *high* IV is, not how *rich* it is relative to what is about to be realized. The extra credit at high IVR is real, but so is the fatter left tail.

**Rule to test.** IVR in 30 to 70 **and** term structure in contango (section 5). Do **not** test "sell when IVR > 50" alone; expect it to underperform on CVaR. IVR is min/max based, so a single spike sets the range for a year and the indicator is distorted. Prefer IVP, or a z-score of IV vs a 252-day mean.

---

## 4. VIX level buckets

**Definition.** The raw VIX close (or VIXCLS). Common buckets: < 12, 12 to 15, 15 to 20, 20 to 25, 25 to 30, 30 to 40, > 40.

**Evidence. [in-house], 1990 to 2026, spread = VIX - fwd RV21 (PUT columns 2008+):**

| VIX | n days | mean fwd RV | mean spread | % IV > RV | 5th pct spread | % PUT 21d < 0 |
|---|---|---|---|---|---|---|
| ≤ 12 | 809 | 8.6 | +2.5 | 89% | -3.2 | 11.6% |
| 12 to 15 | 2155 | 10.5 | +2.9 | 85% | -3.0 | 14.2% |
| 15 to 20 | 2859 | 13.2 | +4.1 | 87% | -3.5 | 15.6% |
| 20 to 25 | 1823 | 17.7 | +4.5 | 84% | -7.0 | 12.2% |
| 25 to 30 | 863 | 21.7 | +5.5 | 84% | -5.0 | 9.3% |
| 30 to 40 | 527 | 26.9 | +6.6 | 89% | -9.8 | 11.6% |
| > 40 | 207 | 44.5 | +5.8 | 78% | **-26.6** | **30%** |

- The absolute premium (vol points) **rises with VIX**, and so does the tail. The percentage premium (spread / VIX) is roughly flat at 20 to 25%. VIX > 40 is the only bucket where the hit rate clearly drops.
- **[in-house]** Switch on PUT: "VIX < 30 only" cut CAGR to 5.75% (it misses the rich post-crash premium). "VIX > 20 only" gave CAGR 4.40% with max drawdown -35.5%. **A level filter alone is a poor regime filter.** Level should drive *sizing and strike distance*, not on/off.

**Rule.** Hold delta constant (e.g. 16 to 20 delta), which automatically widens strikes when VIX is high. Cut notional by `VIX_ref / VIX` (vol targeting). Halt new sales above VIX 40 unless the term structure is back in contango.

---

## 5. VIX term structure: VIX/VIX3M and VIX9D/VIX (the strongest timing signal)

**Definition.**
- `TS = VIX / VIX3M`. Values < 1 mean contango (normal); > 1 mean backwardation (stress).
- Short end: `VIX9D / VIX`.
- Alternatives: VIX/VIX6M, or the VX1/VX2 futures ratio (futures data is not free).

**Evidence.**
- Johnson (2017, JFQA): the slope (2nd principal component) of the VIX term structure predicts excess returns of variance swaps, VIX futures and SPX straddles at all maturities, incremental to other VRP proxies. A low or inverted slope predicts **higher** variance-asset returns (bad for sellers). CXO's summary of 1996 to 2013: next-day return spreads between extreme slope quintiles of 0.29 to 1.81%.
- Simon & Campasano (2014) and the VIX futures literature: the futures basis predicts VIX futures roll returns. Contango is the rolling short-vol carry.
- **[in-house], VIX/VIX3M buckets (2009-09 to 2026):**

  | VIX/VIX3M | % of days | mean VIX | mean spread | % IV > RV | 5th pct spread | PUT 21d 5th pct |
  |---|---|---|---|---|---|---|
  | ≤ 0.85 | 28.7% | 14.6 | +3.3 | 84.5% | -4.9 | -3.1% |
  | 0.85 to 0.90 | 30.2% | 16.8 | +4.1 | 86.4% | -4.3 | -3.7% |
  | 0.90 to 0.95 | 21.8% | 19.3 | +3.7 | 85.2% | -5.6 | -4.0% |
  | 0.95 to 1.00 | 11.7% | 23.1 | +4.2 | 82.2% | -8.2 | -5.7% |
  | 1.00 to 1.05 | 4.9% | 27.9 | +6.3 | 86.5% | -8.5 | -4.4% |
  | 1.05 to 1.10 | 1.1% | 30.6 | +6.3 | 80.9% | -16.9 | -5.5% |
  | **> 1.10** | 1.6% | 39.7 | **-2.7** | **72.5%** | **-60.9** | **-18.0%** |

  Deep backwardation (> 1.10) is the **only** bucket where the average VRP is negative. Mild backwardation (1.0 to 1.1) still pays well, because premium is rich just after spikes. The signal mostly flags the deep end.
- **[in-house], VIX9D/VIX:** above 1.2 (n=64), mean spread -3.7, IV > RV only 65.6%, PUT 21d 5th percentile -17.2%. Below 1.0 it is normal. VIX9D/VIX > 1.1 to 1.2 is an earlier, short-dated warning of the same thing.
- **[in-house]** PUT switch tests, 2008 to 2026 (cash earns 0, signal lagged one day):

  | Filter | CAGR | Vol | Sharpe (rf=0) | Max DD | % invested |
  |---|---|---|---|---|---|
  | Buy & hold PUT | 7.22% | 13.9% | 0.52 | -37.1% | 100% |
  | VIX/VIX3M < 1.0 | 7.17% | 7.6% | 0.95 | -18.0% | 84% |
  | VIX/VIX3M < 0.95 | 5.19% | 6.0% | 0.86 | -14.2% | 73% |
  | VIX9D/VIX < 1.0 | 4.37% | 5.5% | 0.79 | -11.2% | 62% |
  | **VIX/VIX3M < 1 and VIX > RV21** | 7.08% | 7.0% | **1.01** | -13.2% | 73% |
  | VIX > RV21 | **9.45%** | 11.6% | 0.81 | -18.3% | 84% |

  Caveat: toggling a monthly put-write *daily* is not tradable as-is. Exiting a short put mid-cycle costs the spread, and the whole PUT return (including the T-bill yield) is set to zero on cash days. The real test must use entry-only gating (filter at entry, hold to target or stop).

**Rule.** Open new short puts or put spreads only if `VIX/VIX3M < 1.0` (strict: `< 0.95`) **and** `VIX9D/VIX < 1.1`. If TS > 1.10 on an open position, close or hedge. After a spike, re-enable when TS crosses back below 1.0. This "sell the vol crush after the term structure normalizes" entry is the richest premium in the table.

---

## 6. VVIX (vol of VIX)

**Definition.** The 30-day implied vol of VIX options (CBOE). Fixed by `DATE,VVIX`.

**Evidence.**
- Huang, Schlag, Shaliastovich & Thimme (2019, JFQA): VVIX is only weakly related to VIX. Both VIX and VVIX **negatively predict delta-hedged option payoffs**; high VVIX means options are richer, so sellers earn more on average. Vol-of-vol risk is priced separately.
- **[in-house], 2007 to 2026, VVIX quintiles:** top quintile (> 106) had the highest mean spread (+5.2) and best mean PUT 21d (+1.1%), but a fatter 5th percentile (-5.9% vs -3.9%). Monotone in premium, monotone in tail. Same story as VIX level.

**Rule.** Use VVIX as a **sizing** input, not a gate: in the top VVIX quintile, prefer defined-risk structures (spreads, not naked). Test `VVIX > 120 and TS > 1` as a "no new risk" flag. Expect little incremental value over TS.

---

## 7. Put skew: 25-delta risk reversal and CBOE SKEW

**Definition.**
- `RR25 = IV(25Δ put) - IV(25Δ call)` at fixed tenor (30d). On Alpaca (2024+): interpolate IV across strikes from option bars, delta from Black-Scholes.
- CBOE SKEW = `100 - 10 * S`, where S is the risk-neutral skewness of 30d SPX returns. Typical range is 110 to 160; it read 160 on 2026-10-20.

**Evidence.**
- SKEW as a crash predictor is weak. Practitioner tests (r-bloggers/OSM 2019) show CBOE's own SKEW-implied tail probabilities overstate 2-sigma-plus moves with no stable clustering. SKEW's level has drifted up structurally since about 2013 (0DTE and tail-hedge demand).
- **[in-house], SKEW quintiles (1990 to 2026):** *high* SKEW (> 140) came with the **lowest** forward RV (12.6 vs 21.8 in the lowest quintile), the best SPX 21d 5th percentile (-5.6% vs -8.6%), and the best PUT 5th percentile (-3.5% vs -6.2%). High SKEW is a calm-market, complacency-of-spot, demand-for-wings reading, not a crash warning. Low SKEW tends to occur *during* selloffs, when ATM vol has caught up with the wings.
- Academic: Bakshi, Kapadia & Madan (2003) show steep index skew is mostly a risk-aversion premium, which is why OTM index puts are the most overpriced options (largest negative returns for buyers).

**Rule.** When skew is steep (RR25 in its top tercile vs 1 year, or SKEW > 140), favor structures that **sell the rich wing**: put spreads, or put ratio spreads with risk caps, over strangles or ATM straddles. Do **not** use SKEW as an on/off crash filter. If tested, expect no improvement.

---

## 8. Realized-vol estimators (choosing the "RV" in IV - RV)

All annualized by `sqrt(252)`, over a window of n days. The inputs are daily OHLC from Alpaca stock bars. Notation: `o = ln(O_t / C_{t-1})` (overnight), `c = ln(C_t / O_t)`, `u = ln(H_t / O_t)`, `d = ln(L_t / O_t)`.

| Estimator | Formula (daily variance) | Efficiency vs C2C | Notes |
|---|---|---|---|
| Close-to-close | `σ² = Σ r_t² / n` with `r = ln(C_t/C_{t-1})` (zero-mean is fine) | 1x | Matches how short options actually settle and are hedged daily |
| Parkinson (1980) | `σ² = (1/(4 n ln2)) Σ ln(H/L)²` | about 5x | Ignores gaps and drift; **understates** RV for gap-prone stocks |
| Garman-Klass (1980) | `0.5 ln(H/L)² - (2ln2 - 1) ln(C/O)²` | about 7x | Ignores overnight gaps |
| Rogers-Satchell | `u(u - c) + d(d - c)` | drift-robust | Ignores gaps |
| **Yang-Zhang (2000)** | `σ² = σ_o² + k σ_c² + (1 - k) σ_RS²`, with `k = 0.34 / (1.34 + (n+1)/(n-1))` | about 8x, handles gaps and drift | **Recommended** for single names with earnings and overnight gaps |

Evidence: the efficiency figures come from Yang & Zhang (2000, Journal of Business). For VRP *signals*, the estimator matters less than the window. RV5 or RV10 reacts faster to regime change than RV21. For P&L attribution, use close-to-close, because that is what a daily-hedged short option pays.

**Rule.** Compute `IV - RV_YZ(10)` and `IV - RV_C2C(21)`. Test both as the section 2 gate. Expect YZ(10) to exit faster in selloffs (fewer losers in the first leg down) and to whipsaw more.

---

## 9. Trend filters: 200-DMA, 50/200 cross

**Definition.** `SPX > SMA200(SPX)`. Golden cross: `SMA50 > SMA200`. Also distance: `(SPX / SMA200 - 1)`.

**Evidence.**
- **[in-house], 1991 to 2026:** below the 200-DMA, % IV > RV falls to 76.4% (from 84.8% above). The 5th percentile spread is -20.7 vs -5.3. PUT 21d % negative is 32.5% vs 23.5%, and the 5th percentile is -8.0% vs -3.4%. Mean PUT return is about the same (+0.60% vs +0.65%). **The trend filter does not raise average premium; it cuts the tail.**
- **[in-house]** PUT switch: `SPX > 200DMA` gave CAGR 5.76% (vs 7.22%), vol 7.1%, max drawdown -18.9%, Sharpe 0.81 vs 0.52. It is a whipsaw-prone exit: it misses the post-crash premium recovery, which is the richest selling window.
- Practitioners (Option Alpha's 45-DTE SPY put spreads gated on > 200DMA) report improved drawdowns, but publish no full tables. Faber-style trend filters on equities show the same pattern: lower drawdown, similar or lower CAGR.

**Rule.** `SPX > SMA200` **OR** `VIX/VIX3M < 0.95` as the gate. The OR lets you sell into a V-shaped recovery once term structure normalizes, even while price is still below the 200-DMA. Compare against AND. Below the 200-DMA, switch from naked puts to call-side or neutral iron condors, or skip. The 50/200 cross is slower and adds nothing beyond the 200-DMA in most tests; skip it, or test it only as a robustness check.

---

## 10. Drawdown / crash regimes

**Definition.** `DD_t = SPX_t / max(SPX_{≤t}) - 1`.

**Evidence. [in-house]:** DD ≤ -20% (bear market) had the highest mean spread (+5.0) and mean PUT 21d (+1.18%), but the worst 5th percentile (-9.2%). The -5% to -20% drawdown zone had the worst hit rate (about 31% of PUT 21d windows negative). This matches "premium is richest after the crash, deadliest during the slide." Drawdown alone cannot distinguish the slide from the recovery; term structure can.

**Rule.** If `DD < -10%` and `TS > 1`, no new short premium. If `DD < -10%` and TS has just crossed back below 0.95, resume with half size, wider strikes (10 to 12 delta) and defined risk.

---

## 11. Event calendars: earnings, FOMC, CPI

**Earnings (single names).**
- Gao, Xing & Zhang (2018, JFQA): ATM straddles bought 3 days before earnings and held through the announcement earn **+3.34%** on average (+2.3% for the 1-day pre-announcement window), even though single-stock straddles lose on average otherwise. **Implied event vol is on average *underpriced*** (stronger for small and high-vol names, weaker for mega caps). Dubinsky, Johannes, Kaeck & Seeger (2019, RFS) quantify the earnings jump premium embedded in IV.
- Implication: **selling through earnings is the documented loser.** The IV crush is real but on average does not compensate for the move. Mega caps are closer to fair.
- Rule: no short-premium position in a single name may span an earnings date. Exclude any expiry ≥ the next earnings date. Data: Alpaca has no earnings calendar; use the free Nasdaq or Yahoo earnings calendars, or the SEC EDGAR 8-K item 2.02 history. Optional long-side test: buy ATM straddles T-3 to T0 on liquid large caps where `IV_front / IV_back` is below its 1-year median.

**FOMC.**
- VIX falls about 3% on announcement days, gradually over about 45 minutes (AUT working paper). The fall concentrates on meetings with SEP projections. Pre-FOMC implied variance declines about 103 bps in high-vol regimes and about 0 in low-vol regimes (Quantpedia summary). The Lucca & Moench (2015, JF) pre-FOMC drift: about 49 bps of SPX gain in the 24h before announcements (1994 to 2011), weaker since publication.
- But OptionMetrics (2001 to 2017): delta-neutral ATM straddles held *through* FOMC earned **+1.7%**. Event vol is not obviously overpriced in options, even though VIX futures fall.
- Rule: open 30 to 45 DTE short premium the **day after** FOMC (collect the post-event IV reset without the jump), not the day before. Test "enter T+1 after FOMC / CPI" vs "enter on a fixed calendar day". Data: FOMC dates from federalreserve.gov/monetarypolicy/fomccalendars.htm; CPI dates from bls.gov/schedule/news_release/cpi.htm.

**CPI.** Since 2022, CPI days carry a bigger SPX implied move than many FOMCs (practitioner observation). I found no peer-reviewed premium estimate. Treat it like FOMC: avoid 0 to 7 DTE short premium spanning CPI.

---

## 12. Day-of-week, weekend theta, 0DTE

- Jones & Shemesh (2018, JF): option returns are significantly lower over **non-trading periods (weekends)**. The market under-accounts for the fact that variance does not accrue while closed. In equity options, Friday-to-Monday returns are about -0.62% per portfolio (working-paper figure), vs positive on other days. There is little effect in SPX index options. Muravyev & Ni (2020, JFE): option returns are negative overnight and positive intraday, so sellers earn overnight.
  - Rule: for single names, open short premium on **Thursday or Friday close** to own the weekend decay; for SPY the effect is small. Easy to test with Alpaca daily option bars (2024+).
- 0DTE: Dim, Eraker & Vilkov (2024 working paper, SSRN 4692190) find 0DTE options carry the **largest VRP** of any tenor. They find **no evidence** that 0DTE dealer gamma hedging amplifies market moves (the effect is about 0.1 SD, negligible). Bandi, Fusari & Renò (2023) also document rich 0DTE premia. Beckmeyer, Branger & Gayda (2023) show retail 0DTE buyers lose heavily, mostly to spreads.
  - Caveat for this bot: Alpaca *daily* bars cannot model 0DTE (intraday path, intraday stops, assignment). **Do not backtest 0DTE on daily data.**
- Expiry-week effects: WPUT (weekly put-write, from 2006) vs PUT (monthly). Cboe's 2016 study found weekly put-write had lower returns but also lower volatility than monthly. Test monthly vs weekly cadence on the index CSVs before choosing DTE.

---

## 13. Gamma exposure (GEX): claims vs evidence

**Definition (vendor convention, SqueezeMetrics/SpotGamma).** `GEX = Σ_strikes Γ_i * OI_i * 100 * S² * 0.01 * sign_i`, where sign = +1 for calls and -1 for puts. This *assumes* dealers are long calls and short puts. The "zero-gamma flip" is the spot level where GEX changes sign.

**Claims.** Positive GEX means dealers dampen moves (sell rallies, buy dips), so selling premium is safe. Negative GEX means dealers amplify moves.

**Evidence quality: mixed, mostly weak for a daily retail bot.**
- Supportive (mechanism exists): Baltussen, Da, Lammers & Martens (2021, JFE): when dealer and levered-ETF gamma is negative, intraday momentum in the last 30 minutes is stronger, across 60+ futures markets from 1974 to 2020. Ni, Pearson, Poteshman & White (2021, RFS): net-purchased option gamma has measurable mechanical effects on single-stock volatility (absolute returns). Barbon & Buraschi ("Gamma Fragility"): dealer gamma imbalances relate to intraday illiquidity and flash-crash risk.
- Skeptical: Dim, Eraker & Vilkov find no amplification from 0DTE gamma. Cboe's own 2023 analysis estimates net 0DTE dealer gamma is small and balanced. A 2026 Princeton thesis finds hedging-feedback variables add no meaningful predictive power for intraday variance forecasts.
- Fatal practical issue: **the sign of dealer positioning is unobserved**. OI does not say who is long. Vendor GEX assumes customers buy puts and sell calls, which is often wrong (overwriters sell calls, so dealers are long calls; that sign is right, but structured-product flows break the assumption). Alpaca gives no OI history and no trade direction. Effects are documented mainly at the **intraday** horizon.
- Verdict: **hype for a daily premium-selling bot.** Do not build on it. If you test it at all, use the free proxy "is the market near the large open-interest SPX strikes". You cannot reconstruct historical OI on Alpaca anyway.

---

## 14. What is likely overfit or hype

1. **"High IV Rank means sell."** It raises credit and lowers the hit rate; IVR > 80 had the worst tail in-house. The tastytrade evidence is marketing-grade (no CVaR, small n, from a broker paid per trade).
2. **GEX / zero-gamma flip levels.** Unobservable dealer sign, vendor black boxes, intraday-horizon evidence only (section 13).
3. **SKEW as a crash predictor.** If anything the sign is reversed (section 7).
4. **Win-rate marketing ("90% win rate").** Short OTM options trivially have high win rates. Judge only on expectancy, CVaR(5%), max drawdown, and *days to recover*. Feb 2018 (XIV) and Feb/Mar 2020 erased years of premium.
5. **Fine-tuned thresholds** (VIX/VIX3M < 0.9273, IVR 37 to 63, delta 17.5). Use round numbers and report a sensitivity grid (0.90 / 0.95 / 1.00). If performance cliffs between neighbors, it is overfit.
6. **Backtests that start after 2009 or after 2016.** They miss 2008, and post-2016 data has only 2018, 2020, 2022 and 2025 as stresses. The 2024-02+ Alpaca option window has no real bear market at all. Regime filters must be validated on 1990 to 2026 VIX/SPX data or 2007 to 2026 PUT data.
7. **Daily-toggle backtests of monthly strategies** (including my in-house PUT switch). They overstate filter value by assuming free exits.
8. **0DTE edge from daily bars.** Not modelable (section 12).
9. **Single-stock VRP assumed equal to the index VRP.** It is much smaller (correlation premium), and earnings make it negative around events.
10. **Delta-unhedged "VRP" backtests.** Mostly equity beta (Israelov & Nielsen). Compare against a beta-matched long SPY position, e.g. 0.5 delta for ATM puts.

---

## 15. Rules to backtest

The gate is evaluated at the entry close, with signals lagged one day. Default structure: 30 to 45 DTE SPY put spread, 16 to 20 delta short leg, 5 to 10 points wide. Exit at 50% of max profit, or at 21 DTE, or at a 2x credit stop. Report CAGR, Sharpe, Sortino, CVaR5, max drawdown, recovery time, % invested, and turnover. Benchmark against unconditional monthly entry and a beta-matched long SPY.

| # | Rule | Rationale | Expected effect | Data source |
|---|---|---|---|---|
| R1 | Enter only if `VIX - RV21(SPX) > 0` | Sell only when IV is rich vs current realized (Goyal-Saretto; in-house best CAGR) | Higher CAGR, max drawdown about halved vs unconditional | VIX CSV + Alpaca SPY daily bars |
| R2 | Enter only if `VIX/VIX3M < 1.0` (variants: 0.95, 0.90) | Backwardation is the only regime with negative mean VRP (Johnson 2017; in-house) | Sharpe about 0.5 → about 0.9; small CAGR cost | VIX + VIX3M CSVs |
| R3 | R1 AND R2 | Combine richness and stress | Best Sharpe in-house (1.01), max drawdown -13% vs -37% | VIX, VIX3M, SPY bars |
| R4 | Block entry if `VIX9D/VIX > 1.1`; force-close if `> 1.2` | Short-end inversion is an early stress flag (in-house: IV > RV only 66% above 1.2) | Fewer large losers; some false alarms | VIX9D + VIX CSVs |
| R5 | `SPX > SMA200` OR `VIX/VIX3M < 0.95` (vs AND, vs 200DMA alone) | Trend cuts the tail but misses V-recoveries; OR re-admits normalized term structure | 200DMA alone: lower drawdown, lower CAGR; OR should recover CAGR | SPX CSV or Alpaca SPY bars; VIX3M CSV |
| R6 | Size = base × min(1.5, VIX_median / VIX); no new trades if VIX > 40 and TS > 1 | Vol targeting; VIX > 40 has the worst tail | Smoother equity, similar CAGR | VIX CSV |
| R7 | IVP(252) in 30 to 70 AND R2, vs IVR > 50 alone | Test the tastytrade claim honestly; expect IVR > 80 to hurt CVaR | IVR > 50 alone: higher credit, worse CVaR | VIX CSV (SPY); Alpaca option bars for single names (2025+) |
| R8 | Single names: no short option spans the next earnings date | Earnings vol is on average underpriced (Gao-Xing-Zhang +3.34% to straddle buyers) | Avoids the largest single-name losses | Alpaca contracts + external earnings calendar |
| R9 | Long ATM straddle T-3 → T0 around earnings for liquid large caps (long-premium test) | Gao-Xing-Zhang | Small positive expectancy before costs; check bid/ask | Alpaca option bars 2024-02+ |
| R10 | Enter short premium on FOMC/CPI day T+1 vs fixed calendar entry | Collect the post-event IV reset, avoid the jump (straddles earn +1.7% through FOMC) | Modestly better risk-adjusted returns | Fed and BLS calendars + VIX CSV |
| R11 | Single names: open Thu/Fri close vs Mon open | Weekend mispricing (Jones-Shemesh 2018; Muravyev-Ni 2020) | Small positive gain for single names; about nil for SPY | Alpaca option bars 2024-02+ |
| R12 | Cross-section (top 50 large caps): sell puts on the top quintile of `IV30 - RV_YZ(20)`, skip the bottom quintile | Goyal-Saretto cross-sectional sort | Better per-trade expectancy than random-name selection | Alpaca option + stock bars (2024-02+, short sample) |
| R13 | Drawdown gate: no new shorts if `DD < -10%` AND TS > 1; resume at half size when TS < 0.95 | Separate the slide from the recovery | Lower CVaR; keeps the post-crash premium | SPX CSV + VIX3M CSV |
| R14 | Steep skew (SKEW > 140 or RR25 in the top tercile) → put spreads/ratios; flat skew → strangles | Sell the rich wing | Better credit per unit of risk; not a timing signal | SKEW CSV; RR25 from Alpaca option bars |
| R15 | Monthly (PUT) vs weekly (WPUT) cadence under the same R3 gate | Cadence vs VRP harvest | Picks DTE before building any further | PUT and WPUT CSVs |

**Robustness protocol.** Fit thresholds on 1990 to 2009 (VIX/SPX) or 2007 to 2015 (PUT). Test out of sample on 2016 to 2026. Report the 0.90 / 0.95 / 1.00 grid. Use non-overlapping monthly entries for significance (n of about 220 since 2007; t-stats from overlapping daily windows are inflated about 21x in n).

---

## 16. Sources

**Data**
- CBOE daily index CSVs: `https://cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX3M,VIX6M,VIX9D,VIX1D,VVIX,SKEW,SPX,PUT,WPUT,BXM,CNDR,BFLY,PPUT}_History.csv` (verified 2026-10-21)
- FRED VIXCLS: https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS
- Alpaca historical option data (from Feb 2024): https://docs.alpaca.markets/docs/historical-option-data
- Alpaca option bars endpoint: https://docs.alpaca.markets/reference/optionbars
- Alpaca forum, option history start: https://forum.alpaca.markets/t/does-historical-options-data-starts-from-2024-or-my-script-is-wrong/13976
- FOMC calendar: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- CPI release schedule: https://www.bls.gov/schedule/news_release/cpi.htm

**VRP and put-writing**
- Carr & Wu (2009), "Variance Risk Premiums," RFS 22(3). DOI 10.1093/rfs/hhn038
- Bollerslev, Tauchen & Zhou (2009), "Expected Stock Returns and Variance Risk Premia," RFS 22(11). DOI 10.1093/rfs/hhp008
- Bakshi & Kapadia (2003), "Delta-Hedged Gains and the Negative Market Volatility Risk Premium," RFS 16(2). DOI 10.1093/rfs/hhg002
- Bakshi, Kapadia & Madan (2003), "Stock Return Characteristics, Skew Laws, and the Differential Pricing of Individual Equity Options," RFS 16(1). DOI 10.1093/rfs/16.1.0101
- Driessen, Maenhout & Vilkov (2009), "The Price of Correlation Risk," JF 64(3): https://ideas.repec.org/a/bla/jfinan/v64y2009i3p1377-1406.html
- Cboe, Bondarenko white paper summary: https://www.cboe.com/insights/posts/white-paper-shows-volatility-risk-premium-facilitated-higher-risk-adjusted-returns-for-put-index/
- Cboe benchmark index returns: https://www.cboe.com/insights/posts/key-cboe-benchmark-indexes-using-spx-options-offer-strong-risk-adjusted-returns/
- Cboe weekly vs monthly PutWrite study (2016): https://ir.cboe.com/news/news-details/2016/New-Study-On-Weekly-Monthly-SP-500-PutWrite-Indexes-Released-01-27-2016/default.aspx
- CXO summary of PutWrite indexes: https://www.cxoadvisory.com/equity-options/performance-of-cboe-putwrite-indexes
- Israelov & Nielsen (2015), "Covered Calls Uncovered," FAJ 71(6): https://www.aqr.com/Insights/Research/Journal-Article/Covered-Calls-Uncovered
- di Pietro & Vainberg (2006), via CXO: https://www.cxoadvisory.com/equity-options/consistently-expensive-types-of-equity-options/

**IV - RV, term structure, VVIX, skew**
- Goyal & Saretto (2009), "Cross-Section of Option Returns and Volatility," JFE 94(2), DOI 10.1016/j.jfineco.2009.01.001: https://ideas.repec.org/a/eee/jfinec/v94y2009i2p310-326.html
- Johnson (2017), "Risk Premia and the VIX Term Structure," JFQA 52(6): https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/abs/risk-premia-and-the-vix-term-structure/56572D1F060448571BD8F597C732D9C3
- CXO summary of Johnson: https://www.cxoadvisory.com/volatility-effects/vix-term-structure-slope-and-variance-asset-future-returns/
- Huang, Schlag, Shaliastovich & Thimme (2019), "Volatility-of-Volatility Risk," JFQA 54(6): https://ideas.repec.org/a/cup/jfinqa/v54y2019i6p2423-2452_5.html ; SSRN PDF: https://publikationen.ub.uni-frankfurt.de/files/46676/SSRN-id3183610.pdf
- SKEW predictive tests (blog): https://www.r-bloggers.com/2019/12/skewed-perceptions-2/
- ORATS backtest method (contango, skew, VIX filters): https://orats.com/blog/backtesting-basics-trading-spy-options-based-on-contango-skew-and-vix-levels

**Realized-vol estimators**
- Parkinson (1980), Journal of Business 53(1), 61–65. DOI 10.1086/296071
- Garman & Klass (1980), Journal of Business 53(1), 67–78. DOI 10.1086/296072
- Yang & Zhang (2000), "Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices," Journal of Business 73(3), 477–491. DOI 10.1086/209650

**Events, calendar, 0DTE**
- Gao, Xing & Zhang (2018), "Anticipating Uncertainty: Straddles around Earnings Announcements," JFQA 53(6): https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/anticipating-uncertainty-straddles-around-earnings-announcements/7B34877AD5E06304BA3C55FBA3219FDD
- Dubinsky, Johannes, Kaeck & Seeger (2019), "Option Pricing of Earnings Announcement Risks," RFS 32(2). DOI 10.1093/rfs/hhy060
- Lucca & Moench (2015), "The Pre-FOMC Announcement Drift," JF 70(1). DOI 10.1111/jofi.12196
- VIX behavior on FOMC days (AUT working paper): https://openrepository.aut.ac.nz/bitstream/10292/9444/6/Perez.pdf
- FOMC implied variance by regime (Quantpedia): https://quantpedia.com/fomc-equity-drift-occurs-in-periods-of-high-uncertainty/
- OptionMetrics, straddles through FOMC: https://www.globalvolatilitysummit.com/wp-content/uploads/2021/05/OptionMetrics-Long-Vol-Losses-Where-Do-They-Come-From.pdf
- Jones & Shemesh (2018), "Option Mispricing around Nontrading Periods," JF 73(2), DOI 10.1111/jofi.12603: https://ideas.repec.org/a/bla/jfinan/v73y2018i2p861-900.html
- Muravyev & Ni (2020), "Why Do Option Returns Change Sign from Day to Night?" JFE 136(1). DOI 10.1016/j.jfineco.2019.09.002
- Dim, Eraker & Vilkov (2024), "0DTEs: Trading, Gamma Risk and Volatility Propagation," SSRN 4692190: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4692190
- Cboe, "Much Ado About 0DTEs": https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options
- Beckmeyer, Branger & Gayda (2023), "Retail Traders Love 0DTE Options... But Should They?" SSRN 4404704: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4404704
- Bandi, Fusari & Renò (2023), "0DTE Option Pricing," SSRN 4503344: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4503344

**Gamma / GEX**
- Baltussen, Da, Lammers & Martens (2021), "Hedging Demand and Market Intraday Momentum," JFE 142, DOI 10.1016/j.jfineco.2021.04.029: https://www3.nd.edu/~zda/intramom.pdf
- Ni, Pearson, Poteshman & White (2021), "Does Option Trading Have Mechanical Effects on Stock Prices? Evidence from Option Expiration," RFS 34(4). DOI 10.1093/rfs/hhaa082
- Alpha Architect, "Does gamma hedging actually affect stock prices?": https://alphaarchitect.com/hot-topic-does-gamma-hedging-actually-affect-stock-prices/
- SqueezeMetrics GEX white paper (vendor): https://squeezemetrics.com/download/white_paper.pdf
- Princeton senior thesis (2026) on hedging feedback: https://theses-dissertations.princeton.edu/entities/publication/61115cd1-cb09-4a8b-9230-ff488fc2a386

**Practitioner (lower evidence grade)**
- tastylive IV Rank studies: https://tastylive.com/shows/market-measures/episodes/iv-rank-various-time-periods-07-31-2015 ; https://tastylive.com/shows/market-measures/episodes/patience-during-rising-ivr-11-15-2016
- Spintwig short-put backtests (via Early Retirement Now): https://earlyretirementnow.com/2021/11/10/passive-income-through-option-writing-part-9-2016-2021-backtest-guest-post-by-spintwig/
- Option Alpha trend-filter discussion: https://optionalpha.com/podcast/trend-trading

Note: the DOIs for journal articles come from journal and RePEc listings. They were not resolved through LibKey, which needed an authentication this session could not complete.
