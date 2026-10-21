"""Steps 2 - the Fintel short page, rebuilt from the public sources Fintel republishes.

Fintel answers scripts with a Cloudflare 403, so each block comes from its source:

  short interest % float, days to cover, float  -> yfinance, FINRA backfill
  daily off-exchange short volume ratio          -> FINRA RegSHO daily files
  fails-to-deliver                               -> SEC semi-monthly files

Borrow fee has no free source. Borrow availability comes from Alpaca's
shortable / easy_to_borrow flags instead (see broker.py).
"""
from __future__ import annotations

import datetime as dt
import io
import os
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

FINRA_SI_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
FINRA_SHVOL_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
SEC_FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{period}.zip"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cache_path(name: str) -> str:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return os.path.join(config.CACHE_DIR, name)


# ----------------------------------------------------------------- short interest
def _from_yahoo(ticker: str) -> dict:
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {}
    pct = info.get("shortPercentOfFloat")
    date = info.get("dateShortInterest")
    rec = {
        "si_pct_float": round(pct * 100, 2) if pct else None,
        "days_to_cover": info.get("shortRatio"),
        "shares_short": info.get("sharesShort"),
        "float_shares": info.get("floatShares"),
        "si_date": dt.datetime.fromtimestamp(date, dt.timezone.utc).date().isoformat() if date else None,
    }
    return {k: v for k, v in rec.items() if v is not None}


def _from_finra(tickers: list[str]) -> dict[str, dict]:
    """One batched call; newest settlement row per symbol."""
    since = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    try:
        resp = requests.post(
            FINRA_SI_URL,
            json={
                "limit": 5000,
                "compareFilters": [{"fieldName": "settlementDate", "fieldValue": since, "compareType": "GTE"}],
                "domainFilters": [{"fieldName": "symbolCode", "values": list(tickers)}],
            },
            headers={"Content-Type": "application/json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception:
        return {}

    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}
    header = [h.strip('"') for h in lines[0].split(",")]
    newest: dict[str, dict] = {}
    for line in lines[1:]:
        cells = [c.strip('"') for c in line.split(",")]
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        sym, date = row.get("symbolCode"), row.get("settlementDate")
        if sym and date and (sym not in newest or date > newest[sym]["settlementDate"]):
            newest[sym] = row
    return {
        sym: {
            "shares_short": _num(r.get("currentShortPositionQuantity")),
            "days_to_cover": _num(r.get("daysToCoverQuantity")),
            "si_date": r.get("settlementDate"),
        }
        for sym, r in newest.items()
    }


def short_interest(tickers: list[str]) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        yahoo = dict(zip(tickers, pool.map(_from_yahoo, tickers)))
    finra = _from_finra(tickers)

    out = {}
    for t in tickers:
        y, f = yahoo.get(t) or {}, finra.get(t) or {}
        rec = {"si_pct_float": None, "days_to_cover": None, "shares_short": None,
               "float_shares": None, "si_date": None}
        rec.update(f)
        rec.update(y)
        # FINRA shares short over Yahoo float when Yahoo had no percentage.
        if rec["si_pct_float"] is None and rec["shares_short"] and rec["float_shares"]:
            rec["si_pct_float"] = round(100 * rec["shares_short"] / rec["float_shares"], 2)
        rec["si_source"] = "+".join(s for s, d in (("yahoo", y), ("finra", f)) if d) or None
        rec["si_age_days"] = (dt.date.today() - dt.date.fromisoformat(rec["si_date"])).days if rec["si_date"] else None
        out[t] = rec
    return out


# ----------------------------------------------------------------- short volume
def _shvol_day(day: dt.date) -> dict[str, tuple[float, float]]:
    stamp = day.strftime("%Y%m%d")
    path = _cache_path(f"shvol_{stamp}.txt")
    if os.path.exists(path):
        text = open(path, encoding="latin-1").read()
    else:
        try:
            r = requests.get(FINRA_SHVOL_URL.format(date=stamp), timeout=config.REQUEST_TIMEOUT)
        except Exception:
            return {}
        if r.status_code != 200 or not r.text.startswith("Date|"):
            return {}
        text = r.text
        with open(path, "w", encoding="latin-1") as fh:
            fh.write(text)
    out = {}
    for line in text.splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 5:
            sv, tv = _num(p[2]), _num(p[4])
            if sv is not None and tv:
                out[p[1]] = (sv, tv)
    return out


def short_volume(tickers: list[str]) -> dict[str, dict]:
    n = config.SHORT_VOLUME_LOOKBACK_DAYS
    days = [dt.date.today() - dt.timedelta(days=i) for i in range(1, n * 2 + 5)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(_shvol_day, days))
    sessions = [f for f in files if f][:n]

    out = {}
    for t in tickers:
        ratios = [100 * f[t][0] / f[t][1] for f in sessions if t in f]
        out[t] = {
            "short_vol_ratio_latest": round(ratios[0], 1) if ratios else None,
            "short_vol_ratio_avg": round(sum(ratios) / len(ratios), 1) if ratios else None,
        }
    return out


# ----------------------------------------------------------------- fails-to-deliver
def _ftd_periods() -> list[str]:
    out, today = [], dt.date.today()
    y, m = today.year, today.month
    for _ in range(config.FTD_LOOKBACK_MONTHS + 2):
        out += [f"{y}{m:02d}b", f"{y}{m:02d}a"]
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _ftd_file(period: str) -> bytes | None:
    path = _cache_path(f"cnsfails{period}.zip")
    if os.path.exists(path):
        return open(path, "rb").read()
    try:
        r = requests.get(SEC_FTD_URL.format(period=period),
                         headers={"User-Agent": config.SEC_USER_AGENT}, timeout=60)
    except Exception:
        return None
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        return None
    with open(path, "wb") as fh:
        fh.write(r.content)
    return r.content


def fails_to_deliver(tickers: list[str]) -> dict[str, dict]:
    wanted, rows, used = set(tickers), defaultdict(list), 0
    for period in _ftd_periods():
        blob = _ftd_file(period)
        if blob is None:
            continue
        used += 1
        try:
            z = zipfile.ZipFile(io.BytesIO(blob))
            with z.open(z.namelist()[0]) as fh:
                for line in fh:
                    p = line.decode("latin-1").strip().split("|")
                    if len(p) >= 4 and p[2] in wanted:
                        q = _num(p[3])
                        if q:
                            rows[p[2]].append((p[0], q))
        except Exception:
            continue
        if used >= config.FTD_LOOKBACK_MONTHS * 2:
            break

    out = {}
    for t in tickers:
        hits = rows.get(t, [])
        out[t] = {
            "ftd_days": len(hits),
            "ftd_max_shares": max((q for _, q in hits), default=None),
            "ftd_latest_date": max((d for d, _ in hits), default=None),
        }
    return out


def enrich(tickers: list[str]) -> dict[str, dict]:
    """Everything the Fintel short page would show, merged per ticker."""
    si, sv, ftd = short_interest(tickers), short_volume(tickers), fails_to_deliver(tickers)
    return {t: {**si.get(t, {}), **sv.get(t, {}), **ftd.get(t, {})} for t in tickers}
