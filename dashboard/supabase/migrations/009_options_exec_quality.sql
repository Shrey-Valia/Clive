-- Options bot, engineering round 3 (Options Bot/docs/ENGINEERING_ROUND3.md): execution-quality columns.
-- Purely additive. The bot writes these columns before the migration is applied: live/db.py drops
-- any column PostgREST reports missing and retries, so nothing breaks in between.
--
-- Per order job (one row per spread / condor order job, id = the last Alpaca order id):
--   mid_price      planned net mid at the decision time (per share; credit for opens, debit for closes)
--   natural_price  planned natural price (credit: sell bid - buy ask; debit: buy ask - sell bid)
--   slippage       fill vs mid, per share; positive = worse than mid
--   slippage_usd   slippage * 100 * filled contracts
alter table options_live_orders add column if not exists mid_price     numeric;
alter table options_live_orders add column if not exists natural_price numeric;
alter table options_live_orders add column if not exists slippage      numeric;
alter table options_live_orders add column if not exists slippage_usd  numeric;

-- The options bot now raises alerts rows (004) with bot_id 'options'. Kinds: run_failed, refused,
-- lock_busy, assignment, unpaired_legs, exit_unfilled, expiry_risk, condor_deadline, leftover_orders,
-- account, inflight_recovered, exdiv_risk, resting_order, sync_failed. No schema change needed.

notify pgrst, 'reload schema';
