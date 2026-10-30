# Options bot, engineering round 3: host death, execution quality, monitoring, the condor at $15k

Written 2026-10-23, after the 2026-10-22 host outage (the 15:00–15:45 runs were missed) and a second network
outage on 2026-10-23 (the 11:15–12:45 intraday runs never ran; the 13:00 run failed on DNS). Branch
`worktree-agent-a3d77a177ee469bfe`. Code: `live/resilience.py` and `live/alerts.py` (new), plus changes in `run.py`,
`broker.py`, `db.py`, `state.py`, `strategy.py`, `execution.py`, `sync.py`, `config.py`. Tests:
`live/tests/test_round3.py` and `test_entry_fill.py` (37 new). Migration: `dashboard/supabase/migrations/010_options_exec_quality.sql`
(not applied).

Nothing here was deployed. No order was placed, canceled or modified on any account. The only Alpaca calls were
read-only GETs (account, positions, clock/calendar, option chain snapshots, corporate actions).

## 1. Summary

| # | Finding | Severity | Fix | Flag (default) |
|---|---|---|---|---|
| 1 | A host that dies mid-run cannot leave half a spread: every order is one Alpaca mleg order, which fills as a unit. | none (verified) | none needed | – |
| 2 | A host that dies while an order is working loses the fills: the next run adopts the spread blind (entry mid and signal lost, take-profit then measured against the fill instead of the research mid), or journals a close as `external`. | medium | write-ahead inflight journal and recovery | `RECOVER_INFLIGHT` (on, bookkeeping only) |
| 3 | Take-profits and overdue expiry exits depend on the host. Alpaca allows GTC limit orders for mleg, but no stop orders for mleg. | medium | optional resting GTC take-profit and resting overnight expiry exit | `STAGE_TP_ORDERS`, `REST_UNFILLED_EXPIRY_EXITS` (off) |
| 4 | SPY's ex-dividend date is the third Friday of Mar/Jun/Sep/Dec, which is the monthly expiry the condor targets. The condor's planned close day (expiry − 1 session) is the last cum-dividend day, the day an ITM short call gets exercised early. Nothing guarded this. | medium | ex-dividend early-exercise guard | `CONDOR_EXDIV_GUARD` (**on**, safety fix) |
| 5 | The 16:15 sync wrote `run_state = ok` and overwrote a 15:55 `error` (for example "EXPIRY EXIT NOT FILLED") on the dashboard. | medium (bug) | heartbeat split, same as the shorting bot | always |
| 6 | The options bot raised no `alerts` rows and sent no ntfy pushes. | medium | `live/alerts.py`, same contract as the shorting bot | `ALERTS_ENABLED`, `ALERTS_PUSH` (on) |
| 7 | A truncated last line in `trades.jsonl` / `orders.jsonl` (host died mid-append) made every later reader raise: sync, entry recovery. | low (bug) | the reader skips it with a warning | always |
| 8 | Slippage was not recorded: fills were logged without the planned mid. | low | mid, natural, slippage per order (journal + migration 009) | always |
| 9 | Dip spreads still open on expiry day may be worked after 15:00 (up to 15:59 at the daily run), into Alpaca's sell-out window. The condor already had a 15:00 deadline. | low | the same deadline for dip spreads | `DIP_EXPIRY_DAY_DEADLINE_ENABLED` (off) |
| 10 | Plain dry runs wrote Supabase rows and an `ok` heartbeat. | low | `--dry-run`: no orders, no Supabase, no alerts | CLI |
| 11 | `test_shorting_keys_never_reach_trading_host` needed the real `Stock Shorting Bot/.env`, so it failed in any fresh checkout. | test bug | hermetic fake env | – |
| 12 | Condor at $15k: 1 condor, max loss about $2,275 (15% of equity, cap 20%). Alpaca charges about $2,275 under the universal spread rule; the bot reserves the full $2,500 wing. | verified | prints the Alpaca requirement in the plan | – |
| 13 | README said the condor sleeve is off. It has been on since commit 1e80e5f. | doc drift | README fixed | – |

## 2. Alpaca facts this relies on (with sources)

