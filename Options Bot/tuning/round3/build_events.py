"""Build tuning/round3/events.csv: FOMC statement days 2007-2026 and CPI release days 2007-2026.

Sources
* FOMC 2007-2020: federalreserve.gov/monetarypolicy/fomchistoricalYYYY.htm (scheduled meetings;
  the statement day is the last day of the meeting). 2021-2023: fomccalendars.htm. 2024-2026:
  tuning/events/macro_dates.csv.
* CPI 2007-2023: bls.gov/bls/news-release/cpi.htm (archive of CPI news releases, read with
  WebFetch on 2026-10-23 into dl/cpi_2007_2023.txt). 2024-2026: tuning/events/macro_dates.csv.
"""
import re
import sys
from pathlib import Path

import pandas as pd

DL = Path(sys.argv[1])
HERE = Path(__file__).resolve().parent
MON = {m: i + 1 for i, m in enumerate("January February March April May June July August September "
                                       "October November December".split())}
rows = []
for y in range(2007, 2021):
    t = re.sub(r"<[^>]*>", "|", (DL / f"fomc{y}.htm").read_text(errors="ignore"))
    t = re.sub(r"[|\s]+", " ", t)
    for m in re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|"
                         r"December)(?:/(\w+))? ([0-9]+)(?:-([0-9]+))? Meeting - (\d{4})", t):
        mon1, mon2, d1, d2, yy = m.groups()
        ab = {k[:3]: v for k, v in MON.items()}
        mon = MON.get(mon2) or ab.get((mon2 or "")[:3]) or MON[mon1]
        day = int(d2 or d1)
        rows.append(("FOMC", pd.Timestamp(int(yy), mon, day)))
# meetings spanning two months print as "Feb 31-1" on those pages (missed by the regex), plus the
# two unscheduled 2020 cuts (03-03, and Sunday 03-15 -> first trading day 03-16)
for d in ("2017-02-01", "2017-11-01", "2018-08-01", "2020-03-03", "2020-03-16"):
    rows.append(("FOMC", pd.Timestamp(d)))
t = re.sub(r"<[^>]*>", "|", (DL / "fomccal.htm").read_text(errors="ignore"))
t = re.sub(r"[|\s]+", " ", t)
# panels: "2021 FOMC Meetings ... January 26-27 ... "
for yb in re.finditer(r"(20\d\d) FOMC Meetings(.*?)(?=20\d\d FOMC Meetings|$)", t):
    yy = int(yb.group(1))
    if not 2021 <= yy <= 2023:
        continue
    for m in re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|"
                         r"December|Jan/Feb|Apr/May|Oct/Nov)\s+([0-9]+)-([0-9]+)\*?", yb.group(2)):
        mon_s, d1, d2 = m.groups()
        mon = {"Jan/Feb": 2, "Apr/May": 5, "Oct/Nov": 11}.get(mon_s) or MON[mon_s]
        rows.append(("FOMC", pd.Timestamp(yy, mon, int(d2))))
for d in (DL / "cpi_2007_2023.txt").read_text().split():
    rows.append(("CPI", pd.Timestamp(d)))
md = pd.read_csv(HERE.parent / "events" / "macro_dates.csv")
for _, r in md.iterrows():
    rows.append((r["event"], pd.Timestamp(r["date"])))
ev = pd.DataFrame(rows, columns=["event", "date"]).drop_duplicates().sort_values("date")
ev.to_csv(HERE / "events.csv", index=False)
print(ev.groupby([ev.event, ev.date.dt.year]).size().unstack(0).to_string())
