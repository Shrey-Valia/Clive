-- Options Bot backtest results. Purely additive; same access model as 001:
-- the backtester writes with the service-role key, the dashboard reads as an admin.

create table if not exists options_backtests (
  id            text primary key,            -- strategy id, e.g. spy_put_credit
  name          text not null,
  underlying    text,
  capital       numeric,
  period_start  date,
  period_end    date,
  params        jsonb,
  metrics       jsonb,                       -- cagr, total_return, max_drawdown, sharpe, sortino, win_rate, profit_factor, trades (fractions)
  assumptions   text,                        -- fill model, commissions, slippage
  code_version  text,                        -- git sha of the backtester
  is_benchmark  boolean not null default false,
  generated_at  timestamptz not null default now()
);

create table if not exists options_bt_equity (
  backtest_id text references options_backtests (id) on delete cascade,
  date        date not null,
  value       numeric not null,
  drawdown    numeric,                       -- equity / running max - 1
  primary key (backtest_id, date)
);

create table if not exists options_bt_trades (
  id           bigint generated always as identity primary key,
  backtest_id  text references options_backtests (id) on delete cascade,
  opened_at    timestamptz,
  closed_at    timestamptz,
  symbol       text,
  structure    text,                         -- put_credit_spread | iron_condor | ...
  legs         jsonb,                        -- [{type, strike, expiry, qty, entry, exit}]
  qty          numeric,
  entry_credit numeric,
  exit_debit   numeric,
  pnl          numeric,
  pnl_pct      numeric,
  exit_reason  text
);
create index if not exists options_bt_trades_bt on options_bt_trades (backtest_id, closed_at);

do $$
declare t text;
begin
  foreach t in array array['options_backtests','options_bt_equity','options_bt_trades']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists admin_read on %I', t);
    execute format('create policy admin_read on %I for select to authenticated using (is_dashboard_admin())', t);
  end loop;
end $$;
