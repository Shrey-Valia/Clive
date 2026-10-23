// npm test: node --test with type stripping (tests/register.mjs resolves the TS imports).
import { test } from "node:test";
import assert from "node:assert/strict";
import { currentFlow, optionsNextDecision, shortingNextAction, sleeveStatus } from "../lib/next-action.ts";
import { performance } from "../lib/analytics.ts";
import { componentsOf } from "../lib/health.ts";
import type { Run } from "../lib/types.ts";

// 2026-10-23 is a Friday (EDT, UTC-4); the next session is Mon 10-26. 2026-11-27 closes 13:00.
const et = (iso: string, hm: string) => new Date(`${iso}T${hm}:00-04:00`);
const etStd = (iso: string, hm: string) => new Date(`${iso}T${hm}:00-05:00`);
const run = (o: Partial<Run>): Run => ({
  id: 1, run_date: "2026-10-23", mode: "execute", status: "picks_saved", started_at: null, n_candidates: 20, n_selected: 5,
  message: null, strategy_version: "intraday_v2", trade_date: "2026-10-26", ...o,
});
const short = { qty: "-10", side: "short" };
const long = { qty: "5", side: "long" };

test("currentFlow: newest row that names a strategy wins, default intraday_v2", () => {
  assert.equal(currentFlow([]), "intraday_v2");
  assert.equal(currentFlow([{ strategy_version: null }]), "intraday_v2");
  assert.equal(currentFlow([{ strategy_version: null }, { strategy_version: "overnight_v1" }]), "overnight_v1");
  assert.equal(currentFlow([{ strategy_version: null }, { strategy_version: "intraday_v2" }, { strategy_version: "overnight_v1" }]), "intraday_v2");
});

test("flat after the scan with saved picks -> next open and count", () => {
  const a = shortingNextAction({ runs: [run({})], positions: [], now: et("2026-10-23", "17:00") });
  assert.equal(a.text, "NEXT: OPEN 09:31 ET MON 10/26 · 5 SAVED PICKS");
  assert.doesNotMatch(a.text, /15:55/);
});

test("saved row with zero picks -> no picks for the next open", () => {
  const a = shortingNextAction({ runs: [run({ n_selected: 0 })], positions: [], now: et("2026-10-23", "17:00") });
  assert.equal(a.text, "NO PICKS FOR THE NEXT OPEN (MON 10/26)");
});

test("no scan row at all after the scan -> no picks", () => {
  const a = shortingNextAction({ runs: [], positions: [], now: et("2026-10-23", "17:00") });
  assert.equal(a.text, "FLAT · NO PICKS FOR THE NEXT OPEN (MON 10/26)");
});

test("failed scan is bad", () => {
  const a = shortingNextAction({ runs: [run({ status: "failed", n_selected: 0 })], positions: [], now: et("2026-10-23", "17:00") });
  assert.equal(a.tone, "bad");
  assert.match(a.text, /SCAN FAILED/);
});

test("dry-run picks say so", () => {
  const a = shortingNextAction({ runs: [run({ mode: "dry_run" })], positions: [], now: et("2026-10-23", "17:00") });
  assert.match(a.text, /DRY RUN, NOTHING WILL OPEN/);
});

test("before the open, yesterday's picks trade TODAY", () => {
  const a = shortingNextAction({ runs: [run({ run_date: "2026-10-22", trade_date: "2026-10-23", n_selected: 4 })], positions: [], now: et("2026-10-23", "08:00") });
  assert.equal(a.text, "NEXT: OPEN 09:31 ET TODAY · 4 SAVED PICKS");
});

test("mid-day flat with no book -> the 15:45 scan comes next; 15:55 is cover + long sleeve", () => {
  const a = shortingNextAction({ runs: [], positions: [], now: et("2026-10-23", "11:00") });
  assert.equal(a.text, "FLAT · NEXT: 15:45 ET SCAN SAVES PICKS FOR THE MON 10/26 09:31 OPEN");
  assert.equal(a.sub, "15:45 SCAN SAVES PICKS · 15:55 COVER + LONG-SLEEVE BUYS");
});

