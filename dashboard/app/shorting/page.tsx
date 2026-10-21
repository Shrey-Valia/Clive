import { EquityChart, PnlBars } from "@/components/Charts";
import { BotPill, NoAccess, Panel } from "@/components/Bits";
import { getShorting } from "@/lib/data";
import { compact, etTime, num, pct, sign, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

const RUN_LABEL: Record<string, string> = {
  executed: "EXECUTED", executed_with_errors: "EXECUTED WITH ERRORS", dry_run_done: "DRY RUN",
  scanned: "SCANNED, NOT TRADED", failed: "FAILED",
};
const CATALYST_LABEL: Record<string, string> = {
  no_news: "NO NEWS", hype: "HYPE", dilution: "DILUTION", unclear: "UNCLEAR",
  real_catalyst: "REAL NEWS", news_unavailable: "FEED DOWN",
};

/** 10-segment bar, 0-1. */
function Meter({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  const filled = Math.round(v * 10);
  return (
    <span className="meter" title={`${Math.round(v * 100)} / 100`}>
      {Array.from({ length: 10 }, (_, i) => <i key={i} className={i < filled ? "f" : ""} />)}
    </span>
  );
}

export default async function Shorting() {
  const s = await getShorting();
  if (!s) return <NoAccess />;
  const { bot, run, candidates, orders, snaps, trades, positions, account } = s;

  const wins = trades.filter((x) => x.pnl > 0).length;
  const totalPnl = trades.reduce((t, x) => t + x.pnl, 0);
  const worst = trades.length ? trades.reduce((a, b) => (b.pnl_pct < a.pnl_pct ? b : a)) : null;
  const avgPct = trades.length ? trades.reduce((t, x) => t + x.pnl_pct, 0) / trades.length : null;
  const openPl = positions.reduce((t, p) => t + Number(p.unrealized_pl), 0);
  const equity = account ? Number(account.equity) : null;
  const gross = positions.reduce((t, p) => t + Math.abs(Number(p.market_value)), 0);

  return (
    <>
      <div className="pagehead">
        <h1>Stock Shorting Bot</h1><span className="fn">SHRT</span>
        <div className="right">
          {bot && <BotPill bot={bot} />}
          <a href="https://app.alpaca.markets/paper/dashboard/overview" target="_blank" rel="noreferrer">ALPACA PAPER ↗</a>
        </div>
        <p>Top 5 end-of-day gainer fades · sized 19% → 15% of equity · entered 15:55 ET · held to the next rotation.</p>
      </div>

      <div className="stats">
        <div><b>{usd(equity)}</b><span>Equity</span></div>
        <div><b className={sign(openPl)}>{usd(openPl, true)}</b><span>Open P&amp;L</span></div>
        <div><b>{equity ? `${((100 * gross) / equity).toFixed(0)}%` : "—"}</b><span>Gross short</span></div>
        <div><b className={sign(totalPnl)}>{usd(totalPnl, true)}</b><span>Closed P&amp;L · {trades.length} trades</span></div>
        <div><b>{trades.length ? pct((100 * wins) / trades.length, 0) : "—"}</b><span>Win rate</span></div>
        <div><b className={sign(avgPct)}>{pct(avgPct, 2, true)}</b><span>Avg trade</span></div>
        <div><b className={worst ? "neg" : ""}>{worst ? pct(worst.pnl_pct, 1, true) : "—"}</b><span>Worst{worst ? ` · ${worst.ticker}` : ""}</span></div>
      </div>

      <Panel n={1} title="Open positions" code="PRTU" meta="LIVE · ALPACA">
        {account === null ? (
          <div className="empty">Alpaca keys aren&apos;t set on the server. Add <code>APCA_API_KEY_ID</code> and <code>APCA_API_SECRET_KEY</code> in Vercel.</div>
        ) : positions.length === 0 ? (
          <div className="empty blink-cursor">FLAT · NEXT FIVE SHORTS OPEN AT 15:55 ET</div>
        ) : (
          <div className="scroll"><table>
            <thead><tr><th>Ticker</th><th>Side</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Last</th><th className="num">Notional</th><th className="num">% Eq</th><th className="num">Today</th><th className="num">Unrealized</th></tr></thead>
            <tbody>{positions.map((p) => (
              <tr key={p.symbol}>
                <td className="amber">{p.symbol}</td><td>{p.side.toUpperCase()}</td>
                <td className="num">{Math.abs(Number(p.qty))}</td><td className="num">{usd(p.avg_entry_price)}</td>
                <td className="num">{usd(p.current_price)}</td><td className="num">{usd(Math.abs(Number(p.market_value)))}</td>
                <td className="num">{equity ? pct((100 * Math.abs(Number(p.market_value))) / equity, 1) : "—"}</td>
                <td className={`num ${sign(p.unrealized_intraday_pl)}`}>{usd(p.unrealized_intraday_pl, true)}</td>
                <td className={`num ${sign(p.unrealized_pl)}`}>{usd(p.unrealized_pl, true)} ({pct(Number(p.unrealized_plpc) * 100, 1, true)})</td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </Panel>

      <Panel n={2} title={`Scan board${run ? ` · ${run.run_date}` : ""}`} code="SCAN"
        meta={run ? `${RUN_LABEL[run.status] ?? run.status} · ${run.n_selected ?? 0}/${run.n_candidates ?? 0} PICKED · ${etTime(run.started_at)}` : undefined}>
        {run?.status === "failed" && <div className="empty neg">RUN FAILED: {run.message}</div>}
        {!run ? (
          <div className="empty blink-cursor">NO SCANS YET · FIRST SCAN 15:45 ET</div>
        ) : (
          <div className="scroll"><table>
            <thead><tr>
              <th className="num">#</th><th>Ticker</th><th className="num">Chg</th><th className="num">RVol</th><th className="num">Wick</th>
              <th className="num">SI%</th><th className="num">DTC</th><th className="num">ShVol</th><th className="num">FTD</th>
              <th>Borrow</th><th>News</th><th>1 Move</th><th>2 Crowd</th><th>4 FTD</th><th>5 Story</th><th className="num">Score</th><th>Decision</th><th>Headlines</th>
            </tr></thead>
            <tbody>{candidates.map((c) => (
              <tr key={c.ticker} className={c.selected ? "picked" : c.exclude_reason ? "excluded" : ""}>
                <td className="num">{c.rank}</td>
                <td><span className={c.selected ? "amber" : ""}>{c.ticker}</span><div className="small muted">{compact(c.market_cap)} · {usd(c.close)}</div></td>
                <td className="num pos">{pct(c.change, 1, true)}</td>
                <td className="num">{num(c.rvol)}x</td>
                <td className="num">{c.upper_wick === null ? "—" : pct(Number(c.upper_wick) * 100, 0)}</td>
                <td className="num">{pct(c.si_pct_float)}</td>
                <td className="num">{num(c.days_to_cover)}</td>
                <td className="num">{pct(c.short_vol_ratio_latest, 0)}</td>
                <td className="num">{c.ftd_days ?? "—"}</td>
                <td>{c.shortable === false ? <span className="neg">NO</span> : c.easy_to_borrow === false ? <span className="neg">HTB</span> : <span className="pos">ETB</span>}</td>
                <td className={c.catalyst === "real_catalyst" ? "neg" : ""}>{CATALYST_LABEL[c.catalyst ?? ""] ?? "—"}</td>
                <td><Meter v={c.components?.move_volume} /></td>
                <td><Meter v={c.components?.crowding} /></td>
                <td><Meter v={c.components?.ftd} /></td>
                <td><Meter v={c.components?.catalyst} /></td>
                <td className="num val"><b>{num(c.short_score)}</b></td>
                <td>{c.selected
                  ? <span className="pill warn">SHORT {Math.abs(c.target_qty ?? 0)} · {c.target_pct}%</span>
                  : c.exclude_reason ? <span className="small">{c.exclude_reason.toUpperCase()}</span> : <span className="small muted">BELOW TOP 5</span>}
                  {(c.flags ?? []).length > 0 && <div>{c.flags!.map((f) => <span key={f} className="tag">{f}</span>)}</div>}
                </td>
                <td className="wrapcell small" title={(c.headlines ?? []).join("\n")}><div>{(c.headlines ?? []).slice(0, 2).join(" · ") || "—"}</div></td>
              </tr>))}
            </tbody>
          </table></div>
        )}
        {orders.length > 0 && (
          <div className="scroll"><table>
            <thead><tr><th>Order</th><th>Ticker</th><th>Action</th><th className="num">Qty</th><th>Status</th><th className="num">Fill</th><th>Time</th></tr></thead>
            <tbody>{orders.map((o) => (
              <tr key={o.id}>
                <td className="muted small">{o.id.slice(0, 8)}</td><td className="amber">{o.ticker}</td><td>{o.purpose.toUpperCase()}</td><td className="num">{o.qty}</td>
                <td><span className={`pill ${o.status === "filled" ? "ok" : ["rejected", "canceled", "expired"].includes(o.status) ? "bad" : "warn"}`}>{o.status.toUpperCase()}</span>{o.error && <div className="small neg">{o.error}</div>}</td>
                <td className="num">{usd(o.filled_avg_price)}</td><td>{etTime(o.filled_at ?? o.submitted_at)}</td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </Panel>

      <div className="grid2">
        <Panel n={3} title="Equity" code="GP" meta="DASHED · FIRST TRACKED DAY">
          <EquityChart points={snaps.map((x) => ({ date: x.snap_date, value: x.equity }))} baseline={snaps[0]?.equity} />
        </Panel>
        <Panel n={4} title="Daily P&L" code="PNL">
          <PnlBars points={snaps.map((x) => ({ date: x.snap_date, value: x.day_pl ?? 0 }))} />
        </Panel>
      </div>

      <Panel n={5} title="Trade blotter" code="BLTR" meta="REBUILT FROM ALPACA FILLS">
        {trades.length === 0 ? (
          <div className="empty blink-cursor">NO CLOSED TRADES · TODAY&apos;S SHORTS CLOSE AT TOMORROW&apos;S 15:55 ROTATION</div>
        ) : (
          <div className="scroll"><table>
            <thead><tr><th>Ticker</th><th>Side</th><th className="num">Qty</th><th>Entry</th><th className="num">Entry px</th><th>Exit</th><th className="num">Exit px</th><th className="num">P&amp;L</th><th className="num">%</th></tr></thead>
            <tbody>{trades.map((x) => (
              <tr key={x.id}>
                <td className="amber">{x.ticker}</td><td>{x.side.toUpperCase()}</td><td className="num">{x.qty}</td>
                <td>{etTime(x.entry_at)}</td><td className="num">{usd(x.entry_price)}</td>
                <td>{etTime(x.exit_at)}</td><td className="num">{usd(x.exit_price)}</td>
                <td className={`num ${sign(x.pnl)}`}>{usd(x.pnl, true)}</td><td className={`num ${sign(x.pnl_pct)}`}>{pct(x.pnl_pct, 2, true)}</td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </Panel>
    </>
  );
}
