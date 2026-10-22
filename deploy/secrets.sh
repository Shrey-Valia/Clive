#!/usr/bin/env bash
# secrets.sh -- copy the bots' .env files from this Mac to the VM with scp, chmod 600, owner clive.
# Values are never printed, logged, or put on a command line. Only key NAMES are shown.
#
#   deploy/secrets.sh              copy the .env files that exist on the Mac
#   deploy/secrets.sh --prompt     type the values instead (hidden input) for any file missing here
#   deploy/secrets.sh --deploy-env also set HEARTBEAT_URL / NTFY_TOPIC in /etc/clive/deploy.env
#
# Files (Mac -> VM, same relative path under /opt/clive/app):
#   Stock Shorting Bot/.env   Alpaca paper keys (shorting), Supabase URL + service key, NTFY_TOPIC
#                             (also read by the options bot to refuse that account, and by events/)
#   Options Bot/.env          the SEPARATE options paper account, Supabase, SHORTING_ACCOUNT_ID/NUMBER
#   .env  (repo root)         legacy Alpaca keys; nothing deployed reads it, copied for parity if present
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_conf

prompt=0 deploy_env=0
for a in "$@"; do
  case "$a" in
    --prompt) prompt=1 ;;
    --deploy-env) deploy_env=1 ;;
    *) echo "usage: secrets.sh [--prompt] [--deploy-env]" >&2; exit 2 ;;
  esac
done

FILES=("Stock Shorting Bot/.env" "Options Bot/.env" ".env")
TAGS=(shorting options root)
# required_keys / optional_keys TAG -> space-separated key names
required_keys() {
  case "$1" in
    shorting) echo "APCA_API_KEY_ID APCA_API_SECRET_KEY APCA_PAPER SUPABASE_URL SUPABASE_SERVICE_KEY" ;;
    options)  echo "OPT_APCA_API_KEY_ID OPT_APCA_API_SECRET_KEY OPT_APCA_PAPER SUPABASE_URL SUPABASE_SERVICE_KEY" ;;
  esac
}
optional_keys() {
  case "$1" in
    shorting) echo "NTFY_TOPIC NTFY_SERVER FMP_API_KEY" ;;
    options)  echo "SHORTING_ACCOUNT_ID SHORTING_ACCOUNT_NUMBER" ;;
  esac
}

has_key() { grep -qE "^[[:space:]]*$1[[:space:]]*=[[:space:]]*[^[:space:]]" "$2"; }

TMPS=()
cleanup() { local f; for f in ${TMPS[@]+"${TMPS[@]}"}; do [[ -f "$f" ]] && { rm -P "$f" 2>/dev/null || rm -f "$f"; }; done; }
trap cleanup EXIT

prompt_file() {  # prompt_file TAG OUT -> writes a .env typed in by the owner to OUT
  local tag="$1" out="$2" req opt k v
  req="$(required_keys "$tag")"; opt="$(optional_keys "$tag")"
  echo "Enter values for $tag (input hidden; empty = skip an optional key)." >&2
  for k in $req $opt; do
    v=""
    read -r -s -p "  $k: " v </dev/tty; echo >&2
    [[ -z "$v" ]] && continue
    printf '%s=%s\n' "$k" "$v" >> "$out"
  done
}

SCP_OPTS=(-q -P "$SSH_PORT")
[[ -n "${SSH_KEY:-}" ]] && SCP_OPTS+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)

ssh_clive 'install -d -m 700 ~/.clive-incoming'
i=0
for rel in "${FILES[@]}"; do
  tag="${TAGS[$i]}"; i=$((i + 1))
  src="$REPO/$rel"
  if [[ ! -f "$src" ]]; then
    if [[ $prompt -eq 1 && "$tag" != root ]]; then
      src="$(mktemp -t clive-env)"; chmod 600 "$src"; TMPS+=("$src")
      prompt_file "$tag" "$src"
    else
      [[ "$tag" == root ]] && { echo "skip  $rel (not on this Mac; nothing deployed needs it)"; continue; }
      echo "MISSING $rel on this Mac. Re-run with --prompt to type the values." >&2; exit 1
    fi
  fi
  if [[ "$tag" != root ]]; then
    for k in $(required_keys "$tag"); do has_key "$k" "$src" || { echo "MISSING key $k in $rel - fix it on the Mac first." >&2; exit 1; }; done
  fi
  scp "${SCP_OPTS[@]}" "$src" "$VM_USER@$VM_HOST:.clive-incoming/$tag.env"
  dest="$REMOTE_APP/$rel"
  ssh_clive "set -e; d=$(printf '%q' "$(dirname "$dest")"); mkdir -p \"\$d\"; \
    install -m 600 ~/.clive-incoming/$tag.env $(printf '%q' "$dest"); rm -f ~/.clive-incoming/$tag.env"
  lsum="$(shasum -a 256 < "$src" | cut -c1-12)"
  rsum="$(ssh_clive "sha256sum < $(printf '%q' "$dest") | cut -c1-12")"
  [[ "$lsum" == "$rsum" ]] || { echo "checksum mismatch for $rel" >&2; exit 1; }
  echo "ok    $rel -> VM (600, clive), keys: $(sed -nE 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=.*/\1/p' "$src" | tr '\n' ' ')"
done
ssh_clive 'rmdir ~/.clive-incoming 2>/dev/null || true'

shorting_env="$REPO/Stock Shorting Bot/.env"
if [[ -f "$shorting_env" ]] && ! has_key NTFY_TOPIC "$shorting_env"; then
  cat <<'EOF'

NOTE: NTFY_TOPIC is not set in "Stock Shorting Bot/.env", so nothing can reach your phone.
  Pick a long random topic (it is the only password ntfy has), e.g. clive-$(openssl rand -hex 8),
  subscribe to it in the ntfy app, then either
    - add NTFY_TOPIC=... to "Stock Shorting Bot/.env" and re-run secrets.sh (the bots' own alerts
      start pushing too), or
    - run secrets.sh --deploy-env and enter it there (VM health alerts only).
EOF
fi

if [[ $deploy_env -eq 1 ]]; then
  echo
  echo "/etc/clive/deploy.env (input hidden; empty keeps nothing):"
  hb="" nt=""
  read -r -s -p "  HEARTBEAT_URL (healthchecks.io ping URL): " hb </dev/tty; echo
  read -r -s -p "  NTFY_TOPIC override (empty = use the shorting .env's): " nt </dev/tty; echo
  printf '# written by secrets.sh %s\nHEARTBEAT_URL=%s\nNTFY_TOPIC=%s\n' "$(date +%F)" "$hb" "$nt" \
    | ssh_admin "sudo sh -c 'umask 027; cat > /etc/clive/deploy.env.tmp && chown root:clive /etc/clive/deploy.env.tmp && chmod 640 /etc/clive/deploy.env.tmp && mv /etc/clive/deploy.env.tmp /etc/clive/deploy.env'"
  echo "ok    /etc/clive/deploy.env (640 root:clive)"
fi

echo
echo "Done. Check on the VM:  ls -l '/opt/clive/app/Stock Shorting Bot/.env' '/opt/clive/app/Options Bot/.env'"
