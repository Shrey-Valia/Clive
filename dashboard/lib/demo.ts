// DEMO_MODE fixture. The latest run is a real captured scan (demo-scan.json, a
// dry run on 10/20). Everything before it is SIMULATED: ~40 trading days of a
// seeded random walk of the strategy, so every chart has something to draw.
// Only lib/data.ts imports this, and only when isDemo is true.
import demoScan from "@/lib/demo-scan.json";
import type { Account, Bot, Candidate, Order, Position, Run, Snap, Trade } from "@/lib/types";
import { etToDate, nextTradingDay, prevTradingDay } from "@/lib/time";
import { WEIGHTS, COMP_ORDER } from "@/lib/glossary";

/** mulberry32: small deterministic PRNG. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const R = rng(20261020);
const gauss = () => { let u = 0, v = 0; while (!u) u = R(); while (!v) v = R(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
const pickOf = <T,>(a: readonly T[]) => a[Math.floor(R() * a.length)];
const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

const CAPTURED_DATE = "2026-10-20";
const N_DAYS = 40;
const POOL = ["QNRX", "BVTK", "HLXA", "ORMP", "TGLO", "VRPX", "NUVB", "SNTI", "KZIA", "APDN", "CYCN", "PRFX", "MNPR", "ELTK",
  "GNPX", "SOBR", "BNRG", "LIXT", "WINT", "XBIO", "ASNS", "INBS", "OCUP", "PHIO", "RVPH", "SPRC", "TNON", "VINO", "ZVSA", "CNEY"] as const;
const SECTORS = ["Health Technology", "Technology Services", "Finance", "Consumer Services", "Energy Minerals", "Electronic Technology"] as const;
const CATS = ["hype", "no_news", "dilution", "unclear", "hype", "no_news"] as const;
const FLAG_POOL = ["LOW_SI_NO_FORCED_SELLERS", "SMALL_MOVE", "LOW_RVOL", "CLOSING_ON_HIGHS", "HEAVY_SHORT_VOLUME", "NO_SI"] as const;

const days: string[] = [];
{ let d = CAPTURED_DATE; for (let i = 0; i < N_DAYS; i++) { days.unshift(d); d = prevTradingDay(d); } }
const iso = (day: string, mins: number, secs = 0) => new Date(etToDate(day, mins).getTime() + secs * 1000).toISOString();

function score(comps: Record<string, number | null>) {
  let t = 0, w = 0;
  for (const k of COMP_ORDER) { const v = comps[k]; if (v !== null && v !== undefined) { t += WEIGHTS[k] * v; w += WEIGHTS[k]; } }
  return w ? round((100 * t) / w, 1) : 0;
}

function simCandidates(runId: number, equity: number): Candidate[] {
  const n = 16 + Math.floor(R() * 8);
  const used = new Set<string>();
  const list: Candidate[] = [];
  for (let i = 0; i < n; i++) {
    let t = pickOf(POOL); while (used.has(t)) t = pickOf(POOL); used.add(t);
    const change = round(12 + R() * 60, 2), close = round(1 + R() * 14, 2);
    const shortable = R() > 0.5, real = shortable && R() < 0.12;
    const comps = { move_volume: round(R() * 0.8, 3), crowding: R() < 0.1 ? null : round(0.3 + R() * 0.7, 3), ftd: round(R(), 3),
      catalyst: real ? 0 : round(pickOf([0.5, 0.75, 0.9]), 3) };
    const prev = close / (1 + change / 100), high = close * (1 + R() * 0.12), low = prev * (1 + R() * 0.03);
    list.push({ run_id: runId, rank: 0, ticker: t, company: `${t} Holdings (simulated)`, sector: pickOf(SECTORS), close, change,
      prev_close: round(prev), high: round(high), low: round(low),
      market_cap: Math.round(2e7 + R() * 6e8), rvol: round(1 + R() * 12, 2), upper_wick: round(R() * 0.4, 3), close_in_range: round(40 + R() * 60, 1),
      si_pct_float: R() < 0.15 ? null : round(R() * 25, 2), days_to_cover: round(R() * 6, 2), short_vol_ratio_latest: round(20 + R() * 50, 1),
      ftd_days: Math.floor(R() * 8), shortable, easy_to_borrow: shortable, catalyst: real ? "real_catalyst" : pickOf(CATS),
      headlines: [], short_score: score(comps), components: comps,
      flags: FLAG_POOL.filter(() => R() < 0.25), exclude_reason: !shortable ? "not shortable on Alpaca" : real ? "real de-risking catalyst" : null,
      selected: false, target_pct: null, target_qty: null });
  }
  list.sort((a, b) => b.change - a.change).forEach((c, i) => (c.rank = i + 1));
  const elig = list.filter((c) => !c.exclude_reason).sort((a, b) => b.short_score - a.short_score).slice(0, 5);
  elig.forEach((c, i) => { c.selected = true; c.target_pct = 19 - i; c.target_qty = -Math.floor(((19 - i) / 100) * equity / c.close); });
  return list;
}

// ------------------------------------------------------------------ simulate
type Held = { c: Candidate; qty: number; entry: number; price: number; entryAt: string };
const snaps: Snap[] = [];
const runs: Run[] = [];
const orders: Order[] = [];
const allTrades: Trade[] = [];
const candByRun = new Map<number, Candidate[]>();
let equity = 10000;
let book: Held[] = [];
let prevBookDay = -1;

for (let k = 0; k < N_DAYS; k++) {
  const day = days[k];
  // mark the held book to today's close: shorts fade on average, with the odd squeeze
  let dayPl = 0;
  for (const h of book) {
    const g = R() < 0.025 ? 0.1 + R() * 0.15 : -0.008 + 0.05 * gauss();
    const next = Math.max(0.2, h.price * (1 + g));
    dayPl += (h.price - next) * h.qty;
    h.price = next;
  }
  equity += dayPl;
  snaps.push({ snap_date: day, equity: round(equity), day_pl: k === 17 ? null : round(dayPl), taken_at: iso(day, 975, Math.floor(R() * 90)) });
  if (k === N_DAYS - 1) break; // the captured dry run day: no rotation, the book stays open

  const id = 1000 + k;
  const failed = k === 21;
  const cands = failed ? [] : simCandidates(id, equity);
  candByRun.set(id, cands);
  const picks = cands.filter((c) => c.selected);
  const rejectTicker = k === 33 ? picks[4]?.ticker : undefined;
  let nOrders = 0;
  if (!failed) {
    // cover yesterday's book
    for (const h of book) {
      const fill = round(h.price);
      orders.push({ id: `sim-${id}-c-${h.c.ticker}`, run_id: id, ticker: h.c.ticker, side: "buy", qty: h.qty, purpose: "cover", status: "filled",
        submitted_at: iso(day, 955, 3 + nOrders), filled_qty: h.qty, filled_avg_price: fill, filled_at: iso(day, 960, 1), error: null });
      nOrders++;
      allTrades.push({ id: `sim-t-${prevBookDay}-${h.c.ticker}`, ticker: h.c.ticker, side: "short", qty: h.qty, entry_at: h.entryAt,
        entry_price: round(h.entry), exit_at: iso(day, 960, 1), exit_price: fill, pnl: round((h.entry - h.price) * h.qty),
        pnl_pct: round((100 * (h.entry - h.price)) / h.entry, 2) });
    }
    book = [];
    for (const c of picks) {
      const qty = Math.abs(c.target_qty ?? 0);
      const fill = round(c.close * (1 + 0.006 * gauss()));
      const rejected = c.ticker === rejectTicker;
      orders.push({ id: `sim-${id}-s-${c.ticker}`, run_id: id, ticker: c.ticker, side: "sell", qty, purpose: "short",
        status: rejected ? "rejected" : "filled", submitted_at: iso(day, 955, 8 + nOrders), filled_qty: rejected ? 0 : qty,
        filled_avg_price: rejected ? null : fill, filled_at: rejected ? null : iso(day, 960, 1),
        error: rejected ? "asset is not shortable (HTB at submit)" : null });
      nOrders++;
      if (!rejected) book.push({ c, qty, entry: fill, price: fill, entryAt: iso(day, 960, 1) });
    }
    prevBookDay = k;
  }
  runs.push({ id, run_date: day, mode: "execute", status: failed ? "failed" : rejectTicker ? "executed_with_errors" : "executed",
    started_at: iso(day, 945, 4), equity: round(equity), n_candidates: cands.length, n_selected: picks.length, n_orders: nOrders,
    message: failed ? "TradingView returned no gainers" : null });
}

// ------------------------------------------------------------------ captured dry run
const captured = demoScan.candidates as unknown as Candidate[];
// The captured scan is tagged as the new intraday_v2 flow (picks saved at 15:45, entry at the next 09:31 open)
// so the v2 banner and timeline have a row to render; the simulated history stays overnight_v1.
const CAPTURED_RUN: Run = { id: 1000 + N_DAYS - 1, run_date: CAPTURED_DATE, mode: "dry_run", status: "picks_saved",
  started_at: "2026-10-20T19:45:00Z", equity: demoScan.equity, n_candidates: captured.length,
  n_selected: captured.filter((c) => c.selected).length, n_orders: 0, message: null,
  strategy_version: "intraday_v2", trade_date: nextTradingDay(CAPTURED_DATE), scan_at: "2026-10-20T19:45:00Z", code_version: "demo" };
candByRun.set(CAPTURED_RUN.id, captured.map((c) => ({ ...c, run_id: CAPTURED_RUN.id })));
runs.push(CAPTURED_RUN);
runs.reverse(); // newest first, like the real query

const last = snaps[snaps.length - 1], prev = snaps[snaps.length - 2];

export const DEMO_BOTS: Bot[] = [
  { id: "shorting", name: "Stock Shorting Bot", kind: "shorting", status: "active",
    description: "Scans the top 5 end-of-day gainer fades at 15:45 ET, shorts them at the next 09:31 open and covers at 15:55. Alpaca paper.",
    run_state: "ok", last_message: "2026-10-20: dry run, 5 picks", last_heartbeat: "2026-10-20T20:15:12Z" },
  { id: "lucid", name: "Lucid Bot", kind: "lucid", status: "inactive",
    description: "Pine Script alerts → CrossTrade webhook → Tradovate funded accounts.", run_state: null, last_message: null, last_heartbeat: null },
  { id: "options", name: "Options Bot", kind: "options", status: "active", description: "Dip put credit spreads on SPY/QQQ/IWM, Alpaca paper (separate account), 15:45 ET run, decides 15:55.",
    run_state: "ok", last_message: "synced (simulated)", last_heartbeat: new Date(Date.now() - 10 * 3600_000).toISOString() },
];

export const demo = {
  runs,
  latestRun: CAPTURED_RUN,
  candidates: (runId: number) => candByRun.get(runId) ?? [],
  orders,
  snaps,
  trades: [...allTrades].reverse(), // every simulated rotation, newest first
  positions: book.map((h): Position => ({
    symbol: h.c.ticker, qty: String(-h.qty), side: "short", avg_entry_price: String(round(h.entry)), current_price: String(round(h.price)),
    market_value: String(round(-h.qty * h.price)), unrealized_pl: String(round((h.entry - h.price) * h.qty)),
    unrealized_plpc: String(round((h.entry - h.price) / h.entry, 4)), unrealized_intraday_pl: String(round((h.entry - h.price) * h.qty)),
  })),
  account: { equity: String(last.equity), last_equity: String(prev.equity), cash: String(round(last.equity * 1.9)),
    buying_power: String(round(last.equity * 1.1)), short_market_value: String(round(-book.reduce((a, h) => a + h.qty * h.price, 0))) } as Account,
  /** run that opened the currently held book (for SI/DTC/flags in the risk panel) */
  heldRunId: 1000 + prevBookDay,
};
