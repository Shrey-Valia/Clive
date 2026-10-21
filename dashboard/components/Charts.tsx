"use client";

import { useId } from "react";
import { ChartTable, Empty, Tip, chartKeys, fmtTick, md, mdy, niceTicks, sPct, sUsd, useHover, useWidth, usd0, type ChartBase, type DatePt } from "@/components/chart-kit";

const M = { top: 14, right: 70, bottom: 22, left: 58 };

function Frame({ height, children }: { height: number; children?: React.ReactNode }) {
  return <div className="chart" style={{ minHeight: height }}>{children}</div>;
}

/** X date labels: evenly spaced, always the last date, never colliding. */
function dateLabels(n: number, iw: number, x: (i: number) => number, dates: string[], H: number) {
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
  const idx: number[] = [];
  for (let i = 0; i < n; i += every) idx.push(i);
  if (idx[idx.length - 1] !== n - 1) { if (n - 1 - idx[idx.length - 1] < every * 0.95) idx.pop(); idx.push(n - 1); }
  return idx.map((i) => <text key={i} x={x(i)} y={H - 6} className="axis" textAnchor={i === n - 1 ? "end" : i === 0 ? "start" : "middle"}>{md(dates[i])}</text>);
}

// ------------------------------------------------------------------ equity
export function EquityChart({ points, baseline, baselineLabel = "START", height = 220, ariaLabel, group, dayPl }: ChartBase & {
  points: DatePt[]; baseline?: number; baselineLabel?: string; group?: string; dayPl?: (number | null)[];
}) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useHover<number>(group);
  const clip = useId().replace(/:/g, "");
  if (points.length < 2) return <div ref={ref}><Empty text="The equity curve appears after the second daily sync." height={height} /></div>;
  if (w === null) return <div ref={ref}><Frame height={height} /></div>;

  const H = height;
  const base = baseline ?? points[0].value;
  const vals = points.map((p) => p.value).concat(base);
  const { ticks, step } = niceTicks(Math.min(...vals), Math.max(...vals));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const iw = w - M.left - M.right, ih = H - M.top - M.bottom;
  const x = (i: number) => M.left + (i / (points.length - 1)) * iw;
  const y = (v: number) => M.top + ih - ((v - y0) / (y1 - y0)) * ih;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const by = y(base);
  const area = `${line}L${x(points.length - 1)},${by}L${x(0)},${by}Z`;
  const last = points[points.length - 1];
  const hi = hover !== null && hover < points.length ? hover : null;
  const hp = hi !== null ? points[hi] : null;

  function move(e: React.PointerEvent<SVGRectElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(((e.clientX - r.left) / r.width) * (points.length - 1)))));
  }

  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H} role="img" aria-label={ariaLabel}>
        <defs>
          <clipPath id={`${clip}a`}><rect x={M.left} y={M.top} width={iw} height={Math.max(0, by - M.top)} /></clipPath>
          <clipPath id={`${clip}b`}><rect x={M.left} y={by} width={iw} height={Math.max(0, M.top + ih - by)} /></clipPath>
        </defs>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={M.left} x2={M.left + iw} y1={y(t)} y2={y(t)} className="grid" />
            <text x={M.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{fmtTick(t, step)}</text>
          </g>
        ))}
        {dateLabels(points.length, iw, x, points.map((p) => p.date), H)}
        <path d={area} fill="var(--pos-fill)" clipPath={`url(#${clip}a)`} />
        <path d={area} fill="var(--neg-fill)" clipPath={`url(#${clip}b)`} />
        <line x1={M.left} x2={M.left + iw} y1={by} y2={by} className="baseline" />
        <text x={M.left + 4} y={by - 4} className="axis lbl">{baselineLabel} {usd0(base)}</text>
        <path d={line} className="line" />
        <circle cx={x(points.length - 1)} cy={y(last.value)} r={4} className="endpoint" />
        <text x={x(points.length - 1) + 8} y={y(last.value)} className="endlabel" dominantBaseline="middle">{fmtTick(last.value, step < 100 ? 1 : step)}</text>
        {hp && hi !== null && (
          <g>
            <line x1={x(hi)} x2={x(hi)} y1={M.top} y2={M.top + ih} className="crosshair" />
            <circle cx={x(hi)} cy={y(hp.value)} r={4} className="endpoint" />
          </g>
        )}
        <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent" tabIndex={0} className="hit"
          aria-label={`${ariaLabel}. Use arrow keys to move between days.`}
          onPointerMove={move} onPointerLeave={() => setHover(null)} onKeyDown={chartKeys(points.length, hi, setHover)} onBlur={() => setHover(null)} />
      </svg>
      {hp && hi !== null && (
        <Tip x={x(hi)} y={y(hp.value)} w={w}>
          <b>{usd0(hp.value)}</b>
          <span>{mdy(hp.date)}</span>
          {dayPl && dayPl[hi] !== undefined && <span>DAY {dayPl[hi] === null ? "NO SYNC" : sUsd(dayPl[hi] as number)}</span>}
          <span>VS START {sPct((100 * (hp.value - base)) / base, 2)}</span>
        </Tip>
      )}
      <ChartTable caption={ariaLabel} cols={["Date", "Equity"]} rows={points.map((p) => [p.date, p.value.toFixed(2)])} />
    </div>
  );
}

