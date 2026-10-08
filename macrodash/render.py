"""Assemble the JSON snapshot and render the self-contained dashboard.html."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import metrics
from .pipeline import Result

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"
NY = ZoneInfo("America/New_York")

PANEL_NOTES = {
    "us_rates": ["Term premium: NY Fed ACM 10y; Kim-Wright (FRED THREEFYTP10) used if the NY Fed file fails."],
    "sovereigns": [
        "Bund: Bundesbank daily. Gilt: BoE daily par yield. JGB: MOF daily.",
        "BTP and OAT: no free daily official source without registration; tagged M (ECB monthly) "
        "unless a Banque de France key enables daily OAT. Monthly spreads are month-end to month-end.",
    ],
    "credit": ["ICE BofA OAS via FRED. US sector OAS (financials, industrials, ...) is not published "
               "on FRED; rating buckets are shown instead."],
    "vol": ["MOVE is not free; the 10Y realized vol (20-day stdev of daily changes, annualized) is a proxy. "
            "NFCI: negative = looser than average."],
    "liquidity": ["Net liquidity = Fed balance sheet - TGA - ON RRP, on weekly (Wednesday) dates; RRP carried to that date."],
    "fx": ["Fed H.10 daily rates (incl. broad dollar index - not DXY) are published weekly with a lag, "
           "so they are often several days old; their stale threshold is 8 business days."],
    "commodities": ["Brent, WTI and Henry Hub: EIA spot via FRED (lags several days). TTF, gold, copper, "
                    "wheat: front-month futures via Yahoo (unofficial). TTF converted at EURUSD/3.412."],
    "macro": ["ISM dates follow the 1st/3rd business-day rule and ignore holidays."],
    "geo": ["Caldara-Iacoviello Geopolitical Risk index (matteoiacoviello.com), 1985-2019 = 100."],
}

SOURCE_NOTES = [
    ["FRED (St. Louis Fed)", "UST curve, TIPS, breakevens, fed funds, ICE BofA OAS, VIX, NFCI, H.4.1, H.10 FX, EIA spot, S&P/Nasdaq/Nikkei, GDPNow, release calendar",
     "ICE BofA history on FRED may be limited to recent years; H.10 FX lags ~1 week; no sector OAS"],
    ["US Treasury", "Fallback for the UST curve", "Par yield curve CSV"],
    ["NY Fed", "SOFR, EFFR fallback, ACM term premium", "ACM file updated with a short lag"],
    ["Bundesbank", "Bund 10Y daily", "Estimated (Svensson) yield, not a single benchmark bond"],
    ["ECB Data Portal", "OAT, BTP 10Y", "Monthly (convergence-criterion yields)"],
    ["Banque de France", "OAT 10Y daily (optional)", "Requires free BDF_API_KEY"],
    ["Bank of England", "Gilt 10Y daily", "Nominal par yield IUDMNPY"],
    ["Japan MOF", "JGB 10Y daily", "CSV; current month + historical file"],
    ["EIA", "Optional fallback for oil and gas spot", "Requires free EIA_API_KEY"],
    ["matteoiacoviello.com", "GPR daily and monthly", "Academic dataset, updated weekly-ish"],
    ["Yahoo Finance (yfinance)", "Russell 2000, Stoxx 600, EEM, RSP/SPY, sector ETFs, gold, copper, wheat, TTF, USDTRY",
     "UNOFFICIAL. Intraday bars are dropped; only completed daily closes are used"],
    ["RSS feeds", "News", "Official feeds (Fed, ECB, BoE, BoJ, BLS, BEA, EIA) plus free news feeds"],
]


def build_snapshot(cfg: dict, results: dict[str, Result], calendar: dict, news: dict | None,
                   demo: bool, today: date) -> dict:
    order = [s.id for s in cfg["series"]] + [d.id for d in cfg["derived"]]
    rows = {sid: metrics.build_row(results[sid], today) for sid in order if sid in results}
    panels = []
    for key, title in cfg["panels"].items():
        prow = [rows[i] for i in order if i in rows and rows[i]["panel"] == key
                and not {"hidden", "sector"} & set(rows[i]["tags"])]
        panels.append({"key": key, "title": title, "rows": prow,
                       "notes": PANEL_NOTES.get(key, [])})
    extra = [r for r in rows.values() if "hidden" in r["tags"]]
    all_rows = [r for r in rows.values()]

    failures = []
    for sid in order:
        r = results.get(sid)
        if r and r.failed and r.source != "derived" and not r.spec.derived:
            showing = (f"cached value from {rows[sid]['as_of']}" if rows[sid]["as_of"]
                       else "nothing (no cache)")
            failures.append({"name": f"{r.spec.name} ({sid})", "showing": showing,
                             "error": r.error})
    for f in calendar.get("errors", []):
        failures.append({"name": "US release calendar", "showing": "ISM rule dates only", "error": f})
    for f in (news or {}).get("failed", []):
        failures.append({"name": f"News feed: {f['name']}", "showing": "other feeds",
                         "error": f["error"]})

    now = datetime.now(timezone.utc)
    return {
        "demo": demo,
        "run_date": today.isoformat(),
        "generated_utc": now.strftime("%Y-%m-%d %H:%M"),
        "generated_local": now.astimezone(NY).strftime("%a %d %b %Y %H:%M ET"),
        "failures": failures,
        "what_moved": metrics.what_moved(all_rows),
        "panels": panels,
        "extra_rows": extra,
        "curve": metrics.curve(results, [s.id for s in cfg["series"] if "curve" in s.tags]),
        "heatmap": metrics.heatmap(all_rows),
        "calendar": calendar,
        "news": news,
        "source_notes": SOURCE_NOTES,
    }


def write(snapshot: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snapshot.json").write_text(json.dumps(snapshot, indent=1, default=str))
    payload = json.dumps(snapshot, separators=(",", ":"), default=str).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__SNAPSHOT__*/", payload)
    path = out_dir / "dashboard.html"
    path.write_text(html)
    return path
