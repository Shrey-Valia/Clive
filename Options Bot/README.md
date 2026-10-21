# Options Bot: options-strategy backtester (Alpaca data)

This is a daily-bar backtester for income and hedged option strategies on large-cap stocks and ETFs. It uses Alpaca historical option bars, the option-contract reference list and SIP stock bars.

**Data only.** The code never places an order. `optionsbot/data.py` only issues GETs, and it asserts that the URL contains no `/orders` or `/positions`. The API keys are read at runtime from `../Stock Shorting Bot/.env` (or from the environment). They are never copied or printed.

```
Options Bot/
  backtest.py            CLI: main grid, Cboe calibration, sensitivity grid, summary, dashboard
  optionsbot/
    data.py              Alpaca client + disk cache (.cache/), contracts / option bars / stock bars / dividends
    pricing.py           Black-Scholes(-Merton) price, delta, vega, IV inversion; rate & dividend-yield assumptions
    engine.py            Market (chain, contract selection, VIX data) + Engine (fills, marks, expiry, capital)
    strategies.py        Parameterized strategies + REGISTRY
    metrics.py           CAGR, vol, Sharpe, Sortino, drawdown, trade stats, exposure, vs-benchmark
    runner.py            Programmatic API: run() / run_one()
    indicators.py        (from another agent; not used by the core engine)
  tests/                 pytest: BS/IV round trip, expiry settlement, assignment, spread max loss, capital check
  results/               outputs (see below)
  research/              research notes written by other agents (not touched here)
```

## Running it

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
$PY backtest.py                                   # main + calibration + sensitivity + summary + dashboard
$PY backtest.py --steps main,summary,dashboard --symbols SPY,QQQ --strategies csp,put_credit_spread
$PY backtest.py --steps sensitivity,summary --grid-symbols SPY,QQQ,IWM --workers 3
$PY -m pytest -q tests/
```

The first full run takes a while: roughly 5-8k API calls at ~190 req/min, split across workers. Every response is cached under `.cache/` (gitignored), so a re-run is mostly CPU, and results are deterministic for a given cache.

## Programmatic API (for tuning agents)

```python
from optionsbot.runner import run, run_one
res = run("put_credit_spread",                      # registry id or Strategy class
          {"short_delta": 0.20, "width_abs": 5, "kill_switch": True},   # overrides strategy defaults
          ["SPY", "QQQ"], start="2024-03-01", end="2026-10-20", capital=100_000,
          cash_yield=False, early_assign=True)       # Engine kwargs; costs=Costs(...) for fill model
