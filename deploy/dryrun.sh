#!/usr/bin/env bash
# dryrun.sh -- the pre-cutover checklist, run ON THE VM. Sends no orders and writes nothing
# to Supabase (except --check, which only reads). Safe while the Mac is still trading.
#
#   sudo -u clive /opt/clive/app/deploy/dryrun.sh           all steps
#   sudo -u clive /opt/clive/app/deploy/dryrun.sh --tests   also the bots' offline pytest suites
#
# Each step prints PASS/FAIL; the full output goes to /opt/clive/dryrun-<time>.log.
# Read that log the way you would read the Mac's cron.log: the dry run should rank 20 names,
# list the covers it would send, and save picks to logs/next_open_picks.dry_run.json.
set -uo pipefail
APP=/opt/clive/app
PY=/opt/clive/venv/bin/python
export TZ=America/New_York PYTHONUNBUFFERED=1

if [[ "$(id -un)" != clive ]]; then
  exec sudo -u clive -H "$0" "$@"
fi

LOG="/opt/clive/dryrun-$(date +%Y%m%d-%H%M%S).log"
tests=0; [[ "${1:-}" == "--tests" ]] && tests=1
pass=0 fail=0 results=()

step() {  # step NAME DIR TIMEOUT CMD...
  local name="$1" dir="$2" tmo="$3"; shift 3
  echo; echo "### $name" | tee -a "$LOG"
  echo "    (cd '$dir' && $*)" | tee -a "$LOG"
  ( cd "$dir" && timeout "$tmo" "$@" ) >> "$LOG" 2>&1
  local rc=$?
  tail -n 8 "$LOG" | sed 's/^/    | /'
  if [[ $rc -eq 0 ]]; then pass=$((pass + 1)); results+=("PASS  $name")
  else fail=$((fail + 1)); results+=("FAIL  $name (rc=$rc)"); fi
}

S="$APP/Stock Shorting Bot"; O="$APP/Options Bot"; E="$APP/events"
# Supabase blanked: the shorting loader keeps an empty real-env value (setdefault); the options
# loader only takes a non-empty override, so it gets an address that refuses at once.
NOSB_SHORT=(env SUPABASE_URL= SUPABASE_SERVICE_KEY= NTFY_TOPIC=)
NOSB_OPT=(env SUPABASE_URL=http://127.0.0.1:9 SUPABASE_SERVICE_KEY=dryrun NTFY_TOPIC=)

echo "dry-run checklist $(date '+%F %T %Z') on $(hostname), mode=$(cat /etc/clive/mode 2>/dev/null || echo off)" | tee "$LOG"

step "environment: python, clock, secrets present" "$APP" 60 bash -c '
  set -e
  '"$PY"' -c "import sys; assert sys.version_info >= (3, 12), sys.version; print(sys.version)"
  '"$PY"' -c "import alpaca, yfinance, pandas, numpy, requests; print(\"imports ok\")"
  timedatectl show -p Timezone -p NTPSynchronized
  [ "$(timedatectl show -p Timezone --value)" = UTC ] || { echo "system timezone is not UTC"; exit 1; }
  echo "ET now: $(TZ=America/New_York date)"
  chronyc tracking | sed -n "1,4p"
  for f in "Stock Shorting Bot/.env" "Options Bot/.env"; do
    [ -f "$f" ] || { echo "missing $f (run deploy/secrets.sh on the Mac)"; exit 1; }
    p=$(stat -c %a "$f"); [ "$p" = 600 ] || { echo "$f is $p, want 600"; exit 1; }
    echo "$f: present, 600"
  done'

step "shorting: daily.py --now (15:40 job, dry, Supabase blanked)" "$S" 15m \
  "${NOSB_SHORT[@]}" "$PY" daily.py --now
step "shorting: daily.py --open --now (09:31 job, dry, Supabase blanked)" "$S" 10m \
  "${NOSB_SHORT[@]}" "$PY" daily.py --open --now
step "shorting: monitor.py --now (alerts only, Supabase/ntfy blanked)" "$S" 5m \
  "${NOSB_SHORT[@]}" "$PY" monitor.py --now
step "shorting: daily.py --cover-leftovers (dry: which shorts are leftovers)" "$S" 5m \
  "${NOSB_SHORT[@]}" "$PY" daily.py --cover-leftovers
step "options: live/run.py --check (health check)" "$O" 5m \
  "$PY" live/run.py --check
step "options: live/run.py --now --preview (dry, Supabase blocked)" "$O" 15m \
  "${NOSB_OPT[@]}" "$PY" live/run.py --now --preview
step "events: sync_events.py --dry-run" "$E" 10m \
  "$PY" sync_events.py --dry-run
step "timers: every OnCalendar parses (systemd-analyze calendar)" "$APP" 60 bash -c '
  set -e
  grep -h "^OnCalendar=" deploy/systemd/*.timer | sort -u | while IFS= read -r line; do
    c="${line#OnCalendar=}"
    TZ=America/New_York systemd-analyze calendar --iterations=2 "$c" | grep -E "Normalized|Next elapse|Iter"
  done'
step "timers: all clive timers loaded, next runs in ET" "$APP" 30 bash -c '
  n=$(systemctl list-timers "clive-*" --all --no-legend | wc -l); echo "$n clive timers"
  systemctl list-timers "clive-*" --all --no-pager
  [ "$n" -ge 17 ]'
step "healthcheck: --alive --dry and --daily --dry" "$APP" 2m bash -c '
  deploy/healthcheck.sh --alive --dry && { deploy/healthcheck.sh --daily --dry; rc=$?; echo "daily rc=$rc (1 = problems found; expected before the first full day)"; true; }'

if [[ $tests -eq 1 ]]; then
  step "shorting: offline tests" "$S" 15m "$PY" -m pytest -q tests
  step "options: offline tests" "$O" 15m "$PY" -m pytest -q live/tests
fi

echo; echo "==== summary ($pass pass, $fail fail) - full log: $LOG"
printf '  %s\n' "${results[@]}"
[[ $fail -eq 0 ]]
