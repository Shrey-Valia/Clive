"use client";

import { useEffect, useRef, useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";

const BOOT = [
  "CLIVE TERMINAL v0.2 · PROPRIETARY · AUTHORIZED USERS ONLY",
  "LINK  SUPABASE ............................ OK",
  "LINK  ALPACA PAPER (SERVER-SIDE KEYS) ..... OK",
  "FEED  SHORT BOOK · SCAN BOARD · BLOTTER ... READY",
  "AUTH  MAGIC LINK · ADMIN LIST ENFORCED BY RLS",
];

export default function Login() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    const { error } = await supabaseBrowser().auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setError(error.message);
      setState("error");
    } else setState("sent");
  }

  return (
    <div className="signon">
      <div className="signon-brand"><span className="brand keycap">CLIVE</span><span className="muted">TERMINAL SIGN-ON</span></div>
      <ol className="boot" aria-label="System status">
        {BOOT.map((l, i) => <li key={l} style={{ animationDelay: `${i * 180}ms` }}>{l}</li>)}
      </ol>
      <section className="panel hot">
        <div className="panel-head"><h2>SIGN ON</h2><span className="code">LOGN</span></div>
        <div className="panel-body">
          {state === "sent" ? (
            <p className="val">LINK SENT TO <b className="amber">{email.toUpperCase()}</b>. OPEN IT ON THIS DEVICE · EXPIRES IN 60M.</p>
          ) : (
            <form onSubmit={send} className="signon-form">
              <label htmlFor="email">USER (EMAIL)</label>
              <div className="cmd open">
                <span className="prompt">&gt;</span>
                <input id="email" ref={input} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" spellCheck={false} />
                <button type="submit" className="gokey keycap" disabled={state === "sending"}>{state === "sending" ? "SENDING" : "<GO>"}</button>
              </div>
              <p className="note">A one-time sign-in link is emailed to you. Only addresses on the admin list can read data.</p>
              {state === "error" && <p className="neg" role="alert">COULDN&apos;T SEND THE LINK: {error.toUpperCase()}</p>}
            </form>
          )}
        </div>
      </section>
    </div>
  );
}