r = res["SPY"]
r["metrics"]            # flat metrics + in_sample / out_of_sample / vs_buy_hold sub-dicts
r["equity"]             # daily DataFrame: equity, cash, shares, option_value, reserved, delta_dollars, ...
r["trades"]             # trade log DataFrame
r["notes"], r["infeasible"], r["params"]
```

- `optionsbot.strategies.REGISTRY` maps ids to classes. Each class's `defaults` dict lists every tunable parameter.
- `optionsbot.engine.Costs(slip_abs, slip_pct, slip_cap, fee_per_contract, stock_slip_bps, sec_fee_rate)` holds the fill model.
- `Engine(..., mark_mode="model"|"carry")` sets how a leg is marked on a day with no print: Black-Scholes re-price at the last IV, or the last print carried forward.
- `runner.market(symbol)` memoizes the per-symbol `Market` (chain + bars), so repeated `run()` calls in one process are cheap.

## Strategies (ids)

| id | description | key defaults |
|---|---|---|
| `buy_hold` | underlying, fractional shares, dividends reinvested | benchmark |
| `covered_call` | 100-share lots + short call | 0.30Δ, 30-45 DTE (target 38), roll at 21 DTE |
| `csp` | cash-secured put | 0.30Δ, 30-45 DTE, TP 50%, cash-settled if ITM |
| `wheel` | CSP → physical assignment → covered calls (strike ≥ cost basis) → called away → CSP | 0.30Δ puts (TP 50%) / 0.30Δ calls held |
| `put_credit_spread` | short 0.25Δ / long 0.10Δ put | 30-45 DTE, TP 50%, stop at loss = 2× credit, exit 21 DTE, max loss ≤ 25% equity |
| `iron_condor` | 0.16Δ shorts / 0.05Δ wings | 38-52 DTE (45), TP 50%, exit 21 DTE |
| `iron_condor_hold` | CNDR-like: monthly 0.20Δ / 0.05Δ held to expiry | |
| `collar` | lots + long 0.25Δ put + short 0.30Δ call | 30-45 DTE, roll 21 DTE |
| `pmcc` | long 0.80Δ LEAPS (180-400 DTE, roll at 90) + short 0.30Δ 30-45 DTE call | LEAPS budget 35% of equity |
| `pmcc_sma50` | PMCC, short call skipped while close < 50DMA | |
| `trend_pcs` | PCS, entries only when close > 200DMA | |
| `regime_pcs` | PCS, 200DMA + 20d realized vol below its 1y 80th pct | |
| `vix_pcs` | PCS, 200DMA + VIX < 30 (Cboe VIX close) | |
| `csp_kill`, `pcs_kill`, `iron_condor_kill`, `put_write_kill` | the same strategies with the intramonth kill switch | see below |
| `cal_bxm`, `cal_bxmd`, `cal_put` | Cboe index clones for calibration | monthly, held to expiry |

**Kill switch** (`kill_switch=True`, from `research/long_history_validation.md`):
- Triggers at the close when VIX/VIX3M > 1.15, VIX > 40, or a position's loss exceeds 8% of short-strike notional.
- All positions close at the next day's close.
- Re-entry waits for the next monthly expiry, and then only once VIX/VIX3M ≤ 1.15 and VIX ≤ 40.
- Parameters: `kill_ratio`, `kill_vix`, `kill_loss`.
- VIX and VIX3M come from the cached Cboe CSVs in `.cache/charts/`.

## Engine mechanics and assumptions

- **Timing.** All decisions and fills happen at the daily close. Exits are evaluated before entries each day.
- **Contract selection.**
  - Expiries in the DTE window are ranked as follows: monthlies first, then Friday weeklies, then other weeklies (penalty), then by distance to the DTE target.
  - Alpaca's contract list contains weeklies that were listed only ~2 weeks before expiry. A contract therefore only counts as eligible if it actually printed on the entry day.
  - Strike choice: the code inverts Black-Scholes on each nearby strike's bar close vs the underlying close and picks the strike whose inferred delta is closest to the target. The search window moves if the best strike lies on its edge.
  - If no strike on the top four expiries printed that day, it falls back to the nearest strike by model delta (seeded with 20d realized vol × 1.15) at a model price. Those entries are flagged `theo_entry`; in practice they were very rare (see `n_theo_selections`).
- **Marks.**
  - A leg is marked at its daily bar close if it printed that day (floored at intrinsic).
  - Otherwise it is re-priced with Black-Scholes at its last inferred IV and today's spot and time to expiry, and flagged stale. `pct_trades_with_stale_marks` and `pct_days_with_stale_legs` are reported.
- **Fills.**
  - Buy at close + slip, sell at close − slip, with slip = max($0.03, 2% of price) capped at $0.25 per leg.
  - Fees: $0.03/contract regulatory pass-through. Alpaca charges $0 commission.
  - Stock: 1 bp slippage plus the SEC fee on sales.
  - Expiration settles at intrinsic with no cost.
- **Expiration.**
  - ITM legs settle at intrinsic on the underlying's raw close on expiry day.
  - Covered calls, CSPs and spreads are cash-settled at intrinsic. This is economically the same as assignment followed by an immediate re-trade at the close.
  - The wheel takes physical delivery: shares are bought at the strike, or called away.
- **Early assignment.** This is a heuristic. An ITM short call whose time value is below the next day's dividend is assumed to be exercised at today's close. Short puts are never assigned early, and calls are not assigned early for any other reason.
- **Capital.** Positions are fully cash-secured, with no naked short options and no margin borrowing. These requirements are reserved from free cash:
  - CSP: strike × 100 × n.
  - Vertical: width × 100 × n, with the credit sitting in cash.
  - Condor: the wider wing.
  - Covered calls and collars are covered by the shares.
  - PMCC short call: covered by the LEAPS, which always has the lower strike.
  - New positions must fit in free cash.
  - Spreads and condors are sized so that max loss ≤ `risk_frac` (25%) of equity. Only one position is open at a time (`max_positions`).
- **Sizing honesty.**
  - Stock strategies trade whole 100-share lots, and CSPs need the full strike in cash. When that doesn't fit, the run is reported as infeasible, or as partially blocked (`blocked_days`, `notes`). The code never fractionalizes contracts.
  - For example, $100k buys only one lot of SPY (~$50-77k), so the SPY covered call is only ~55-75% invested.
- **Dividends.** Cash dividends come from Alpaca's corporate-actions endpoint and are credited on the ex-date for shares held. Option pricing uses a flat yield per symbol (`pricing.DIV_YIELD`).
- **Splits.** A split is detected from raw vs split-adjusted closes, and share counts scale by it. No option position may span a split: NVDA's 10:1 split on 2024-06-10 re-used OCC symbols, so pre- and post-split bars share names.
- **Rates.** `pricing.risk_free` is a piecewise approximation of the fed funds rate: 5.3% to Sep-2024, then cut steps down to 3.6% from Dec-2025. It is used for BS and for the optional `cash_yield` (T-bill interest on idle cash and collateral, off by default because Alpaca pays none by default).
- **Metrics.**
  - Sharpe and Sortino use rf = 0 on daily returns. Max-drawdown duration is in trading days.
  - `avg_net_delta_pct` is mean dollar-delta / equity.
  - `avg_capital_at_risk_pct` is (stock + reserved collateral + long option value) / equity.
  - The in-sample/out-of-sample split comes from the same continuous run: 2024-03-01..2025-06-30 vs 2025-07-01..2026-10-20.

## Outputs (`results/`)

- `summary.json`: assumptions, per-run flat metrics, per-strategy aggregates (medians across underlyings), infeasible runs, calibration table and sensitivity summary.
- `summary_table.csv`: one row per strategy × underlying × capital.
- `<strategy>/equity_curves.csv`: daily equity, one column per `SYMBOL_CAPITAL`. `<strategy>/trades.csv` is the full trade log.
- `calibration.csv`: the Cboe calibration table.
- `sensitivity.csv`: the parameter grid with full-period, IS and OOS metrics.
- `dashboard.json`: compact payload for the web dashboard (< 2 MB, curves downsampled).
- `runs.json`: the raw per-run payload used to rebuild summary and dashboard. It is gitignored.

## Results (run of 2026-10-21; period 2024-03-01 → 2026-10-20)

This is a short sample: about 2.6 years, mostly a strong bull market, with drawdowns in Aug-2024 and Apr-2025 and a few smaller scares. Read every number below with that in mind.

### Calibration against Cboe indexes (`results/calibration.csv`)

These are SPY clones on $1M, monthly, held to expiry. "2024" runs from 2024-01-19, the first day of Alpaca option bars, so it misses about +1.5% of SPX gains from 2-19 Jan.

| clone | Cboe 2024 | ours 2024 (0% cash / T-bill cash) | Cboe 2025 | ours 2025 (0% / T-bill) |
|---|---|---|---|---|
| ATM buy-write (BXM) | 20.1% | 17.1% / 17.3% | 8.9% | 12.2% / 12.4% |
| 30Δ buy-write (BXMD) | 23.8% | 20.1% / 20.3% | 12.1% | 12.9% / 13.0% |
| ATM put-write (PUT) | 17.8% | 12.8% / **18.3%** | 9.2% | 6.3% / **11.1%** |
| SPY total return | | 23.0% | | 17.7% |

With T-bill interest on collateral, every comparable gap is within ±3.6pp; the largest is BXMD 2024 at −3.6pp, half of which is the missing 2½ weeks of January. Without interest, PUT 2024 is −5.0pp, which is about the T-bill yield on the collateral. These gaps are what you'd expect from the differences: SPY vs SPX, American vs European exercise, trade prints vs Cboe's VWAP/settlement fills, our slippage, and lot rounding. They don't point to a pricing or fill bug.

The main strategy grid keeps `cash_yield=False`. Alpaca pays no interest on idle cash by default, so every cash-heavy strategy (CSP, spreads, condors) is understated by about 4-5%/yr relative to a broker that does pay.

### Headline: $100k, cells are CAGR % / Sharpe / max DD %

| strategy | SPY | QQQ | IWM | AAPL | MSFT | NVDA | AMZN | GOOGL | META | median Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|
| `buy_hold` | 17.9 / 1.14 / -19 | 20.5 / 0.99 / -23 | 13.0 / 0.70 / -27 | 27.8 / 1.02 / -33 | 10.6 / 0.51 / -34 | 44.5 / 1.02 / -37 | 15.8 / 0.62 / -31 | 41.7 / 1.29 / -30 | 13.4 / 0.52 / -34 | 0.99 (n=9) |
| `covered_call` | 7.9 / 1.09 / -9 | 13.6 / 0.92 / -19 | 11.1 / 0.85 / -19 | 19.7 / 0.98 / -28 | 8.7 / 0.61 / -22 | 43.4 / 1.21 / -28 | 13.2 / 0.66 / -22 | 24.5 / 1.18 / -23 | 13.5 / 0.63 / -28 | 0.92 (n=9) |
| `wheel` | 6.6 / 0.98 / -10 | 14.9 / 1.08 / -18 | 9.6 / 0.86 / -18 | 15.5 / 0.92 / -22 | 9.3 / 0.56 / -26 | 29.1 / 1.02 / -26 | 20.3 / 1.00 / -21 | 15.8 / 0.84 / -21 | 15.3 / 0.91 / -16 | 0.92 (n=9) |
| `collar` | 5.0 / 1.21 / -5 | 7.1 / 0.94 / -11 | 6.5 / 0.79 / -10 | 11.7 / 1.08 / -9 | 4.3 / 0.44 / -18 | 23.3 / 1.06 / -22 | 8.9 / 0.69 / -15 | 12.0 / 0.93 / -16 | 12.6 / 0.97 / -13 | 0.94 (n=9) |
| `csp` | 4.9 / 0.86 / -8 | 7.4 / 0.67 / -15 | 9.0 / 0.82 / -19 | 8.1 / 0.69 / -19 | 1.7 / 0.20 / -17 | 27.4 / 1.07 / -18 | 4.2 / 0.34 / -20 | 8.9 / 0.68 / -17 | 13.3 / 0.75 / -21 | 0.69 (n=9) |
| `put_write_kill` | 4.9 / 0.96 / -8 | 4.8 / 0.53 / -17 | 4.6 / 0.49 / -16 | -0.8 / 0.01 / -31 | 3.2 / 0.33 / -18 | 8.6 / 0.48 / -32 | 10.4 / 0.77 / -19 | 14.0 / 0.98 / -20 | 3.3 / 0.31 / -24 | 0.49 (n=9) |
| `csp_kill` | 3.1 / 0.67 / -8 | 1.1 / 0.18 / -15 | 4.5 / 0.52 / -18 | 4.2 / 0.43 / -19 | 0.6 / 0.11 / -15 | 10.4 / 0.59 / -24 | 0.1 / 0.08 / -18 | 5.4 / 0.52 / -18 | -1.9 / -0.13 / -19 | 0.43 (n=9) |
| `pmcc` | 12.0 / 0.72 / -26 | 14.0 / 0.65 / -28 | 6.4 / 0.37 / -35 | 18.4 / 0.73 / -34 | 9.0 / 0.48 / -27 | 15.0 / 0.62 / -24 | 5.9 / 0.34 / -28 | 23.7 / 0.84 / -34 | 3.3 / 0.25 / -39 | 0.62 (n=9) |
| `pmcc_sma50` | 17.5 / 0.89 / -29 | 17.9 / 0.76 / -30 | 9.2 / 0.46 / -38 | 19.2 / 0.70 / -37 | 5.8 / 0.34 / -34 | 15.1 / 0.60 / -27 | 10.5 / 0.47 / -35 | 25.1 / 0.84 / -37 | 11.3 / 0.53 / -30 | 0.60 (n=9) |
| `iron_condor` | 9.7 / 0.76 / -13 | 7.2 / 0.61 / -11 | 0.0 / 0.09 / -34 | -9.2 / -0.40 / -31 | 1.8 / 0.19 / -19 | 8.8 / 0.64 / -13 | 5.5 / 0.42 / -14 | -7.4 / -0.37 / -29 | -1.7 / 0.02 / -23 | 0.19 (n=9) |
| `iron_condor_kill` | 5.3 / 0.55 / -12 | 3.0 / 0.32 / -11 | -4.9 / -0.28 / -32 | -13.3 / -0.75 / -39 | -0.3 / 0.05 / -22 | 5.8 / 0.48 / -15 | 1.2 / 0.16 / -18 | -10.9 / -0.64 / -35 | -6.2 / -0.25 / -32 | 0.05 (n=9) |
| `iron_condor_hold` | 5.2 / 0.34 / -27 | 4.4 / 0.30 / -27 | 3.7 / 0.27 / -24 | -20.9 / -0.64 / -60 | 3.6 / 0.27 / -38 | -1.0 / 0.10 / -45 | 14.2 / 0.62 / -33 | -6.3 / -0.03 / -43 | -17.0 / -0.39 / -66 | 0.27 (n=9) |
| `put_credit_spread` | -2.8 / -0.07 / -33 | -2.0 / 0.02 / -34 | -5.4 / -0.12 / -41 | 0.8 / 0.16 / -32 | -14.9 / -0.50 / -56 | 12.4 / 0.54 / -33 | -3.8 / -0.00 / -31 | -1.2 / 0.08 / -43 | -8.7 / -0.21 / -47 | -0.00 (n=9) |
| `pcs_kill` | -5.1 / -0.21 / -35 | -2.6 / -0.02 / -35 | -4.6 / -0.09 / -38 | 1.0 / 0.17 / -32 | -15.2 / -0.53 / -56 | 15.3 / 0.63 / -29 | -3.9 / -0.02 / -30 | -1.1 / 0.08 / -44 | -5.3 / -0.07 / -41 | -0.02 (n=9) |
| `trend_pcs` | -9.3 / -0.49 / -37 | 1.3 / 0.16 / -22 | 1.9 / 0.19 / -19 | 6.8 / 0.43 / -21 | -4.3 / -0.14 / -33 | 13.5 / 0.60 / -25 | -9.3 / -0.25 / -39 | -0.0 / 0.12 / -38 | -16.4 / -0.67 / -54 | 0.12 (n=9) |
| `vix_pcs` | -9.3 / -0.49 / -37 | 1.3 / 0.16 / -22 | 1.9 / 0.19 / -19 | 6.5 / 0.41 / -21 | -4.3 / -0.14 / -33 | 13.5 / 0.60 / -25 | -9.3 / -0.25 / -39 | -0.0 / 0.12 / -38 | -13.9 / -0.55 / -50 | 0.12 (n=9) |
| `regime_pcs` | -2.3 / -0.09 / -24 | -7.2 / -0.35 / -32 | -3.7 / -0.11 / -26 | -5.7 / -0.21 / -32 | -6.4 / -0.28 / -33 | 10.1 / 0.51 / -25 | -1.2 / 0.05 / -24 | 1.7 / 0.19 / -31 | -2.3 / -0.03 / -25 | -0.09 (n=9) |

### $10k (`n/a` = infeasible: one 100-share lot, or one fully secured put, does not fit)

| strategy | SPY | QQQ | IWM | AAPL | MSFT | NVDA | AMZN | GOOGL | META | median Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|
| `buy_hold` | 17.9 / 1.14 / -19 | 20.5 / 0.99 / -23 | 13.0 / 0.70 / -27 | 27.8 / 1.02 / -33 | 10.6 / 0.51 / -34 | 44.5 / 1.02 / -37 | 15.8 / 0.62 / -31 | 41.7 / 1.29 / -30 | 13.4 / 0.52 / -34 | 0.99 (n=9) |
| `covered_call` | n/a | n/a | n/a | n/a | n/a | 38.2 / 1.27 / -27 | n/a | n/a | n/a | 1.27 (n=1) |
| `wheel` | n/a | n/a | n/a | n/a | n/a | 17.9 / 1.21 / -14 | n/a | n/a | n/a | 1.21 (n=1) |
| `collar` | n/a | n/a | n/a | n/a | n/a | 11.5 / 1.00 / -17 | n/a | n/a | n/a | 1.00 (n=1) |
| `csp` | n/a | n/a | n/a | n/a | n/a | 13.7 / 0.98 / -14 | n/a | n/a | n/a | 0.98 (n=1) |
| `put_write_kill` | n/a | n/a | n/a | n/a | n/a | 5.2 / 0.95 / -3 | n/a | n/a | n/a | 0.95 (n=1) |
| `csp_kill` | n/a | n/a | n/a | n/a | n/a | 5.4 / 0.59 / -15 | n/a | n/a | n/a | 0.59 (n=1) |
| `pmcc` | n/a | n/a | 10.7 / 0.55 / -27 | 7.7 / 0.44 / -26 | n/a | 23.9 / 0.84 / -27 | 1.5 / 0.18 / -19 | 17.8 / 0.86 / -30 | n/a | 0.55 (n=5) |
| `pmcc_sma50` | n/a | n/a | 9.8 / 0.48 / -37 | 7.9 / 0.42 / -26 | n/a | 24.6 / 0.84 / -29 | 2.9 / 0.26 / -23 | 25.7 / 1.02 / -33 | n/a | 0.48 (n=5) |
| `iron_condor` | -2.1 / -0.07 / -24 | 5.9 / 0.93 / -5 | -4.3 / -0.28 / -27 | -7.0 / -0.41 / -27 | 4.7 / 0.47 / -10 | 9.2 / 0.81 / -10 | 4.9 / 0.47 / -10 | -7.6 / -0.74 / -20 | 1.2 / 0.36 / -4 | 0.36 (n=9) |
| `iron_condor_kill` | -4.8 / -0.52 / -18 | 5.9 / 0.93 / -5 | -0.8 / -0.01 / -27 | -10.7 / -0.76 / -33 | 1.3 / 0.18 / -12 | 6.9 / 0.69 / -12 | -0.4 / 0.04 / -19 | -7.0 / -0.82 / -21 | 1.2 / 0.36 / -4 | 0.04 (n=9) |
| `iron_condor_hold` | -21.6 / -0.76 / -49 | -16.9 / -0.54 / -46 | 1.6 / 0.18 / -18 | -31.2 / -1.43 / -64 | -1.6 / 0.03 / -37 | 6.6 / 0.41 / -34 | 12.8 / 0.69 / -25 | -3.3 / 0.03 / -38 | 3.8 / 0.40 / -8 | 0.03 (n=9) |
| `put_credit_spread` | -4.5 / -0.37 / -23 | -9.5 / -0.67 / -29 | -5.8 / -0.23 / -36 | -0.8 / 0.04 / -31 | -14.9 / -0.83 / -44 | 11.5 / 0.60 / -25 | -2.2 / -0.01 / -27 | -1.1 / 0.05 / -35 | 2.7 / 0.27 / -17 | -0.01 (n=9) |
| `pcs_kill` | -4.5 / -0.37 / -23 | -9.5 / -0.67 / -29 | -5.5 / -0.23 / -33 | -0.1 / 0.08 / -28 | -14.9 / -0.83 / -44 | 14.3 / 0.71 / -21 | -2.3 / -0.02 / -25 | -1.1 / 0.05 / -35 | 2.7 / 0.27 / -17 | -0.02 (n=9) |
| `trend_pcs` | -6.4 / -0.61 / -23 | -6.6 / -0.49 / -23 | -1.0 / 0.02 / -19 | 5.6 / 0.40 / -19 | -5.2 / -0.27 / -31 | 12.5 / 0.67 / -18 | -6.2 / -0.23 / -31 | -1.1 / 0.04 / -32 | 4.2 / 0.44 / -9 | 0.02 (n=9) |
| `vix_pcs` | -6.4 / -0.61 / -23 | -6.6 / -0.49 / -23 | -1.0 / 0.02 / -19 | 5.4 / 0.39 / -19 | -5.2 / -0.27 / -31 | 12.5 / 0.67 / -18 | -6.2 / -0.23 / -31 | -1.1 / 0.04 / -32 | 4.2 / 0.44 / -9 | 0.02 (n=9) |
| `regime_pcs` | -11.8 / -1.06 / -30 | -8.9 / -0.80 / -25 | -4.5 / -0.23 / -22 | -7.2 / -0.43 / -29 | -8.3 / -0.54 / -29 | 10.2 / 0.62 / -17 | -1.9 / -0.02 / -19 | -1.9 / -0.02 / -29 | 5.5 / 0.63 / -9 | -0.23 (n=9) |

At $10k, only defined-risk spreads, condors and PMCC (on cheaper names) are feasible, plus NVDA stock strategies after its 10:1 split. QQQ, SPY, MSFT and META PMCC are infeasible: one 0.80Δ LEAPS costs more than 35% of $10k. Feasible-but-blocked days are counted in `blocked_days`.

### What the numbers say

- **Almost nothing beat buy-and-hold on total return** in this bull-market sample. The exceptions are the wheel on AMZN (+15pp) and META (+6pp) and the covered call on META (+0.2pp); the NVDA covered call came close (−5pp). The best risk-adjusted results come from the stock-plus-premium family. By median Sharpe at $100k: covered call 0.92 and wheel 0.92 (OOS 1.32 for both), collar 0.94 with roughly half the drawdown, and buy-and-hold 0.99.
  - Their CAGR is lower because their delta is lower: the covered call averages 0.62 net delta and the wheel 0.48.
  - On SPY at $100k they are also under-invested, because only one 100-share lot fits.
- **The wheel ≥ CSP ≥ CSP+kill.** Physical assignment plus covered calls above cost basis kept upside participation after dips.
- **Put credit spreads (0.25Δ/0.10Δ, 25% of equity at max loss) lost money on 6 of 9 names.** Win rates were about 73%, but the losses that hit the 2×-credit stop or the 21-DTE exit were 3-5× the average win.
  - The trend (SMA200) and VIX<30 filters produced identical trades (VIX<30 never bound while price was above the 200DMA). They did not help on the ETFs.
  - PCS results are very cost-sensitive. On SPY, CAGR goes −2.8% at default costs, +2.0% at half slippage, and +5.7% at zero cost; QQQ is similar (`results/cost_sensitivity.csv`).
  - Alpaca SPY/QQQ spreads are often tighter than the default $0.03 + 2% assumption, so the ETF PCS numbers are pessimistic. Single-name numbers are about right.
- **Iron condors.** The 45-DTE, 0.16Δ condor with TP 50% and a 21-DTE exit was positive on the ETFs: SPY 9.7%/0.76, QQQ 7.2%/0.61. It was negative on AAPL and GOOGL.
  - The held-to-expiry CNDR-like variant on single names was the worst strategy: AAPL −21%/yr and META −17%/yr, with max DD −60% to −66%. Monthly condors held through earnings gaps lose repeatedly at 25% risk per trade. It is not a bug: the trade log shows ITM settlements at intrinsic.
- **Kill switch (VIX/VIX3M > 1.15, VIX > 40, or −8% of notional).** Here it hurt: CSP median CAGR fell from 8.1% to 3.1%. Most exits came at the close of 2025-04-07, one day before the low, and re-entry waited for the next monthly cycle, so the strategy missed the V-shaped rebound.
  - The 8%-of-notional loss leg fires often on single stocks.
  - This sample has no 2008/2020-style crash, which is exactly where the long-history study says the rule pays. Treat "hurts in a V-shaped scare" as the expected cost, not as a refutation.
- **PMCC / PMCC-sma50.** Results were mid-pack, with Sharpe 0.6-0.9 and drawdowns −25% to −39%, close to buy-and-hold drawdowns at lower return.

### Sensitivity / out-of-sample (`results/sensitivity.csv`, SPY+QQQ+IWM, $100k)

The grid covered 170 parameter sets × 3 ETFs. For each strategy family, the "best" set is the one with the highest mean in-sample Sharpe (2024-03 → 2025-06), which is then scored out of sample (2025-07 → 2026-10).

| family | sets | IS-best params | IS Sharpe | OOS Sharpe | OOS rank of IS-best | default OOS Sharpe | IS↔OOS rank corr |
|---|---|---|---|---|---|---|---|
| put credit spread | 128 | sd 0.16, 45 DTE, TP 50%, long 0.10Δ, no filter | 0.03 | 0.63 | 43/128 | 0.79 | 0.69 |
| iron condor | 24 | sd 0.20, 45 DTE, TP 50%, 0.05Δ wings | 0.39 | 0.94 | 8/24 | 0.99 | 0.06 |
| CSP | 18 | 0.20Δ, 45 DTE, TP 75% | 0.59 | 1.85 | 3/18 | 1.52 | 0.41 |

- The in-sample pick did not beat the default out of sample for PCS or the condor. The condor's IS/OOS rank correlation is about zero, so its parameter ranking is mostly noise. Don't tune on this sample.
- CSP is the one family with some IS/OOS persistence: lower delta (0.20) and longer DTE ranked best in both halves.
- PCS dimensions:
  - Delta-defined wings (0.10Δ long) beat fixed $5 wings everywhere. $5 wings on SPY/QQQ need many contracts and the costs swamp them.
  - Filters (SMA200, SMA200+VIX, kill switch) changed little.
- OOS (2025-07 →) Sharpes are higher across the board than IS because OOS was a calmer, trending market. Compare strategies within a period, not across periods.

## Data coverage problems found

- **History is short.** Alpaca option bars start on 2024-01-18, so there is about 2.75 years of option data in total.
- **Bars are trade prints, not quotes.**
  - Sparse strikes can carry stale prints from earlier in the day; the code floors marks at intrinsic.
  - There are no historical bid/ask quotes or greeks, so IV and delta are inferred from closes.
  - 5Δ condor wings are often stale: 43-45% of condor trades had at least one stale-mark day, and PMCC LEAPS were stale on about 30% of days. Those days are model-marked at the last IV.
  - 30Δ short legs on these names almost always print. Model-priced (theoretical) entries were about 0% for every strategy except the wheel (0.6%).
- **Contract list vs listing date.** The contracts endpoint lists weeklies that only existed about 2 weeks before expiry. The selector therefore requires a print on the entry day; without that, early versions entered 56% of trades in non-existent contracts.
- **NVDA 10:1 split (2024-06-10).** OCC symbols were re-used across the split; for example, NVDA240621C00120000 has prints at ~$650 before the split. Options are therefore never held across a split.
- **Adjusted roots** (e.g. `1SPY`, `NVDA1`) and non-100 multipliers are dropped.
- **Dividends.** The corporate-actions endpoint was missing SPY's 2026-09 dividend. One quarterly SPY dividend (~0.25%) is therefore missing for stock-holding SPY strategies.
- **VIX filter.** It uses the real Cboe VIX/VIX3M (`.cache/charts/`). A SPY 30-DTE ATM-IV proxy built from Alpaca bars (`Market.atm_iv_series`) is a fallback and is used for nothing else.

## Known limitations / caveats

- Daily closes only: no intraday stops or fills, and a stop can fill well beyond its level after a gap (several PCS stops lost 3-4× credit).
- Black-Scholes is European with a flat dividend yield and a piecewise rate. Early assignment is modeled only for ITM short calls before an ex-dividend date. Deep-ITM short puts are never assigned early. Paper-account next-day assignment posting is not modeled.
- There is no survivorship issue within this universe (all mega-caps), but the universe itself was chosen with hindsight: NVDA, GOOGL and META were winners.
- Single path, small trade counts (30-90 per run), one bull regime. Confidence intervals on Sharpe are about ±0.6. Results don't generalize to 2008/2020-type crashes.
- One position at a time, with fixed 25% max-loss sizing for spreads and condors. Laddering or smaller sizing would change both drawdowns and costs.
- Earnings-event strategies (E1/E3/E5/Z1 in `research/event_strategies.md`) are **not implemented yet**. The SEC EDGAR 8-K item 2.02 source is documented there and would plug in as a new Strategy subclass.

