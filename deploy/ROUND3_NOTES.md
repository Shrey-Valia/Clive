# Round 3 notes (2026-10-23): ready for a DigitalOcean droplet, better monitoring, dashboard v2 fixes

This work is on branch `worktree-agent-a3851c11be3d11ec2`. Nothing has been deployed: no Netlify deploy, no Supabase migration, no crontab edit, no SSH to anything, no Alpaca orders. The live bots in the main checkout were not touched.

## Found while testing (act on these)
- **The Mac was on battery during market hours today.** At 14:58 ET it was on battery at 77%; at 15:36 ET at 67%. Idle sleep was held off only by caffeinate.
- **The Mac's power log shows it asleep 174 min in market hours today**: 09:52–10:03 and 10:58–13:42 ET, lid closed, on battery. The 15:40 close is at risk whenever the lid closes on battery.
- The new probe flags both conditions: `host.power_source` FAIL, `host.sleep_today` FAIL, and `readiness.host_awake` FAIL at 15:30. It only does this once this branch is merged.

## 1. Deploy kit: DigitalOcean / Hetzner / Oracle x86, 1 GB
- **`QUICKSTART_DIGITALOCEAN.md`** lists the owner's exact clicks, one command (`ssh-keygen … && pbcopy`), then Claude's 13-step runbook, the shadow check, the cutover and the rollback. A table covers Hetzner CPX11 and the Oracle E2.1.Micro.
- **`bootstrap.sh`**:
  - The admin can be `root` (DigitalOcean/Hetzner) or `ubuntu` (Oracle). The admin's keys are copied to `clive`, with any forced-command prefix stripped.
  - When root is the admin, root keeps key-only login. Otherwise root login is off.
  - **Bug fixed:** the SSH drop-in was `60-clive.conf`, and sshd keeps the first value it reads. Cloud-init's `50-cloud-init.conf` (`PasswordAuthentication yes` on DigitalOcean) therefore won. The drop-in is now `00-clive.conf`; the old file is removed and the effective `sshd -T` values are printed.
  - Swap is created **before** apt/uv: 2 GB, or 1 GB on a small disk, with `swappiness=10` and `vfs_cache_pressure=50`.
  - uv runs with 1 build and 2 installs at a time, `UV_NO_CONFIG`, and a cwd clive can read. That last part was a real failure: `bash -s` from `/root` gave "permission denied" on uv.toml.
  - numpy, pandas, pydantic-core, curl_cffi, msgpack, websockets, frozendict and charset-normalizer are wheels only, so nothing compiles on 1 GB.
  - fail2ban has an sshd jail with the systemd backend and the ufw ban action. ufw rate-limits SSH and allows nothing else. Oracle's iptables are still detected and left alone.
  - chrony adds Cloudflare as a second time source where the cloud has no metadata NTP (DigitalOcean, Hetzner).
  - unattended-upgrades: security only. A needed reboot happens at 06:30 UTC, even with users logged in; apt timers run at 05:00–06:20 UTC; needrestart only lists; snap refresh runs at 05:00–06:00.
  - journald is persistent, capped at 300 MB with 90-day retention. logrotate now also covers `shortingbot.log`, which grew forever, plus old dryrun logs.
  - `--verify` prints PASS/WARN/FAIL for 21 items and exits 1 on a FAIL. Container detection skips the clock, swap and ufw steps.
- **`clive-job@.service`** sets `OOMScoreAdjust=-300`, so the OOM killer picks something else before a trading job.
- **`lib.sh` / `vm.conf.example`**: `VM_ADMIN` defaults to `root`.
- **`healthcheck.sh --failed`** now logs a line in mode off instead of exiting silently.

## 2. Shadow-mode verification: `shadow_compare.sh` / `shadow_compare.py`
- After a shadow day (after 16:20 ET), `deploy/shadow_compare.sh [DAY]` pulls the VM's **log files only** (never `.env`, nothing written on the VM) and compares them with the Mac's real run.
- What it compares:
  - Every job ran and exited 0 (the run-job markers).
  - The 15:45 scan: universe overlap, picks, order and qty.
  - The 09:31 open plan.
  - The 15:55 cover and the long-sleeve buys.
  - The options 15:45 decision: kill switch, chosen legs within a strike tolerance, condor sleeve.
  - Sweep and intraday exits.
