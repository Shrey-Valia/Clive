"use client";
// Small client islands: live countdowns, tick-flash numbers, auto-refresh,
// the sticky status bar, and the chrome gate that hides the tape on /login.

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { countdown, etParts, isTradingDay, nextRotation, schedule } from "@/lib/time";

function useNow(ms = 1000) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

/** "2H14M" until `to`, updating live. */
export function Countdown({ to, secs = false }: { to: string; secs?: boolean }) {
  const now = useNow(secs ? 1000 : 15000);
  if (now === null) return <span className="mono">--</span>;
  return <span className="mono">{countdown(new Date(to).getTime() - now, secs)}</span>;
}

/** A number that flashes green/red for 700ms when its value changes between refreshes. */
export function Num({ v, children, className = "" }: { v: number | null; children: React.ReactNode; className?: string }) {
  const prev = useRef(v);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    if (prev.current !== null && v !== null && v !== prev.current) {
      setFlash(v > prev.current ? "flash-up" : "flash-down");
      const t = setTimeout(() => setFlash(""), 700);
      prev.current = v;
      return () => clearTimeout(t);
    }
    prev.current = v;
  }, [v]);
  return <span className={`${className} ${flash}`}>{children}</span>;
}

/** router.refresh() every 30s around the rotation (15:40–16:20 ET), every 5 min in market hours. */
export function AutoRefresh() {
  const router = useRouter();
  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const tick = () => {
      const p = etParts();
      let wait: number | null = null;
      if (isTradingDay(p.date)) {
        const s = schedule(p.date);
        if (p.mins >= s.scan - 5 && p.mins < s.sync + 5) wait = 30_000;
        else if (p.mins >= 570 && p.mins < s.close) wait = 300_000;
      }
      t = setTimeout(() => { if (wait !== null && document.visibilityState === "visible") router.refresh(); tick(); }, wait ?? 300_000);
    };
    tick();
    return () => clearTimeout(t);
  }, [router]);
  return null;
}

/** Hides its children on /login (no tape, alerts or command bar on the sign-on screen). */
export function ChromeGate({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  if (path.startsWith("/login")) return null;
  return <>{children}</>;
}

/** Sticky bottom status bar with live fields. */
export function StatusBar({ demo, email, positions, gross, lastSync, flow = "intraday_v2" }: {
  demo: boolean; email: string | null; positions: number | null; gross: number | null; lastSync: string | null; flow?: string;
}) {
  const now = useNow(1000);
  const path = usePathname();
  const login = path.startsWith("/login");
  const rot = now !== null ? nextRotation(new Date(now)) : null;
  const left = rot && now !== null ? rot.getTime() - now : null;
  const cls = left === null ? "" : left < 60_000 ? "neg blink" : left < 15 * 60_000 ? "warn" : "val";
  const syncAge = lastSync && now !== null ? Math.round((now - new Date(lastSync).getTime()) / 60000) : null;
  return (
    <footer className="statusbar">
      <span className="sb-brand">CLIVE TERMINAL</span>
      {!login && (
        <>
          <span>{flow === "intraday_v2" ? "COVER" : "ROTATION"} <b className={cls}>T−{left === null ? "--:--:--" : countdown(left, true)}</b></span>
          <span>SYNC <b className="val">{syncAge === null ? "—" : syncAge < 60 ? `${syncAge}M AGO` : syncAge < 2880 ? `${Math.round(syncAge / 60)}H AGO` : `${Math.round(syncAge / 1440)}D AGO`}</b></span>
          <span>POS <b className="val">{positions ?? "—"}</b></span>
          <span>GROSS <b className="val">{gross === null ? "—" : `${gross.toFixed(0)}%`}</b></span>
          <span className="hide-sm">ALPACA PAPER</span>
          <span className="hide-sm">SUPABASE {demo ? <b className="warn">OFFLINE · DEMO</b> : "LINKED"}</span>
        </>
      )}
      <span className="hint">{email ? <>{email.toUpperCase()} · </> : null}<kbd>?</kbd> KEYS · <kbd>/</kbd> COMMAND</span>
    </footer>
  );
}
