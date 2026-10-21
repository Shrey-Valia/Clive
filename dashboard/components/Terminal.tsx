"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { countdown, etParts, marketState, nextRun } from "@/lib/time";

const KEYS = [
  { key: "F1", n: "1", g: "h", code: "HOME", href: "/", label: "Overview" },
  { key: "F2", n: "2", g: "s", code: "SHRT", href: "/shorting", label: "Shorting" },
  { key: "F3", n: "3", g: "r", code: "RUNS", href: "/shorting/runs", label: "Run history" },
  { key: "F4", n: "4", g: "o", code: "BT", href: "/options", label: "Options lab" },
];
const SECONDARY = [{ g: "l", code: "LUCD", href: "/lucid", label: "Lucid (inactive)" }];

type Cmd = { word: string; desc: string; href?: string; run?: string };
const COMMANDS: Cmd[] = [
  { word: "HOME", desc: "Overview of every bot", href: "/" },
  { word: "SHRT", desc: "Stock shorting bot", href: "/shorting" },
  { word: "RUNS", desc: "Run history", href: "/shorting/runs" },
  { word: "RUN", desc: "RUN <YYYY-MM-DD> · open one run", run: "RUN " },
  { word: "RISK", desc: "Risk panel: gross, gap scenarios", href: "/shorting#risk" },
  { word: "PERF", desc: "Performance panel", href: "/shorting#perf" },
  { word: "LOG", desc: "System log", href: "/shorting#log" },
  { word: "BT", desc: "Options backtest lab", href: "/options" },
  { word: "OPTN", desc: "Options backtest lab", href: "/options" },
  { word: "LUCD", desc: "Lucid bot (inactive)", href: "/lucid" },
  { word: "HELP", desc: "List commands and keys" },
];
const ALIASES: Record<string, string> = { OV: "HOME", SHORT: "SHRT", SHORTING: "SHRT", LUCID: "LUCD", OPT: "OPTN", OPTIONS: "OPTN", HISTORY: "RUNS", "?": "HELP" };

function flash(el: Element | null) {
  if (!el) return false;
  el.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  el.classList.remove("flash"); void (el as HTMLElement).offsetWidth; el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1600);
  return true;
}
function flashTicker(t: string) {
  const els = [...document.querySelectorAll(`[data-ticker="${CSS.escape(t)}"]`)].filter((e) => (e as HTMLElement).offsetParent !== null);
  if (!els.length) return false;
  flash(els[0]); els.slice(1).forEach((e) => { e.classList.add("flash"); setTimeout(() => e.classList.remove("flash"), 1600); });
  return true;
}
function flashPanel(n: string) {
  const el = document.querySelector(`[data-panel="${n}"]`) ?? document.getElementById(n);
  return flash(el);
}

function useNow() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => { setNow(new Date()); const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  return now;
}

