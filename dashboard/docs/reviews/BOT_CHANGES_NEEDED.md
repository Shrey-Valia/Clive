# Bot and schema changes the dashboard needs

The dashboard work stayed inside `dashboard/`. Everything below needs a change in `Stock Shorting Bot/` or in the database, so it's left for you. Until each one ships, the dashboard works around it as described, using today's schema.

## 1. Heartbeat split (UX review P0 #5)
**Problem:** `sync.py` calls `db.heartbeat("ok", ...)` at 16:15, which overwrites the run's `warning` or `error` in `bots.run_state`. A bad run can then show HEALTHY.
**Change:** pick one of these.
- Add the columns `bots.last_run_status`, `last_run_at`, `last_sync_status` and `last_sync_at`. `daily.py` writes the run pair and `sync.py` writes the sync pair. Or:
- Have `sync.py` leave `run_state` alone and only touch `last_heartbeat` and `last_message`, except on a sync failure.

**Dashboard today:** `botHealth()` in `lib/analytics.ts` takes the worse of the latest `short_runs` row (`failed` → RUN FAILED, `executed_with_errors` → CHECK ORDERS) and the heartbeat. A failed run stays visible even after sync overwrites `run_state`. Staleness is now computed in ET.

## 2. `run_events` table plus a logging handler (UX #8)
```sql
create table run_events (
  id bigint generated always as identity primary key,
  run_id bigint references short_runs(id) on delete cascade,
  at timestamptz not null default now(),
  level text not null,          -- INFO | WARNING | ERROR
  step text,                    -- scan | score | news | trade | sync
  message text
);
create index on run_events (run_id, at);
-- plus the admin_read RLS policy, same as 001
```
**Bot:** add a `logging.Handler` that buffers WARNING and above, plus a few INFO milestones (scan start, N candidates, orders sent), and flushes them to `run_events` with the current `run_id` at the end of the run and in the `except` path. Also log data-source health per run (TradingView, SI source, FINRA, SEC FTD, news), for example as `step='source'` rows.
**Dashboard today:** `getShorting()` and `getRun()` already query `run_events` and fall back to `[]` if the table is missing. The System log panel builds lines from runs, order errors, the snapshot and the heartbeat, and merges in `run_events` rows once they exist.

## 3. `sync_runs` table (UX #8)
Have `sync.py` write one row per sync: `(id, at, status, equity, n_positions, n_trades_upserted, message)`. That separates sync failures from run failures. The dashboard currently uses `equity_snapshots.taken_at` as "last sync".

## 4. `run_id` on `short_trades` (UX #7, #21)
`alter table short_trades add column run_id bigint references short_runs(id);` In `sync.py`, set it when the trade is rebuilt from fills (the opening order's `run_id` is in `short_orders`).
**Dashboard today:** best-effort join, matching ticker plus the trade's ET entry date to `short_runs.run_date`. It drives next-day P&L per run, P&L by rank slot, catalyst and score bucket. The UI shows the matched count.

## 5. `monitor.py` plus an `alerts` table (UX #10)
`monitor.py` runs every 15–30 minutes during market hours (launchd). It reads Alpaca positions and writes `alerts(id, at, level, kind, ticker, message, resolved_at)` when a position loses ≥ `ALERT_POSITION_LOSS_PCT=25`% or the book loses ≥ `ALERT_BOOK_LOSS_PCT=5`%. ntfy push is optional.
**Dashboard today:** the red TOP alert bar is computed from failed runs, rejected/canceled/expired orders, a stale heartbeat and "no run today". Once an `alerts` table exists, it should be merged into `getChrome()` (one query, same pattern as `run_events`).

## 6. Holidays and session close (UX #1)
The bot logs "market closed" and returns without writing a run row. Write a `short_runs` row with `status='market_closed'` (or keep a `sessions(date, close_at)` table) so the dashboard knows about holidays and half days from the bot's own calendar. **Dashboard today:** `lib/time.ts` has a hard-coded NYSE 2026–2027 holiday and early-close list. Extend it each year until this change ships.

## 7. Small additions to the run row
- `short_runs.finished_at` and `short_runs.cutoff_at`, so the 4-step timeline can show actual trade and cutoff times. It currently infers them from `short_orders.submitted_at`.
- `short_runs.code_version` (git sha).

## 8. Options backtester
`supabase/migrations/002_options.sql` adds `options_backtests`, `options_bt_equity` and `options_bt_trades` (admin-read RLS). The /options lab currently reads `dashboard/lib/options-backtest.json`. Copy `Options Bot/results/dashboard.json` there and rebuild. The loader accepts rates as fractions or percents, and tolerates missing fields. Include `assumptions` (fill model) and `code_version` in the export; the lab shows both in its HYPOTHETICAL banner.
