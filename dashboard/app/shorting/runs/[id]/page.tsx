import { notFound } from "next/navigation";
import { Abbr, Empty, ModePill, NoAccess, PageHead, Panel, Sgn, Stat } from "@/components/Bits";
import { ScanBoard } from "@/components/ScanBoard";
import { ScanFunnel, ScoreBoard, ScoreLegend } from "@/components/Viz";
import { getRun } from "@/lib/data";
import { funnel, runFlow, runStatusLabel, runTone, runTrades, tradeDate } from "@/lib/analytics";
import { RUN_LABEL } from "@/lib/glossary";
import { dayLabel, etDateOf, etHm, etTime, pct, usd, ymd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function RunDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const r = await getRun(Number(id));
  if (!r) return <NoAccess />;
  if (!r.run) notFound();
  const { run, candidates, orders, simulated: sim } = r;
  const live = run.mode === "execute";
  const v2 = runFlow(run) === "intraday_v2";
  const { trades, by: joinedBy } = runTrades(run, r.trades);
  const close = new Map(candidates.map((c) => [c.ticker, c.close]));
  const filled = orders.filter((o) => o.status === "filled").length;
  const rejected = orders.filter((o) => ["rejected", "canceled", "expired"].includes(o.status)).length;
  const nextPnl = trades.reduce((a, t) => a + t.pnl, 0);
  const keys = [
    ...(r.prev ? [{ n: 91, label: "◀ PREV", href: `/shorting/runs/${r.prev}` }] : []),
    ...(r.next ? [{ n: 92, label: "NEXT ▶", href: `/shorting/runs/${r.next}` }] : []),
    { n: 97, label: "ALL RUNS", href: "/shorting/runs" },
  ];

  return (
    <>
      <PageHead fn="RUN" title={`Run ${dayLabel(run.run_date)} · #${run.id}`} desc={`Scanned ${etTime(run.scan_at ?? run.started_at)} ET · ${run.n_candidates ?? 0} names · ${run.n_selected ?? 0} picked${v2 ? ` · trades ${dayLabel(tradeDate(run))}: short 09:31${run.entry_at ? ` (${etHm(run.entry_at)})` : ""} → cover 15:55${run.exit_at ? ` (${etHm(run.exit_at)})` : ""}` : ""}${run.strategy_version ? ` · ${run.strategy_version}` : ""}${run.code_version ? ` · ${run.code_version.slice(0, 8)}` : ""}`}
        right={<><ModePill run={run} big /><span className={`pill ${runTone(run.status)}`}>{runStatusLabel(run.status)}</span></>}
        keys={keys} />

      {run.status === "failed" && (
        <div className="today bad"><b className="t-head">FAILED</b><span className="t-state">{(run.message ?? "unknown error").toUpperCase()}</span>
          <code className="t-rerun">RERUN: cd &quot;Stock Shorting Bot&quot; &amp;&amp; python daily.py --execute --now</code></div>
      )}
      {!live && <div className="dryband">DRY RUN · NO ORDERS WERE SENT · PICKS READ <b>WOULD SHORT</b></div>}

      <div className="stats">
        <Stat label="EQUITY AT RUN">{usd(run.equity ?? null)}</Stat>
        <Stat label="SCANNED" sub={`${run.n_selected ?? 0} PICKED`}>{run.n_candidates ?? "—"}</Stat>
        <Stat label="ORDERS" sub={`${filled} FILLED`}>{orders.length || run.n_orders || 0}</Stat>
        <Stat label="REJECTED"><span className={rejected ? "neg" : "zero"}>{rejected}</span></Stat>
        <Stat label={v2 ? "BOOK P&L" : "NEXT-DAY P&L"} term="ROTATION" sub={trades.length ? `${trades.length} TRADES CLOSED · BY ${joinedBy === "run_id" ? "RUN_ID" : "DATE"}` : live ? "NOT CLOSED YET" : "DRY RUN · NO TRADES"}><Sgn v={trades.length ? nextPnl : null} /></Stat>
      </div>

      {candidates.length > 0 ? (
        <>
          <div className="grid2">
            <Panel n={1} title="Scan funnel" code="FNL" sim={sim}><ScanFunnel stages={funnel(candidates)} /></Panel>
            <Panel n={2} title="Score breakdown" code="SCOR" sim={sim} meta={<ScoreLegend />}><ScoreBoard candidates={candidates} /></Panel>
          </div>
          <Panel n={3} hot title={`Scan board · ${ymd(run.run_date)}`} code="SCAN" sim={sim}>
            <ScanBoard candidates={candidates} live={live} />
          </Panel>
        </>
      ) : <Panel n={1} title="Scan" code="SCAN"><Empty>NO CANDIDATES RECORDED FOR THIS RUN{run.status === "failed" ? " · IT FAILED BEFORE THE SCAN FINISHED" : ""}</Empty></Panel>}

      <Panel n={4} title="Orders & fill quality" code="FILL" sim={sim} meta="SLIP = FILL VS THE SCAN'S CLOSE · SHORTS: HIGHER FILL IS BETTER">
        {orders.length === 0 ? <Empty>{live ? "NO ORDERS RECORDED" : "DRY RUN · NO ORDERS"}</Empty> : (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Ticker</th><th>Action</th><th className="num">Qty</th><th>Status</th><th className="num">Scan close</th><th className="num">Fill</th><th className="num"><Abbr term="SLIP">Slip</Abbr></th><th>Sent ET</th><th className="num">To fill</th><th>Error</th><th className="fill" /></tr></thead>
            <tbody>{orders.map((o) => {
              const c = close.get(o.ticker);
              const opening = o.purpose === "short" || o.purpose === "add_short";
              const slip = opening && c && o.filled_avg_price ? (100 * (o.filled_avg_price - c)) / c : null;
              const ttf = o.filled_at && o.submitted_at ? Math.round((new Date(o.filled_at).getTime() - new Date(o.submitted_at).getTime()) / 1000) : null;
              return (
                <tr key={o.id} data-ticker={o.ticker}>
                  <td className="amber">{o.ticker}</td><td>{o.purpose.toUpperCase()}</td><td className="num">{o.qty}</td>
                  <td><span className={`pill ${o.status === "filled" ? "ok" : ["rejected", "canceled", "expired"].includes(o.status) ? "bad" : "warn"}`}>{o.status.toUpperCase()}</span></td>
                  <td className="num">{opening ? usd(c ?? null) : <span className="muted">—</span>}</td><td className="num">{usd(o.filled_avg_price)}</td>
                  <td className="num"><Sgn v={slip} kind="pct" digits={2} /></td>
                  <td>{etHm(o.submitted_at)}</td><td className="num">{ttf === null ? "—" : ttf >= 60 ? `${Math.floor(ttf / 60)}M${String(ttf % 60).padStart(2, "0")}S` : `${ttf}S`}</td>
                  <td className="small neg">{o.error ?? ""}</td><td className="fill" />
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Panel>

      <Panel n={5} title={v2 ? "Book result" : "Next-day result"} code="RSLT" sim={sim} meta={joinedBy === "run_id" ? "TRADES WITH THIS RUN_ID" : v2 ? "TRADES ENTERED AT THE NEXT SESSION'S OPEN, COVERED 15:55" : "TRADES ENTERED ON THIS RUN'S DATE, CLOSED AT THE NEXT ROTATION"}>
        {trades.length === 0 ? <Empty>{live ? "THIS BOOK HASN'T CLOSED YET" : "DRY RUN · NOTHING TRADED"}</Empty> : (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Ticker</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Exit</th><th>Exit ET</th><th className="num">P&amp;L</th><th className="num">%</th><th className="fill" /></tr></thead>
            <tbody>{trades.map((t) => (
              <tr key={t.id} data-ticker={t.ticker}><td className="amber">{t.ticker}</td><td className="num">{t.qty}</td><td className="num">{usd(t.entry_price)}</td><td className="num">{usd(t.exit_price)}</td>
                <td>{etTime(t.exit_at)}</td><td className="num"><Sgn v={t.pnl} /></td><td className="num"><Sgn v={t.pnl_pct} kind="pct" digits={2} /></td><td className="fill" /></tr>
            ))}
              <tr className="group"><td colSpan={5}>TOTAL · {trades.length} TRADES · WIN {pct((100 * trades.filter((t) => t.pnl > 0).length) / trades.length, 0)}</td><td className="num"><Sgn v={nextPnl} /></td><td /><td className="fill" /></tr>
            </tbody>
          </table></div>
        )}
      </Panel>

      <Panel n={6} title="Run log" code="LOG" meta={r.events.length ? "RUN_EVENTS" : "FROM THE RUN ROW · run_events NOT AVAILABLE YET"}>
        <ol className="syslog">
          {r.events.map((l, i) => <li key={i} className={l.level.toLowerCase()}><time>{etTime(l.at)}</time><b>{l.level}</b><span className="step">{l.step}</span><span>{l.message}</span></li>)}
          {!r.events.length && run.started_at && <li className={run.status === "failed" ? "error" : "info"}><time>{etTime(run.started_at)}</time><b>{run.status === "failed" ? "ERROR" : "INFO"}</b><span className="step">RUN</span><span>{run.status.replace(/_/g, " ")}{run.message ? ` · ${run.message}` : ""}</span></li>}
          {!r.events.length && orders.filter((o) => o.error).map((o) => <li key={o.id} className="error"><time>{etTime(o.submitted_at)}</time><b>ERROR</b><span className="step">ORDER</span><span>{o.ticker}: {o.error}</span></li>)}
        </ol>
      </Panel>
    </>
  );
}
