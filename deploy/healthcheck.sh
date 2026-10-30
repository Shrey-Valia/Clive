#!/usr/bin/env bash
# healthcheck.sh -- runs on the VM as the clive user, from three systemd units:
#
#   --daily        clive-healthcheck.timer, 16:30 ET Mon-Fri. On a trading day, checks that every
#                  expected job ran today and exited 0 (journald), that the bots' Supabase heartbeats
#                  moved today (bots.last_run_at / last_sync_at / last_heartbeat), and that no short
#                  is still open on the shorting account. Problems -> ntfy + a Supabase alerts row.
#   --failed JOB   clive-alert@JOB.service, the OnFailure= hook: one job just failed or timed out.
#   --alive        clive-alive.timer, hourly: pings HEARTBEAT_URL (a dead-man switch such as
#                  healthchecks.io, which alerts you when the pings STOP - the only way to hear
#                  about a VM that is down), and once a day at 08:xx ET sends a "VM alive" ntfy.
#
# Reads NTFY_TOPIC / NTFY_SERVER / SUPABASE_* / APCA_* from "Stock Shorting Bot/.env" and
# HEARTBEAT_URL (plus optional NTFY_TOPIC override) from /etc/clive/deploy.env.
# Secrets never reach argv (ps), stdout or the journal: curl gets them through --config <(...).
#
# Extra flags: --dry (print what would be sent, send nothing), --date YYYY-MM-DD (check another day).
set -uo pipefail
export TZ=America/New_York

APP="${CLIVE_APP:-/opt/clive/app}"
SHORT_ENV="$APP/Stock Shorting Bot/.env"
DEPLOY_ENV="${CLIVE_DEPLOY_ENV:-/etc/clive/deploy.env}"
MODE_FILE="${CLIVE_MODE_FILE:-/etc/clive/mode}"
ALPACA_PAPER="https://paper-api.alpaca.markets"

action="" failed_job="" dry=0 day="$(date +%F)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --daily|--alive) action="${1#--}" ;;
    --failed) action="failed"; failed_job="${2:-}"; shift ;;
    --dry) dry=1 ;;
    --date) day="${2:-}"; shift ;;
    *) echo "usage: healthcheck.sh --daily|--alive|--failed JOB [--dry] [--date YYYY-MM-DD]" >&2; exit 2 ;;
  esac
  shift
done
[[ -n "$action" ]] || { echo "usage: healthcheck.sh --daily|--alive|--failed JOB [--dry]" >&2; exit 2; }

mode="off"; [[ -r "$MODE_FILE" ]] && mode="$(tr -d '[:space:]' < "$MODE_FILE")"

# ------------------------------------------------------------------ env (values never printed)
envget() {  # envget KEY FILE -> value of the last KEY=... line, quotes stripped
  local key="$1" file="$2" line=""
  [[ -r "$file" ]] || return 0
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n 1)" || true
  line="${line#*=}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
  printf '%s' "$line"
}
NTFY_TOPIC="$(envget NTFY_TOPIC "$DEPLOY_ENV")"; [[ -n "$NTFY_TOPIC" ]] || NTFY_TOPIC="$(envget NTFY_TOPIC "$SHORT_ENV")"
NTFY_SERVER="$(envget NTFY_SERVER "$DEPLOY_ENV")"; [[ -n "$NTFY_SERVER" ]] || NTFY_SERVER="$(envget NTFY_SERVER "$SHORT_ENV")"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"; NTFY_SERVER="${NTFY_SERVER%/}"
SB_URL="$(envget SUPABASE_URL "$SHORT_ENV")"; SB_URL="${SB_URL%/}"
SB_KEY="$(envget SUPABASE_SERVICE_KEY "$SHORT_ENV")"
APCA_KEY="$(envget APCA_API_KEY_ID "$SHORT_ENV")"
APCA_SECRET="$(envget APCA_API_SECRET_KEY "$SHORT_ENV")"
HEARTBEAT_URL="$(envget HEARTBEAT_URL "$DEPLOY_ENV")"

log() { echo "healthcheck: $*"; }

