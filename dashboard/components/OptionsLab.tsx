"use client";
// Options backtest lab: comparison table, risk/return scatter, small multiples
// and a strategy detail view. Everything here is BACKTEST · HYPOTHETICAL.

import { Fragment, useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/Bits";
import { DrawdownChart } from "@/components/Charts";
import { MonthlyReturnsHeatmap } from "@/components/Viz";
import { ChartTable, Empty, Tip, fmtTick, md, mdy, niceTicks, sPct, useHover, useWidth, type DatePt } from "@/components/chart-kit";
import type { BtLab, BtStrategy } from "@/lib/options-backtest";

type Key = "name" | "cagr" | "total_return" | "max_drawdown" | "sharpe" | "sortino" | "win_rate" | "profit_factor" | "trades";
const COLS: { k: Key; label: string; kind: "pct" | "num" | "int" | "text"; better: "hi" | "lo" }[] = [
  { k: "name", label: "Strategy", kind: "text", better: "hi" },
  { k: "cagr", label: "CAGR", kind: "pct", better: "hi" },
  { k: "total_return", label: "Total", kind: "pct", better: "hi" },
  { k: "max_drawdown", label: "Max DD", kind: "pct", better: "hi" },
  { k: "sharpe", label: "Sharpe", kind: "num", better: "hi" },
  { k: "sortino", label: "Sortino", kind: "num", better: "hi" },
  { k: "win_rate", label: "Win %", kind: "pct", better: "hi" },
  { k: "profit_factor", label: "PF", kind: "num", better: "hi" },
  { k: "trades", label: "Trades", kind: "int", better: "hi" },
];
const val = (s: BtStrategy, k: Key) => (k === "name" ? s.name : k === "trades" ? s.metrics.trades ?? s.trades_count : s.metrics[k]);
const fmt = (v: number | string | null | undefined, kind: string) =>
  v === null || v === undefined ? "—" : kind === "text" ? String(v) : kind === "pct" ? sPct(Number(v) * 100, 1) : kind === "int" ? String(Math.round(Number(v))) : Number(v).toFixed(2);
const cls = (v: unknown, kind: string) => (kind === "pct" && typeof v === "number" ? (v > 0 ? "pos" : v < 0 ? "neg" : "zero") : "");

/** Index an equity curve to 100 at its first point. */
const indexed = (eq: DatePt[]) => (eq.length ? eq.map((p) => ({ date: p.date, value: (100 * p.value) / eq[0].value })) : []);
const ts = (d: string) => new Date(d + "T12:00:00Z").getTime();

export function OptionsLab({ lab }: { lab: BtLab }) {
  const [sort, setSort] = useState<{ k: Key; dir: 1 | -1 }>({ k: "sharpe", dir: -1 });
  const [sel, setSel] = useState<string | null>(lab.strategies[0]?.id ?? null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("s");
    if (q && lab.strategies.some((s) => s.id === q)) setSel(q);
  }, [lab.strategies]);
  const choose = (id: string) => {
    setSel(id);
    const u = new URL(window.location.href); u.searchParams.set("s", id); window.history.replaceState(null, "", u);
  };
  // 300+ series = strategy x ticker x capital; filter before showing anything.
  const capitals = useMemo(() => [...new Set(lab.strategies.map((x) => x.capital).filter(Boolean))].sort((a, b) => Number(a) - Number(b)), [lab.strategies]);
  const tickers = useMemo(() => [...new Set(lab.strategies.map((x) => x.underlying).filter(Boolean))].sort(), [lab.strategies]);
  const [cap, setCap] = useState<number | null>(() => (capitals.includes(100000) ? 100000 : (capitals[capitals.length - 1] as number) ?? null));
  const [tick, setTick] = useState<string>("ALL");
  const [showAll, setShowAll] = useState(false);
  const pool = useMemo(() => lab.strategies.filter((x) => (cap === null || x.capital === cap) && (tick === "ALL" || x.underlying === tick)), [lab.strategies, cap, tick]);
  const sorted = useMemo(() => [...pool].sort((a, b) => {
    const x = val(a, sort.k), y = val(b, sort.k);
    if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
    return (typeof x === "string" ? x.localeCompare(String(y)) : x - Number(y)) * sort.dir;
  }), [pool, sort]);
  const shown = showAll ? sorted : sorted.slice(0, 25);
  // Keep the detail panel on a run that is visible under the current filters.
  useEffect(() => { if (sorted.length && !sorted.some((x) => x.id === sel)) setSel(sorted[0].id); }, [sorted, sel]);
  const best = (k: Key) => { const vs = pool.map((s) => val(s, k)).filter((v): v is number => typeof v === "number"); return vs.length ? Math.max(...vs) : null; };
  const s = lab.strategies.find((x) => x.id === sel) ?? null;

  if (!lab.strategies.length) return <Panel n={1} title="Backtest lab" code="BT" bt><Empty text="NO STRATEGIES IN THE BACKTEST FILE YET" /></Panel>;

  return (
    <>
      <Panel n={1} hot bt title="Strategy comparison" code="CMP" meta={`${pool.length} RUNS · CLICK A HEADER TO SORT · CLICK A ROW TO SELECT`}>
        <div className="bt-filters" role="toolbar" aria-label="Filter backtests">
          <span className="muted">CAPITAL</span>
          {capitals.map((c) => (
            <button key={String(c)} className={`chip${cap === c ? " on" : ""}`} aria-pressed={cap === c} onClick={() => setCap(c as number)}>${Number(c) / 1000}K</button>
          ))}
          <span className="muted">UNDERLYING</span>
          {["ALL", ...tickers].map((t) => (
            <button key={t} className={`chip${tick === t ? " on" : ""}`} aria-pressed={tick === t} onClick={() => setTick(t as string)}>{t}</button>
          ))}
        </div>
        <div className="scroll"><table className="dense fit bt-table">
          <thead><tr>
            {COLS.map((c) => (
              <th key={c.k} className={c.kind === "text" ? "" : "num"} aria-sort={sort.k === c.k ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                <button className="sortbtn" onClick={() => setSort((p) => ({ k: c.k, dir: p.k === c.k ? (p.dir === 1 ? -1 : 1) : c.kind === "text" ? 1 : -1 }))}>
                  {c.label}{sort.k === c.k ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                </button>
              </th>
            ))}
            <th>Underlying</th><th className="fill" />
          </tr></thead>
          <tbody>{shown.map((x) => (
            <tr key={x.id} className={x.id === sel ? "picked" : ""} onClick={() => choose(x.id)} tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(x.id); } }} aria-selected={x.id === sel}>
              {COLS.map((c) => {
                const v = val(x, c.k);
                const top = c.kind !== "text" && c.k !== "trades" && typeof v === "number" && v === best(c.k);
                return <td key={c.k} className={`${c.kind === "text" ? (x.id === sel ? "amber b" : "b") : "num"} ${cls(v, c.kind)}${top ? " best" : ""}`}>{fmt(v, c.kind)}</td>;
              })}
              <td className="muted">{x.underlying}</td><td className="fill" />
            </tr>
          ))}
            {lab.benchmark && (
              <tr className="bench">
                {COLS.map((c) => <td key={c.k} className={c.kind === "text" ? "muted" : `num muted`}>{c.k === "name" ? lab.benchmark!.name : c.k === "trades" ? "—" : fmt(lab.benchmark!.metrics[c.k], c.kind)}</td>)}
                <td className="muted">BENCHMARK</td><td className="fill" />
              </tr>
            )}
          </tbody>
        </table></div>
        {sorted.length > 25 && (
          <button className="chip" onClick={() => setShowAll((v) => !v)}>{showAll ? "SHOW TOP 25" : `SHOW ALL ${sorted.length}`}</button>
        )}
      </Panel>

      <div className="grid2">
        <Panel n={2} bt title="Risk vs return" code="RR" meta="X = |MAX DD| · Y = CAGR · SIZE = SHARPE">
          <RiskReturnScatter strategies={pool} selected={sel} onSelect={choose} benchmark={lab.benchmark} />
        </Panel>
        <Panel n={3} bt title={s ? `Detail · ${s.name}` : "Detail"} code="DTL" meta={s ? `${s.underlying} · CAPITAL ${s.capital ? `$${s.capital.toLocaleString("en-US")}` : "—"}` : undefined}>
          {s ? <StrategyDetail s={s} bench={lab.benchmark?.equity ?? []} benchName={lab.benchmark?.name ?? "Benchmark"} /> : <Empty text="SELECT A STRATEGY" />}
        </Panel>
      </div>

      <Panel n={4} bt title="Equity, indexed to 100" code="SM" meta="TOP 12 BY CURRENT SORT · SHARED Y-SCALE · GRAY = BENCHMARK">
        <StrategySmallMultiples strategies={sorted.slice(0, 12)} benchmark={lab.benchmark?.equity ?? []} selected={sel} onSelect={choose} />
      </Panel>

      {s && (
        <Panel n={5} bt title={`Monthly returns · ${s.name}`} code="MRET">
          <MonthlyReturnsHeatmap monthly={s.monthly} ariaLabel={`Monthly returns for ${s.name}`} />
        </Panel>
      )}
    </>
  );
}