// ------------------------------------------------------------------ daily P&L
export function PnlBars({ points, equity, height = 220, ariaLabel, group, firstIsDeposit = true }: ChartBase & {
  points: { date: string; value: number | null }[]; equity?: number[]; group?: string; firstIsDeposit?: boolean;
}) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useHover<number>(group);
  if (!points.length) return <div ref={ref}><Empty text="Daily P&L appears after the first sync." height={height} /></div>;
  if (w === null) return <div ref={ref}><Frame height={height} /></div>;

  const H = height;
  const usable = points.map((p, i) => (firstIsDeposit && i === 0 ? null : p.value));
  const vals = usable.filter((v): v is number => v !== null).concat(0);
  const { ticks, step } = niceTicks(Math.min(...vals), Math.max(...vals));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const iw = w - M.left - 16, ih = H - M.top - M.bottom;
  const band = iw / points.length;
  const gap = band >= 6 ? 2 : 0;
  const bw = Math.max(1, Math.min(24, band - gap));
  const y = (v: number) => M.top + ih - ((v - y0) / (y1 - y0)) * ih;
  const cx = (i: number) => M.left + band * i + band / 2;
  const zero = y(0);
  let best = -1, worst = -1;
  usable.forEach((v, i) => { if (v === null) return; if (best < 0 || v > (usable[best] as number)) best = i; if (worst < 0 || v < (usable[worst] as number)) worst = i; });
  const running: number[] = [];
  usable.reduce<number>((a, v, i) => (running[i] = a + (v ?? 0)), 0);
  const hi = hover !== null && hover < points.length ? hover : null;

  /** Bar path rounded only at the data end. */
  const bar = (i: number, v: number) => {
    const x0 = cx(i) - bw / 2, x1 = x0 + bw, r = Math.min(3, bw / 2, Math.abs(y(v) - zero));
    if (v >= 0) { const t = y(v); return `M${x0},${zero}V${t + r}Q${x0},${t} ${x0 + r},${t}H${x1 - r}Q${x1},${t} ${x1},${t + r}V${zero}Z`; }
    const b = y(v); return `M${x0},${zero}V${b - r}Q${x0},${b} ${x0 + r},${b}H${x1 - r}Q${x1},${b} ${x1},${b - r}V${zero}Z`;
  };
  const xi = (i: number) => cx(i);

  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H} role="img" aria-label={ariaLabel}>
        {ticks.map((t, i) => (
          <g key={i}>
            {t !== 0 && <line x1={M.left} x2={M.left + iw} y1={y(t)} y2={y(t)} className="grid" />}
            <text x={M.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{fmtTick(t, step)}</text>
          </g>
        ))}
        {dateLabels(points.length, iw, xi, points.map((p) => p.date), H)}
        {usable.map((v, i) => v === null || v === 0 ? (
          <rect key={i} x={cx(i) - bw / 2} y={zero - 1} width={bw} height={2} className="bar-null" />
        ) : (
          <path key={i} d={bar(i, v)} className={v > 0 ? "bar-pos" : "bar-neg"} opacity={hi === null || hi === i ? 1 : 0.45} />
        ))}
        <line x1={M.left} x2={M.left + iw} y1={zero} y2={zero} className="baseline" />
        {[best, worst].filter((i, k, a) => i >= 0 && a.indexOf(i) === k).map((i) => {
          const v = usable[i] as number;
          return <text key={`l${i}`} x={Math.min(Math.max(cx(i), M.left + 30), M.left + iw - 30)} y={v >= 0 ? y(v) - 5 : y(v) + 12} className="axis lbl" textAnchor="middle">{v >= 0 ? "BEST " : "WORST "}{sUsd(v, 0)}</text>;
        })}
        {hi !== null && <rect x={M.left + band * hi} y={M.top} width={band} height={ih} className="hoverband" />}
        <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent" tabIndex={0} className="hit" aria-label={`${ariaLabel}. Use arrow keys.`}
          onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(points.length - 1, Math.floor(((e.clientX - r.left) / r.width) * points.length)))); }}
          onPointerLeave={() => setHover(null)} onKeyDown={chartKeys(points.length, hi, setHover)} onBlur={() => setHover(null)} />
      </svg>
      {hi !== null && (
        <Tip x={cx(hi)} y={usable[hi] === null ? zero : y(Math.max(0, usable[hi] as number))} w={w}>
          {usable[hi] === null ? <b>{firstIsDeposit && hi === 0 ? "FIRST DAY" : "NO SYNC"}</b> : <b className={(usable[hi] as number) > 0 ? "pos" : (usable[hi] as number) < 0 ? "neg" : ""}>{sUsd(usable[hi] as number)}</b>}
          <span>{mdy(points[hi].date)}</span>
          {firstIsDeposit && hi === 0 && <span>MAY INCLUDE THE DEPOSIT · NOT PLOTTED</span>}
          {usable[hi] !== null && <span>RUNNING {sUsd(running[hi])}</span>}
          {usable[hi] !== null && equity && equity[hi - 1] ? <span>{sPct((100 * (usable[hi] as number)) / equity[hi - 1], 2)} OF PRIOR EQUITY</span> : null}
        </Tip>
      )}
      <ChartTable caption={ariaLabel} cols={["Date", "P&L"]} rows={points.map((p) => [p.date, p.value === null ? "no sync" : p.value.toFixed(2)])} />
    </div>
  );
}

