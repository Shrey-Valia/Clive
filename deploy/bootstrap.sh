#!/usr/bin/env bash
# bootstrap.sh -- prepare an Ubuntu 22.04 / 24.04 VM (x86_64 or arm64) for the Clive bots.
# Idempotent: safe to run again at any time. Tested on Ubuntu 24.04 (x86_64 and arm64) in a
# systemd container by deploy/test/docker_test.sh.
#
# Targets: DigitalOcean droplet (admin user root), Hetzner Cloud (root), Oracle (ubuntu),
# GCP / AWS (your user / ubuntu). 1 GB RAM is enough: a 2 GB swapfile is added below 2 GB RAM.
#
# First run (from the Mac, one command):
#   DigitalOcean / Hetzner:  ssh root@VM_IP 'bash -s' < deploy/bootstrap.sh
#   Oracle / AWS / GCP:      ssh ubuntu@VM_IP 'sudo bash -s' < deploy/bootstrap.sh
#
# After every code sync, sync_code.sh runs the app half only:
#   sudo bash /opt/clive/app/deploy/bootstrap.sh --app
#
#   (no flag)  system setup; then the app half too if the code is already on the VM
#   --app      venv + requirements, systemd units, clive-mode, logrotate
#   --system   system setup only
#   --verify   change nothing; print PASS/WARN/FAIL for every item below (exit 1 on a FAIL)
#
# The system half: `clive` user (no sudo) with the admin's SSH keys, UTC system clock, chrony,
# unattended-upgrades (security only; a needed reboot waits for 06:30 UTC = 01:30/02:30 ET, never
# market hours), ufw (inbound SSH only; Oracle's own iptables kept), fail2ban (sshd, systemd
# backend), key-only SSH (root keeps key-only login when root IS the admin), persistent journald
# capped at 300 MB, a 2 GB swapfile when RAM < 2 GB, uv + Python 3.13 in /opt/clive/venv.
# It reads no secrets and writes none.
set -euo pipefail

CLIVE_USER=clive
BASE=/opt/clive
APP=$BASE/app
VENV=$BASE/venv
PYVER=3.13
ETC=/etc/clive
SSH_DROPIN=/etc/ssh/sshd_config.d/00-clive.conf   # 00-: sshd keeps the FIRST value it reads, and
                                                  # cloud images ship 50-cloud-init.conf (passwords on)

say() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { echo "run as root: sudo bash bootstrap.sh" >&2; exit 1; }

do_system=1 do_app=1 do_verify=0
case "${1:-}" in
  --app) do_system=0 ;;
  --system) do_app=0 ;;
  --verify) do_system=0 do_app=0 do_verify=1 ;;
  "") [[ -f "$APP/deploy/bin/run-job.sh" ]] || do_app=0 ;;
  *) echo "usage: bootstrap.sh [--app|--system|--verify]" >&2; exit 2 ;;
esac

# In a test container (deploy/test/docker_test.sh) the clock, swap and firewall belong to the
# host: those steps are skipped with a note instead of failing.
IN_CONTAINER=0
if systemd-detect-virt --container --quiet 2>/dev/null || [[ -f /.dockerenv ]]; then IN_CONTAINER=1; fi

# The admin whose SSH keys the clive user gets: $CLIVE_ADMIN, else whoever ran sudo, else root.
ADMIN="${CLIVE_ADMIN:-${SUDO_USER:-root}}"
ADMIN_HOME="$(getent passwd "$ADMIN" | cut -d: -f6)"; ADMIN_HOME="${ADMIN_HOME:-/root}"
ADMIN_KEYS="$ADMIN_HOME/.ssh/authorized_keys"