- Each row is MATCH, DIFF-tolerated, MISMATCH, MISSING, NOT-COMPARABLE or N/A. The result is PASS/FAIL with exit 0/1, plus `--json` and `--strict`. Tolerances are flags (`--min-overlap 0.8`, `--qty-tol 0.10`, `--strike-tol 2`).
- Shadow artifacts are handled: the 15:50 dry fallback interleaving with the 15:40 log, position read races while the Mac trades, the first shadow morning, holidays and half days.
- 23 offline tests use fixtures in the bots' real log formats. It was also run read-only on the real Mac logs for 10-21 and 10-23.
- **Gaps:** these would need small log lines in the bots. The bots were not changed.
  - The Mac's exit codes (cron drops them).
  - A machine-readable options decision record (`live/logs/decision-DATE.json`).
  - Tagging the fallback scan's lines.
  - The options tests writing into `live/logs`.

## 3. Health probe (`health/check.py`, 23 new tests in `health/tests/`)
- **Transient errors are not missing migrations.** This is the 2026-10-23 07:15 false alarm.
  - Supabase GETs raise a typed error, and transient errors (5xx, 429, timeouts, DNS, non-JSON proxy pages) are retried 3 times.
  - A migration counts as "not applied" only on a PostgREST "does not exist" answer (42703, 42P01, PGRST20x). Anything else gives `unknown`, with the message "could not verify … not a missing migration".
  - `supabase.reachable` and `dashboard.site` on a transient error, and `market_events` unreadable, are now warn/unknown instead of fail.
  - A failed health_checks insert no longer says "apply migration 008" unless the table really is missing.
- **Mac battery and lid** (`host.power_source`, `host.lid`), judged 09:20–16:25 ET. FAIL: battery with no sleep assertion, battery < 20%, lid closed on battery. WARN: battery with caffeinate holding, or lid closed on AC without disablesleep.
- **VM**: `host.memory` (warn below 200 MB available, fail below 100), `host.swap` (warn above 60% used, fail above 90%, warn with no swap under 2 GB), `host.vm_mode`, `host.failed_units`.
- **Pre-close readiness**: runs in the 15:30 run (12:30 on half days), or any time with `--readiness`.
  - Checks: host awake, guards already dated today, stuck locks, keys, cron lines or timers, DNS, Alpaca clock and both accounts, RAM/swap/disk.
  - A FAIL sends **one urgent ntfy per host per day**, separate from the generic push.
  - A host that does not trade (VM shadow/off, Mac after cutover) checks that another host reported in the last 35 min instead.
  - No new cron line is needed: the existing 15-minute probe covers it.
