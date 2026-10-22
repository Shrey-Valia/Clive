#!/usr/bin/env bash
# bootstrap.sh -- prepare an Ubuntu 22.04 / 24.04 VM (x86_64 or arm64) for the Clive bots.
# Idempotent: safe to run again at any time.
#
# First run (from the Mac, one command; README step 4):
#   ssh ubuntu@VM_IP 'sudo bash -s' < deploy/bootstrap.sh
#
# After every code sync, sync_code.sh runs the app half only:
#   sudo bash /opt/clive/app/deploy/bootstrap.sh --app
#
#   (no flag)  system setup; then the app half too if the code is already on the VM
#   --app      venv + requirements, systemd units, clive-mode, logrotate
#   --system   system setup only
#
# What the system half does: `clive` user (no sudo), UTC system clock, chrony, unattended-upgrades
# (reboots only at 06:30 UTC = 01:30/02:30 ET), SSH-only firewall, key-only SSH, persistent
# journald, swap when RAM < 2 GB, uv + Python 3.13 (same minor as the Mac) in /opt/clive/venv.
# It reads no secrets and writes none.
set -euo pipefail

CLIVE_USER=clive
BASE=/opt/clive
APP=$BASE/app
VENV=$BASE/venv
PYVER=3.13
ETC=/etc/clive

say() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { echo "run as root: sudo bash bootstrap.sh" >&2; exit 1; }

do_system=1 do_app=1
case "${1:-}" in
  --app) do_system=0 ;;
  --system) do_app=0 ;;
  "") [[ -f "$APP/deploy/bin/run-job.sh" ]] || do_app=0 ;;
  *) echo "usage: bootstrap.sh [--app|--system]" >&2; exit 2 ;;
esac