// ------------------------------------------------------------------ detail
function StrategyDetail({ s, bench, benchName }: { s: BtStrategy; bench: DatePt[]; benchName: string }) {
  const m = s.metrics;
  const params = Object.entries(s.params);
  return (
    <div className="bt-detail">
      <div className="stats inner">
        {([["CAGR", m.cagr, "pct"], ["MAX DD", m.max_drawdown, "pct"], ["SHARPE", m.sharpe, "num"], ["WIN %", m.win_rate, "pct"], ["PF", m.profit_factor, "num"], ["TRADES", m.trades ?? s.trades_count, "int"]] as const).map(([l, v, k]) => (
          <div key={l}><span className="st-l">{l}</span><b className={cls(v, k)}>{fmt(v, k)}</b></div>
        ))}
      </div>
      <OverlayChart series={[{ label: s.name, points: indexed(s.equity), color: "#ffa028" }, ...(bench.length ? [{ label: benchName, points: indexed(bench), color: "#5b636b" }] : [])]} ariaLabel={`${s.name} vs ${benchName}, indexed to 100`} />
      <DrawdownChart equity={s.equity} compact ariaLabel={`${s.name} drawdown`} />
      {params.length > 0 && (
        <dl className="kv params">{params.map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></Fragment>)}</dl>
      )}
    </div>
  );
}

