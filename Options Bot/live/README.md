# Options Bot: live paper trading (dip put credit spread)

This bot paper-trades the research finalist from `tuning/signals/REPORT.md`, `dip_pcs`, frozen as in `finalists.json`:

| | Rule |
|---|---|
| Universe | SPY, QQQ, IWM. Single names exist in `config.py` but are off (`ENABLE_SINGLE_NAMES`). |
| Entry, 15:55 ET (`DECIDE_MINUTES_BEFORE_CLOSE = 5`) | close > 200DMA, RSI(2) < 10, IBS < 0.2, and no spread already open on that underlying. The live price stands in for the close, and IBS uses today's high/low so far. See **Decision time** below. |
| Structure | Sell a put near 0.25Δ and buy a put near 0.10Δ on the same expiry, 7–14 DTE (target 10). The expiry ranking is the backtest's: Friday expiries are preferred. |
| Order | One multi-leg limit order, starting at the net mid. It is re-priced up to 4 times (30 s per price), walking at most halfway toward the natural price, and canceled by 15:58. |
| Guards | Skip when the credit is above 60% of the width (BUGS.md #7) or below the floor (see the note below). Also skip when either leg's bid/ask exceeds 35% of its mid, the combo market is too wide, or a leg's delta is more than 0.08 from target. |
| Exits, at 15:55 | 50% of the entry **mid** credit captured (as in the research, not the fill); close > 5DMA (from the day after entry); 5 trading days held; or 1 trading day before expiry. An optional stop (`STOP_MULT`, research semantics: cost ≥ credit × (1 + STOP_MULT)) is off by default, as it was in the research. A zero bid on the long leg never blocks an exit. |
| Exit orders | A multi-leg limit order walking from mid to natural. At the cutoff it is re-priced to natural + $0.05 (capped at the width) and kept working until 30 s before the close. |
| Intraday, optional | `--intraday`, every 15 min, checks only take-profit and stop, and closes anything expiring **today**. Intraday take-profits never chase the price. The 1-day-before-expiry exit is left to the daily run. |
| Expiry-day sweep, mandatory | `--sweep`, 09:45 ET: closes any spread expiring today (an exit that did not fill the day before) and flattens stock left by an assignment, well before Alpaca's own expiration-day handling starts at 15:30 ET. |
| Sizing | Max loss per spread ≤ 25% of equity. Total max loss across spreads ≤ 50% of equity. Also capped by options buying power and `MAX_CONTRACTS` (50). |
| Kill switch | No new entries when VIX/VIX3M > 1.15, VIX > 40, or VIX data is unavailable **or not fresh**: both VIX and VIX3M must be today's Cboe delayed quotes, at most 30 minutes old. The Cboe history CSV (yesterday's close) is shown for information only and blocks entries. |
| Data checks | Entries fail closed when the daily history is short (< 205 bars), stale (last bar > 5 days old) or has gaps, when the live trade is older than 5 minutes, or when an option quote is older than 15 minutes or has no timestamp. |

> **Credit floor.** The brief asked for a 15%-of-width minimum. In the research backtest, 0.25Δ/0.10Δ spreads at 7–14 DTE collected a median of 12.3% of width, with a minimum of 6.8%. **51 of the 54 SPY/QQQ/IWM trades were below 15%.** A 15% floor would turn the strategy off, so `MIN_CREDIT_FRAC` defaults to 6.5%. Set it to `0.15` in `config.py` if you want the stricter rule anyway.

## Decision time

The signals are computed on a snapshot at `close − DECIDE_MINUTES_BEFORE_CLOSE` (default **5 → 15:55 ET**, 12:55 on half days). The research decided on the official close, so the decision time was re-validated in `tuning/live_timing/` (`run_timing.py`). The frozen rule was re-run on SPY/QQQ/IWM, 2024-03 to 2026-10, with the signal and 5DMA exit bar rebuilt from SIP minute bars at each time:

| Decide at | IS Sharpe | OOS Sharpe | Research trades kept / missed / added (of 53) |
|---|---|---|---|
| close (research) | 0.76 | 1.54 | 53 / 0 / 0 |
| 15:45 | 0.57 | 1.71 | 34 / 19 / 18 |
| 15:50 (old default) | 0.52 | 1.62 | 35 / 18 / 14 |
| **15:55 (default)** | **0.74** | **1.89** | **45 / 8 / 11** |
| 15:56 | 0.71 | 1.67 | 46 / 7 / 6 |

- 15:50 kept only two thirds of the research trades and cut in-sample Sharpe from 0.76 to 0.52 (max DD −7.7% → −10.2%). 15:55 keeps the in-sample quality.
- 15:56 is about as faithful to the research as 15:55 but leaves only about 2 minutes before the 15:58 cutoff, so each ladder price would get about 20 s. 15:55 gives 3 minutes (5 prices × 30 s, `STEP_WAIT_SECONDS = 30`).
- The free-plan data mix (SIP up to 16 min old plus IEX for the last 16 min) gives the same picture at 15:55 (IS 0.76, OOS 1.94).
- Moving the exit decision changes nothing; the loss came from the entry signal. Exits are decided at the same time as entries.
- The sample is small (53 trades). Read Sharpe gaps of 0.1–0.2 as noise. 15:55 was chosen because the number of research trades kept rises steadily with the time of day, not because it has the best Sharpe.

To change it, edit `DECIDE_MINUTES_BEFORE_CLOSE` in `config.py`. Keep `(DECIDE − CUTOFF) × 60 ≥ (ENTRY_STEPS + 1) × STEP_WAIT_SECONDS` so the ladder fits; `work_orders` also shrinks the step to fit.

## Files

```
live/
  config.py      every number + credential loading (OPT_* only for trading)
  broker.py      Alpaca REST: account, positions, calendar, mleg submit/replace/cancel, order status,
                 stock bars/snapshots, option chain snapshots (indicative feed)
  signals.py     RSI2 / IBS / SMAs via optionsbot/indicators.py on prior bars + today's partial bar; VIX
  strategy.py    pure functions: signal, kill switch, strike pick, guards, sizing, price ladders, exits,
                 grouping option legs into spreads
  execution.py   order lifecycle: submit -> re-price -> cancel / aggressive fallback, all jobs in lockstep
  state.py       live/state/: positions.json (entry date/credit), trades.jsonl, orders.jsonl, once-per-day guard
  db.py          Supabase PostgREST writes (no-op when unset)
  run.py         CLI (below)
  sync.py        16:15 ET: equity, legs, spreads, closed trades -> Supabase
  003_options_live.sql   proposed dashboard migration (copy to dashboard/supabase/migrations/)
  tests/         offline unit tests (fake broker, fake chain, fake clock); test_fixes.py covers the review findings
  logs/          one log file per day + cron.log
```

## Setup

1. **Create a second Alpaca paper account for options.** Do not reuse the Stock Shorting Bot account.
   - In the Alpaca dashboard, open the account menu (top left) → **Open New Paper Account**. Alpaca allows several paper accounts per login.
   - Give it the equity you want to test with (for example $10,000) and switch to it.
   - Under **Account → Configure**, make sure options trading is **Level 3**, which multi-leg spreads need. Paper accounts usually default to the highest level; `--check` shows the level.
   - Generate API keys **while that account is selected**. Paper keys start with `PK`.
2. Create the env file:
   ```bash
   cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot"
   cp .env.example .env && chmod 600 .env
   # fill in OPT_APCA_API_KEY_ID, OPT_APCA_API_SECRET_KEY; keep OPT_APCA_PAPER=true
   # optional: SUPABASE_URL / SUPABASE_SERVICE_KEY (same values as the shorting bot's .env)
   ```
3. Optional, for the dashboard: apply `live/003_options_live.sql` in the Supabase SQL editor, or copy it into `dashboard/supabase/migrations/`.
4. Check everything:
   ```bash
   PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
   $PY live/run.py --check
   $PY -m pytest -q live/tests/
   $PY live/run.py --now --preview      # dry run on the latest data; --preview also prices spreads without a signal
   ```
5. Do a few days of dry runs from cron (drop `--execute`) and read `live/logs/`. Then switch to `--execute`.