# ------------------------------------------------------------------ senders
ntfy() {  # ntfy TITLE PRIORITY MESSAGE
  local title="$1" prio="$2" msg="$3"
  [[ "$mode" == "shadow" ]] && title="[VM shadow] $title"
  if [[ $dry -eq 1 ]]; then log "DRY ntfy ($prio) $title: $msg"; return 0; fi
  if [[ -z "$NTFY_TOPIC" ]]; then log "ntfy not configured (no NTFY_TOPIC) - would send: $title: $msg"; return 0; fi
  curl -fsS -m 15 --retry 2 -o /dev/null \
    --config <(printf 'url = "%s/%s"\n' "$NTFY_SERVER" "$NTFY_TOPIC") \
    -H "Title: $title" -H "Priority: $prio" -H "Tags: rotating_light" \
    --data-binary "$msg" || log "ntfy push failed"
}

sb_curl() {  # sb_curl METHOD PATH [curl args...]  (Supabase REST with the service key)
  local method="$1" path="$2"; shift 2
  curl -fsS -m 20 -X "$method" \
    --config <(printf 'url = "%s/rest/v1/%s"\nheader = "apikey: %s"\nheader = "Authorization: Bearer %s"\n' \
                      "$SB_URL" "$path" "$SB_KEY" "$SB_KEY") "$@"
}

sb_alert() {  # sb_alert BOT_ID|- LEVEL KIND MESSAGE  -> one alerts row (live mode only)
  local bot="$1" level="$2" kind="$3" msg="$4" body
  if [[ "$mode" != "live" ]]; then log "mode=$mode: no Supabase alert row ($kind)"; return 0; fi
  if [[ -z "$SB_URL" || -z "$SB_KEY" ]]; then log "Supabase not configured - no alert row"; return 0; fi
  body="$(jq -nc --arg b "$bot" --arg l "$level" --arg k "$kind" --arg m "$msg" \
          '{bot_id: (if $b == "-" then null else $b end), level: $l, kind: $k, message: $m}')"
  if [[ $dry -eq 1 ]]; then log "DRY alerts row: $body"; return 0; fi
  sb_curl POST "alerts" -o /dev/null -H "Content-Type: application/json" -H "Prefer: return=minimal" \
    --data-binary "$body" || log "Supabase alerts insert failed"
}

alpaca_get() {  # alpaca_get PATH  (shorting paper account, read-only)
  curl -fsS -m 20 --config <(printf 'url = "%s%s"\nheader = "APCA-API-KEY-ID: %s"\nheader = "APCA-API-SECRET-KEY: %s"\n' \
                                     "$ALPACA_PAPER" "$1" "$APCA_KEY" "$APCA_SECRET")
}

bot_of() { case "$1" in short-*) echo shorting ;; opt-*) echo options ;; *) echo - ;; esac; }

# ------------------------------------------------------------------ journal checks
day_start="$(date -d "$day 00:00" +%s)"
day_end="$(date -d "$day 23:59:59" +%s)"

job_status() {  # job_status JOB -> ok | skipped | off | failed:<line> | missing
  local job="$1" out last
  out="$(journalctl -q --no-pager -o cat -u "clive-job@${job}.service" \
          --since "@${day_start}" --until "@${day_end}" 2>/dev/null)" || true
  last="$(grep -E "^=== clive-job ${job} (exit|TIMED)" <<< "$out" | tail -n 1)"
  if [[ -n "$last" ]]; then
    if grep -q "exit 0 " <<< "$last"; then echo ok; else echo "failed:${last#=== clive-job }"; fi
  elif grep -q "run-job ${job}: mode=shadow, skipped" <<< "$out"; then echo skipped
  elif grep -q "run-job ${job}: mode=off" <<< "$out"; then echo off
  else echo missing
  fi
}

# ================================================================== --failed JOB
if [[ "$action" == "failed" ]]; then
  [[ "$mode" == "off" ]] && { log "mode off: not alerting for ${failed_job}"; exit 0; }
  tail_lines="$(journalctl -q --no-pager -o cat -u "clive-job@${failed_job}.service" -n 6 2>/dev/null | cut -c1-200)"
  level="warning"; prio="high"
  case "$failed_job" in short-open|short-close|short-halfday|short-fallback|short-halfday-fallback|short-cover-leftovers|opt-sweep|opt-daily|opt-halfday)
    level="critical"; prio="urgent" ;; esac
  msg="clive-job@${failed_job} failed on the VM at $(date '+%H:%M %Z'). Last lines:
