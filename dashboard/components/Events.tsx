// Market events: chips, legend, the chrome strip and the two-week agenda.
// No hooks, so server pages and the client calendar both render these.
// Color is never alone: every chip leads with its text code (FOMC, CPI, ERN…),
// and importance is weight plus a marker (■ high, □ medium, · low).
import Link from "next/link";
import type { MarketEvent } from "@/lib/types";
import { CATS, byDate, evTime, imp, kindInfo, sessionDays } from "@/lib/events";
import { addDays } from "@/lib/time";
import { dayLabel, ymd } from "@/lib/format";

const MARK = ["", "·", "□", "■"];

export function EvChip({ e, text = false, date = false, align = "left" }: { e: MarketEvent; text?: boolean; date?: boolean; align?: "left" | "right" }) {
  const k = kindInfo(e.kind);
  const i = imp(e);
  const label = `${k.code}${e.ticker ? ` ${e.ticker}` : ""}`;
  return (
    <span className={`evchip ev-${k.cat} imp${i}`} tabIndex={0} aria-label={`${k.label}: ${e.title}, ${ymd(e.event_date)} ${evTime(e)}, importance ${i} of 3`}>
      <b>{label}</b>{text && <span className="ev-t">{e.title}</span>}
      <span className={`ev-pop${align === "right" ? " r" : ""}`} role="tooltip">
        <span className="ev-pop-h"><i className={`ev-sw ev-${k.cat}`} />{k.code} · {k.label.toUpperCase()}</span>
        <b>{e.title}</b>
        <span>{date ? `${dayLabel(e.event_date)} · ` : ""}{evTime(e)}{e.event_time && /^\d/.test(e.event_time) ? " ET" : ""}</span>
        <span>IMPORTANCE {MARK[i]} {["", "LOW", "MEDIUM", "HIGH"][i]}{e.ticker ? ` · ${e.ticker}` : ""}</span>
        <span className="muted">{e.source === "SAMPLE" ? "SAMPLE DATA · NOT A FEED" : `SOURCE ${(e.source ?? "—").toUpperCase()}`}</span>
      </span>
    </span>
  );
}

export function EvLegend({ compact = false }: { compact?: boolean }) {
  return (
    <div className="evlegend" aria-label="Event colors">
      {CATS.map((c) => (
        <span key={c.cat}><span className={`evchip ev-${c.cat} imp2 static`}><b>{c.code}</b></span>{!compact && <> {c.label}</>}</span>
      ))}
      <span className="muted">■ HIGH · □ MED · · LOW</span>
    </div>
  );
}

export const SampleTag = () => <span className="simtag" title="DEMO_MODE sample events, not the market_events feed">SAMPLE</span>;

/** Top-chrome strip: today plus the next five sessions, highest-importance chips first. */
export function EventStrip({ events, today, sample }: { events: MarketEvent[]; today: string; sample: boolean }) {
  const days = sessionDays(today, 5);
  const by = byDate(events);
  return (
    <div className="evstrip" aria-label="Market events, today and the next five sessions">
      <Link href="/events" className="evstrip-l">EVENTS{sample ? " · SAMPLE" : ""}</Link>
      <div className="evstrip-days">
        {days.map((d, di) => {
          const list = (by.get(d) ?? []).filter((e) => imp(e) >= 2 || e.kind === "HOLIDAY" || e.kind === "HALF_DAY").sort((a, b) => imp(b) - imp(a));
          const shown = list.slice(0, 3);
          return (
            <div key={d} className={`evday${d === today ? " today" : ""}`}>
              <span className="evday-d">{d === today ? "TODAY" : dayLabel(d).slice(0, 3)} <em>{d.slice(5).replace("-", "/")}</em></span>
              {shown.length === 0 ? <span className="zero small">—</span> : shown.map((e, i) => <EvChip key={i} e={e} align={di > 3 ? "right" : "left"} />)}
              {list.length > shown.length && <Link href={`/events?d=${d}`} className="evmore">+{list.length - shown.length}</Link>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Agenda rows for a list of events, grouped by day. */
export function Agenda({ events, today, empty = "NO EVENTS IN THIS RANGE" }: { events: MarketEvent[]; today: string; empty?: string }) {
  const by = byDate(events);
  if (!by.size) return <div className="empty line">{empty}</div>;
  return (
    <div className="agenda">
      {[...by.entries()].map(([d, list]) => (
        <div key={d} className={`ag-day${d === today ? " today" : ""}${d < today ? " past" : ""}`}>
          <div className="ag-d">{dayLabel(d)}{d === today && <b> · TODAY</b>}</div>
          <ul>
            {list.map((e, i) => {
              const k = kindInfo(e.kind);
              return (
                <li key={i} className={`imp${imp(e)}`}>
                  <span className="ag-time">{evTime(e)}</span>
                  <EvChip e={e} />
                  <span className="ag-title">{e.title}</span>
                  <span className="ag-kind muted">{k.label.toUpperCase()}</span>
                  {e.url && <a className="ag-src" href={e.url} target="_blank" rel="noreferrer">SRC ›</a>}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** This week and next, Mon–Fri, chips per day. */
export function TwoWeeks({ events, today }: { events: MarketEvent[]; today: string }) {
  const dow = new Date(today + "T12:00:00Z").getUTCDay();
  const mon = addDays(today, -((dow + 6) % 7));
  const by = byDate(events, "importance");
  const weeks = [0, 7].map((o) => Array.from({ length: 5 }, (_, i) => addDays(mon, o + i)));
  return (
    <div className="twoweeks" role="table" aria-label="Events this week and next">
      {weeks.map((w, wi) => (
        <div key={wi} className="tw-row" role="row">
          <span className="tw-wk" role="rowheader">{wi === 0 ? "THIS WK" : "NEXT WK"}</span>
          {w.map((d, di) => {
            const list = by.get(d) ?? [];
            return (
              <div key={d} role="cell" className={`tw-cell${d === today ? " today" : ""}${d < today ? " past" : ""}`}>
                <span className="tw-d">{dayLabel(d)}</span>
                {list.length === 0 ? <span className="zero small">—</span> : list.slice(0, 5).map((e, i) => <EvChip key={i} e={e} align={di > 2 ? "right" : "left"} />)}
                {list.length > 5 && <Link href={`/events?d=${d}`} className="evmore">+{list.length - 5}</Link>}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/** Inline chips for one ticker or day, used by /shorting and /options flags. */
export function EvChips({ events, date = true }: { events: MarketEvent[]; date?: boolean }) {
  if (!events.length) return null;
  return <span className="evchips">{events.map((e, i) => <EvChip key={i} e={e} date={date} />)}</span>;
}
