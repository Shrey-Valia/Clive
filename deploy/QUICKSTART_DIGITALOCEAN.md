# Quickstart: the bots on a DigitalOcean droplet

**Target:** a DigitalOcean Basic droplet: $6/mo, 1 vCPU, 1 GB RAM, 25 GB SSD, x86_64, Ubuntu 24.04, New York region. The admin user is `root`.

Hetzner CPX11 (Ashburn) and the Oracle E2.1.Micro work the same way. See [Other providers](#other-providers).

**Who does what.** Shrey (they/them) makes one key and clicks through one form, about 10 minutes. Claude does everything after that from the Mac. The Mac keeps trading until the evening of the cutover.

---

## Part 1: Shrey

### 1. Run one command on the Mac (Terminal)
```bash
ssh-keygen -t ed25519 -f ~/.ssh/clive_vm -C clive-vm -N "" && pbcopy < ~/.ssh/clive_vm.pub && echo "public key copied"
```
This makes a login key just for the VM and copies its **public** half to the clipboard. The private half stays in `~/.ssh/clive_vm` and never leaves the Mac. The key has no passphrase, so Claude can run the deploy scripts without prompting you.

### 2. Create the droplet (cloud.digitalocean.com)
1. Sign up or log in. Add a card or PayPal under **Billing**.
2. Click the green **Create** button (top right), then **Droplets**.
3. **Choose Region:** **New York**. Datacenter **NYC3** or **NYC1**.
4. **Choose an image:** the **OS** tab, then **Ubuntu**, then version **24.04 (LTS) x64**.
5. **Choose Size:**
   - Droplet Type **Basic**.
   - CPU options **Regular** (Disk type: SSD).
   - The **$6/mo** card: 1 GB / 1 CPU, 25 GB SSD, 1,000 GB transfer.
6. **Choose Authentication Method:**
   - Click **SSH Key**, then **New SSH Key**.
   - Paste with ⌘V (the key from step 1).
   - Name it `clive-vm` and click **Add SSH Key**. Make sure it is ticked.
7. **Recommended options:**
   - Leave **Add improved metrics monitoring and alerting (free)** ticked.
   - Leave Backups off. (Weekly backups are $1.20/mo extra; nothing on the VM is hard to rebuild.)
8. **Finalize details:** Quantity **1**, Hostname **`clive-vm`**. The health tab shows this name.
9. Click **Create Droplet**. After about a minute, copy the **ipv4** address shown next to it.
10. Optional but recommended: go to **Settings → Billing → Billing alerts** and set an alert at **$10**.

### 3. Optional: the dead-man switch (healthchecks.io, free)
The VM can't tell you it is down. This service notices when the VM goes quiet.
1. Sign up at healthchecks.io and click **Add Check**.
2. Set **Period 1 hour** and **Grace 1 hour**, then click **Save**.
3. Under **Integrations**, add email (or the ntfy app).
4. Copy the ping URL (`https://hc-ping.com/…`).

### 4. Send Claude two things
- The droplet's IPv4 address.
- The healthchecks ping URL, if you made one. Claude asks for it through a hidden prompt (`secrets.sh --deploy-env`), so you can also type it there yourself.

That's all. Don't change any settings on the droplet.

---

## Part 2: Claude (from the Mac, `deploy/`)

All commands run in `"/Users/shreyvalia/Desktop/Clive-Trading Bot/deploy"`. Times are ET.

| # | When | Command | Pass looks like |
|---|---|---|---|
| 1 | any time | `cp vm.conf.example vm.conf`, set `VM_HOST=<ip>`, `VM_ADMIN=root`, `SSH_KEY=~/.ssh/clive_vm` | |
| 2 | | `ssh -i ~/.ssh/clive_vm root@IP 'uname -m; free -m; lsb_release -ds'` | `x86_64`, about 960 MB, Ubuntu 24.04 |
| 3 | | `ssh -i ~/.ssh/clive_vm root@IP 'bash -s' < bootstrap.sh` | ends with `==> done`. A 2 GB swapfile, ufw (SSH only), fail2ban, key-only SSH, chrony, uv + Python 3.13 |
| 4 | | `./sync_code.sh --dry-run` then `./sync_code.sh` | no `.env`, `logs/` or `state/` in the list; ends with 17 timers, mode `off` |
| 5 | | `./secrets.sh` then `./secrets.sh --deploy-env` | 3 × `ok`, checksums match; `/etc/clive/deploy.env` gets HEARTBEAT_URL |
| 6 | | `ssh -i ~/.ssh/clive_vm root@IP 'bash /opt/clive/app/deploy/bootstrap.sh --verify'` | `0 fail` |
| 7 | | `ssh -i ~/.ssh/clive_vm clive@IP /opt/clive/app/deploy/dryrun.sh --tests` | every step PASS (picks ranked, spreads priced, no orders) |
| 8 | before 09:20 on a trading day | `ssh -i ~/.ssh/clive_vm root@IP clive-mode shadow` | the VM runs every job as a dry run, and the Mac still trades |
| 9 | after 16:20 the same day | `./shadow_compare.sh` | **PASS**: same picks, same 09:31 plan, same 15:55 cover and sleeve, same options decision, every job exit 0 |
| 10 | | `ssh -i ~/.ssh/clive_vm clive@IP 'free -m; journalctl -u "clive-job@*" -p warning --since today'` | swap use under 60% at 15:45; no unexpected warnings |
| 11 | repeat 8–10 for a 2nd trading day | | two PASS days in a row (one day is the minimum) |
| 12 | **cutover:** an evening after 16:20, or a weekend | `./mac_cron.sh disable` → `./mac_cron.sh status` (0 active) → `./migrate_state.sh push` → `ssh … root@IP clive-mode live` → `./migrate_state.sh diff` | every key file `same`; mode `live` |
| 13 | next trading day | 09:31 `journalctl -u clive-job@short-open -f`; 15:30 the readiness run; 16:30 `journalctl -u clive-healthcheck` | dashboard runs show the VM; Health tab: `readiness.summary` OK on `clive-vm`; no 16:30 alert |

Notes:
- `shadow_compare.sh` exits 1 on any MISMATCH or MISSING. Before going live, read every **DIFF-tolerated** row. Those are expected only when live data differed between the two machines (for example the VM read positions a second after the Mac traded). The first shadow morning has no VM picks from the night before, so that one row is NOT-COMPARABLE.
- The Mac's **health** cron line stays on after cutover. It then reports "bots run on the VM", and its 15:30 readiness run checks that the VM reported recently.
- Leave the parked Mac crontab lines in place for two weeks before deleting them.

## Rollback (about 2 minutes, any time)
```bash
cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/deploy"
./migrate_state.sh pull     # VM -> off (waits for a running job), the VM's state comes back to the Mac
./mac_cron.sh enable        # the Mac's bot lines are live again
./mac_cron.sh status        # active bot lines: 15
```
- **The VM is unreachable during market hours:** run `./mac_cron.sh enable` at once. Skip `pull`, because you can't reach the VM.
  - If shorts are open before 15:55, the Mac's 15:40 job covers them, as long as `logs/last_execute.json` on the Mac isn't already dated today.
  - Otherwise run by hand: `cd "../Stock Shorting Bot" && python3 daily.py --cover-leftovers --execute`.
- **Just stop everything:** `ssh -i ~/.ssh/clive_vm root@IP clive-mode off`. Every VM job becomes a no-op. Nothing trades anywhere until one side is switched back on.
- **Destroy the droplet:** first run `migrate_state.sh pull` and `mac_cron.sh enable`. Then in DigitalOcean go to **Droplet → Destroy**. Billing stops at once; it is hourly, capped at $6/mo.

## What the droplet runs
- Seventeen systemd timers in America/New_York. The system clock stays UTC.
- One mode switch, `/etc/clive/mode` (off, shadow or live).
- The jobs run as user `clive`, which has no sudo.
- Only SSH (22, rate-limited) is open.
- A needed reboot waits for 06:30 UTC (01:30 or 02:30 ET).
- On 1 GB of RAM: a 2 GB swap at `swappiness=10`. The trading jobs get `OOMScoreAdjust=-300`. Heavy wheels are installed binary-only, so nothing compiles.
- The health probe on the VM adds memory, swap, VM mode and failed-unit rows. The 15:30 readiness check sends an urgent ntfy if anything would stop the 15:40 or 15:45 jobs.

## Other providers
| | DigitalOcean $6 | Hetzner CPX11 (Ashburn) | Oracle E2.1.Micro |
|---|---|---|---|
| Admin user | `root` | `root` | `ubuntu` (`VM_ADMIN=ubuntu`) |
| Bootstrap | `ssh root@IP 'bash -s' < bootstrap.sh` | same | `ssh ubuntu@IP 'sudo bash -s' < bootstrap.sh` |
| RAM | 1 GB (+2 GB swap) | 2 GB (+2 GB swap) | 1 GB (+2 GB swap) |
| Clicks | above | Console → Add Server → Location **Ashburn** → Image Ubuntu 24.04 → Type **Shared vCPU x86, CPX11** → SSH key (paste) → Name `clive-vm` → Create & Buy | Compute → Instances → Create → Ubuntu 24.04 → Shape `VM.Standard.E2.1.Micro` → **Assign a public IPv4 address** → paste key |
| Firewall | ufw (bootstrap) | ufw (bootstrap) | Oracle's iptables are kept (bootstrap detects them) |
| Note | | IPv4 is about €0.60/mo extra; keep it, because the bots need IPv4 (Alpaca, TradingView) | free, but "capacity limit exceeded" on the public IPv4 so far |