| Fact | Source |
|---|---|
| An mleg order fills as a unit: "Placing these four legs as a single MLeg order ensures they fill together or not at all." | [Options Level 3 Trading](https://docs.alpaca.markets/docs/options-level-3-trading) |
| An mleg order is accepted only if every short leg is covered inside that order. Ratios must be in simplest form. At most 4 legs. | same; [Create an order](https://docs.alpaca.markets/us/reference/postorder) ("list of order legs (<= 4)") |
| Order types: options "market, limit, stop, stop_limit"; **multileg "market, limit"** only. "Stop and stop-limit orders are limited to single-leg orders." | [Create an order](https://docs.alpaca.markets/us/reference/postorder); [Options Trading](https://docs.alpaca.markets/us/docs/options-trading) |
| Time in force for options: "`day` or `gtc`". GTC options orders: limit only, auto-canceled after 90 days, and "Submitting a GTC order will reserve the required buying power". | [Options Trading](https://docs.alpaca.markets/us/docs/options-trading); [GTC options (Alpaca Learn, 2026-03-05)](https://alpaca.markets/learn/how-to-trade-good-til-canceled-options-trading-with-python-and-alpaca) |
| mleg `limit_price`: positive = debit, negative = credit. | [Create an order](https://docs.alpaca.markets/us/reference/postorder) |
| Margin, universal spread rule: maintenance margin is the worst-case loss of the payoff, premiums excluded, computed per expiration. Cost basis = maintenance margin + net price × multiplier (worked example: $1,000 margin − $500 credit = $500 charged). | [Options Level 3 Trading](https://docs.alpaca.markets/docs/options-level-3-trading) |
| Expiration day: from 3:30 pm ET Alpaca stops accepting new orders that open or extend positions expiring that day. ITM by $0.01 or more auto-exercises; without the buying power "Alpaca will sell-out the position within 1 hour before expiry". | [Options trading overview](https://docs.alpaca.markets/docs/options-trading-overview); [Options Trading](https://docs.alpaca.markets/us/docs/options-trading) |
| Early assignment: "the OCC assigns a random account and sellers of contracts can be assigned overnight". Assignments arrive as OPASN/OPTRD activities and are not on the websocket. | [Options trading overview](https://docs.alpaca.markets/docs/options-trading-overview); [Non-trade activities for option events](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events) |
| SPY ex-dividend dates (Alpaca `GET /v1/corporate-actions`, read 2026-10-23): 2024-12-20, 2025-03-21, 2025-06-20, 2025-09-19, 2025-12-19, 2026-03-20, 2026-06-18 (Juneteenth Friday), 2026-09-18. Each is the third Friday of the quarter month, or the session before it. Rates: $1.70–$1.99. Future dates are not announced until days before. IWM goes ex around mid-month (2026-09-15) and is only in the put sleeve. | Alpaca market data, read-only call |

Not documented, so it goes on the paper checklist (section 8): whether a working closing order holds the
position's quantity so a second closing order is rejected ("insufficient qty"); whether a GTC mleg close
reserves buying power; whether a GTC mleg order submitted at 15:59:50 is accepted; whether an mleg order
partially fills.

## 3. Host death, job by job

Every row assumes the process stops dead at the worst moment. "Fills" means fills at Alpaca after the death.

| Job | Dies… | Before round 3 | Now |
|---|---|---|---|
| any | mid-submit of a spread | Impossible to be half-filled: one mleg order. The order exists or it doesn't. | same |
| 15:45 daily | after `mark_ran`, before orders | No trading today; a rerun needs `--force`. Health probe flags the missed run. | same, plus `run_failed` alert if it raised |
| 15:45 daily | with entry or exit orders working | DAY orders keep working until they fill or hit 16:00. Fills are found by the next run: an entry is **adopted** (entry date from fills, entry credit = broker average, entry mid and signal lost); an exit becomes a vanished spread journaled `external_closed` with a P&L from activities. | The inflight journal names every client_order_id before its submit. The next executing run (sweep, intraday, daily or close-all) looks each one up, follows `replaced_by`, cancels anything still open, and books the fills with the **planned** template (entry mid, signal, deltas), the dead run's date and the exit reason. It replaces a blind adoption. It skips anything the dead run had already booked. Event alert `inflight_recovered`. |
| 15:45 daily | the 1-day-before-expiry exit unfilled | Next morning's 09:45 sweep closes it. If the host is still down: Alpaca's expiry handling (ITM exercise / sell-out from 15:00, assignment of short ITM legs). | Same, plus optional `REST_UNFILLED_EXPIRY_EXITS`: the unfilled exit rests as a GTC close at its cutoff price and works from 09:30 without the host. |
| 09:45 sweep | before or mid-close | DAY close orders work until 16:00 (a close is allowed after 15:30; only opening orders are blocked). Nothing else closes the spread. | same, plus the resting expiry exit if enabled |
| intraday | mid take-profit | The TP order keeps working until the close. A fill is booked as `external` the next run. | recovered with reason `take_profit` |
| any | holding a dip spread intraday | Take-profit happens only when a run happens. | Optional `STAGE_TP_ORDERS`: a GTC close at the research TP price works at Alpaca all day. |
| any | holding a condor | Held to expiry by design; no TP. Risk is in the last two days (below). | ex-div guard plus resting expiry exit |
| close-all | mid single-leg orphan closes | The orphan legs are unmatched quantities; closing some and not others does not unpair a spread. | same |
| 16:15 sync | anywhere | Reporting only; the next sync repeats it. A truncated journal line made every later sync fail. | truncated lines skipped |

Atomicity of the local files: `positions.json`, `condors.json`, `inflight.json`, `resting.json`, the
once-per-day guard and the alert state are written through a temp file and `os.replace`. The JSONL journals are
appended; a torn last line is now skipped by the reader. The flock is released by the kernel when the
process dies, so a dead run never blocks the next one.

### Resting orders: how they are kept from colliding with the bot

* client_order_id prefix `options-rest-…` (`REST_ORDER_PREFIX`). The broker refuses a GTC order without it, so
  no ordinary job can rest by mistake. `Broker.cancel_all()` (the leftover cancel at the start of a run)
  leaves them alone; `--close-all` cancels them (`include_resting=True`).
* Every executing run that reads the book first cancels every resting order and **confirms** it is final
  (`resilience.cancel_resting`). A resting order that filled meanwhile is booked as a closed trade
  (`tp_resting` / `expiry_resting`) before the book is reconciled, so the bot never works an exit on a spread
  that is already closed. A resting order that will not confirm marks its spread as not tradable this run, plus
  a critical `resting_order` alert.
* After the run is settled and saved, the resting orders are staged again. Take-profits start from the session
  **after** entry: the research never took profit on the entry day, and an entry-day close would be a day
  trade on a sub-$25k account. Spreads with ≤ 1 session to expiry get none; the expiry exit owns those.
* Cost: about two extra calls per held spread per run while a flag is on. Nothing at all when both flags are
  off and `state/resting.json` is empty.

### Exchange-side stops: not possible

Alpaca rejects stop and stop-limit for mleg. A stop on the short leg alone would leave the long leg behind and
could fill the short leg against a stale print, which is the unpaired-leg risk this design avoids. So
`STOP_MULT` (off, as in the research) can only ever be host-side.

## 4. Execution quality

* **Pricing** (unchanged, verified): entries start at the net mid and walk at most halfway toward the natural
  over 5 prices, 30 s each, 15:55–15:58. Exits walk from mid to natural, then natural + $0.05 (capped at the
  width) at the cutoff until 15:59:30. Partially filled orders are never replaced: they are canceled, settled,
  and the remainder resubmitted. Fills are summed per order id.
* **Entry not filled by 15:58**: the order is canceled at the cutoff (entries have no aggressive fallback), the
  final sweep confirms it, and there is no entry that day. The signal is re-evaluated at the next daily run.
  That is the research's "entering one day late" caveat. Now logged explicitly.
* **Slippage**: each order journal row (`orders.jsonl`) and `options_live_orders` row now carries `mid_price`,
  `natural_price`, `slippage` (per share, positive = worse than mid) and `slippage_usd`. Before migration 009 is
  applied, `db.py` drops the unknown columns and retries, as the shorting bot's `db.py` does, so the row still
  lands.
* **Expiry day vs Alpaca's handling**: the 09:45 sweep closes spreads expiring today, and the intraday runs do too
  until 15:30. The condor stops at 15:00 (deadline), which lines up with Alpaca's "sell-out within 1 hour before
  expiry". Dip spreads may still be worked until 15:59. Closing orders remain allowed after 15:30, but they then
  race Alpaca's own sell-out. `DIP_EXPIRY_DAY_DEADLINE_ENABLED` applies the condor's deadline to them (off:
  closing late is usually better than leaving it to exercise).
* **Assignment / early exercise**:
  * Dip sleeve (short puts on SPY / IWM): a dividend makes an early put exercise *less* attractive, so ex-dates
    are not a put risk. Deep-ITM puts can be exercised early for carry; exposure is short (≤ 5 sessions, closed
    1 session before expiry). Assigned stock is flattened at the next daily, sweep or intraday run.
  * Condor short call: the real risk. The Dec 2026 expiry (2026-12-18) **is** SPY's expected ex-date, so the
    close on 2026-12-17 happens on the last cum-dividend day. If that close does not fill and the 814 call is ITM
    with extrinsic value below about $1.90, assignment that night is likely: short 100 SPY per condor, owing the
    dividend. The new guard closes the condor on the last cum-dividend session before any ex-date inside its
    life when the call is ITM and extrinsic value < dividend (Alpaca's latest rate, at least $2.00). It runs at the
    daily run, plus the intraday runs from 15:00 for redundancy. An ITM call without a quote counts as at risk.
  * After an assignment the remaining long leg is an unpaired leg. It is never traded automatically, and it is
    risk-limited, but see residual risk 4.

## 5. Monitoring

* `live/alerts.py`, same contract as `shortingbot/alerts.py`. Rows go to Supabase `alerts` (`bot_id 'options'`),
  with an ntfy push. Topic lookup order: process env (set but empty means off, which the deploy kit uses in
  shadow mode), then `Options Bot/.env`, then `Stock Shorting Bot/.env`, where the owner's `NTFY_TOPIC` lives. Only
  `NTFY_TOPIC` / `NTFY_SERVER` are read from the shorting file. Local open-alert state is kept in
  `live/state/alerts_state.json`.
* Condition alerts resolve themselves:
  * `assignment` and `unpaired_legs` follow the book.
  * `exit_unfilled`, `expiry_risk`, `condor_deadline` and `exdiv_risk` resolve when the spread is gone.
  * `run_failed`, `refused` and `lock_busy` resolve on the next successful run of the same kind.
  * `account` and `leftover_orders` resolve on the next daily run that passes.
  * `resting_order` resolves on the next confirmed cancel.
  * `sync_failed` resolves on the next good sync.
* Event alerts: `inflight_recovered`.
* Intraday failures alert at `warning` (the next check retries); daily, sweep and close-all failures at
  `critical`.
* Dry runs never alert.
* Heartbeat parity with the shorting bot:
  * Runs write `last_run_status/at` and sync writes `last_sync_status/at`. `run_state` is the worse of the two.
  * Before migration 004 the old behaviour applies, except that a sync no longer overwrites with anything but an
    error.
  * Quiet intraday checks now update `last_heartbeat`/`last_run_at` every 15 minutes, so a dead host shows up
    as a stale heartbeat. They don't touch `run_state` or the message.
* `--check` shows alerts and ntfy status (topic found yes/no, never the value), any pending inflight journal,
  resting orders, and the next SPY ex-date.

## 6. Condor at $15k

Sizing (`strategy.size_position` with `CONDOR_RISK_PER_POSITION = 0.20`, the shared `RISK_TOTAL = 0.50`):

* Per-condor max loss = (wider wing − worst ladder credit) × 100. The worst ladder credit is used, so a better fill
  only lowers the real max loss.
* Budget = min(20% × equity, 50% × equity − open max loss of both sleeves) = min($3,000, $7,500) = $3,000.
* Options buying power is charged the wider wing × 100 per condor. Alpaca's own charge is the universal spread
  rule's margin minus the credit: about the max loss, so the bot's reservation is conservative by the credit
  (about $230 per condor).
* The dip sleeve then gets min(25% × $15k, $7,500 − condor max loss) = $3,750 per spread.

Today's chain (read-only, 2026-10-23): SPY 760.5, 2026-12-18 (56 DTE, monthly), 650/675 puts and 814/835 calls.
The put wing is 25 and the call wing 21, against a 3% target of 22.8: these are the nearest listed strikes. Mid
credit ≈ $2.30, natural $2.21, ladder 2.30 → 2.25. Max loss ≈ (25 − 2.25) × 100 = **$2,275 per condor, so 1x
(15.2% of equity)**; 2x would be $4,550, over the $3,000 cap. Alpaca margin $2,500, cost basis ≈ $2,275. The bot
reserves $2,500 of the $15,000 options buying power. The final 15:55 decision is in section 7.

Note the uneven wings: with $5 strikes below 680 the put side is wider than 3%, which sets the max loss. The
research engine picked the nearest strike the same way, so this is faithful, but it makes the condor's
credit / risk worse than a symmetric 3% condor.

## 7. Dry run of today's decision path (2026-10-23)

Command: `live/run.py --dry-run --preview` started 15:38:59 from this worktree, using the options paper account's
keys (OPT_* only, no Supabase, no ntfy). It waited for the 15:55 decision time, as the cron run does, with the
strict freshness checks on. Output at 15:55:03:

```
MODE DRY RUN | equity $15,000.00 | VIX cboe delayed 15:39; VIX3M cboe delayed 15:39
kill switch: ok -> VIX 19.82, VIX/VIX3M 0.957
-- open spreads: 0
SPY  close 759.92 200DMA 727.01 RSI2 15.0 IBS 0.26 -> no signal: RSI2 15.0 >= 10; IBS 0.26 >= 0.2
     | preview: would size 2x (max loss $1,411/spread, budget $3,750)
     SPY 2026-10-30 DTE 7: sell 748P (Δ-0.26 2.96/3.10) buy 732P (Δ-0.10 1.07/1.11) width 16 mid 1.94 nat 1.85
IWM  close 275.87 200DMA 278.23 RSI2 48.6 IBS 0.87 -> no signal (below the 200DMA)
     | preview: would size 7x (max loss $517/spread)
-- condor sleeve (ENABLED): open 0
SPY condor spot 759.92 -> OPEN 1x (max loss $2,283/spread, budget $3,000)
     2026-12-18 DTE 56: put sell 675P (Δ-0.10 3.80/3.92) buy 650P (2.75/2.82) wing 25
                        call sell 814C (Δ0.10 1.59/1.69) buy 835C (0.41/0.49) wing 21
     mid credit 2.27 nat 2.08 (9% of width 25)
     limit credits: 2.26, 2.24, 2.21, 2.19, 2.17; max loss $2,283 per condor (worst ladder credit)
     Alpaca requirement: margin $2,500; cost basis $2,283; the bot reserves $2,500 of options BP
     expected SPY ex-dividend date inside the condor's life: 2026-12-18 (guard ON)
     sizing: 1x -> max loss $2,283 = 15.2% of equity (cap 20%); shared budget after $2,283 of $7,500
DRY RUN: nothing sent. 0 dip exits / 0 dip entries; condor sleeve: 0 exits, 1 entry.
```

The live bot made the same decision at 15:55:02 from the main checkout. It submitted the same condor with
slightly different quotes (mid 2.285, natural 2.13, ladder 2.28 → 2.20), which did not fill (section 10).

## 8. Residual risks

1. **A dead host on expiry day with no resting order.** If the 15:55 exit on expiry − 1 and the 09:45 sweep are
   both missed, Alpaca's expiry handling decides (exercise, sell-out, assignment). `REST_UNFILLED_EXPIRY_EXITS`
   covers the "exit did not fill" case, but not "the run never happened". Only a second host (the free-VM kit in
   `deploy/`) covers that.
2. **Resting orders are untested on paper.** Holding of position quantity, reserved buying power for GTC closes,
   and acceptance of a GTC submit in the last seconds of the session are not documented. Both flags are off until
   the checklist below passes.
3. **The ex-dividend rule is a rule.** SPY announces only days ahead. The quarterly third-Friday rule matches every
   date since 2024-12 and holidays are handled from the calendar, but an off-cycle date would be missed. The
   dividend estimate is the larger of Alpaca's latest rate and $2.00.
4. **The leg left after an assignment.** After an early assignment the surviving long leg is an unpaired leg and is
   never closed automatically. If it finishes ITM, Alpaca exercises it (or sells it out) and may create a stock
   position, which the next run flattens. Close it by hand or with `--close-all`.
5. **The daily-run lock plus the once-per-day guard.** A daily run that dies after `mark_ran` needs a manual
   `--force` rerun the same day. The inflight journal makes that rerun book correctly.
6. **Dry runs from cron still write.** Plain dry runs (no `--execute`, no `--dry-run`) still write
   `options_live_runs` and an `ok` heartbeat, as documented for the first-days dry runs. Use `--dry-run` for ad hoc
   runs.
7. **Migration numbering.** `010_options_exec_quality.sql` is new. If another branch also adds a 009, rename one
   before applying.
8. **PDT.** On a $15k account four day trades in five sessions trigger Alpaca's PDT protection. The bot never
   opens and closes a spread on the same day, and resting take-profits start the session after entry.

## 9. Paper-test checklist for next week

Run each from the main checkout after merging. Record order ids and screenshots in the Night/Day report.

1. `python3 -m pytest -q live/tests`: 156 pass.
2. `live/run.py --check`. Expect alerts on with "ntfy push on (topic found)", inflight "no unfinished run",
   resting orders 0, and the next SPY ex-date 2026-12-18.
3. `live/run.py --now --preview --dry-run` during market hours. Nothing appears in Supabase (`options_live_runs`,
   `bots.last_heartbeat` unchanged) and no ntfy.
4. Apply `010_options_exec_quality.sql`. After the next fill, `options_live_orders` has `mid_price`, `natural_price`
   and `slippage`. Compare `slippage` to the Alpaca fill.
5. Heartbeat: after a daily run with a warning, the 16:15 sync leaves `bots.run_state` at `warning` or `error`
   (not `ok`), and `last_sync_status = ok`.
6. Alerts: on a day with no position, put `NTFY_TOPIC` in the process env of a manual intraday run that fails on
   purpose (for example `OPT_APCA_BASE_URL=https://example.com`, refused). Expect one `refused` alert row and one
   push. Rerun normally and the row gets `resolved_at`.
7. Inflight recovery, with the 15:45 daily run on a day it will enter:
   * Kill the process (`kill -9`) at about 15:56, while the entry is working.
   * After 16:00, check `live/state/inflight.json` exists.
   * The next morning's sweep logs "recovered the daily run…". `positions.json` has the spread with `entry_mid`
     and `signal`, and is not adopted.
8. mleg atomicity: on the first condor entry, check in the Alpaca UI that all four legs filled together at one
   net price, shown as a credit. If a partial fill ever shows, check it is in whole condors.
9. Resting take-profit (`STAGE_TP_ORDERS = True` in a copy, one dip spread held):
   * After the next run, the Alpaca UI shows a GTC mleg limit close at `entry_mid × 0.5` with client id `options-rest-tp-…`.
   * Note whether `options_buying_power` moves.
   * At the next run it is canceled first, and re-staged at the end.
   * With the TP working, try a manual second close of the same spread in the UI. Does Alpaca reject it for
     insufficient qty? (This documents the quantity-holding behaviour.)
10. Resting expiry exit (`REST_UNFILLED_EXPIRY_EXITS = True`): on an expiry − 1 day, if the exit does not fill,
    check that a GTC order exists after 16:00 and is canceled by the 09:45 sweep before it trades.
11. Ex-dividend guard: on 2026-12-17 with the condor held, the daily log shows "ex-dividend check SPY: dates
    2026-12-18" and the condor row's `exdiv` note. If the 814 call is ITM, the condor closes with reason
    `exdiv_exit` (it closes that day anyway as `expiry_exit`). The intraday runs from 15:00 show the check too.
12. Entry ladder: with `CONDOR_ENTRY_LADDER = "natural"`, the next flat-sleeve daily run logs a ladder ending
    at the natural (or at the backtest credit). The order fills on paper at that last price at the latest; the
    journal row shows `slippage` ≈ mid − natural.
13. Final-sweep log level: on a day whose entry does not fill, `grep ERROR live/logs/<date>.log` is empty, and the
    health probe shows the options daily run as OK.
14. Close-all: with a resting order working, `--close-all --execute` cancels it (log: resting order canceled /
    booked) before it works the closes.

## 10. Follow-ups from the 2026-10-23 live run

### 10.1 The first condor entry did not fill

Live log, 2026-10-23:

* At 15:55:02 the bot submitted 1x SPY 2026-12-18 IC 650/675/814/835 at a $2.28 credit (mid 2.285, natural 2.13).
* The ladder 2.28, 2.26, 2.24, 2.22, 2.20 ran for 5 × 30 s until 15:57:33 with no fill.
* The final sweep then canceled the order.

Why: Alpaca paper "orders are filled only when they become marketable" against the NBBO. Paper does not model
price improvement, latency slippage or queue position for resting limits
([Paper Trading](https://docs.alpaca.markets/docs/paper-trading)). A credit mleg order is marketable only
when its limit is at or below the combo's natural credit (sell bids − buy asks). The "halfway" ladder
(`ENTRY_MAX_CONCESSION = 0.5`) stops halfway from mid to natural, at 2.20 here against 2.13. So on paper it fills
only if the market moves through it in those 2.5 minutes. A real exchange often fills SPY combos inside the
spread; paper never does. The NBBO is OPRA, while the bot sees the `indicative` feed, so the natural the bot
computes can be off by a cent or two either way.

What the backtest assumed: `optionsbot.engine` (default `Costs`, which F1's frozen numbers use) fills each leg at
its close mark ∓ slip, with slip = min($0.25, max($0.03, 2% × price)) per leg. For this condor at the 15:55
quotes, the slips are 0.077 + 0.056 + 0.033 + 0.030 = 0.196. The **backtest-assumed credit is ≈ $2.09**, below the
$2.13 natural. Filling at the natural is still $0.04 better than the research assumed. Under the calibrated cost
model (SPY half-spread × 1.25) it would be ≈ $2.22; F1 Sharpe is 1.08 at default costs and 1.16 calibrated.

Fix (flags, default off, tests in `live/tests/test_entry_fill.py`):

* `CONDOR_ENTRY_LADDER = "natural"` (and `DIP_ENTRY_LADDER`): walk evenly from the mid to target = max(natural,
  backtest-assumed credit, credit floor). The target is rounded up to the tick and is never undercut. Today that
  gives 2.28, 2.24, 2.20, 2.16, 2.13: the last price is marketable on paper, and the bot never accepts less than the
  research assumed. If the natural is worse than the backtest credit, the ladder stops at the backtest credit and
  may not fill. Sizing uses the worst ladder credit: max loss $2,287, still 1 condor.
* `CONDOR_ENTRY_EARLY = True`: a first attempt from the 15:45 cron start, 90 s per price, ending 1 minute before
  the decision time. If it does not fill, the 15:55 decision tries again. The condor has no close-dependent
  signal, so starting 10 minutes early costs nothing in fidelity.
* Retry the next day: already the behaviour. A flat condor sleeve plans an entry at every daily run.

Recommendation: turn on `CONDOR_ENTRY_LADDER = "natural"` for the paper run. Without it, paper fills only by luck.
`CONDOR_ENTRY_EARLY` is optional. `DIP_ENTRY_LADDER = "natural"` also helps the dip sleeve fill on paper, but it
gives up up to half the mid-to-natural spread more than today's ladder. Decide after seeing the slippage
columns.

### 10.2 The health probe marked the daily run FAILED

`health/check.py` counts any ERROR line inside a run's time span as a failure. The final sweep logged
`ERROR final sweep: open … still open; canceling` for the unfilled condor entry. The ladder had already canceled
it and confirmed the cancel, but Alpaca's open-orders list still showed it for a moment. Fixed in
`execution.final_sweep`:

* An order the job already saw final is re-read. If it is final, the sweep logs INFO
  ("stale open-orders list").
* A genuinely open leftover is canceled at WARNING.
* Only a cancel that will not confirm is an ERROR (`_cancel_and_settle`'s "not confirmed canceled").

The rest of today's run logged at INFO, so with this change the probe would have counted the run as OK.
