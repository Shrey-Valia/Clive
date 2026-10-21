# Options Bot: live paper trading (dip put credit spread)

This bot paper-trades the research finalist from `tuning/signals/REPORT.md`, `dip_pcs`, frozen as in `finalists.json`:

| | Rule |
|---|---|
| Universe | SPY, QQQ, IWM. Single names exist in `config.py` but are off (`ENABLE_SINGLE_NAMES`). |
| Entry, about 15:50 ET | close > 200DMA, RSI(2) < 10, IBS < 0.2, and no spread already open on that underlying. The live price stands in for the close, and IBS uses today's high/low so far. |
| Structure | Sell a put near 0.25Δ and buy a put near 0.10Δ on the same expiry, 7–14 DTE (target 10). The expiry ranking is the backtest's: Friday expiries are preferred. |
| Order | One multi-leg limit order, starting at the net mid. It is re-priced up to 4 times, walking at most halfway toward the natural price, and canceled by 15:58. |
| Guards | Skip when the credit is above 60% of the width (BUGS.md #7) or below the floor (see the note below). Also skip when either leg's bid/ask exceeds 35% of its mid, the combo market is too wide, or a leg's delta is more than 0.08 from target. |
| Exits, at 15:50 | 50% of the credit captured; close > 5DMA (from the day after entry); 5 trading days held; or 1 trading day before expiry. An optional stop at 2× credit (`STOP_MULT`) is off by default, as it was in the research. |
| Exit orders | A multi-leg limit order walking from mid to natural. At the cutoff it is re-priced to natural + $0.05 (capped at the width) and kept working until 30 s before the close. |
| Intraday, optional | `--intraday`, every 15 min, checks only take-profit, stop and the expiry guard. Intraday take-profits never chase the price. |
| Sizing | Max loss per spread ≤ 25% of equity. Total max loss across spreads ≤ 50% of equity. Also capped by options buying power and `MAX_CONTRACTS` (50). |
| Kill switch | No new entries when VIX/VIX3M > 1.15, VIX > 40, or VIX data is unavailable (fails closed). Data comes from Cboe's delayed quote, falling back to the Cboe history CSV. |

> **Credit floor.** The brief asked for a 15%-of-width minimum. In the research backtest, 0.25Δ/0.10Δ spreads at 7–14 DTE collected a median of 12.3% of width, with a minimum of 6.8%. **51 of the 54 SPY/QQQ/IWM trades were below 15%.** A 15% floor would turn the strategy off, so `MIN_CREDIT_FRAC` defaults to 6.5%. Set it to `0.15` in `config.py` if you want the stricter rule anyway.

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
  tests/         offline unit tests (fake broker, fake chain, fake clock)
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
$PY live/run.py                         # dry run: waits for 15:50 ET, prints what it would do
$PY live/run.py --now                   # dry run now (works after hours on the latest prices)
$PY live/run.py --execute               # what cron runs at 15:45
$PY live/run.py --intraday --execute    # take-profit / stop checks (cron every 15 min)
$PY live/run.py --check                 # health check
$PY live/run.py --close-all             # emergency: show the closing orders
$PY live/run.py --close-all --execute   # emergency: cancel all orders, close every spread now (and unpaired legs)
$PY live/sync.py                        # 16:15 ET: push the account's state to Supabase
```

`--force` allows a second executed daily run on the same day. `--symbols SPY` overrides the universe.

## Cron (not installed; add with `crontab -e`)

This Mac's clock is ET, so these times are ET. If the timezone changes, change them too.

```cron
# Options bot: 15:45 ET daily run (waits for 15:50, cancels entries by 15:58). The 12:45 line covers 13:00
# half days; on normal days it sees the decision time is hours away and exits immediately.
45 15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --execute >> live/logs/cron.log 2>&1
45 12 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --execute >> live/logs/cron.log 2>&1
# Optional intraday take-profit checks, every 15 min, 10:00-15:15 ET (the bot enforces its own window).
*/15 10-15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/run.py --intraday --execute >> live/logs/cron.log 2>&1
# 16:15 ET: sync equity, positions, spreads and closed trades into Supabase.
15 16 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Options Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 live/sync.py >> live/logs/cron.log 2>&1
```

The Mac has to be awake from 15:40 to 16:20 ET, as it does for the shorting bot. `cron` needs Full Disk Access because the folder is on the Desktop.

## Safety

- **Separate account, separate keys.** Trading reads only `OPT_APCA_API_KEY_ID` / `OPT_APCA_API_SECRET_KEY` from `Options Bot/.env`. The process environment can override those same names, but nothing else.
  - The `APCA_*` variables and `Stock Shorting Bot/.env` are never used for trading.
  - Missing keys mean a dry run with a simulated $10,000 flat account, or a refusal with `--execute`.
- **Paper only.** The bot refuses when `OPT_APCA_PAPER` is not `true`, when the base URL is not `paper-api.alpaca.markets`, when the key looks live (`AK…`), or when the account number does not start with `PA`. It checks this before the first trading call.
- **Dry run by default.** Only `--execute` sends orders. The broker object raises on submit, replace or cancel in a dry run.
- **Market data.** Data calls use the OPT keys when present. Until then they fall back to the shorting bot's keys, **for read-only calls to `data.alpaca.markets` only**, which the data client enforces by refusing order, position, account and activity paths. Those keys never reach the trading host.
- **No secrets in logs or Supabase.** Errors log the HTTP status and response text only.
- **Once per day.** An executed daily run writes `live/state/last_execute.json` before sending anything. A second run that day refuses unless you pass `--force`. A file lock stops overlapping executing runs.
- **Timing.** No orders at or after 15:58 ET (2 minutes before the calendar close; half days shift automatically). Before starting, it cancels any orders left open in the options account.
- **Unpaired legs.** Assignment or a partial manual close can leave a leg on its own. Such legs are logged and never traded automatically. `--close-all --execute` closes them with single-leg limit orders.
- **Unfilled exits.** An exit that has not filled by the close is logged as an error and flagged in the heartbeat ("EXITS NOT FILLED"). It is retried at the next run, and the expiry guard forces the close no later than the day before expiry.

## Known limits / things to verify on the first paper trades

- **mleg price sign.** This follows Alpaca's docs: a negative `limit_price` is a credit and a positive one a debit. Watch the first order in the Alpaca UI to confirm it shows as a credit at the expected price.
  - alpaca-py 0.21 cannot place mleg orders, so `broker.py` uses REST directly.
- **Replacing an order** (`PATCH /v2/orders/{id}`) returns a new order id. If a replace is rejected (for example while the order is `pending_new`), the bot cancels the order, confirms it is canceled, and resubmits.
- **Feeds on the free data plan.**
  - The live stock price and day range come from IEX, widened with SIP minute bars up to 16 minutes old. SIP history is used for all prior days.
  - Option quotes and greeks are the `indicative` feed. That feed is not the OPRA NBBO, so quotes can lag or be wider.
  - With an Algo Trader Plus subscription, set `OPT_STOCK_FEED=sip` and `C.OPTION_FEED="opra"`.
- **No dividend adjustment.** Indicators use split-adjusted closes, as the research did.
- **Not the backtest.** The research filled at the daily close. Here the decision is made at 15:50 at the mid, so results will differ, and REPORT.md caveat 1 (entering one day late hurt in-sample) applies.
- **Adopted spreads.** A spread at the broker with no local record is adopted with today as its entry date and the broker's average prices as its credit. A warning is logged. Fix `live/state/positions.json` by hand if that matters.
