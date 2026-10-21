# Event and Short-Dated Option Strategies (Mega-caps and Index ETFs)

Research note, 2026-10-21. Scope: earnings (AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, NFLX), 0DTE/1DTE SPY/QQQ, FOMC/CPI, weekly vs monthly expiries, and ex-dividend assignment on covered calls. Each section covers mechanics, the best-documented parameters, evidence, failure modes, and a rule a daily-bar backtester can run on Alpaca data.

## 0. Data constraints that shape every rule below

- **Alpaca option history starts in February 2024.** Bars are built from OPRA trades, and there are no historical quotes (forum reports show 404s on historical quotes). So the daily `close` is the last trade, not a mid, and it can be stale on illiquid strikes. Use the bar `vwap` and `trade_count` as liquidity checks. ([Alpaca docs](https://docs.alpaca.markets/docs/historical-option-data), [forum](https://forum.alpaca.markets/t/does-historical-options-data-starts-from-2024-or-my-script-is-wrong/13976))
- **Implication.** About 2.7 years of option history gives roughly 10-11 earnings events per ticker, about 85 events across the 8 names, and about 21 FOMC meetings. Treat every event backtest as low-power. Realized moves can be tested back to 2016 with stock bars plus the EDGAR dates below; implied moves exist only from 2024-02.
- **Cost model (required).** Price each leg at `close ± k * max($0.01, 0.5 * est_spread)`. A reasonable per-leg haircut on liquid ATM mega-cap and SPY/QQQ options is $0.01-0.03 per leg for SPY/QQQ and 1-3% of premium for single-name ATM options around earnings. OptionKrafter's 0DTE SPY condor test shows the gap between mid and realistic fills (about $1.48 per condor over 8 leg-fills) is enough to flip the sign of expectancy (see section 2).
- **Strike availability.** Use the Alpaca contracts endpoint with `status=inactive` (expired contracts) to rebuild each day's chain. Pick strikes only among contracts with a bar on the entry date and `volume > 0`.

## 1. Earnings-date data source (verified 2026-10-21)

### Primary source: SEC EDGAR submissions API (free, no key; requires a descriptive User-Agent header)

1. Ticker to CIK: `GET https://www.sec.gov/files/company_tickers.json`
   - Verified CIKs: AAPL 320193, MSFT 789019, NVDA 1045810, AMZN 1018724, GOOGL 1652044, META 1326801, TSLA 1318605, NFLX 1065280.
2. Filings: `GET https://data.sec.gov/submissions/CIK##########.json` (10-digit zero-padded, e.g. `CIK0000320193.json`).
   - `filings.recent` holds parallel arrays: `form`, `items`, `filingDate`, `acceptanceDateTime`, `accessionNumber`.
   - Older filings are in `filings.files[].name`, e.g. `https://data.sec.gov/submissions/CIK0001326801-submissions-001.json`. META's early-2024 reports are only in this older file.
3. Filter: `form == "8-K"` and `"2.02" in items` (Item 2.02, Results of Operations and Financial Condition).
4. Timing (important). The `acceptanceDateTime` in the JSON is labelled `Z`, but it is not true UTC. Comparing it with the filing index page shows it is ET wall-clock time plus the ET offset again:
   - Apple 2026-07-30: JSON `2026-07-31T00:30:28Z`, while the index page `.../000032019326000018/0000320193-26-000018-index.htm` shows "Accepted 2026-07-30 16:30:28".
   - Apple 2026-01-29: JSON `02:30:33Z`, true time 16:30:33 EST.
   - Tesla 2025-10-22: JSON `00:06:36Z`, true time 16:06:36.
   - Fix: `true_et = to_ET(json_ts) + utcoffset(to_ET(json_ts))`.
   - Classify the report as after the close (AMC) if `true_et.hour >= 16`, and before the open (BMO) if earlier than 09:30.
5. Gotchas:
   - **TSLA** also files Item 2.02 for quarterly delivery reports around 9:00 ET on the 2nd of Jan/Apr/Jul/Oct. Drop TSLA 2.02 filings with day-of-month <= 5 or `true_et.hour < 12`.
   - Watch for 8-K/A amendments.
   - Every report for these 8 names since 2024 was AMC.
6. Fair-access limit: 10 req/s or fewer, with a User-Agent such as `"OptionsBot research contact@yourdomain"`.

### Extracted dates (all AMC; "day D" = filing date; the reaction is at D+1)

| Ticker | Report dates (2024-01 to 2026-10) |
|---|---|
| AAPL | 2024-02-01, 05-02, 08-01, 10-31; 2025-01-30, 05-01, 07-31, 10-30; 2026-01-29, 04-30, 07-30 |
| MSFT | 2024-01-30, 04-25, 07-30, 10-30; 2025-01-29, 04-30, 07-30, 10-29; 2026-01-28, 04-29, 07-29 |
| NVDA | 2024-02-21, 05-22, 08-28, 11-20; 2025-02-26, 05-28, 08-27, 11-19; 2026-02-25, 05-20, 08-26 |
| AMZN | 2024-02-01, 04-30, 08-01, 10-31; 2025-02-06, 05-01, 07-31, 10-30; 2026-02-05, 04-29, 07-30 |
| GOOGL | 2024-01-30, 04-25, 07-23, 10-29; 2025-02-04, 04-24, 07-23, 10-29; 2026-02-04, 04-29, 07-22 |
| META | 2024-02-01, 04-24, 07-31, 10-30; 2025-01-29, 04-30, 07-30, 10-29; 2026-01-28, 04-29, 07-29 |
| TSLA | 2024-01-24, 04-23, 07-23, 10-23; 2025-01-29, 04-22, 07-23, 10-22; 2026-01-28, 04-22, 07-22 (delivery 2.02s removed) |
| NFLX | 2024-01-23, 04-18, 07-18, 10-17; 2025-01-21, 04-17, 07-17, 10-21; 2026-01-20, 04-16, 07-16, 10-20 |

### Secondary and forward-looking sources

- **Nasdaq calendar** (free, unofficial, needs a browser User-Agent): `GET https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`.
  - Verified: 304 rows for 2025-10-30, including AAPL and AMZN.
  - Fields: `symbol, time, eps, epsForecast, fiscalQuarterEnding, ...`.
  - Note: `time` came back as `time-not-supplied` for these names, so use EDGAR for timing. Use Nasdaq only for upcoming dates, since EDGAR is historical only.
- **Nasdaq dividends** (verified, 83 rows for AAPL): `GET https://api.nasdaq.com/api/quote/AAPL/dividends?assetclass=stocks`. Fields: `exOrEffDate, amount, declarationDate, recordDate, paymentDate`. Alpaca's corporate-actions API is an alternative.
- **FOMC dates:** `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` (verified 200). Statement links follow the pattern `monetaryYYYYMMDDa.htm`, but non-meeting releases (e.g. `monetary20250822a`) also match, so check against the meeting table. Older years: `fomc_historical_year.htm`.
- **CPI dates:** BLS (`https://www.bls.gov/schedule/news_release/cpi.htm`) returned 403 to scripted requests. Use the FRED release calendar `https://fred.stlouisfed.org/releases/calendar?rid=10&y=YYYY` (verified 200), or the FRED API `release/dates?release_id=10` (free key). CPI prints at 08:30 ET, before the open.

## 2. Earnings strategies

### 2.1 Background: the "earnings announcement premium" literature

- **Dubinsky, Johannes, Kaeck & Seeger, "Option Pricing of Earnings Announcement Risks", RFS 32(2) 2019, 646-687, doi:10.1093/rfs/hhy060.**
  - Option prices embed a separate, large, time-varying earnings-day jump variance.
  - That variance can be extracted from the term structure of implied volatility (front expiry spans the event, next expiry dilutes it).
  - It predicts realized announcement volatility.
  - This is the theory behind the "implied move" and behind calendars: the variance of the front expiry minus the variance of the back expiry isolates the event.
- **Gao, Xing & Zhang, "Anticipating Uncertainty: Straddles Around Earnings Announcements", JFQA 53(6) 2018, 2587-2617, doi:10.1017/S0022109018000285.** Sample: 1996-2013, OptionMetrics, ATM delta-neutral straddles, priced at closing mid.
  - Unconditionally, straddles lose money: -0.19%/day, -2.12%/week, -17.09%/month.
  - Around earnings (pooled, equal-weighted), with day 0 = announcement day:
    - [-3,-1]: +1.90% (t=16.4)
    - [-3,0]: +2.60%
    - [-3,1]: +1.98%
    - [-1,1]: +2.43%
  - **Large-firm (dollar-open-interest weighted, time-series) results matter most for this universe:**
    - [-3,-1]: +1.37% (t=3.8). This is the pure run-up, sold before the news.
    - [-3,0]: +1.10% (t=2.0)
    - [-3,1]: -0.67% (n.s.)
    - Day-by-day: [-2,-1] +1.11% (t=4.2); [0,1] **-1.37% (t=-3.1)**.
  - Interpretation for big names: the run-up into the report is modestly positive, and holding long vol across the report is negative. The edge is concentrated in small, illiquid, high-spread names (high-volume quintile +1.33% vs low-volume +5.83%), which is the opposite of this universe.
- **Muravyev & Ni, "Why do option returns change sign from day to night?", JFE 136(1) 2020, 219-238.**
  - Delta-hedged S&P 500 option returns are about -1%/day close-to-open and +0.3%/day open-to-close, and equity options show the same pattern.
  - Relevance: short premium held overnight (close-to-open) is where option sellers earn, and an AMC earnings trade is by construction an overnight hold.
- **Stock-side "earnings announcement premium":**
  - Frazzini & Lamont (NBER w13090, 2007): stocks earn over 7%/yr, or about 60 bp/month long-short, in announcement months.
  - Barber, De George, Lehavy & Trueman (JFE 2013): the premium appears globally.
  - This is a small positive drift in the underlying, and implies a mild call-side tilt at most.
- **Milian (JRFM 16(5) 2023, "Efficiency of Weekly Option Prices around Earnings").** Weekly earnings straddle returns are higher (lower) when the historical earnings move is high (low) relative to the implied move. **Implied/historical ratio is the documented filter.**
- **ORATS earnings backtest** (5,217 announcements, 2020-2021, entry day before, exit day after, about 14 minutes before close):
  - sell straddle +1.18% (largest loss -26.7%)
  - buy straddle +0.40%
  - long calendar +0.90% in-sample and +0.91% out-of-sample, the most robust
  - ([ORATS](https://orats.com/blog/earnings-options-strategies-backtest))
- **OptionMetrics on AAPL (2021):** buying a straddle before earnings and selling after lost 40-60% of the premium over the prior year. Buying one day before and selling before the report did better. ([OptionMetrics](https://optionmetrics.com/blog/2021_appl_earnings/))

### 2.2 Implied vs realized move, by company

- **Definition.** Implied move IM = (ATM call + ATM put) / S, using the first expiry after the report, taken at the close of day D (the AMC report day). Realized move RM = |S(D+1 close) / S(D close) - 1|. A gap-based version uses the D+1 open.
- **Published tracker** (earnings-watcher.com, as of July 2026; about 16 reports per name; "beat" = peak earnings-day move > implied, which is an intraday-peak measure and so overstates close-to-close beats):

| Ticker | Avg move | 95th pct tail | Realized > implied |
|---|---|---|---|
| AAPL | ±4.9% | ±8.1% | 44% (7/16); 53% (9/17) on another page |
| MSFT | ±5.0% | ±9.4% | 50% (8/16) |
| NVDA | ±8.3% | ±20.1% | **25% (4/16)**; 29% (5/17) on another page |
| AMZN | ±7.4% | ±13.7% | 60% (9/15) |
| GOOGL | ±6.5% | ±10.3% | 56% (9/16) |
| META | ±11.8% | ±25.8% | **63% (10/16)**; up to 75% on another page |
| TSLA | ±9.3% | ±16.3% | 56% (9/16); up to 69% |
| NFLX | ±10.5% | ±20.1% | 56% (9/16) |

- Recent examples (same source):
  - AAPL 2026-07-30: implied ±3.8%, realized -7.4%
  - NVDA 2026-08-26: implied ±5.9%, realized +8.7%
  - TSLA 2026-07-22: implied ±5.9%, realized -15.6%
  - META 2026-07-29: implied ±8.7%, realized -8.0%
- **Reading.**
  - A beat rate around 50% on an intraday-peak basis implies a lower close-to-close beat rate.
  - Even so, these names are not a uniform sell. NVDA (and, per the tracker, AAPL/MSFT) look systematically overpriced. META, AMZN and TSLA have fat tails (95th percentile at 2-3x the average move) that wipe out many small wins.
  - Practitioner claims that the implied move "overstates 65-75% of the time" are unsourced; do not rely on them.
- **Backtester task #1** (no options needed, runs from 2016): compute RM per event from Alpaca stock bars and EDGAR dates. Then from 2024-02, compute IM from option closes and produce the per-ticker ratio RM/IM: mean, median, % > 1, and the 90th percentile.

### 2.3 Pre-earnings IV run-up (long straddle/strangle, exit before the report)

- **Mechanics.**
  - Buy the ATM straddle in the first expiry after the report, N trading days before D.
  - Sell at the close of D, before the AMC release.
  - Profit comes from front-expiry IV rising faster than theta decays, plus any pre-report drift.
- **Evidence.**
  - Large firms: GXZ [-3,-1] +1.37% and [-2,-1] +1.11%, at mid, before costs. That is thin after a 1-3% round-trip spread on the straddle.
  - Khan & Khan (SSRN 4832160, 17 years of S&P 500 names): straddles opened about 30 trading days before earnings lose value up to the day before, because theta outweighs the IV gain.
  - FlashAlpha light backtest (buy 10 sessions before, sell the session before): mean -0.32%, 33% win rate, n=12 (on ETFs, so weak relevance).
  - **Conclusion:** the window must be short, roughly 3-5 days, not 10-30.
- **Failure modes.**
  - Theta on a short-dated front expiry. The run-up is already priced (market makers mark event variance from the day the date is confirmed, so the front IV rises mostly mechanically as days leave the denominator, which theta offsets).
  - Wide spreads, and pre-report drift in the wrong direction (small, since the position is delta-neutral at entry).
- **Best parameter set to test:** entry at close D-3 (alternatives D-5, D-2), first post-event expiry, ATM straddle, exit at close D, no hedging.

### 2.4 Short vol through the report (iron condor / iron fly entered at close of D)

- **Mechanics.**
  - At the close of D (AMC report tonight), sell a structure in the first expiry after D (usually that Friday).
  - Short strikes at about 1.0-1.2x the implied move, wings 1.5-2x the implied move (iron condor). Iron fly variant: short ATM straddle with wings at ±1.5x IM.
  - Exit at the open or close of D+1, after the crush.
- **Evidence.**
  - GXZ large-firm [0,1] long straddle -1.37%: short earns that before costs.
  - ORATS sell straddle +1.18% per trade, largest loss -26.7% of stock price.
  - Muravyev-Ni overnight short-premium effect.
  - Risk side: tail moves on META/TSLA/NFLX (95th pct ±16-26%) mean naked short straddles are unacceptable; wings are mandatory.
  - A case study on NVDA found the crush gains "typically overwhelmed" by move losses on naked shorts. This contradicts the beat-rate data and argues for defined risk.
- **Failure modes.**
  - One 3x-implied move per year erases about 10 wins.
  - Gap opens leave no stop opportunity.
  - Strike staleness in Alpaca closes.
  - Selection bias from picking only names that recently looked overpriced.
- **Filter (from Milian 2023):** trade only when IM / median(last 8 RM) >= 1.15-1.25. Optionally restrict to tickers with a historical RM/IM median < 0.8 (for example NVDA, MSFT, AAPL) and skip META/TSLA unless the ratio is extreme.

### 2.5 Calendar spreads around earnings

- **Mechanics.** Sell the front (post-event weekly) ATM option or straddle, buy the next monthly at the same strike, at the close of D. After the event, the front IV collapses much more than the back. This is exactly the DJKS term-structure effect.
- **Evidence.**
  - ORATS long calendar: +0.90% in-sample and +0.91% out-of-sample; the best-generalizing of 4 structures.
  - OptionsTradingIQ double calendar (30 Dow stocks, strikes at ±implied move, front = first expiry 2 days or more out, back = monthly 21 days or more later, 15% profit target): 26 wins, 4 losses, about +$56/trade. This is a manual test with an unknown date range.
  - Max loss is roughly the debit plus adjustment risk. The main loss is a move well beyond the strike, where both legs go to intrinsic.
- **Failure modes.**
  - The back month also gets crushed (vega in both legs).
  - The back month is less liquid, so its stale last-trade price is a severe problem in Alpaca daily bars: require back-leg volume > 0 on both entry and exit days.
  - Early assignment on an ITM short call before ex-dividend (rare for these names).
- **Daily-bar testable:** yes, entry at close D and exit at close D+1.

## 3. 0DTE / 1DTE SPY and QQQ

- **Context.**
  - Bandi, Fusari & Renò ("0DTE Option Pricing", SSRN 4503344): 0DTE options are over 43% of SPX volume, and the 0DTE variance risk premium is high.
  - Dim, Eraker & Vilkov: 0DTE gamma does not amplify volatility.
  - Beckmeyer, Branger & Gayda (SSRN 4404704, 2021-2023 CBOE data): retail 0DTE traders lost about $358k/day after May 2022, mostly as buyers.
- **Best quantified selling study.** OptionKrafter, SPY 0DTE iron condor (2023-04 to 2025-12, n=558, minute data, trade-print fills):
  - Setup: entry 10:00 ET, 16-delta shorts, $5 wings, min credit $0.20, take profit 50%, stop at 2x credit, time exit 15:45.
  - Win rate 75.6% vs break-even 77.0%. Average win +$19.97, average loss -$66.73. Expectancy **-$1.16/trade**; net -$649 with realistic fills vs **+$179 at mid**. Max drawdown -$2,217.
  - By year: 2023 +$428, 2024 -$571, 2025 -$506.
  - Skipping FOMC/CPI days barely helped; event-day expectancy was -$1.71 vs -$1.12 on other days.
  - ([OptionKrafter](https://optionkrafter.com/blog/0dte-iron-condor))
- **Other vendor numbers.**
  - Option Omega SPX 0DTE IC (2018-2022): 82.7% win rate, n=133, with the largest loss far larger than the typical win.
  - Option Alpha SPX IC (about 0.30 delta, $5 wings, 3 years): 52.3% win rate, profit factor 0.8, -$4,827.
  - Option Alpha also reports about a $15k P&L difference between 10:30 and 14:30 entries in one study. No rigorous entry-time study was found.
- **Conclusion.** The documented 0DTE condor edge is around zero at mid and negative after costs. Results are dominated by stop rules and fills, which need intraday data.
- **What daily bars can and cannot test.**
  - **Cannot:** intraday entries (10:00, 14:30), profit-take or stop exits, touch-based stops, and butterflies needing precise pin timing. Results for these are path-dependent.
  - **Can, cleanly: "1DTE close-to-settlement."**
    - Sell at the close of T (option daily close) an iron condor or put spread expiring T+1.
    - Settle at intrinsic using the underlying close on T+1.
    - SPY/QQQ have daily expiries (Mon-Fri) for this whole sample.
    - This directly harvests the overnight premium (Muravyev-Ni) plus the next day's session, with no exit fills (expire or settle).
    - Caveat: SPY/QQQ are American and physically settled. Model pin risk by assuming exercise if the close is within $0.05 of the strike, and the after-hours move risk.
  - **Can, approximately: "0DTE open-to-close."** Sell at the daily `open` of a 0DTE contract and settle at the underlying close. The option open is the first print, which may be minutes late or from a wide opening quote, so apply a larger haircut.
  - **Can: "overnight short straddle."** Sell the 1DTE ATM straddle at the close of T, buy back at the open of T+1 (option bar `open`). This tests Muravyev-Ni directly, but open prints are noisy.

## 4. FOMC and CPI event vol

- **Evidence.**
  - Wright (NBER w28306, "Event-Day Options"): variance risk premia on announcement days are large and significantly positive, "especially for FOMC days". About half of the event-day rise in implied variance is premium (Treasury-futures options).
  - Nasdaq/NDX commentary: a 1-day ATM straddle seller profited on 9 of the last 12 FOMC days through 2025.
  - OptionMetrics and a Delaware dissertation report positive delta-neutral index straddle returns over FOMC meetings, attributed to a "Fed put". This is ambiguous in sign convention; verify before using.
  - Windmar (Lund 2025): long straddles bought ahead of FOMC/CPI/PPI return less than the average announcement.
  - Option Alpha: since 2022, the SPX averages about a 2% absolute move on FOMC days vs 1.25% otherwise, and the day after FOMC is down 66.7% of the time (small n).
  - CPI (Rhoads, Feb 2024, trailing 12 months): average |SPX| move on CPI day ±0.60%, the same as on all days (±0.61%), while 1-day straddles price elevated risk. So CPI-day straddles were overpriced in that window, but the Nov 2023 +1.9% day "ran over" sellers.
- **Timing.**
  - FOMC: the statement is at 14:00 ET and the press conference at 14:30, so the event sits inside a daily bar. Entering at the close of T-1 with a 1DTE (expiring on FOMC day) captures the whole event.
  - CPI: released at 08:30 ET, before the open. Entering at the close of T-1 with an expiry on CPI day captures the gap, but not the crush in the CPI-day open (the option opens already crushed).
- **Failure modes.**
  - Regime dependence: 2022 inflation prints produced 3-5% SPX days.
  - Small sample: about 21 FOMC and about 32 CPI events since 2024-02.
  - Event dates cluster with other events.

## 5. Weekly vs monthly expiries for premium selling

- **Theory.**
  - Gamma scales with 1/sqrt(T): a 5-DTE ATM option has about 2.6x the gamma of a 35-DTE one.
  - Andries, Eisenbach, Kahn & Schmalz (Review of Finance 2025 / NY Fed SR 736): the price of variance risk is largest in absolute value at the shortest maturities. Short-dated options carry more premium per unit of variance but more jump exposure.
  - Other papers (Amengual) find larger premia at longer horizons, so the evidence is mixed.
- **Practitioner numbers** (unverified vendors):
  - OptionsPilot SPY 16-delta IC (2016-2026): weekly 18.4% per year, Sharpe 0.88, max drawdown 28.6%; 45-DTE 13.2%, Sharpe 1.14, max drawdown 18.4%.
  - Tastytrade's claimed result that EV per trade rises with DTE was not retrievable as a primary source.
- **Testable on daily bars.** Compare 7-DTE and 30-45-DTE 16-delta put spreads or ICs on SPY/QQQ:
  - same entry rule (every Monday close, or first trading day of the month)
  - same management (close at 50% of max profit, or at 21 DTE for the monthly)
  - same cost model
  - Report CAGR, Sharpe, max drawdown, and worst 5-day loss.

## 6. Ex-dividend early assignment (covered calls)

- **Mechanics.**
  - A long call holder rationally exercises on the day before the ex-date if the dividend D exceeds the call's remaining extrinsic value.
  - Proxy: the extrinsic value of the same-strike, same-expiry put (by put-call parity, roughly the call's time value). ([Schwab](https://www.schwab.com/learn/story/ex-dividend-dates-understanding-dividend-risk))
  - If assigned, the covered-call writer loses the stock and the dividend (and the stock leaves the strategy).
- **Evidence.**
  - Hao, Kalay & Mayhew (RFS 23(1) 2010, "Ex-dividend Arbitrage in Option Markets"): more than 40% of calls that should be exercised before ex-dividend are not, which is a windfall to writers. Even at $0.84/share of forgone value, more than 32% of open interest stays unexercised.
  - Pool, Stoll & Whaley (J. Financial Markets 11, 2008): same anomaly, exploited by market makers via dividend-play volume.
  - **For a backtester, the conservative assumption is still 100% assignment** whenever the call is ITM and extrinsic < dividend on the day before the ex-date.
- **Relevance by name.**
  - AAPL (~$0.26-0.27 quarterly), MSFT (~$0.83-0.91), NVDA ($0.01) and GOOGL/META (small) have low dividends relative to price.
  - Assignment risk is real only for deep-ITM short calls in the last 1-2 weeks of life.
  - SPY (quarterly, ex-date the third Friday of Mar/Jun/Sep/Dec, about $1.7-1.9) and QQQ (smaller) matter more, because the SPY ex-date coincides with monthly expiration.
  - AMZN, TSLA and NFLX pay none.
- **Rule.** On the trading day before each ex-date, for every short call: if `S - K > 0` and `put_extrinsic(K, expiry) < dividend`, then either (a) roll or close the call at that day's close (the defensive rule), or (b) simulate assignment at the close (the stress rule). Ex-dates come from the Nasdaq dividends endpoint above.

## 7. Rules to backtest

Conventions for all rules:
- D = EDGAR 8-K 2.02 date (AMC).
- Prices are Alpaca daily option closes with the haircut from section 0.
- ATM = strike nearest the underlying close.
- "First post-event expiry" = the nearest expiry strictly after D.
- Report per-ticker and pooled results: n, mean, median, win rate, average win/loss, worst trade, % of P&L from the top 3 trades.
- Also report results at mid-equivalent (no haircut) vs with the haircut, to see how sensitive each rule is to costs.

| # | Name | Entry | Structure | Exit | Filter / notes |
|---|---|---|---|---|---|
| E1 | Implied-move ledger | close D | none (measurement) | close D+1 | IM = (C+P)/S on the first post-event expiry; RM = abs(S_{D+1}/S_D - 1). Output RM/IM per ticker. Prerequisite for E3-E5. |
| E2 | Pre-earnings run-up | close D-3 (variants D-5, D-2) | long ATM straddle, first post-event expiry | close D (before AMC report) | Skip if the straddle cost is more than 1.3x the median of the last 8 RM (run-up already priced). Expect a small edge (GXZ large-firm +1.1-1.4% at mid). |
| E3 | Earnings iron condor | close D | short strikes at ±1.0x IM (nearest listed), long wings at ±2.0x IM; first post-event expiry | close D+1 (variant: D+1 open bar) | Trade only if IM / median(last 8 RM, using stock history from 2016) >= 1.2. Max loss = width - credit, size so max loss <= 1% of equity. |
| E4 | Earnings iron fly | close D | short ATM straddle, wings ±1.5x IM | close D+1 | Same filter as E3. Compare with E3 per ticker (NVDA/MSFT/AAPL vs META/TSLA/NFLX). |
| E5 | Earnings long calendar | close D | sell front ATM straddle (first post-event expiry), buy the same strike in the first monthly expiry 21 days or more later | close D+1 | Require volume > 0 on all legs on both days. ORATS: +0.9% of stock price, the most robust out-of-sample. |
| E6 | Combined E2 then E3 | E2 then E3 | long run-up straddle D-3 to D, then E3 at D | close D+1 | Tests whether capturing both halves of the GXZ pattern beats either alone. |
| Z1 | 1DTE close-to-settle IC | close T, every day | SPY (and QQQ) 1DTE IC: short strikes at about ±1.0x the 1-day implied move (or 10-16 delta via BS on the close IV), $2-5 wings | settle at underlying close T+1 (intrinsic) | Variants: skip if VIX is above its 20-day high; skip FOMC/CPI eves; put-spread-only. No intraday management, so this is fully daily-testable. |
| Z2 | Overnight short straddle | close T | short SPY 1DTE ATM straddle | buy back at open T+1 (option bar open) | Direct Muravyev-Ni test. Heavy haircut on open prints; compare with the close-to-close version. |
| Z3 | 0DTE open-to-close put spread | open T (option bar open) | SPY 0DTE 10-delta put spread, $2-5 wide | settle at close T | Approximate only: the open print is noisy. Use as a sanity check, not a deployable result. |
| F1 | FOMC 1DTE straddle/IC seller | close of the day before the FOMC statement | SPY straddle or IC (±1x IM) expiring on FOMC day | settle at close of FOMC day | About 21 events; also test the reverse (long) to check sign. Day-after-FOMC call spread (Option Alpha "down 66.7%") as a separate tiny-n idea. |
| F2 | CPI eve seller | close of the day before CPI | SPY IC expiring on CPI day, ±1x IM | settle at close of CPI day | Rhoads: realized CPI-day move about equal to a normal day while straddles were priced higher. Compare with a non-event-day placebo. |
| W1 | Weekly vs monthly put spread | Monday close (7-DTE) vs first trading day of month (30-45 DTE) | SPY 16-delta / 5-delta put spread | 50% profit take at any daily close, else expiry (weekly) or 21 DTE (monthly) | Compare Sharpe, max drawdown, worst week at equal notional risk. |
| D1 | Covered-call ex-div guard | every day before an ex-date | existing short calls | close or roll if ITM and put extrinsic < dividend | Run the covered-call backtest twice: guard on, and stress (assume assignment). Ex-dates from Nasdaq or Alpaca corporate actions. |

Priority, by evidence strength and daily-bar fidelity:
1. E1 (measurement)
2. E5 calendar
3. E3 / E4 with the IM/RM >= 1.2 filter
4. Z1 1DTE close-to-settle
5. F1 / F2
6. E2 run-up (likely too thin after costs on mega-caps)
7. Z2 / Z3 (data-quality-limited)

Guardrails:
- Event n is around 10 per ticker, so pool across tickers. Do not optimize thresholds per ticker.
- Hold out 2026 for out-of-sample.
- Bootstrap the mean.
- Inspect the 3 worst trades by hand.

## Sources

Academic:
- Dubinsky, Johannes, Kaeck, Seeger (2019) RFS 32(2):646-687, doi:10.1093/rfs/hhy060. https://sussex.figshare.com/articles/journal_contribution/Option_pricing_of_earnings_announcement_risks/23454023
- Gao, Xing, Zhang (2018) JFQA 53(6):2587-2617, doi:10.1017/S0022109018000285 (full text read; Tables 2-4). https://www.pbcsf.tsinghua.edu.cn/__local/A/11/35/B0A1714D795BD9799087BCCC2AD_A01C4283_54641.pdf
- Muravyev, Ni (2020) JFE 136(1):219-238, doi:10.1016/j.jfineco.2018.12.006. https://ideas.repec.org/a/eee/jfinec/v136y2020i1p219-238.html
- Frazzini, Lamont (2007) NBER w13090. https://www.nber.org/papers/w13090 ; Barber et al. (2013) JFE.
- Milian (2023) JRFM 16(5):270. https://www.mdpi.com/1911-8074/16/5/270
- Khan, Khan, 17-Year Backtest of Straddles around S&P 500 Earnings, SSRN 4832160. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4832160
- Bandi, Fusari, Renò, 0DTE Option Pricing, SSRN 4503344. https://carey.jhu.edu/articles/risk-reward-insights-0dte-option-trading
- Beckmeyer, Branger, Gayda, Retail Traders Love 0DTE Options..., SSRN 4404704. https://cxoadvisory.com/individual-investing/retail-0dte-option-trader-performance
- Dim, Eraker, Vilkov, 0DTEs: Trading, Gamma Risk and Volatility Propagation. https://harbourfrontquant.substack.com/p/the-rise-of-0dte-options-cause-for
- Wright, Event-Day Options, NBER w28306. https://www.nber.org/system/files/working_papers/w28306/w28306.pdf
- Andries, Eisenbach, Kahn, Schmalz, Term Structure of the Price of Variance Risk, NY Fed SR 736. https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr736.pdf
- Hao, Kalay, Mayhew (2010) RFS 23(1):271-303, doi:10.1093/rfs/hhp038 (working-paper full text read). https://en-coller.tau.ac.il/sites/coller-english.tau.ac.il/files/RP_170_Kalay.pdf
- Pool, Stoll, Whaley (2008) J. Financial Markets 11:1-35. https://ideas.repec.org/a/eee/finmar/v11y2008i1p1-35.html
- Windmar (2025, Lund), summarized at https://harbourfrontquant.substack.com/p/the-volatility-risk-premium-around

Practitioner and vendor sources (unaudited):
- https://earnings-watcher.com/wiki/earnings-expected-moves (per-ticker beat rates)
- https://orats.com/blog/earnings-options-strategies-backtest
- https://optionmetrics.com/blog/2021_appl_earnings/
- https://optionstradingiq.com/double-calendar-earnings-trade/
- https://optionkrafter.com/blog/0dte-iron-condor
- https://optionstradingiq.com/option-omega/
- https://app.optionalpha.com/zdte/backtester/test/ZT21740081074387151342
- https://optionalpha.com/blog/trading-the-fomc-meeting-0dte-next-day-strategies
- https://russellrhoads.substack.com/p/index-option-history-around-cpi-reports
- https://www.nasdaq.com/articles/fomc-volatility-premium-evidence-1-day-nasdaq-100-straddles (fetch timed out; figure taken from the search summary)
- https://optionspilot.app/blog/weekly-vs-monthly-options-selling-premium-backtest
- https://www.schwab.com/learn/story/ex-dividend-dates-understanding-dividend-risk
- https://docs.alpaca.markets/docs/historical-option-data

Data endpoints (verified by fetch on 2026-10-21):
- https://www.sec.gov/files/company_tickers.json
- https://data.sec.gov/submissions/CIK##########.json (plus `-submissions-001.json` pages)
- https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{accession}-index.htm (true acceptance time)
- https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD
- https://api.nasdaq.com/api/quote/{SYM}/dividends?assetclass=stocks
- https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- https://fred.stlouisfed.org/releases/calendar?rid=10&y=YYYY (CPI)