test("holding shorts in the session -> cover time; half day -> 12:55", () => {
  assert.equal(shortingNextAction({ runs: [], positions: [short, short, short], now: et("2026-10-23", "11:00") }).text, "HOLDING 3 SHORTS · COVER 15:55 ET");
  assert.equal(shortingNextAction({ runs: [], positions: [short], now: etStd("2026-11-27", "11:00") }).text, "HOLDING 1 SHORT · COVER 12:55 ET");
});

test("a short held after the cover is a leftover", () => {
  const a = shortingNextAction({ runs: [run({})], positions: [short], now: et("2026-10-23", "17:00") });
  assert.equal(a.tone, "bad");
  assert.match(a.text, /LEFTOVER/);
});

test("overnight longs are the sleeve, not a problem", () => {
  const a = shortingNextAction({ runs: [run({})], positions: [long, long], now: et("2026-10-23", "17:00") });
  assert.equal(a.text, "NEXT: OPEN 09:31 ET MON 10/26 · 5 SAVED PICKS");
  assert.equal(a.sub, "2 LONG-SLEEVE POSITIONS · SOLD AT THE 09:31 OPEN");
});

test("options next decision: 15:45 today, then next session, 12:45 on half days", () => {
  let n = optionsNextDecision([], et("2026-10-23", "10:00"));
  assert.deepEqual([n.date, n.run, n.decide, n.half], ["2026-10-23", "15:45", "15:55", false]);
  n = optionsNextDecision([], et("2026-10-23", "17:00"));
  assert.equal(n.date, "2026-10-26");
  n = optionsNextDecision([{ run_date: "2026-10-23", kind: "daily", status: "executed" }], et("2026-10-23", "15:50"));
  assert.equal(n.date, "2026-10-26");
  assert.equal(n.doneToday, true);
  n = optionsNextDecision([], etStd("2026-11-27", "09:00"));
  assert.deepEqual([n.run, n.decide, n.half], ["12:45", "12:55", true]);
  assert.equal(n.at?.toISOString(), "2026-11-27T17:45:00.000Z");
});

test("sleeve status splits dip and condor", () => {
  const pos = (sleeve: string | null, pl: number) => ({ spread_id: "x", underlying: "SPY", expiry: null, short_symbol: null, long_symbol: null, short_strike: null,
    long_strike: null, qty: 1, entry_date: null, entry_credit: null, mark_debit: null, unrealized_pl: pl, max_loss: 100, updated_at: null, sleeve });
  const s = sleeveStatus([pos(null, 10), pos("dip", 5), pos("condor", -3)],
    [{ kind: "daily", signals: [{ symbol: "SPY", action: "OPEN 1x (ok)" }, { symbol: "QQQ", action: "no signal" }, { symbol: "SPY condor", action: "hold: 1 condor(s) open" }] as never }]);
  assert.deepEqual(s.map((x) => [x.id, x.open, x.openPl, x.lastAction]), [["dip", 2, 15, "1 OPEN · 2 CHECKED"], ["condor", 1, -3, "hold: 1 condor(s) open"]]);
});

test("best/worst day hidden until two P&L days", () => {
  const one = performance([{ snap_date: "2026-10-21", equity: 100000, day_pl: null }, { snap_date: "2026-10-22", equity: 100500, day_pl: 500 }], []);
  assert.equal(one.best, null);
  assert.equal(one.worst, null);
  const two = performance([{ snap_date: "2026-10-21", equity: 100000, day_pl: null }, { snap_date: "2026-10-22", equity: 100500, day_pl: 500 },
    { snap_date: "2026-10-23", equity: 100300, day_pl: -200 }], []);
  assert.equal(two.best?.date, "2026-10-22");
  assert.equal(two.worst?.date, "2026-10-23");
});

test("health: unknown components still get a group", () => {
  const ids = componentsOf([{ component: "scheduler" }, { component: "summary" }, { component: "brand_new" }]).map((c) => c.id);
  assert.equal(ids[1], "readiness");
  assert.ok(ids.includes("alerts"));
  assert.equal(ids[ids.length - 1], "brand_new");
  assert.ok(!ids.includes("summary"));
});
