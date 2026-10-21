# Engine / strategy issues found during the spreads study

None of these were fixed in the shared files (owned by another agent). The workarounds live in
`optionsbot/strategies_spreads.py` and `tuning/spreads/lib.py`.

## 1. Kill-switch loss trigger can never fire for spreads (design gap, not a crash)

`Strategy.kill_switch` (strategies.py) triggers on `pnl < -kill_loss * notional`, where notional is
the sum of `short strike * 100 * n`. For a vertical spread that threshold is above the spread's whole
structural max loss, so only the VIX/VIX3M > 1.15 and VIX > 40 legs of the switch are ever active.

Minimal repro (from the `Options Bot/` folder):

```python
from optionsbot.runner import run_one
r = run_one("pcs_kill", None, "SPY", "2024-03-01", "2026-10-20", 100_000)
eng = r["engine"]
w = min(eng.closed, key=lambda p: p.entry_cash + p.exit_cash)
s = [l for l in w.legs if l.qty < 0][0]; l_ = [l for l in w.legs if l.qty > 0][0]
print(0.08 * -s.qty * 100 * s.strike,                                # 39,888 trigger
      (s.strike - l_.strike) * 100 * -s.qty - w.entry_cash)        # 18,706 max loss
```

The worst trade (2025-03-24 to 2025-04-04, -$11.6k) was 2.1x below the trigger. Suggested fix:
for positions with long protection, use the structural max loss as the denominator, so that
`kill_loss` reads as a fraction of max loss (research/long_history_validation.md §9.3 already says
"for spreads, scale to the max loss"). Workaround in this study: the existing `sl_mult` and `breach`
stops in `SpreadBook` cover the per-position loss leg, and the kill switch is tested only as a VIX
switch.

## 2. The default slippage model is per leg with a $0.03 floor, so narrow spreads look far worse than they trade

`Costs.slip = min(cap, max(0.03, 2% * price))` is charged on every leg, at entry and again at exit.
This is not a code bug. As a calibration for SPY it is about 3x the observed half-spread (see
REPORT.md §2). The $0.03 floor on a cheap long wing dominates at narrow widths: the baseline PCS
with $5 wings at 25% risk sizing paid $153k of slippage on $100k (`stage0_costs.txt`). A real
mleg order is filled as one net price, usually within $0.01-0.03 of the net mid for SPY. Workaround:
every result is reported under both the default and a per-underlying calibrated model, and
finalists are also shown at half and double slippage.

## 3. Daily-close fills/marks are last-trade prints, not the 16:00 mid (limitation)

`Engine._mark` uses the option bar close, which is the last trade and can be minutes or hours older
than the underlying's 16:00 close. Stops and take-profits are therefore evaluated on slightly
noisy marks. In the baseline PCS, 1 of 8 SPY stop-outs had a stale leg, and all 8 coincided with
real 3-12% underlying declines (`stage0_costs.txt`). This study found no evidence that false stops
are material. Stop fills do overshoot: the mean stopped-out loss was 2.56x credit against a 2.0x rule
on SPY, and 2.66x on QQQ, because the stop is only checked at the close.

## 4. Credit from asynchronous leg prints can be impossible (reported by the signals agent, tuning/signals/BUGS.md #7)

Entry credit is `short close - long close`, and the two closes can be from trades hours apart.
Example: on 2025-04-24 a SPY call credit spread showed $1.81 credit on a $2 width, so the size was
`width - credit = $0.19` max loss per spread, which opened 159-185 contracts. Workaround:
`SpreadBook` now skips any entry whose mark credit exceeds `max_credit_frac` (default 0.60) of the
width, for both the core position and the RSI2 call-spread overlay. Every grid row in
results.csv was produced with the guard in place: stage A was restarted after this fix for an
unrelated reason, see #5. The before/after effect on the finalists is in REPORT.md.

## 5. macOS fork crash in grid workers (harness issue, not engine)

Forked multiprocessing workers segfault in `_scproxy.get_proxy_settings` (SystemConfiguration/ObjC
is not fork-safe) the first time `requests` builds a request in the child. With `Pool.imap` a
dead worker hangs the whole grid silently. Fix in `tuning/spreads/lib.py`: set `no_proxy=*` before
forking, and use a checkpointed `ProcessPoolExecutor` that rebuilds itself on `BrokenProcessPool`.
Any other agent that forks after importing `optionsbot.data` on macOS needs the same fix.
