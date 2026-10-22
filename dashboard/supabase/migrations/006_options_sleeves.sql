-- Options bot: a second strategy sleeve (SPY iron condor, spreads finalist F1) next to the
-- dip put credit spread. Purely additive and idempotent; existing rows become sleeve 'dip'.
--
-- The bot writes the new columns ONLY on condor rows, so dip rows keep working before this
-- migration is applied. Condor rows (orders, positions, trades) need it: until then PostgREST
-- rejects them and the bot just logs the error (Supabase failures never stop trading).
--
-- Condor rows reuse the spread columns for the put side (short_symbol / long_symbol,
-- short_strike / long_strike) and put the call side in the call_* columns below.
-- spread_id for a condor looks like SPY-20261218-IC670/695/825/850 (put long / put short /
-- call short / call long); for a dip spread it stays SPY-20261030-P683/668.

alter table options_live_orders    add column if not exists sleeve text not null default 'dip';

alter table options_live_positions add column if not exists sleeve text not null default 'dip';
alter table options_live_positions add column if not exists call_short_symbol text;
alter table options_live_positions add column if not exists call_long_symbol  text;
alter table options_live_positions add column if not exists call_short_strike numeric;
alter table options_live_positions add column if not exists call_long_strike  numeric;

alter table options_live_trades    add column if not exists sleeve text not null default 'dip';
alter table options_live_trades    add column if not exists call_short_strike numeric;
alter table options_live_trades    add column if not exists call_long_strike  numeric;

do $$
declare t text;
begin
  foreach t in array array['options_live_orders','options_live_positions','options_live_trades']
  loop
    execute format('alter table %I drop constraint if exists %I', t, t || '_sleeve_check');
    execute format('alter table %I add constraint %I check (sleeve in (''dip'', ''condor''))', t, t || '_sleeve_check');
  end loop;
end $$;

create index if not exists options_live_trades_sleeve on options_live_trades (sleeve, closed_at desc);
create index if not exists options_live_orders_sleeve on options_live_orders (sleeve);

-- RLS: these tables already have admin_read (003); new columns inherit it. Nothing to change.