# ============================================================================ system
system_setup() {
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
    warn "tested on Ubuntu 22.04/24.04 only; this is ${PRETTY_NAME:-unknown}. Continuing."
  fi
  ARCH="$(uname -m)"; say "Ubuntu ${VERSION_ID:-?} on $ARCH"

  say "packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends \
    ca-certificates curl jq rsync git tzdata chrony unattended-upgrades apt-listchanges \
    ufw fail2ban logrotate util-linux procps

  say "clock: system stays UTC, jobs are scheduled in America/New_York by the timers"
  timedatectl set-timezone UTC
  # Cloud metadata time servers are closer and leap-smeared the same way as the host.
  vendor="$(cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/chassis_asset_tag 2>/dev/null | tr '\n' ' ')"
  src=""
  case "$vendor" in
    *Google*) src="server metadata.google.internal prefer iburst" ;;
    *OracleCloud*|*Oracle*) src="server 169.254.169.254 prefer iburst" ;;
    *Amazon*) src="server 169.254.169.123 prefer iburst" ;;
  esac
  mkdir -p /etc/chrony/sources.d
  if [[ -n "$src" ]]; then
    echo "$src" > /etc/chrony/sources.d/clive-cloud.sources
  else
    rm -f /etc/chrony/sources.d/clive-cloud.sources
  fi
  # Ubuntu 22.04's chrony.conf may lack the sourcedir line; add it once.
  grep -q '^sourcedir /etc/chrony/sources.d' /etc/chrony/chrony.conf || echo 'sourcedir /etc/chrony/sources.d' >> /etc/chrony/chrony.conf
  systemctl enable --now chrony >/dev/null 2>&1 || systemctl enable --now chronyd
  systemctl restart chrony 2>/dev/null || systemctl restart chronyd
  timedatectl set-ntp true 2>/dev/null || true

  say "user $CLIVE_USER"
  if ! id "$CLIVE_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$CLIVE_USER"
  fi
  usermod -aG systemd-journal "$CLIVE_USER"    # healthcheck.sh reads the jobs' journal
  # Let the Mac rsync/scp as clive with the same key you used for the admin user.
  admin="${SUDO_USER:-}"
  ch=/home/$CLIVE_USER/.ssh
  install -d -m 700 -o "$CLIVE_USER" -g "$CLIVE_USER" "$ch"
  if [[ -n "$admin" && "$admin" != root && -s "/home/$admin/.ssh/authorized_keys" ]]; then
    touch "$ch/authorized_keys"
    while IFS= read -r k; do
      [[ -z "$k" || "$k" == \#* ]] && continue
      grep -qxF "$k" "$ch/authorized_keys" || echo "$k" >> "$ch/authorized_keys"
    done < "/home/$admin/.ssh/authorized_keys"
    chown "$CLIVE_USER:$CLIVE_USER" "$ch/authorized_keys"; chmod 600 "$ch/authorized_keys"
  else
    warn "could not copy SSH keys from the admin user; add yours to $ch/authorized_keys"
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

  say "swap (only when RAM < 2 GB)"
  mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  if [[ "$mem_kb" -lt 2000000 ]]; then
    if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
      [[ -f /swapfile ]] || fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
      chmod 600 /swapfile
      mkswap /swapfile >/dev/null
      swapon /swapfile
    fi
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' > /etc/sysctl.d/60-clive-swap.conf
    sysctl -q -p /etc/sysctl.d/60-clive-swap.conf
    echo "RAM $((mem_kb / 1024)) MB: 2 GB /swapfile on"
  else
    echo "RAM $((mem_kb / 1024)) MB: no swap needed"
  fi

  say "unattended-upgrades (security only, reboot if needed at 06:30 UTC, never in market hours)"
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
  cat > /etc/apt/apt.conf.d/52clive-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "06:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
  # apt's own timers fire at random times by default; keep them away from 09:00-16:30 ET.
  for t in apt-daily apt-daily-upgrade; do
    mkdir -p /etc/systemd/system/$t.timer.d
    when="05:00"; [[ $t == apt-daily-upgrade ]] && when="06:00"
    printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* %s:00 UTC\nRandomizedDelaySec=20m\n' "$when" > /etc/systemd/system/$t.timer.d/clive.conf
  done
  # needrestart (24.04) must not restart services interactively or mid-job.
  if [[ -d /etc/needrestart/conf.d ]]; then
    echo "\$nrconf{restart} = 'l';" > /etc/needrestart/conf.d/50-clive.conf
  fi
  systemctl daemon-reload
  systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

  say "journald: persistent, capped at 300 MB"
  mkdir -p /var/log/journal /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=persistent\nSystemMaxUse=300M\n' > /etc/systemd/journald.conf.d/60-clive.conf
  systemctl restart systemd-journald

  say "SSH: keys only, no root login"
  ssh_port="$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}')"; ssh_port="${ssh_port:-22}"
  if [[ -n "$admin" && -s "/home/$admin/.ssh/authorized_keys" ]]; then
    mkdir -p /etc/ssh/sshd_config.d
    cat > /etc/ssh/sshd_config.d/60-clive.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
    if sshd -t; then
      systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    else
      warn "sshd config test failed; removing the clive drop-in"; rm -f /etc/ssh/sshd_config.d/60-clive.conf
    fi
  else
    warn "admin user has no authorized_keys: leaving SSH password settings alone"
  fi

  say "firewall: inbound SSH only"
  if [[ -f /etc/iptables/rules.v4 ]] && grep -q -E 'InstanceServices|169\.254\.0\.0/16' /etc/iptables/rules.v4; then
    # Oracle's Ubuntu images ship iptables rules that already allow only SSH and keep the
    # instance-metadata/iSCSI rules the VM needs. ufw on top of them breaks those; leave them.
    echo "Oracle iptables rules found (SSH-only already): not enabling ufw"
  else
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow "${ssh_port}/tcp" >/dev/null
    ufw --force enable >/dev/null
    ufw status verbose | sed -n '1,6p'
  fi
  systemctl enable --now fail2ban >/dev/null 2>&1 || true

  say "uv + Python $PYVER"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
  fi
  uv --version
  as_clive() { sudo -u "$CLIVE_USER" -H env UV_PYTHON_INSTALL_DIR="$BASE/python" UV_CACHE_DIR="/home/$CLIVE_USER/.cache/uv" "$@"; }
  as_clive uv python install "$PYVER"
  if [[ ! -x $VENV/bin/python ]] || ! "$VENV/bin/python" -c "import sys; sys.exit(0 if sys.version_info[:2] == (${PYVER/./, }) else 1)"; then
    rm -rf "$VENV"
    install -d -m 755 -o "$CLIVE_USER" -g "$CLIVE_USER" "$VENV"
    as_clive uv venv --python "$PYVER" "$VENV"
  fi
  "$VENV/bin/python" --version
}

# ============================================================================ app
app_setup() {
  [[ -f "$APP/deploy/bin/run-job.sh" ]] || { echo "no code in $APP yet: run deploy/sync_code.sh on the Mac" >&2; exit 1; }
  [[ -x $VENV/bin/python ]] || { echo "no venv yet: run bootstrap.sh without --app first" >&2; exit 1; }

  say "requirements (pinned to the Mac's versions by deploy/constraints-vm.txt)"
  cd "$APP"
  reqs=(-r "Stock Shorting Bot/requirements.txt" -r "Options Bot/requirements.txt" -r deploy/requirements-vm.txt)
  [[ -f health/requirements.txt ]] && reqs+=(-r health/requirements.txt)
  sudo -u "$CLIVE_USER" -H env UV_CACHE_DIR="/home/$CLIVE_USER/.cache/uv" \
    uv pip install --python "$VENV/bin/python" "${reqs[@]}" -c deploy/constraints-vm.txt
  sudo -u "$CLIVE_USER" "$VENV/bin/python" -c "import alpaca, yfinance, pandas, numpy, requests; print('imports ok: alpaca-py', __import__('importlib.metadata').metadata.version('alpaca-py'), '| pandas', pandas.__version__)"

  chmod 755 "$APP/deploy/bin/run-job.sh" "$APP/deploy/healthcheck.sh" "$APP/deploy/dryrun.sh"
  install -m 755 "$APP/deploy/bin/clive-mode" /usr/local/bin/clive-mode

  say "logrotate for the cron logs (the bots' own logs are left alone)"
  cat > /etc/logrotate.d/clive <<EOF
"$APP/Stock Shorting Bot/logs/cron.log" "$APP/Options Bot/live/logs/cron.log" "$APP/events/logs/sync.log" "$APP/health/logs/check.log" {
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $CLIVE_USER $CLIVE_USER
}
EOF

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

[[ $do_system -eq 1 ]] && system_setup
[[ $do_app -eq 1 ]] && app_setup
say "done"
