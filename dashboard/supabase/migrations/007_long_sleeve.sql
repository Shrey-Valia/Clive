-- Overnight long sleeve (Stock Shorting Bot, config.OVERNIGHT_LONG_ENABLED, off by default).
-- Additive only. The sleeve's orders and trades go in the existing short_orders / short_trades
-- tables (side 'buy'/'sell' on orders, 'long' on trades); these columns tell them apart and keep
-- the prices the fills are judged against. Until this is pushed, db.py drops the unknown columns
-- and keeps writing.

-- short_orders: purpose 'long_entry' (T 15:55 buy), 'long_exit' (T+1 open sell), 'flatten_long' (halt)
alter table short_orders add column if not exists sleeve      text;     -- 'overnight_long'; null = the v2 short
alter table short_orders add column if not exists limit_price numeric;  -- the 15:55 marketable limit (ask x 1.005)
alter table short_orders add column if not exists ref_mid     numeric;  -- IEX quote midpoint when the order was sent
alter table short_orders add column if not exists ref_last    numeric;  -- IEX last trade when the order was sent
create index if not exists short_orders_sleeve on short_orders (sleeve) where sleeve is not null;

-- short_trades: sync.py copies the entry order's sleeve onto the round trip
alter table short_trades add column if not exists sleeve text;
create index if not exists short_trades_sleeve on short_trades (sleeve) where sleeve is not null;

-- alerts.kind gains: long_entry_failed, long_exit_failed (daily.py), long_loss, long_outside_window (monitor.py)
