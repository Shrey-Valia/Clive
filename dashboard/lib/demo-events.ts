// DEMO_MODE sample events. Rule-based market dates (opex, VIX, holidays) are
// computed; the macro and company dates are illustrative, not a feed. Every
// row carries source "SAMPLE" and the UI labels it so. Only lib/data.ts
// imports this, behind isDemo.
import type { MarketEvent } from "@/lib/types";
import { addDays, closeMins, isHoliday, isTradingDay, prevTradingDay } from "@/lib/time";

const ev = (event_date: string, kind: string, title: string, importance: number, event_time: string | null = null, ticker: string | null = null): MarketEvent =>
  ({ event_date, kind, title, importance, event_time, ticker, source: "SAMPLE", url: null, tz: "America/New_York" });

const HOLIDAY_NAMES: Record<string, string> = {
  "2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving Day", "2026-12-25": "Christmas Day", "2027-01-01": "New Year's Day",
  "2027-01-18": "Martin Luther King Jr. Day", "2027-02-15": "Washington's Birthday",
};

function thirdFriday(ym: string) {
  const first = `${ym}-01`;
  const dow = new Date(first + "T12:00:00Z").getUTCDay();
  const d = addDays(first, ((5 - dow + 7) % 7) + 14);
  return isTradingDay(d) ? d : prevTradingDay(d);
}
const nextYm = (ym: string) => { const [y, m] = ym.split("-").map(Number); return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`; };

export function demoEvents(from: string, to: string, tickers: { held: string[]; picks: string[] }, today: string): MarketEvent[] {
  const out: MarketEvent[] = [
    // FOMC 2026–27 meeting ends (decision days), 14:00 statement + 14:30 press conference
    ...["2026-09-16", "2026-10-28", "2026-12-09", "2027-01-27"].flatMap((d) => [
      ev(d, "FOMC", "FOMC rate decision", 3, "14:00"), ev(d, "FOMC_PRESSER", "Powell press conference", 3, "14:30"),
      ev(addDays(d, 21), "FOMC_MINUTES", `FOMC minutes (${d.slice(5)} meeting)`, 2, "14:00"),
    ]),
    ev("2026-10-23", "FED_SPEECH", "Gov. Waller on the economic outlook", 2, "10:00"),
    ev("2026-11-05", "FED_SPEECH", "Chair Powell, Q&A at a policy forum", 2, "13:30"),
    ev("2026-10-14", "CPI", "CPI (Sep)", 3, "08:30"), ev("2026-11-12", "CPI", "CPI (Oct)", 3, "08:30"), ev("2026-12-10", "CPI", "CPI (Nov)", 3, "08:30"),
    ev("2026-10-15", "PPI", "PPI (Sep)", 2, "08:30"), ev("2026-11-13", "PPI", "PPI (Oct)", 2, "08:30"), ev("2026-12-11", "PPI", "PPI (Nov)", 2, "08:30"),
    ev("2026-10-02", "NFP", "Employment situation (Sep)", 3, "08:30"), ev("2026-11-06", "NFP", "Employment situation (Oct)", 3, "08:30"),
    ev("2026-12-04", "NFP", "Employment situation (Nov)", 3, "08:30"), ev("2027-01-08", "NFP", "Employment situation (Dec)", 3, "08:30"),
    ev("2026-10-30", "PCE", "PCE price index (Sep)", 2, "08:30"), ev("2026-11-25", "PCE", "PCE price index (Oct)", 2, "08:30"),
    ev("2026-10-29", "GDP", "GDP Q3 advance", 2, "08:30"), ev("2026-11-25", "GDP", "GDP Q3 second estimate", 2, "08:30"),
    ev("2026-10-16", "RETAIL_SALES", "Retail sales (Sep)", 2, "08:30"), ev("2026-11-17", "RETAIL_SALES", "Retail sales (Oct)", 2, "08:30"),
    ev("2026-11-10", "TREASURY_AUCTION", "10-year note auction", 1, "13:00"), ev("2026-11-12", "TREASURY_AUCTION", "30-year bond auction", 1, "13:00"),
    // mega-cap earnings (illustrative timing)
    ev("2026-10-20", "EARNINGS", "NFLX Q3 earnings", 3, "AMC", "NFLX"), ev("2026-10-21", "EARNINGS", "TSLA Q3 earnings", 3, "AMC", "TSLA"),
    ev("2026-10-27", "EARNINGS", "GOOGL Q3 earnings", 3, "AMC", "GOOGL"), ev("2026-10-28", "EARNINGS", "MSFT Q1 FY27 earnings", 3, "AMC", "MSFT"),
    ev("2026-10-28", "EARNINGS", "META Q3 earnings", 3, "AMC", "META"), ev("2026-10-29", "EARNINGS", "AAPL Q4 FY26 earnings", 3, "AMC", "AAPL"),
    ev("2026-10-29", "EARNINGS", "AMZN Q3 earnings", 3, "AMC", "AMZN"), ev("2026-11-18", "EARNINGS", "NVDA Q3 FY27 earnings", 3, "AMC", "NVDA"),
    ev("2026-10-28", "EARNINGS_CALL", "MSFT earnings call", 2, "17:30", "MSFT"),
    ev("2026-11-05", "DIVIDEND_EX", "AAPL ex-dividend $0.27", 1, null, "AAPL"),
    ev("2026-12-18", "DIVIDEND_EX", "SPY ex-dividend", 1, null, "SPY"), ev("2026-12-21", "DIVIDEND_EX", "QQQ ex-dividend", 1, null, "QQQ"),
    ev("2026-11-17", "INVESTOR_DAY", "AMZN AWS investor briefing", 2, "10:00", "AMZN"),
    ev("2026-12-02", "CONFERENCE_TALK", "NVDA CFO at a tech conference", 1, "11:00", "NVDA"),
  ];
  // held / picked names: one earnings and one company talk in the next two sessions, so the /shorting flags have something to show
  let n1 = today; do n1 = addDays(n1, 1); while (!isTradingDay(n1));
  let n2 = n1; do n2 = addDays(n2, 1); while (!isTradingDay(n2));
  if (tickers.held[0]) out.push(ev(n1, "EARNINGS", `${tickers.held[0]} Q3 earnings`, 2, "BMO", tickers.held[0]));
  if (tickers.picks[0]) out.push(ev(n2, "CONFERENCE_TALK", `${tickers.picks[0]} CEO at a small-cap conference`, 1, "09:00", tickers.picks[0]));
  // rule-based market days
  let ym = from.slice(0, 7);
  while (ym <= to.slice(0, 7)) {
    const tf = thirdFriday(ym);
    const quad = ["03", "06", "09", "12"].includes(ym.slice(5));
    out.push(quad ? ev(tf, "QUAD_WITCH", "Quarterly quad witching", 3, "16:00") : ev(tf, "OPEX", "Monthly options expiry", 2, "16:00"));
    let vix = addDays(thirdFriday(nextYm(ym)), -30);
    if (!isTradingDay(vix)) vix = prevTradingDay(vix);
    out.push(ev(vix, "VIX_EXPIRY", "VIX options and futures expiry", 1, "09:30"));
    ym = nextYm(ym);
  }
  for (let d = from; d <= to; d = addDays(d, 1)) {
    if (isHoliday(d)) out.push(ev(d, "HOLIDAY", `${HOLIDAY_NAMES[d] ?? "Market holiday"} · NYSE closed`, 2));
    else if (isTradingDay(d) && closeMins(d) < 16 * 60) out.push(ev(d, "HALF_DAY", "Early close 13:00 ET", 2, "13:00"));
  }
  return out.filter((e) => e.event_date >= from && e.event_date <= to);
}
