// Market events calendar: kind → category, text code and label, plus the
// session helpers the strips and flags use. Pure, so server and client share it.
// Colors live in globals.css as --ev-<cat> tokens; every chip also carries its
// text code, so color is never the only encoding.
import type { MarketEvent } from "@/lib/types";
import { addDays, isTradingDay, nextTradingDay } from "@/lib/time";

export type EvCat = "fed" | "macro" | "corp" | "opx" | "hol";

export const CATS: { cat: EvCat; code: string; label: string }[] = [
  { cat: "fed", code: "FOMC", label: "Fed & rates" },
  { cat: "macro", code: "CPI", label: "Inflation & jobs" },
  { cat: "corp", code: "ERN", label: "Earnings & company talks" },
  { cat: "opx", code: "OPX", label: "Options expiry" },
  { cat: "hol", code: "HOL", label: "Holidays & half days" },
];

type KindInfo = { cat: EvCat; code: string; label: string };
export const KINDS: Record<string, KindInfo> = {
  FOMC: { cat: "fed", code: "FOMC", label: "FOMC statement" },
  FOMC_PRESSER: { cat: "fed", code: "PRES", label: "FOMC press conference" },
  FOMC_MINUTES: { cat: "fed", code: "MINS", label: "FOMC minutes" },
  FED_SPEECH: { cat: "fed", code: "FED", label: "Fed speech" },
  TREASURY_AUCTION: { cat: "fed", code: "AUCT", label: "Treasury auction" },
  CPI: { cat: "macro", code: "CPI", label: "CPI" },
  PPI: { cat: "macro", code: "PPI", label: "PPI" },
  NFP: { cat: "macro", code: "NFP", label: "Jobs report (NFP)" },
  PCE: { cat: "macro", code: "PCE", label: "PCE inflation" },
  GDP: { cat: "macro", code: "GDP", label: "GDP" },
  RETAIL_SALES: { cat: "macro", code: "RTL", label: "Retail sales" },
  JOBLESS_CLAIMS: { cat: "macro", code: "CLM", label: "Jobless claims" },
  EARNINGS: { cat: "corp", code: "ERN", label: "Earnings" },
  EARNINGS_CALL: { cat: "corp", code: "CALL", label: "Earnings call" },
  INVESTOR_DAY: { cat: "corp", code: "INVD", label: "Investor day" },
  CONFERENCE_TALK: { cat: "corp", code: "TALK", label: "Conference talk" },
  DIVIDEND_EX: { cat: "corp", code: "DIV", label: "Ex-dividend" },
  OPEX: { cat: "opx", code: "OPX", label: "Monthly opex" },
  QUAD_WITCH: { cat: "opx", code: "QUAD", label: "Quad witching" },
  VIX_EXPIRY: { cat: "opx", code: "VIX", label: "VIX expiry" },
  HOLIDAY: { cat: "hol", code: "HOL", label: "Market holiday" },
  HALF_DAY: { cat: "hol", code: "HALF", label: "Early close 13:00" },
};
export const kindInfo = (k: string): KindInfo => KINDS[k] ?? { cat: "corp", code: k.slice(0, 4), label: k.replace(/_/g, " ").toLowerCase() };

/** Kinds that move a single company (for the shorting flags). */
export const COMPANY_KINDS = new Set(["EARNINGS", "EARNINGS_CALL", "INVESTOR_DAY", "CONFERENCE_TALK"]);
/** Index-wide days that matter for an open SPY/QQQ/IWM spread. */
export const SPREAD_RISK_KINDS = new Set(["FOMC", "FOMC_PRESSER", "CPI", "NFP", "PCE", "OPEX", "QUAD_WITCH"]);

export const imp = (e: MarketEvent) => Math.max(1, Math.min(3, Number(e.importance ?? 1)));

/** "08:30", "BMO", "AMC" → display text. */
export const evTime = (e: MarketEvent) => {
  const t = (e.event_time ?? "").trim().toUpperCase();
  if (!t) return e.kind === "HOLIDAY" ? "CLOSED" : "ALL DAY";
  if (t === "BMO") return "PRE-MKT";
  if (t === "AMC") return "AFTER CLOSE";
  return t;
};

/** Sort key within a day: timed events by clock, BMO before the open, AMC after the close. */
const timeKey = (e: MarketEvent) => {
  const t = (e.event_time ?? "").trim().toUpperCase();
  if (t === "BMO") return "07:00";
  if (t === "AMC") return "16:05";
  return /^\d\d:\d\d/.test(t) ? t : "00:00";
};
/** Agenda order: by day, then clock (BMO before the open, AMC after the close), then importance. */
export const sortEvents = (a: MarketEvent, b: MarketEvent) =>
  a.event_date.localeCompare(b.event_date) || timeKey(a).localeCompare(timeKey(b)) || imp(b) - imp(a) || a.title.localeCompare(b.title);
/** Chip order (where only the first few fit): by day, then importance, then clock. */
export const sortByImportance = (a: MarketEvent, b: MarketEvent) =>
  a.event_date.localeCompare(b.event_date) || imp(b) - imp(a) || timeKey(a).localeCompare(timeKey(b)) || a.title.localeCompare(b.title);

export function byDate(events: MarketEvent[], order: "time" | "importance" = "time"): Map<string, MarketEvent[]> {
  const m = new Map<string, MarketEvent[]>();
  for (const e of [...events].sort(order === "time" ? sortEvents : sortByImportance)) m.set(e.event_date, [...(m.get(e.event_date) ?? []), e]);
  return m;
}

/** Today plus the next n sessions (today is kept even on a weekend or holiday, so a HOL chip still shows). */
export function sessionDays(today: string, n: number): string[] {
  const out = [today];
  let d = today;
  for (let i = 0; i < n; i++) { d = nextTradingDay(d); out.push(d); }
  return out;
}

/** The ET date `n` sessions after `today` (n=0 → today or the next session). */
export function sessionsAhead(today: string, n: number) {
  let d = isTradingDay(today) ? today : nextTradingDay(today);
  for (let i = 0; i < n; i++) d = nextTradingDay(d);
  return d;
}

/** Company events for `ticker` from today through `n` sessions ahead. */
export function companyEventsWithin(events: MarketEvent[], ticker: string, today: string, n = 2) {
  const end = sessionsAhead(today, n);
  return events.filter((e) => e.ticker === ticker && COMPANY_KINDS.has(e.kind) && e.event_date >= today && e.event_date <= end).sort(sortEvents);
}

/** Market-wide risk days from today through `until` (an expiry). */
export function riskDaysUntil(events: MarketEvent[], today: string, until: string) {
  return events.filter((e) => SPREAD_RISK_KINDS.has(e.kind) && !e.ticker && e.event_date >= today && e.event_date <= until).sort(sortEvents);
}

/** Calendar weeks (Mon–Sun) covering a month, as ISO dates. */
export function monthGrid(ym: string): string[][] {
  const first = `${ym}-01`;
  const dow = new Date(first + "T12:00:00Z").getUTCDay();
  let d = addDays(first, -((dow + 6) % 7));
  const weeks: string[][] = [];
  do {
    weeks.push(Array.from({ length: 7 }, (_, i) => addDays(d, i)));
    d = addDays(d, 7);
  } while (d.slice(0, 7) === ym);
  return weeks;
}

export const monthName = (ym: string) =>
  new Date(ym + "-15T12:00:00Z").toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).toUpperCase();
export const addMonths = (ym: string, n: number) => {
  const [y, m] = ym.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1 + n, 15));
  return t.toISOString().slice(0, 7);
};