## Commands

```bash
$PY live/run.py                         # dry run: waits for 15:55 ET, prints what it would do
$PY live/run.py --now                   # dry run now (works after hours on the latest prices)
$PY live/run.py --execute               # what cron runs at 15:45
$PY live/run.py --intraday --execute    # take-profit / stop checks (cron every 15 min)
$PY live/run.py --sweep --execute       # expiry-day morning sweep (cron 09:45, mandatory)
$PY live/run.py --check                 # health check
$PY live/run.py --close-all             # emergency: show the closing orders
$PY live/run.py --close-all --execute   # emergency: cancel the bot's orders, close every spread now (and unpaired legs, assigned stock)
$PY live/sync.py                        # 16:15 ET: push the account's state to Supabase
```

`--force` allows a second executed daily run on the same day. `--symbols SPY` overrides the universe.

## Cron (not installed; add with `crontab -e`)

This Mac's clock is ET, so these times are ET. If the timezone changes, change them too.

```cron
# Options bot: 15:45 ET daily run (waits for the 15:55 decision time, cancels entries by 15:58). The 12:45 line covers
# 13:00 half days; on normal days it sees the decision time is hours away and exits immediately.
45 15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --execute >> live/logs/cron.log 2>&1
45 12 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --execute >> live/logs/cron.log 2>&1
# Mandatory: expiry-day morning sweep at 09:45 ET (closes spreads expiring today, flattens assigned stock).
45 9 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --sweep --execute >> live/logs/cron.log 2>&1
# Optional intraday take-profit checks, every 15 min 10:00-15:30 ET. Hour 15 stops at :30 so nothing fires at 15:45,
# the daily run's minute (the bot also checks its own window before taking the lock).
*/15 10-14 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --intraday --execute >> live/logs/cron.log 2>&1
0,15,30 15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --intraday --execute >> live/logs/cron.log 2>&1
# 16:15 ET: sync equity, positions, spreads and closed trades into Supabase.
15 16 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/sync.py >> live/logs/cron.log 2>&1
```

**Locking.** Executing runs share `live/state/.lock`. Each run checks its own time window *before* taking the lock, so an out-of-window intraday run never blocks the daily run. The daily run waits up to `LOCK_TIMEOUT_SECONDS` (240 s) for the lock. If it still can't get it, it exits with an `error` heartbeat instead of failing silently.

The Mac has to be awake from 09:40 to 09:50 ET (sweep) and from 15:40 to 16:20 ET, as it does for the shorting bot. `cron` needs Full Disk Access because the folder is on the Desktop.

## Safety

- **Separate account, separate keys.** Trading reads only `OPT_APCA_API_KEY_ID` / `OPT_APCA_API_SECRET_KEY` from `Options Bot/.env`. The process environment can override those same names, but nothing else.
  - The `APCA_*` variables and `Stock Shorting Bot/.env` are never used for trading.
  - Missing keys mean a dry run with a simulated $10,000 flat account, or a refusal with `--execute`.
