#!/usr/bin/env bash
# docker_test.sh -- test the deploy kit in a throwaway Ubuntu 24.04 container with systemd as
# PID 1 (a stand-in for a fresh DigitalOcean / Hetzner droplet, admin user root). Run on the Mac:
#
#   deploy/test/docker_test.sh            amd64 (the droplet's arch) and arm64
#   deploy/test/docker_test.sh amd64      one arch only
#   KEEP=1 deploy/test/docker_test.sh     leave the container running to poke at it
#
# It copies the git-tracked files only (no .env, no logs, no state), runs bootstrap.sh, a
# sync-like copy, bootstrap.sh --app (real pip installs), --verify, systemd-analyze, the run-job
# modes, the OnFailure hook, SSH as clive, the health probe and the offline test suites.
# Not covered in a container: ufw/iptables, swapon, chrony setting the clock, real reboots.
# Everything it creates (containers, images) is removed at the end unless KEEP=1.
set -euo pipefail
DOCKER="${DOCKER:-docker}"
command -v "$DOCKER" >/dev/null 2>&1 || DOCKER=/usr/local/bin/docker
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
archs=("$@"); [[ ${#archs[@]} -gt 0 ]] || archs=(amd64 arm64)
rc_all=0

for arch in "${archs[@]}"; do
  if [[ "$arch" == amd64 && "$(uname -m)" == arm64 ]]; then
    # uv (Rust) segfaults under qemu-user, so a full systemd run of bootstrap.sh is not possible
    # here. Prove the x86_64 Python stack instead; the systemd/bootstrap half is arch-neutral bash.
    echo "=== amd64 on an arm64 Mac: x86_64 wheels + test suites (deploy/test/x86_wheels.sh)"
    if "$HERE/x86_wheels.sh"; then echo "=== amd64: PASS (wheels)"; else echo "=== amd64: FAIL (wheels)"; rc_all=1; fi
    continue
  fi
  img="clive-vm-test:$arch" name="clive-vm-test-$arch"
  echo "=== $arch: building $img"
  "$DOCKER" build -q --platform "linux/$arch" -t "$img" "$HERE" >/dev/null
  "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true
  "$DOCKER" run -d --name "$name" --hostname clive-vm-test --platform "linux/$arch" \
    --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    --tmpfs /run --tmpfs /run/lock --memory 1g --memory-swap 3g "$img" >/dev/null
  for _ in $(seq 1 60); do
    s="$("$DOCKER" exec "$name" systemctl is-system-running 2>/dev/null || true)"
    [[ "$s" == running || "$s" == degraded ]] && break
    sleep 2
  done
  echo "=== $arch: systemd $s; copying the tracked files (no .env)"
  (cd "$REPO" && git ls-files -z | grep -zv -E '(^|/)\.env($|\.)' | COPYFILE_DISABLE=1 xargs -0 tar --no-xattrs --no-mac-metadata -cf - ) \
    | "$DOCKER" exec -i "$name" bash -c 'mkdir -p /root/clive-src && tar -xf - -C /root/clive-src'
  # untracked new files of this round (when run before a commit)
  (cd "$REPO" && git ls-files -z --others --exclude-standard -- deploy health | grep -zv -E '(^|/)\.env($|\.)' | COPYFILE_DISABLE=1 xargs -0 tar --no-xattrs --no-mac-metadata -cf - ) \
    | "$DOCKER" exec -i "$name" bash -c 'tar -xf - -C /root/clive-src 2>/dev/null || true'
  if "$DOCKER" exec "$name" bash /root/clive-src/deploy/test/in_container.sh; then
    echo "=== $arch: PASS"
  else
    echo "=== $arch: FAIL (log: docker exec $name cat /root/clive-test.log; KEEP=1 keeps it)"
    rc_all=1
  fi
  if [[ "${KEEP:-0}" != 1 ]]; then
    "$DOCKER" rm -f "$name" >/dev/null
    "$DOCKER" rmi "$img" >/dev/null 2>&1 || true
  fi
done
exit $rc_all
