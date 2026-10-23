// "What happens next" lines for the bot pages. Pure (no I/O, relative imports only) so
// `npm test` can run it under plain node without the Next bundler.
//
// Shorting bot, intraday_v2 (the only live flow since 2026-10):
//   15:45 ET scan on day T  -> picks saved (short_runs row, status picks_saved, trade_date T+1)
//   09:31 ET on T+1         -> short the saved picks (cutoff 09:50)
//   15:55 ET on T+1         -> cover every short + buy the long sleeve (held to the next 09:31)
// Half days move the scan / cover to 12:45 / 12:55.
//
// Options bot: 09:45 expiry sweep, intraday exits every 15 min, 15:45 ET daily run that decides
// at 15:55 (12:45 / 12:55 on half days), 16:15 sync.
import { closeMins, etParts, etToDate, hhmm, isTradingDay, nextTradingDay, type Flow } from "./time";
import type { OptPosition, OptRun, Position, Run } from "./types";

export type Tone = "ok" | "warn" | "bad" | "off" | "idle";
export type NextAction = { tone: Tone; text: string; sub?: string };

/**
 * The flow the bot runs NOW. The newest row that names its strategy decides; rows without
 * strategy_version (written before migration 004, or market_closed rows from older code) are
 * skipped, and with no such row at all the answer is intraday_v2, because overnight_v1 is retired.
 */
export function currentFlow(runs: Pick<Run, "strategy_version">[]): Flow {
  const r = runs.find((x) => !!x.strategy_version);
  return r?.strategy_version === "overnight_v1" ? "overnight_v1" : "intraday_v2";
}

const ENTRY = 9 * 60 + 31, ENTRY_CUTOFF = 9 * 60 + 50;
const dlabel = (iso: string) => {
  const d = new Date(iso + "T12:00:00Z");
  return `${["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][d.getUTCDay()]} ${iso.slice(5, 7)}/${iso.slice(8, 10)}`;
};
const isV2 = (r: Run) => r.strategy_version === "intraday_v2" || (!r.strategy_version && !!r.trade_date);
const bookDate = (r: Run) => r.trade_date ?? nextTradingDay(r.run_date);

export type ShortingNextInput = { runs: Run[]; positions: Pick<Position, "qty" | "side">[] | null; now?: Date };

/** The real next action of the shorting bot (intraday_v2), for the Open positions panel. */
export function shortingNextAction({ runs, positions, now = new Date() }: ShortingNextInput): NextAction {
  const p = etParts(now);
  const trading = isTradingDay(p.date);
  const c = closeMins(p.date);
  const scan = c - 15, exit = c - 5;
  const shorts = (positions ?? []).filter((x) => x.side === "short" || Number(x.qty) < 0).length;
  const longs = (positions ?? []).filter((x) => !(x.side === "short" || Number(x.qty) < 0)).length;

  // the session the next 09:31 entry trades, and the scan that feeds it
  const entryDay = trading && p.mins < ENTRY_CUTOFF ? p.date : nextTradingDay(p.date);
  const book = runs.find((r) => isV2(r) && bookDate(r) === entryDay && r.run_date < entryDay) ?? null;
  const dry = book ? book.mode !== "execute" : false;
  const scanPending = trading && p.mins < scan && entryDay !== p.date;
  const rest = trading && p.mins < exit
    ? `${p.mins < scan ? `${hhmm(scan)} SCAN SAVES PICKS · ` : ""}${hhmm(exit)} COVER + LONG-SLEEVE BUYS`
    : undefined;

  if (shorts > 0) {
    if (trading && p.mins >= ENTRY && p.mins < exit)
      return { tone: "ok", text: `HOLDING ${shorts} SHORT${shorts === 1 ? "" : "S"} · COVER ${hhmm(exit)} ET`, sub: longs ? `${longs} LONG${longs === 1 ? "" : "S"} ALSO HELD` : rest };
    return { tone: "bad", text: `${shorts} SHORT${shorts === 1 ? "" : "S"} HELD OUTSIDE THE 09:31–${hhmm(exit)} WINDOW · LEFTOVER, COVER IT`,
      sub: "v2 never holds a short overnight: daily.py --cover-leftovers --execute in market hours; the 09:31 job covers leftovers first" };
  }

  // flat on shorts: what opens next?
  let text: string, tone: Tone;
  if (book && book.status === "failed") { tone = "bad"; text = `SCAN FAILED · NO PICKS FOR THE ${dlabel(entryDay)} OPEN`; }
  else if (book && book.status === "picks_saved" && (book.n_selected ?? 0) > 0) {
    tone = dry ? "warn" : "idle";
    text = `NEXT: OPEN 09:31 ET ${entryDay === p.date ? "TODAY" : dlabel(entryDay)} · ${book.n_selected} SAVED PICK${book.n_selected === 1 ? "" : "S"}${dry ? " · DRY RUN, NOTHING WILL OPEN" : ""}`;
  } else if (book && ["entered", "entered_with_errors", "executed", "executed_with_errors"].includes(book.status)) {
    tone = "warn"; text = "BOOK ENTERED BUT NO SHORTS HELD · CHECK THE 09:31 FILLS";
  } else if (book && ["exited", "exited_with_errors", "no_entries"].includes(book.status)) {
    tone = "idle"; text = `FLAT · ${book.status === "no_entries" ? "NOTHING ENTERED TODAY" : "BOOK COVERED"}`;
  } else if (book) {
    tone = "idle"; text = `NO PICKS FOR THE NEXT OPEN (${dlabel(entryDay)})`;
  } else if (scanPending) {
    tone = "idle"; text = `FLAT · NEXT: ${hhmm(scan)} ET SCAN SAVES PICKS FOR THE ${dlabel(entryDay)} 09:31 OPEN`;
  } else if (trading && entryDay !== p.date && p.mins >= scan && p.mins < scan + 10) {
    tone = "warn"; text = `FLAT · ${hhmm(scan)} SCAN RUNNING · PICKS FOR THE ${dlabel(entryDay)} OPEN`;
  } else {
    tone = "idle"; text = `FLAT · NO PICKS FOR THE NEXT OPEN (${dlabel(entryDay)})`;
  }
  const sub = longs ? `${longs} LONG-SLEEVE POSITION${longs === 1 ? "" : "S"} · SOLD AT THE 09:31 OPEN` : rest;
  return { tone, text, ...(sub ? { sub } : {}) };
}

