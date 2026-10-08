"""Synthetic data for `python run.py --demo`.

Used to preview the dashboard layout when the data sources are unreachable.
Values are seeded random walks around plausible levels - NOT market data - and the
rendered page carries a DEMO banner. Demo data lives in data/demo.sqlite and never
touches the real cache.
"""
from __future__ import annotations

import zlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import sources
from .store import Store

# code -> (level, daily vol, kind, lag in business days, freq)
#   kind: "add" = vol in level units (yields/spreads), "mult" = vol in % (prices)
P = {
    "DGS1MO": (3.90, .02, "add", 1, "D"), "DGS3MO": (3.86, .02, "add", 1, "D"),
    "DGS6MO": (3.76, .025, "add", 1, "D"), "DGS1": (3.62, .03, "add", 1, "D"),
    "DGS2": (3.52, .05, "add", 1, "D"), "DGS5": (3.68, .055, "add", 1, "D"),
    "DGS7": (3.88, .055, "add", 1, "D"), "DGS10": (4.11, .055, "add", 1, "D"),
    "DGS20": (4.66, .05, "add", 1, "D"), "DGS30": (4.74, .05, "add", 1, "D"),
    "DFII10": (1.78, .045, "add", 1, "D"), "T10YIE": (2.33, .025, "add", 1, "D"),
    "T5YIFR": (2.24, .025, "add", 1, "D"), "DFF": (3.88, .0, "add", 1, "D"),
    "secured/sofr": (3.91, .01, "add", 1, "D"), "ACMTP10": (0.78, .03, "add", 2, "D"),
    "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A": (2.66, .045, "add", 1, "D"),
    "IRS/M.FR.L.L40.CI.0000.EUR.N.Z": (3.38, .12, "add", 0, "M"),
    "IRS/M.IT.L.L40.CI.0000.EUR.N.Z": (3.47, .14, "add", 0, "M"),
    "IUDMNPY": (4.63, .05, "add", 1, "D"), "10Y": (1.71, .025, "add", 1, "D"),
    "BAMLC0A0CM": (.81, .01, "add", 1, "D"), "BAMLH0A0HYM2": (2.92, .04, "add", 1, "D"),
    "BAMLC0A1CAAA": (.36, .008, "add", 1, "D"), "BAMLC0A2CAA": (.47, .008, "add", 1, "D"),
    "BAMLC0A3CA": (.66, .009, "add", 1, "D"), "BAMLC0A4CBBB": (1.02, .011, "add", 1, "D"),
    "BAMLH0A1HYBB": (1.83, .03, "add", 1, "D"), "BAMLH0A2HYB": (3.04, .04, "add", 1, "D"),
    "BAMLH0A3HYC": (8.6, .12, "add", 1, "D"), "BAMLEMCBPIOAS": (1.62, .02, "add", 1, "D"),
    "VIXCLS": (17.2, 6.0, "mult", 1, "D"), "NFCI": (-.52, .015, "add", 0, "W-FRI"),
    "WALCL": (6_540_000, .15, "mult", 0, "W-WED"), "WTREGEN": (842_000, 4.0, "mult", 0, "W-WED"),
    "RRPONTSYD": (21.0, 9.0, "mult", 1, "D"), "DTWEXBGS": (121.4, .3, "mult", 4, "D"),
    "DEXUSEU": (1.163, .4, "mult", 4, "D"), "DEXJPUS": (149.2, .55, "mult", 4, "D"),
    "DEXCHUS": (7.12, .12, "mult", 4, "D"), "DEXMXUS": (18.42, .6, "mult", 4, "D"),
    "DEXBZUS": (5.36, .8, "mult", 4, "D"), "DEXINUS": (88.6, .2, "mult", 4, "D"),
    "TRY=X": (42.6, .25, "mult", 1, "D"),
    "DCOILBRENTEU": (66.4, 1.8, "mult", 2, "D"), "DCOILWTICO": (62.3, 1.9, "mult", 2, "D"),
    "DHHNGSP": (3.21, 3.5, "mult", 2, "D"), "TTF=F": (31.8, 2.6, "mult", 1, "D"),
    "GC=F": (4105.0, 1.1, "mult", 1, "D"), "HG=F": (4.92, 1.4, "mult", 1, "D"),
    "ZW=F": (538.0, 1.6, "mult", 1, "D"),
    "SP500": (6890.0, .9, "mult", 1, "D"), "NASDAQCOM": (23480.0, 1.2, "mult", 1, "D"),
    "^RUT": (2452.0, 1.4, "mult", 1, "D"), "NIKKEI225": (45980.0, 1.3, "mult", 1, "D"),
    "^STOXX": (571.0, .85, "mult", 1, "D"), "EEM": (52.4, 1.0, "mult", 1, "D"),
    "RSP": (189.6, .85, "mult", 1, "D"), "SPY": (687.2, .9, "mult", 1, "D"),
    "GDPNOW": (2.4, .08, "add", 1, "D"), "daily:GPRD_MA7": (128.0, 3.0, "mult", 2, "D"),
    "daily:GPRD": (131.0, 12.0, "mult", 2, "D"), "monthly:GPR": (124.0, 6.0, "mult", 0, "M"),
}
for etf, lvl in dict(XLK=288, XLF=53.1, XLE=89.4, XLV=141, XLI=152, XLY=232, XLP=79.8,
                     XLU=86.5, XLB=91.2, XLRE=42.7, XLC=114).items():
    P[etf] = (lvl, 1.2, "mult", 1, "D")

