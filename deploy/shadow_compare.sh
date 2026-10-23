#!/usr/bin/env bash
# shadow_compare.sh -- on the Mac: pull the VM's logs for a shadow day (read-only), then compare
# them with this Mac's real run (deploy/shadow_compare.py). PASS = the VM decided what the Mac did.
#
#   deploy/shadow_compare.sh                 today (ET); run it after 16:20 ET
#   deploy/shadow_compare.sh 2026-10-27      a given day (the scan and picks-file rows are best the same evening)
#   deploy/shadow_compare.sh DAY --json      any shadow_compare.py flag passes through (--strict, --qty-tol 0.15, ...)
#   KEEP=1 deploy/shadow_compare.sh          keep the pulled copy (its path is printed)
#
# Nothing is written on the VM. The .env files are never pulled.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_conf

day="$(TZ=America/New_York date +%F)"
if [[ "${1:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then day="$1"; shift; fi

mode="$(vm_mode || echo unknown)"
[[ "$mode" == "shadow" ]] || echo "note: the VM is in mode '$mode' (a shadow day needs 'shadow')" >&2

tmp="$(mktemp -d -t clive-shadow)"
if [[ "${KEEP:-0}" == 1 ]]; then echo "VM copy kept in $tmp" >&2; else trap 'rm -rf "$tmp"' EXIT; fi
mkdir -p "$tmp/vm"

# The log files only (no state, no .env). Missing files are skipped on the VM side.
files=(
  "Stock Shorting Bot/logs/shortingbot.log" "Stock Shorting Bot/logs/cron.log"
  "Stock Shorting Bot/logs/next_open_picks.dry_run.json" "Stock Shorting Bot/logs/overnight_longs.dry_run.json"
  "Options Bot/live/logs/$day.log" "Options Bot/live/logs/cron.log"
  "events/logs/sync.log" "health/logs/check.log"
)
remote="cd $(printf '%q' "$REMOTE_APP") && for f in"
for f in "${files[@]}"; do remote+=" $(printf '%q' "$f")"; done
remote+='; do [ -f "$f" ] && printf "%s\0" "$f"; done | tar --null -T - -czf -'
ssh_clive "$remote" | tar -xzf - -C "$tmp/vm"

python3 "$DEPLOY_DIR/shadow_compare.py" --mac "$REPO" --vm "$tmp/vm" --date "$day" "$@"