export function TopBar({ demo, tickers = [] }: { demo: boolean; tickers?: string[] }) {
  const path = usePathname();
  const router = useRouter();
  const now = useNow();
  const [cmd, setCmd] = useState("");
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [mobileCmd, setMobileCmd] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const gPending = useRef(0);

  const suggestions = useMemo(() => {
    const w = cmd.trim().toUpperCase();
    if (!w) return [] as { word: string; desc: string }[];
    const out: { word: string; desc: string }[] = [];
    if (/^\d$/.test(w)) out.push({ word: w, desc: `Focus panel ${w} on this page` });
    for (const c of COMMANDS) if (c.word.startsWith(w.split(" ")[0])) out.push({ word: c.run && w.startsWith("RUN ") ? w : c.word, desc: c.desc });
    for (const t of tickers) if (t.startsWith(w) && !out.some((o) => o.word === t)) out.push({ word: t, desc: "Ticker · find its row" });
    return out.slice(0, 8);
  }, [cmd, tickers]);

  const exec = useCallback((raw: string) => {
    let w = raw.trim().toUpperCase().replace(/\s*<?GO>?$/, "");
    w = ALIASES[w] ?? w;
    setErr(""); setOpen(false);
    if (!w) return;
    const done = () => { setCmd(""); input.current?.blur(); setMobileCmd(false); };
    if (w === "HELP") { setHelp(true); return done(); }
    if (/^\d{1,2}$/.test(w)) { if (flashPanel(w)) return done(); setErr(`NO PANEL ${w} ON THIS PAGE`); return; }
    const run = w.match(/^RUN\s+(\S+)$/);
    if (run) { router.push(/^\d+$/.test(run[1]) ? `/shorting/runs/${run[1]}` : `/shorting/runs?d=${run[1]}`); return done(); }
    const c = COMMANDS.find((x) => x.word === w && x.href);
    if (c?.href) {
      const [p, hash] = c.href.split("#");
      if (hash && path === p) flashPanel(hash); else router.push(c.href);
      return done();
    }
    if (/^[A-Z.]{1,6}$/.test(w)) {
      if (flashTicker(w)) return done();
      if (tickers.includes(w)) { router.push(`/shorting#t=${w}`); return done(); }
    }
    setErr(`UNKNOWN: ${w} · TYPE HELP`);
  }, [path, router, tickers]);

  // keyboard: F-keys, 1–4, g-chords, / and ?
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || (e.target as HTMLElement)?.isContentEditable;
      if (e.key === "Escape") { setHelp(false); setOpen(false); return; }
      const f = KEYS.find((x) => x.key === e.key);
      if (f) { e.preventDefault(); router.push(f.href); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); setMobileCmd(true); setTimeout(() => input.current?.focus(), 0); return; }
      if (e.key === "?") { e.preventDefault(); setHelp((v) => !v); return; }
      if (Date.now() - gPending.current < 1200) {
        gPending.current = 0;
        const k = [...KEYS, ...SECONDARY].find((x) => x.g === e.key.toLowerCase());
        if (k) { e.preventDefault(); router.push(k.href); }
        return;
      }
      if (e.key === "g") { gPending.current = Date.now(); return; }
      const n = KEYS.find((x) => x.n === e.key);
      if (n) { e.preventDefault(); router.push(n.href); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  // deep links: /shorting#t=TICKER flashes the row, #risk / #p3 flashes the panel
  useEffect(() => {
    const go = () => {
      const h = decodeURIComponent(window.location.hash.slice(1));
      if (!h) return;
      setTimeout(() => { if (h.startsWith("t=")) flashTicker(h.slice(2)); else flashPanel(h.replace(/^p(\d+)$/, "$1")); }, 250);
    };
    go();
    window.addEventListener("hashchange", go);
    return () => window.removeEventListener("hashchange", go);
  }, [path]);

  function onInputKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setSel((s) => Math.min(suggestions.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Tab" && open && suggestions[sel]) { e.preventDefault(); setCmd(suggestions[sel].word + (suggestions[sel].word === "RUN" ? " " : "")); }
    else if (e.key === "Escape") { setOpen(false); input.current?.blur(); }
  }
  function submit(e: React.FormEvent) {
    e.preventDefault();
    const pick = open && suggestions[sel] && cmd.trim().toUpperCase() !== suggestions[sel].word ? suggestions[sel].word : cmd;
    if (pick === "RUN") { setCmd("RUN "); return; }
    exec(pick);
  }

  const mkt = now ? marketState(now) : null;
  const nr = now ? nextRun(now) : null;
  const nrLeft = nr && now ? nr.at.getTime() - now.getTime() : null;
  const sameDay = nr && now ? nr.date === etParts(now).date : false;
  const active = (href: string) => (href === "/" ? path === "/" : href === "/shorting" ? path === "/shorting" : path.startsWith(href));

  return (
    <header className="topbar">
      <div className="cmdrow">
        <Link href="/" className="brand keycap">CLIVE</Link>
        <form onSubmit={submit} className={`cmd${mobileCmd ? " open" : ""}`} role="search">
          <label htmlFor="cmd" className="prompt">&gt;</label>
          <input id="cmd" ref={input} value={cmd} spellCheck={false} autoComplete="off" placeholder="FUNCTION, TICKER OR PANEL #  ·  HELP"
            aria-label="Command" role="combobox" aria-expanded={open && suggestions.length > 0} aria-controls="cmd-list" aria-autocomplete="list"
            aria-activedescendant={open && suggestions[sel] ? `cmd-opt-${sel}` : undefined}
            onChange={(e) => { setCmd(e.target.value); setSel(0); setOpen(true); setErr(""); }}
            onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} onKeyDown={onInputKey} />
          <button type="submit" className="gokey keycap">&lt;GO&gt;</button>
          {open && suggestions.length > 0 && (
            <ul id="cmd-list" className="cmd-list" role="listbox">
              {suggestions.map((s, i) => (
                <li key={s.word + i} id={`cmd-opt-${i}`} role="option" aria-selected={i === sel} className={i === sel ? "on" : ""}
                  onMouseDown={(e) => { e.preventDefault(); exec(s.word); }} onMouseEnter={() => setSel(i)}>
                  <b>{s.word}</b><span>{s.desc}</span>
                </li>
              ))}
            </ul>
          )}
        </form>
        <button type="button" className="cmdtoggle keycap" aria-label="Open command bar" onClick={() => { setMobileCmd((v) => !v); setTimeout(() => input.current?.focus(), 0); }}>&gt;_</button>
        {err && <span className="cmderr" role="alert">{err}</span>}
        <div className="clock">
          {demo && <span className="pill warn">DEMO DATA</span>}
          {mkt && <span className={`pill ${mkt.cls}`}>{mkt.label}</span>}
          {nr && nrLeft !== null && (
            <span className={`pill ${nrLeft < 15 * 60_000 ? "warn" : "off"} nextrun`} title={`Next bot step: ${nr.kind} on ${nr.date}`}>
              {nr.kind} T−{countdown(nrLeft)}{sameDay ? "" : ` · ${nr.date.slice(5).replace("-", "/")}`}
            </span>
          )}
          <span className="mono et">{now ? now.toLocaleTimeString("en-US", { timeZone: "America/New_York", hourCycle: "h23" }) : "--:--:--"} ET</span>
        </div>
      </div>
      <nav className="fkeys" aria-label="Functions">
        {KEYS.map((k) => (
          <Link key={k.key} href={k.href} className={`keycap${active(k.href) ? " on" : ""}`} aria-current={active(k.href) ? "page" : undefined}>
            <kbd>{k.key}</kbd><b>{k.code}</b><span>{k.label}</span>
          </Link>
        ))}
        {SECONDARY.map((k) => (
          <Link key={k.code} href={k.href} className={`secondary${path.startsWith(k.href) ? " on" : ""}`}>{k.code}</Link>
        ))}
        {!demo && (
          <form action="/auth/signout" method="post" className="signout"><button>LOGOFF</button></form>
        )}
      </nav>
      {help && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={() => setHelp(false)}>
          <div className="overlay-box panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-head"><h2>HELP · KEYS AND COMMANDS</h2><button className="x" onClick={() => setHelp(false)} aria-label="Close">ESC</button></div>
            <div className="panel-body help-grid">
              <div>
                <h3>KEYS</h3>
                <dl className="keys">
                  <dt><kbd>/</kbd></dt><dd>Command bar</dd>
                  <dt><kbd>?</kbd></dt><dd>This overlay</dd>
                  {KEYS.map((k) => <Fragment key={k.key}><dt><kbd>{k.n}</kbd> <kbd>{k.key}</kbd> <kbd>g</kbd><kbd>{k.g}</kbd></dt><dd>{k.label}</dd></Fragment>)}
                  <dt><kbd>g</kbd><kbd>l</kbd></dt><dd>Lucid bot</dd>
                  <dt><kbd>↑</kbd><kbd>↓</kbd> <kbd>⏎</kbd></dt><dd>Pick a suggestion, GO</dd>
                  <dt><kbd>←</kbd><kbd>→</kbd></dt><dd>Step through a focused chart</dd>
                  <dt><kbd>Esc</kbd></dt><dd>Close</dd>
                </dl>
              </div>
              <div>
                <h3>COMMANDS · TYPE, THEN &lt;GO&gt;</h3>
                <dl className="keys">
                  <dt>2</dt><dd>Focus panel 2 on this page</dd>
                  <dt>AEYE</dt><dd>Find a ticker&apos;s row and flash it</dd>
                  {COMMANDS.map((c) => <Fragment key={c.word}><dt>{c.word}</dt><dd>{c.desc}</dd></Fragment>)}
                </dl>
              </div>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
