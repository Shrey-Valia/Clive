"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const KEYS = [
  { key: "F1", code: "HOME", href: "/", label: "Overview" },
  { key: "F2", code: "SHRT", href: "/shorting", label: "Shorting" },
  { key: "F3", code: "LUCD", href: "/lucid", label: "Lucid" },
  { key: "F4", code: "OPTN", href: "/options", label: "Options" },
];
const ALIASES: Record<string, string> = {
  HOME: "/", OV: "/", SHRT: "/shorting", SHORT: "/shorting", SHORTING: "/shorting",
  LUCD: "/lucid", LUCID: "/lucid", OPTN: "/options", OPT: "/options", OPTIONS: "/options",
};

function useEtClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function marketState(d: Date) {
  const et = new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const mins = et.getHours() * 60 + et.getMinutes(), day = et.getDay();
  if (day === 0 || day === 6) return { label: "CLOSED", cls: "off" };
  if (mins >= 570 && mins < 960) return mins >= 945 && mins < 960 ? { label: "ROTATION", cls: "warn" } : { label: "OPEN", cls: "ok" };
  if (mins >= 240 && mins < 570) return { label: "PRE-MKT", cls: "warn" };
  if (mins >= 960 && mins < 1200) return { label: "AFTER-HRS", cls: "warn" };
  return { label: "CLOSED", cls: "off" };
}

export function TopBar({ demo }: { demo: boolean }) {
  const path = usePathname();
  const router = useRouter();
  const now = useEtClock();
  const [cmd, setCmd] = useState("");
  const [err, setErr] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const k = KEYS.find((x) => x.key === e.key);
      if (k) { e.preventDefault(); router.push(k.href); return; }
      if (e.key === "/" && document.activeElement !== input.current) { e.preventDefault(); input.current?.focus(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  function go(e: React.FormEvent) {
    e.preventDefault();
    const word = cmd.trim().toUpperCase().replace(/\s*<?GO>?$/, "");
    const href = ALIASES[word];
    if (href) { router.push(href); setCmd(""); setErr(""); }
    else setErr(word ? `UNKNOWN: ${word}. TRY SHRT, LUCD, OPTN, HOME` : "");
  }

  const mkt = now ? marketState(now) : null;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <header className="topbar">
      <div className="cmdrow">
        <Link href="/" className="brand">CLIVE</Link>
        <form onSubmit={go} className="cmd">
          <label htmlFor="cmd" className="prompt">&gt;</label>
          <input id="cmd" ref={input} value={cmd} onChange={(e) => setCmd(e.target.value)} spellCheck={false}
            autoComplete="off" placeholder="TYPE A FUNCTION, e.g. SHRT" aria-label="Command" />
          <button type="submit" className="gokey">GO</button>
        </form>
        {err && <span className="cmderr">{err}</span>}
        <div className="clock">
          {demo && <span className="pill warn">DEMO DATA</span>}
          {mkt && <span className={`pill ${mkt.cls}`}>{mkt.label}</span>}
          <span className="mono">{now ? now.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false }) : "--:--:--"} ET</span>
        </div>
      </div>
      <nav className="fkeys">
        {KEYS.map((k) => (
          <Link key={k.key} href={k.href} className={active(k.href) ? "on" : ""}>
            <kbd>{k.key}</kbd><b>{k.code}</b><span>{k.label}</span>
          </Link>
        ))}
        {!demo && (
          <form action="/auth/signout" method="post" className="signout"><button>LOGOFF</button></form>
        )}
      </nav>
    </header>
  );
}
