// Server-safe charts (no hooks): sparkline, score bars, exposure, heatmap,
// funnel, candle glyph. Tooltips are CSS (:hover / :focus-within), so they work
// on keyboard focus and on tap.
import { COMP_COLOR, COMP_LABEL, COMP_ORDER, WEIGHTS, type CompKey } from "@/lib/glossary";
import type { Candidate } from "@/lib/types";

const sPct = (v: number, d = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`;
const sUsd = (v: number, d = 0) => `${v > 0 ? "+" : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;

// ------------------------------------------------------------------ sparkline
export function Sparkline({ values, width = 72, height = 18, baseline, ariaLabel }: {
  values: number[]; width?: number; height?: number; baseline?: number | "first"; ariaLabel: string;
}) {
  if (values.length < 2) return <span className="muted small">—</span>;
  const b = baseline === "first" ? values[0] : baseline;
  const lo = Math.min(...values, b ?? Infinity), hi = Math.max(...values, b ?? -Infinity);
  const x = (i: number) => 1 + (i / (values.length - 1)) * (width - 4);
  const y = (v: number) => (hi === lo ? height / 2 : 2 + (1 - (v - lo) / (hi - lo)) * (height - 4));
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <svg width={width} height={height} role="img" aria-label={ariaLabel} className="spark">
      <title>{ariaLabel}</title>
      {b !== undefined && <line x1={0} x2={width} y1={y(b)} y2={y(b)} stroke="var(--grid)" strokeWidth={1} />}
      <path d={d} fill="none" stroke="var(--ctx)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={2.5} fill="var(--amber)" />
    </svg>
  );
}

// ------------------------------------------------------------------ score contribution
/** Points each component contributes, renormalized over the components present, so they sum to the score. */
export function contributions(components: Partial<Record<string, number | null>> | null) {
  const present = COMP_ORDER.filter((k) => components?.[k] !== null && components?.[k] !== undefined);
  const wsum = present.reduce((a, k) => a + WEIGHTS[k], 0);
  return COMP_ORDER.map((k) => {
    const raw = components?.[k];
    const has = raw !== null && raw !== undefined;
    return { k, raw: has ? Number(raw) : null, pts: has && wsum ? (100 * WEIGHTS[k] * Number(raw)) / wsum : 0, eff: has && wsum ? (100 * WEIGHTS[k]) / wsum : 0 };
  });
}

export function ScoreContributionBar({ components, score, width = 120, dimmed = false, picked = false }: {
  components: Partial<Record<string, number | null>> | null; score: number; weights?: Record<CompKey, number>; width?: number | string; dimmed?: boolean; picked?: boolean;
}) {
  const parts = contributions(components);
  const label = `Score ${score.toFixed(1)}: ` + parts.map((p) => `${COMP_LABEL[p.k]} ${p.raw === null ? "no data" : `${p.pts.toFixed(1)} pts`}`).join(", ");
  return (
    <span className={`scorebar${dimmed ? " dim" : ""}${picked ? " picked" : ""}`} style={{ width }} role="img" aria-label={label} tabIndex={0}>
      {parts.map((p) => p.pts > 0 && (
        <i key={p.k} style={{ width: `${p.pts}%`, background: COMP_COLOR[p.k] }} />
      ))}
      <span className="vt-pop" role="tooltip">
        <b>SCORE {score.toFixed(1)}</b>
        {parts.map((p) => (
          <span key={p.k} className="row"><i style={{ background: COMP_COLOR[p.k] }} />{COMP_LABEL[p.k].padEnd(5, " ")} {p.raw === null ? "NO DATA" : `${p.pts.toFixed(1).padStart(4, " ")} = ${p.raw.toFixed(2)} × ${p.eff.toFixed(0)}`}</span>
        ))}
      </span>
    </span>
  );
}

export function ScoreLegend() {
  return (
    <span className="legend">
      {COMP_ORDER.map((k) => <span key={k}><i style={{ background: COMP_COLOR[k] }} />{COMP_LABEL[k]} {WEIGHTS[k]}</span>)}
    </span>
  );
}

/** Board mode: every scanned name as a stacked bar on a 0–100 scale, with the CUT line under the picks. */
export function ScoreBoard({ candidates }: { candidates: Candidate[] }) {
  const eligible = candidates.filter((c) => !c.exclude_reason).sort((a, b) => Number(b.selected) - Number(a.selected) || b.short_score - a.short_score);
  const excluded = candidates.filter((c) => c.exclude_reason).sort((a, b) => b.short_score - a.short_score);
  const nPicked = eligible.filter((c) => c.selected).length;
  const row = (c: Candidate) => (
    <div key={c.ticker} className={`sb-row${c.selected ? " picked" : ""}${c.exclude_reason ? " excluded" : ""}`} data-ticker={c.ticker}>
      <span className="sb-t">{c.ticker}</span>
      <span className="sb-bar">
        <ScoreContributionBar components={c.components} score={c.short_score} width="100%" dimmed={!!c.exclude_reason} picked={c.selected} />
      </span>
      <span className="sb-v">{c.short_score.toFixed(1)}</span>
    </div>
  );
  return (
    <div className="scoreboard">
      <div className="sb-axis"><span /><span className="sb-ticks"><i>0</i><i>25</i><i>50</i><i>75</i><i>100</i></span><span /></div>
      {eligible.slice(0, nPicked).map(row)}
      {nPicked > 0 && <div className="sb-cut"><span>CUT · TOP {nPicked} TRADED</span></div>}
      {eligible.slice(nPicked).map(row)}
      {excluded.length > 0 && <div className="sb-group">EXCLUDED · {excluded.length}</div>}
      {excluded.map(row)}
    </div>
  );
}

// ------------------------------------------------------------------ exposure
export function ExposureBars({ equity, rows, ariaLabel = "Exposure by position" }: {
  equity: number; ariaLabel?: string;
  rows: { ticker: string; marketValue: number; targetPct: number | null; unrealizedPl: number; unrealizedPlpc: number }[];
}) {
  if (!rows.length || !equity) return <div className="empty line">NO EXPOSURE · FLAT</div>;
  const pcts = rows.map((r) => (100 * Math.abs(r.marketValue)) / equity);
  const gross = pcts.reduce((a, b) => a + b, 0);
  const maxPct = Math.max(25, ...pcts, ...rows.map((r) => r.targetPct ?? 0));
  const maxPl = Math.max(1, ...rows.map((r) => Math.abs(r.unrealizedPl)));
  return (
    <div className="exposure" role="table" aria-label={ariaLabel}>
      <div className="ex-head" role="row">
        <span role="columnheader">TICKER</span><span role="columnheader">% OF EQUITY · GROSS {gross.toFixed(0)}%</span>
        <span role="columnheader">UNREALIZED</span>
      </div>
      {rows.map((r, i) => (
        <div key={r.ticker} className="ex-row" role="row" tabIndex={0} data-ticker={r.ticker}>
          <span role="cell" className="amber">{r.ticker}</span>
          <span role="cell" className="ex-cell">
            <span className="ex-track">
              <i className="ex-pct" style={{ width: `${(pcts[i] / maxPct) * 100}%` }} />
              {r.targetPct !== null && <b className="ex-target" style={{ left: `${(r.targetPct / maxPct) * 100}%` }} />}
            </span>
            <em>{pcts[i].toFixed(1)}%</em>
          </span>
          <span role="cell" className="ex-cell">
            <span className="ex-track div">
              <i className={r.unrealizedPl >= 0 ? "ex-pos" : "ex-neg"}
                style={r.unrealizedPl >= 0 ? { left: "50%", width: `${(Math.abs(r.unrealizedPl) / maxPl) * 50}%` } : { right: "50%", width: `${(Math.abs(r.unrealizedPl) / maxPl) * 50}%` }} />
            </span>
            <em className={r.unrealizedPl > 0 ? "pos" : r.unrealizedPl < 0 ? "neg" : "zero"}>{sUsd(r.unrealizedPl)}</em>
          </span>
          <span className="vt-pop" role="tooltip">
            <b>{r.ticker}</b>
            <span>{pcts[i].toFixed(1)}% OF EQUITY{r.targetPct !== null ? ` · TARGET ${r.targetPct.toFixed(0)}%` : ""}</span>
            <span>${Math.abs(r.marketValue).toLocaleString("en-US", { maximumFractionDigits: 0 })} NOTIONAL</span>
            <span>{sUsd(r.unrealizedPl, 2)} ({sPct(r.unrealizedPlpc, 1)})</span>
          </span>
        </div>
      ))}
      <div className="ex-foot"><span /><span><b className="ex-target static" /> TARGET SIZE</span><span /></div>
    </div>
  );
}

// ------------------------------------------------------------------ monthly heatmap
const GREENS = ["#1b5e39", "#26884f", "#31b36a", "#3ddc84"], REDS = ["#7f2222", "#ab2d2d", "#d83c3c", "#ff4d4d"];
function heat(retPct: number) {
  const a = Math.abs(retPct);
  if (a < 0.25) return { bg: "#1a2026", fg: "var(--text)" };
  const i = a < 0.5 ? 0 : a < 1.5 ? 1 : a < 3 ? 2 : 3;
  const strong = a >= 5;
  return { bg: (retPct > 0 ? GREENS : REDS)[i], fg: i === 3 ? "#000" : "#fff", strong };
}
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function MonthlyReturnsHeatmap({ monthly, ariaLabel = "Monthly returns" }: { monthly: { month: string; ret: number }[]; ariaLabel?: string }) {
  if (!monthly.length) return <div className="empty line">MONTHLY RETURNS APPEAR AFTER THE FIRST MONTH-END</div>;
  const years = [...new Set(monthly.map((m) => m.month.slice(0, 4)))].sort();
  const get = (y: string, i: number) => monthly.find((m) => m.month === `${y}-${String(i + 1).padStart(2, "0")}`);
  return (
    <div className="scroll">
      <table className="heatmap" aria-label={ariaLabel}>
        <thead><tr><th>YEAR</th>{MONTHS.map((m) => <th key={m} className="num">{m}</th>)}<th className="num">YR</th></tr></thead>
        <tbody>
          {years.map((y) => {
            const ms = monthly.filter((m) => m.month.startsWith(y));
            const yr = (ms.reduce((a, m) => a * (1 + m.ret), 1) - 1) * 100;
            return (
              <tr key={y}>
                <th scope="row">{y}</th>
                {MONTHS.map((mn, i) => {
                  const c = get(y, i);
                  if (!c) return <td key={mn} className="hm-empty" />;
                  const v = c.ret * 100, h = heat(v);
                  return <td key={mn} className={`num hm${h.strong ? " strong" : ""}`} style={{ background: h.bg, color: h.fg }} title={`${mn} ${y}: ${sPct(v, 2)}`} tabIndex={0}>{sPct(v, 1)}</td>;
                })}
                {(() => { const h = heat(yr); return <td className="num hm yr" style={{ background: h.bg, color: h.fg }} title={`${y}: ${sPct(yr, 2)}`}>{sPct(yr, 1)}</td>; })()}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="hm-legend">
        {[...REDS].reverse().map((c, i) => <i key={`r${i}`} style={{ background: c }} />)}<i style={{ background: "#1a2026" }} />{GREENS.map((c, i) => <i key={`g${i}`} style={{ background: c }} />)}
        <span>−3% · −1.5 · −0.5 · −0.25 · 0 · +0.25 · +0.5 · +1.5 · +3%</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ funnel
const AMBERS = ["#5f4214", "#99621a", "#d0821f", "#ffa028"];
export function ScanFunnel({ stages }: { stages: { label: string; n: number; reason: string; dropped: string[] }[] }) {
  const max = Math.max(1, stages[0]?.n ?? 1);
  return (
    <div className="funnel" role="list" aria-label="Scan funnel">
      {stages.map((s, i) => (
        <div key={s.label} className="fn-row" role="listitem" tabIndex={0}>
          <span className="fn-l">{s.label}</span>
          <span className="fn-track"><i style={{ width: `${(s.n / max) * 100}%`, background: AMBERS[Math.min(AMBERS.length - 1, i)] }} /></span>
          <span className="fn-n">{s.n}</span>
          <span className="fn-d">{i > 0 && stages[i - 1].n - s.n > 0 ? `−${stages[i - 1].n - s.n}` : ""}</span>
          <span className="vt-pop" role="tooltip">
            <b>{s.label} · {s.n}</b>
            <span>{s.reason}</span>
            {s.dropped.length > 0 && <span className="tip-wrap">{s.dropped.join(" ")}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ candle glyph
export function Candle({ prev, close, high, low }: { prev?: number | null; close: number; high?: number | null; low?: number | null }) {
  if (!prev || !high || !low) return null;
  const lo = Math.min(low, prev), hi = Math.max(high, close);
  const y = (v: number) => 1 + (1 - (v - lo) / (hi - lo || 1)) * 22;
  const up = close >= prev;
  return (
    <svg width={8} height={24} className="candle" aria-hidden="true">
      <line x1={4} x2={4} y1={y(high)} y2={y(low)} stroke={up ? "var(--pos)" : "var(--neg)"} strokeWidth={1} />
      <rect x={1} width={6} y={Math.min(y(prev), y(close))} height={Math.max(1, Math.abs(y(prev) - y(close)))} fill={up ? "var(--pos)" : "var(--neg)"} />
    </svg>
  );
}
