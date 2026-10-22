"use client";
// Interactive pieces of the events calendar: the month grid (step months,
// tap a day for its list) and the /events browser (filter by category, kind
// and ticker; agenda list).
import { useMemo, useState } from "react";
import type { MarketEvent } from "@/lib/types";
import { CATS, KINDS, addMonths, byDate, imp, kindInfo, monthGrid, monthName, type EvCat } from "@/lib/events";
import { Agenda, EvChip, EvLegend } from "@/components/Events";
import { dayLabel } from "@/lib/format";
import { isHoliday, isTradingDay } from "@/lib/time";

export function MonthCalendar({ events, today, from, to, maxChips = 4 }: { events: MarketEvent[]; today: string; from: string; to: string; maxChips?: number }) {
  const [ym, setYm] = useState(today.slice(0, 7));
  const [day, setDay] = useState<string | null>(null);
  const by = useMemo(() => byDate(events, "importance"), [events]);
  const weeks = monthGrid(ym);
  const canPrev = addMonths(ym, -1) >= from.slice(0, 7), canNext = addMonths(ym, 1) <= to.slice(0, 7);
  const sel = day ? by.get(day) ?? [] : [];
  return (
    <div className="mcal">
      <div className="mcal-nav">
        <button className="chip" disabled={!canPrev} onClick={() => { setYm(addMonths(ym, -1)); setDay(null); }} aria-label="Previous month">◀</button>
        <b>{monthName(ym)}</b>
        <button className="chip" disabled={!canNext} onClick={() => { setYm(addMonths(ym, 1)); setDay(null); }} aria-label="Next month">▶</button>
        {ym !== today.slice(0, 7) && <button className="chip" onClick={() => { setYm(today.slice(0, 7)); setDay(null); }}>TODAY</button>}
        <span className="muted small">DATA {from.slice(5).replace("-", "/")} → {to.slice(5).replace("-", "/")}</span>
      </div>
      <div className="scroll">
        <table className="mgrid" aria-label={`Events in ${monthName(ym)}`}>
          <thead><tr>{["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map((d) => <th key={d}>{d}</th>)}</tr></thead>
          <tbody>
            {weeks.map((w) => (
              <tr key={w[0]}>
                {w.map((d, di) => {
                  const list = by.get(d) ?? [];
                  const out = d.slice(0, 7) !== ym;
                  const closed = !isTradingDay(d);
                  return (
                    <td key={d} className={`${out ? "out" : ""}${d === today ? " today" : ""}${closed ? " closed" : ""}${isHoliday(d) ? " hol" : ""}${day === d ? " sel" : ""}`}>
                      <button className="mg-d" onClick={() => setDay(day === d ? null : d)} aria-label={`${dayLabel(d)}, ${list.length} events`} aria-pressed={day === d}>{Number(d.slice(8))}</button>
                      <div className="mg-chips">
                        {list.slice(0, maxChips).map((e, i) => <EvChip key={i} e={e} align={di > 3 ? "right" : "left"} />)}
                        {list.length > maxChips && <button className="evmore" onClick={() => setDay(d)}>+{list.length - maxChips}</button>}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {day && (
        <div className="mcal-day">
          <div className="subhead">{dayLabel(day)} · {sel.length} EVENT{sel.length === 1 ? "" : "S"} <button className="chip" onClick={() => setDay(null)}>CLOSE</button></div>
          <Agenda events={sel} today={today} empty="NOTHING SCHEDULED" />
        </div>
      )}
    </div>
  );
}

export function EventsBrowser({ events, today, from, to, initial }: {
  events: MarketEvent[]; today: string; from: string; to: string;
  initial: { kind?: string; ticker?: string; cat?: string; day?: string; past?: boolean };
}) {
  const initCats = initial.cat && CATS.some((c) => c.cat === initial.cat) ? [initial.cat as EvCat] : [];
  const [cats, setCats] = useState<EvCat[]>(initCats);
  const [kind, setKind] = useState<string>(initial.kind && KINDS[initial.kind] ? initial.kind : "ALL");
  const [ticker, setTicker] = useState<string>(initial.ticker ?? "ALL");
  const [minImp, setMinImp] = useState(1);
  const [past, setPast] = useState(!!initial.past || (!!initial.day && initial.day < today));
  const tickers = useMemo(() => [...new Set(events.map((e) => e.ticker).filter(Boolean) as string[])].sort(), [events]);
  const kinds = useMemo(() => Object.keys(KINDS).filter((k) => events.some((e) => e.kind === k)), [events]);
  const shown = events.filter((e) =>
    (!cats.length || cats.includes(kindInfo(e.kind).cat)) && (kind === "ALL" || e.kind === kind || (kind === "FOMC" && e.kind.startsWith("FOMC"))) &&
    (ticker === "ALL" || (ticker === "MARKET" ? !e.ticker : e.ticker === ticker)) && imp(e) >= minImp &&
    (past || e.event_date >= today) && (!initial.day || e.event_date >= initial.day || past));
  const toggle = (c: EvCat) => setCats((v) => (v.includes(c) ? v.filter((x) => x !== c) : [...v, c]));
  const counts = Object.fromEntries(CATS.map((c) => [c.cat, events.filter((e) => kindInfo(e.kind).cat === c.cat && (past || e.event_date >= today)).length]));
  return (
    <>
      <div className="bt-filters ev-filters" role="toolbar" aria-label="Filter events">
        <span className="muted">CATEGORY</span>
        {CATS.map((c) => (
          <button key={c.cat} className={`chip evcat ev-${c.cat}${cats.includes(c.cat) ? " on" : ""}`} aria-pressed={cats.includes(c.cat)} onClick={() => toggle(c.cat)}>
            <b>{c.code}</b> {c.label.toUpperCase()} <em>{counts[c.cat]}</em>
          </button>
        ))}
        {cats.length > 0 && <button className="chip" onClick={() => setCats([])}>ALL</button>}
      </div>
      <div className="bt-filters ev-filters" role="toolbar" aria-label="Filter events by kind and ticker">
        <label className="muted" htmlFor="ev-kind">KIND</label>
        <select id="ev-kind" className="sel" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="ALL">ALL KINDS</option>
          {kinds.map((k) => <option key={k} value={k}>{KINDS[k].code} · {KINDS[k].label.toUpperCase()}</option>)}
        </select>
        <label className="muted" htmlFor="ev-tk">TICKER</label>
        <select id="ev-tk" className="sel" value={ticker} onChange={(e) => setTicker(e.target.value)}>
          <option value="ALL">ALL</option>
          <option value="MARKET">MARKET-WIDE ONLY</option>
          {tickers.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span className="muted">IMPORTANCE</span>
        {[1, 2, 3].map((n) => <button key={n} className={`chip${minImp === n ? " on" : ""}`} aria-pressed={minImp === n} onClick={() => setMinImp(n)}>{["", "ALL", "□ MED+", "■ HIGH"][n]}</button>)}
        <button className={`chip${past ? " on" : ""}`} aria-pressed={past} onClick={() => setPast(!past)}>SHOW PAST 30D</button>
      </div>
      <EvLegend />
      <div className="ev-count muted small">{shown.length} EVENT{shown.length === 1 ? "" : "S"} · {past ? from.slice(5).replace("-", "/") : today.slice(5).replace("-", "/")} → {to.slice(5).replace("-", "/")} · TIMES ET</div>
      <Agenda events={shown} today={today} empty="NO EVENTS MATCH THESE FILTERS" />
    </>
  );
}
