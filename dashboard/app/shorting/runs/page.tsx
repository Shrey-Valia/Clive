import Link from "next/link";
import { redirect } from "next/navigation";
import { Abbr, Empty, NoAccess, PageHead, Panel, Sgn, Stat } from "@/components/Bits";
import { Sparkline } from "@/components/Viz";
import { getRuns } from "@/lib/data";
import { runFlow, runRows, runTone, tradeDate } from "@/lib/analytics";
import { RUN_LABEL } from "@/lib/glossary";
import { dayLabel, etHm, ymd } from "@/lib/format";

export const dynamic = "force-dynamic";

const tone = runTone;

export default async function Runs({ searchParams }: { searchParams: Promise<{ d?: string }> }) {
  const sp = await searchParams;
  const r = await getRuns();
  if (!r) return <NoAccess />;
  if (sp.d) {
    const hit = r.runs.find((x) => x.run_date === sp.d);
    if (hit) redirect(`/shorting/runs/${hit.id}`);
  }
  const rows = runRows(r.runs, r.orders, r.trades);
  const live = rows.filter((x) => x.mode === "execute").length;
  const failed = rows.filter((x) => x.status === "failed").length;
  const withErr = rows.filter((x) => x.status === "executed_with_errors").length;
  const pnl = rows.filter((x) => x.nextPnl !== null).reverse();

  return (
    <>
      <PageHead fn="RUNS" title="Run history" desc="Every daily run of the shorting bot: did it scan, did it trade, and what its book made the next day."
        keys={[{ n: 97, label: "SHRT", href: "/shorting" }, { n: 96, label: "EXPORT", href: "/shorting/export" }]} />
      {sp.d && <div className="empty line warn">NO RUN ON {sp.d}. SHOWING ALL RUNS.</div>}
      <div className="stats">
        <Stat label="RUNS" sub={`${live} LIVE · ${rows.length - live} DRY`}>{rows.length}</Stat>
        <Stat label="FAILED" sub={withErr ? `${withErr} WITH ORDER ERRORS` : "NO ORDER ERRORS"}><span className={failed ? "neg" : "zero"}>{failed}</span></Stat>
        <Stat label="NEXT-DAY P&L" sub={`${pnl.length} RUNS WITH CLOSED TRADES`}><Sgn v={pnl.length ? pnl.reduce((a, x) => a + (x.nextPnl ?? 0), 0) : null} /></Stat>
        <Stat label="TREND" sub="CUMULATIVE NEXT-DAY P&L">
          <Sparkline values={pnl.reduce<number[]>((a, x) => [...a, (a[a.length - 1] ?? 0) + (x.nextPnl ?? 0)], [])} width={120} height={24} baseline={0} ariaLabel="Cumulative next-day P&L by run" />
        </Stat>
      </div>
      <Panel n={1} hot title="Runs" code="RUNS" sim={r.simulated} meta="NEWEST FIRST · CLICK A ROW FOR THE DRILL-DOWN">
        {rows.length === 0 ? <Empty>NO RUNS YET · FIRST SCAN 15:45 ET</Empty> : (
          <div className="scroll"><table className="dense fit runs">
            <thead><tr><th>Scan date</th><th>Trades</th><th>Mode</th><th>Status</th><th>Start ET</th><th className="num">Scanned</th><th className="num">Picked</th><th className="num">Orders</th><th className="num">Filled</th><th className="num">Rej</th>
              <th className="num"><Abbr term="ROTATION">Next-day P&amp;L</Abbr></th><th>Message</th><th className="fill" /></tr></thead>
            <tbody>{rows.map((x) => (
              <tr key={x.id} className={x.status === "failed" ? "failrow" : ""}>
                <td><Link href={`/shorting/runs/${x.id}`} className="b">{dayLabel(x.run_date)}</Link> <span className="muted small">{ymd(x.run_date).slice(6)}</span></td>
                <td className="small">{runFlow(x) === "intraday_v2" ? <>{dayLabel(tradeDate(x))} <span className="muted">09:31→15:55</span></> : <span className="muted">15:55→NEXT 15:55</span>}</td>
                <td>{x.mode === "execute" ? <span className="mode live">LIVE</span> : <span className="mode dry">DRY</span>}</td>
                <td><span className={`pill ${tone(x.status)}`}>{RUN_LABEL[x.status] ?? x.status.toUpperCase()}</span></td>
                <td>{etHm(x.started_at)}</td>
                <td className="num">{x.n_candidates ?? "—"}</td><td className="num">{x.n_selected ?? "—"}</td><td className="num">{x.n_orders ?? (x.filled + x.rejected || "—")}</td>
                <td className="num">{x.filled || <span className="zero">0</span>}</td><td className="num">{x.rejected ? <span className="neg">{x.rejected}</span> : <span className="zero">0</span>}</td>
                <td className="num">{x.nextPnl === null ? <span className="muted">—</span> : <><Sgn v={x.nextPnl} /> <span className="muted small">{x.nextN}</span></>}</td>
                <td className="small muted clip">{x.message ?? ""}</td><td className="fill" />
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <p className="note">{rows.some((x) => x.joinedBy === "run_id")
          ? <>P&amp;L joins closed trades to their run by <code>short_trades.run_id</code>; older trades without it fall back to the ET entry date.</>
          : <>P&amp;L joins closed trades to the run whose entry date (scan day for overnight_v1, the next session for intraday_v2) equals the trade&apos;s ET entry date. <code>short_trades.run_id</code> takes over once the bot fills it.</>}</p>
      </Panel>
    </>
  );
}