// ------------------------------------------------------------------ drawdown
export function DrawdownChart({ equity, compact = false, height, ariaLabel, group }: ChartBase & { equity: DatePt[]; compact?: boolean; group?: string }) {
  const H = height ?? (compact ? 56 : 160);
  const [ref, w] = useWidth();
  const [hover, setHover] = useHover<number>(group);
  if (equity.length < 2) return <div ref={ref}><Empty text="Drawdown appears after the second daily sync." height={compact ? 40 : H} /></div>;
  if (w === null) return <div ref={ref}><Frame height={H} /></div>;

  let pk = -Infinity;
  const dd = equity.map((p) => { pk = Math.max(pk, p.value); return pk > 0 ? (100 * p.value) / pk - 100 : 0; });
  let mi = 0; dd.forEach((v, i) => { if (v < dd[mi]) mi = i; });
  const m = compact ? { top: 4, right: M.right, bottom: 4, left: M.left } : M;
  const lo = Math.min(-0.5, dd[mi]);
  const { ticks, step } = niceTicks(lo, 0, compact ? 1 : 3);
  const y0 = ticks[0];
  const iw = w - m.left - m.right, ih = H - m.top - m.bottom;
  const x = (i: number) => m.left + (i / (dd.length - 1)) * iw;
  const y = (v: number) => m.top + (v / y0) * ih;
  const line = dd.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const area = `${line}L${x(dd.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
  const hi = hover !== null && hover < dd.length ? hover : null;

  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H} role="img" aria-label={ariaLabel}>
        {!compact && ticks.map((t, i) => (
          <g key={i}>
            {t !== 0 && <line x1={m.left} x2={m.left + iw} y1={y(t)} y2={y(t)} className="grid" />}
            <text x={m.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{fmtTick(t, step, "pct")}</text>
          </g>
        ))}
        {compact && <text x={m.left - 8} y={m.top + 8} className="axis" textAnchor="end">DD</text>}
        {!compact && dateLabels(dd.length, iw, x, equity.map((p) => p.date), H)}
        <path d={area} fill="var(--neg-fill)" />
        <path d={line} fill="none" stroke="var(--neg)" strokeWidth={compact ? 1.5 : 2} strokeLinejoin="round" />
        <line x1={m.left} x2={m.left + iw} y1={y(0)} y2={y(0)} className="baseline" />
        {dd[mi] < 0 && (
          <g>
            <circle cx={x(mi)} cy={y(dd[mi])} r={compact ? 2.5 : 3.5} fill="var(--neg)" stroke="var(--panel)" strokeWidth={2} />
            <text x={x(mi) + (x(mi) > m.left + iw * 0.7 ? -6 : 6)} y={compact ? Math.min(H - 4, y(dd[mi])) : y(dd[mi]) + 4} className="axis lbl"
              textAnchor={x(mi) > m.left + iw * 0.7 ? "end" : "start"} dominantBaseline={compact ? "auto" : "hanging"}>
              MAX {sPct(dd[mi], 1)} · {md(equity[mi].date)}
            </text>
          </g>
        )}
        <text x={m.left + iw + 8} y={y(dd[dd.length - 1])} className="endlabel" dominantBaseline="middle">{sPct(dd[dd.length - 1], 1)}</text>
        {hi !== null && <line x1={x(hi)} x2={x(hi)} y1={m.top} y2={m.top + ih} className="crosshair" />}
        <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" tabIndex={0} className="hit" aria-label={`${ariaLabel}. Use arrow keys.`}
          onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(dd.length - 1, Math.round(((e.clientX - r.left) / r.width) * (dd.length - 1))))); }}
          onPointerLeave={() => setHover(null)} onKeyDown={chartKeys(dd.length, hi, setHover)} onBlur={() => setHover(null)} />
      </svg>
      {hi !== null && (
        <Tip x={x(hi)} y={compact ? 0 : y(dd[hi])} w={w}>
          <b className={dd[hi] < 0 ? "neg" : ""}>{sPct(dd[hi], 2)}</b><span>{mdy(equity[hi].date)}</span><span>FROM PEAK</span>
        </Tip>
      )}
      <ChartTable caption={ariaLabel} cols={["Date", "Drawdown %"]} rows={equity.map((p, i) => [p.date, dd[i].toFixed(2)])} />
    </div>
  );
}

// ------------------------------------------------------------------ trade histogram
export function TradeReturnHistogram({ trades, binPct = 1, clampPct = 15, height = 180, ariaLabel }: ChartBase & {
  trades: { id: string; ticker: string; pnl_pct: number }[]; binPct?: number; clampPct?: number;
}) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useHover<number>();
  if (!trades.length) return <div ref={ref}><Empty text="The return distribution appears after the first closed trade." height={height} /></div>;
  if (w === null) return <div ref={ref}><Frame height={height} /></div>;
  const H = height;
  const m = { top: 30, right: 16, bottom: 22, left: 36 };
  const iw = w - m.left - m.right, ih = H - m.top - m.bottom;
  const vals = trades.map((t) => t.pnl_pct);
  const sorted = [...vals].sort((a, b) => a - b);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  const med = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  const lo = -clampPct, hiE = clampPct;
  const xs = (v: number) => m.left + ((Math.max(lo - binPct, Math.min(hiE + binPct, v)) - (lo - binPct)) / (hiE - lo + 2 * binPct)) * iw;

  // strip plot for tiny samples
  if (trades.length < 5) {
    return (
      <div ref={ref} className="chart">
        <svg width={w} height={70} role="img" aria-label={ariaLabel}>
          <line x1={xs(0)} x2={xs(0)} y1={10} y2={48} className="baseline" />
          {trades.map((t, i) => <circle key={t.id} cx={xs(t.pnl_pct)} cy={30} r={5} className={t.pnl_pct >= 0 ? "dot-pos" : "dot-neg"} tabIndex={0}
            onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />)}
          {[-10, 0, 10].map((v) => <text key={v} x={xs(v)} y={64} className="axis" textAnchor="middle">{sPct(v, 0)}</text>)}
        </svg>
        {hover !== null && <Tip x={xs(trades[hover].pnl_pct)} y={30} w={w}><b>{trades[hover].ticker}</b><span>{sPct(trades[hover].pnl_pct, 2)}</span></Tip>}
        <ChartTable caption={ariaLabel} cols={["Ticker", "Return %"]} rows={trades.map((t) => [t.ticker, t.pnl_pct.toFixed(2)])} />
      </div>
    );
  }

  // bins: [lo-b, lo) clamp, [lo, lo+b) ... [hi-b, hi), [hi, hi+b) clamp. An edge sits at 0.
  const nb = Math.round((hiE - lo) / binPct) + 2;
  const bins = Array.from({ length: nb }, (_, i) => ({ from: lo - binPct + i * binPct, to: lo + i * binPct, items: [] as typeof trades }));
  for (const t of trades) {
    const i = t.pnl_pct < lo ? 0 : t.pnl_pct >= hiE ? nb - 1 : Math.floor((t.pnl_pct - lo) / binPct) + 1;
    bins[Math.min(nb - 1, i)].items.push(t);
  }
  const maxN = Math.max(...bins.map((b) => b.items.length));
  const bandW = iw / nb;
  const y = (c: number) => m.top + ih - (c / maxN) * ih;
  const hb = hover !== null ? bins[hover] : null;
  const rangeLabel = (i: number) => i === 0 ? `< ${sPct(lo, 0)}` : i === nb - 1 ? `≥ ${sPct(hiE, 0)}` : `${sPct(bins[i].from, 0)} to ${sPct(bins[i].to, 0)}`;

  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H} role="img" aria-label={ariaLabel}>
        {[0, Math.ceil(maxN / 2), maxN].filter((v, i, a) => a.indexOf(v) === i).map((c) => (
          <g key={c}>
            {c > 0 && <line x1={m.left} x2={m.left + iw} y1={y(c)} y2={y(c)} className="grid" />}
            <text x={m.left - 6} y={y(c)} className="axis" textAnchor="end" dominantBaseline="middle">{c}</text>
          </g>
        ))}
        {bins.map((b, i) => b.items.length > 0 && (
          <rect key={i} x={m.left + i * bandW + 1} y={y(b.items.length)} width={Math.max(1, bandW - 2)} height={m.top + ih - y(b.items.length)}
            className={b.to <= 0 ? "bar-neg" : "bar-pos"} opacity={hover === null || hover === i ? 1 : 0.45} />
        ))}
        <line x1={m.left} x2={m.left + iw} y1={m.top + ih} y2={m.top + ih} className="baseline" />
        {[-clampPct, -10, -5, 0, 5, 10, clampPct].filter((v, i, a) => a.indexOf(v) === i).map((v) => (
          <text key={v} x={m.left + ((v - lo + binPct) / binPct) * bandW} y={H - 6} className="axis" textAnchor="middle">{v === 0 ? "0" : sPct(v, 0)}</text>
        ))}
        {[{ v: avg, l: "AVG", dy: -8 }, { v: med, l: "MED", dy: 3 }].map(({ v, l, dy }) => {
          const px = m.left + ((Math.max(lo - binPct, Math.min(hiE + binPct, v)) - lo + binPct) / binPct) * bandW;
          return (
            <g key={l}>
              <line x1={px} x2={px} y1={m.top - 2} y2={m.top + ih} stroke={l === "AVG" ? "var(--amber)" : "var(--value)"} strokeWidth={1.5} />
              <text x={px + (l === "AVG" ? 4 : -4)} y={m.top - 6 + dy} className="axis lbl" textAnchor={avg >= med ? (l === "AVG" ? "start" : "end") : (l === "AVG" ? "end" : "start")} dx={avg >= med ? 0 : (l === "AVG" ? -8 : 8)}>{l} {sPct(v, 2)}</text>
            </g>
          );
        })}
        {bins.map((b, i) => (
          <rect key={`h${i}`} x={m.left + i * bandW} y={m.top} width={bandW} height={ih} fill="transparent" tabIndex={b.items.length ? 0 : -1}
            aria-label={`${rangeLabel(i)}: ${b.items.length} trades`}
            onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
        ))}
      </svg>
      {hb && hover !== null && hb.items.length > 0 && (
        <Tip x={m.left + hover * bandW + bandW / 2} y={y(hb.items.length)} w={w}>
          <b>{hb.items.length} TRADE{hb.items.length > 1 ? "S" : ""}</b><span>{rangeLabel(hover)}</span>
          <span className="tip-wrap">{hb.items.slice(0, 12).map((t) => t.ticker).join(" ")}{hb.items.length > 12 ? " …" : ""}</span>
        </Tip>
      )}
      <ChartTable caption={ariaLabel} cols={["Range", "Trades"]} rows={bins.map((b, i) => [rangeLabel(i), b.items.length])} />
    </div>
  );
}
