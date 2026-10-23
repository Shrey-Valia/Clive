#!/usr/bin/env bash
# x86_wheels.sh -- prove every Python dependency installs and runs on x86_64 (a DigitalOcean /
# Hetzner droplet) from an Apple-silicon Mac, where uv itself crashes under qemu-user emulation.
#
#   1. native arm64 container: uv downloads the x86_64 CPython 3.13 build and installs the exact
#      pinned requirements for x86_64-manylinux_2_28 / cp313 into a volume (wheels only for the
#      compiled packages, as bootstrap.sh does);
#   2. emulated linux/amd64 Ubuntu 24.04 container: that x86_64 Python imports everything and runs
#      the bots' offline test suites.
# Called by docker_test.sh for amd64 on an arm64 host. Cleans up its containers and volume.
set -euo pipefail
DOCKER="${DOCKER:-docker}"
command -v "$DOCKER" >/dev/null 2>&1 || DOCKER=/usr/local/bin/docker
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VOL="clive-x86-wheels-$$"
cleanup() { "$DOCKER" volume rm -f "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT
"$DOCKER" volume create "$VOL" >/dev/null

echo "--- x86: staging the code (tracked files, no .env)"
(cd "$REPO" && { git ls-files -z; git ls-files -z --others --exclude-standard -- deploy health; } \
   | grep -zv -E '(^|/)\.env($|\.)' | COPYFILE_DISABLE=1 xargs -0 tar --no-xattrs --no-mac-metadata -cf - ) \
  | "$DOCKER" run --rm -i -v "$VOL:/x" ubuntu:24.04 sh -c 'mkdir -p /x/src && tar -xf - -C /x/src 2>/dev/null; ls /x/src | head -3'

echo "--- x86: resolving + downloading x86_64 wheels on native arm64"
"$DOCKER" run --rm -v "$VOL:/x" --platform linux/arm64 ubuntu:24.04 bash -c '
  set -euo pipefail
  apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
  export UV_NO_CONFIG=1 UV_PYTHON_INSTALL_DIR=/x/python
  uv python install 3.13 cpython-3.13-linux-x86_64-gnu 2>&1 | tail -n 2
  cd /x/src
  uv pip install --python 3.13 --target /x/site --python-platform x86_64-manylinux_2_28 --python-version 3.13 \
    -r "Stock Shorting Bot/requirements.txt" -r "Options Bot/requirements.txt" -r deploy/requirements-vm.txt \
    -c deploy/constraints-vm.txt \
    --only-binary numpy,pandas,pydantic-core,curl-cffi,msgpack,websockets,frozendict,charset-normalizer 2>&1 | tail -n 4
  echo "x86_64 extension modules: $(find /x/site -name "*x86_64*.so" | wc -l)"
  echo "aarch64 extension modules (must be 0): $(find /x/site -name "*aarch64*.so" | wc -l)"
  [ "$(find /x/site -name "*aarch64*.so" | wc -l)" -eq 0 ]
'

echo "--- x86: importing + running the offline suites under linux/amd64 (qemu, slow)"
"$DOCKER" run --rm -v "$VOL:/x" --platform linux/amd64 ubuntu:24.04 bash -c '
  set -euo pipefail
  PY="$(ls -d /x/python/cpython-3.13*-linux-x86_64-gnu/bin/python3.13 | head -n 1)"
  export PYTHONPATH=/x/site PYTHONDONTWRITEBYTECODE=1 TZ=America/New_York
  "$PY" -c "import platform, sys; print(\"python\", sys.version.split()[0], platform.machine()); assert platform.machine() == \"x86_64\""
  "$PY" -c "
import importlib.metadata as md
import alpaca, yfinance, pandas, numpy, requests, curl_cffi, pydantic_core, msgpack, websockets, peewee, frozendict
print(\"imports ok:\", \", \".join(f\"{p} {md.version(p)}\" for p in (\"alpaca-py\", \"yfinance\", \"pandas\", \"numpy\", \"curl_cffi\", \"pydantic\")))
import pandas as pd, numpy as np
s = pd.Series(np.arange(5.0)).rolling(2).mean(); assert s.iloc[-1] == 3.5
"
  cd "/x/src/Stock Shorting Bot"
  for f in "/x/src/Stock Shorting Bot/.env" "/x/src/Options Bot/.env"; do   # fake values, test only
    printf "APCA_API_KEY_ID=PKTESTONLY000000\nAPCA_API_SECRET_KEY=test-only\nAPCA_PAPER=true\nOPT_APCA_API_KEY_ID=PKTESTOPT0000000\nOPT_APCA_API_SECRET_KEY=test-only\nOPT_APCA_PAPER=true\n" > "$f"
  done
  "$PY" -m pytest -q -p no:cacheprovider tests 2>&1 | tail -n 1
  cd "/x/src/Options Bot" && "$PY" -m pytest -q -p no:cacheprovider live/tests 2>&1 | tail -n 1
  cd /x/src && "$PY" -m pytest -q -p no:cacheprovider health/tests deploy/tests 2>&1 | tail -n 1
'
echo "--- x86: PASS"