# One-day shocks on the latest observation (in daily-vol multiples) so the
# "What moved" strip and z-score highlighting have something to show.
SHOCKS = {"DCOILBRENTEU": 3.1, "DCOILWTICO": 2.8, "BAMLH0A0HYM2": 2.6, "TRY=X": 3.4,
          "GC=F": 2.4, "XLE": 2.9, "DGS2": -2.5, "TTF=F": 2.7, "^RUT": -2.2,
          "BAMLH0A3HYC": 2.3}

# Sources that "fail" in the demo, to show cached/stale handling.
FAILING = {"gpr": "simulated outage: matteoiacoviello.com unreachable"}


def _dates(freq: str, lag: int, today: date, n_years: int = 5) -> pd.DatetimeIndex:
    end = pd.Timestamp(np.busday_offset(np.datetime64(today), -lag, roll="backward"))
    if freq == "D":
        return pd.bdate_range(end - pd.DateOffset(years=n_years), end)
    if freq == "M":  # last complete month published early next month
        return pd.date_range(end - pd.DateOffset(years=n_years), end - pd.offsets.MonthEnd(1),
                             freq="ME")
    return pd.date_range(end - pd.DateOffset(years=n_years), end - pd.Timedelta(days=6),
                         freq=freq)


# Daily yield series share a common "rates" factor so curves and spreads look sane.
RATES_BETA = {c: 0.97 for c in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS5", "DGS7",
                               "DGS10", "DGS20", "DGS30", "DFII10", "IUDMNPY",
                               "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A")}
RATES_BETA.update({"DGS1MO": 0.8, "DGS3MO": 0.85, "DGS6MO": 0.9, "DFII10": 0.9, "10Y": 0.4})
BUND = "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
# Monthly euro-area yields = Bund month-end + a slowly moving spread.
MONTHLY_OVER_BUND = {"IRS/M.FR.L.L40.CI.0000.EUR.N.Z": .72, "IRS/M.IT.L.L40.CI.0000.EUR.N.Z": .81}


def _gpr(code: str, today: date) -> pd.Series:
    """Mean-reverting (AR(1) in logs) rather than a random walk."""
    level, vol, _, lag, freq = P[code]
    idx = _dates(freq, lag, today)
    rng = np.random.default_rng(zlib.crc32(code.encode()))
    x = np.zeros(len(idx))
    phi = 0.97 if freq == "D" else 0.8
    for i in range(1, len(idx)):
        x[i] = phi * x[i - 1] + rng.normal(0, vol / 100 * (1 if "MA7" in code else 2.5))
    return pd.Series(level * np.exp(x - x[-1] * 0.6), index=idx)