- **Paper only.** The bot refuses when `OPT_APCA_PAPER` is not `true`, when the base URL is not exactly `https://paper-api.alpaca.markets` (exact hostname, https), when the key looks live (`AK…`), or when the account number does not start with `PA`. It checks this before the first trading call.
- **Never the shorting bot's account.** The bot refuses when `OPT_APCA_API_KEY_ID` equals `APCA_API_KEY_ID` in `Stock Shorting Bot/.env`, or when the account's id/number equals `SHORTING_ACCOUNT_ID` / `SHORTING_ACCOUNT_NUMBER` (set one of these in `Options Bot/.env`; `--check` warns while it is missing). The values are compared, never printed. The first account seen by an executing run is pinned in `live/state/account_pin.json`, and a different account later is refused.
- **Only its own orders.** Every order's `client_order_id` is `options-…-<uuid>` (≤ 128 characters). Cancels only ever target those orders; the bot never calls `DELETE /v2/orders` (cancel everything).
- **Dry run by default.** Only `--execute` sends orders. The broker object raises on submit, replace or cancel in a dry run.
- **Market data.** Data calls use the OPT keys when present. Until then they fall back to the shorting bot's keys, **for read-only calls to `data.alpaca.markets` only**, which the data client enforces by refusing order, position, account and activity paths. Those keys never reach the trading host.
- **No secrets in logs or Supabase.** Errors log the HTTP status and response text only.
- **Once per day.** An executed daily run writes `live/state/last_execute.json` before sending anything. A second run that day refuses unless you pass `--force`. A file lock stops overlapping executing runs.
- **Timing.** No orders at or after 15:58 ET (2 minutes before the calendar close; half days shift automatically). Before starting, it cancels its own leftover orders and waits until none is open. If one won't cancel, it refuses to trade.
- **Order safety.** A partially filled order is never replaced: it is canceled and the remainder resubmitted. After every replace the old order is re-read, and the bot follows `replaced_by`. Fills are summed over every order id. After a submit error the bot looks the order up by `client_order_id` before doing anything else. GETs and cancels retry on 429/5xx, and cancels retry with backoff. Every run ends, even after an exception, by canceling the bot's orders, booking every fill and saving positions.
- **Unpaired legs.** A partial manual close can leave a leg on its own. Such legs are logged and never traded automatically. `--close-all --execute` closes only their *unmatched* quantity with single-leg limit orders.
- **Assignment.** Stock in the options account (any position that is not an OCC option symbol) means an assignment or exercise. The daily, sweep and close-all runs flatten it with a market order and set the heartbeat to `error`.
- **Unfilled exits.** An exit that has not filled by the close is flagged in the heartbeat ("EXITS NOT FILLED"). When it is the 1-day-before-expiry exit, the heartbeat is an `error` ("EXPIRY EXIT NOT FILLED"). The next morning's `--sweep` closes it, before Alpaca's 15:30 ET expiration handling. An expiry exit with no usable quotes is still sent, priced from intrinsic value, with a warning.
- **Spreads that disappear** (expired, assigned or closed by hand) are journaled with a P&L taken from the account's FILL/OPEXP/OPASN activities when it can be worked out.

## Known limits / things to verify on the first paper trades

- **mleg price sign.** This follows Alpaca's docs: a negative `limit_price` is a credit and a positive one a debit. Watch the first order in the Alpaca UI to confirm it shows as a credit at the expected price.
  - alpaca-py 0.21 cannot place mleg orders, so `broker.py` uses REST directly.
- **Replacing an order** (`PATCH /v2/orders/{id}`) returns a new order id. If a replace is rejected (for example while the order is `pending_new`), the bot cancels the order, confirms it is canceled, and resubmits.
- **Feeds on the free data plan.**
  - The live stock price and day range come from IEX, widened with SIP minute bars up to 16 minutes old. SIP history is used for all prior days.
  - Option quotes and greeks are the `indicative` feed. That feed is not the OPRA NBBO, so quotes can lag or be wider.
  - With an Algo Trader Plus subscription, set `OPT_STOCK_FEED=sip` and `C.OPTION_FEED="opra"`.
- **No dividend adjustment.** Indicators use split-adjusted closes, as the research did.
- **Not the backtest.** The research filled at the daily close. Here the decision is made at 15:55 and the fill is a working limit order from the mid, so results will differ (see **Decision time**). REPORT.md caveat 1 (entering one day late hurt in-sample) still applies.
- **Intentional differences from the research**, all on purpose:
  - **Expiry exit.** It closes 1 trading day before expiry; the research had no expiry exit (`close_dte=None`).
  - **Optional intraday take-profit.** The research checked take-profit at the close only.
  - **No same-day re-entry.** The research could reopen on the day it closed.
  - **Shared caps.** One account carries all three ETFs with a shared 50% total-risk cap, while the research used three independent sleeves. When SPY and QQQ are both open, IWM is usually squeezed out.
  - **Quote, credit and freshness guards.** These are new.
  - **Deltas.** They come from Alpaca's indicative greeks; the research used its own Black-Scholes deltas.
- **Adopted spreads.** A spread at the broker with no local record is adopted with today as its entry date and the broker's average prices as its credit. A warning is logged. Fix `live/state/positions.json` by hand if that matters.