/** Up to 3 series on one indexed scale, direct-labeled at the end. */
function OverlayChart({ series, ariaLabel, height = 180 }: { series: { label: string; points: DatePt[]; color: string }[]; ariaLabel: string; height?: number }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useHover<number>();
  const all = series.flatMap((x) => x.points);
  if (all.length < 2) return <div ref={ref}><Empty text="NO EQUITY CURVE IN THE FILE" /></div>;
  if (w === null) return <div ref={ref} style={{ height }} />;
  const M = { top: 10, right: 92, bottom: 22, left: 44 };
  const t0 = Math.min(...all.map((p) => ts(p.date))), t1 = Math.max(...all.map((p) => ts(p.date)));
  const { ticks, step } = niceTicks(Math.min(...all.map((p) => p.value), 100), Math.max(...all.map((p) => p.value)));
  const iw = w - M.left - M.right, ih = height - M.top - M.bottom;
  const x = (d: string) => M.left + ((ts(d) - t0) / (t1 - t0 || 1)) * iw;
  const y = (v: number) => M.top + ih - ((v - ticks[0]) / (ticks[ticks.length - 1] - ticks[0])) * ih;
  const main = series[0].points;
  const hi = hover !== null && hover < main.length ? hover : null;
  const near = (pts: DatePt[], d: string) => pts.reduce((a, b) => (Math.abs(ts(b.date) - ts(d)) < Math.abs(ts(a.date) - ts(d)) ? b : a), pts[0]);
  // end labels, nudged apart
  const ends = series.map((sr) => ({ sr, y: y(sr.points[sr.points.length - 1]?.value ?? 100) })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 12) ends[i].y = ends[i - 1].y + 12;
  return (
    <div ref={ref} className="chart">
      <svg width={w} height={height} role="img" aria-label={ariaLabel}>
        {ticks.map((t, i) => <g key={i}><line x1={M.left} x2={M.left + iw} y1={y(t)} y2={y(t)} className={t === 100 ? "baseline" : "grid"} /><text x={M.left - 6} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{fmtTick(t, step, "num")}</text></g>)}
        <text x={M.left} y={height - 6} className="axis">{mdy(main[0].date)}</text>
        <text x={M.left + iw} y={height - 6} className="axis" textAnchor="end">{mdy(main[main.length - 1].date)}</text>
        {[...series].reverse().map((sr) => <path key={sr.label} d={sr.points.map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join("")} fill="none" stroke={sr.color} strokeWidth={sr === series[0] ? 2 : 1.5} strokeLinejoin="round" />)}
        {ends.map(({ sr, y: ly }) => <text key={sr.label} x={M.left + iw + 6} y={ly} className="axis lbl" dominantBaseline="middle" fill={sr.color}>{(sr.points[sr.points.length - 1]?.value ?? 100).toFixed(0)} {sr.label.length > 11 ? sr.label.slice(0, 10) + "…" : sr.label}</text>)}
        {hi !== null && <line x1={x(main[hi].date)} x2={x(main[hi].date)} y1={M.top} y2={M.top + ih} className="crosshair" />}
        <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent" tabIndex={0} className="hit" aria-label={`${ariaLabel}. Arrow keys step through dates.`}
          onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(main.length - 1, Math.round(((e.clientX - r.left) / r.width) * (main.length - 1))))); }}
          onPointerLeave={() => setHover(null)} onBlur={() => setHover(null)}
          onKeyDown={(e) => { if (e.key === "ArrowLeft") setHover(Math.max(0, (hi ?? main.length) - 1)); else if (e.key === "ArrowRight") setHover(Math.min(main.length - 1, (hi ?? -1) + 1)); else if (e.key === "Escape") setHover(null); else return; e.preventDefault(); }} />
      </svg>
      {hi !== null && (
        <Tip x={x(main[hi].date)} y={y(main[hi].value)} w={w}>
          <span>{mdy(main[hi].date)}</span>
          {series.map((sr) => { const p = near(sr.points, main[hi].date); return <span key={sr.label}><i className="swatch" style={{ background: sr.color }} />{sr.label}: <b>{p.value.toFixed(1)}</b></span>; })}
        </Tip>
      )}
      <ChartTable caption={ariaLabel} cols={["Date", ...series.map((s) => s.label)]} rows={main.map((p) => [p.date, ...series.map((sr) => near(sr.points, p.date).value.toFixed(1))])} />
    </div>
  );
}

