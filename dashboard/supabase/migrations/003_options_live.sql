-- Proposed dashboard migration for the live options paper bot (bot id 'options').
-- Copy to dashboard/supabase/migrations/003_options_live.sql when adopting it.
-- Purely additive; same access model as 001/002: the bot writes with the
-- service-role key, the dashboard reads as an admin.
-- The bot also writes the shared tables from 001: equity_snapshots and
-- positions (bot_id = 'options', one row per option leg) and bots.heartbeat.

create table if not exists options_live_runs (
  id           bigint generated always as identity primary key,
  run_date     date not null,
  mode         text not null,               -- dry_run | execute
  kind         text not null default 'daily', -- daily | intraday | close_all
  status       text not null,               -- planned | executed | failed
  started_at   timestamptz,
  equity       numeric,
  vix          numeric,
  vix3m        numeric,
  kill_switch  boolean,
  signals      jsonb,                       -- per symbol: close, sma200, rsi2, ibs, action
  n_exits      int,
  n_entries    int,
  message      text
);
create index if not exists options_live_runs_date on options_live_runs (run_date desc);

create table if not exists options_live_orders (
  id            text primary key,           -- last Alpaca order id of the job (replace creates new ids)
  run_id        bigint references options_live_runs (id) on delete set null,
  spread_id     text not null,              -- e.g. SPY-20261030-P761/746
  underlying    text,
  purpose       text,                       -- open | close
  qty           numeric,
  filled_qty    numeric,
  limit_prices  jsonb,                      -- ladder, per-share net (credit for open, debit for close)
  avg_price     numeric,
  status        text,                       -- filled | partial | unfilled | error | skipped
  attempts      int,
  history       jsonb,                      -- submit / replace / cancel events
  error         text,
  submitted_at  timestamptz
);
create index if not exists options_live_orders_spread on options_live_orders (spread_id);

create table if not exists options_live_positions (
  bot_id        text references bots (id),
  spread_id     text not null,
  underlying    text,
  expiry        date,
  short_symbol  text,
  long_symbol   text,
  short_strike  numeric,
  long_strike   numeric,
  qty           int,
  entry_date    date,
  entry_credit  numeric,                    -- per share
  mark_debit    numeric,                    -- mid cost to close, per share
  unrealized_pl numeric,
  max_loss      numeric,
  updated_at    timestamptz,
  primary key (bot_id, spread_id)
);

create table if not exists options_live_trades (
  id            text primary key,           -- spread_id-entry_date-exit_date
  spread_id     text not null,
  underlying    text,
  expiry        date,
  short_strike  numeric,
  long_strike   numeric,
  qty           int,
  opened_at     date,
  closed_at     date,
  entry_credit  numeric,
  exit_debit    numeric,
  pnl           numeric,
  pnl_pct       numeric,                    -- pnl / max loss at entry (fraction)
  exit_reason   text                        -- take_profit | sma5_exit | time_exit | stop_loss | expiry_exit | emergency_close
);
create index if not exists options_live_trades_closed on options_live_trades (closed_at desc);

do $$
declare t text;
begin
  foreach t in array array['options_live_runs','options_live_orders','options_live_positions','options_live_trades']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists admin_read on %I', t);
    execute format('create policy admin_read on %I for select to authenticated using (is_dashboard_admin())', t);
  end loop;
end $$;

update bots set description = 'Dip put credit spread (RSI2<10, IBS<0.2, >200DMA) on SPY/QQQ/IWM, Alpaca paper, 15:50 ET.'
where id = 'options';
-- When the paper account is live and cron is installed:
-- update bots set status = 'active' where id = 'options';
