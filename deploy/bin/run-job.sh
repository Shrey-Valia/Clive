#!/usr/bin/env bash
# run-job.sh JOB -- the one entry point every clive-job@JOB.service calls on the VM.
#
# It maps a job name to the exact command the Mac crontab runs, applies the VM mode
# (/etc/clive/mode), enforces a hang timeout, and sends output both to journald
# (stdout) and to the bot's own cron log (tee -a), like the Mac's ">> logs/cron.log 2>&1".
#
# Modes (set with: sudo clive-mode off|shadow|live):
#   off     every job exits 0 at once. The default after install, and the rollback state.
#   shadow  dry runs on the real schedule while the Mac still trades: --execute is dropped,
#           Supabase and ntfy are blanked, and jobs that can only write (sync, monitor) are
#           skipped. Nothing reaches Alpaca orders, Supabase or your phone.
#   live    the exact Mac commands, with --execute.
#
# Never prints secrets: it never reads the .env files; the bots read them themselves.
set -uo pipefail

APP="${CLIVE_APP:-/opt/clive/app}"
PY="${CLIVE_PY:-/opt/clive/venv/bin/python}"
MODE_FILE="${CLIVE_MODE_FILE:-/etc/clive/mode}"

job="${1:-}"
if [[ -z "$job" ]]; then
  echo "usage: run-job.sh JOB   (see the table in this file)" >&2
  exit 2
fi

mode="off"
if [[ -r "$MODE_FILE" ]]; then
  mode="$(tr -d '[:space:]' < "$MODE_FILE")"
fi
case "$mode" in off|shadow|live) ;; *) echo "run-job: bad mode '$mode' in $MODE_FILE, treating as off" >&2; mode="off" ;; esac

SHORT="$APP/Stock Shorting Bot"
OPT="$APP/Options Bot"
EVENTS="$APP/events"
HEALTH="$APP/health"

# job table -----------------------------------------------------------------------------------
# dir | log file (relative to dir) | timeout | live args | shadow args ("SKIP" = do not run)
# The live args are copied from `crontab -l` on the Mac (2026-10-22) and the bots' READMEs.
# Timeouts are hang protection only: each is well past the job's own cutoff
# (09:50 for --open, 15:58 / 12:58 for the cover, 16:00 for the options decision).
dir="" log="" tmo="" live_args=() shadow_args=()
case "$job" in
  short-open)            dir="$SHORT";  log="logs/cron.log";      tmo="45m"; live_args=(daily.py --open --execute);           shadow_args=(daily.py --open) ;;
  short-monitor)         dir="$SHORT";  log="logs/cron.log";      tmo="30m"; live_args=(monitor.py);                          shadow_args=(SKIP) ;;
  short-halfday)         dir="$SHORT";  log="logs/cron.log";      tmo="45m"; live_args=(daily.py --execute --half-day-only);  shadow_args=(daily.py --half-day-only) ;;
  short-close)           dir="$SHORT";  log="logs/cron.log";      tmo="45m"; live_args=(daily.py --execute);                  shadow_args=(daily.py) ;;
  short-sync)            dir="$SHORT";  log="logs/cron.log";      tmo="20m"; live_args=(sync.py);                             shadow_args=(SKIP) ;;
  # Safety nets from "Stock Shorting Bot/README.md" (Scheduling): 15:50 fallback if the 15:40 job
  # never started, 10:00/12:00/14:00 leftover covers, and the README's optional 12:50 half-day
  # fallback (does nothing on a regular day).
  short-fallback)        dir="$SHORT";  log="logs/cron.log";      tmo="30m"; live_args=(daily.py --execute --fallback);       shadow_args=(daily.py --fallback) ;;
  short-halfday-fallback) dir="$SHORT"; log="logs/cron.log";      tmo="30m"; live_args=(daily.py --execute --fallback --half-day-only); shadow_args=(daily.py --fallback --half-day-only) ;;
  short-cover-leftovers) dir="$SHORT";  log="logs/cron.log";      tmo="30m"; live_args=(daily.py --cover-leftovers --execute); shadow_args=(daily.py --cover-leftovers) ;;
  opt-sweep)             dir="$OPT";    log="live/logs/cron.log"; tmo="40m"; live_args=(live/run.py --sweep --execute);       shadow_args=(live/run.py --sweep) ;;
  opt-intraday)          dir="$OPT";    log="live/logs/cron.log"; tmo="40m"; live_args=(live/run.py --intraday --execute);    shadow_args=(live/run.py --intraday) ;;
  opt-halfday)           dir="$OPT";    log="live/logs/cron.log"; tmo="40m"; live_args=(live/run.py --execute);               shadow_args=(live/run.py) ;;
  opt-daily)             dir="$OPT";    log="live/logs/cron.log"; tmo="40m"; live_args=(live/run.py --execute);               shadow_args=(live/run.py) ;;
  opt-sync)              dir="$OPT";    log="live/logs/cron.log"; tmo="20m"; live_args=(live/sync.py);                        shadow_args=(SKIP) ;;
  events-sync)           dir="$EVENTS"; log="logs/sync.log";      tmo="20m"; live_args=(sync_events.py);                      shadow_args=(sync_events.py --dry-run) ;;
  # health/check.py (read-only probe -> Supabase health_checks rows + ntfy). Tagged with this
  # VM's hostname so its rows are told apart from the Mac's on the dashboard's Health tab.
  # In shadow mode it still writes its rows (they are host-tagged), but ntfy is blanked so the
  # phone does not get every alert twice while the Mac is also probing.
  health-check)          dir="$HEALTH"; log="logs/check.log";     tmo="5m";  live_args=(check.py);                            shadow_args=(check.py) ;;
  *) echo "run-job: unknown job '$job'" >&2; exit 2 ;;