// ------------------------------------------------------------------ scatter
export function RiskReturnScatter({ strategies, selected, onSelect, benchmark, height = 330 }: {
  strategies: BtStrategy[]; selected: string | null; onSelect: (id: string) => void; benchmark: BtLab["benchmark"]; height?: number;
}) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pts = strategies.flatMap((s, i) => s.metrics.cagr !== null && s.metrics.max_drawdown !== null
    ? [{ i, s, dd: Math.abs(s.metrics.max_drawdown) * 100, cagr: s.metrics.cagr * 100, sharpe: s.metrics.sharpe }] : []);
  if (!pts.length) return <div ref={ref}><Empty text="NEEDS CAGR AND MAX DRAWDOWN FOR AT LEAST ONE STRATEGY" /></div>;
  if (w === null) return <div ref={ref} style={{ height }} />;
  const bm = benchmark && benchmark.metrics.cagr !== null && benchmark.metrics.max_drawdown !== null
    ? { dd: Math.abs(benchmark.metrics.max_drawdown) * 100, cagr: benchmark.metrics.cagr * 100 } : null;
  const M = { top: 14, right: 20, bottom: 30, left: 48 };
  const xs = niceTicks(0, Math.max(...pts.map((p) => p.dd), bm?.dd ?? 0) * 1.1, 4);
  const ys = niceTicks(Math.min(0, ...pts.map((p) => p.cagr), bm?.cagr ?? 0), Math.max(...pts.map((p) => p.cagr), bm?.cagr ?? 0) * 1.1, 4);
  const iw = w - M.left - M.right, ih = height - M.top - M.bottom;
  const x = (v: number) => M.left + (v / xs.ticks[xs.ticks.length - 1]) * iw;
  const y = (v: number) => M.top + ih - ((v - ys.ticks[0]) / (ys.ticks[ys.ticks.length - 1] - ys.ticks[0])) * ih;
  const maxSh = Math.max(0.1, ...pts.map((p) => p.sharpe ?? 0));
  const r = (sh: number | null) => 5 + (Math.max(0, sh ?? 0) / maxSh) * 7;
  // Pareto front: nobody has both lower DD and higher CAGR
  const front = pts.filter((p) => !pts.some((q) => q !== p && q.dd <= p.dd && q.cagr >= p.cagr && (q.dd < p.dd || q.cagr > p.cagr))).sort((a, b) => a.dd - b.dd);
  const hp = hover !== null ? pts.find((p) => p.i === hover) ?? null : null;
  function move(e: React.PointerEvent<SVGRectElement>) {
    const b = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - b.left + M.left, my = e.clientY - b.top + M.top;
    let best = pts[0], bd = Infinity;
    for (const p of pts) { const d = (x(p.dd) - mx) ** 2 + (y(p.cagr) - my) ** 2; if (d < bd) { bd = d; best = p; } }
    setHover(bd < 60 ** 2 ? best.i : null);
  }
  return (
    <div ref={ref} className="chart">
      <svg width={w} height={height} role="img" aria-label="Risk versus return by strategy">
        {xs.ticks.map((t, i) => <g key={`x${i}`}>{t > 0 && <line x1={x(t)} x2={x(t)} y1={M.top} y2={M.top + ih} className="grid" />}<text x={x(t)} y={height - 14} className="axis" textAnchor="middle">{fmtTick(t, xs.step, "pct")}</text></g>)}
        {ys.ticks.map((t, i) => <g key={`y${i}`}><line x1={M.left} x2={M.left + iw} y1={y(t)} y2={y(t)} className={t === 0 ? "baseline" : "grid"} /><text x={M.left - 6} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{fmtTick(t, ys.step, "pct")}</text></g>)}
        <text x={M.left + iw} y={height - 2} className="axis" textAnchor="end">|MAX DRAWDOWN| →</text>
        <text x={M.left + 4} y={M.top + 8} className="axis">↑ CAGR</text>
        {front.length > 1 && <path d={front.map((p, i) => `${i ? "L" : "M"}${x(p.dd)},${y(p.cagr)}`).join("")} fill="none" stroke="var(--rule-strong)" strokeWidth={1} />}
        {bm && <g><rect x={x(bm.dd) - 4} y={y(bm.cagr) - 4} width={8} height={8} fill="none" stroke="var(--ctx)" strokeWidth={1.5} /><text x={x(bm.dd) + 8} y={y(bm.cagr)} className="axis" dominantBaseline="middle">{benchmark!.name.toUpperCase()}</text></g>}
        {pts.map((p) => {
          const on = p.s.id === selected, onFront = front.includes(p);
          return (
            <g key={p.s.id} tabIndex={0} role="button" aria-label={`${p.s.name}: CAGR ${p.cagr.toFixed(1)}%, max drawdown ${p.dd.toFixed(1)}%`} aria-pressed={on}
              onFocus={() => setHover(p.i)} onBlur={() => setHover(null)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(p.s.id); } }}>
              <circle cx={x(p.dd)} cy={y(p.cagr)} r={r(p.sharpe)} fill={on ? "var(--amber)" : "var(--c1)"} fillOpacity={on ? 1 : 0.75} stroke="var(--panel)" strokeWidth={2} />
              {(on || onFront) && <text x={x(p.dd) + r(p.sharpe) + 4} y={y(p.cagr)} className={`axis lbl${on ? " on" : ""}`} dominantBaseline="middle"
                textAnchor={x(p.dd) > M.left + iw * 0.75 ? "end" : "start"} dx={x(p.dd) > M.left + iw * 0.75 ? -2 * r(p.sharpe) - 8 : 0}>{p.s.name.length > 18 ? p.s.name.slice(0, 17) + "…" : p.s.name}</text>}
            </g>
          );
        })}
        <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent" style={{ cursor: hp ? "pointer" : "default" }}
          onPointerMove={move} onPointerLeave={() => setHover(null)} onClick={() => hp && onSelect(hp.s.id)} />
      </svg>
      {hp && (
        <Tip x={x(hp.dd)} y={y(hp.cagr)} w={w}>
          <b>{hp.s.name}</b><span>CAGR {sPct(hp.cagr, 1)} · MAX DD −{hp.dd.toFixed(1)}%</span><span>SHARPE {hp.sharpe?.toFixed(2) ?? "—"}</span><span className="muted">CLICK TO SELECT</span>
        </Tip>
      )}
      <ChartTable caption="Risk versus return" cols={["Strategy", "CAGR %", "Max DD %", "Sharpe"]} rows={pts.map((p) => [p.s.name, p.cagr.toFixed(1), p.dd.toFixed(1), p.sharpe?.toFixed(2) ?? "—"])} />
    </div>
  );
}

