"use client";

import { useEffect, useRef, useState } from "react";

type Pt = { date: string; value: number };

const M = { top: 12, right: 16, bottom: 26, left: 64 };
const H = 220;

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceTicks(min: number, max: number, n = 4) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw)!;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

const money = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const shortDate = (d: string) => new Date(d + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });

function Empty({ text }: { text: string }) {
  return <div className="chart-empty">{text}</div>;
}

export function EquityChart({ points, baseline }: { points: Pt[]; baseline?: number }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return <div ref={ref}><Empty text="The equity curve appears after the second daily sync." /></div>;

  const vals = points.map((p) => p.value).concat(baseline ?? []);
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const iw = w - M.left - M.right, ih = H - M.top - M.bottom;
  const x = (i: number) => M.left + (i / (points.length - 1)) * iw;
  const y = (v: number) => M.top + ih - ((v - y0) / (y1 - y0)) * ih;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const area = `${line}L${x(points.length - 1)},${y(y0)}L${x(0)},${y(y0)}Z`;
  const labelEvery = Math.ceil(points.length / Math.max(2, Math.floor(iw / 90)));
  const h = hover !== null ? points[hover] : null;

  function move(e: React.PointerEvent<SVGRectElement>) {
    const r = (e.currentTarget as SVGRectElement).getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) / r.width) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  }

  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H} role="img" aria-label="Account equity by day">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={w - M.right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={M.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{money(t)}</text>
          </g>
        ))}
        {baseline !== undefined && (
          <line x1={M.left} x2={w - M.right} y1={y(baseline)} y2={y(baseline)} className="baseline" />
        )}
        {points.map((p, i) => i % labelEvery === 0 && (
          <text key={p.date} x={x(i)} y={H - 6} className="axis" textAnchor="middle">{shortDate(p.date)}</text>
        ))}
        <path d={area} className="area" />
        <path d={line} className="line" />
        <circle cx={x(points.length - 1)} cy={y(points[points.length - 1].value)} r={4} className="endpoint" />
        {h && hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + ih} className="crosshair" />
            <circle cx={x(hover)} cy={y(h.value)} r={5} className="endpoint" />
          </g>
        )}
        <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent"
          onPointerMove={move} onPointerLeave={() => setHover(null)} />
      </svg>
      {h && hover !== null && (
        <div className="tip" style={{ left: Math.min(x(hover) + 12, w - 160), top: 8 }}>
          <span>{shortDate(h.date)}</span><b>{money(h.value)}</b>
        </div>
      )}
    </div>
  );
}

export function PnlBars({ points }: { points: Pt[] }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  if (!points.length) return <div ref={ref}><Empty text="Daily P&L appears after the first sync." /></div>;

  const vals = points.map((p) => p.value).concat(0);
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const iw = w - M.left - M.right, ih = H - M.top - M.bottom;
  const band = iw / points.length;
  const bw = Math.max(2, Math.min(28, band - 2));
  const y = (v: number) => M.top + ih - ((v - y0) / (y1 - y0)) * ih;
  const labelEvery = Math.ceil(points.length / Math.max(2, Math.floor(iw / 90)));
  const h = hover !== null ? points[hover] : null;

  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H} role="img" aria-label="Profit and loss by day">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={w - M.right} y1={y(t)} y2={y(t)} className={t === 0 ? "baseline" : "grid"} />
            <text x={M.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{money(t)}</text>
          </g>
        ))}
        {points.map((p, i) => {
          const cx = M.left + band * i + band / 2;
          const top = y(Math.max(p.value, 0)), bottom = y(Math.min(p.value, 0));
          return (
            <g key={p.date}>
              <rect x={cx - bw / 2} y={top} width={bw} height={Math.max(1, bottom - top)} rx={Math.min(4, bw / 2)}
                className={p.value >= 0 ? "bar-pos" : "bar-neg"} opacity={hover === null || hover === i ? 1 : 0.45} />
              <rect x={cx - band / 2} y={M.top} width={band} height={ih} fill="transparent"
                onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} />
              {i % labelEvery === 0 && <text x={cx} y={H - 6} className="axis" textAnchor="middle">{shortDate(p.date)}</text>}
            </g>
          );
        })}
      </svg>
      {h && hover !== null && (
        <div className="tip" style={{ left: Math.min(M.left + band * hover + band / 2 + 12, w - 160), top: 8 }}>
          <span>{shortDate(h.date)}</span><b>{h.value >= 0 ? "+" : "−"}{money(Math.abs(h.value))}</b>
        </div>
      )}
    </div>
  );
}