esac

stamp() { TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z'; }

if [[ "$mode" == "off" ]]; then
  echo "run-job $job: mode=off, not running (sudo clive-mode shadow|live to turn on)"
  exit 0
fi

if [[ "$mode" == "live" ]]; then
  args=("${live_args[@]}")
else
  args=("${shadow_args[@]}")
  if [[ "$job" == "health-check" ]]; then
    export NTFY_TOPIC=""
  elif [[ "${args[0]}" == "SKIP" ]]; then
    echo "run-job $job: mode=shadow, skipped (this job only writes to Supabase or can trade)"
    exit 0
  else
    # Blank every outside write. The shorting bot's .env loader uses setdefault(), so an empty
    # value in the real environment wins. The options bot only lets a NON-empty process value
    # override its .env, so it gets an address that refuses at once; its db.py logs and carries on.
    if [[ "$dir" == "$OPT" ]]; then
      export SUPABASE_URL="http://127.0.0.1:9" SUPABASE_SERVICE_KEY="shadow-mode-no-writes"
    else
      export SUPABASE_URL="" SUPABASE_SERVICE_KEY=""
    fi
    export NTFY_TOPIC=""
  fi
fi

if [[ "$job" == "health-check" && ! -f "$dir/check.py" ]]; then
  echo "run-job $job: $dir/check.py not deployed yet - nothing to do"
  exit 0
fi
if [[ "$job" == "health-check" ]]; then
  # host = this VM's hostname. Passed both ways: --host if check.py has that flag, and env.
  export HEALTH_HOST CLIVE_HOST
  HEALTH_HOST="$(hostname)"; CLIVE_HOST="$HEALTH_HOST"
  if [[ -f "$dir/check.py" ]] && grep -q -- '"--host"' "$dir/check.py"; then
    args+=(--host "$HEALTH_HOST")
  fi
fi

if [[ ! -d "$dir" ]]; then
  echo "run-job $job: $dir is missing - run deploy/sync_code.sh from the Mac" >&2
  exit 1
fi
cd "$dir" || exit 1
mkdir -p "$(dirname "$log")"

# The newer shorting flags: refuse loudly if an older daily.py without them was synced.
for a in "${args[@]}"; do
  if [[ "$a" == "--fallback" || "$a" == "--cover-leftovers" ]]; then
    if ! grep -q -- "\"$a\"" daily.py; then
      echo "run-job $job: daily.py has no $a flag yet - disable this timer (sudo systemctl disable --now clive-$job.timer)" | tee -a "$log" >&2
      exit 1
    fi
  fi
done

export PYTHONUNBUFFERED=1
export TZ="${TZ:-America/New_York}"

echo "=== clive-job $job | mode=$mode | $(stamp) | ${args[*]} ===" | tee -a "$log"
timeout --kill-after=60s "$tmo" "$PY" "${args[@]}" 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
if [[ $rc -eq 124 || $rc -eq 137 ]]; then
  echo "=== clive-job $job TIMED OUT after $tmo (rc=$rc) | $(stamp) ===" | tee -a "$log"
else
  echo "=== clive-job $job exit $rc | $(stamp) ===" | tee -a "$log"
fi
exit "$rc"
