# Long-history validation of options regime filters (2007-01 to 2026-10)

Code and outputs: `Options Bot/longhist/` (`data.py`, `engine.py`, `study.py`, `charts.py`; tables in `out/*.csv`, charts in `out/*.png`). Run with `python3 -I study.py && python3 -I charts.py`.

## TL;DR

1. **Entry gates applied only at the monthly roll do not help out of sample. 0 of 13 families (and 1 of 43 grid variants) beat the plain PUT index in 2017-2026.** That covers VRP>0, VIX/VIX3M<1.0/0.95/0.90, the VIX9D/VIX block, SPX>200DMA, the OR/AND combos, the VIX>40 gate, the DD+backwardation block, vol targeting and VIX-median sizing. The earlier in-house numbers (for example "TS<1 AND VRP>0, Sharpe 1.01") came from free daily switching with cash at 0%. Under monthly-roll switching with T-bills when out, a 0.10% switching cost and a 1-day lag, the same rule has a Sharpe of **0.32 vs 0.46 for the baseline** (2007-2026). The mechanism: a monthly gate can't get out before a fast crash. By the next roll, VIX is high and the term structure is inverted, so the gate stays out and **misses the post-crash premium, which is the richest of the cycle.** In COVID (Feb 19 to Apr 30, 2020), every roll gate lost **-25% to -27%**, against -18% for the baseline.
2. **The only rules that helped out of sample are intramonth emergency exits.** Each one closes the short put on any day after a stress signal and re-enters only at the next roll. Out of sample (2017-2026), with the parameters chosen on 2007-2016:

   | rule (IS-chosen) | OOS Sharpe | vs base 0.50 | OOS CAGR | OOS max DD |
   |---|---|---|---|---|
   | exit when VIX/VIX3M > 1.2 | 0.68 | +0.17 | 9.0% | -16.0% |
   | exit when VIX > 40 | 0.64 | +0.13 | 8.5% | -16.0% |
   | stop at -8% since roll | 0.79 | +0.28 | 10.2% | -15.7% |

   The baseline had an OOS CAGR of 8.5% and an OOS max DD of -28.9%. These exits beat baseline Sharpe in 56-66% of rolling 5-year windows.
3. **None of this clears the multiple-testing bar.** There were 64 PUT variants (212 in all). The White reality check on OOS Sharpe gains gives **p = 0.36**. The individual block-bootstrap p-values are 0.08 to 0.22, so 0 of 17 selected filters are significant at 5%. The whole gain of the exit rules comes from **2 events, 2008 and 2020**. Remove Sep 2008 to Mar 2009 and Feb to Apr 2020, and the Sharpe change is between -0.09 and +0.03. Treat these exits as **cheap crash insurance that paid off twice, not as alpha**.
4. **Weekly put-writing (WPUT) is clearly worse than monthly.** Its OOS Sharpe is 0.16 vs 0.50 for PUT, and no weekly gate fixes it (the best is TS<1.1 at 0.22). Faster reaction doesn't make up for the weaker premium.
5. **Recommended for the live bot:** keep selling monthly (30-45 DTE) index puts with **no entry regime gate**. Add a hard intramonth kill switch: `VIX/VIX3M > 1.15 OR VIX > 40 OR position loss > 8% of notional since entry → close, no re-entry until the next monthly cycle with VIX/VIX3M ≤ 1.15`. Expect about 2-3 switches a year, little effect in normal years, and roughly half the max drawdown in a 2008 or 2020-type crash.

## 1. Data and method

