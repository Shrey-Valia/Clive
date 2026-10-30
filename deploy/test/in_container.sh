#!/usr/bin/env bash
# in_container.sh -- run INSIDE the test container by docker_test.sh, as root, with the repo
# copied (no .env files) to /root/clive-src. Exercises the deploy kit the way a fresh droplet
# would see it, then prints PASS/FAIL per step. Never touches Alpaca or Supabase: no keys exist.
set -uo pipefail
SRC=/root/clive-src
APP=/opt/clive/app
PY=/opt/clive/venv/bin/python
pass=0 fail=0 results=()
LOG=/root/clive-test.log
: > "$LOG"

step() {  # step NAME CMD...
  local name="$1"; shift
  printf '\n######## %s\n' "$name" | tee -a "$LOG"
  local t0=$SECONDS
  ( "$@" ) >> "$LOG" 2>&1   # subshell: a step's set -e must not leak
  local rc=$?
  tail -n 6 "$LOG" | sed 's/^/   | /'
  if [[ $rc -eq 0 ]]; then pass=$((pass + 1)); results+=("PASS  $name ($((SECONDS - t0))s)")
  else fail=$((fail + 1)); results+=("FAIL  $name (rc=$rc, $((SECONDS - t0))s)"); fi
}

sync_like_mac() {  # what deploy/sync_code.sh copies, minus rsync over ssh
  set -e
  install -d -o clive -g clive "$APP"
  cd "$SRC"
  cp -a "Stock Shorting Bot" events health deploy "$APP/"
  mkdir -p "$APP/Options Bot"
  cp -a "Options Bot/live" "Options Bot/optionsbot" "Options Bot/requirements.txt" "$APP/Options Bot/"
  rm -rf "$APP/Stock Shorting Bot/logs" "$APP/Options Bot/live/logs" "$APP/Options Bot/live/state"
  chown -R clive:clive "$APP"
  echo "test-sync $(date -u +%FT%TZ)" > "$APP/CODE_VERSION"
}

lint() {
  set -e
  cd "$SRC/deploy"
  local files=(bootstrap.sh sync_code.sh secrets.sh migrate_state.sh mac_cron.sh dryrun.sh healthcheck.sh lib.sh
               bin/run-job.sh bin/clive-mode test/docker_test.sh test/in_container.sh test/x86_wheels.sh)
  [[ -f shadow_compare.sh ]] && files+=(shadow_compare.sh)
  for f in "${files[@]}"; do bash -n "$f"; done
  echo "bash -n ok: ${#files[@]} scripts"
  shellcheck -S warning -x "${files[@]}"
  echo "shellcheck -S warning ok"
}

verify_units() {
  set -e
  cd /etc/systemd/system
  local units=(clive-*.timer clive-alive.service clive-healthcheck.service clive-job@short-close.service clive-alert@short-close.service)
  systemd-analyze verify "${units[@]}"
  echo "systemd-analyze verify ok: ${#units[@]} units"
  grep -h '^OnCalendar=' clive-*.timer | sort -u | while IFS= read -r l; do
    systemd-analyze calendar --iterations=1 "${l#OnCalendar=}" | sed -n '2p;4p'
  done
}

timers_et() {
  set -e
  local n; n="$(systemctl list-timers 'clive-*' --all --no-legend | wc -l)"
  systemctl list-timers 'clive-*' --all --no-pager | head -n 25
  [[ $n -ge 17 ]] || { echo "only $n timers"; return 1; }
  # the 15:40 close must fire at 15:40 New York time whatever the system clock zone is
  local nx; nx="$(systemctl show clive-short-close.timer -p NextElapseUSecRealtime --value)"   # "Fri 2026-10-23 19:40:00 UTC"
  echo "next short-close: $nx -> $(TZ=America/New_York date -d "$nx" '+%a %H:%M %Z')"
  [[ "$(TZ=America/New_York date -d "$nx" +%H:%M)" == 15:40 ]]
  nx="$(systemctl show clive-opt-daily.timer -p NextElapseUSecRealtime --value)"
  echo "next opt-daily:   $nx -> $(TZ=America/New_York date -d "$nx" '+%a %H:%M %Z')"
  [[ "$(TZ=America/New_York date -d "$nx" +%H:%M)" == 15:45 ]]
}

job_modes() {
  set -e
  clive-mode off >/dev/null
  systemctl start clive-job@short-close.service
  out="$(journalctl -u clive-job@short-close --no-pager -n 5)"     # no grep -q in a pipe: SIGPIPE + pipefail
  grep 'mode=off, not running' <<< "$out"
  echo "off: short-close is a no-op"
  clive-mode shadow
  systemctl start clive-job@short-sync.service
  out="$(journalctl -u clive-job@short-sync --no-pager -n 5)"
  grep 'mode=shadow, skipped' <<< "$out"
  echo "shadow: short-sync skipped (write-only job)"
  out="$(clive-mode live 2>&1 || true)"
  grep 'Refusing' <<< "$out" && echo "live refused before migrate_state push"
  [[ "$(cat /etc/clive/mode)" == shadow ]]
  clive-mode off >/dev/null
}

