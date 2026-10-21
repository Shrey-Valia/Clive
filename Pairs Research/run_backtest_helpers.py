"""Earnings reaction-day lookup from SEC EDGAR 8-K Item 2.02 filing dates (.cache/earnings_dates.csv)."""
import numpy as np
import pandas as pd
import pairs_lib as L

dates = L.load_panel()["dates"]
EARN = {}
_ef = L.CACHE / "earnings_dates.csv"
if _ef.exists():
    _e = pd.read_csv(_ef)
    for sym, g_ in _e.groupby("symbol"):
        # filing day (BMO reaction) and the next trading day (AMC reaction) are both marked
        ix = np.searchsorted(dates.values, pd.to_datetime(g_.filing_date).values, side="left")
        EARN[sym] = np.unique(np.r_[ix, ix + 1])


def earn_between(sym, i0, i1):
    e = EARN.get(sym)
    if e is None:
        return False
    k = np.searchsorted(e, i0, side="left")
    return k < len(e) and e[k] <= i1