| item | detail |
|---|---|
| Data | All 15 CBOE CSVs downloaded (HTTP 200 after a 307 redirect; **none 404'd**): VIX, VIX3M (2009-09), VIX9D (2011-01), VIX6M (2008-01), VVIX, SKEW, SPX, PUT, BXM, BXMD, CNDR, WPUT, PPUT, CLL (2008-08), BFLY. FRED DTB3 was used for cash. Cached in `longhist/.cache/`. |
| Sample | 2007-01-03 to 2026-10-20. In-sample (IS) is 2007-2016; out-of-sample (OOS) is 2017-2026. |
| VIX3M before 2009-09 | Proxy: log-linear fit of VIX3M on VIX and VIX6M over the overlap (sign agreement with the real ratio: 98% at 1.0 and 99.7% at 1.10). This uses a little look-ahead in the proxy coefficients, only for 2008-01 to 2009-09. **2007 has no term structure, so TS filters are inactive (invested) in 2007.** **VIX9D filters are inactive before 2011, so their IS sample has no 2008.** |
| Switching | Exposure changes only on the monthly roll: 3rd Friday, or the prior trading day if that Friday is a holiday (238 rolls). For WPUT, the roll is the last trading day of each week. Decisions use the close of r-1. Out of the market means T-bills. Cost is 0.10% × \|Δexposure\|. Return = cash + e·(R_index − cash), so 1.5x means 1.5x the overlay on the same collateral. |
| Intramonth exits | The signal at close t-1 triggers a buy-back during day t, and day t's return is still earned (a conservative 2-day effective lag). Exit cost is 0.30% of notional; 0.5% and 1.0% were tested as sensitivities. Re-entry happens only at a roll, when the condition is clear. |
| RV | SPX 21-day close-to-close (Yang-Zhang isn't possible with close-only SPX). |
| Selection | For each family, the parameter with the best IS Sharpe is chosen and frozen, then evaluated OOS. The OOS result for every grid point is in `out/walkforward.csv` (`grid_OOS_Sharpe`). |
| Benchmark | SPX is **price only**. The "SPX +1.8%/yr" series adds a flat 1.8%/yr dividend approximation and **is not a true total return**. |
| Sharpe/Sortino | Daily returns in excess of T-bills, annualized with √252. |

## 2. Baselines (2007-01 to 2026-10, always invested)

| index | CAGR | vol | Sharpe | max DD | IS Sharpe | OOS Sharpe |
|---|---|---|---|---|---|---|
| PUT (monthly ATM put-write) | 7.3% | 13.8% | 0.46 | -37.1% | 0.43 | 0.50 |
| WPUT (weekly put-write) | 4.4% | 12.4%* | 0.29 | -28.6% | 0.41 | 0.16 |
| BXM (buy-write) | 6.1% | 14.4%* | 0.37 | -40.1% | 0.31 | 0.45 |
| CNDR (iron condor) | 1.0% | 7.6%* | -0.04 | -19.7% | 0.06 | -0.15 |
| SPX price | 8.9% | 19.6% | 0.45 | -56.8% | | 0.65 |
| SPX +1.8% div (approx) | 10.9% | 19.6% | 0.55 | -55.7% | | 0.75 |

\*2008+ figures. The sanity check matches the prior in-house PUT 2008-2026 numbers: CAGR 7.2%, max DD -37.1%. Other indexes were downloaded but not tested as filter targets: BXMD (0.49 Sharpe 2008+), PPUT (0.57, a protective put, not a premium-selling strategy), CLL (0.46), and BFLY (-0.28). **CNDR has negative excess return over the full period, so the filters can't rescue condors.**

## 3. Walk-forward results, PUT monthly (chosen on 2007-2016, frozen, tested on 2017-2026)

The parenthetical OOS max DD is a **static mix with the same average exposure** (Sharpe doesn't depend on leverage, but drawdown does). Placebo p is the share of 1000 random circular time-shifts of the filter's own exposure that reach its OOS Sharpe. Boot p is a one-sided stationary block bootstrap (63-day blocks) of the OOS Sharpe difference vs baseline, with a 90% CI. The roll5y column shows the share of rolling 5-year windows (178, monthly steps) in which the filter's Sharpe beats the baseline, and the median ΔSharpe.

Baseline OOS: Sharpe **0.50**, CAGR 8.5%, max DD -28.9%.

| filter [IS-chosen param] | IS Sh | OOS Sh | ΔOOS | OOS CAGR | OOS maxDD (static) | % in | placebo p | boot p [90% CI] | roll5y win / med Δ |
|---|---|---|---|---|---|---|---|---|---|
| VRP>x [-2] | 0.79 | 0.33 | -0.18 | 6.0% | -27.2% (-26.7%) | 92% | 0.99 | 0.99 [-0.30,-0.05] | 13% / -0.11 |
| VIX/VIX3M<x [1.1] | 0.66 | 0.49 | -0.02 | 8.2% | -28.9% (-28.2%) | 99% | 0.87 | 0.83 [-0.06,+0.00] | 13% / -0.03 |
| VIX9D/VIX>x block [1.2] | 0.41 | 0.44 | -0.06 | 7.6% | -28.3% (-28.7%) | 99% | 1.00 | 0.91 [-0.15,+0.00] | 0% / -0.07 |
| SPX>SMAx [100] | 0.51 | 0.38 | -0.13 | 6.1% | -28.2% (-21.7%) | 80% | 0.60 | 0.81 [-0.36,+0.28] | 24% / -0.21 |
| SMA200 OR TS<x [0.9] | 0.53 | 0.33 | -0.17 | 5.7% | -28.2% (-23.3%) | 84% | 0.88 | 0.90 [-0.40,+0.16] | 29% / -0.15 |
| SMA200 AND TS<x [1.05] | 0.46 | 0.50 | -0.00 | 7.6% | -28.2% (-22.8%) | 83% | 0.26 | 0.53 [-0.26,+0.48] | 27% / -0.09 |
| TS<a AND VRP>b [1.05/0] | 0.52 | 0.37 | -0.13 | 6.3% | -27.2% (-24.2%) | 85% | 0.69 | 0.92 [-0.30,+0.12] | 15% / -0.19 |
| TS<a AND 9D/VIX≤b [0.95/1.1] | 0.41 | 0.31 | -0.20 | 5.4% | -27.2% (-22.5%) | 80% | 0.80 | 0.96 [-0.38,+0.13] | 13% / -0.21 |
| VIX≤x gate [50] | 0.53 | 0.44 | -0.06 | 7.6% | -28.3% (-28.5%) | 99% | 1.00 | 0.91 [-0.15,+0.00] | 15% / +0.00 |
| not(VIX>x & TS>1) [30] | 0.65 | 0.38 | -0.12 | 6.8% | -27.2% (-27.6%) | 98% | 1.00 | 0.95 [-0.24,-0.00] | 13% / -0.08 |
| DD>x & TS>1 block, half size when deep [0.15] | 0.60 | 0.35 | -0.15 | 6.2% | -27.3% (-26.7%) | 98% | 1.00 | 0.96 [-0.30,-0.02] | 13% / -0.05 |
| Vol-target x / PUT 63d vol, cap 1.5 [0.12] | 0.48 | 0.33 | -0.18 | 6.8% | -39.3% (-34.2%) | 100% | 1.00 | 0.97 [-0.34,+0.04] | 26% / -0.18 |
| VIXmed252/VIX, cap [1.0] | 0.46 | 0.40 | -0.10 | 6.6% | -25.4% (-26.0%) | 100% | 0.97 | 0.92 [-0.22,+0.03] | 22% / -0.07 |
| **Exit anytime VIX/VIX3M > x [1.2]** | 0.81 | **0.68** | **+0.17** | 9.0% | **-16.0%** (-28.3%) | 98% | 0.01 | 0.16 [-0.13,+0.49] | 59% / +0.18 |
| **Exit anytime VIX > x [40]** | 0.68 | **0.64** | **+0.13** | 8.5% | **-16.0%** (-27.3%) | 97% | 0.02 | 0.22 [-0.20,+0.43] | 56% / +0.10 |
| Exit anytime VIX9D/VIX > x [1.2] | 0.41 | 0.79 | +0.28 | 9.8% | -16.0% (-27.9%) | 96% | 0.01 | 0.11 [-0.11,+0.66] | 66% / +0.13 |
| **Stop: index −x since roll [0.08]** | 0.59 | **0.79** | **+0.28** | 10.2% | **-15.7%** (-28.6%) | 99% | 0.00 | 0.08 [-0.03,+0.63] | 64% / +0.10 |

Reading the table:
- **Roll-gated entry filters:** IS gains are real but come from 2008 alone. The 2008 crash unfolded over several months, so a monthly gate could get out in time. OOS, all of them lose Sharpe and give **no drawdown benefit beyond simply holding less exposure**: the static mix with the same exposure has an equal or shallower DD in 9 of 13 families. The 100/200DMA and OR/AND gates cut OOS DD only to -28%, against -22% to -23% for a static mix with the same exposure.
- **VIX9D/VIX exit:** its IS (2011-2016, no 2008) was below baseline (0.41 vs 0.43), so a strict walk-forward would **not have adopted it**. Its OOS result is luck from Feb 2018 and 2020.
- **Threshold cliffs:** the TS exit at 1.0 gives OOS 0.40, at 1.05 gives 0.42, at 1.10 gives 0.67 and at 1.20 gives 0.68. The VIX exit at 30 gives 0.24, at 35 gives 0.36, at 40 gives 0.64 and at 50 gives 0.69. Only **loose** thresholds work; tight ones whipsaw. The good range is wide (TS 1.1-1.2, VIX 40-50), which is reassuring, but the cliff below it is the real risk.
- **Exit cost sensitivity** (TS>1.10 exit): Sharpe is 0.73 at 0.3% cost, 0.71 at 0.5% and 0.67 at 1.0%. The result survives realistic crisis bid-ask spreads.
- **Combinations add nothing:** TS<1.05 entry plus TS>1.10 exit gives 0.71 full / 0.63 OOS, vs 0.73 / 0.67 for the exit alone. Exit on TS>1.10 OR VIX>40 gives 0.63 / 0.54.

## 4. Full-sample stats (2007-2026) for the key variants

| strategy | CAGR | vol | Sharpe | Sortino | max DD | worst month | % invested |
|---|---|---|---|---|---|---|---|
| PUT baseline | 7.3% | 13.8% | 0.46 | 0.63 | -37.1% | -17.7% | 100% |
| gate VIX/VIX3M<1.0 @roll | 4.9% | 11.2% | 0.34 | 0.45 | -29.6% | -18.2% | 87% |
| gate SPX>200DMA @roll | 5.9% | 9.6% | 0.48 | 0.62 | -28.2% | -18.2% | 76% |
| gate TS<1 AND VRP>0 @roll | 4.4% | 10.4% | 0.32 | 0.41 | -27.2% | -18.2% | 75% |
| gate 200DMA OR TS<0.95 @roll | 6.3% | 10.4% | 0.49 | 0.63 | -27.2% | -18.2% | 85% |
| VIX≤40 gate @roll | 6.5% | 12.6% | 0.44 | 0.58 | -27.2% | -18.2% | 96% |
| vol-target 10% (cap 1.5x) | 5.7% | 14.1% | 0.35 | 0.45 | -39.3% | -26.8% | 100% (avg 1.09x) |
| VIXmed/VIX (cap 1.5x) | 6.4% | 12.1% | 0.44 | 0.58 | -27.6% | -15.9% | 100% (avg 0.99x) |
| **exit VIX/VIX3M>1.2 anytime** | **9.5%** | 10.8% | **0.75** | **1.01** | **-16.0%** | -9.0% | 98% |
| exit VIX/VIX3M>1.1 anytime | 8.6% | 9.8% | 0.73 | 0.98 | -19.5% | -8.3% | 94% |
| **exit VIX>40 anytime** | 8.0% | 10.1% | 0.66 | 0.87 | **-16.0%** | -8.7% | 94% |
| stop −8% since roll | 8.9% | 11.3% | 0.67 | 0.91 | -27.1% | -16.7% | 99% |
| SPX price | 8.9% | 19.6% | 0.45 | 0.63 | -56.8% | -16.9% | 100% |
| SPX +1.8% div (approx TR) | 10.9% | 19.6% | 0.55 | 0.76 | -55.7% | -16.8% | 100% |

Roll gates make the worst month worse (-18.2% vs -17.7%). That month is March 2020: the gate is fully exposed into the crash, then sits in cash for the rebound.

## 5. Crash windows (total return over the window, %)

| strategy | GFC Sep08-Mar09 | Aug 2011 | Aug15-Feb16 | Feb 2018 | Q4 2018 | COVID Feb19-Apr30 2020 | 2022 | Apr 2025 |
|---|---|---|---|---|---|---|---|---|
| PUT baseline | -24.7 | -14.6 | -2.8 | -2.3 | -11.3 | -18.3 | -7.7 | -1.6 |
| gate VIX/VIX3M<1.0 | -7.0 | -17.3 | -4.8 | -1.9 | -12.0 | **-26.7** | -7.7 | -4.4 |
| gate SPX>200DMA | +0.2 | -11.8 | -3.6 | -2.3 | -4.2 | **-26.7** | -1.0 | +0.4 |
| gate VRP>-2 | +14.4 | -17.3 | -4.0 | -1.9 | -11.3 | **-26.7** | -7.6 | -4.4 |
| vol-target 12% | -12.9 | -18.8 | -5.5 | -3.5 | -12.8 | **-37.0** | -10.4 | -3.4 |
| **exit TS>1.2 anytime** | **+9.4** | -12.7 | -9.5 | -4.4 | -12.7 | **-5.5** | -7.7 | -5.9 |
| **exit VIX>40 anytime** | **-2.7** | -13.5 | -9.5 | -2.3 | -11.3 | **-6.3** | -7.7 | -5.9 |
| stop −8% since roll | -15.4 | -12.7 | -2.8 | -2.3 | -11.3 | **-0.5** | -7.4 | -5.9 |
| SPX +1.8% div approx | -37.1 | -17.9 | -7.2 | -4.3 | -13.6 | -13.3 | -18.0 | -0.6 |

(Grid-wide figures are in `out/all_results.csv`, in the `crash_*` columns.)

- The **exit rules win only the two crashes where losses kept piling up for weeks after the signal** (2008, 2020). They cost 1-7 points in the V-shaped shocks (2015-16, Feb 2018, Apr 2025), where they sell at the low and miss the snapback until the next roll.
- **Grinding bear markets** (2022, Q4 2018) are slow and never inverted the term structure far enough, so no VIX-based rule triggered. In 2022, only the 200DMA gate helped (-1.0% vs -7.7%), and that same gate gave up 8 points in COVID.
- **Concentration check** (ΔSharpe vs baseline after removing Sep08-Mar09 and Feb-Apr 2020): exit TS>1.2 is -0.01, exit VIX>40 is -0.09, stop -8% is +0.03, and exit TS>1.1 is -0.03. Outside those two events the exits are neutral to a small drag, and they beat baseline in only 3-4 of 20 calendar years.

## 6. Weekly decision variant (WPUT, a gate can switch every Friday)

| filter | WPUT IS Sh | WPUT OOS Sh | WPUT max DD | PUT OOS Sh (monthly) |
|---|---|---|---|---|
| Baseline | 0.41 | **0.16** | -28.6% | **0.50** |
| VIX/VIX3M<1.1 (IS-best) | 0.72 | 0.22 | -20.6% | 0.49 |
| VIX/VIX3M<1.0 | 0.55 | -0.05 | -27.5% | 0.30 |
| SPX>200DMA | 0.45 | 0.22 | -19.1% | 0.50 |
| 200DMA OR TS<0.95 | 0.58 | 0.11 | -18.9% | 0.48 |
| TS<1 AND VRP>0 | 0.47 | 0.01 | -21.8% | 0.32 |
| VIX9D/VIX>1.1 block | 0.38 | 0.15 | -28.6% | 0.40 |

With weekly switching, the TS<1.1 gate cuts the COVID loss from -12.2% to -1.2% and the GFC loss from -22.5% to -1.8%. That confirms the problem with monthly gates is reaction speed. Even so, **10 of 43 weekly variants beat the WPUT baseline OOS, and none come close to plain monthly PUT.** Weekly premium is too thin after 2017. Within the R15 comparison (cadence under the same gate), **monthly wins in every case.**

## 7. Other indexes (IS-chosen parameters, OOS ΔSharpe vs that index's baseline)

- **BXM:** roll gates have a median ΔOOS of -0.10; only SMA200 AND TS<1.05 is positive (+0.03). The exits carry over: TS>1.2 gives +0.14, VIX>40 gives +0.13, the -5% stop gives +0.21, and 9D/VIX>1.2 gives +0.26. So the exit effect isn't specific to PUT.
- **CNDR:** everything stays at or below zero Sharpe OOS. Regime filters can't fix a structure with no edge.

## 8. Multiple-testing accounting

| test | result |
|---|---|
| Variants tried | 64 on PUT (including baseline and diagnostics), 212 across PUT, WPUT, BXM, CNDR and SPX |
| Grid variants beating baseline OOS Sharpe | Roll-gated entry/size: **1 of 43** (VRP>4, which was IS-rejected). Intramonth exits: 12 of 16. WPUT: 10 of 43 |
| Selected filters with block-bootstrap p < 0.05 (OOS ΔSharpe) | **0 of 17** (about 0.85 expected by chance) |
| White reality check (max ΔSharpe over 17 selected, OOS) | **p = 0.36**, so the best filter is not distinguishable from data-snooping luck |
| Timing placebo (circular shift) | 4 of 17 have p < 0.05, all of them exit rules. This shows that **when** they act matters, but the effective sample is about 2 events. |
| Deflated Sharpe (Bailey-López de Prado, N=64, full sample, monthly) | Expected max SR under the null is 0.35 annualized. DSR: exit TS>1.2 0.98, exit TS>1.1 0.96, baseline 0.79, 200DMA gate 0.74, TS<1.0 gate 0.53. The exits are the only variants with a high DSR, but DSR tests SR>0 after selection, not SR > baseline. |

**Verdict:** no filter has a statistically established out-of-sample edge. The exit rules have the right sign, a plausible mechanism, results that carry over to BXM, robustness to exit costs, and a broad stable threshold region. Their evidence is still about 2 independent crash episodes. Adopt them as risk limits sized for survivability, not as a return source.

## 9. Recommended rules for the live options bot

1. **Core:** sell monthly-cycle (30-45 DTE, enter near the monthly roll) SPY/SPX puts, or put spreads. **Don't use weekly cycles.** The OOS Sharpe for weekly is a third of monthly.
2. **No entry regime gate at the roll.** Drop VRP>0, VIX/VIX3M<1.0/0.95, 200DMA, AND/OR combos, the DD+TS block and the VIX9D block as entry gates. Out of sample they cost 0.1-0.2 Sharpe and miss post-crash premium. The doc rules R1, R2, R3, R5, R13 and R15 (gated) are **not validated**. Update `regime_indicators.md` §5 and §15: its switch-test table used free daily switching.
3. **Intramonth kill switch (adopt):** close all short premium the day after any of these at the close:
   - `VIX/VIX3M > 1.15` (validated range 1.10-1.20; **never use 1.0-1.05**, which whipsaws),
   - `VIX > 40` (validated range 40-50; 30-35 whipsaws),
   - the position has lost ≥ 8% of notional since entry (roughly the PUT index falling 8%; for spreads, scale to the max loss).
   Re-enter only at the next monthly entry date, and only when `VIX/VIX3M ≤ 1.15` and `VIX ≤ 40`. Expected trigger count is about 1-2 a year. It cuts a 2008 or 2020-style max DD from -37% to about -16%, and costs roughly 0.5-1% in a year with a V-shaped scare (2015, 2018, Apr 2025).
4. **No vol-target or VIX-ratio sizing.** It was worse than flat sizing in every OOS cut, and vol-target 12% raised max DD to -39%. If you want lower risk, run a smaller **constant** size. A static 77% allocation matches the DD of the 200DMA gate with the baseline's Sharpe.
5. **Grinding bear markets (2022-type) aren't caught by any VIX rule.** Use defined-risk spreads, not naked puts, so the slow bleed is capped by structure rather than by a timing signal.
6. On the Alpaca 2024-02+ window, validate only execution and fills for rule 3: buy-back slippage at VIX > 30. The study assumed 0.3% of notional and showed the result holds up to 1.0%.

## 10. Caveats

- CBOE indexes have no commissions and mark at index methodology prices. A stress buy-back at the close is assumed to fill near the index mark plus 0.3-1.0%.
- VIX3M is a proxy for 2008-01 to 2009-09, so the 2008 TS exits depend on it. 2007 has no TS signal, and VIX9D has none before 2011.
- SPX is price only; "+1.8%/yr" is a flat approximation, not total return.
- The exits re-enter only at the roll. A faster re-entry (for example weekly once TS < 1) wasn't tested and is the obvious next experiment, but it adds degrees of freedom.
- OOS is 118 months containing only one fast crash (2020). Statistical power is low either way. The negative verdict on roll gates is firmer: 13 of 13 families lost Sharpe, and the bootstrap CIs exclude a gain for VRP, the DD block and the VIX/TS gate.

## Charts (`Options Bot/longhist/out/`)
- `equity_log.png`: growth of $1 on a log scale. Baseline PUT 4.02x; exit TS>1.2 5.98x; stop -8% 5.39x; 200DMA gate 3.11x; TS<1.0 gate 2.58x; SPX approx TR 7.76x.
- `underwater.png`: drawdowns for baseline, the two exits and the two roll gates.
- `crash_windows.png`: returns in each crash window.
- `rolling_sharpe_3y.png`: rolling 3-year Sharpe with the OOS period shaded.
