#!/usr/bin/env python3
"""Rule 5 event list: +5% spikes on liquid optionable names, labelled from headlines.

Source (read-only): the Stock Shorting Bot research cache
  events.parquet      the bot's daily 15:45 top-20 gainers with features (chg1545, adv20, next-5-day OHLC)
  news/<date>.parquet Alpaca/Benzinga headlines for those names, 36h before the 15:45 scan
                      (created_at <= 15:45 ET, so nothing after the decision time leaks in)
Classifier: shortingbot.news STRONG / FLUFF / DILUTION patterns (imported read-only), applied to the
ticker-specific headlines left after removing "movers"/market-wrap stories, plus an earnings pattern
(scheduled fundamental news counts as a real catalyst).

Labels
  hype   ticker-specific headlines, news.py label "hype", no earnings
  stale  no ticker-specific headline left (no news at all, or only movers/wrap/"maintains" stories)
  real   news.py "real_catalyst", or earnings, or "dilution"
  other  ticker-specific news without any keyword ("unclear")

Output: .cache/hype_events.parquet
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SBOT = HERE.parents[2] / "Stock Shorting Bot"
SC = SBOT / "research" / ".cache"
sys.path.insert(0, str(SBOT))
from shortingbot.news import DILUTION, FLUFF, STRONG  # noqa: E402  (read-only import)

MOVERS = [r"stocks moving", r"moving (higher|lower)", r"whale activity", r"shares are (trading|moving)",
          r"what'?s going on", r"\bwhy (is |are )?.{0,60}(shares|stock)", r"stock market today",
          r"what'?s (moving|driving) (the )?markets?", r"\bhalted\b", r"resume trade", r"circuit breaker",
          r"mid-?day", r"pre-?market", r"after-?hours", r"top (gainers|movers|losers)", r"trending stocks",
          r"\bmaintains\b", r"\breiterates\b", r"big stocks moving", r"session\b.*\bstocks\b",
          r"\b(dow|nasdaq|s&p 500)\b.*\b(gains|falls|rises|drops|jumps|tumbles|points)\b",
          r"\bshares (are )?(soaring|surging|jumping|spiking|rallying|skyrocketing|higher)\b",
          r"\bstock (is )?(soaring|surging|jumping|spiking|rallying|skyrocketing|higher)\b"]
EARN = [r"\b(q[1-4]|quarter(ly)?|fy ?\d*|full[- ]year|fiscal)\b.{0,40}\b(results|earnings|eps|revenue|sales|profit)\b",
        r"\bearnings\b", r"\bguidance\b", r"\beps\b", r"\b(beats|misses|tops)\b"]


def any_match(pats, text):
    return any(re.search(p, text) for p in pats)


def label(heads: list[str]) -> tuple[str, int]:
    spec = [h for h in heads if not any_match(MOVERS, h.lower())]
    if not spec:
        return "stale", 0
    text = " ".join(spec).lower()
    if any_match(EARN, text):
        return "real", len(spec)
    s, f, d = any_match(STRONG, text), any_match(FLUFF, text), any_match(DILUTION, text)
    if d or (s and not d):
        return "real", len(spec)
    if f:
        return "hype", len(spec)
    return "other", len(spec)


def main():
    e = pd.read_parquet(SC / "events.parquet", columns=[
        "symbol", "date", "chg1545", "p1545_raw", "adv20", "mcap", "cat_class", "c", "c_raw", "adj_ratio",
        "o1", "h1", "l1", "c1", "o2", "c2", "o3", "c3", "o4", "c4", "o5", "c5", "h2", "h3", "h4", "h5"])
    e = e[(e.date >= "2024-03-01") & (e.date <= "2026-10-12") & (e.chg1545 >= 0.05) & (e.p1545_raw > 10)
          & (e.adv20 >= 5e7)].copy()
    rows = []
    cache = {}
    for _, r in e.iterrows():
        f = SC / "news" / f"{r.date}.parquet"
        if r.date not in cache:
            cache[r.date] = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["symbols", "headline"])
        n = cache[r.date]
        mine = n[n.symbols.fillna("").str.split(",").apply(lambda xs: r.symbol in xs)] if len(n) else n
        lab, nspec = label(list(mine.headline.fillna("")))
        rows.append({"label": lab, "n_specific": nspec, "n_heads": len(mine)})
    e = pd.concat([e.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    k = e["adj_ratio"].fillna(1.0)                    # raw = adjusted * adj_ratio
    for c in ["c", "o1", "h1", "l1", "c1", "o2", "c2", "o3", "c3", "o4", "c4", "o5", "c5", "h2", "h3", "h4", "h5"]:
        e[c + "_r"] = e[c] * k
    e.to_parquet(HERE / ".cache" / "hype_events.parquet")
    print(len(e), e.symbol.nunique())
    print(pd.crosstab(e.label, e.cat_class))


if __name__ == "__main__":
    main()
