# Build spec: third sleeve (trend) and optional pilot (QQQ noise momentum)

Status: spec only. Nothing here is built or deployed. Evidence: `REPORT.md`.

## A. Trend sleeve (GO for paper)

### A.1 Module and schedule
- **Code:** a new package, e.g. `Trend Sleeve/trendsleeve/`. Do not import from or modify `shortingbot/`.
- **Shared code:** reuse `broker.py` patterns for keys, retries and order ids.
- **Cron / timer:** one job on trading days at 15:40 ET.
  - It acts only on the last trading day of the month. On other days it only writes snapshots and monitoring.
  - Use the existing market calendar.

### A.2 Signal (computed at the 15:40 job of the last trading day)
- **Data:** Alpaca daily bars (`adjustment=all`) for the 20 ETFs plus SHY, up to the **prior** close (the second-to-last trading day). This matches the backtest's one-day signal lag.
- **Signal:** for each ETF, s = mean over L ∈ {21, 63, 252} of 1[c_t / c_{t−L} − 1 > SHY_t / SHY_{t−L} − 1].
- **Volatility:** σ = stdev(daily returns over the last 63 days) × √252.
- **Weights:**
  - w = s × 0.10 / σ / √N, where N is the number of ETFs with ≥ 273 days of history.
  - If Σw > 1.5, scale all weights down so Σw = 1.5.
- **Logging:** save the signal, σ and w to the DB with a run id.

### A.3 Orders
- **Target dollars:** sleeve_equity × w. sleeve_equity = TREND_ALLOC (default 0.40) × account equity at 15:40, capped by available buying power after the short bot's open positions.
- **Delta:** target minus current position for each ETF. Skip deltas under $25 or under 0.5% of sleeve equity.
- **Order types:**
  - Whole-share part: MOC (`time_in_force=cls`), submitted before the 15:50 cutoff.
  - Fractional remainder: a DAY marketable limit at NBBO ± 5 bp at 15:45. Alpaca fractional orders may not support `cls` [verify].
- **Sequence:** sells first, then buys.
- **Fills:** read fills after 16:05. Log the implementation shortfall vs the official close.

### A.4 Risk
- Long only, no shorts, no leverage. Σw ≤ 1.5 of sleeve equity, which is ≤ 0.6 of account equity at a 0.40 allocation.
- **Account-level interaction** with the review's kill-switch ladder (§8.5):
  - On a daily −3% account loss: the trend sleeve does nothing intraday (it is a monthly book).
  - At −10% drawdown: halve TREND_ALLOC at the next rebalance.
  - At −15%: flatten everything at the next close.
- **Monitoring:** daily sleeve P&L, gross, number of active ETFs, and turnover.
  - Alert if realized 20-day vol exceeds 2× the target, or if drift from target weights exceeds 25%.
  - **Optional mid-month vol brake:** if 20-day sleeve vol > 20% annualized, scale to half. Not backtested, so off by default.

### A.5 Validation before trusting it
- **Shadow mode:** for one month, compute weights without trading. Confirm they match `src/f3_trend.py` run on the same data to within 1e-6.
- **Paper trading:** run 6+ months.
- **Expected:** about 9× a year turnover, slippage under 3 bp a side, monthly P&L correlation with the backtest replay above 0.95.
- **Kill criterion:** a rolling 12-month return more than 2.5 SD below the backtest mean (about −14%) triggers a review.

### A.6 Config defaults

```
TREND_ENABLED=0
TREND_ALLOC=0.40
TREND_UNIVERSE=[SPY,IWM,EFA,EEM,EWJ,TLT,IEF,LQD,HYG,TIP,GLD,SLV,DBC,USO,UNG,DBA,UUP,FXE,FXY,VNQ]
TREND_LOOKBACKS=[21,63,252]
TREND_VOL_LB=63
TREND_ASSET_VOL=0.10
TREND_GROSS_CAP=1.5
TREND_RF_PROXY=SHY
TREND_MIN_ORDER=25
```

## B. QQQ noise-momentum pilot (WATCH, paper only, small)

### B.1 Schedule
- Jobs at 10:00:05 and every 30 minutes through 15:30:05 ET, plus a 15:49 MOC flatten safety job.
- Skip half days.

### B.2 State
- At 09:29, compute from the prior 14 sessions of 1-minute QQQ bars: σ_t for each check minute t, and dvol14 (the 14-day stdev of daily returns).

### B.3 Logic at each check
1. Read the last completed 1-minute bar close p, the session VWAP, the 09:30 open, and the prior close.
2. UB = max(open, prior close) × (1 + σ_t); LB = min(open, prior close) × (1 − σ_t).
3. Exits: a long exits if p < max(UB, VWAP); a short exits if p > min(LB, VWAP).
4. When flat: go long if p > UB; go short if p < LB.
5. **Size:** notional = NOISE_ALLOC × account equity × min(2, 0.02 / dvol14).
6. **Orders:** marketable limits at NBBO ± 2 bp, IOC. Retry once, then use a market order.
7. **15:50:** submit MOC to flatten any open position. As a fallback, market at 15:57.
8. Shorting QQQ requires ETB. Check the asset flags and skip the short if QQQ is not shortable.

### B.4 Size and limits
- NOISE_ALLOC = 0.15 of equity. Maximum intraday notional is 0.30 of equity.
- Never hold overnight.
- **PDT:** each entry/exit is a day trade. Share the short bot's PDT logic (`PDT_ENFORCE="auto"`) and its day-trade counter [verify for the live account].

### B.5 Pilot success and kill criteria
- **Logging:** decision price, fill and slippage on every order.
- **Kill if:**
  - Median slippage exceeds 1 bp a side after 50 trades (the edge falls to OOS Sharpe 0.8 at 1.5 bp and turns negative at 3 bp).
  - Or the pilot loses more than 8% of sleeve capital (the backtest's 95th-percentile 3-month loss).
- **Graduate** only after 6 months with slippage under 1 bp a side and a Sharpe above 0.

## C. Not to build
- Overnight ETF long, stock momentum long/short, calendar, losers/gaps, reversal, short interest, earnings drift: see REPORT §§3–4.
