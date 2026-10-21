"""Run (and disk-cache) every engine run of the grid with <= 2 worker processes."""
from __future__ import annotations

import multiprocessing as mp
import sys
import time

import common as C


def _job(t):
    sid, params, sym, cost = t[:4]
    cap = t[4] if len(t) > 4 else C.CAPITAL
    try:
        C.run_cached(sid, params, sym, cost, capital=cap)
        return None
    except Exception as e:          # noqa: BLE001
        return f"{sid} {sym} {cost}: {e!r}"


def run_tasks(tasks, workers=2):
    cap = lambda t: t[4] if len(t) > 4 else C.CAPITAL  # noqa: E731
    todo = [t for t in tasks if not (C.CACHE / f"{C._key(t[0], t[1], t[2], t[3], cap(t))}.pkl").exists()]
    seen, uniq = set(), []
    for t in todo:
        k = C._key(t[0], t[1], t[2], t[3], cap(t))
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    uniq.sort(key=lambda t: t[2])
    print(f"{len(tasks)} tasks, {len(uniq)} to run", flush=True)
    if not uniq:
        return []
    t0 = time.time()
    errs = []
    with mp.get_context("spawn").Pool(workers) as pool:
        for i, e in enumerate(pool.imap_unordered(_job, uniq, chunksize=1)):
            if e:
                errs.append(e)
                print("ERR", e, flush=True)
            if (i + 1) % 25 == 0:
                print(f"  {i + 1}/{len(uniq)} done, {time.time() - t0:.0f}s", flush=True)
    return errs


if __name__ == "__main__":
    from variants import GRID, baselines
    tasks = [(sid, p, s, "default") for (_, _, sid, p, syms) in GRID + baselines() for s in syms]
    errs = run_tasks(tasks, workers=int(sys.argv[1]) if len(sys.argv) > 1 else 2)
    print("errors:", len(errs))
