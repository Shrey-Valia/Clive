# Review: live options bot, order lifecycle and broker semantics

Saved by the coordinating session from the reviewer's findings. Line numbers refer to commit 3ff8fd9.

## Findings, by severity

### High

**1. Cron lock collision can silently skip the daily run** (`run.py:562-567`, README cron line).
- The `*/15 10-15` intraday job fires at 15:45, the same minute as the daily `--execute` job. Both take a non-blocking flock before checking their time window.
- If the intraday job wins, the daily run exits 1 with no heartbeat. That day gets no exits, including the expiry exit, and no entries.
- Fix:
  - Check the intraday window before locking, or lock with a blocking timeout.
  - Change the hour-15 cron to `0,15,30`.
  - Send a heartbeat when the lock fails.

**2. A replace that races a fill corrupts the bot's records** (`execution.py:125-133`).
- Alpaca says a successful replace response does not guarantee the old order was replaced. `_reprice` retires the old order using a fill count from a poll up to 3 s old.
- If the old order filled in that gap:
  - **Entry:** the spread exists at the broker with no record.
  - **Exit:** the spread is marked vanished and its P&L is never booked.
- Replacing a partially filled order may also overfill.
- Fix:
  - Never replace an order that a fresh GET shows as partially filled. Cancel it, settle, then resubmit the remaining quantity.
  - After every replace, GET the old order (`filled_qty`, `replaced_by`).
  - Recompute fills from all of a job's order ids.

**3. A replace or submit timeout can leave two live orders** (`execution.py:134-142`; `TERMINAL` at line 21).
- `replaced` is in `TERMINAL`, so the bot resubmits while the unknown replacement order may still be working. The same gap exists for a submit that times out.
- Fix:
  - Follow `replaced_by` instead of treating the order as done.
  - Look up orders by `client_order_id` after any exception.
  - Run a final sweep of open orders.

**4. An exception partway through the price ladder leaves orders working and fills unrecorded** (`execution.py:142`; `run.py:402-404`).
- The resubmit inside `_reprice` has no try/except.
- Fix:
  - Wrap that call.
  - Wrap `work_orders` in try/finally so the finally block cancels the bot's orders and always runs `apply_results` and `save_positions`.

**5. The bot cannot tell whether the OPT keys belong to the shorting bot's account** (`run.py:371`, `run.py:465`, `config.py:148-163`).
- `DELETE /v2/orders` would cancel the shorting bot's orders.
- Fix:
  - Refuse to run if the OPT key equals the shorting bot's key, or if the account ids match.
  - Cancel only orders whose `client_order_id` starts with `options-`.

### Medium

**6. The exit is blocked near expiry when the long leg's bid is 0** (`strategy.py:36-37`).
- Fix: accept `bid >= 0` with `ask > 0` for exits. A missing quote must never block `expiry_exit`.

**7. An unfilled exit is retried after Alpaca's 15:30 ET expiration-day handling has started.**
- Fix:
  - Run a mandatory expiry-day morning sweep.
  - Alert when an exit is still unfilled with `tdte <= 1`.

**8. `--close-all` uses the raw quantity on a partly matched orphan leg** (`run.py:484`).
- Fix: use `unmatched_qty`.

**9. Assignment is not handled** (`run.py:306-307`, `482-492`).
- Assigned stock is sent to the option quotes endpoint.
- Fix:
  - Detect positions that are not OCC option symbols and set the heartbeat to warn or error.
  - Flatten the stock with a stock order.

**10. A failed cancel is ignored and the job is still marked finished** (`execution.py:226-234`).
- Fix: retry with backoff, then sweep open orders.

**11. Truncating `client_order_id` to 48 characters creates duplicate ids** for half-dollar strikes (`execution.py:97`, `broker.py:168`).
- Fix: allow up to 128 characters and add a uuid suffix.

**12. To check on the first fill: the mleg parent order's `filled_avg_price`** (`execution.py:82`).
- Fix: fall back to the legs' prices, and log loudly when the parent price is missing.

### Low

**13. Trading calls don't retry on 429/5xx.**
- Fix: retry idempotent calls. Retry POSTs only after looking up the `client_order_id`.

**14. `TERMINAL` includes `stopped` and `suspended`.**
- Fix: treat only filled, canceled, expired, rejected and done_for_day as final.

**15. The startup `cancel_all` is not awaited.**
- Fix: poll until no orders are open.

**16. The paper-URL check is a substring match.**
- Fix: compare hostnames exactly and require https.

## Verified correct
- **Leg construction:** sides, `position_intent`, `ratio_qty` and covered legs.
- **Limit price sign:** a credit is negative and a debit positive.
- **Partial fills:** mleg orders fill in whole spreads.
- **Cutoffs:** the cutoff, half days and the once-per-day guard behave correctly.
- **Expiry rule:** it fires at `tdte <= 1`.
- **OCC parsing:** symbols parse and spreads pair up correctly.
- **Safety refusals:** paper-only, OPT keys only, read-only data client.

## Docs
- https://docs.alpaca.markets/docs/options-level-3-trading
- https://docs.alpaca.markets/reference/postorder
- https://docs.alpaca.markets/reference/patchorderbyorderid-1
- https://docs.alpaca.markets/docs/orders-at-alpaca
- https://docs.alpaca.markets/docs/options-trading-overview
