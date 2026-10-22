import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono } from "next/font/google";
import { Tape } from "@/components/Tape";
import { TopBar } from "@/components/Terminal";
import { AlertBanner } from "@/components/Bits";
import { AutoRefresh, ChromeGate, StatusBar } from "@/components/Live";
import { EventStrip } from "@/components/Events";
import { getChrome, getEvents, getTape, isDemo } from "@/lib/data";
import { etParts } from "@/lib/time";
import "./globals.css";

const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--f-mono" });

export const metadata: Metadata = { title: "Clive Terminal", description: "Every bot, its positions and its performance.", manifest: "/manifest.webmanifest" };
export const viewport: Viewport = { themeColor: "#000000", width: "device-width", initialScale: 1, viewportFit: "cover" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [tape, chrome, ev] = await Promise.all([
    getTape().catch(() => ({ date: null, live: false, items: [] })),
    getChrome().catch(() => ({ alerts: [], email: null, positions: null, gross: null, equity: null, lastSync: null, tickers: [] as string[], flow: "overnight_v1" as const })),
    getEvents().catch(() => null),
  ]);
  return (
    <html lang="en" className={mono.variable}>
      <body>
        <ChromeGate>
          <TopBar demo={isDemo} tickers={chrome.tickers} flow={chrome.flow} />
          <Tape {...tape} />
          {ev && !ev.missing && <EventStrip events={ev.events} today={etParts().date} sample={ev.sample} />}
          <AlertBanner alerts={chrome.alerts} />
          <AutoRefresh />
        </ChromeGate>
        <main className="wrap">{children}</main>
        <StatusBar demo={isDemo} email={chrome.email} positions={chrome.positions} gross={chrome.gross} lastSync={chrome.lastSync} flow={chrome.flow} />
      </body>
    </html>
  );
}
