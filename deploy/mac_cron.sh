#!/usr/bin/env bash
# mac_cron.sh -- turn the bots' lines in THIS Mac's crontab off or back on. Nothing else in the
# crontab is touched. Every change first saves a copy in deploy/backups/.
#
#   deploy/mac_cron.sh status    show which bot lines are active
#   deploy/mac_cron.sh disable   comment them out with a "#CLIVE-ON-VM# " prefix (cutover)
#   deploy/mac_cron.sh enable    remove that prefix again (rollback)
set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARK='#CLIVE-ON-VM# '
BOT_RE='daily[.]py|monitor[.]py|[^/]sync[.]py|live/run[.]py|live/sync[.]py|sync_events[.]py'

cur="$(crontab -l 2>/dev/null || true)"
backup() {
  mkdir -p "$DEPLOY_DIR/backups"
  local f; f="$DEPLOY_DIR/backups/crontab-$(date +%Y%m%d-%H%M%S).txt"
  printf '%s\n' "$cur" > "$f"; echo "backup: $f"
}
active() { printf '%s\n' "$cur" | grep -v '^[[:space:]]*#' | grep -E "$BOT_RE" || true; }
parked() { printf '%s\n' "$cur" | grep -F "$MARK" || true; }

case "${1:-status}" in
  status)
    a="$(active)"; p="$(parked)"
    echo "active bot lines: $(printf '%s' "$a" | grep -c . || true)"
    echo "parked (#CLIVE-ON-VM#) lines: $(printf '%s' "$p" | grep -c . || true)"
    [[ -n "$a" ]] && printf '%s\n' "$a" | sed 's/^/  ON  /'
    [[ -n "$p" ]] && printf '%s\n' "$p" | sed 's/^/  OFF /'
    exit 0
    ;;
  disable)
    [[ -n "$(active)" ]] || { echo "no active bot lines; nothing to do"; exit 0; }
    backup
    new="$(printf '%s\n' "$cur" | awk -v mark="$MARK" -v re="$BOT_RE" \
      '$0 !~ /^[[:space:]]*#/ && $0 ~ re { print mark $0; next } { print }')"
    printf '%s\n' "$new" | crontab -
    echo "disabled. Check: crontab -l"
    # a job started by cron just before this keeps running; make sure none is
    if pgrep -f 'daily\.py|monitor\.py|live/run\.py|live/sync\.py|sync_events\.py|Stock Shorting Bot.*sync\.py' >/dev/null; then
      echo "WARNING: a bot process is still running on this Mac:"; pgrep -fl 'daily\.py|monitor\.py|live/run\.py|sync\.py|sync_events\.py' || true
      echo "Wait for it to finish before migrate_state.sh push."
    fi
    ;;
  enable)
    [[ -n "$(parked)" ]] || { echo "no parked lines; nothing to do"; exit 0; }
    backup
    printf '%s\n' "$cur" | sed "s/^${MARK}//" | crontab -
    echo "enabled. Check: crontab -l"
    ;;
  *) echo "usage: mac_cron.sh status|disable|enable" >&2; exit 2 ;;
esac