${tail_lines}
Check: journalctl -u clive-job@${failed_job} -n 100"
  ntfy "Clive job failed: ${failed_job}" "$prio" "$msg"
  sb_alert "$(bot_of "$failed_job")" "$level" "vm_job_failed" "VM job ${failed_job} failed: $(tail -n 1 <<< "$tail_lines")"
  exit 0
fi

# ================================================================== --alive
if [[ "$action" == "alive" ]]; then
  if [[ -n "$HEARTBEAT_URL" && $dry -eq 0 ]]; then
    # mode in the ping body; healthchecks.io shows it. Not a secret; the URL is passed via --config.
    curl -fsS -m 10 --retry 3 -o /dev/null --config <(printf 'url = "%s"\n' "$HEARTBEAT_URL") \
      --data-raw "mode=$mode host=$(hostname)" || log "heartbeat ping failed"
  else
    log "no HEARTBEAT_URL in $DEPLOY_ENV (or --dry): no dead-man ping"
  fi
  if [[ "$(date +%H)" == "08" || $dry -eq 1 ]]; then
    failed_units="$(systemctl list-units --failed --no-legend --plain 'clive-*' 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
    mem="$(free -m | awk '/^Mem:/ {print $7 " MB free of " $2}')"
    disk="$(df -h / | awk 'NR==2 {print $4 " free (" $5 " used)"}')"
    next="$(systemctl list-timers 'clive-*' --no-legend --no-pager 2>/dev/null | head -n 1 | awk '{print $NF, "at", $2, $3}')"
    msg="VM $(hostname) alive. mode=${mode}. up $(uptime -p | sed 's/^up //'). mem ${mem}. disk ${disk}. clock synced: $(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo ?). failed units: ${failed_units:-none}. next: ${next}"
    log "$msg"
    [[ "$mode" != "off" ]] && ntfy "Clive VM alive" "min" "$msg"
  fi
  exit 0
fi

# ================================================================== --daily
[[ "$mode" == "off" ]] && { log "mode=off: nothing to check"; exit 0; }

problems=()   # "level|bot|kind|message"
add() { problems+=("$1|$2|$3|$4"); log "PROBLEM [$1] $4"; }

# 1. Is today a trading day? (Alpaca calendar, shorting paper keys; read-only)
close_hm=""
cal="$(alpaca_get "/v2/calendar?start=${day}&end=${day}" 2>/dev/null)" || cal=""
if [[ -z "$cal" ]]; then
  add warning - vm_healthcheck "Alpaca calendar unreachable at the 16:30 check; assumed a regular day"
  close_hm="16:00"
else
  close_hm="$(jq -r --arg d "$day" '.[] | select(.date == $d) | .close' <<< "$cal" | head -n 1)"
  if [[ -z "$close_hm" ]]; then log "$day: market closed (holiday/weekend) - nothing to check"; exit 0; fi
fi
half_day=0; [[ "$close_hm" < "16:00" ]] && half_day=1
log "$day: trading day, close $close_hm ET, half_day=$half_day, mode=$mode"

# 2. Did every expected job run and exit 0?
critical_jobs=(short-open short-close short-fallback short-cover-leftovers opt-sweep opt-daily)
[[ $half_day -eq 1 ]] && critical_jobs+=(short-halfday short-halfday-fallback opt-halfday)
other_jobs=(short-sync opt-sync events-sync short-monitor opt-intraday)
# health/check.py owns the bot-level probe (health_checks rows, Health tab); here we only make
# sure the VM actually ran it. Its own findings are not re-checked.
[[ -f "$APP/health/check.py" ]] && other_jobs+=(health-check)

for job in "${critical_jobs[@]}" "${other_jobs[@]}"; do
  lvl=warning; for c in "${critical_jobs[@]}"; do [[ "$c" == "$job" ]] && lvl=critical; done
  st="$(job_status "$job")"
  case "$st" in
    ok) log "ok       $job" ;;
    skipped) [[ "$mode" == "shadow" ]] && log "skipped  $job (shadow)" || add "$lvl" "$(bot_of "$job")" vm_missed_run "$job was skipped in mode=$mode" ;;
    off) add "$lvl" "$(bot_of "$job")" vm_missed_run "$job ran in mode=off (mode changed today?)" ;;
    missing) add "$lvl" "$(bot_of "$job")" vm_missed_run "$job did not run today ($day) - no journal entry" ;;
    failed:*) add "$lvl" "$(bot_of "$job")" vm_missed_run "$job did not finish cleanly: ${st#failed:}" ;;
  esac