# ============================================================================ system
system_setup() {
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
    warn "tested on Ubuntu 22.04/24.04 only; this is ${PRETTY_NAME:-unknown}. Continuing."
  fi
  ARCH="$(uname -m)"
  mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  say "Ubuntu ${VERSION_ID:-?} on $ARCH, $((mem_kb / 1024)) MB RAM, admin $ADMIN$([[ $IN_CONTAINER -eq 1 ]] && echo ', in a container')"

  # Swap FIRST: on a 1 GB droplet apt + uv installs can need more than the free RAM.
  say "swap (only when RAM < 2 GB)"
  if [[ "$mem_kb" -lt 2000000 ]]; then
    if [[ $IN_CONTAINER -eq 1 ]]; then
      echo "container: swap is the host's; skipped"
    else
      if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
        free_mb="$(df -Pm / | awk 'NR==2 {print $4}')"
        size=2048; [[ "$free_mb" -lt 6000 ]] && size=1024
        [[ -f /swapfile ]] || fallocate -l "${size}M" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count="$size" status=none
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
        swapon /swapfile
      fi
      grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
      # swappiness 10: keep the bots in RAM, swap only under pressure. vfs_cache_pressure 50
      # keeps the venv's file metadata cached between 15-minute jobs.
      printf 'vm.swappiness=10\nvm.vfs_cache_pressure=50\n' > /etc/sysctl.d/60-clive-swap.conf
      sysctl -q -p /etc/sysctl.d/60-clive-swap.conf || warn "sysctl failed"
      echo "RAM $((mem_kb / 1024)) MB: $(swapon --show=SIZE --noheadings | head -n1) /swapfile on, swappiness 10"
    fi
  else
    echo "RAM $((mem_kb / 1024)) MB: no swap needed"
  fi

  say "packages"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
  apt-get update -q
  apt-get install -y -q --no-install-recommends \
    ca-certificates curl jq rsync git tzdata chrony unattended-upgrades apt-listchanges \
    ufw fail2ban python3-systemd logrotate util-linux procps sudo openssh-server less

  say "clock: system stays UTC, jobs are scheduled in America/New_York by the timers"
  if [[ $IN_CONTAINER -eq 1 ]]; then
    ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime; echo Etc/UTC > /etc/timezone
  else
    timedatectl set-timezone UTC
  fi
  # Cloud metadata time servers where the cloud has one; DigitalOcean and Hetzner have none,
  # so they use Ubuntu's pool (ntp.ubuntu.com + pool.ntp.org) plus Cloudflare as a 2nd operator.
  vendor="$(cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/chassis_asset_tag 2>/dev/null | tr '\n' ' ' || true)"
  src=""
  case "$vendor" in
    *Google*) src="server metadata.google.internal prefer iburst" ;;
    *OracleCloud*|*Oracle*) src="server 169.254.169.254 prefer iburst" ;;
    *Amazon*) src="server 169.254.169.123 prefer iburst" ;;
    *DigitalOcean*|*Hetzner*|*) src="server time.cloudflare.com iburst" ;;
  esac
  mkdir -p /etc/chrony/sources.d
  echo "$src" > /etc/chrony/sources.d/clive-cloud.sources
  # Ubuntu 22.04's chrony.conf may lack the sourcedir line; add it once.
  grep -q '^sourcedir /etc/chrony/sources.d' /etc/chrony/chrony.conf || echo 'sourcedir /etc/chrony/sources.d' >> /etc/chrony/chrony.conf
  systemctl enable chrony >/dev/null 2>&1 || true
  if [[ $IN_CONTAINER -eq 1 ]]; then
    echo "container: chrony configured, not started (the host owns the clock)"
  else
    systemctl restart chrony 2>/dev/null || systemctl restart chronyd || warn "chrony did not start"
    timedatectl set-ntp true 2>/dev/null || true
  fi

  say "user $CLIVE_USER"
  if ! id "$CLIVE_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$CLIVE_USER"
  fi
  usermod -aG systemd-journal "$CLIVE_USER"    # healthcheck.sh reads the jobs' journal
  # Let the Mac rsync/scp as clive with the same key you used for the admin user (root on
  # DigitalOcean/Hetzner, ubuntu on Oracle).
  ch=/home/$CLIVE_USER/.ssh
  install -d -m 700 -o "$CLIVE_USER" -g "$CLIVE_USER" "$ch"
  if [[ -s "$ADMIN_KEYS" ]]; then
    touch "$ch/authorized_keys"
    while IFS= read -r k; do
      [[ -z "$k" || "$k" == \#* ]] && continue
      # cloud images put a forced 'Please login as ubuntu' command on root's keys: copy the bare key
      k="$(sed -E 's/^.*(ssh-(ed25519|rsa|dss)|ecdsa-sha2-[a-z0-9]+|sk-[a-z0-9@.-]+) /\1 /' <<< "$k")"
      grep -qxF "$k" "$ch/authorized_keys" || echo "$k" >> "$ch/authorized_keys"
    done < "$ADMIN_KEYS"
    chown "$CLIVE_USER:$CLIVE_USER" "$ch/authorized_keys"; chmod 600 "$ch/authorized_keys"
    echo "copied $(grep -c . "$ch/authorized_keys") key(s) from $ADMIN to $CLIVE_USER"
  else
    warn "no keys in $ADMIN_KEYS; add yours to $ch/authorized_keys (or set CLIVE_ADMIN=user)"
  fi

  say "folders"
  install -d -m 755 -o "$CLIVE_USER" -g "$CLIVE_USER" "$BASE" "$APP" "$BASE/python"
  install -d -m 750 -o root -g "$CLIVE_USER" "$ETC"
  install -d -m 755 -o root -g root /var/lib/clive
  [[ -f $ETC/mode ]] || { echo off > $ETC/mode; chmod 644 $ETC/mode; }
  if [[ ! -f $ETC/deploy.env ]]; then
    cat > $ETC/deploy.env <<'EOF'
# Deployment settings for healthcheck.sh (not the bots). secrets.sh fills these in.
# Dead-man switch: a healthchecks.io (free) ping URL. It alerts YOU when the hourly ping stops,
# i.e. when the VM itself is down - something the VM can never report on its own.
HEARTBEAT_URL=
# Optional: override the ntfy topic from "Stock Shorting Bot/.env" for VM alerts.
NTFY_TOPIC=
EOF
    chown root:$CLIVE_USER $ETC/deploy.env; chmod 640 $ETC/deploy.env
  fi

  say "unattended-upgrades (security only, a needed reboot waits for 06:30 UTC, never in market hours)"
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
  cat > /etc/apt/apt.conf.d/52clive-unattended <<'EOF'
// Clive: security updates nightly. A reboot, when one is needed, happens at 06:30 UTC
// (01:30 EST / 02:30 EDT) - after the 16:15 sync, before the 07:00 events job, never in market
// hours. If an upgrade finishes after 06:30, shutdown waits for 06:30 the NEXT day.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "06:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
EOF
  # apt's own timers fire at random times by default; keep them at 05:00-06:20 UTC.
  for t in apt-daily apt-daily-upgrade; do
    mkdir -p /etc/systemd/system/$t.timer.d
    when="05:00"; [[ $t == apt-daily-upgrade ]] && when="06:00"
    printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* %s:00 UTC\nRandomizedDelaySec=20m\nPersistent=false\n' "$when" > /etc/systemd/system/$t.timer.d/clive.conf
  done
  # needrestart (24.04) must not restart services interactively or mid-job: list only.
  if [[ -d /etc/needrestart/conf.d ]]; then
    echo "\$nrconf{restart} = 'l';" > /etc/needrestart/conf.d/50-clive.conf
  fi
  # DigitalOcean's droplet-agent and Ubuntu's motd-news/ua timers are harmless; snapd refreshes are
  # held to the night window too when snapd is present.
  if command -v snap >/dev/null 2>&1; then snap set system refresh.timer=05:00-06:00 2>/dev/null || true; fi
  systemctl daemon-reload
  systemctl enable unattended-upgrades >/dev/null 2>&1 || true

  say "journald: persistent, capped at 300 MB"
  mkdir -p /var/log/journal /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=persistent\nSystemMaxUse=300M\nMaxRetentionSec=90day\n' > /etc/systemd/journald.conf.d/60-clive.conf
  systemctl restart systemd-journald || true

  say "SSH: keys only$([[ $ADMIN == root ]] && echo ', root keeps key-only login (it is the admin)' || echo ', no root login')"
  ssh_port="$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}')"; ssh_port="${ssh_port:-22}"
  rm -f /etc/ssh/sshd_config.d/60-clive.conf          # the round-2 name, which cloud-init's 50- beat
  if [[ -s "$ADMIN_KEYS" ]]; then
    mkdir -p /etc/ssh/sshd_config.d /run/sshd
    root_login=no; [[ "$ADMIN" == root ]] && root_login=prohibit-password
    cat > "$SSH_DROPIN" <<EOF
# Clive (deploy/bootstrap.sh). 00- so these win over 50-cloud-init.conf: sshd keeps the first value.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin $root_login
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 4
LoginGraceTime 30
ClientAliveInterval 120
EOF
    if sshd -t; then
      systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
      eff="$(sshd -T 2>/dev/null | awk '/^passwordauthentication / {print $2}')"
      [[ "$eff" == no ]] && echo "effective: passwordauthentication no, permitrootlogin $root_login" || warn "sshd still reports passwordauthentication=$eff"
    else
      warn "sshd config test failed; removing the clive drop-in"; rm -f "$SSH_DROPIN"
    fi
  else
    warn "$ADMIN has no authorized_keys: leaving SSH password settings alone"
  fi

  say "firewall: inbound SSH only"
  if [[ $IN_CONTAINER -eq 1 ]]; then
    echo "container: firewall is the host's; ufw rules written but not enabled"
    ufw --dry-run default deny incoming >/dev/null 2>&1 || true
  elif [[ -f /etc/iptables/rules.v4 ]] && grep -q -E 'InstanceServices|169\.254\.0\.0/16' /etc/iptables/rules.v4; then
    # Oracle's Ubuntu images ship iptables rules that already allow only SSH and keep the
    # instance-metadata/iSCSI rules the VM needs. ufw on top of them breaks those; leave them.
    echo "Oracle iptables rules found (SSH-only already): not enabling ufw"
  else
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw limit "${ssh_port}/tcp" comment 'ssh (rate-limited)' >/dev/null
    ufw --force enable >/dev/null
    ufw status verbose | sed -n '1,8p'
  fi

  say "fail2ban: sshd jail (systemd journal backend)"
  mkdir -p /etc/fail2ban/jail.d
  cat > /etc/fail2ban/jail.d/clive.local <<EOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5
backend = systemd
banaction = $([[ $IN_CONTAINER -eq 1 ]] && echo dummy || echo ufw)

[sshd]
enabled = true
port = $ssh_port
EOF
  grep -q 'InstanceServices' /etc/iptables/rules.v4 2>/dev/null && sed -i 's/^banaction = ufw/banaction = iptables-multiport/' /etc/fail2ban/jail.d/clive.local
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban 2>/dev/null || warn "fail2ban did not start (check: journalctl -u fail2ban)"

  say "uv + Python $PYVER ($ARCH)"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
  fi
  uv --version
  as_clive uv python install "$PYVER"
  if [[ ! -x $VENV/bin/python ]] || ! "$VENV/bin/python" -c "import sys; sys.exit(0 if sys.version_info[:2] == (${PYVER/./, }) else 1)"; then
    rm -rf "$VENV"
    install -d -m 755 -o "$CLIVE_USER" -g "$CLIVE_USER" "$VENV"
    as_clive uv venv --python "$PYVER" "$VENV"
  fi
  "$VENV/bin/python" --version
}

# uv as clive, gentle on a 1 GB machine: one build and two installs at a time.
# UV_NO_CONFIG + a readable cwd: run from root's home (ssh 'bash -s') clive could not even stat it.
as_clive() {
  (
    sudo -u "$CLIVE_USER" test -x "$PWD" 2>/dev/null || cd /
    sudo -u "$CLIVE_USER" -H env UV_NO_CONFIG=1 UV_PYTHON_INSTALL_DIR="$BASE/python" UV_CACHE_DIR="/home/$CLIVE_USER/.cache/uv" \
      UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_INSTALLS=2 UV_CONCURRENT_DOWNLOADS=4 UV_HTTP_TIMEOUT=120 "$@"
  )
}

# ============================================================================ app
app_setup() {
  [[ -f "$APP/deploy/bin/run-job.sh" ]] || { echo "no code in $APP yet: run deploy/sync_code.sh on the Mac" >&2; exit 1; }
  [[ -x $VENV/bin/python ]] || { echo "no venv yet: run bootstrap.sh without --app first" >&2; exit 1; }

  say "requirements (pinned to the Mac's versions by deploy/constraints-vm.txt; wheels only)"
  cd "$APP"
  reqs=(-r "Stock Shorting Bot/requirements.txt" -r "Options Bot/requirements.txt" -r deploy/requirements-vm.txt)
  [[ -f health/requirements.txt ]] && reqs+=(-r health/requirements.txt)
  # --only-binary for the heavy ones: a missing wheel must fail loudly, never compile numpy/pandas
  # on a 1 GB droplet. The small pure-Python sdists (multitasking, peewee, sseclient-py) still build.
  as_clive uv pip install --python "$VENV/bin/python" "${reqs[@]}" -c deploy/constraints-vm.txt \
    --only-binary numpy,pandas,pydantic-core,curl-cffi,msgpack,websockets,frozendict,charset-normalizer
  sudo -u "$CLIVE_USER" "$VENV/bin/python" -c "import alpaca, yfinance, pandas, numpy, requests, curl_cffi; print('imports ok: alpaca-py', __import__('importlib.metadata').metadata.version('alpaca-py'), '| yfinance', yfinance.__version__, '| pandas', pandas.__version__, '| numpy', numpy.__version__)"

  chmod 755 "$APP/deploy/bin/run-job.sh" "$APP/deploy/healthcheck.sh" "$APP/deploy/dryrun.sh"
  [[ -f "$APP/deploy/shadow_compare.py" ]] && chmod 755 "$APP/deploy/shadow_compare.py"
  install -m 755 "$APP/deploy/bin/clive-mode" /usr/local/bin/clive-mode

  say "logrotate for the cron logs and shortingbot.log (live/logs/DATE.log are one small file a day)"
  cat > /etc/logrotate.d/clive <<EOF
"$APP/Stock Shorting Bot/logs/cron.log" "$APP/Stock Shorting Bot/logs/shortingbot.log" "$APP/Options Bot/live/logs/cron.log" "$APP/events/logs/sync.log" "$APP/health/logs/check.log" {
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $CLIVE_USER $CLIVE_USER
}
$BASE/dryrun-*.log {
    monthly
    rotate 0
    maxage 60
    missingok
}
EOF
  logrotate -d /etc/logrotate.d/clive >/dev/null 2>&1 || warn "logrotate -d reported a problem with /etc/logrotate.d/clive"

  say "systemd units"
  install -m 644 "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/
  systemctl daemon-reload
  for t in "$APP"/deploy/systemd/*.timer; do
    name="$(basename "$t")"
    systemctl enable --now "$name" >/dev/null
    systemctl restart "$name"
  done
  echo; echo "mode: $(cat $ETC/mode)   (sudo clive-mode shadow|live|off)"
  systemctl list-timers 'clive-*' --no-pager | head -n 20
}

# ============================================================================ verify
# shellcheck disable=SC2034  # sshd_t is read inside ok_if's eval
verify() {
  local pass=0 warns=0 fails=0
  r() {  # r PASS|WARN|FAIL "what"
    printf '%-5s %s\n' "$1" "$2"
    case "$1" in PASS) pass=$((pass + 1)) ;; WARN) warns=$((warns + 1)) ;; *) fails=$((fails + 1)) ;; esac
  }
  ok_if() { if eval "$2"; then r PASS "$1"; else r "${3:-FAIL}" "$1"; fi; }
  local mem_kb; mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  echo "verify on $(hostname) ($(uname -m), $((mem_kb / 1024)) MB RAM, admin $ADMIN$([[ $IN_CONTAINER -eq 1 ]] && echo ', container'))"
  ok_if "user clive exists, no sudo" "id clive >/dev/null 2>&1 && ! id -nG clive | grep -qw sudo"
  ok_if "clive has SSH keys" "[[ -s /home/clive/.ssh/authorized_keys ]]" WARN
  ok_if "clive can read the journal" "id -nG clive | grep -qw systemd-journal"
  ok_if "/etc/clive/mode = $(cat $ETC/mode 2>/dev/null || echo missing)" "[[ -f $ETC/mode ]]"
  ok_if "system timezone UTC" "[[ \"\$(readlink -f /etc/localtime)\" == */UTC ]]"
  ok_if "America/New_York tz data present" "[[ -f /usr/share/zoneinfo/America/New_York ]]"
  if [[ $IN_CONTAINER -eq 1 ]]; then r PASS "chrony configured (not run in a container)"; else
    ok_if "chrony running" "systemctl is-active --quiet chrony"
    ok_if "clock synchronised" "[[ \"\$(timedatectl show -p NTPSynchronized --value 2>/dev/null)\" == yes ]]" WARN
  fi
  if [[ "$mem_kb" -lt 2000000 && $IN_CONTAINER -eq 0 ]]; then
    ok_if "swap on (RAM < 2 GB)" "[[ -n \"\$(swapon --show --noheadings)\" ]]"
  else r PASS "swap not required here"; fi
  ok_if "unattended-upgrades reboot at 06:30 UTC" "grep -q 'Automatic-Reboot-Time \"06:30\"' /etc/apt/apt.conf.d/52clive-unattended"
  ok_if "apt timers held to 05:00-06:20 UTC" "[[ -f /etc/systemd/system/apt-daily-upgrade.timer.d/clive.conf ]]"
  local sshd_t; sshd_t="$(sshd -T 2>/dev/null || true)"     # into a variable: grep -q on a pipe + pipefail = SIGPIPE
  ok_if "sshd: passwords off" "grep -qx 'passwordauthentication no' <<< \"\$sshd_t\""
  ok_if "sshd: root login $([[ $ADMIN == root ]] && echo key-only || echo off)" \
    "grep -qxE 'permitrootlogin (no|prohibit-password|without-password)' <<< \"\$sshd_t\""
  if [[ $IN_CONTAINER -eq 1 ]]; then r PASS "firewall (host's in a container)"; else
    ok_if "firewall: ufw active (or Oracle iptables)" "ufw status | grep -q 'Status: active' || grep -q InstanceServices /etc/iptables/rules.v4 2>/dev/null"
  fi
  ok_if "fail2ban sshd jail" "fail2ban-client status sshd >/dev/null 2>&1" WARN
  ok_if "journald persistent, capped" "[[ -f /etc/systemd/journald.conf.d/60-clive.conf ]]"
  ok_if "venv Python $PYVER" "$VENV/bin/python -c 'import sys; assert sys.version_info[:2] == (${PYVER/./, })' 2>/dev/null"
  ok_if "bot imports (alpaca, yfinance, pandas, numpy, curl_cffi)" "sudo -u clive $VENV/bin/python -c 'import alpaca, yfinance, pandas, numpy, requests, curl_cffi' 2>/dev/null"
  ok_if "logrotate config" "[[ -f /etc/logrotate.d/clive ]]"
  local nt; nt="$(systemctl list-timers 'clive-*' --all --no-legend 2>/dev/null | wc -l | tr -d ' ')"
  ok_if "clive timers loaded ($nt, want 17)" "[[ $nt -ge 17 ]]"
  ok_if "no failed clive-* units" "[[ -z \"\$(systemctl list-units 'clive-*' --state=failed --no-legend)\" ]]" WARN
  ok_if "clive-mode installed" "[[ -x /usr/local/bin/clive-mode ]]"
  echo "---- $pass pass, $warns warn, $fails fail"
  [[ $fails -eq 0 ]]
}

[[ $do_system -eq 1 ]] && system_setup
[[ $do_app -eq 1 ]] && app_setup
if [[ $do_verify -eq 1 ]]; then verify; exit $?; fi
say "done"
