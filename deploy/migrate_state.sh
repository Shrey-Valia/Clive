#!/usr/bin/env bash
# migrate_state.sh -- move the bots' STATE between the Mac and the VM. Run on the Mac.
#
#   deploy/migrate_state.sh push    Mac -> VM, at cutover (README step 8)
#   deploy/migrate_state.sh pull    VM -> Mac, at rollback (README "Rollback")
#   deploy/migrate_state.sh diff    compare the key state files on both sides (read-only)
#
# State = the files that make each job run exactly once and carry positions across days:
#   Stock Shorting Bot/logs/   last_execute.json, last_open.json, next_open_picks.json,
#                              equity_peak.json, overnight_longs.json, alerts_state.json,
#                              halt.json, pdt_detected.json, etb_history.csv, *.lock, the logs
#   Options Bot/live/state/    positions.json, condors.json, trades.jsonl, orders.jsonl,
#                              last_execute.json, account_pin.json, .lock
#   Options Bot/live/logs/, events/logs/   history only
#
# Safety: push refuses while any bot line is active in the Mac crontab or a bot process runs
# on the Mac; pull refuses unless the VM is in mode off with no job running. Exactly one side
# owns the state at any time. Every run keeps a tarball in deploy/backups/.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_conf

DIRS=("Stock Shorting Bot/logs" "Options Bot/live/state" "Options Bot/live/logs" "events/logs" "health/logs")
KEYFILES=(
  "Stock Shorting Bot/logs/last_execute.json" "Stock Shorting Bot/logs/last_open.json"
  "Stock Shorting Bot/logs/next_open_picks.json" "Stock Shorting Bot/logs/equity_peak.json"
  "Stock Shorting Bot/logs/overnight_longs.json" "Stock Shorting Bot/logs/alerts_state.json"
  "Stock Shorting Bot/logs/halt.json" "Stock Shorting Bot/logs/pdt_detected.json"
  "Options Bot/live/state/positions.json" "Options Bot/live/state/condors.json"
  "Options Bot/live/state/last_execute.json" "Options Bot/live/state/account_pin.json"
  "Options Bot/live/state/trades.jsonl" "Options Bot/live/state/orders.jsonl"
)
STAMP="$(date +%Y%m%d-%H%M%S)"
force=0; [[ "${2:-}" == "--force" ]] && force=1

q() { printf '%q' "$1"; }
mac_procs() { pgrep -fl 'daily\.py|monitor\.py|live/run\.py|live/sync\.py|sync_events\.py|Stock Shorting Bot.*sync\.py' || true; }