done

# 2b. VM-only checks health/check.py cannot see from inside a bot: every timer loaded and
#     waiting, and job-unit errors in today's journal.
for t in /etc/systemd/system/clive-*.timer; do
  [[ -e "$t" ]] || continue
  name="$(basename "$t")"
  systemctl is-active --quiet "$name" || add critical - vm_timer_inactive "timer $name is not active (sudo systemctl enable --now $name)"
done
errs="$(journalctl -q --no-pager -o cat -p err --since "@${day_start}" --until "@${day_end}" \
         -u 'clive-job@*' 2>/dev/null | grep -c . || true)"
[[ "${errs:-0}" -gt 0 ]] && log "journal: $errs error-priority lines from clive jobs today (journalctl -p err -u 'clive-job@*' --since today)"
if systemctl list-units --failed --no-legend --plain 'clive-*' 2>/dev/null | grep -q .; then
  add warning - vm_units_failed "failed units: $(systemctl list-units --failed --no-legend --plain 'clive-*' | awk '{print $1}' | tr '\n' ' ')"
fi

# 3. Supabase heartbeats (live only: in shadow the Mac writes them)
if [[ "$mode" == "live" ]]; then
  if [[ -n "$SB_URL" && -n "$SB_KEY" ]]; then
    bots="$(sb_curl GET "bots?select=id,run_state,last_run_status,last_run_at,last_sync_at,last_heartbeat&id=in.(shorting,options)" 2>/dev/null)" || bots=""
    if [[ -z "$bots" ]]; then
      add warning - vm_healthcheck "Supabase bots table unreachable at the 16:30 check"
    else
      since_today() {  # since_today ISO -> 0 if the timestamp is on/after today 00:00 ET
        [[ -n "$1" && "$1" != "null" ]] || return 1
        local s; s="$(date -d "$1" +%s 2>/dev/null)" || return 1
        [[ $s -ge $day_start ]]
      }
      f() { jq -r --arg id "$1" --arg k "$2" '.[] | select(.id == $id) | .[$k] // "null"' <<< "$bots"; }
      since_today "$(f shorting last_run_at)"  || add critical shorting vm_missed_run "bots.last_run_at for shorting not updated today (no daily.py run reported)"
      since_today "$(f shorting last_sync_at)" || add warning shorting vm_missed_run "bots.last_sync_at for shorting not updated today (16:15 sync)"
      since_today "$(f options last_heartbeat)" || add warning options vm_missed_run "bots.last_heartbeat for options not updated today"
      [[ "$(f shorting run_state)" == "error" ]] && add warning shorting vm_healthcheck "shorting bot run_state is error: see the dashboard"
      [[ "$(f options run_state)" == "error" ]] && add warning options vm_healthcheck "options bot run_state is error: see the dashboard"
    fi
  else
    log "Supabase not configured: heartbeat check skipped"
  fi
fi

# 4. The failure that started all this: any short still open after the close?
if [[ -n "$APCA_KEY" && -n "$APCA_SECRET" ]]; then
  pos="$(alpaca_get "/v2/positions" 2>/dev/null)" || pos=""
  if [[ -z "$pos" ]]; then
    add warning shorting vm_healthcheck "Alpaca positions unreachable at the 16:30 check"
  else
    shorts="$(jq -r '[.[] | select(.side == "short" or ((.qty|tonumber) < 0)) | .symbol] | join(",")' <<< "$pos")"
    [[ -n "$shorts" ]] && add critical shorting vm_shorts_open "Shorts still open after the close: ${shorts}. Cover before the next open."
  fi
fi

# 5. Report
if [[ ${#problems[@]} -eq 0 ]]; then
  log "all expected runs happened on $day"
  exit 0
fi
worst="warning"; body=""
for p in "${problems[@]}"; do
  IFS='|' read -r lvl bot kind msg <<< "$p"
  [[ "$lvl" == "critical" ]] && worst="critical"
  body+="- [$lvl] $msg"$'\n'
  sb_alert "$bot" "$lvl" "$kind" "$msg"
done
prio="high"; [[ "$worst" == "critical" ]] && prio="urgent"
ntfy "Clive 16:30 check: ${#problems[@]} problem(s)" "$prio" "$body"
exit 1
