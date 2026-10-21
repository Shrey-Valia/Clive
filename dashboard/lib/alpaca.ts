// Live reads from the Alpaca PAPER account. Server-only: the keys never reach the browser.
import "server-only";

const BASE = "https://paper-api.alpaca.markets/v2";

async function get<T>(path: string): Promise<T | null> {
  const key = process.env.APCA_API_KEY_ID, secret = process.env.APCA_API_SECRET_KEY;
  if (!key || !secret) return null;
  try {
    const r = await fetch(`${BASE}${path}`, {
      headers: { "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret },
      cache: "no-store",
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export type AlpacaAccount = { equity: string; last_equity: string; cash: string; buying_power: string; short_market_value: string };
export type AlpacaPosition = {
  symbol: string; qty: string; side: string; avg_entry_price: string; current_price: string;
  market_value: string; unrealized_pl: string; unrealized_plpc: string; unrealized_intraday_pl: string;
};

export const alpacaAccount = () => get<AlpacaAccount>("/account");
export const alpacaPositions = () => get<AlpacaPosition[]>("/positions");
