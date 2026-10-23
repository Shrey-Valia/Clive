import Link from "next/link";
import { DrawdownChart, EquityChart, PnlBars, TradeReturnHistogram } from "@/components/Charts";
import { Abbr, AlertsTable, BotPill, DirChip, Empty, FlagLegend, FlagTags, ModePill, NoAccess, PageHead, Panel, Sgn, Stat, Timeline, TodayBanner } from "@/components/Bits";
import { EvChips, SampleTag } from "@/components/Events";
import { ExposureBars, MonthlyReturnsHeatmap, ScanFunnel, ScoreBoard, ScoreLegend, Sparkline } from "@/components/Viz";
import { ScanBoard } from "@/components/ScanBoard";
import { Num } from "@/components/Live";
import { getEvents, getShorting } from "@/lib/data";
import { attribute, dayTimeline, equitySeries, funnel, isLive, monthlyReturns, performance, risk, systemLog, todayState, tradeDate } from "@/lib/analytics";
import { companyEventsWithin } from "@/lib/events";
import { etParts } from "@/lib/time";
import { currentFlow, shortingNextAction } from "@/lib/next-action";
import { RUN_LABEL } from "@/lib/glossary";
import { dayLabel, etDateOf, etHm, etTime, md, num, pct, usd, ymd } from "@/lib/format";
import type { MarketEvent, Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

const RANGES = { "7d": 7, "30d": 30, all: Infinity } as const;

export default async function Shorting({ searchParams }: { searchParams: Promise<{ bl?: string }> }) {
  const sp = await searchParams;
  const [s, ev] = await Promise.all([getShorting(), getEvents().catch(() => null)]);
  if (!s) return <NoAccess />;
  const { bot, run, runs, candidates, orders, snaps, trades, positions, account, simulated: sim } = s;
  const live = isLive(run);
  const now = new Date();
  const flow = currentFlow(runs);
  const ctx = { runs, bookOrders: s.bookOrders, flow };
  const today = todayState(run, orders, account, now, ctx);
  const steps = dayTimeline(run, orders, snaps, now, { ...ctx, syncs: s.syncs, bot });
  const v2 = flow === "intraday_v2";
  const next = shortingNextAction({ runs, positions: account ? positions : null, now });
  const todayEt = etParts(now).date;
  // company events (earnings, calls, investor days, talks) within 2 sessions, for every pick and held name
  const evBy: Record<string, MarketEvent[]> = {};
  for (const t of new Set([...candidates.filter((c) => c.selected).map((c) => c.ticker), ...positions.map((p) => p.symbol)])) {
    const hit = companyEventsWithin(ev?.events ?? [], t, todayEt, 2);
    if (hit.length) evBy[t] = hit;
  }
  const flagged = Object.keys(evBy);
  const perf = performance(snaps, trades);
  const attr = attribute(trades, s.picks);
  const rk = risk(positions, account, new Map(s.held));
  const eq = equitySeries(snaps);
  const equity = account ? Number(account.equity) : null;
  const openPl = positions.reduce((t, p) => t + Number(p.unrealized_pl), 0);
  const openBasis = positions.reduce((t, p) => t + Math.abs(Number(p.avg_entry_price) * Number(p.qty)), 0);
  const log = systemLog(bot, runs, orders, snaps, s.events, s.syncs);
  const asOf = etHm(now.toISOString());

  // blotter, grouped by rotation (exit) day, filtered
  const range = (sp.bl && sp.bl in RANGES ? sp.bl : "7d") as keyof typeof RANGES;
  const lastDay = trades[0] ? etDateOf(trades[0].exit_at)! : null;
  const cutoff = lastDay && Number.isFinite(RANGES[range]) ? new Date(new Date(lastDay + "T12:00:00Z").getTime() - RANGES[range] * 864e5).toISOString().slice(0, 10) : null;
  const shown = trades.filter((t) => !cutoff || (etDateOf(t.exit_at) ?? "") > cutoff);
  const byDay = new Map<string, Trade[]>();
  for (const t of shown) { const d = etDateOf(t.exit_at) ?? "?"; byDay.set(d, [...(byDay.get(d) ?? []), t]); }
  const daysAsc = [...byDay.keys()].sort();
  const cum = new Map<string, number>();
  daysAsc.reduce((a, d) => { const v = a + byDay.get(d)!.reduce((x, t) => x + t.pnl, 0); cum.set(d, v); return v; }, 0);
  const daysDesc = [...daysAsc].reverse();

  return (
    <>
      <PageHead fn="SHRT" title="Stock Shorting Bot"
        desc={v2
          ? "Top 5 end-of-day gainer fades · scanned 15:45 ET · shorted at the next session's 09:31 open · covered 15:55 the same day · sized 19% → 15% of equity"
          : "Top 5 end-of-day gainer fades · sized 19% → 15% of equity · entered 15:55 ET with market orders · held to the next rotation"}
        right={<>{bot && <BotPill bot={bot} run={run} />}<ModePill run={run} big /></>}
        keys={[{ n: 95, label: "ALPACA", href: "https://app.alpaca.markets/paper/dashboard/overview", ext: true }, { n: 96, label: "EXPORT", href: "/shorting/export" }, { n: 97, label: "RUNS", href: "/shorting/runs" }]} />

      <TodayBanner t={today} />
      <Timeline steps={steps} />
      {v2 && run && <div className="flowline small muted">FLOW <b className="val">INTRADAY_V2</b> · LATEST SCAN {dayLabel(run.run_date)} → TRADES {dayLabel(tradeDate(run))}{run.code_version ? <> · CODE <code>{run.code_version.slice(0, 8)}</code></> : null}</div>}
      {flagged.length > 0 && (
        <div className="empty line warn evflag" role="status">
          <b>EVENT RISK ≤2 SESSIONS</b>{ev?.sample && <SampleTag />}
          {flagged.map((t) => <span key={t}><a href={`#t=${t}`} className="amber b">{t}</a> <EvChips events={evBy[t]} /></span>)}
        </div>
      )}
      {s.alerts.length > 0 && (
        <Panel id="alerts" title="Open alerts" code="ALRT" meta={`${s.alerts.length} OPEN · FROM THE ALERTS TABLE${sim ? " · SAMPLE" : ""}`}>
          <AlertsTable rows={s.alerts} />
        </Panel>
      )}

      {positions.length > 0 && (
        <div className="secstrip" aria-label="Held positions">
          {rk.rows.map((r) => (
            <a key={r.ticker} href={`#t=${r.ticker}`} className="sec"><b>{r.ticker}</b><DirChip side={r.side} /><span>{r.pctEq.toFixed(1)}%</span><Sgn v={r.plpc} kind="pct" />{evBy[r.ticker] && <span className="evmark" title="Company event within 2 sessions">EVT</span>}</a>
          ))}
          {sim && <span className="simtag">SIMULATED</span>}
        </div>
      )}

      <div className="stats">
        <Stat label="DAY P&L" term="DAY P&L" hero sub={<Sgn v={today.dayPct} kind="pct" digits={2} />}><Num v={today.dayPl}><Sgn v={today.dayPl} /></Num></Stat>
        <Stat label="EQUITY" sub={sim ? "SIMULATED" : account ? `LIVE · ${asOf}` : "NO ALPACA KEYS"}>{usd(equity)}</Stat>
        <Stat label="OPEN P&L" term="OPEN P&L" sub={<Sgn v={openBasis ? (100 * openPl) / openBasis : null} kind="pct" />}><Sgn v={positions.length ? openPl : null} /></Stat>
        <Stat label="GROSS" term="GROSS" sub={`${positions.length} POSITIONS`}>{rk.grossPct === null ? "—" : `${rk.grossPct.toFixed(0)}%`}</Stat>
        <Stat label="CLOSED P&L" sub={`${trades.length} TRADES`}><Sgn v={trades.length ? perf.totalPnl : null} /></Stat>
        <Stat label="WIN RATE" sub={perf.winRate === null ? "NO TRADES" : `${perf.wins}/${perf.trades}`}>{perf.winRate === null ? "—" : pct(perf.winRate, 0)}</Stat>
        <Stat label="AVG TRADE" sub="RETURN ON NOTIONAL"><Sgn v={perf.avgPct} kind="pct" digits={2} /></Stat>
        <Stat label="WORST" sub={perf.worstTrade ? `${perf.worstTrade.ticker} · ${md(etDateOf(perf.worstTrade.exit_at))}` : "—"}><Sgn v={perf.worstTrade?.pnl_pct ?? null} kind="pct" /></Stat>
      </div>

      <Panel n={1} title="Open positions" code="PRTU" sim={sim} meta={account ? `${sim ? "SIM" : "LIVE · ALPACA"} · AS OF ${asOf} ET` : "ALPACA OFFLINE"}>
        {v2 && account !== null && positions.length > 0 && <div className={`empty line ${next.tone === "bad" ? "bad" : next.tone === "warn" ? "warn" : ""}`}>{next.text}{next.sub ? <span className="muted"> · {next.sub}</span> : null}</div>}
        {account === null ? (
          <Empty>Alpaca keys aren&apos;t set on the server. Add <code>APCA_API_KEY_ID</code> and <code>APCA_API_SECRET_KEY</code> in Vercel.</Empty>
        ) : positions.length === 0 ? (
          <Empty>{v2 ? next.text : "FLAT · NEXT FIVE SHORTS OPEN AT 15:55 ET"}{!v2 && !live && run ? " · BOT IS IN DRY RUN, NOTHING WILL OPEN" : ""}{v2 && next.sub ? <span className="muted"> · {next.sub}</span> : null}</Empty>
        ) : (
          <>
            <div className="scroll desk-only"><table className="dense fit">
              <thead><tr><th>Ticker</th><th>Side</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Last</th><th className="num">Notional</th><th className="num">% Eq</th><th className="num">Today</th><th className="num">Unrealized</th><th className="num">%</th><th>Events ≤2 sessions</th><th className="fill" /></tr></thead>
              <tbody>{positions.map((p) => (
                <tr key={p.symbol} data-ticker={p.symbol}>
                  <td className="amber b">{p.symbol}</td><td><DirChip side={p.side} /></td>
                  <td className="num">{Math.abs(Number(p.qty))}</td><td className="num">{usd(p.avg_entry_price)}</td>
                  <td className="num">{usd(p.current_price)}</td><td className="num">{usd(Math.abs(Number(p.market_value)))}</td>
                  <td className="num">{equity ? pct((100 * Math.abs(Number(p.market_value))) / equity, 1) : "—"}</td>
                  <td className="num"><Sgn v={Number(p.unrealized_intraday_pl)} /></td>
                  <td className="num"><Sgn v={Number(p.unrealized_pl)} /></td>
                  <td className="num"><Sgn v={Number(p.unrealized_plpc) * 100} kind="pct" /></td><td><EvChips events={evBy[p.symbol] ?? []} /></td><td className="fill" />
                </tr>))}
              </tbody>
            </table></div>
            <div className="mob-only cards">
              {positions.map((p) => (
                <div key={p.symbol} className="card" data-ticker={p.symbol}>
                  <div className="card-top"><span className="b amber">{p.symbol}</span><DirChip side={p.side} /><span>{Math.abs(Number(p.qty))}</span>{evBy[p.symbol] && <EvChips events={evBy[p.symbol]} />}<Sgn v={Number(p.unrealized_pl)} /><Sgn v={Number(p.unrealized_plpc) * 100} kind="pct" /></div>
                  <div className="card-kv"><span>ENTRY <b>{usd(p.avg_entry_price)}</b></span><span>LAST <b>{usd(p.current_price)}</b></span><span>NOTIONAL <b>{usd(Math.abs(Number(p.market_value)), false, 0)}</b></span><span>% EQ <b>{equity ? pct((100 * Math.abs(Number(p.market_value))) / equity, 1) : "—"}</b></span></div>
                </div>
              ))}
            </div>
          </>
        )}
      </Panel>

      <Panel n={2} hot title={<>Scan board{run ? ` · ${ymd(run.run_date)}` : ""}</>} code="SCAN"
        meta={run ? `${RUN_LABEL[run.status] ?? run.status.toUpperCase()} · ${run.n_selected ?? 0}/${run.n_candidates ?? 0} PICKED · ${etTime(run.started_at)} ET` : undefined}>
        {run?.status === "failed" && <div className="empty line bad">RUN FAILED: {run.message} · RERUN: <code>cd &quot;Stock Shorting Bot&quot; &amp;&amp; python daily.py --execute --now</code></div>}
        {!run ? <Empty>NO SCANS YET · FIRST SCAN 15:45 ET</Empty> : (
          <>
            {!live && <div className="dryband">DRY RUN · NO ORDERS WERE SENT · PICKS READ <b>WOULD SHORT</b></div>}
            <div className="legendrow"><ScoreLegend /><FlagLegend /></div>
            <ScanBoard candidates={candidates} live={live} events={evBy} />
          </>
        )}
        {orders.length > 0 && (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Order</th><th>Ticker</th><th>Action</th><th className="num">Qty</th><th>Status</th><th className="num">Fill</th><th>Time ET</th><th className="fill" /></tr></thead>
            <tbody>{orders.map((o) => (
              <tr key={o.id} data-ticker={o.ticker}>
                <td className="muted small">{o.id.slice(0, 8)}</td><td className="amber">{o.ticker}</td><td>{o.purpose.toUpperCase()}</td><td className="num">{o.qty}</td>
                <td><span className={`pill ${o.status === "filled" ? "ok" : ["rejected", "canceled", "expired"].includes(o.status) ? "bad" : "warn"}`}>{o.status.toUpperCase()}</span>{o.error && <div className="small neg">{o.error}</div>}</td>
                <td className="num">{usd(o.filled_avg_price)}</td><td>{etTime(o.filled_at ?? o.submitted_at)}</td><td className="fill" />
              </tr>))}
            </tbody>
          </table></div>
        )}
      </Panel>

      {candidates.length > 0 && (
        <div className="grid2">
          <Panel n={3} title="Score breakdown" code="SCOR" meta={<ScoreLegend />}>
            <ScoreBoard candidates={candidates} />
          </Panel>
          <Panel n={4} title="Scan funnel" code="FNL" meta={run ? ymd(run.run_date) : undefined}>
            <ScanFunnel stages={funnel(candidates)} />
            <p className="note">Hover or focus a stage for the drop reason and the tickers dropped.</p>
          </Panel>
        </div>
      )}

      <Panel n={5} id="risk" title="Risk" code="RISK" sim={sim} meta={`AS OF ${asOf} ET · GAP = A MOVE AGAINST EVERY POSITION`}>
        {!positions.length ? <Empty>FLAT · NO OPEN RISK</Empty> : (
          <div className="riskgrid">
            <div className="stats inner">
              <Stat label="GROSS" term="GROSS" sub={usd(rk.gross, false, 0)}>{rk.grossPct === null ? "—" : `${rk.grossPct.toFixed(0)}%`}</Stat>
              <Stat label="LARGEST" sub={rk.largest?.ticker}>{rk.largest ? `${rk.largest.pctEq.toFixed(1)}%` : "—"}</Stat>
              <Stat label="BP CUSHION" sub="BUYING POWER">{usd(rk.buyingPower, false, 0)}</Stat>
              <Stat label="GAP +20%" sub={<Sgn v={rk.gap20Pct === null ? null : -rk.gap20Pct} kind="pct" />}><Sgn v={-rk.gap20} digits={0} /></Stat>
              <Stat label="GAP +50%" sub={<Sgn v={rk.gap50Pct === null ? null : -rk.gap50Pct} kind="pct" />}><Sgn v={-rk.gap50} digits={0} /></Stat>
            </div>
            <ExposureBars equity={rk.equity} rows={rk.rows.map((r) => ({ ticker: r.ticker, marketValue: r.mv, targetPct: r.targetPct, unrealizedPl: r.unrealized, unrealizedPlpc: r.plpc }))} />
            <div className="scroll"><table className="dense fit">
              <thead><tr><th>Held</th><th>Side</th><th>Sector</th><th className="num"><Abbr term="SI%">SI%</Abbr></th><th className="num"><Abbr term="DTC">DTC</Abbr></th><th>Flags</th><th>Entry run</th><th className="fill" /></tr></thead>
              <tbody>{rk.rows.map((r) => (
                <tr key={r.ticker} data-ticker={r.ticker}><td className="amber">{r.ticker}</td><td><DirChip side={r.side} /></td><td className="small">{(r.sector ?? "—").toUpperCase()}</td>
                  <td className="num">{pct(r.si)}</td><td className="num">{num(r.dtc)}</td><td><FlagTags flags={r.flags} /></td><td>{r.entryRun ? ymd(r.entryRun) : <span className="muted">NOT IN A RECENT RUN</span>}</td><td className="fill" /></tr>
              ))}</tbody>
            </table></div>
            <div className="sectors">
              <span className="muted small">SECTOR CONCENTRATION</span>
              {rk.sectors.map((x) => (
                <div key={x.sector} className="sector"><span>{x.sector.toUpperCase()}</span><span className="ex-track"><i className="ex-pct" style={{ width: `${x.pct}%` }} /></span><em>{x.pct.toFixed(0)}%</em></div>
              ))}
            </div>
          </div>
        )}
      </Panel>

      <Panel n={6} id="perf" title="Performance" code="PERF" sim={sim} meta={`${perf.days} SNAPSHOTS · ${perf.trades} TRADES`}>
        <div className="stats inner">
          <Stat label="MAX DD" term="MAX DD" sub={perf.maxDdDate ? md(perf.maxDdDate) : undefined}><Sgn v={perf.maxDd === null ? null : perf.maxDd * 100} kind="pct" /></Stat>
          <Stat label="CURRENT DD" term="DD"><Sgn v={perf.curDd === null ? null : perf.curDd * 100} kind="pct" /></Stat>
          <Stat label="SHARPE" term="SHARPE" sub={perf.sharpe === null ? "NEEDS 5+ DAYS" : "ANNUALIZED"}>{num(perf.sharpe, 2)}</Stat>
          <Stat label="SORTINO" term="SORTINO">{num(perf.sortino, 2)}</Stat>
          <Stat label="PROFIT FACTOR" term="PF">{perf.pf === null ? "—" : Number.isFinite(perf.pf) ? perf.pf.toFixed(2) : "∞"}</Stat>
          <Stat label="AVG WIN / LOSS"><Sgn v={perf.avgWin} digits={0} /> <span className="muted">/</span> <Sgn v={perf.avgLoss} digits={0} /></Stat>
          {perf.best && perf.worst && perf.pnlDays >= 2 && <>
            <Stat label="BEST DAY" sub={md(perf.best.date)}><Sgn v={perf.best.v} digits={0} /></Stat>
            <Stat label="WORST DAY" sub={md(perf.worst.date)}><Sgn v={perf.worst.v} digits={0} /></Stat>
          </>}
        </div>
        <div className="grid2 tight">
          <div>
            <h3 className="subhead">TRADE RETURNS · 1% BINS</h3>
            <TradeReturnHistogram trades={trades} ariaLabel="Distribution of closed trade returns" />
          </div>
          <div>
            <h3 className="subhead">P&amp;L BY RANK SLOT <span className="muted">· {attr.matched}/{trades.length} TRADES MATCHED TO A PICK{attr.byRunId ? ` · ${attr.byRunId} BY RUN_ID, REST BY TICKER + DATE` : " BY TICKER + DATE"}</span></h3>
            {attr.byRank.length === 0 ? <Empty>NO TRADES MATCHED TO A RUN YET</Empty> : (
              <div className="scroll"><table className="dense">
                <thead><tr><th>Slot</th><th className="num">N</th><th className="num">P&amp;L</th><th className="num">Avg</th><th className="num">Win</th></tr></thead>
                <tbody>{attr.byRank.map((b) => <tr key={b.key}><td className="amber">{b.key}</td><td className="num">{b.n}</td><td className="num"><Sgn v={b.pnl} /></td><td className="num"><Sgn v={b.avgPct} kind="pct" digits={2} /></td><td className="num">{pct(b.winRate, 0)}</td></tr>)}</tbody>
              </table></div>
            )}
            {attr.byCatalyst.length > 0 && (
              <div className="scroll"><table className="dense">
                <thead><tr><th>Catalyst</th><th className="num">N</th><th className="num">P&amp;L</th><th className="num">Avg</th><th>Score bucket</th><th className="num">N</th><th className="num">Avg</th></tr></thead>
                <tbody>{Array.from({ length: Math.max(attr.byCatalyst.length, attr.byScore.length) }, (_, i) => {
                  const c = attr.byCatalyst[i], b = attr.byScore[i];
                  return <tr key={i}>
                    <td>{c?.key ?? ""}</td><td className="num">{c?.n ?? ""}</td><td className="num">{c && <Sgn v={c.pnl} />}</td><td className="num">{c && <Sgn v={c.avgPct} kind="pct" digits={2} />}</td>
                    <td>{b?.key ?? ""}</td><td className="num">{b?.n ?? ""}</td><td className="num">{b && <Sgn v={b.avgPct} kind="pct" digits={2} />}</td></tr>;
                })}</tbody>
              </table></div>
            )}
          </div>
        </div>
        <h3 className="subhead">MONTHLY RETURNS</h3>
        <MonthlyReturnsHeatmap monthly={monthlyReturns(eq)} />
      </Panel>

      <div className="grid2">
        <Panel n={7} title="Equity" code="GP" sim={sim} meta={snaps.length ? `DAILY CLOSE · LAST SYNC ${etTime(snaps[snaps.length - 1].taken_at ?? null)}` : "DAILY CLOSE · 16:15 ET SYNC"}>
          <EquityChart points={eq} baseline={snaps[0]?.equity} ariaLabel="Account equity by day" group="shrt" dayPl={snaps.map((x) => x.day_pl)} />
          <DrawdownChart equity={eq} compact ariaLabel="Drawdown from peak" group="shrt" />
        </Panel>
        <Panel n={8} title="Daily P&L" code="PNL" sim={sim} meta="BEST AND WORST LABELED · FIRST DAY NOT PLOTTED">
          <PnlBars points={snaps.map((x) => ({ date: x.snap_date, value: x.day_pl }))} equity={snaps.map((x) => x.equity)} ariaLabel="Profit and loss by day" group="shrt" />
        </Panel>
      </div>

      <Panel n={9} title="Underwater" code="DD" sim={sim} meta="EQUITY ÷ RUNNING PEAK − 1">
        <DrawdownChart equity={eq} ariaLabel="Drawdown from peak by day" group="shrt" />
      </Panel>

      <Panel n={10} title="Trade blotter" code="BLTR" sim={sim}
        meta={<>{(["7d", "30d", "all"] as const).map((r) => <Link key={r} href={`?bl=${r}#p10`} scroll={false} className={`seg${range === r ? " on" : ""}`}>{r.toUpperCase()}</Link>)}
          {daysAsc.length > 1 && <Sparkline values={daysAsc.map((d) => cum.get(d)!)} baseline={0} ariaLabel="Cumulative realized P&L" />}</>}>
        {trades.length === 0 ? (
          <Empty>{v2 ? "NO CLOSED TRADES · A BOOK CLOSES AT 15:55 ON THE DAY IT OPENS" : <>NO CLOSED TRADES · TODAY&apos;S SHORTS CLOSE AT TOMORROW&apos;S 15:55 ROTATION</>}</Empty>
        ) : (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Ticker</th><th>Side</th><th className="num">Qty</th><th>Entry ET</th><th className="num">Entry px</th><th>Exit ET</th><th className="num">Exit px</th><th className="num">P&amp;L</th><th className="num">%</th><th className="fill" /></tr></thead>
            <tbody>{daysDesc.map((d) => {
              const ts = byDay.get(d)!;
              const sub = ts.reduce((a, t) => a + t.pnl, 0);
              return [
                <tr key={d} className="group"><td colSpan={7}>{v2 ? "EXIT" : "ROTATION"} {dayLabel(d)} · {ts.length} TRADES · CUM <Sgn v={cum.get(d)!} /></td><td className="num"><Sgn v={sub} /></td><td /><td className="fill" /></tr>,
                ...ts.map((x) => (
                  <tr key={x.id} data-ticker={x.ticker}>
                    <td className="amber">{x.ticker}</td><td><DirChip side={x.side} /></td><td className="num">{x.qty}</td>
                    <td>{etTime(x.entry_at)}</td><td className="num">{usd(x.entry_price)}</td>
                    <td>{etTime(x.exit_at)}</td><td className="num">{usd(x.exit_price)}</td>
                    <td className="num"><Sgn v={x.pnl} /></td><td className="num"><Sgn v={x.pnl_pct} kind="pct" digits={2} /></td><td className="fill" />
                  </tr>
                )),
              ];
            })}</tbody>
          </table></div>
        )}
      </Panel>

      <Panel n={11} id="log" title="System log" code="LOG" meta={`${s.events.length ? "RUN_EVENTS · " : ""}RUNS · ORDER ERRORS · ${s.syncs.length ? "SYNC_RUNS" : "SYNC"} · HEARTBEAT`}>
        {log.length === 0 ? <Empty>NO EVENTS YET</Empty> : (
          <ol className="syslog">{log.map((l, i) => (
            <li key={i} className={l.level.toLowerCase()}><time>{etTime(l.at)}</time><b>{l.level}</b><span className="step">{l.step}</span><span>{l.message}</span></li>
          ))}</ol>
        )}
      </Panel>
    </>
  );
}
