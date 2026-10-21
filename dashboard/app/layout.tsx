import type { Metadata } from "next";
import { IBM_Plex_Mono } from "next/font/google";
import { Tape } from "@/components/Tape";
import { TopBar } from "@/components/Terminal";
import { getTape, isDemo } from "@/lib/data";
import "./globals.css";

const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--f-mono" });

export const metadata: Metadata = { title: "Clive Terminal", description: "Every bot, its positions and its performance." };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const tape = await getTape().catch(() => []);
  return (
    <html lang="en" className={mono.variable}>
      <body>
        <TopBar demo={isDemo} />
        <Tape items={tape} />
        <main className="wrap">{children}</main>
        <footer className="statusbar">
          <span>CLIVE TERMINAL</span><span>ALPACA PAPER</span><span>SUPABASE {isDemo ? "OFFLINE · DEMO" : "LINKED"}</span>
          <span className="hint">PRESS / FOR COMMAND · F1–F4 TO SWITCH</span>
        </footer>
      </body>
    </html>
  );
}
