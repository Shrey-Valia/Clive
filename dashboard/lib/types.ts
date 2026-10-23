// Row types shared by server and client code. Mirrors supabase/migrations/001_init.sql.

export type Bot = {
  id: string; name: string; kind: string; status: string; description: string | null;
  run_state: string | null; last_message: string | null; last_heartbeat: string | null;
  // 004_bot_ops: the run and the sync report separately, so a good sync can't hide a bad run.
  last_run_status?: string | null; last_run_at?: string | null; last_sync_status?: string | null; last_sync_at?: string | null;
};
export type Snap = { snap_date: string; equity: number; day_pl: number | null; taken_at?: string | null };
export type Run = {
  id: number; run_date: string; mode: string; status: string; started_at: string | null;
  equity?: number | null; n_candidates: number | null; n_selected: number | null; n_orders?: number | null; message: string | null;
  // 004_bot_ops (all optional: older rows and a not-yet-migrated DB lack them).
  // intraday_v2: run_date = scan day T (15:45), trade_date = T+1 (short 09:31, cover 15:55).
  trade_date?: string | null; scan_at?: string | null; entry_at?: string | null; exit_at?: string | null; finished_at?: string | null;
  session_close?: string | null; code_version?: string | null; strategy_version?: string | null; config?: Record<string, unknown> | null;
};
export type Candidate = {
  run_id?: number | null;
  rank: number; ticker: string; company: string | null; sector: string | null; close: number; change: number;
  prev_close?: number | null; high?: number | null; low?: number | null;
  market_cap: number | null; rvol: number | null; upper_wick: number | null; close_in_range: number | null;
  si_pct_float: number | null; days_to_cover: number | null; short_vol_ratio_latest: number | null;
  ftd_days: number | null; shortable: boolean | null; easy_to_borrow: boolean | null; catalyst: string | null;
  headlines: string[] | null; short_score: number; components: Record<string, number | null> | null;
  flags: string[] | null; exclude_reason: string | null; selected: boolean; target_pct: number | null; target_qty: number | null;
};
export type Order = {
  id: string; run_id?: number | null; ticker: string; side: string; qty: number; purpose: string; status: string;
  submitted_at: string | null; filled_qty?: number | null; filled_avg_price: number | null; filled_at: string | null; error: string | null;
};
export type Trade = {
  id: string; ticker: string; side: string; qty: number; entry_at: string; entry_price: number;
  exit_at: string; exit_price: number; pnl: number; pnl_pct: number; run_id?: number | null;
};
export type Position = {
  symbol: string; qty: string; side: string; avg_entry_price: string; current_price: string;
  market_value: string; unrealized_pl: string; unrealized_plpc: string; unrealized_intraday_pl: string;
};
export type Account = { equity: string; last_equity: string; cash: string; buying_power: string; short_market_value: string };
export type LucidAccount = {
  account_id: string; label: string | null; plan: string | null; connection: string | null; balance: number | null;
  day_pl: number | null; max_loss_floor: number | null; profit_target: number | null; open_position: string | null; updated_at: string | null;
};
export type LogLine = { at: string; level: "INFO" | "WARN" | "ERROR"; step: string; message: string };
export type Alert = { level: "bad" | "warn"; text: string; href?: string };
export type DatePt = { date: string; value: number };

/** 004_bot_ops.sync_runs: one row per sync job. */
export type SyncRun = { bot_id: string; at: string; status: string; equity: number | null; n_positions: number | null; n_trades: number | null; message: string | null };
/** 004_bot_ops.alerts: written by monitor.py and the bots; open while resolved_at is null. */
export type BotAlert = { id?: number | string; bot_id: string | null; at: string; level: string; kind: string | null; ticker: string | null; message: string; resolved_at: string | null };

/** 005_market_events. event_time is "HH:MM" ET, or BMO / AMC for earnings, or null. */
export type MarketEvent = {
  id?: number; event_date: string; event_time: string | null; tz?: string | null; kind: string; ticker: string | null;
  title: string; importance: number | null; source: string | null; url: string | null; updated_at?: string | null;
};

// 003_options_live
export type OptRun = {
  id: number; run_date: string; mode: string; kind: string; status: string; started_at: string | null; equity: number | null;
  vix: number | null; vix3m: number | null; kill_switch: boolean | null; signals: Record<string, Record<string, unknown>> | null;
  n_exits: number | null; n_entries: number | null; message: string | null;
};
export type OptOrder = {
  id: string; run_id: number | null; spread_id: string; underlying: string | null; purpose: string | null; qty: number | null; filled_qty: number | null;
  limit_prices: number[] | null; avg_price: number | null; status: string | null; attempts: number | null; error: string | null; submitted_at: string | null;
};
export type OptPosition = {
  spread_id: string; underlying: string | null; expiry: string | null; short_symbol: string | null; long_symbol: string | null;
  short_strike: number | null; long_strike: number | null; qty: number | null; entry_date: string | null; entry_credit: number | null;
  sleeve?: string | null;   // 006_options_sleeves: dip | condor
  mark_debit: number | null; unrealized_pl: number | null; max_loss: number | null; updated_at: string | null;
};
export type OptTrade = {
  id: string; spread_id: string; underlying: string | null; expiry: string | null; short_strike: number | null; long_strike: number | null; qty: number | null;
  opened_at: string | null; closed_at: string | null; entry_credit: number | null; exit_debit: number | null; pnl: number | null; pnl_pct: number | null; exit_reason: string | null;
};
