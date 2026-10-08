"""Upcoming US data releases (FRED API + ISM rule) and central bank meetings."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np

from . import config, sources

log = logging.getLogger("macrodash.calendar")


def _nth_bday(year: int, month: int, n: int) -> date:
    d = np.busday_offset(np.datetime64(f"{year}-{month:02d}-01"), n - 1, roll="forward")
    return d.astype(date)


def ism_dates(today: date, horizon: int) -> list[dict]:
    """ISM is not on FRED. Manufacturing = 1st business day, Services = 3rd
    (US holidays not accounted for, so dates are marked approximate)."""
    out = []
    for k in range(3):
        m = (today.month - 1 + k) % 12 + 1
        y = today.year + (today.month - 1 + k) // 12
        for name, n in (("ISM Manufacturing PMI", 1), ("ISM Services PMI", 3)):
            d = _nth_bday(y, m, n)
            if today <= d <= today + timedelta(days=horizon):
                out.append({"date": d.isoformat(), "name": name, "approx": True})
    return out


def fred_releases(cfg: dict, today: date, horizon: int) -> list[dict]:
    wanted = {r["id"]: r["name"] for r in cfg.get("calendar_releases", [])}
    js = sources.fred_get(
        "releases/dates", realtime_start=today.isoformat(),
        realtime_end=(today + timedelta(days=horizon)).isoformat(),
        include_release_dates_with_no_data="true", sort_order="asc",
        order_by="release_date", limit=1000)
    return [{"date": x["date"], "name": wanted[x["release_id"]], "approx": False}
            for x in js.get("release_dates", []) if x["release_id"] in wanted]


def central_banks(today: date, horizon: int = 90) -> dict:
    cal = config.load_calendar()
    out = []
    for bank, days in cal["central_banks"].items():
        for d in days:
            d = d if isinstance(d, date) else date.fromisoformat(str(d))
            if today <= d <= today + timedelta(days=horizon):
                out.append({"date": d.isoformat(), "bank": bank})
    out.sort(key=lambda x: x["date"])
    last = max(date.fromisoformat(str(d)) for v in cal["central_banks"].values() for d in v)
    return {"meetings": out, "verified": bool(cal.get("verified")),
            "needs_update": last < today + timedelta(days=horizon)}


def build(cfg: dict, today: date, horizon: int = 14) -> dict:
    errors = []
    try:
        rel = fred_releases(cfg, today, horizon)
    except Exception as e:  # noqa: BLE001
        log.warning("FRED release calendar failed: %s", e)
        errors.append("FRED release calendar unavailable")
        rel = []
    rel += ism_dates(today, horizon)
    rel.sort(key=lambda x: x["date"])
    return {"releases": rel, "central_banks": central_banks(today), "errors": errors}
