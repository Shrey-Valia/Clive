"""Bulk-prefetch option bars for the strike ranges the spreads grid can touch, in batches
of 100 symbols per request (the lazy path inside Market.select fetches 1-13 symbols per
request). Uses the exact cache keys of Market.bars(), so later runs are cache hits.
Single process, >= 1 s between requests (shared Alpaca budget)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa

from optionsbot.runner import client, market

ENTRY_LO, ENTRY_HI = pd.Timestamp(START), pd.Timestamp(END)


def plan(sym, put_lo=0.78, put_hi=1.01, call_lo=0.99, call_hi=1.12, dte_lo=12, dte_hi=72):
    m = market(sym)
    st = m.stock["close"]
    exps = sorted(set(m.expiries["P"]) | set(m.expiries["C"]))
    fri = {e for e in exps if e.weekday() == 4}
    todo = []
    c = client()
    for e in exps:
        if e.weekday() == 3 and (e + pd.Timedelta(days=1)) in fri:
            continue
        if e.weekday() not in (3, 4):
            continue
        a, b = max(ENTRY_LO, e - pd.Timedelta(days=dte_hi)), min(ENTRY_HI, e - pd.Timedelta(days=dte_lo))
        if a > b:
            continue
        s = st[a:b]
        if s.empty:
            continue
        lo, hi = s.min(), s.max()
        for cp, klo, khi in (("P", put_lo * lo, put_hi * hi), ("C", call_lo * lo, call_hi * hi)):
            g = m._by_exp.get((e, cp))
            if g is None:
                continue
            syms = g[(g.strike_price >= klo) & (g.strike_price <= khi)].symbol.tolist()
            start = max(pd.Timestamp("2024-01-01"), e - pd.Timedelta(days=450)).strftime("%Y-%m-%d")
            end = min(e, m.end).strftime("%Y-%m-%d")
            need = [x for x in syms if not c._cache_path("optbars", f"{x}|{start}|{end}").exists()]
            if need:
                todo.append((e, need))
    return m, todo


if __name__ == "__main__":
    syms = sys.argv[1:] or ["SPY", "QQQ", "IWM"]
    c = client()
    for sym in syms:
        m, todo = plan(sym)
        n = sum(len(x) for _, x in todo)
        print(sym, "expiries needing data:", len(todo), "symbols:", n, "~requests:",
              sum((len(x) + 99) // 100 for _, x in todo), flush=True)
        t0, r0 = time.time(), c.n_requests
        for i, (e, need) in enumerate(todo):
            m.bars(need, e)
            if (i + 1) % 20 == 0:
                print(f"  {sym} {i + 1}/{len(todo)} requests so far {c.n_requests - r0} "
                      f"({(c.n_requests - r0) / max(time.time() - t0, 1) * 60:.0f}/min)", flush=True)
        print(sym, "done, requests", c.n_requests - r0, flush=True)
