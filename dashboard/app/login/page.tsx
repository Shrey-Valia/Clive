"use client";

import { useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";

export default function Login() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");

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
    <main className="login">
      <h1 className="amber">CLIVE TERMINAL · SIGN ON</h1>
      {state === "sent" ? (
        <p>Check {email} for a sign-in link. It expires in one hour.</p>
      ) : (
        <form onSubmit={send}>
          <label htmlFor="email">EMAIL</label>
          <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <button type="submit" disabled={state === "sending"}>
            {state === "sending" ? "Sending…" : "SEND SIGN-IN LINK <GO>"}
          </button>
          {state === "error" && <p className="neg">Couldn't send the link: {error}</p>}
        </form>
      )}
    </main>
  );
}
