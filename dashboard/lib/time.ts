// ET calendar helpers shared by server and client. Everything here is in
// America/New_York regardless of the viewer's local zone.

export const TZ = "America/New_York";

/** NYSE full-day holidays (observed dates). */
const HOLIDAYS = new Set([
  "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
  "2026-09-07", "2026-11-26", "2026-12-25",
  "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
  "2027-09-06", "2027-11-25", "2027-12-24",
]);
/** 13:00 ET early closes. */
const EARLY = new Set(["2026-11-27", "2026-12-24", "2027-11-26"]);

export type EtParts = { date: string; y: number; m: number; d: number; dow: number; mins: number; secs: number };

/** Wall-clock parts of `at` in ET. */
export function etParts(at: Date = new Date()): EtParts {
  const f = new Intl.DateTimeFormat("en-US", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hourCycle: "h23", weekday: "short",
  });
  const p = Object.fromEntries(f.formatToParts(at).map((x) => [x.type, x.value]));
  const y = Number(p.year), m = Number(p.month), d = Number(p.day);
  const dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday);
  const h = Number(p.hour) % 24, mi = Number(p.minute), s = Number(p.second);
  return { date: `${p.year}-${p.month}-${p.day}`, y, m, d, dow, mins: h * 60 + mi, secs: h * 3600 + mi * 60 + s };
}

const dowOf = (iso: string) => new Date(iso + "T12:00:00Z").getUTCDay();
export const addDays = (iso: string, n: number) => {
  const t = new Date(iso + "T12:00:00Z");
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
};

export const isHoliday = (iso: string) => HOLIDAYS.has(iso);
export const isTradingDay = (iso: string) => { const w = dowOf(iso); return w !== 0 && w !== 6 && !HOLIDAYS.has(iso); };
/** Session close in minutes after midnight ET. */
export const closeMins = (iso: string) => (EARLY.has(iso) ? 13 * 60 : 16 * 60);

export const prevTradingDay = (iso: string) => { let d = addDays(iso, -1); while (!isTradingDay(d)) d = addDays(d, -1); return d; };
export const nextTradingDay = (iso: string) => { let d = addDays(iso, 1); while (!isTradingDay(d)) d = addDays(d, 1); return d; };

/** The bot's daily schedule, minutes after midnight ET, for a given session. */
export function schedule(iso: string) {
  const c = closeMins(iso);
  return { scan: c - 15, trade: c - 5, cutoff: c - 2, close: c, sync: c + 15 };
}

/** Convert an ET wall-clock (date + minutes) to a real Date, DST-safe. */
export function etToDate(iso: string, mins: number): Date {
  const [y, m, d] = iso.split("-").map(Number);
  const guess = Date.UTC(y, m - 1, d, Math.floor(mins / 60), mins % 60);
  // offset = what ET shows for that instant minus the instant itself
  const p = etParts(new Date(guess));
  const shown = Date.UTC(p.y, p.m - 1, p.d, Math.floor(p.mins / 60), p.mins % 60);
  return new Date(guess - (shown - guess));
}

export type NextRun = { kind: "SCAN" | "TRADE" | "SYNC"; at: Date; date: string };

/** Next scheduled bot event after `now`, knowing weekends and holidays. */
export function nextRun(now: Date = new Date()): NextRun {
  const p = etParts(now);
  let day = p.date;
  for (let i = 0; i < 10; i++) {
    if (isTradingDay(day)) {
      const s = schedule(day);
      const steps: [NextRun["kind"], number][] = [["SCAN", s.scan], ["TRADE", s.trade], ["SYNC", s.sync]];
      for (const [kind, m] of steps) {
        const at = etToDate(day, m);
        if (at.getTime() > now.getTime()) return { kind, at, date: day };
      }
    }
    day = addDays(day, 1);
  }
  return { kind: "SCAN", at: etToDate(day, 945), date: day };
}

/** Next rotation (15:55 trade) after now. */
export function nextRotation(now: Date = new Date()): Date {
  const p = etParts(now);
  let day = p.date;
  for (let i = 0; i < 10; i++) {
    if (isTradingDay(day)) {
      const at = etToDate(day, schedule(day).trade);
      if (at.getTime() > now.getTime()) return at;
    }
    day = addDays(day, 1);
  }
  return etToDate(day, 955);
}

export function marketState(now: Date = new Date()) {
  const p = etParts(now);
  if (!isTradingDay(p.date)) return { label: isHoliday(p.date) ? "HOLIDAY" : "CLOSED", cls: "off" };
  const s = schedule(p.date);
  if (p.mins >= 570 && p.mins < s.close) return p.mins >= s.scan ? { label: "ROTATION", cls: "warn" } : { label: "OPEN", cls: "ok" };
  if (p.mins >= 240 && p.mins < 570) return { label: "PRE-MKT", cls: "warn" };
  if (p.mins >= s.close && p.mins < 1200) return { label: "AFTER-HRS", cls: "warn" };
  return { label: "CLOSED", cls: "off" };
}

/** "2H14M", "13:18" style countdown text from milliseconds. */
export function countdown(ms: number, withSecs = false) {
  const t = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  if (withSecs) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  if (h >= 24) return `${Math.floor(h / 24)}D${h % 24}H`;
  return h ? `${h}H${String(m).padStart(2, "0")}M` : `${m}M`;
}

export const hhmm = (mins: number) => `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
