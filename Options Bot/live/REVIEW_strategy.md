# Review: live dip_pcs bot, strategy fidelity and risk logic

The coordinating session saved this file from the reviewer's findings. The reviewer's scratch scripts are in the session scratchpad under `review/`.

## Replication
- **Indicators match the research.** Live `signals.compute` and `entry_signal` on research OHLC over 661 days × 3 ETFs agree 28/28 SPY, 31/31 QQQ and 21/21 IWM with 0 mismatches, and all 54 research entries fire.
- **Data matches.** Live Alpaca SIP split-adjusted daily bars are identical to the research data.
- **Deciding at 15:50 is the largest divergence.** Recomputed on 15:50 minute bars, the live rule catches only 39 of the 54 research trades. The 15 trades it misses were all winners and account for 37% of research P&L ($2,507 of $6,746). It also takes 11 live-only trades that were never backtested.
- **Guards:**
  - MIN_CREDIT_FRAC=0.065 filters no research trade. The research minimum was 6.78%, so the margin is thin.
  - The kill switch would have blocked 0 research entries.

## Findings
1. **HIGH: the 15:50 decision time loses about a third of the edge** (`config.py:94`, `run.py:336`). Re-backtest using a 15:50 snapshot, decide at about 15:56, or re-validate the rule.
2. **HIGH: an exception while re-pricing leaves orders working and unrecorded** (`execution.py:136-142`). Catch per job and add a finally block that runs cancel_all, apply_results and save_positions. Recover the entry date from fills.
3. **HIGH: exits are blocked when one leg bids zero** (`strategy.py:37`). Allow bid=0 on the leg being sold, raise a heartbeat warning, and fall back to the natural price at tdte<=1.
4. **MEDIUM: the kill switch can read stale or mixed-source VIX** (`signals.py:118-134`). Entries should require same-day, same-source quotes no older than 30 minutes.
5. **MEDIUM: take-profit is measured against the fill instead of entry_mid** (`run.py:124`).
6. **MEDIUM: missing history bars and stale quotes don't fail closed** (`run.py:92`, `signals.py`, `broker.py:296-305`).
7. **MEDIUM: the per-position cap is sized at mid instead of the last ladder price** (`run.py:195-206`).
8. **MEDIUM: P&L journaling gaps.** Spreads that vanish lose their P&L, and the trade id collides when there are two partial closes in one day.
9. **LOW: intentional divergences to document:** the 1-day expiry exit, the intraday take-profit, no same-day re-entry, shared caps that always squeeze IWM, the new quote guards, and the delta source.
10. **LOW: smaller issues:**
    - is_monthly misses Thursday expiries.
    - A stale docstring.
    - STOP_MULT semantics differ from the research.
    - The earnings guard window is wrong.