on_failure_hook() {
  set -e
  clive-mode shadow >/dev/null                 # in mode off the hook stays quiet on purpose
  systemctl start clive-job@no-such-job.service || true
  local out=""
  for _ in $(seq 1 30); do
    out="$(journalctl -t clive-alert --no-pager 2>/dev/null || true)"
    grep -q 'would send: \[VM shadow\] Clive job failed: no-such-job' <<< "$out" && break
    sleep 1
  done
  tail -n 4 <<< "$out"
  grep 'Clive job failed: no-such-job' <<< "$out"
  clive-mode off >/dev/null
  systemctl reset-failed 'clive-job@no-such-job.service' 'clive-alert@no-such-job.service' 2>/dev/null || true
}

ssh_as_clive() {
  set -e
  systemctl restart ssh
  ssh -q -i /root/.ssh/test_key -o StrictHostKeyChecking=no -o BatchMode=yes clive@127.0.0.1 'echo "ssh as $(whoami) ok"; id'
  # passwords off despite the 50-cloud-init.conf drop-in
  local t; t="$(sshd -T)"
  grep -x 'passwordauthentication no' <<< "$t"
  grep -xE 'permitrootlogin (without-password|prohibit-password)' <<< "$t"
}

probe_linux() {
  set -e
  cd "$APP/health"
  sudo -u clive env CLIVE_MODE_FILE=/etc/clive/mode "$PY" check.py --dry-run --readiness --only host,readiness,supabase | tee /tmp/probe.txt
  grep -E '^host +memory ' /tmp/probe.txt
  grep -E '^host +swap ' /tmp/probe.txt
  grep -E '^host +vm_mode ' /tmp/probe.txt
  grep -E '^readiness +readiness.trading_host' /tmp/probe.txt     # vm-off: not the trading host
}

offline_tests() {
  set -e
  # Some options tests read a shorting .env to prove its keys never reach the trading host.
  # Fake, obviously-invalid values; removed again below. Never real keys.
  local f made=()
  for f in "$APP/Stock Shorting Bot/.env" "$APP/Options Bot/.env"; do
    if [[ ! -e "$f" ]]; then
      printf 'APCA_API_KEY_ID=PKTESTONLY000000\nAPCA_API_SECRET_KEY=test-only-not-a-key\nAPCA_PAPER=true\n' > "$f"
      printf 'OPT_APCA_API_KEY_ID=PKTESTOPT0000000\nOPT_APCA_API_SECRET_KEY=test-only-not-a-key\nOPT_APCA_PAPER=true\n' >> "$f"
      chown clive:clive "$f"; chmod 600 "$f"; made+=("$f")
    fi
  done
  trap 'rm -f "${made[@]}"' RETURN
  cd "$APP/Stock Shorting Bot" && sudo -u clive "$PY" -m pytest -q -p no:cacheprovider tests 2>&1 | tail -n 3
  cd "$APP/Options Bot" && sudo -u clive "$PY" -m pytest -q -p no:cacheprovider live/tests 2>&1 | tail -n 3
  cd "$APP" && sudo -u clive "$PY" -m pytest -q -p no:cacheprovider health/tests 2>&1 | tail -n 3
  if [[ -d "$APP/deploy/tests" ]]; then cd "$APP" && sudo -u clive "$PY" -m pytest -q -p no:cacheprovider deploy/tests 2>&1 | tail -n 3; fi
}

mem_report() { free -m; ps -eo rss,comm --sort=-rss | head -n 8; du -sh /opt/clive/venv /opt/clive/python; }

echo "container: $(uname -m), $(. /etc/os-release; echo "$PRETTY_NAME"), $(nproc) cpu, $(free -m | awk '/Mem:/ {print $2}') MB"
step "lint: bash -n + shellcheck on every script" lint
step "bootstrap --system (fresh, admin root)" bash "$SRC/deploy/bootstrap.sh" --system
step "copy the code like sync_code.sh" sync_like_mac
step "bootstrap --app (venv, pinned requirements, units)" bash "$APP/deploy/bootstrap.sh" --app
step "bootstrap again, no flag (idempotent)" bash "$APP/deploy/bootstrap.sh"
step "bootstrap --verify" bash "$APP/deploy/bootstrap.sh" --verify
step "systemd-analyze verify + calendar" verify_units
step "17 timers loaded, 15:40 fires at 15:40 New York" timers_et
step "run-job modes: off / shadow / live refused" job_modes
step "OnFailure -> clive-alert@ -> healthcheck.sh --failed" on_failure_hook
step "ssh as clive with root's key; passwords off" ssh_as_clive
step "health probe on Linux (memory, swap, vm_mode, readiness)" probe_linux
step "offline test suites in the VM venv" offline_tests
step "memory / disk footprint" mem_report

echo; echo "==== $(uname -m): $pass pass, $fail fail (full log $LOG)"
printf '  %s\n' "${results[@]}"
[[ $fail -eq 0 ]]
