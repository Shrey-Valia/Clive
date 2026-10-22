import { NoAccess, PageHead, Panel } from "@/components/Bits";
import { EvLegend, SampleTag, TwoWeeks } from "@/components/Events";
import { EventsBrowser, MonthCalendar } from "@/components/EventsClient";
import { getEvents } from "@/lib/data";
import { etParts } from "@/lib/time";
import { etTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Events({ searchParams }: { searchParams: Promise<{ k?: string; t?: string; c?: string; d?: string; past?: string }> }) {
  const sp = await searchParams;
  const ev = await getEvents();
  if (!ev) return <NoAccess />;
  const today = etParts().date;
  const kind = sp.k?.toUpperCase(), ticker = sp.t?.toUpperCase();
  return (
    <>
      <PageHead fn="EVT" title="Market events"
        desc={<>Fed, inflation &amp; jobs data, earnings and company talks, options expiries and market holidays · 30 days back, 90 ahead · times ET</>}
        right={<>{ev.sample && <SampleTag />}{ev.updated && <span className="muted small">SYNCED {etTime(ev.updated)} ET</span>}</>}
        keys={[{ n: 91, label: "FOMC", href: "/events?k=FOMC" }, { n: 92, label: "EARNINGS", href: "/events?c=corp" }, { n: 93, label: "CALENDAR", href: "/events#cal" }]} />
      {ev.sample && <div className="dryband">SAMPLE EVENTS · DEMO MODE · MARKET DATES (OPEX, VIX, HOLIDAYS) ARE COMPUTED, MACRO AND COMPANY DATES ARE ILLUSTRATIVE</div>}
      {ev.missing && (
        <div className="empty line warn">
          market_events TABLE NOT FOUND · APPLY <code>supabase/migrations/005_market_events.sql</code>, THEN RUN <code>python3 events/sync_events.py</code>
        </div>
      )}
      <Panel n={1} hot title="This week and next" code="WK" meta={<EvLegend compact />}>
        <TwoWeeks events={ev.events} today={today} />
      </Panel>
      <div className="grid2 evgrid">
        <Panel n={2} title="Agenda" code="AGND" meta="FILTER BY CATEGORY, KIND, TICKER">
          <EventsBrowser key={`${kind}|${ticker}|${sp.c}|${sp.d}`} events={ev.events} today={today} from={ev.from} to={ev.to}
            initial={{ kind, ticker, cat: sp.c, day: sp.d, past: sp.past === "1" }} />
        </Panel>
        <Panel n={3} id="cal" title="Month" code="CAL" meta="TAP A DAY FOR ITS LIST · HOVER A CHIP FOR DETAIL">
          <MonthCalendar events={ev.events} today={today} from={ev.from} to={ev.to} maxChips={5} />
        </Panel>
      </div>
    </>
  );
}