// ------------------------------------------------------------------ small multiples
export function StrategySmallMultiples({ strategies, benchmark, selected, onSelect }: {
  strategies: BtStrategy[]; benchmark: DatePt[]; selected: string | null; onSelect: (id: string) => void;
}) {
  const [hoverT, setHoverT] = useHover<number>("bt-sm");
  const series = strategies.map((s) => ({ s, pts: indexed(s.equity) })).filter((x) => x.pts.length > 1);
  const bench = indexed(benchmark);
  if (!series.length) return <Empty text="NO EQUITY CURVES IN THE FILE" />;
  const all = [...series.flatMap((x) => x.pts), ...bench];
  const t0 = Math.min(...all.map((p) => ts(p.date))), t1 = Math.max(...all.map((p) => ts(p.date)));
  const yr = niceTicks(Math.min(...all.map((p) => p.value)), Math.max(...all.map((p) => p.value)), 4);
  return (
    <div className="smgrid">
      {series.map(({ s, pts }) => (
        <MiniPanel key={s.id} s={s} pts={pts} bench={bench} t0={t0} t1={t1} yr={yr} on={s.id === selected} onSelect={() => onSelect(s.id)} hoverT={hoverT} setHoverT={setHoverT} />
      ))}
    </div>
  );
}

function MiniPanel({ s, pts, bench, t0, t1, yr, on, onSelect, hoverT, setHoverT }: {
  s: BtStrategy; pts: DatePt[]; bench: DatePt[]; t0: number; t1: number; yr: { ticks: number[]; step: number };
  on: boolean; onSelect: () => void; hoverT: number | null; setHoverT: (v: number | null) => void;
}) {
  const [ref, w] = useWidth(160);
  const H = 120, DDH = 30, M = { top: 6, right: 8, bottom: 4, left: 30 };
  const lo = yr.ticks[0], hi = yr.ticks[yr.ticks.length - 1];
  const iw = (w ?? 200) - M.left - M.right, ih = H - M.top - M.bottom;
  const x = (t: number) => M.left + ((t - t0) / (t1 - t0 || 1)) * iw;
  const y = (v: number) => M.top + ih - ((v - lo) / (hi - lo)) * ih;
  const path = (p: DatePt[], yy: (v: number) => number) => p.map((q, i) => `${i ? "L" : "M"}${x(ts(q.date)).toFixed(1)},${yy(q.value).toFixed(1)}`).join("");
  let pk = -Infinity;
  const dd = pts.map((p) => { pk = Math.max(pk, p.value); return { date: p.date, value: p.value / pk - 1 }; });
  const ddMin = Math.min(-0.01, ...dd.map((d) => d.value));
  const yd = (v: number) => 2 + (v / ddMin) * (DDH - 4);
  const near = hoverT !== null ? pts.reduce((a, b) => (Math.abs(ts(b.date) - hoverT) < Math.abs(ts(a.date) - hoverT) ? b : a), pts[0]) : null;
  const last = pts[pts.length - 1];
  return (
    <div ref={ref} className={`mini${on ? " on" : ""}`}>
      <button className="mini-head" onClick={onSelect} aria-pressed={on}>
        <b>{s.name} · {s.underlying}</b><span className={last.value >= 100 ? "pos" : "neg"}>{sPct(last.value - 100, 1)}</span>
      </button>
      {w !== null && (
        <div className="chart">
          <svg width={w} height={H + DDH} role="img" aria-label={`${s.name} equity indexed to 100, with drawdown strip`}>
            {yr.ticks.map((t, i) => <g key={i}><line x1={M.left} x2={M.left + iw} y1={y(t)} y2={y(t)} className={t === 100 ? "baseline" : "grid"} />{i > 0 && <text x={M.left - 4} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{t.toFixed(0)}</text>}</g>)}
            {bench.length > 1 && <path d={path(bench, y)} fill="none" stroke="var(--ctx)" strokeWidth={1} />}
            <path d={path(pts, y)} fill="none" stroke={on ? "var(--amber)" : "var(--c1)"} strokeWidth={1.75} strokeLinejoin="round" />
            <g transform={`translate(0 ${H})`}>
              <path d={`${path(dd, yd)}L${x(ts(dd[dd.length - 1].date))},2L${x(ts(dd[0].date))},2Z`} fill="var(--neg-fill)" />
              <path d={path(dd, yd)} fill="none" stroke="var(--neg)" strokeWidth={1} />
              <line x1={M.left} x2={M.left + iw} y1={2} y2={2} className="baseline" />
              <text x={M.left - 4} y={12} className="axis" textAnchor="end">DD</text>
              <text x={M.left + iw} y={DDH - 2} className="axis" textAnchor="end">{sPct(ddMin * 100, 1)}</text>
            </g>
            {near && <><line x1={x(ts(near.date))} x2={x(ts(near.date))} y1={M.top} y2={H + DDH - 2} className="crosshair" /><circle cx={x(ts(near.date))} cy={y(near.value)} r={3} className="endpoint" /></>}
            <rect x={M.left} y={0} width={iw} height={H + DDH} fill="transparent" tabIndex={0} className="hit" aria-label={`${s.name}. Arrow keys move the shared crosshair.`}
              onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHoverT(t0 + ((e.clientX - r.left) / r.width) * (t1 - t0)); }}
              onPointerLeave={() => setHoverT(null)} onClick={onSelect} onBlur={() => setHoverT(null)}
              onKeyDown={(e) => { const stepT = (t1 - t0) / 50; if (e.key === "ArrowLeft") setHoverT(Math.max(t0, (hoverT ?? t1) - stepT)); else if (e.key === "ArrowRight") setHoverT(Math.min(t1, (hoverT ?? t0) + stepT)); else if (e.key === "Escape") setHoverT(null); else if (e.key === "Enter") onSelect(); else return; e.preventDefault(); }} />
          </svg>
          {near && <div className="mini-read">{md(near.date)} · <b>{near.value.toFixed(1)}</b></div>}
        </div>
      )}
    </div>
  );
}