backup_mac() {
  mkdir -p "$DEPLOY_DIR/backups"
  local f="$DEPLOY_DIR/backups/state-mac-$STAMP.tgz" have=() d
  for d in "${DIRS[@]}"; do [[ -d "$REPO/$d" ]] && have+=("$d"); done
  if [[ ${#have[@]} -gt 0 ]]; then
    tar -czf "$f" -C "$REPO" "${have[@]}"
    echo "Mac backup: $f"
  fi
}

diff_state() {
  local rel l r
  printf '%-52s %-14s %-14s\n' "file" "Mac" "VM"
  for rel in "${KEYFILES[@]}"; do
    l="-"; [[ -f "$REPO/$rel" ]] && l="$(shasum -a 256 < "$REPO/$rel" | cut -c1-12)"
    r="$(ssh_clive "f=$(q "$REMOTE_APP/$rel"); [ -f \"\$f\" ] && sha256sum < \"\$f\" | cut -c1-12 || echo -")"
    printf '%-52s %-14s %-14s %s\n' "$rel" "$l" "$r" "$([[ "$l" == "$r" ]] && echo same || echo DIFF)"
  done
}

vm_jobs_running() { ssh_clive "systemctl list-units 'clive-job@*' --state=activating,active --no-legend --plain | awk '{print \$1}'"; }

case "${1:-}" in
  diff) diff_state ;;

  push)
    a="$(mac_active_bot_lines)"
    if [[ -n "$a" ]]; then
      echo "Refusing: bot lines are still active in the Mac crontab. Run deploy/mac_cron.sh disable first." >&2
      exit 1
    fi
    p="$(mac_procs)"; [[ -z "$p" ]] || { echo "Refusing: a bot is running on the Mac:" >&2; echo "$p" >&2; exit 1; }
    if in_market_window && [[ $force -eq 0 ]]; then
      echo "Refusing during 09:20-16:25 ET on a weekday: cut over in the evening or at the weekend (--force overrides)." >&2
      exit 1
    fi
    mode="$(vm_mode)"
    [[ "$mode" == "live" ]] && { echo "Refusing: the VM is already live. Use 'diff' to compare." >&2; exit 1; }
    echo "VM mode $mode -> off while the state moves"
    ssh_admin "sudo clive-mode off" >/dev/null
    r="$(vm_jobs_running)"; [[ -z "$r" ]] || { echo "Refusing: a job is running on the VM: $r" >&2; exit 1; }

    backup_mac
    for d in "${DIRS[@]}"; do
      ssh_clive "d=$(q "$REMOTE_APP/$d"); if [ -d \"\$d\" ]; then mv \"\$d\" \"\$d.vm-before-cutover-$STAMP\"; fi; mkdir -p \"\$d\""
      if [[ -d "$REPO/$d" ]]; then
        rsync -a --exclude '.DS_Store' -e "$RSYNC_RSH" "$REPO/$d/" "$VM_USER@$VM_HOST:$(rpath "$REMOTE_APP/$d/")"
        echo "pushed  $d"
      fi
    done
    # Lock files hold a Mac pid that means nothing on the VM; the bots clear dead-pid locks
    # themselves, but drop them so nothing waits on them.
    ssh_clive "rm -f $(q "$REMOTE_APP/Stock Shorting Bot/logs")/*.lock $(q "$REMOTE_APP/Options Bot/live/state")/.lock"
    diff_state
    for d in "Stock Shorting Bot/logs" "Options Bot/live/logs"; do
      [[ -d "$REPO/$d" ]] || continue
      printf 'State moved to the VM %s (deploy/migrate_state.sh push).\nDo not run --execute here. Rollback: deploy/README.md.\n' "$STAMP" > "$REPO/$d/MOVED_TO_VM.txt"
    done
    ssh_admin "sudo sh -c 'echo \"pushed from the Mac $STAMP\" > /var/lib/clive/state-migrated'"
    echo
    echo "State is on the VM. Next: ssh $VM_ADMIN@$VM_HOST 'sudo clive-mode live'"
    ;;

  pull)
    mode="$(vm_mode)"
    if [[ "$mode" != "off" ]]; then
      echo "Setting the VM to mode off first (it was $mode)."
      ssh_admin "sudo clive-mode off" >/dev/null
    fi
    r="$(vm_jobs_running)"; [[ -z "$r" ]] || { echo "Refusing: a job is running on the VM: $r. Wait for it." >&2; exit 1; }
    if in_market_window && [[ $force -eq 0 ]]; then
      echo "Market hours: pulling now leaves NOTHING running until you re-enable the Mac crontab."
      confirm "Continue?" || exit 1
    fi
    backup_mac
    for d in "${DIRS[@]}"; do
      [[ -d "$REPO/$d" ]] && mv "$REPO/$d" "$REPO/$d.mac-before-rollback-$STAMP"
      mkdir -p "$REPO/$d"
      rsync -a -e "$RSYNC_RSH" "$VM_USER@$VM_HOST:$(rpath "$REMOTE_APP/$d/")" "$REPO/$d/" 2>/dev/null && echo "pulled  $d" || echo "skip    $d (not on the VM)"
    done
    rm -f "$REPO/Stock Shorting Bot/logs/"*.lock "$REPO/Options Bot/live/state/.lock" \
          "$REPO/Stock Shorting Bot/logs/MOVED_TO_VM.txt" "$REPO/Options Bot/live/logs/MOVED_TO_VM.txt"
    ssh_admin "sudo rm -f /var/lib/clive/state-migrated"
    diff_state
    echo
    echo "State is back on the Mac and the VM is off. Next: deploy/mac_cron.sh enable"
    ;;

  *) echo "usage: migrate_state.sh push|pull|diff [--force]" >&2; exit 2 ;;
esac