- **Event-alert auto-resolution** (`alerts.auto_resolve`): every run resolves the bots' one-off event alerts once the evidence says they are over. It lives in the health layer and is idempotent next to the bot's own resolution:
  - shorts kinds: when no short is left open;
  - sleeve kinds: when no long is outside its window;
  - one-day kinds: when a later session ran cleanly;
  - any other kind: stale after 3 sessions.
  - Deduped condition kinds owned by `monitor.py` are never touched, nor is anything younger than 10 min.
  - Every PATCH carries `resolved_at=is.null`, so an alert the shorting bot (the other agent's work) already resolved keeps its own timestamp.
- **The Mac after cutover**: with its bot cron lines parked, the Mac probe reports scheduler, state, cron entries and sleep as one `ok` row each ("bots run on the VM") instead of false FAILs.

## 4. Dashboard (`dashboard/`; `npm run build` passes, `npm test` 15/15, typecheck clean)
- The shorting page shows the real v2 next action:
  - `NEXT: OPEN 09:31 ET MON 10/26 · 5 SAVED PICKS`
  - `NO PICKS FOR THE NEXT OPEN`
  - `SCAN FAILED · …`
  - `HOLDING N SHORTS · COVER 15:55 ET` (12:55 on half days)
  - leftover-short and sleeve lines.
- **Root cause of the stale text:** v1/v2 was taken from the newest `short_runs` row, so it fell back to v1 with no or old rows. `currentFlow()` now defaults to intraday_v2.
- Best day / Worst day (and the chart labels) are hidden until there are 2 or more P&L days.
- Stale v1 wording fixed: the options banner said 15:50 (now 15:45), the glossary T−/ROTATION entries, the demo descriptions, and the home book line.
- New options card "Next decision · sleeves": 15:45 ET deciding at 15:55 (12:45/12:55 on half days), DONE once today's run is recorded, and per sleeve (dip put spread, SPY condor) the open count, P&L and last plan.
- Health tab: `readiness` and `alerts` components with labels and fix hints. Unknown components now get their own group, so future checks never vanish.
- Not deployed. Review, then deploy with Netlify.

## 5. What was tested in Docker
- **`deploy/test/docker_test.sh arm64`: 14/14 PASS.** Ubuntu 24.04 with systemd as PID 1, admin root, and cloud-init's `PasswordAuthentication yes` drop-in present. Steps:
  1. shellcheck (`-S warning`) and `bash -n` on every script.
  2. `bootstrap.sh --system` on a fresh image.
  3. A sync-like copy, then `--app` with the **real pinned pip installs**.
  4. A second bootstrap (idempotent), then `--verify` with 0 fail.
  5. `systemd-analyze verify` on every unit, and `calendar` on every OnCalendar.
  6. All 17 timers loaded, with the next short-close at **15:40 New York** and opt-daily at **15:45 New York** on a UTC system clock.
  7. run-job modes: off is a no-op, shadow skips write-only jobs, live is refused before the migration.
  8. OnFailure goes to `clive-alert@` and then `healthcheck.sh --failed`.
  9. SSH as `clive` works with root's key; sshd -T shows passwords off and root key-only.
  10. The health probe on Linux (memory, swap, vm_mode, readiness).
  11. The bots' offline suites in the VM venv: 210 + 119, and 21 + 23 for health and shadow.
- **`deploy/test/x86_wheels.sh` (amd64): PASS.** uv segfaults under qemu-user, so the full systemd run can't be emulated on this Apple-silicon Mac. Instead:
  - The exact pins resolve to **x86_64-manylinux cp313 wheels**: 69 x86_64 extension modules, 0 aarch64.
  - Under `linux/amd64` Ubuntu 24.04, the x86_64 CPython 3.13 imports alpaca-py 0.21.0, yfinance 0.2.65, pandas 2.3.2, numpy 2.2.6, curl_cffi 0.13.0 and pydantic 2.12.4.
  - It runs the suites: shorting 210, options 119, health+shadow 44, all passing.
  - pyarrow is not a dependency.
- **Not testable in a container:** ufw and fail2ban banning, swapon, chrony setting the clock, a real reboot, and the `ubuntu`-admin path (Oracle). The ubuntu path is the same code with `SUDO_USER`. The first real droplet run (`bootstrap.sh --verify`) covers these.
- Clean-up: the test containers, images and volume are removed by the scripts. The shellcheck, binfmt and ubuntu images used for testing were removed afterwards.

## Next steps
1. Shrey: Part 1 of `QUICKSTART_DIGITALOCEAN.md` (one command, the droplet form, an optional healthchecks.io check), then send Claude the IPv4.
2. Claude: Part 2, steps 1–7 (bootstrap, sync, secrets, verify, dry runs), on any day.
3. One or two shadow trading days, each followed by `./shadow_compare.sh` PASS.
4. Cutover in an evening (`mac_cron.sh disable` → `migrate_state.sh push` → `clive-mode live`).
5. Merge this branch first, so the probe changes (readiness, battery/lid, transient handling) and the dashboard fixes run on the Mac too. Then deploy the dashboard.
