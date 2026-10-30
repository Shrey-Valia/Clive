# Execution round 3: exchange-staged exits, open execution, alert hygiene, failure modes

Written 2026-10-23 for Shrey. Branch `worktree-agent-a3ad13ed8d8dad675`. Every new behaviour is behind a flag in `shortingbot/config.py` and is **off** by default. Three exceptions are bug fixes and are on; they are listed under [Flags](#6-flags). The test suite has 266 tests and all of them pass. Before this round it had 210.

Two incidents drove this work:

1. **2026-10-22.** The Mac slept or lost its network from 15:01 to 16:15 ET. The 15:40 job never ran, and five shorts were held overnight, which cost about $315 of open profit.
2. **2026-10-23 09:31.** Alpaca paper was slow at the open. The bot cancelled its five leftover covers, re-sent them, and raised a false `cover_failed`.

---

## 1. Facts

### 1.1 What Alpaca paper actually did on 2026-10-23 (read-only `GET /v2/orders`)

| Order (client id) | created | submitted | cancel confirmed | filled |
|---|---|---|---|---|
| `v2-leftover-…-ASST-0` (pass 1) | 09:31:02.5 | 09:31:20.7 | 09:31:52.2 | never |
| the other four pass-1 orders | 09:31:02.5–.6 | 09:31:20.7–.9 | 09:31:52.0–.2 | never |
| `v2-leftover-…-BNC-2` (pass 3) | 09:31:53.7 | 09:32:05.3 | – | 09:33:14.9 |
| `v2-leftover-…-RCEL-2` | 09:31:53.8 | 09:32:05.4 | – | 09:33:40.7 |
| `v2-leftover-…-AVX-2` | 09:31:53.7 | 09:32:05.3 | – | 09:34:17.2 |
| `v2-leftover-…-NAKA-2` | 09:31:53.7 | 09:32:05.3 | – | 09:34:25.0 (partial at 09:32:26) |
| `v2-leftover-…-ASST-2` | 09:31:53.6 | 09:32:05.3 | – | 09:34:52.6 |
| 2026-10-21 15:55 entries (for comparison) | 15:55:00.6–01.2 | 15:55:02.4–05.0 | – | 15:55:03.3–05.8 |

What the table shows:

- **Acceptance.** At the open, Alpaca took about 18 s to move an order from created to submitted (12 s on the second pass). At 15:55 it took about 2–4 s.
- **Fills.** Market orders at the open filled 1–3 minutes after they were submitted. At 15:55 they filled in 1–2 s.
- **Cancels.** A cancel took about 19 s to confirm (09:31:33 to 09:31:52). Meanwhile the bot's own re-cancels got `{"code":42210000,"message":"order pending cancel"}`.
- **What went wrong.** The bot cancelled orders that were about to fill and re-sent them. Commit `a7d6a1d` lengthened the waits. Round 3 adds a loop that never cancels a working cover (§3.2).

### 1.2 Alpaca rules this design rests on

The source labels mean: DOC = Alpaca documentation; STAFF = Alpaca staff on forum.alpaca.markets; USER = a forum user report. Main page: <https://docs.alpaca.markets/docs/orders-at-alpaca> (called O below).

| # | Fact | Source |
|---|---|---|
| F1 | "CLS orders submitted after 3:50pm but before 7:00pm ET will be rejected." Orders sent after 19:00 go to the next day's closing auction. "Any unfilled orders after the close will be cancelled." cls takes market or limit orders only, not stop or stop-limit. | DOC, O |
| F2 | "OPG orders submitted after 9:28am but before 7:00pm ET will be rejected." Unfilled ones are cancelled after the open. Market and limit only. | DOC, O |
| F3 | "Any market orders received before 9:28 will be filled at the Nasdaq Official Opening Price." Nasdaq's cutoff for the cross is 9:28, so a market order at 09:30–09:31 does not join the cross. | DOC, O |
| F4 | Paper "simulates the order filling based on the real-time quotes" (NBBO), and size is not checked against NBBO size. 10% of orders get random partial fills. | DOC, <https://docs.alpaca.markets/docs/paper-trading> |
| F5 | Paper treats cls orders "simply like market orders placed at the close"; they "will not fill at the actual end of day auction price". Paper MOC orders can partly fill and "expire before filling completely". | STAFF 2026-06-24, <https://forum.alpaca.markets/t/paper-moc-orders-partially-fill-remainder-expires-same-setup-on-live-works/19155> |
| F6 | Paper does not tell MOO and MOC orders apart from market orders; they fill at the current quote. | STAFF 2023-01-24, <https://forum.alpaca.markets/t/opening-and-closing-market-prices-in-paper-trading/11664> |
| F7 | **Live:** "MOC orders are not generally supported in live trading unless one has chosen the elite smart router order handling." The TIF table marks opg and cls with "contact the sales team". | STAFF (F5 link); DOC, O |
| F8 | **Quantity reservation.** An open order reserves position shares: "insufficient qty available for order (requested: X, available: 0)". It applies to buys against a short too: a 40-share short with an open bracket exit showed `held_for_orders: 40`. This rule is not on any official doc page. | STAFF 2025-08-27, <https://forum.alpaca.markets/t/alpaca-py-apierror-insufficient-qty-available-for-order/17712>; USER 2024-10-10, <https://forum.alpaca.markets/t/403-insufficient-qty-available-for-order-requested-40-available-0/15147> |
| F9 | **Wash trades.** Alpaca rejects (HTTP 403) a new order when an open opposite-side order could interact with it, and this "applies to your paper trading account". Market and stop orders against an opposite-side order are always rejected. Bracket, OCO and trailing-stop orders are exempt. The forum's error text is "potential wash trade detected. use complex orders". | DOC, <https://docs.alpaca.markets/us/docs/user-protection>; STAFF, <https://forum.alpaca.markets/t/apierror-potential-wash-trade-detected-use-complex-orders/13441> |
| F10 | OCO is "two orders with the same side … currently only exit order is supported". The take-profit leg must be a limit and the stop leg a stop or stop-limit. Bracket orders must be day or gtc. Trailing stops work "only with single orders". | DOC, O |
| F11 | A stop "becomes a market order" once elected, on the consolidated print. A DAY order still unfilled after the closing auction is cancelled. | DOC, O |
| F12 | `PATCH /v2/orders/{id}` can change qty, time_in_force (cls included), limit_price and stop_price. It is refused while the order is `accepted`, `pending_new`, `pending_cancel` or `pending_replace`, and a success code does not guarantee the order was replaced. | DOC, <https://docs.alpaca.markets/us/reference/patchorderbyorderid-1> |
| F13 | No equity market-order collar is documented; Alpaca's 2% collar is for crypto only. `DELETE /v2/positions/{symbol}` does not document `cancel_orders`. | DOC / support |
| F14 | **Not documented.** These are open questions for the paper test: whether a cls order can be cancelled after 15:50, the half-day cls cutoff (probably 12:50), and the OCO time_in_force. NYSE Rule 123C bars MOC cancels after 15:50 (and after 15:40 except to fix an error), so assume a resting MOC cannot be taken back late. | exchange rule, not Alpaca |

### 1.3 What this means

- **(a)** A DAY buy-stop and a cls buy cannot both rest on the same shares of a short (F8). If Alpaca did allow it, the result would be worse: both orders could fill and leave a long overnight.
- **(b)** No order class pairs a stop with a cls leg (F1, F10). An OCO-like "stop or MOC, whichever comes first" cannot be built at Alpaca.
- **(c)** The useful side of F8 is that the reservation makes double covers impossible. While a staged MOC rests, any market cover for the same shares is rejected with "insufficient qty", and so is a halt flatten or a manual close. The design relies on this and keeps its own quantities consistent with it (§2.2).
- **(d)** Paper cannot show auction prices (F5, F6). It can show that cls orders are accepted, that they rest, the reservation rule, and the cutoffs. It cannot show what a live MOC would cost (F7).

---

## 2. Design: exchange-staged exits (`STAGED_MOC_COVER`)

### 2.1 Modes

| Mode | 09:31 after the entries fill | During the day | If the host is dead from 09:35 to the close |
|---|---|---|---|
| `off` (default) | DAY buy-stop at +35% (as now) | – | stop part protected; the short is **held overnight** (the 10-22 incident) |
| `full` | a cls buy for every filled share; **no broker stop** | catch-ups top up any short whose MOC is missing or the wrong size | **covered at the close**, but no squeeze stop |
| `split` | stop on ceil(qty × `MOC_SPLIT_STOP_FRACTION`) (default 0.5), cls buy on the rest | as `full` | the MOC half is covered at the close; the stop half is held overnight unless its stop fired |
| `handoff` | stop as now | from `MOC_HANDOFF_AT` (default 14:00, so the existing 14:00 `--cover-leftovers` run does it), and always at the start of the 15:40 job: cancel the stop, wait for the confirmation, then stage a cls buy for the whole short | covered at the close if the host was alive at any moment between 14:00 and 15:49; the stop protects 09:31–14:00 |

The 10-22 outage started at 15:01. `full`, `split` (half the book) and `handoff` would each have covered it. Today's code needs the host from 15:40 to 15:58.

### 2.2 Invariants (`daily.stage_mocs`)

- **What the MOC reserves.** For each short, the open cls buys reserve exactly `|short| − (shares reserved by other open buys)`. The other buys are a stop, or a working market cover. It is one rule for every mode:
  - `full`: no stop, so the MOC covers the whole short.
  - `split`: the stop takes its part and the MOC the rest.
  - `handoff`: once the stop is cancelled, the MOC covers the whole short.
  - The MOC and the stop never reserve the same share. In split mode, if the stop fires and the host is dead, the auction buys exactly the rest. Nothing goes long (tested).
- **Orders before positions.** Open orders are read first, then positions. A fill landing between the two reads can only make the bot under-cover.
- **Working sells.** A name with an open sell (an entry still working) is skipped, because a buy against it would be a wash trade (F9). The next catch-up run stages it.
- **Deadline.** No cls order goes out after `close − MOC_STAGE_BY_MINUTES_BEFORE_CLOSE` (15:49; 12:49 on a half day).
- **Oversized MOC.** If an MOC is larger than the short now (a stop or a manual cover took shares), it is cancelled, the cancel is confirmed, and the MOC is re-staged at the right size. If that cancel fails, the bot raises a critical `moc_cover_failed`, because the auction would buy past the short.
- **Order ids and state.** Order ids are `v2-moc-DATE-TICKER-n[-S1400]`. The state file is `logs/staged_moc.json`, and the picks file gets `staged_moc`.

### 2.3 How each job treats a staged MOC

| Job | Behaviour |
|---|---|
| 09:31 `--open` | Waits up to `ENTRY_FILL_WAIT_SECONDS` for the entry fills. Then it places stops (`off`, `split`, `handoff`) and stages MOCs (`full`, `split`). An entry still working gets neither, and raises a `stop_missing` warning. If an earlier session's MOC bought past its short, the long is sold at the open (`moc_orphan_long`, critical); with the long sleeve on, the sleeve's exit sells it anyway. |
| 10:00 / 12:00 / 14:00 `--cover-leftovers` | Leftover covers keep the staged MOC (`cover_all(keep_staged=True)`). Then `repair_protection` runs: a stop for any of today's shorts that has none, MOC top-ups, and the handoff once `MOC_HANDOFF_AT` has passed. |
| optional `--stage-moc` | The same `repair_protection`, at any cron time you like, for example 14:30, 15:00 and 15:30 for redundancy. |
| 15:40 job | At the start: `repair_protection` (handoff, top-ups). At 15:48: `MOC_RELEASE` may take MOCs back (see the note below the table). At 15:55 the cover leaves staged MOCs alone and covers only the shares they do not reserve. `left` never counts staged shares, so there is no false `cover_failed`. |
| 15:50 `--fallback` | Same as the 15:55 cover: staged MOCs are kept, only the unreserved shares are covered. |
| halt flatten (monitor / 09:31) | `keep_staged=False`: cancels the MOCs as well and covers at market. After 15:50 the cancel fails, the name is skipped, and the auction covers it. |
| 16:15 `sync.py` | If a name staged today is still short, it raises a critical `moc_cover_failed` (F5: a partial fill in paper). The next 09:31 leftover cover buys it back. |

`MOC_RELEASE` choices for the 15:40 job:
- `"overlap"` (default): take back the MOCs on names that are in today's picks, but only while the long sleeve is on. Otherwise a resting cls buy-to-cover would make the 15:55 long entry skip the name as "still short". It waits for the scan until 15:48.
- `"all"`: take back every MOC and cover at market at 15:55, as before. The MOC is then pure insurance for a dead host.
- `"none"`: keep every MOC.

A cancel that does not confirm by about 15:49 leaves that name staged.

### 2.4 Turning it off mid-day is safe

If `logs/staged_moc.json` for today exists, the cover path still treats cls buys as staged (`staging_possible`). With the flag off and no file, the cover path makes exactly the same Alpaca calls as before; `tests/test_flag_off_regression.py` still passes.

### 2.5 Which mode

- **`handoff` (recommended)** keeps the squeeze stop for most of the session. It needs the host alive once in a window of almost two hours, and three cron runs (14:00, 15:40, and an optional 15:00 `--stage-moc`) get a chance. The cost is that from 14:00 to 16:00 there is no broker stop. A +35% squeeze in those two hours is covered only at the close, or by you after a `position_loss` alert.
- **`full`** makes the close fully host-independent, but drops the stop for the whole day.
- **`split`** sits between the two.
- **None of them is a substitute for moving the bot off the Mac** (`deploy/README.md`, the Oracle/GCP kit). That fixes the 09:31 entry, the sleeve and the alerts too.

### 2.6 MOO / opening auction (`opg`)

- **Sleeve long exit.** This is already built: `OVERNIGHT_LONG_EXIT_ORDER="opg"`, with a fall-back to market if the job starts after 09:28. It needs the `--open` cron line moved to 09:20. It is worth it live, where the order would fill at the official open (F3). On paper it fills at the quote (F6), so paper cannot judge it.
  - **Host independence: not built.** The exit could be staged the evening before, but an opg sent before 19:00 is rejected (F2). That would need a 19:05 job, and the Mac sleeps after 16:20.
  - **Residual.** If the host is dead the next morning, the 10:00 catch-up sells the sleeve. If it is dead all day, the sleeve is held.
- **09:31 short entry.** Not recommended, so not built:
  - (i) The entry re-checks the gap-up and the $10 price against the open, which an opg order sent by 09:28 cannot know.
  - (ii) A sleeve name is sold at the open and then shorted. Two sells at once would trip the qty reservation (F8), and the short must wait for the sell to fill.
  - (iii) Borrow availability is re-checked at the open.
  - A limit-on-open at the scan price × (1 + `MAX_GAP_UP_PCT`) could handle (i), but not (ii) or (iii).

---

## 3. Open-execution quality

### 3.1 Entry wait (bug fix, on)

The 09:31 job used to wait 30 s for entry fills before placing the stops. On 10-23 paper took 1–3 minutes to fill at the open, so most entries would have had no fill at 30 s and got **no stop**. A partly filled entry would have had its stop rejected as a wash trade (F9). Two changes:

- Entries now poll for up to `ENTRY_FILL_WAIT_SECONDS = 150` and return as soon as every entry has filled.
- A stop is placed only once its entry is terminal. An entry still working raises `stop_missing`, and the next catch-up run places the stop (`STOP_REPAIR`, on).

### 3.2 Cover loop (`COVER_KEEP_WORKING`, off)

The old loop, on every pass: cancel every order on the name, wait, re-read, re-send. On 10-23 that cancelled five market covers that were about to fill. The new loop:

- **First pass:** as before. It cancels stops, keeps staged MOCs, re-reads and sends.
- **After that:** it never cancels a working cover. It polls with `broker.wait_filled_progress`, which keeps waiting past the pass time while fills are still moving or an order is still `pending_new`/`accepted`.
- **Each poll:** it reads open orders, then positions, and sends a new cover only for shares that no working buy and no staged MOC covers (a rejected order, a 503, an expiry).
- **When it stops:** when the book is flat, at the cutoff, or after `fill_wait × (1 + COVER_EXTRA_PASSES)`.
- **Alerts:** a leftover still covered by a working order at exit is a `cover_working` warning, not a critical `cover_failed`.

### 3.3 Marketable limits with a collar (off)

- **`ENTRY_ORDER="limit_collar"`:** each short entry is a DAY limit at bid × (1 − `ENTRY_COLLAR_PCT`/100), default 1%. Whatever is unfilled after the entry wait is cancelled, the cancel is confirmed, and only the filled shares get protection. A name that ran away from the collar is not traded that day. This protects against a bad opening print on thin names, but it costs fills on fast fades. Measure it on paper before turning it on, knowing that paper fills at the quote.
- **`COVER_ORDER="limit_collar"`:** the first cover pass is a limit at ask × (1 + `COVER_COLLAR_PCT`/100), and every later pass is a market order. The keep-working loop cancels a stalled collar limit and goes to market. Default is market, because a cover must get done.

---

## 4. Event-alert hygiene (`AUTO_RESOLVE_EVENT_ALERTS`, off)

Before this, event alerts (`no_picks`, `cover_failed`, `leftover_covered`, …) never cleared. With the flag on:

- **The 16:15 sync resolves:**
  - every event alert from an earlier session (raised before midnight ET today);
  - the short-state kinds (`cover_failed`, `cover_blocked`, `close_missed`, `overnight_leftover`, `leftover_covered`, `moc_cover_failed`) once no short is open after the close;
  - the long-state kinds (`long_exit_failed`, `long_exit_late`) once every long held is part of today's sleeve entry;
  - condition alerts on names no longer held (`position_loss`, `long_loss`, `long_outside_window`, `book_loss`).
- **Each leftover check that finds nothing** (09:31, 10:00, 12:00, 14:00) resolves the short-state kinds raised before that run started. A run never resolves its own alerts, so you still see them once.
- **What it never touches:** monitor's open condition rows (their ids in `logs/alerts_state.json`). `resolve_cleared` still owns those.
- **Mechanics:** one PostgREST `PATCH alerts?bot_id=eq.shorting&resolved_at=is.null&kind=in.(…)&at=lt.…`. It writes `resolution` (why), which needs migration `009_alert_resolution.sql`; until that is pushed the bot writes without it. The flag changes nothing but the dashboard.

---

## 5. Failure-mode review

For each job, two questions: what happens if the host dies at this exact moment, and what happens if Alpaca is slow or returns a 5xx. "Fixed" means fixed in this branch.

| When | Host dies here | Alpaca slow / 5xx here |
|---|---|---|
| 09:31 before entries | Flat, nothing entered. The 10:00 catch-up covers any leftover. OK. | `session_or_fallback` retries, then assumes 09:30–16:00. OK. |
| 09:31 entries sent, before stops | Shorts open with no stop, and nothing covers them if the host stays dead. `full`/`split`/`handoff` + a later catch-up: covered at the close. **Residual:** the 1–3 min between entry and staging cannot be closed, because a buy against a working sell is a wash trade (F9). | **Fixed:** a POST that got a 5xx is now looked up by client id (`broker._send`). Before, an order Alpaca had taken was recorded as rejected and got no stop. A 15 s timeout was already looked up. |
| 09:31 slow entry fills | – | **Fixed:** the 150 s polling wait, plus `STOP_REPAIR` at 10:00/12:00/14:00. |
| 09:31 sleeve exit | Longs held. The 10:00 catch-up sells them (existing). | 3 passes, then `long_exit_failed` (existing). |
| 10:00–14:00 catch-ups | The next one runs (existing). | **Fixed (flag):** `COVER_KEEP_WORKING` stops the cancel/re-send churn and the false `cover_failed`. |
| 15:01–16:15 (the 10-22 incident) | `off`: held overnight, covered at 09:31. `handoff` (14:00), `full`, `split`: covered at the close. | – |
| 15:40–15:55 | The same as the row above. | – |
| 15:55 cover, positions unreadable | – | **Fixed:** `cover_with_retries`. Before, one exception failed the job after the guard was written "started", so the 15:50 fallback stood down and nothing covered. It now retries every 10 s until about 20 s before the cutoff. |
| 15:55 after cancelling stops, before the covers go out | `off`: shorts with no stop, held overnight. Staged modes: the MOC is never cancelled, so they are covered at the close. | the existing pass logic, plus the retry above |
| 15:55 long-sleeve entry | Partial sleeve. The 09:31 exit sells whatever exists. OK. | Limit expires (existing). |
| 15:50 fallback when the 15:40 job died between 15:49:30 and 15:50 with the guard "started" | The fallback stands down (guard), and nothing covers. **Residual** (a 30 s window). A staged MOC covers it. | – |
| 16:00 auction (staged) | – | Paper may fill part of an MOC (F5). 16:15 `moc_cover_failed`, and 09:31 covers the rest. **Residual:** the rest is held overnight. |
| 16:15 sync | Dashboard stale. Harmless. | Logged, `sync_runs` error (existing). |
| Overnight | Sleeve longs held. If the host is dead the next morning, longs are sold at 10:00. **Residual:** host dead all day. | – |

Other residual risks:

- **Live MOC availability (F7).** Before going live with any staged mode, confirm with Alpaca that cls orders are accepted on the account (elite routing). If they are rejected, the bot raises `moc_stage_failed` and keeps its stop (`split`/`handoff`). In `full` the short has no protection, so do not run `full` live without confirming.
- **Half days.** The cls cutoff is not documented (F14). The bot assumes 12:50 and stages by 12:49. Check it on Friday 2026-11-27, which is a half day.
- **No stop in `full`, or after the handoff.** A squeeze is capped only by the size cap (5% of equity, about 5% per +100%).
- **`STOP_REPAIR` (on) places orders in the 10:00/12:00/14:00 runs.** It only acts when one of today's shorts has no open order at all. If the price is already past fill + 35%, the stop triggers at once. That is the intended +35% rule, applied late.
- **The fake broker models F8 and F9 as the forum describes them.** The paper test plan (§7) confirms them on the real account before anything relies on them.

---

## 6. Flags

| Flag | Default | What it does |
|---|---|---|
| `STAGED_MOC_COVER` | `"off"` | `off` / `full` / `split` / `handoff` (§2) |
| `MOC_SPLIT_STOP_FRACTION` | 0.5 | `split`: the share of each short the stop protects |
| `MOC_HANDOFF_AT` | (14, 0) | `handoff`: the earliest ET time a run swaps the stop for the MOC |
| `MOC_STAGE_BY_MINUTES_BEFORE_CLOSE` | 11 | no new cls order after 15:49 |
| `MOC_RELEASE` | `"overlap"` | the 15:40 job takes MOCs back: `overlap` / `all` / `none` |
| `MOC_RELEASE_MINUTES_BEFORE_CLOSE` | 12 | release cancels go out by 15:48 |
| `COVER_KEEP_WORKING` | False | the never-cancel-a-working-cover loop (§3.2) |
| `COVER_STALL_SECONDS` | 20 | keep waiting while fills move within this many seconds |
| `ENTRY_ORDER` / `ENTRY_COLLAR_PCT` | `"market"` / 1.0 | marketable limit entries |
| `COVER_ORDER` / `COVER_COLLAR_PCT` | `"market"` / 1.0 | first cover pass as a marketable limit |
| `AUTO_RESOLVE_EVENT_ALERTS` | False | §4. Safe to turn on now; it is dashboard-only. |
| `ENTRY_FILL_WAIT_SECONDS` | **150 (bug fix, on)** | entries wait for their fills before stops / MOCs (was the 30 s `FILL_WAIT_SECONDS`) |
| `STOP_REPAIR` | **True (bug fix, on)** | catch-up runs give a stop to any of today's shorts that has none |
| (no flag) | **on (bug fix)** | `cover_with_retries` at 15:55; `broker._send` looks up an order after a 5xx; `place_stops` skips an entry that is still working |

New CLI: `daily.py --stage-moc [--execute]`. New alerts: `moc_stage_failed` (warning), `moc_cover_failed` (critical), `moc_orphan_long` (critical), `stop_missing` (warning), `cover_working` (warning). New migration: `dashboard/supabase/migrations/009_alert_resolution.sql`, which adds `alerts.resolution` and `short_orders.tif`. It is not applied.

---

## 7. Paper test plan for next week

Do this on the paper account with tiny size. Set `SIZE_PCT_PER_NAME = 0.5` in `config.py` for the test days (about $50 a name), and restore 5.0 afterwards. Run each day from a checkout of this branch, not the main one. Keep `logs/cron.log` and the Alpaca order history; the scratch script that read the 10-23 timings does `GET /v2/orders?status=all&nested=true`.

**Day 0 (any day, before the open): confirm the three Alpaca facts everything rests on.** Place these by hand on paper, with one share of a liquid name (SPY or AAPL).
1. Short 1 share at market. Add a DAY buy-stop for 1 share at +35%. Then submit a cls buy for 1 share. **Expect** `insufficient qty available` (F8). If it is *accepted*, stop: `full`/`split` still work, but `handoff` and the cover logic would need a rethink. Cancel everything and buy back.
2. Short 1 share and submit a cls buy for 1 share at about 10:00. **Expect** it to rest as `new`/`accepted` until 16:00 and fill at about the closing quote. Note `filled_at`.
3. After 15:50, try to cancel a resting cls buy (stage one at 15:45). **Record** whether paper allows it (F14). Also try a 1-share cls buy at 15:51 and **expect** a rejection (F1).

**Day 1 (flags off, run as live): the bug fixes.**
1. 09:31: the log shows the entries waiting (`ENTRY_FILL_WAIT_SECONDS`). Each filled short gets `STOP BUY`. Any entry still working shows `stop_missing`.
2. 10:00: `protection repair` places the missing stops. Check that every short in Alpaca has exactly one open buy-stop.
3. 15:55: the cover is the same as before; `tests/test_flag_off_regression.py` guards this.

**Day 2: `STAGED_MOC_COVER="split"`, `MOC_RELEASE="none"`, long sleeve off (`SHORTBOT_OVERNIGHT_LONG=0` on the cron line).**
1. About 09:33–09:35: in the Alpaca UI, each short has a stop for ceil(n/2) shares and a cls buy for the rest. `logs/staged_moc.json` lists them.
2. 10:00: `--cover-leftovers` changes nothing (idempotent). Check the log for no new `STAGED MOC` lines.
3. 15:40: the log shows `repair_protection (15:40 job)` with no changes. At 15:55 there is only market buys of the stop halves, and the log says "left to the closing auction (staged MOC)".
4. 16:00: the cls orders fill. At 16:15 the sync shows 0 shorts and no `moc_cover_failed`. If paper partly filled one (F5), you get `moc_cover_failed` and 09:31 the next day covers the rest. That is the test of that path.

**Day 3: `STAGED_MOC_COVER="handoff"`, sleeve on, `MOC_RELEASE="overlap"`.**
1. 09:31: stops only, as today.
2. 14:00: `--cover-leftovers` cancels the stops (confirmed) and then stages a full cls buy for each name. **Check the order:** each stop is canceled before its cls is created.
3. Optional: add a `--stage-moc --execute` cron line at 15:00 (cron line in §8). It is a no-op if 14:00 worked.
4. 15:40–15:48: if a pick overlaps a short, the log shows `MOC release: [X] back to the 15:55 cover`, and X is covered at 15:55 and then bought by the sleeve.

**Day 4: the host-dead drill.** Use `STAGED_MOC_COVER="full"`. After the 09:31 entries have MOCs, comment out the 15:40, 15:50 and 14:00 lines (or close the lid at about 13:30). **Expect** every short to be covered at the close with nothing running. Restore the cron lines.

**Any day:**
- `COVER_KEEP_WORKING=True`: watch a 10:00 leftover cover. There should be no `cancelled` lines and one order per name. A slow fill shows `cover_working`, not `cover_failed`.
- `AUTO_RESOLVE_EVENT_ALERTS=True`: after 16:15, yesterday's `no_picks` and `leftover_covered` alerts leave the dashboard. Push migration 009 first to get the `resolution` text.

**Half day, Friday 2026-11-27 (close 13:00):** with a staged mode on, the 12:40 job stages or tops up by 12:49. Check that Alpaca accepts cls orders at 12:45, and record whether one at 12:51 is rejected.

---

## 8. Cron (only if you adopt a staged mode; not changed here)

```
# optional extra handoff / top-up chances for STAGED_MOC_COVER (15:00 and 15:30 ET)
0,30 15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 daily.py --stage-moc --execute >> logs/cron.log 2>&1
```

The existing 10:00/12:00/14:00 `--cover-leftovers` and 15:40 lines already run `repair_protection`, so this line is redundancy only.
