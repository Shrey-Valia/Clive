// Row types shared by server and client code. Mirrors supabase/migrations/001_init.sql.

export type Bot = {
  id: string; name: string; kind: string; status: string; description: string | null;
  run_state: string | null; last_message: string | null; last_heartbeat: string | null;
};
export type Snap = { snap_date: string; equity: number; day_pl: number | null; taken_at?: string | null };
export type Run = {
  id: number; run_date: string; mode: string; status: string; started_at: string | null;
  equity?: number | null; n_candidates: number | null; n_selected: number | null; n_orders?: number | null; message: string | null;
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
  exit_at: string; exit_price: number; pnl: number; pnl_pct: number;
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
