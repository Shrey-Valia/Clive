# Round 3: new equity patterns for a third sleeve

Prepared 2026-10-23 for Shrey. This is research only. No live bot code was changed and no orders were placed. Code is in `src/`, results tables are in `results/`, and charts are in `out/`. (The coordinating session saved this file from the agent's hand-back, because the agent could not write report files itself.)

## The question
The institutional review found that the current book is one correlated short-convexity bet. This round looked for a third sleeve that does not sell insurance and that a ~$10k Alpaca paper account can run from a daily or intraday cron job.

## Verdict

| Rank | Candidate | Verdict | IS Sharpe | OOS Sharpe | OOS CAGR | Max DD | ρ SPY | ρ short bot | On SPY's 20 worst days |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Cross-asset trend, long/flat** (20 ETFs, monthly, 1/3/12-month blend, inverse-vol) | **GO** (paper sleeve) | 0.86 (2001–15) | 0.84 (2016–26) | 7.1% | −12.6% IS / −17.1% OOS | 0.37 | −0.01 | −17% (SPY −103%) |
| 2 | **Noise-area intraday momentum, QQQ**, vol-targeted, flat at close | **WATCH**: paper pilot | 0.91 (2016–21) | 1.42 (2022–26); 1.02 after publication | 18.3% | −15.1% | −0.05 | 0.01 | **+22%** |
| 3 | Overnight-only ETF long (MOC buy / MOO sell), IWM | WATCH | 0.72 (1994–2015) | 0.83 at 0.5 bp; 0.48 at 1.5 bp | 11% | −30% | 0.59 | −0.05 | −64% |
| 4 | 12-1 stock momentum, long/short deciles, liquid names | WATCH | 0.35 | 0.52 | 12% | −37% | −0.02 | 0.02 | +18% (−20% on the short bot's worst 20 days) |
| 5 | Calendar (turn of month ∪ pre-holiday ∪ FOMC), SPY | NO | 0.66 | 0.52 | 4% | −23% | 0.46 | −0.05 | −17% |
| 6 | Sector / country cross-sectional momentum | NO (it is just beta) | 0.42 | 0.83 | 14% | −47% | 0.88 | −0.04 | −97% |
| 7 | Long/short trend on ETFs | NO | 0.73 | 0.22 | 1% | −22% | −0.15 | 0.01 | +19% |
| 8 | Earnings-announcement-return drift (S&P 500) | NO | 1.05 (L−SPY) / −0.04 (L/S) | 0.25 / 0.05 | 1% | −13% | 0.15 | −0.01 | — |
| 9 | Residual short-term reversal, top 500, weekly or monthly | NO | −0.14 to 0.00 | 0.16–0.41 | ≤8% | −48% | 0.2 | −0.02 | −26 to −53% |
| 10 | Short interest (high-SI losers short; low-SI long) | NO | 0.08 / −1.21 | 0.36 / −0.05 | — | −65% | — | — | — |
| 11 | 52-week-high momentum | NO | 0.04 | 0.15 | 0% | −60% | −0.42 | 0.06 | — |
| 12 | Liquid top losers (overnight bounce / next-day long / continuation short / 5-day hold) | NO | −1.9 to 0.6 | −1.9 to 0.3 | <0 | −68 to −100% | ~0 | ~0 | — |
| 13 | Gap-down fills and gap continuation, liquid names | NO | −0.9 | −0.5 to −1.4 | <0 | ~−100% | — | — | — |
| 14 | Market intraday momentum (Gao et al.) | NO | −1.3 to 0.2 | −2.5 to −0.3 | <0 | — | — | — | — |
| 15 | 5-minute opening-range breakout, SPY/QQQ/IWM | NO | −0.5 to 0.4 | −0.5 to 0.0 | ~0 | −20 to −39% | — | — | — |
| 16 | Tug-of-war fight-day count (Akbas et al.) | NO | 0.16 | −0.37 | −5% | −40% | — | — | — |

Notes on the table:
- Sharpe ratios are annualized on the sleeve's own capital, with 0 on flat days, no interest on idle cash, and net of costs.
- **Costs per side:**
  - Stocks: 4 / 6 / 10 / 20 bp by ADV (≥$500M / ≥$100M / ≥$20M / below), and 1.5× that at the open.
  - ETFs: 1.5 bp for majors, 4 bp for thinner ones. QQQ/SPY intraday: 0.5 bp.
  - Plus sell fees. Borrow: 0.5% a year for general collateral and 5% for high-SI names.
- **IS/OOS:** stock and intraday tests use 2016–21 / 2022–26-10. Long-history ETF tests use through 2015 / 2016–26.
- **Deflated Sharpe ratio:** 168 trials in 12 families, including the parameter-neighbourhood and cross-ETF robustness runs.
  - The cross-trial SD of the Sharpe is 0.62 annualized, so the expected maximum Sharpe under the null is 1.69. **No candidate clears a DSR of 0.95.**
  - The best is noise QQQ at 1.31: DSR 0.10, OOS DSR 0.26.
  - Within family: trend 0.67 (36 trials), noise 0.06 (54 trials).
  - PSR (probability the OOS Sharpe is above 0): trend 0.98, noise 0.999.
  - Trend's GO rests on a century of literature, a specification fixed before the OOS run, robustness under every perturbation, and its role in the book (a diversifier). It does not rest on the backtest beating a multiple-testing hurdle.
- **Charts** in `out/`: `candidates_equity.png`, `corr_monthly.png`, `combo_equity.png`, `trials_scatter.png`, `noise_cross_etf.png`, `noise_neighbourhood.png`, `noise_cost.png`, `trend_drawdowns.png`.

## 1. GO: cross-asset trend, long/flat

### 1.1 Hypothesis
- **Time-series momentum** (Moskowitz, Ooi and Pedersen 2012): an asset's own past return predicts its next month. Hurst, Ooi and Pedersen (2017) find this across 1880–2016.
- **Convexity:** trend payoffs look like a long lookback straddle (Fung and Hsieh 2001). This is the overlay the review cites from Man AHL (§8.4).
- **Why long/flat and not long/short:**
  - The short leg lost in 2016–26 (L/S OOS Sharpe 0.22).
  - Long/flat keeps most of the crash cushion.
  - It needs no borrow.

### 1.2 Rules
- **Universe (20 ETFs):** SPY IWM EFA EEM EWJ TLT IEF LQD HYG TIP GLD SLV DBC USO UNG DBA UUP FXE FXY VNQ. An ETF is eligible after 13 months of history.
- **Signal:** at the close of the second-to-last trading day of each month, s_i = mean over L ∈ {21, 63, 252} of 1[R_i,L > R_SHY,L], which is in {0, ⅓, ⅔, 1}.
- **Weight:** w_i = s_i × (0.10 / σ63_i) / √N, where σ63 is the 63-day realized vol, annualized.
  - If Σw > 1.5, scale all weights down so Σw = 1.5.
- **Trade:** rebalance at the month-end close (MOC) and hold one month. Average gross is 0.92; turnover is about 9× a year.
- **Costs:** as in the table notes.

### 1.3 Results
- **IS (2001–15):** CAGR 7.3%, vol 8.7%, Sharpe 0.86, max DD −12.6%. SPY over the same years: Sharpe 0.49, max DD −55%.
- **OOS (2016–26):** CAGR 7.1%, vol 8.5%, Sharpe 0.84, max DD −17.1%.
- **Per year, %:**
  - 2001–2015: 0.0, −2.2, 22.0, 11.8, 5.0, 15.4, 15.0, **1.6**, 5.5, 14.1, 9.0, 6.1, 5.0, 4.1, −2.8.
  - 2016–2026: 5.0, 13.2, −3.4, 10.4, 6.3, 7.9, **2.9**, −1.3, 12.7, 17.7, 7.0 (2026 YTD).
- **Robustness:**
  - Costs ×2: OOS Sharpe 0.81. Costs ×3: 0.77.
  - Vol lookback of 21 or 126 days: OOS 0.84 / 0.88.
  - Gross cap 1.0 or 2.0: OOS 0.90 / 0.81.
  - Lookbacks of 3/6/12 months: OOS 0.83.
  - **Leave-one-ETF-out: OOS Sharpe 0.77–0.89.**
- **Where it does not help:** gap crashes. Feb 2018 −6.9%, Covid −6.3%, Apr 2025 −5.3%, Aug 2024 −2.5%.
- **Where it helps:** grinding bears. 2008, 2022, and Q4 2018 (−4.8% vs SPY −18.9%).
- So it is a diversifier with mild positive convexity at monthly horizons, not a day-one crash hedge. The review's tail hedge is still needed.

### 1.4 Fit with the short bot
- Correlation to P7: −0.01 daily, −0.06 monthly.
- Short bot alone: Sharpe 1.31, max DD −11.8%, worst month −7.0%. 50/50 with trend: 1.52 / −9.0% / −3.4%.
- **Capital sharing:** the short bot is active about 30 days a year at ≤ 50% gross. At $10k, run trend on about 40% of equity. Overnight gross is then about 37% long, well within Reg-T and the review's 15% stress budget.
- **Capacity:** the thinnest ETFs trade over $10M a day. A $100k sleeve at a monthly rebalance is trivial.

## 2. WATCH (paper pilot): noise-area intraday momentum on QQQ

### 2.1 Hypothesis
- Zarattini, Barbon and Aziz (2024; working paper): when price breaks out of a time-of-day noise band around the open, the move tends to run to the close.
- Mechanism: dealer gamma hedging and late-day rebalancing (Baltussen et al. 2021; Gao et al. 2018).
- Payoff: long convexity. Daily skew +2.0, monthly skew +0.31. +22% on SPY's 20 worst days and +8% on the short bot's 20 worst days.

### 2.2 Rules (`src/noise.py`)
- **Noise band:** σ_t = mean over the prior 14 sessions of |close_t / open − 1|, for each minute-of-day t.
  - UB = max(open, prior close) × (1 + σ_t)
  - LB = min(open, prior close) × (1 − σ_t)
- **Checks:** at 10:00, 10:30, …, 15:30 (bar close).
  - **When flat:** go long if price > UB; go short if price < LB.
  - **Exit:** a long exits if price < max(UB, VWAP); a short exits if price > min(LB, VWAP). It may re-enter at the same check.
  - **Fill:** the next minute's open.
  - **End of day:** flat at the official close (MOC).
- **Sizing:** notional = sleeve capital × min(2, 2% / 14-day daily vol of QQQ).
- **Skip** half days.
- **Costs:** 0.5 bp/side.

### 2.3 Results
- **IS:** CAGR 10.8%, Sharpe 0.91, max DD −15.0%.
- **OOS:** CAGR 18.3%, Sharpe 1.42, max DD −15.1%.
- **Since publication (2024-05-20):** Sharpe 1.02.
- **Per year:** 2016 −3.5%, 2017 4.8%, 2018 **51.6%**, 2019 −1.5%, 2020 15.1%, 2021 6.0%, 2022 20.5%, 2023 31.4%, 2024 22.7%, 2025 6.2%, 2026 YTD 8.1%.
- **Trades:** 237 a year, win rate 41%, average +5.1 bp (wins +48 bp, losses −25 bp). Both sides make money.
- **Parameter neighbourhood:** 26 of 26 variants are positive. OOS 0.59–1.44 across σ lookback 10/14/20 days × check step 15/30/60 min × band ×0.75/1/1.25.
- **Cost sensitivity, OOS Sharpe:** 0 bp 1.83, 0.25 bp 1.58, 0.5 bp 1.42, 1 bp 1.11, 1.5 bp 0.80, 3 bp −0.11.

### 2.4 Why WATCH
1. **It does not generalize.** On the same rules, only QQQ (1.42), XLK (1.09), SPY (0.65) and GLD (0.28) are positive OOS. IWM, DIA, MDY, XLF, XLE, XLV, TLT, EEM and EFA are negative. So the effect looks specific to mega-cap tech or to this sample.
2. **QQQ was chosen on IS** over SPY and IWM.
3. **2018 dominates IS.**
4. **DSR fails.**

The case for a pilot: a plausible mechanism, a clean OOS and post-publication record, a broad parameter plateau, the best convexity of any candidate, and no capacity limit. In the backtest, short bot + noise + trend at a third each gives Sharpe 1.97, max DD −6.2%, worst month −2.8%.

## 3. Watch list
- **Overnight-only ETF long** (Cliff, Cooper and Gulen; Kelly and Clark; Lou, Polk and Skouras):
  - The pattern is real: about 5 bp a night vs about 0 intraday.
  - But the result depends entirely on costs (on Alpaca bars, IWM OOS is 0.48 at 0.5 bp and 0.12 at 1.5 bp).
  - It is long beta (−30% max DD), so it is not a diversifier.
  - Revisit only if measured MOO/MOC slippage on the bot's own fills is under 0.5 bp.
- **12-1 stock momentum, long/short:**
  - OOS 0.52; Covid +16%, 2022 +38%, 2023 −21%.
  - It loses on the short bot's worst days (junk rallies; Daniel and Moskowitz 2016).
  - It needs about 150 names a side.
- **Lou-Polk-Skouras confirmed:** momentum winners earned +11.5 bp overnight and −1.3 bp intraday. Trading it daily in stocks costs 10.6 bp a day, which kills it.

## 4. What failed
- **Liquid losers:** the overnight bounce is +19–22 bp gross, but intraday continuation is −19 to −22 bp, and both are below the 17–30 bp round-trip cost.
- **Gap fills:** they don't pay the opening spread.
- **Residual reversal:** turnover kills it (about 175× a year); the academic result assumes institutional costs.
- **Calendar effects:** turn of month is thin, FOMC drift is gone since 2016, and a part-time long sleeve beats neither SPY's Sharpe nor its CAGR.
- **Earnings drift:** IS mostly survivorship (today's S&P 500 list); it decays OOS (Martineau 2022).
- **Short interest, 52-week high, tug-of-war, Gao et al., and 5-minute ORB:** all fail.

## 5. Caveats
- **Survivorship:** the stock panel includes 939 delisted names Alpaca still serves, but others are missing. The earnings test uses today's S&P 500. Neither affects the ETF candidates.
- **Bad prints:** a filter removed spike-and-revert glitches (for example AQB on 2017-01-09). Without it, the losers family showed a fake +1,800 bp per trade.
- **Yahoo vs Alpaca:** the two price sources agree on 2016–26 (daily close-to-close correlation ≥ 0.96, median absolute difference ≤ 1.6 bp).
- **Short-bot reference:** correlations use the P7 backtest. The live bot now runs 5% per name with a 35% stop.
- **Idle cash:** no interest is credited.

## 6. References

LibKey-resolved:
- Moskowitz, Ooi & Pedersen (2012), JFE 104(2). DOI 10.1016/j.jfineco.2011.11.003. [Download PDF](https://libkey.io/libraries/545/articles/19074553/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Hurst, Ooi & Pedersen (2017), JPM 44(1). DOI 10.3905/jpm.2017.44.1.015. [Access options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Hurst&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=A%20Century%20of%20Evidence%20on%20Trend-Following%20Investing&volume=44&issue=1&spage=15&epage=29&date=2017-10-31&rft_id=info:doi/10.3905%2Fjpm.2017.44.1.015&rft_id=info:pmid/&sid=LibKey)
- Fung & Hsieh (2001), RFS 14(2). DOI 10.1093/rfs/14.2.313. [Download PDF](https://libkey.io/libraries/545/articles/27130243/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Lou, Polk & Skouras (2019), JFE 134(1). DOI 10.1016/j.jfineco.2019.03.011. [Download PDF](https://libkey.io/libraries/545/articles/302597070/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Gao, Han, Li & Zhou (2018), JFE 129(2). DOI 10.1016/j.jfineco.2018.05.009. [Download PDF](https://libkey.io/libraries/545/articles/207215657/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Lucca & Moench (2015), JF 70(1). DOI 10.1111/jofi.12196. [Download PDF](https://libkey.io/libraries/545/articles/52436790/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Lakonishok & Smidt (1988), RFS 1(4). DOI 10.1093/rfs/1.4.403. Access options via OpenURL with that DOI.
- Ariel (1987), JFE 18(1). DOI 10.1016/0304-405X(87)90066-3. Access options via OpenURL.
- McConnell & Xu (2008), FAJ 64(2). DOI 10.2469/faj.v64.n2.11. [Download PDF](https://libkey.io/libraries/545/articles/100473773/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Bouman & Jacobsen (2002), AER 92(5). DOI 10.1257/000282802762024683. [Download PDF](https://libkey.io/libraries/545/articles/47559766/full-text-file?utm_source=api_4212&allow_speedbump=true)
- George & Hwang (2004), JF 59(5). DOI 10.1111/j.1540-6261.2004.00695.x. [Download PDF](https://libkey.io/libraries/545/articles/29955007/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Moskowitz & Grinblatt (1999), JF 54(4). DOI 10.1111/0022-1082.00146. [Download PDF](https://libkey.io/libraries/545/articles/29954467/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Asness, Moskowitz & Pedersen (2013), JF 68(3). DOI 10.1111/jofi.12021. [Download PDF](https://libkey.io/libraries/545/articles/35274697/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Daniel & Moskowitz (2016), JFE 122(2). DOI 10.1016/j.jfineco.2015.12.002. [Download PDF](https://libkey.io/libraries/545/articles/60246770/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Jegadeesh (1990), JF 45(3). DOI 10.1111/j.1540-6261.1990.tb05110.x. Access options via OpenURL.
- Nagel (2012), RFS 25(7). DOI 10.1093/rfs/hhs066. [Download PDF](https://libkey.io/libraries/545/articles/27131527/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Blitz, Huij, Lansdorp & Verbeek (2013), JFM 16(3). DOI 10.1016/j.finmar.2012.10.005. [Download PDF](https://libkey.io/libraries/545/articles/32560944/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Chan, Jegadeesh & Lakonishok (1996), JF 51(5). DOI 10.1111/j.1540-6261.1996.tb05222.x. Access options via OpenURL.
- Martineau (2022), CFR 11(3–4). DOI 10.1561/104.00000122. Access options via OpenURL.
- Novy-Marx & Velikov (2016), RFS 29(1). DOI 10.1093/rfs/hhv063. [Download PDF](https://libkey.io/libraries/545/articles/57275731/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Bailey & López de Prado (2014), JPM 40(5). DOI 10.3905/jpm.2014.40.5.094. Access options via OpenURL.

Not LibKey-resolved: Zarattini, Barbon & Aziz (2024), SSRN 4824172; Zarattini & Aziz (2023), SSRN 4416622; Baltussen, Da, Lammers & Martens (2021), JFE 142(1); Ariel (1990), JF 45(5); Asquith, Pathak & Ritter (2005), JFE 78(2); Boehmer, Huszar & Jordan (2010), JFE 96(1); Akbas, Boehmer, Jiang & Koch (2022), JFE; Brandt, Kishore, Santa-Clara & Venkatachalam (2008), working paper; Cliff, Cooper & Gulen (2008), working paper; Kelly & Clark (2011), J. Asset Management 12(2); Antonacci (2014), book. The first guessed DOIs for Asquith and Akbas resolved to different papers, so they are listed as unresolved.

## 7. Reproduce
From `src/`, run:

```
build_panel → fetch_etfs → fetch_minute_etf [SYMS] → f1_calendar → f2_overnight → f3_trend → f4_losers
→ F5_CS=0.5e-4 f5_intraday → f6_xsection → f9_earnings → f5b_noise_deep → f3_trend_deep → analyze → charts
```

Caches go to `.cache/` (gitignored). The FOMC dates came from federalreserve.gov; unscheduled releases are excluded by an explicit list.
