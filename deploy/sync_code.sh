#!/usr/bin/env bash
# sync_code.sh -- copy the bots' CODE from this Mac to the VM, then install requirements and
# systemd units there (bootstrap.sh --app). Run it from anywhere on the Mac:
#
#   deploy/sync_code.sh            sync + install
#   deploy/sync_code.sh --dry-run  show what would change, change nothing
#   deploy/sync_code.sh --no-install
#
# Copied: "Stock Shorting Bot" (code, tests, docs), "Options Bot" live/ + optionsbot/ +
# requirements.txt, events/, health/ (when present), deploy/. Never copied: .env files (secrets.sh does those), logs/,
# state/, .cache/, research/, tuning/, longhist/, results/, __pycache__, node_modules.
# Files on the VM inside the never-copied paths are left alone (rsync --delete skips excluded
# paths), so the VM's logs/ and state/ survive every sync.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_conf

dry=() install=1
for a in "$@"; do
  case "$a" in
    --dry-run|-n) dry=(-n) ;;
    --no-install) install=0 ;;
    *) echo "usage: sync_code.sh [--dry-run] [--no-install]" >&2; exit 2 ;;
  esac
done

mode="$(vm_mode || echo unknown)"
if [[ "$mode" == "live" ]] && in_market_window && [[ ${#dry[@]} -eq 0 ]]; then
  echo "The VM is live and it is market hours (09:20-16:25 ET). A job may be running."
  confirm "Sync code anyway?" || exit 1
fi

COMMON=(-az --delete --itemize-changes ${dry[@]+"${dry[@]}"}
  --exclude '.env' --exclude '.env.local' --exclude '.DS_Store'
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/'
  --exclude '.cache/' --exclude 'node_modules/' --exclude '.netlify/')

push() {  # push DEST_SUBDIR SRC... [-- extra rsync args]
  local dest="$1"; shift
  local srcs=() extra=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do srcs+=("$1"); shift; done
  [[ "${1:-}" == "--" ]] && { shift; extra=("$@"); }
  ssh_clive "mkdir -p $(printf '%q' "$REMOTE_APP/$dest")"
  echo "--> $dest"
  rsync "${COMMON[@]}" ${extra[@]+"${extra[@]}"} -e "$RSYNC_RSH" "${srcs[@]}" \
    "$VM_USER@$VM_HOST:$(rpath "$REMOTE_APP/$dest/")"
}

cd "$REPO"
push "Stock Shorting Bot" "Stock Shorting Bot/" -- \
  --exclude '/logs/' --exclude '/logs.*/' --exclude '/research/'
opt_src=("Options Bot/live" "Options Bot/optionsbot" "Options Bot/requirements.txt" "Options Bot/README.md")
[[ -f "Options Bot/.env.example" ]] && opt_src+=("Options Bot/.env.example")
push "Options Bot" "${opt_src[@]}" -- \
  --exclude '/live/logs/' --exclude '/live/logs.*/' --exclude '/live/state/' --exclude '/live/state.*/' \
  --exclude '/live/.cache/'
push "events" "events/" -- --exclude '/logs/' --exclude '/logs.*/'
# health/check.py (built separately): only if it exists on the Mac yet.
[[ -d "$REPO/health" ]] && push "health" "health/" -- --exclude '/logs/' --exclude '/logs.*/'
push "deploy" "deploy/" -- --exclude '/vm.conf' --exclude '/backups/'

# The VM has no .git, so the bots record code_version = null there. Leave a note of what was synced.
ver="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
[[ -n "$(git -C "$REPO" status --porcelain -- 'Stock Shorting Bot' 'Options Bot/live' 'Options Bot/optionsbot' events health 2>/dev/null)" ]] && ver="$ver-dirty"
if [[ ${#dry[@]} -eq 0 ]]; then
  ssh_clive "echo '$ver synced $(date -u +%FT%TZ)' > $REMOTE_APP/CODE_VERSION"
  echo "code version on the VM: $ver"
fi

if [[ $install -eq 1 && ${#dry[@]} -eq 0 ]]; then
  echo "--> bootstrap.sh --app (requirements + units)"
  ssh_admin "sudo bash $REMOTE_APP/deploy/bootstrap.sh --app"
fi