def _walk(code: str, today: date) -> pd.Series:
    if "GPR" in code:
        return _gpr(code, today)
    level, vol, kind, lag, freq = P[code]
    if code in MONTHLY_OVER_BUND:
        bund = _walk(BUND, today).resample("ME").last()
        bund = bund[bund.index <= pd.Timestamp(today) - pd.offsets.MonthEnd(1)]
        rng = np.random.default_rng(zlib.crc32(code.encode()))
        spread = MONTHLY_OVER_BUND[code] + np.concatenate(
            [np.cumsum(rng.normal(0, .04, len(bund) - 1)[::-1])[::-1], [0]])
        return bund + spread
    idx = _dates(freq, lag, today)
    rng = np.random.default_rng(zlib.crc32(code.encode()))
    steps = rng.standard_t(4, len(idx)) / np.sqrt(2) * vol
    if code in RATES_BETA:
        common = np.random.default_rng(7).standard_t(4, len(pd.bdate_range(
            pd.Timestamp(today) - pd.DateOffset(years=6), today))) / np.sqrt(2)
        b = RATES_BETA[code]
        steps = vol * (b * common[-len(idx) - lag:len(common) - lag if lag else None][:len(idx)]
                       + np.sqrt(1 - b * b) * steps / vol)
    steps[-1] += SHOCKS.get(code, 0) * vol
    # walk backward from the target level so the latest value is plausible
    if kind == "add":
        path = level - np.concatenate([np.cumsum(steps[::-1])[::-1][1:], [0]])
        if code in ("DFF",):
            path = np.where(idx < idx[-1] - pd.Timedelta(days=60), level + .25, level)
    else:
        logp = np.log(level) - np.concatenate([np.cumsum((steps / 100)[::-1])[::-1][1:], [0]])
        path = np.exp(logp)
    return pd.Series(path, index=idx)


def install(store: Store, today: date | None = None) -> None:
    """Swap the real fetchers for synthetic ones and pre-seed cache for 'failing' sources."""
    today = today or date.today()
    cache: dict[str, pd.Series] = {}

    def series(code: str) -> pd.Series:
        if code not in cache:
            if code == "T10Y2Y":
                cache[code] = series("DGS10") - series("DGS2")
            elif code == "T10Y3M":
                cache[code] = series("DGS10") - series("DGS3MO")
            else:
                cache[code] = _walk(code, today)
        return cache[code]

    def make(source: str):
        def fetch(code: str, start: date):
            if source in FAILING:
                raise sources.SourceError(FAILING[source])
            if source in ("banque_de_france", "eia"):
                raise sources.MissingKey("demo: optional key not set")
            if code not in P and code not in ("T10Y2Y", "T10Y3M"):
                raise sources.SourceError(f"demo has no synthetic data for {code}")
            s = series(code)
            return s[s.index >= pd.Timestamp(start)], {"title": f"DEMO {code}"}
        return fetch

    for name in list(sources.FETCHERS):
        sources.FETCHERS[name] = make(name)

    # A cached copy of the "failing" GPR data that ends ~2.5 weeks ago -> shown stale.
    for sid, code in (("gpr_daily", "daily:GPRD_MA7"), ("gpr_daily_raw", "daily:GPRD"),
                      ("gpr_monthly", "monthly:GPR")):
        if store.last_date(sid, "gpr", code) is None:
            s = _walk(code, today - timedelta(days=17))
            store.upsert(sid, "gpr", code, s)


def calendar(today: date) -> list[dict]:
    """Illustrative release dates (the FRED release calendar is unreachable in demo)."""
    items = [(4, "Jobless claims"), (5, "CPI"), (6, "PPI"), (6, "Retail sales"),
             (11, "Jobless claims"), (13, "GDP")]
    out = []
    for off, name in items:
        d = np.busday_offset(np.datetime64(today), off, roll="forward").astype(date)
        out.append({"date": d.isoformat(), "name": name + " (illustrative date)",
                    "approx": True})
    return out


def news(feeds_cfg: dict) -> dict:
    groups = []
    for g, feeds in feeds_cfg["groups"].items():
        items = [{"title": f"[Demo placeholder] Latest item from {f['name']} appears here",
                  "link": f["url"], "source": f["name"], "official": bool(f.get("official")),
                  "published": None} for f in feeds]
        groups.append({"name": g, "items": items, "feeds": [f["name"] for f in feeds]})
    return {"groups": groups, "failed": []}
