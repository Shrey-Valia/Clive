#!/usr/bin/env bash
# Shared helpers for the Mac-side scripts (sync_code.sh, secrets.sh, migrate_state.sh, mac_cron.sh).
# shellcheck disable=SC2034
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DEPLOY_DIR/.." && pwd)"
REMOTE_APP=/opt/clive/app

load_conf() {
  local conf="$DEPLOY_DIR/vm.conf"
  if [[ ! -f "$conf" ]]; then
    echo "Missing $conf - copy vm.conf.example to vm.conf and set VM_HOST." >&2
    exit 1
  fi
  # shellcheck source=/dev/null
  . "$conf"
  : "${VM_HOST:?set VM_HOST in deploy/vm.conf}"
  VM_ADMIN="${VM_ADMIN:-ubuntu}"; VM_USER="${VM_USER:-clive}"; SSH_PORT="${SSH_PORT:-22}"
  SSH_OPTS=(-p "$SSH_PORT" -o ServerAliveInterval=30 -o ConnectTimeout=15)
  if [[ -n "${SSH_KEY:-}" ]]; then
    SSH_KEY="${SSH_KEY/#\~/$HOME}"
    SSH_OPTS+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
  fi
  RSYNC_RSH="ssh ${SSH_OPTS[*]}"
}

ssh_admin() { ssh "${SSH_OPTS[@]}" "$VM_ADMIN@$VM_HOST" "$@"; }
ssh_clive() { ssh "${SSH_OPTS[@]}" "$VM_USER@$VM_HOST" "$@"; }

# Remote path for rsync/scp: the remote shell splits on spaces, so escape them.
rpath() { local p="$1"; printf '%s' "${p// /\\ }"; }

vm_mode() { ssh_clive 'cat /etc/clive/mode 2>/dev/null || echo off' | tr -d '[:space:]'; }

# 0 when now is a weekday between 09:20 and 16:25 ET (jobs may be running).
in_market_window() {
  local dow hm
  dow="$(TZ=America/New_York date +%u)"; hm="$(TZ=America/New_York date +%H%M)"
  (( dow <= 5 && 10#$hm >= 920 && 10#$hm <= 1625 ))
}

confirm() {  # confirm "question" -> 0 on y/yes
  local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy](es)?$ ]]
}

# Bot lines in the Mac crontab that are still active (not commented).
mac_active_bot_lines() {
  crontab -l 2>/dev/null | grep -v '^[[:space:]]*#' | grep -E 'daily\.py|monitor\.py|sync\.py|live/run\.py|live/sync\.py|sync_events\.py' || true
}
