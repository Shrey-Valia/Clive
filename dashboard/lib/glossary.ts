// Glossary for <Abbr> tooltips and the flag legend.

export const GLOSSARY: Record<string, string> = {
  "SI%": "Short interest as a % of float: shares already sold short. High SI = crowded short, more squeeze risk.",
  DTC: "Days to cover: short interest ÷ average daily volume. How many days shorts would need to buy back.",
  RVOL: "Relative volume: today's volume vs its recent average. 3x+ means unusual attention.",
  WICK: "Upper wick: how far the close sits below the day's high, as % of the range. A big wick = sellers showed up.",
  SHVOL: "FINRA short-sale volume ratio for the latest day: % of reported volume that was short sales.",
  FTD: "Fails-to-deliver: days in the recent SEC FTD file with fails. Persistent FTDs hint at a hard-to-borrow name.",
  BRW: "Borrow: ETB = easy to borrow on Alpaca, HTB = hard to borrow, NO = not shortable (excluded).",
  NEWS: "Catalyst read from headlines. NO NEWS / HYPE / DILUTION favor a fade; REAL NEWS (de-risking) excludes the name.",
  SCORE: "Short score 0–100 = 35·MOVE + 20·CROWD + 10·FTD + 35·STORY, renormalized over the parts with data.",
  MOVE: "Move & volume component: size of the gain, RVOL, upper wick and close-in-range. Weight 35.",
  CRWD: "Crowding component: SI% / DTC / short volume in the ideal band (not so crowded it squeezes). Weight 20.",
  STRY: "Story component: how little real news backs the move. Weight 35.",
  MCAP: "Market capitalization.",
  GROSS: "Gross exposure: sum of |position value| ÷ equity.",
  "DAY P&L": "Equity now minus equity at the prior close (Alpaca last_equity).",
  "OPEN P&L": "Unrealized P&L on the positions held right now.",
  DD: "Drawdown: equity ÷ its running peak − 1.",
  "MAX DD": "Largest peak-to-trough drop in equity.",
  SHARPE: "Annualized Sharpe: mean daily return ÷ its std dev × √252. No risk-free rate.",
  SORTINO: "Like Sharpe but divides by downside deviation only.",
  PF: "Profit factor: gross winning $ ÷ gross losing $. Above 1 makes money.",
  CAGR: "Compound annual growth rate.",
  ROTATION: "overnight_v1: at 15:55 ET the bot covers yesterday's shorts and opens today's five. intraday_v2: picks from the 15:45 scan are shorted at the next 09:31 open and covered at 15:55 the same day.",
  "DRY RUN": "The bot scanned and picked but sent no orders. Picks read WOULD SHORT.",
  LIVE: "The bot sent real orders to the Alpaca paper account.",
  SLIP: "Slippage: fill price vs the scan's close. For a short, a lower fill is worse.",
  ETB: "Easy to borrow.",
  HTB: "Hard to borrow.",
  CUT: "Only the top 5 eligible names by rank are traded. Everything below the cut is watched, not traded.",
  "T−": "Countdown to the next 15:55 ET rotation.",
  MLL: "Maximum loss limit: the trailing floor a funded account can't close below.",
};

export type FlagInfo = { code: string; label: string; tone: "bad" | "warn" | "info"; help: string };

/** Bot flags → short code, tone for a SHORT, and plain-English help. */
export const FLAGS: Record<string, FlagInfo> = {
  NO_SI: { code: "NOSI", label: "No SI data", tone: "info", help: "No short-interest data; crowding is scored without it." },
  LOW_SI_NO_FORCED_SELLERS: { code: "LOSI", label: "Low SI", tone: "info", help: "Short interest below the ideal band: little squeeze fuel, but little crowd to follow either." },
  SMALL_MOVE: { code: "SMOV", label: "Small move", tone: "warn", help: "Gained under 20%: less stretched, weaker fade setup." },
  LOW_RVOL: { code: "LRVL", label: "Low RVOL", tone: "warn", help: "Relative volume under 3x: the move lacks attention." },
  CLOSING_ON_HIGHS: { code: "CLHI", label: "Closing on highs", tone: "bad", help: "Closing in the top 15% of the range: buyers still in control, risky to short." },
  HEAVY_SHORT_VOLUME: { code: "HSV", label: "Heavy short vol", tone: "bad", help: "60%+ of volume was short sales: crowded, squeeze risk." },
  STALE_SI: { code: "STSI", label: "Stale SI", tone: "info", help: "Short-interest data is over 30 days old." },
};

export function flagInfo(f: string): FlagInfo {
  if (FLAGS[f]) return FLAGS[f];
  if (f.startsWith("STALE_SI")) return { ...FLAGS.STALE_SI, help: `${FLAGS.STALE_SI.help} (${f.replace("STALE_SI_", "")})` };
  return { code: f.slice(0, 4), label: f.replace(/_/g, " "), tone: "info", help: f.replace(/_/g, " ").toLowerCase() };
}

export const CATALYST: Record<string, { label: string; tone: "good" | "bad" | "info" }> = {
  no_news: { label: "NO NEWS", tone: "good" },
  hype: { label: "HYPE", tone: "good" },
  dilution: { label: "DILUTION", tone: "good" },
  unclear: { label: "UNCLEAR", tone: "info" },
  real_catalyst: { label: "REAL NEWS", tone: "bad" },
  news_unavailable: { label: "FEED DOWN", tone: "info" },
};

export const RUN_LABEL: Record<string, string> = {
  executed: "EXECUTED", executed_with_errors: "EXECUTED · ERRORS", dry_run_done: "DRY RUN",
  scanned: "SCANNED", failed: "FAILED",
  market_closed: "MARKET CLOSED", picks_saved: "PICKS SAVED", entered: "ENTERED", exited: "EXITED",
};

export const WEIGHTS = { move_volume: 35, crowding: 20, ftd: 10, catalyst: 35 } as const;
export type CompKey = keyof typeof WEIGHTS;
export const COMP_ORDER: CompKey[] = ["move_volume", "crowding", "ftd", "catalyst"];
export const COMP_LABEL: Record<CompKey, string> = { move_volume: "MOVE", crowding: "CROWD", ftd: "FTD", catalyst: "STORY" };
export const COMP_COLOR: Record<CompKey, string> = { move_volume: "#3987e5", crowding: "#d55181", ftd: "#9085e9", catalyst: "#199e70" };
