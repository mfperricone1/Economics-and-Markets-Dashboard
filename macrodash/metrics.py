"""Turn fetched series into display rows: changes, z-scores, staleness, chart data."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .pipeline import Result

Z_THRESHOLD = 2.0


def _dp(units: str, v: float, dp: int | None) -> int:
    if dp is not None:
        return dp
    if units in ("%", "% saar"):
        return 2
    if units == "bps":
        return 0
    a = abs(v)
    return 4 if a < 10 else 2 if a < 1000 else 0


def _change(kind: str, v: float, prev: float | None) -> float | None:
    if prev is None or pd.isna(prev):
        return None
    if kind == "bps":
        return (v - prev) * 100
    if kind == "pct":
        return (v / prev - 1) * 100 if prev else None
    return v - prev


def _asof(s: pd.Series, when: pd.Timestamp) -> float | None:
    if s.index[0] > when:
        return None
    return float(s.asof(when))


def _changes_series(kind: str, s: pd.Series) -> pd.Series:
    if kind == "bps":
        return s.diff() * 100
    if kind == "pct":
        return s.pct_change() * 100
    return s.diff()


def zscore(kind: str, s: pd.Series) -> float | None:
    """Latest obs-to-obs change vs the trailing 1y distribution of such changes."""
    ch = _changes_series(kind, s).dropna()
    if len(ch) < 30:
        return None
    last_d = ch.index[-1]
    hist = ch[(ch.index > last_d - pd.Timedelta(days=365)) & (ch.index < last_d)]
    if len(hist) < 30 or hist.std() == 0:
        return None
    return float((ch.iloc[-1] - hist.mean()) / hist.std())


def _round(x: float | None, sig: int = 6) -> float | None:
    if x is None or not np.isfinite(x):
        return None
    return float(f"{x:.{sig}g}")


def build_row(r: Result, today: date) -> dict:
    sp = r.spec
    base = {
        "id": sp.id, "name": sp.name, "panel": sp.panel, "units": sp.units,
        "source": r.source, "code": r.code, "unofficial": r.unofficial,
        "failed": r.failed, "from_cache": r.from_cache, "fallback": r.fallback,
        "monthly": r.monthly, "note": sp.note, "error": r.error if r.failed else "",
        "freq": "monthly" if r.monthly else sp.freq, "change_kind": sp.change,
        "tags": sp.tags,
    }
    s = r.series.dropna() if r.series is not None else pd.Series(dtype=float)
    if s.empty:
        return {**base, "value": None, "as_of": None, "stale": True, "bdays_old": None,
                "chg": {}, "z": None, "hist": {"d": [], "v": []}, "dp": 2}
    last_d, v = s.index[-1], float(s.iloc[-1])
    freq = base["freq"]
    bdays_old = int(np.busday_count(last_d.date(), today))
    threshold = 45 if r.monthly else sp.stale_after
    kind = sp.change
    chg = {
        "d1": _change(kind, v, float(s.iloc[-2])) if freq == "daily" and len(s) > 1 else None,
        "w1": (_change(kind, v, _asof(s, last_d - pd.Timedelta(days=7)))
               if freq in ("daily", "weekly") else None),
        "m1": _change(kind, v, _asof(s, last_d - pd.DateOffset(months=1))),
        "ytd": _change(kind, v, _asof(s, pd.Timestamp(last_d.year - 1, 12, 31))),
    }
    z = zscore(kind, s) if freq in ("daily", "weekly") else None
    yr = s[s.index > last_d - pd.Timedelta(days=366)]
    return {
        **base,
        "value": _round(v), "as_of": last_d.strftime("%Y-%m-%d"),
        "bdays_old": bdays_old, "stale": bdays_old > threshold or r.failed,
        "chg": {k: _round(x, 4) for k, x in chg.items()},
        "z": _round(z, 3), "zflag": z is not None and abs(z) > Z_THRESHOLD,
        "dp": _dp(sp.units, v, sp.dp),
        "hist": {"d": [d.strftime("%Y-%m-%d") for d in yr.index],
                 "v": [_round(x) for x in yr.values]},
    }


def what_moved(rows: list[dict], n: int = 8) -> list[dict]:
    cands = [r for r in rows if r.get("z") is not None and not r["stale"]
             and "sector" not in r["tags"]]
    cands.sort(key=lambda r: abs(r["z"]), reverse=True)
    return [{"id": r["id"], "name": r["name"], "z": r["z"], "panel": r["panel"],
             "chg": r["chg"].get("d1") if r["freq"] == "daily" else r["chg"].get("w1"),
             "change_kind": r["change_kind"], "units": r["units"], "as_of": r["as_of"],
             "freq": r["freq"]} for r in cands[:n]]


TENOR_YEARS = {"ust_1m": 1 / 12, "ust_3m": 0.25, "ust_6m": 0.5, "ust_1y": 1, "ust_2y": 2,
               "ust_5y": 5, "ust_7y": 7, "ust_10y": 10, "ust_20y": 20, "ust_30y": 30}


def curve(results: dict[str, Result], ids: list[str]) -> dict | None:
    ser = {i: results[i].series for i in ids if results.get(i) and results[i].series is not None}
    if len(ser) < 3:
        return None
    df = pd.concat(ser, axis=1).dropna(how="all")
    full = df.dropna()
    anchor = full.index[-1] if not full.empty else df.index[-1]

    def at(when: pd.Timestamp) -> dict:
        row = df[df.index <= when].ffill().iloc[-1] if (df.index <= when).any() else None
        if row is None:
            return {}
        return {"date": df.index[df.index <= when][-1].strftime("%Y-%m-%d"),
                "vals": [_round(row.get(i)) for i in ids]}

    return {
        "labels": [results[i].spec.name.replace("UST ", "") for i in ids],
        "ids": ids,
        "today": at(anchor),
        "m1": at(anchor - pd.DateOffset(months=1)),
        "y1": at(anchor - pd.DateOffset(years=1)),
    }


def heatmap(rows: list[dict]) -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "as_of": r["as_of"], "stale": r["stale"],
             "chg": r["chg"]} for r in rows if "sector" in r["tags"]]
