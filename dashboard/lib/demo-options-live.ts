// DEMO_MODE fixture for the live options paper bot (003_options_live tables).
// SIMULATED: a handful of SPY/QQQ/IWM put credit spreads. Only lib/data.ts imports it.
import type { OptOrder, OptPosition, OptRun, OptTrade, Snap } from "@/lib/types";
import { addDays, etParts, etToDate, isTradingDay, prevTradingDay } from "@/lib/time";

export function demoOptionsLive(today: string) {
  // last session whose 15:50 run and 16:15 sync have happened
  const lastSession = isTradingDay(today) && etParts().mins >= 16 * 60 + 15 ? today : prevTradingDay(today);
  const snaps: Snap[] = [];
  let eq = 10000, d = lastSession;
  const days: string[] = [];
  for (let i = 0; i < 25; i++) { days.unshift(d); d = prevTradingDay(d); }
  days.forEach((day, i) => {
    const pl = i === 0 ? 0 : Math.round((Math.sin(i * 1.7) * 38 + 9) * 100) / 100;
    eq += pl;
    snaps.push({ snap_date: day, equity: Math.round(eq * 100) / 100, day_pl: i === 0 ? null : pl, taken_at: etToDate(day, 16 * 60 + 12).toISOString() });
  });
  const exp = (n: number) => { let x = addDays(lastSession, n); while (new Date(x + "T12:00:00Z").getUTCDay() !== 5) x = addDays(x, 1); return x; };
  const e1 = exp(6), e2 = exp(20);
  const positions: OptPosition[] = [
    { spread_id: `SPY-${e1.replace(/-/g, "")}-P655/645`, underlying: "SPY", expiry: e1, short_symbol: `SPY${e1.slice(2).replace(/-/g, "")}P00655000`,
      long_symbol: `SPY${e1.slice(2).replace(/-/g, "")}P00645000`, short_strike: 655, long_strike: 645, qty: 1, entry_date: days[days.length - 4],
      entry_credit: 1.42, mark_debit: 0.88, unrealized_pl: 54, max_loss: 858, updated_at: snaps[snaps.length - 1].taken_at ?? null },
    { spread_id: `QQQ-${e2.replace(/-/g, "")}-P560/550`, underlying: "QQQ", expiry: e2, short_symbol: `QQQ${e2.slice(2).replace(/-/g, "")}P00560000`,
      long_symbol: `QQQ${e2.slice(2).replace(/-/g, "")}P00550000`, short_strike: 560, long_strike: 550, qty: 1, entry_date: days[days.length - 2],
      entry_credit: 1.65, mark_debit: 1.91, unrealized_pl: -26, max_loss: 835, updated_at: snaps[snaps.length - 1].taken_at ?? null },
  ];
  const at = (day: string, m: number) => etToDate(day, m).toISOString();
  const runs: OptRun[] = days.slice(-6).reverse().map((day, i) => ({
    id: 500 - i, run_date: day, mode: "execute", kind: "daily", status: "executed", started_at: at(day, 950), equity: snaps[snaps.length - 1 - i]?.equity ?? null,
    vix: 16.2 + i * 0.4, vix3m: 18.1 + i * 0.2, kill_switch: false,
    signals: { SPY: { action: i === 3 ? "enter" : "hold" }, QQQ: { action: i === 1 ? "enter" : "hold" }, IWM: { action: "none" } },
    n_exits: i === 2 ? 1 : 0, n_entries: i === 1 || i === 3 ? 1 : 0, message: null,
  }));
  const orders: OptOrder[] = [
    { id: "sim-o-1", run_id: 499, spread_id: positions[1].spread_id, underlying: "QQQ", purpose: "open", qty: 1, filled_qty: 1, limit_prices: [1.75, 1.7, 1.65], avg_price: 1.65,
      status: "filled", attempts: 3, error: null, submitted_at: at(days[days.length - 2], 951) },
    { id: "sim-o-2", run_id: 498, spread_id: "IWM-SIM-P230/225", underlying: "IWM", purpose: "close", qty: 1, filled_qty: 1, limit_prices: [0.4, 0.45], avg_price: 0.45,
      status: "filled", attempts: 2, error: null, submitted_at: at(days[days.length - 3], 952) },
  ];
  const trades: OptTrade[] = [
    { id: "sim-t-1", spread_id: "IWM-SIM-P230/225", underlying: "IWM", expiry: addDays(days[days.length - 3], 9), short_strike: 230, long_strike: 225, qty: 1,
      opened_at: days[days.length - 9], closed_at: days[days.length - 3], entry_credit: 0.95, exit_debit: 0.45, pnl: 50, pnl_pct: 0.123, exit_reason: "take_profit" },
    { id: "sim-t-2", spread_id: "SPY-SIM-P640/630", underlying: "SPY", expiry: addDays(days[days.length - 12], 10), short_strike: 640, long_strike: 630, qty: 1,
      opened_at: days[days.length - 16], closed_at: days[days.length - 12], entry_credit: 1.3, exit_debit: 2.1, pnl: -80, pnl_pct: -0.092, exit_reason: "stop_loss" },
  ];
  return { snaps, positions, runs, orders, trades };
}
