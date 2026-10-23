# Moving the bots to a free cloud VM

On 2026-10-22 the Mac slept from 15:01 to 16:15 ET and missed the 15:40 cover job. This kit moves both bots (the Stock Shorting Bot and the Options Bot), the events sync, and the `health/` probe onto a small always-on Linux VM. Each job runs at the same Eastern time as before, under systemd instead of cron.

Nothing here changes the bots. The kit copies their code, runs the same commands the Mac crontab runs, and takes the `.env` files from the Mac at deploy time. No secret is stored in this folder.

> **Round 3 (2026-10-23): the target is now a DigitalOcean $6 droplet** (x86_64, 1 GB, Ubuntu 24.04, admin `root`, NYC). Oracle A1 had no capacity and the E2.1.Micro got no public IPv4. **Start with [QUICKSTART_DIGITALOCEAN.md](QUICKSTART_DIGITALOCEAN.md)**: the owner's clicks, one command, then Claude's runbook, shadow check and rollback. Hetzner CPX11 and Oracle work the same way (`VM_ADMIN=root` or `ubuntu`). The Oracle/GCP notes below still apply to those providers. What changed this round is in [ROUND3_NOTES.md](ROUND3_NOTES.md).

**Contents**

1. [Which free VM](#1-which-free-vm)
2. [What is in this folder](#2-what-is-in-this-folder)
3. [Step by step](#3-step-by-step) (create the VM, bootstrap, code, secrets, dry runs, shadow, cutover)
4. [The schedule](#4-the-schedule)
5. [Health checks and alerts](#5-health-checks-and-alerts)
6. [State migration](#6-state-migration)
7. [Rollback](#7-rollback)
8. [Fallback and catch-up jobs](#8-fallback-and-catch-up-jobs)
9. [Day to day](#9-day-to-day)
10. [Gotchas](#10-gotchas)

---

## 1. Which free VM

**Recommended: Oracle Cloud Always Free, Ampere A1 (Arm), Ubuntu 24.04, home region US East (Ashburn, `us-ashburn-1`). Upgrade the account to Pay As You Go.**
**Fallback: Google Cloud `e2-micro`, Ubuntu 24.04, `us-east1` (South Carolina).**

| | Oracle A1 Always Free | Google e2-micro Always Free | AWS (new accounts since July 2025) |
|---|---|---|---|
| CPU / RAM | Up to **2 OCPU / 12 GB** Arm. Oracle cut this from 4 / 24 on 2026-06-15: the pool is now 1,500 OCPU-h and 9,000 GB-h a month. A 1 OCPU / 6 GB shape is plenty. The AMD `E2.1.Micro` (1/8 OCPU, 1 GB, up to 2 of them) is also free. | 2 shared vCPU, **1 GB** (bootstrap adds 2 GB of swap) | t3/t4g small sizes, paid from credits |
| Disk | 200 GB of block storage in total (a boot volume is about 47–50 GB) | 30 GB standard persistent disk | from credits |
| Network | 10 TB/month out | **1 GB/month out** from North America (China and Australia excluded) | from credits |
| How long it is free | Indefinitely | Indefinitely (in us-west1, us-central1 or us-east1 only) | **$100 + up to $100 in credits; the Free plan closes after 6 months** or when the credits run out |
| Main risk | Idle reclamation (below), capacity errors on A1, quiet cuts to the free tier | Only 1 GB of RAM. A public IPv4 address may be billed (see Gotchas). | It is not free after 6 months |

Why Oracle A1 comes first:
- **RAM.** At 15:40–15:55 ET up to four Python processes overlap: the shorting close job (pandas, yfinance and a scan thread), the options daily run, `monitor.py`, and the health probe. That fits easily in 6–12 GB. On a 1 GB `e2-micro` it swaps, and a slow cover is what we are trying to avoid.
- **Network.** 10 TB a month out, against Google's 1 GB. The bots send little data out, but 1 GB is still a ceiling you could hit.
- **The catch is idle reclamation.** Oracle may reclaim an Always Free instance that is idle for 7 days: CPU at the 95th percentile under 20%, network under 20% and (A1 only) memory under 20%. These bots are idle almost all the time, so they **will** look idle. Oracle applies this to free-tier accounts only. **Upgrading to Pay As You Go removes it**, and Always Free resources stay $0. Do the upgrade, set a $1 budget alert, and keep to Always Free shapes. If you don't want a card on file, use Google instead.

Sources, checked 2026-10-22. Re-check them before you sign up, because free tiers change without notice.
- Oracle: [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [InfoQ on the A1 cut](https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)
- Google: [free features](https://docs.cloud.google.com/free/docs/free-cloud-features)
- AWS: [free tier](https://aws.amazon.com/free), [July 2025 change](https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/)

### Sign-up: Oracle (recommended)
1. Go to oracle.com/cloud/free and click **Start for free**. A card is needed to verify your identity; nothing is charged.
2. **Home region: US East (Ashburn).** You cannot change it later, and Always Free compute only runs in the home region.
3. After the account is active, go to **Billing → Upgrade and Manage Payment → Pay As You Go**. This removes idle reclamation. Then go to **Billing → Budgets** and add a $1 budget with an email alert.
4. Go to **Compute → Instances → Create instance**:
   - Image: **Canonical Ubuntu 24.04**.
   - Shape: **Ampere → VM.Standard.A1.Flex**, 1 OCPU and 6 GB (or 2 and 12). The "Always Free eligible" tag must show.
   - Networking: the default new VCN with a public subnet, and **Assign a public IPv4 address** ticked.
   - SSH keys: **Paste public key**. Make the key first on the Mac: `ssh-keygen -t ed25519 -f ~/.ssh/clive_vm -C clive-vm`, then paste the contents of `~/.ssh/clive_vm.pub`.
   - Boot volume: the default (about 47 GB).
5. If you get "Out of host capacity", try another availability domain (AD-1, AD-2 or AD-3), retry later, or use `VM.Standard.E2.1.Micro` for now. On the AMD micro, the bootstrap adds swap.
6. The default security list allows only inbound SSH (22). Leave it that way. The login user is `ubuntu`.

### Sign-up: Google (fallback)
1. Go to console.cloud.google.com and create a billing account (the $300 trial is fine) and a project.
2. Go to **Compute Engine → VM instances → Create**:
   - Region **us-east1**, machine **e2-micro**.
   - Boot disk: **Ubuntu 24.04 LTS**, **Standard persistent disk**, 30 GB. "Balanced" is not free.
   - Firewall: leave HTTP and HTTPS unticked.
3. Go to **Metadata → SSH keys** and add the contents of `~/.ssh/clive_vm.pub`. The login user is the name at the end of the key (`clive-vm`) or your Google username. Put that name in `VM_ADMIN`.
4. Go to **Billing → Budgets & alerts** and add a $1 budget.

---

## 2. What is in this folder

| File | Where it runs | What it does |
|---|---|---|
| `QUICKSTART_DIGITALOCEAN.md` | you | The short path: clicks, one command, Claude's runbook, rollback. |
| `shadow_compare.sh` / `shadow_compare.py` | Mac | After a shadow day: pulls the VM's logs (never `.env`) and compares them with the Mac's real run: picks, 09:31 plan, 15:55 cover and sleeve, options decision, job exits. PASS/FAIL (exit 0/1), `--json`. |
| `test/docker_test.sh` | Mac (Docker) | Runs the kit in a throwaway Ubuntu 24.04 systemd container: bootstrap, `--app`, `--verify`, timers, modes, OnFailure, SSH. `test/x86_wheels.sh` proves the x86_64 Python stack from an Apple-silicon Mac. |
| `bootstrap.sh` | VM (root) | Idempotent setup. Admin `root` (DigitalOcean/Hetzner) or `ubuntu` (Oracle); `--verify` prints PASS/FAIL per item. Also sets up fail2ban (sshd). The SSH drop-in is `00-clive.conf`, so it wins over cloud-init's `50-` file. Creates the `clive` user, keeps the clock in UTC, sets up chrony, unattended-upgrades (reboots at 06:30 UTC only), an SSH-only firewall and key-only SSH, persistent journald, swap when RAM is under 2 GB, and uv + Python 3.13 in `/opt/clive/venv`. With `--app` it installs the requirements, the systemd units and `clive-mode`. |
| `sync_code.sh` | Mac | rsyncs the code to `/opt/clive/app` and then runs `bootstrap.sh --app`. Never copies `.env`, `logs/`, `state/`, `.cache/`, `research/`, `tuning/` or `node_modules`. |
| `secrets.sh` | Mac | scps the three `.env` files to the VM with chmod 600 and owner `clive`. It shows key names only. `--prompt` lets you type values in; `--deploy-env` sets `HEARTBEAT_URL`. |
| `migrate_state.sh` | Mac | `push` / `pull` / `diff` of the bots' state files. Refuses to run while the other side is still active. |
| `mac_cron.sh` | Mac | `status` / `disable` / `enable` for the bot lines in the Mac crontab. Saves a backup first. |
| `dryrun.sh` | VM | The pre-cutover checklist (section 3, step 6). |
| `healthcheck.sh` | VM | The 16:30 ET run check, the alert sent when a job fails, and the hourly VM-alive heartbeat. |
| `bin/run-job.sh` | VM | The single entry point for every job. It maps a job name to the exact crontab command, applies the mode, the timeout and the logging. |
| `bin/clive-mode` | VM | `sudo clive-mode off\|shadow\|live`. |
| `systemd/` | VM | `clive-job@.service`, the timers, and the alert, health and alive units. |
| `requirements-vm.txt`, `constraints-vm.txt` | VM | Explicit pandas, numpy and tzdata, pinned to the Mac's versions (alpaca-py 0.21.0, yfinance 0.2.65, pandas 2.3.2, numpy 2.2.6, requests 2.31.0, …). |
| `vm.conf.example` | Mac | Copy it to `vm.conf` (git-ignored) and set `VM_HOST`. |

### The three modes
The VM always has a mode, stored in `/etc/clive/mode`. Every job checks it before it starts.

| Mode | What jobs do |
|---|---|
| `off` | Exit at once. This is the mode after install and after a rollback. |
| `shadow` | Dry runs on the real schedule while **the Mac still trades**. `--execute` is dropped, and Supabase and ntfy are blanked, so nothing reaches Alpaca orders, the dashboard or your phone. `sync.py`, `live/sync.py` and `monitor.py` are skipped: they only write, and `monitor.py` can flatten if the daily-loss halt is on. `health/check.py` still runs, because its rows carry the VM's hostname, but its ntfy pushes are blanked. |
| `live` | The exact Mac commands, with `--execute`. `clive-mode` refuses to switch to `live` until `migrate_state.sh push` has run. |

The Mac and the VM never trade at the same time. The Mac trades only while its crontab lines are on, and the VM trades only in `live`. `live` needs the state push, and the push refuses while any Mac bot line is still active.

---

## 3. Step by step

Times are ET. Do steps 1–7 on any day; the Mac keeps trading throughout. Do step 8 in an **evening** (after 16:20 ET) or at a weekend.

### Step 1. Create the VM
Follow the sign-up steps in section 1. You need the VM's **public IP** and the **login user** (`ubuntu` on Oracle).

### Step 2. Open SSH and add your key
```bash
ssh-keygen -t ed25519 -f ~/.ssh/clive_vm -C clive-vm     # skip if you made it in step 1
ssh -i ~/.ssh/clive_vm ubuntu@VM_IP 'echo ok; uname -m'   # prints ok and aarch64 or x86_64
```
If the connection times out, check that port 22 is open in the cloud firewall (Oracle: VCN → Security List; Google: VPC firewall `default-allow-ssh`).

### Step 3. Tell the kit where the VM is
```bash
cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/deploy"
cp vm.conf.example vm.conf
open -e vm.conf          # set VM_HOST, VM_ADMIN (ubuntu), SSH_KEY=~/.ssh/clive_vm
```

### Step 4. Run the bootstrap (one command)
```bash
ssh -i ~/.ssh/clive_vm ubuntu@VM_IP 'sudo bash -s' < bootstrap.sh
```
It takes about 3–5 minutes and is safe to run again. Afterwards `ssh -i ~/.ssh/clive_vm clive@VM_IP` works too, because the bootstrap copies your key to `clive`.

### Step 5. Copy the code, then the secrets
```bash
./sync_code.sh --dry-run   # look at the file list: no .env, logs/, state/ or research/
./sync_code.sh             # rsync + requirements + systemd timers (mode stays off)
./secrets.sh               # the 3 .env files: scp, chmod 600, checksums compared
./secrets.sh --deploy-env  # optional: your healthchecks.io ping URL (section 5)
```
`secrets.sh` warns if `NTFY_TOPIC` is missing from `Stock Shorting Bot/.env`. It is missing today. Without it, no alert can reach your phone. Choose a long random topic, subscribe to it in the ntfy app, add `NTFY_TOPIC=...` to that `.env` on the Mac, and run `./secrets.sh` again.

### Step 6. The dry-run checklist (on the VM)
```bash
ssh -i ~/.ssh/clive_vm clive@VM_IP
/opt/clive/app/deploy/dryrun.sh --tests
```
It runs the following and prints PASS or FAIL for each. The full output goes to `/opt/clive/dryrun-*.log`.

| # | Check | What a pass looks like |
|---|---|---|
| 1 | Python ≥ 3.12 (3.13 is installed), imports, system timezone UTC, chrony synced, both `.env` files present with mode 600 | |
| 2 | `daily.py --now` with Supabase blanked (`SUPABASE_URL=` `SUPABASE_SERVICE_KEY=` `NTFY_TOPIC=`) | 20 names ranked, the covers it would send, picks in `logs/next_open_picks.dry_run.json` |
| 3 | `daily.py --open --now`, Supabase blanked | Re-checks the dry-run picks; no orders |
| 4 | `monitor.py --now`, Supabase and ntfy blanked | |
| 5 | `live/run.py --check` | Account, options level 3, keys separate from the shorting account. It warns while `SHORTING_ACCOUNT_ID` is unset. |
| 6 | `live/run.py --now --preview`, Supabase blocked (`SUPABASE_URL=http://127.0.0.1:9`: the options loader ignores an *empty* override) | Prices spreads; no orders |
| 7 | `sync_events.py --dry-run` | Fetches and prints; writes nothing |
| 8 | Every `OnCalendar` parses with `systemd-analyze calendar` | |
| 9 | All timers are loaded and the next runs are listed | |
| 10 | `healthcheck.sh --alive --dry` and `--daily --dry` | |
| 11 | (`--tests`) both bots' offline pytest suites | |

Compare steps 2 and 6 with the same dry runs on the Mac. They should rank similar names; the data is live, so small differences are normal.

### Step 7. Shadow for 1–3 trading days
```bash
ssh -i ~/.ssh/clive_vm ubuntu@VM_IP 'sudo clive-mode shadow'
```
The VM now runs every job at its real time as a dry run while the Mac trades. After each day, look on the VM:
```bash
systemctl list-timers 'clive-*'                       # last and next run of every job
journalctl -u 'clive-job@short-close' --since today   # the 15:40 job, as a dry run
journalctl -u 'clive-job@*' -p warning --since today  # anything that complained
tail -50 "/opt/clive/app/Stock Shorting Bot/logs/cron.log"
```
The 16:30 health check runs in shadow too: its ntfy titles start with `[VM shadow]` and it writes no Supabase rows. Go on only when a full day shows every job `exit 0`: 09:31, 09:45, the monitor runs, 12:40/12:45, 15:40, 15:45 and 16:15.

### Step 8. Cut over (an evening or a weekend, never 09:20–16:25 ET)
The order matters: switch the Mac off, move the state, then switch the VM on.
```bash
cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/deploy"
./mac_cron.sh disable          # 1. comments out the 15 bot lines (backup in deploy/backups/)
./mac_cron.sh status           #    "active bot lines: 0"
./migrate_state.sh push        # 2. VM -> off, Mac state -> VM, checksums shown side by side
ssh -i ~/.ssh/clive_vm ubuntu@VM_IP 'sudo clive-mode live'   # 3. the VM trades from the next timer
./migrate_state.sh diff        #    everything "same"
```
`push` refuses if any bot line is still active in the crontab, if a bot process is running on the Mac, if it is market hours, or if the VM is already live.

Next trading morning, check that it worked:
- At 09:31: `journalctl -u clive-job@short-open -f` on the VM.
- At 09:45: the dashboard shows the run, and `bots.last_run_at` moves.
- At 16:30: no alert from the health check, or a clean "all expected runs happened" in `journalctl -u clive-healthcheck`.

Leave the Mac crontab lines commented for a week or two before you delete them. `mac_cron.sh enable` is the rollback.

---

## 4. The schedule

The system clock stays **UTC**. Each timer is written in `America/New_York`, so it follows DST without edits (the Mac's cron relied on the Mac's own clock being ET). Every service also sets `TZ=America/New_York`, so the bots' log timestamps stay Eastern as before.

Every timer has `Persistent=false`. A run missed while the VM was down is **not** replayed late, which matters: a 09:31 entry must not fire at 11:00. The bots have their own guards as well (no entry after 09:50, no order within 2 minutes of the close). `AccuracySec=1s` stops systemd's default one-minute coalescing.

| Mac cron (ET) | Command | Timer `OnCalendar=` | Timeout |
|---|---|---|---|
| `31 9 * * 1-5` | `daily.py --open --execute` | `Mon..Fri *-*-* 09:31:00 America/New_York` | 45 min |
| `*/15 9-15 * * 1-5` | `monitor.py` | `Mon..Fri *-*-* 09..15:00/15:00 America/New_York` | 30 min |
| `40 12 * * 1-5` | `daily.py --execute --half-day-only` | `Mon..Fri *-*-* 12:40:00 America/New_York` | 45 min |
| `40 15 * * 1-5` | `daily.py --execute` | `Mon..Fri *-*-* 15:40:00 America/New_York` | 45 min |
| `15 16 * * 1-5` | `sync.py` | `Mon..Fri *-*-* 16:15:00 America/New_York` | 20 min |
| `50 15 * * 1-5` | `daily.py --execute --fallback` | `Mon..Fri *-*-* 15:50:00 America/New_York` | 30 min |
| `50 12 * * 1-5` | `daily.py --execute --fallback --half-day-only` | `Mon..Fri *-*-* 12:50:00 America/New_York` | 30 min |
| `0 10,12,14 * * 1-5` | `daily.py --cover-leftovers --execute` | `Mon..Fri *-*-* 10,12,14:00:00 America/New_York` | 30 min |
| `45 9 * * 1-5` | `live/run.py --sweep --execute` | `Mon..Fri *-*-* 09:45:00 America/New_York` | 40 min |
| `*/15 10-14 * * 1-5` + `0,15,30 15 * * 1-5` | `live/run.py --intraday --execute` | `Mon..Fri *-*-* 10..14:00/15:00` + `Mon..Fri *-*-* 15:00,15,30:00` (both America/New_York) | 40 min |
| `45 12 * * 1-5` | `live/run.py --execute` | `Mon..Fri *-*-* 12:45:00 America/New_York` | 40 min |
| `45 15 * * 1-5` | `live/run.py --execute` | `Mon..Fri *-*-* 15:45:00 America/New_York` | 40 min |
| `15 16 * * 1-5` | `live/sync.py` | `Mon..Fri *-*-* 16:15:00 America/New_York` | 20 min |
| `0 7 * * *` | `events/sync_events.py` | `*-*-* 07:00:00 America/New_York` | 20 min |
| new | `health/check.py --host <hostname>` | `Mon..Fri *-*-* 09..16:00/15:00`, `07:15`, `17:00` (America/New_York) | 5 min |
| new | `healthcheck.sh --daily` | `Mon..Fri *-*-* 16:30:00 America/New_York` | 5 min |
| new | `healthcheck.sh --alive` | `*-*-* *:05:00 UTC` (hourly) | 2 min |

- The timeouts guard against hangs only. Each one is well past its job's own cutoff. systemd allows 75 minutes as a final backstop.
- A timer that fires while its job is still running does not start a second copy. On the Mac, the bots' locks did that job.
- Each job runs as `clive` and logs twice: to journald (`journalctl -u clive-job@NAME`) and, appended, to the bot's own file, as on the Mac (`logs/cron.log`, `live/logs/cron.log`, `events/logs/sync.log`, `health/logs/check.log`). The bots also keep writing `logs/shortingbot.log` and `live/logs/DATE.log` themselves. The cron logs rotate weekly (12 weeks kept). journald is capped at 300 MB.
- Holidays: the timers fire Monday to Friday, and the bots read the exchange calendar themselves, as they do today.

---

## 5. Health checks and alerts

There are three layers, so that a dead VM, a failed job and a quiet miss each get noticed.

1. **A job failed: an alert within seconds.** Every `clive-job@` unit has `OnFailure=clive-alert@%i`. A non-zero exit or a timeout sends an ntfy push (`urgent` for the 09:31, 15:40, 12:40, 09:45 and 15:45 jobs) with the last log lines, and writes an `alerts` row (`kind = vm_job_failed`, `bot_id` shorting or options).
2. **16:30 ET on trading days: `healthcheck.sh --daily`.**
   - It reads the Alpaca calendar (the shorting paper keys, read-only) and skips holidays. On half days it also expects the 12:40 and 12:45 jobs.
   - It checks the journal of each expected job for today's `exit 0`.
   - It checks the VM itself: every timer active, no failed `clive-*` units, and a count of error lines from today's jobs.
   - In `live` mode, it checks the Supabase heartbeats: `bots.last_run_at` and `last_sync_at` for `shorting`, `last_heartbeat` for `options`, and `run_state` not `error`.
   - It checks for **any short still open on the shorting account**. That is the failure from 2026-10-22.
   - Each problem becomes an `alerts` row (`vm_missed_run`, `vm_shorts_open`, `vm_timer_inactive`, …) and all of them go out in one ntfy push.
3. **The VM is down: a dead-man switch.** A VM that is off can't send an alert, so something outside it has to notice the silence. `healthcheck.sh --alive` runs hourly and pings `HEARTBEAT_URL`. Make a free check at healthchecks.io with a 1-hour period and a 1-hour grace, connect it to email or the ntfy app, and store the ping URL with `./secrets.sh --deploy-env`. You then hear within about 2 hours if the VM dies or gets reclaimed. Once a day at 08:05 ET there is also a low-priority "VM alive" ntfy with the mode, uptime, memory, disk, clock sync and the next timer.

**`health/check.py` and the dashboard's Health tab.** `health/check.py` is the read-only probe that writes `health_checks` rows to Supabase and sends its own ntfy alerts. It runs on the VM every 15 minutes from 09:00 to 16:45 ET, plus 07:15 and 17:00, Monday to Friday. Its rows are tagged with the VM's hostname, so they can be told apart from the Mac's (`--host $(hostname)` if `check.py` has that flag, and always `HEALTH_HOST` / `CLIVE_HOST` in the environment). **The dashboard's new Health tab shows these results.** `healthcheck.sh` does not repeat its checks. It only confirms that the VM really ran the probe, and adds what only the VM can see: timers, systemd failures, the journal and the dead-man ping. Until `health/` exists, `sync_code.sh` skips it and the timer exits 0.

ntfy: the topic comes from `NTFY_TOPIC` in `Stock Shorting Bot/.env`, or from an override in `/etc/clive/deploy.env`. Without a topic, alerts only reach the journal and Supabase.

---

## 6. State migration

The bots keep state on disk, and that state makes each job run once per day and carries positions across days. **It must live in exactly one place.** At cutover it moves to the VM and stops being written on the Mac, because the Mac crontab is off.

| Path | Files | Why it matters |
|---|---|---|
| `Stock Shorting Bot/logs/` | `last_execute.json`, `last_open.json` | Once-a-day guards for the 15:40 and 09:31 jobs. A missing guard after a mid-day move means a second run. |
| | `next_open_picks.json` | The picks the 15:45 scan saved for the next 09:31 entry |
| | `equity_peak.json` | Drawdown halt baseline |
| | `overnight_longs.json` | Longs bought at 15:55 and sold at 09:31 (long sleeve) |
| | `alerts_state.json`, `halt.json`, `pdt_detected.json`, `etb_history.csv`, `*.lock`, logs | Alert dedupe, halts, PDT, research history |
| `Options Bot/live/state/` | `positions.json`, `condors.json`, `trades.jsonl`, `orders.jsonl`, `last_execute.json`, `account_pin.json`, `.lock` | Open spreads and condors, once-a-day guard, account pin |
| `Options Bot/live/logs/`, `events/logs/`, `health/logs/` | logs | History only |

The exact steps (`migrate_state.sh push` does them in this order):
1. Check that the Mac crontab has no active bot line (`mac_cron.sh disable` first) and that no bot process runs on the Mac. Refuse during 09:20–16:25 ET on weekdays.
2. Set the VM to `off` and check that no `clive-job@` unit is running.
3. Back up the Mac copies to `deploy/backups/state-mac-<time>.tgz`.
4. On the VM, rename any existing folder (shadow runs created some) to `*.vm-before-cutover-<time>`, then rsync the Mac folders over.
5. Delete the `*.lock` files. They hold Mac pids; the bots clear dead-pid locks anyway.
6. Show a checksum table of the key files, Mac against VM (`migrate_state.sh diff` shows it again at any time).
7. Write `MOVED_TO_VM.txt` in the Mac's `logs/` and `live/logs/` as a reminder, and `/var/lib/clive/state-migrated` on the VM, which unlocks `clive-mode live`.

Best time: after the 16:15 sync. By then nothing is held short (v2 covers at 15:55), and `next_open_picks.json` for tomorrow is already saved and moves with the rest. With the overnight long sleeve on, longs are held overnight and `overnight_longs.json` is what the 09:31 job uses to sell them, so the push carries it too. After cutover, **don't run `--execute` on the Mac**. Dry runs there are harmless.

---

## 7. Rollback

Use this if the VM misbehaves or a provider problem appears. It takes about 2 minutes.
```bash
cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/deploy"
./migrate_state.sh pull     # VM -> off (waits for a running job), VM state -> Mac (Mac copy kept aside)
./mac_cron.sh enable        # the 15 Mac lines come back
./mac_cron.sh status
```
`pull` renames the Mac's current folders to `*.mac-before-rollback-<time>` and copies the VM's in. It removes the `state-migrated` marker, so the VM can't go `live` again without a fresh push.

- **During market hours**, if the VM is unreachable: run `./mac_cron.sh enable` right away. If a 15:55 cover is at stake, run `python3 daily.py --execute` on the Mac by hand. The guard files may be a day old, so check `logs/last_execute.json` first.
- Go back to the VM later by repeating step 8.

To switch the VM off without rolling back: `sudo clive-mode off`. Every job then exits 0 and nothing trades **anywhere** until either side is switched back on.

---

## 8. Fallback and catch-up jobs

These come from the shorting bot's README (Scheduling) and from the Mac crontab, which has all three lines. They are converted like the rest and enabled by default:
- **15:50, `daily.py --execute --fallback`.** If the 15:40 job never started (no guard and no live `close.lock`), it covers at once, runs the scan and saves the picks. Otherwise it exits 0. On the VM it covers the case where the 15:40 unit failed to start.
- **12:50, `daily.py --execute --fallback --half-day-only`.** The README's "12:50 equivalent" for half days. It does nothing on a regular day.
- **10:00, 12:00 and 14:00, `daily.py --cover-leftovers --execute`.** Covers any short not opened today. It exits at once outside market hours, while the 09:31 job runs, or while another cover holds `close.lock`.

The 16:30 check counts all three as critical. `run-job.sh` refuses, with a clear error and an alert, if an older `daily.py` without these flags is ever synced. In shadow mode they run as dry runs (`--fallback`, `--cover-leftovers` without `--execute`).

The 09:31 `--open` job also covers leftovers first (`COVER_LEFTOVERS_AT_OPEN`). On the VM that runs exactly as on the Mac.

## 9. Day to day

```bash
ssh -i ~/.ssh/clive_vm clive@VM_IP
clive-mode                                     # mode, migration marker, every timer's last and next run
systemctl list-timers 'clive-*'
journalctl -u clive-job@short-close -n 200     # one job's output
journalctl -u 'clive-job@*' --since today -p warning
sudo systemctl start clive-job@short-sync      # run one job now (it obeys the mode)
tail -f "/opt/clive/app/Stock Shorting Bot/logs/shortingbot.log"
```
- **Code changes:** edit on the Mac as usual, then `deploy/sync_code.sh`. It asks first if the VM is live and it is market hours. The VM's `logs/`, `state/` and `.env` are never touched. `CODE_VERSION` on the VM records the git sha that was synced; the bots' own `code_version` is null there, because the VM has no `.git`.
- **Secret changes:** edit the `.env` on the Mac, then `deploy/secrets.sh`.
- **Library upgrades:** change `constraints-vm.txt`, then `sync_code.sh`, then `dryrun.sh`.
- **OS updates:** unattended-upgrades installs security updates every night and reboots, if needed, at 06:30 UTC (01:30 or 02:30 ET). The apt timers are moved to 05:00–06:20 UTC so they never run during the session.

---

## 10. Gotchas

- **Oracle idle reclamation.** See section 1. Upgrade to Pay As You Go, or the instance may be stopped after 7 quiet days. The dead-man switch would tell you, but hours late.
- **Oracle capacity and quiet cuts.** A1 capacity in Ashburn is often "out of host capacity". Retry, or start on the AMD micro. Oracle halved A1 on 2026-06-15 with no announcement, so watch your tenancy's limits page.
- **Oracle firewall.** Oracle's Ubuntu images ship their own iptables rules (SSH only, plus the metadata and iSCSI rules the VM needs). `bootstrap.sh` sees them and does **not** enable ufw on top, because ufw breaks them. On Google and AWS it enables ufw with SSH only.
- **Google egress: 1 GB a month free**, from North America, excluding China and Australia. The bots mostly download, which is free, but `pip` uploads and the Alpaca/Supabase request bodies count. Check Billing → Reports after the first week.
- **Google external IPv4.** The free tier page lists the VM, the disk and egress. A public IPv4 address may be billed separately (a few dollars a month), and the bots need one or a paid Cloud NAT to reach the internet. Check Billing after 48 hours. If it is billed, that is the price of the fallback; Oracle includes the public IP for free.
- **Google: one free e2-micro per month, in total.** The allowance is a pool of hours, so a second instance uses it up twice as fast. The disk must be "Standard", not "Balanced".
- **AWS** is not a long-term free option any more: new accounts get credits, and the Free plan closes after 6 months.
- **sshd drop-in order.** sshd keeps the *first* value it reads, and cloud images ship `sshd_config.d/50-cloud-init.conf` (DigitalOcean's sets `PasswordAuthentication yes`). The round-2 `60-clive.conf` was silently overridden. The bootstrap now writes `00-clive.conf`, removes the old file and prints the effective `sshd -T` values.
- **Root as admin (DigitalOcean, Hetzner).** Root keeps key-only login (`PermitRootLogin prohibit-password`), because it is the only admin; the bots run as `clive` without sudo. On Oracle/AWS (`ubuntu`) root login is off.
- **1 GB RAM machines** (DigitalOcean $6, `e2-micro`, Oracle AMD micro): the bootstrap adds a 2 GB swapfile *before* apt and uv run, with `swappiness=10`. The trading jobs get `OOMScoreAdjust=-300`. numpy, pandas, pydantic-core, curl_cffi and friends install as wheels only (`--only-binary`), so nothing compiles. The health probe's `memory` and `swap` rows warn at < 200 MB available or > 60% swap used. Watch `free -m` around 15:45 in shadow mode. If swap use is high, move to Oracle A1.
- **Clock.** Times are ET by timezone, not by system clock. Don't change the system timezone; the bootstrap sets UTC on purpose. Chrony uses the cloud's metadata time server.
- **Alpaca from a datacenter IP.** Paper trading is fine. TradingView's scanner and Yahoo occasionally rate-limit cloud IPs. The dry runs (section 3, step 6) and shadow days are where this would show: a scan with 0 names, or yfinance errors in `cron.log`.
- **`SHORTING_ACCOUNT_ID`.** The options bot refuses to trade the shorting account. Its pin file (`account_pin.json`) moves with the state, so the first live run on the VM sees the same account.
- **The Mac still has pmset wake settings and Full Disk Access for cron.** These are harmless after cutover. Keep them until you delete the crontab lines.