// ------------------------------------------------------------------ options
export type OptionsNext = { at: Date | null; date: string; run: string; decide: string; half: boolean; doneToday: boolean };

/** Next options daily decision: the 15:45 ET run (12:45 on half days) deciding at close − 5. */
export function optionsNextDecision(runs: Pick<OptRun, "run_date" | "kind" | "status">[], now = new Date()): OptionsNext {
  const p = etParts(now);
  const daily = (d: string) => runs.some((r) => r.run_date === d && (r.kind ?? "daily") === "daily" && r.status !== "failed");
  let day = p.date;
  const doneToday = isTradingDay(p.date) && daily(p.date);
  for (let i = 0; i < 10; i++) {
    if (isTradingDay(day)) {
      const c = closeMins(day);
      const due = c - 15;
      if (day !== p.date || (p.mins < c - 2 && !doneToday)) {
        return { at: etToDate(day, due), date: day, run: hhmm(due), decide: hhmm(c - 5), half: c < 16 * 60, doneToday };
      }
    }
    day = nextTradingDay(day);
  }
  return { at: null, date: day, run: "15:45", decide: "15:55", half: false, doneToday };
}

export type SleeveStatus = { id: "dip" | "condor"; label: string; open: number; maxLoss: number; openPl: number; lastAction: string | null };

type Sig = { symbol?: string; action?: string; sleeve?: string };
const sigRows = (s: unknown): Sig[] => (Array.isArray(s) ? s : s && typeof s === "object" ? Object.entries(s as Record<string, Sig>).map(([k, v]) => ({ symbol: k, ...(v ?? {}) })) : []) as Sig[];
const isCondorSig = (r: Sig) => r.sleeve === "condor" || /condor/i.test(r.symbol ?? "");

/** Per-sleeve status: open positions (options_live_positions.sleeve, 006) and the latest daily run's plan. */
export function sleeveStatus(positions: (OptPosition & { sleeve?: string | null })[], runs: Pick<OptRun, "kind" | "signals">[]): SleeveStatus[] {
  const last = runs.find((r) => (r.kind ?? "daily") === "daily" && r.signals);
  const sigs = sigRows(last?.signals);
  return (["dip", "condor"] as const).map((id) => {
    const ps = positions.filter((x) => (x.sleeve ?? "dip") === id);
    const ss = sigs.filter((r) => (id === "condor" ? isCondorSig(r) : !isCondorSig(r)));
    let lastAction: string | null = null;
    if (ss.length) {
      const opens = ss.filter((r) => /^OPEN/i.test(r.action ?? "")).length;
      lastAction = id === "condor" ? (ss[0].action ?? null) : opens ? `${opens} OPEN · ${ss.length} CHECKED` : `NO ENTRY · ${ss.length} CHECKED`;
    }
    return {
      id, label: id === "dip" ? "DIP PUT SPREAD" : "SPY IRON CONDOR", open: ps.length,
      maxLoss: ps.reduce((a, x) => a + (x.max_loss ?? 0), 0), openPl: ps.reduce((a, x) => a + (x.unrealized_pl ?? 0), 0), lastAction,
    };
  });
}
