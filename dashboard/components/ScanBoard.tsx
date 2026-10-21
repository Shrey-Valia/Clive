import type { Candidate } from "@/lib/types";
import { CATALYST } from "@/lib/glossary";
import { compact, num, pct, usd } from "@/lib/format";
import { Abbr, FlagTags, ScoreCell } from "@/components/Bits";
import { Candle, ScoreContributionBar } from "@/components/Viz";

const excl = (r: string) => (r.includes("shortable") ? "NO BORROW" : r.includes("catalyst") ? "REAL NEWS" : r.toUpperCase());

function Decision({ c, live }: { c: Candidate; live: boolean }) {
  if (c.selected) {
    const q = Math.abs(c.target_qty ?? 0);
    return live
      ? <span className="pill warn">SHORT {q} · {num(c.target_pct, 0)}%</span>
      : <span className="pill would">WOULD SHORT {q} · {num(c.target_pct, 0)}%</span>;
  }
  if (c.exclude_reason) return <span className={`small ${c.exclude_reason.includes("shortable") ? "noborrow" : "neg"}`} title={c.exclude_reason}>{excl(c.exclude_reason)}</span>;
  return <span className="small muted">BELOW CUT</span>;
}

function Borrow({ c }: { c: Candidate }) {
  return c.shortable === false ? <span className="noborrow">NO</span> : c.easy_to_borrow === false ? <span className="neg">HTB</span> : <span className="pos">ETB</span>;
}
function News({ c }: { c: Candidate }) {
  const k = CATALYST[c.catalyst ?? ""];
  return k ? <span className={k.tone === "bad" ? "neg" : k.tone === "good" ? "val" : "muted"}>{k.label}</span> : <span className="muted">—</span>;
}

export function ScanBoard({ candidates, live }: { candidates: Candidate[]; live: boolean }) {
  const picked = candidates.filter((c) => c.selected).sort((a, b) => a.rank - b.rank);
  const eligible = candidates.filter((c) => !c.selected && !c.exclude_reason).sort((a, b) => b.short_score - a.short_score);
  const excluded = candidates.filter((c) => !c.selected && c.exclude_reason).sort((a, b) => b.short_score - a.short_score);
  const groups: [string, Candidate[]][] = [
    [live ? "PICKED · SHORTED" : "PICKED · WOULD SHORT (DRY RUN)", picked],
    ["ELIGIBLE · BELOW THE TOP-5 CUT", eligible],
    ["EXCLUDED", excluded],
  ];
  const COLS = 18;

  return (
    <>
      <div className="scroll desk-only">
        <table className="dense scan">
          <thead><tr>
            <th className="num">#</th><th>Ticker</th><th className="num"><Abbr term="MCAP">MCAP</Abbr></th><th className="num">Last</th><th className="num">Chg</th>
            <th className="num"><Abbr term="RVOL">RVOL</Abbr></th><th className="num"><Abbr term="WICK">Wick</Abbr></th>
            <th className="num"><Abbr term="SI%">SI%</Abbr></th><th className="num"><Abbr term="DTC">DTC</Abbr></th>
            <th className="num"><Abbr term="SHVOL">ShVol</Abbr></th><th className="num"><Abbr term="FTD">FTD</Abbr></th>
            <th><Abbr term="BRW">Brw</Abbr></th><th><Abbr term="NEWS">News</Abbr></th>
            <th className="num"><Abbr term="SCORE">Score</Abbr></th><th>Breakdown</th><th>Decision</th><th>Flags</th><th>Headlines</th>
          </tr></thead>
          <tbody>
            {groups.map(([label, rows]) => rows.length > 0 && [
              <tr key={label} className="group"><td colSpan={COLS}>{label} · {rows.length}</td></tr>,
              ...rows.map((c) => (
                <tr key={c.ticker} data-ticker={c.ticker} className={c.selected ? "picked" : c.exclude_reason ? "excluded" : ""}>
                  <td className="num muted">{c.rank}</td>
                  <td className="tk"><Candle prev={c.prev_close} close={c.close} high={c.high} low={c.low} /><span className={c.selected ? "amber b" : "b"}>{c.ticker}</span></td>
                  <td className="num">{compact(c.market_cap)}</td>
                  <td className="num">{usd(c.close)}</td>
                  <td className="num pos">{pct(c.change, 1, true)}</td>
                  <td className="num">{num(c.rvol)}x</td>
                  <td className="num">{c.upper_wick === null ? "—" : pct(Number(c.upper_wick) * 100, 0)}</td>
                  <td className="num">{pct(c.si_pct_float)}</td>
                  <td className="num">{num(c.days_to_cover)}</td>
                  <td className="num">{pct(c.short_vol_ratio_latest, 0)}</td>
                  <td className="num">{c.ftd_days ?? "—"}</td>
                  <td><Borrow c={c} /></td>
                  <td><News c={c} /></td>
                  <ScoreCell score={c.short_score} />
                  <td><ScoreContributionBar components={c.components} score={c.short_score} dimmed={!!c.exclude_reason} picked={c.selected} /></td>
                  <td><Decision c={c} live={live} /></td>
                  <td><FlagTags flags={c.flags} /></td>
                  <td className="wrapcell small" title={(c.headlines ?? []).join("\n")}><div>{(c.headlines ?? []).slice(0, 2).join(" · ") || "—"}</div></td>
                </tr>
              )),
            ])}
          </tbody>
        </table>
      </div>

      <div className="mob-only cards">
        {groups.slice(0, 2).map(([label, rows]) => rows.length > 0 && (
          <div key={label} className="cardgroup">
            <div className="cg-head">{label} · {rows.length}</div>
            {rows.map((c) => <PickCard key={c.ticker} c={c} live={live} />)}
          </div>
        ))}
        {excluded.length > 0 && (
          <details className="cardgroup">
            <summary className="cg-head">EXCLUDED · {excluded.length} <span className="muted">TAP TO SHOW</span></summary>
            {excluded.map((c) => <PickCard key={c.ticker} c={c} live={live} />)}
          </details>
        )}
      </div>
    </>
  );
}

function PickCard({ c, live }: { c: Candidate; live: boolean }) {
  return (
    <div className={`card${c.selected ? " picked" : ""}${c.exclude_reason ? " excluded" : ""}`} data-ticker={c.ticker}>
      <div className="card-top">
        <span className="b amber">{c.ticker}</span>
        <span className="pos">{pct(c.change, 1, true)}</span>
        <span className="muted">{usd(c.close)}</span>
        <span className="card-score" style={{ background: `rgba(255,160,40,${(Math.max(0, Math.min(1, (c.short_score - 30) / 70)) * 0.35).toFixed(3)})` }}>{c.short_score.toFixed(1)}</span>
      </div>
      <ScoreContributionBar components={c.components} score={c.short_score} width="100%" dimmed={!!c.exclude_reason} picked={c.selected} />
      <div className="card-kv">
        <span>RVOL <b>{num(c.rvol)}x</b></span><span>SI <b>{pct(c.si_pct_float)}</b></span><span>DTC <b>{num(c.days_to_cover)}</b></span>
        <span>BRW <b><Borrow c={c} /></b></span><span>NEWS <b><News c={c} /></b></span>
      </div>
      <div className="card-foot"><Decision c={c} live={live} /> <FlagTags flags={c.flags} /></div>
    </div>
  );
}
