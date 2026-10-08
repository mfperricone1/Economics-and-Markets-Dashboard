#!/usr/bin/env python3
"""Verify every series in series.yaml exists and returns recent data.

For each series, every candidate source is tried (not just the first), so the
report shows which fallbacks are alive. Prints a table and writes
output/verify_report.{md,csv}. Exit code is 1 if any series has no working source.

Also checks:
  * FRED calendar release ids used for the US data calendar
  * whether ICE BofA *sector* OAS series exist on FRED (search API)
  * every RSS feed in feeds.yaml (item count, newest item)

Usage: python verify_sources.py [--only id1,id2] [--no-extras]
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from urllib.parse import urlparse

import numpy as np

from macrodash import config, sources

OUT = config.ROOT / "output"

LOOKBACK = {"daily": 120, "weekly": 180, "monthly": 3 * 365, "quarterly": 4 * 365}


def bdays_since(d: date, today: date) -> int:
    return int(np.busday_count(d, today))


def short_err(e: Exception) -> str:
    msg = str(e).splitlines()[0] if str(e) else type(e).__name__
    # Proxy / DNS failures are network-policy problems, not bad series ids.
    if any(k in msg for k in ("Tunnel connection failed", "unreachable earlier", "CONNECT tunnel", "403 Forbidden")) or "Proxy" in type(e).__name__:
        return "NETWORK BLOCKED (proxy/firewall)"
    if "NameResolutionError" in msg or "Failed to resolve" in msg:
        return "NETWORK: DNS failure"
    return f"{type(e).__name__}: {msg}"[:110]


def try_candidate(s: config.Series, spec: config.SourceSpec, today: date) -> dict:
    start = today - timedelta(days=LOOKBACK.get(s.freq, 120))
    row = {"source": spec.source, "code": spec.code, "note": spec.note}
    try:
        ser, meta = sources.FETCHERS[spec.source](spec.code, start)
        last = ser.index[-1].date()
        age = bdays_since(last, today)
        row.update(last=last.isoformat(), value=round(float(ser.iloc[-1]) * s.scale, 4),
                   n=len(ser), age=age, title=meta.get("title", ""))
        if meta.get("discontinued"):
            row["status"] = "DISCONTINUED"
        elif spec.note == "monthly" and s.freq == "daily":
            row["status"] = "OK-MONTHLY" if age <= 45 else "STALE"
        elif age > s.stale_after:
            row["status"] = "STALE"
        else:
            row["status"] = "OK"
    except sources.MissingKey as e:
        row.update(status="SKIPPED", error=str(e))
    except Exception as e:  # noqa: BLE001 - report everything
        row.update(status="FAIL", error=short_err(e))
    return row


def verify_series(cfg, only: set[str] | None, today: date) -> list[dict]:
    results = []
    for s in cfg["series"]:
        if only and s.id not in only:
            continue
        cands = [try_candidate(s, spec, today) for spec in s.sources]
        good = [c for c in cands if c["status"] in ("OK", "OK-MONTHLY")]
        chosen = good[0] if good else next(
            (c for c in cands if c["status"] in ("STALE", "DISCONTINUED")), cands[0])
        status = chosen["status"]
        if good and chosen is not cands[0]:
            status += " (fallback)"
        if not good and all(c["status"] in ("FAIL", "SKIPPED") for c in cands):
            status = "FAIL"
        results.append({"id": s.id, "name": s.name, "panel": s.panel, "status": status,
                        "chosen": chosen, "candidates": cands})
        mark = {"OK": ".", "FAIL": "X"}.get(status.split()[0], "!")
        print(mark, end="", flush=True)
    print()
    return results


def check_calendar(cfg) -> list[dict]:
    rows = []
    for rel in cfg.get("calendar_releases", []):
        try:
            info = sources.fred_get("release", release_id=rel["id"])["releases"][0]
            rows.append({"id": rel["id"], "expected": rel["name"], "fred_name": info["name"],
                         "status": "OK"})
        except Exception as e:  # noqa: BLE001
            rows.append({"id": rel["id"], "expected": rel["name"], "fred_name": "",
                         "status": "FAIL: " + short_err(e)})
    return rows


def check_sector_oas() -> tuple[str, list[str]]:
    """Search FRED for ICE BofA OAS series and keep those that look sector-specific."""
    try:
        hits = sources.fred_get("series/search", search_text="ICE BofA option-adjusted spread",
                                limit=1000)["seriess"]
    except Exception as e:  # noqa: BLE001
        return "FAIL: " + short_err(e), []
    rating = ("AAA", "AA ", "Single-A", "BBB", "BB ", "Single-B", "CCC", "High Yield Index",
              "Corporate Index Option", "US Corporate Index")
    sector_words = ("Financial", "Industrial", "Utilit", "Energy", "Bank", "Insurance",
                    "Telecom", "Media", "Health", "Technology", "Real Estate", "Non-Financial",
                    "Sector")
    sector = [f"{h['id']}: {h['title']}" for h in hits
              if any(w in h["title"] for w in sector_words)
              and "DISCONTINUED" not in h["title"].upper()]
    return f"OK ({len(hits)} OAS series found, {len(sector)} look sector-based)", sector


def check_feeds() -> list[list]:
    from macrodash import news
    rows = []
    for group, feeds in config.load_feeds()["groups"].items():
        for f in feeds:
            _, items, err = news._fetch(f)
            newest = max((i["published"] or "" for i in items), default="")[:10]
            status = ("FAIL: " + short_err(Exception(err))) if err else (
                "OK" if items else "EMPTY (no items / filter too strict)")
            rows.append([group, f["name"], len(items), newest or "-", status])
    return rows


def fmt_table(rows: list[list[str]], headers: list[str]) -> str:
    w = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: "  ".join(str(x).ljust(n) for x, n in zip(r, w))  # noqa: E731
    return "\n".join([line(headers), line(["-" * n for n in w])] + [line(r) for r in rows])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated series ids")
    ap.add_argument("--no-extras", action="store_true", help="skip calendar/sector checks")
    args = ap.parse_args()

    cfg = config.load()
    today = date.today()
    only = set(args.only.split(",")) if args.only else None
    print(f"Verifying {len(cfg['series'])} series as of {today} ...")
    res = verify_series(cfg, only, today)

    headers = ["series", "name", "source", "last obs", "bd age", "value", "status"]
    rows = []
    for r in res:
        c = r["chosen"]
        rows.append([r["id"], r["name"][:28], f"{c['source']}:{c['code']}"[:44],
                     c.get("last", "-"), c.get("age", "-"), c.get("value", "-"), r["status"]])
    table = fmt_table(rows, headers)
    print("\n" + table)

    problems = []
    for r in res:
        for c in r["candidates"]:
            if c["status"] not in ("OK", "OK-MONTHLY"):
                problems.append([r["id"], f"{c['source']}:{c['code']}"[:50], c["status"],
                                 c.get("error", f"last obs {c.get('last')}")])
    if problems:
        print("\nCandidate problems (primary or fallback sources that did not work):")
        print(fmt_table(problems, ["series", "candidate", "status", "detail"]))

    blocked_hosts = sorted({urlparse_host(c) for r in res for c in r["candidates"]
                            if c.get("error", "").startswith("NETWORK")})

    extras = ""
    if not args.no_extras:
        cal = check_calendar(cfg)
        print("\nFRED release ids (US calendar):")
        print(fmt_table([[c["id"], c["expected"], c["fred_name"], c["status"]] for c in cal],
                        ["id", "expected", "FRED name", "status"]))
        st, sector = check_sector_oas()
        print(f"\nICE BofA sector OAS on FRED: {st}")
        for s in sector[:40]:
            print("   ", s)
        feeds = check_feeds()
        print("\nRSS feeds:")
        print(fmt_table(feeds, ["group", "feed", "items", "newest", "status"]))
        extras = (f"\n## RSS feeds\n\n```\n" +
                  fmt_table(feeds, ["group", "feed", "items", "newest", "status"]) + "\n```\n" +
                  f"\n## FRED calendar releases\n\n" +
                  "\n".join(f"- {c['id']} {c['expected']} -> {c['fred_name']} [{c['status']}]"
                            for c in cal) +
                  f"\n\n## ICE BofA sector OAS on FRED\n\n{st}\n\n" +
                  "\n".join(f"- {s}" for s in sector))

    n_fail = sum(r["status"] == "FAIL" for r in res)
    n_fb = sum("fallback" in r["status"] for r in res)
    n_stale = sum(r["status"].startswith(("STALE", "DISCONTINUED")) for r in res)
    summary = (f"{len(res)} series: {len(res) - n_fail - n_stale} ok "
               f"({n_fb} via fallback), {n_stale} stale/discontinued, {n_fail} failed")
    print("\n" + summary)
    if blocked_hosts:
        print("Network-blocked hosts: " + ", ".join(blocked_hosts))

    OUT.mkdir(exist_ok=True)
    (OUT / "verify_report.md").write_text(
        f"# Source verification {today}\n\n{summary}\n\n```\n{table}\n```\n\n"
        + ("## Candidate problems\n\n```\n" +
           fmt_table(problems, ["series", "candidate", "status", "detail"]) + "\n```\n"
           if problems else "") + extras + "\n")
    with open(OUT / "verify_report.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["series", "candidate_rank", "source", "code", "status", "last_obs",
                    "bd_age", "value", "error"])
        for r in res:
            for i, c in enumerate(r["candidates"]):
                w.writerow([r["id"], i, c["source"], c["code"], c["status"], c.get("last", ""),
                            c.get("age", ""), c.get("value", ""), c.get("error", "")])
    print(f"Report written to {OUT / 'verify_report.md'}")
    return 1 if n_fail else 0


SOURCE_HOSTS = {
    "fred": "api.stlouisfed.org", "treasury": "home.treasury.gov",
    "nyfed_rates": "markets.newyorkfed.org", "nyfed_acm": "www.newyorkfed.org",
    "ecb": "data-api.ecb.europa.eu", "bundesbank": "api.statistiken.bundesbank.de",
    "banque_de_france": "webstat.banque-france.fr", "boe": "www.bankofengland.co.uk",
    "mof_jgb": "www.mof.go.jp", "eia": "api.eia.gov", "gpr": "www.matteoiacoviello.com",
    "yfinance": "query1/query2.finance.yahoo.com, fc.yahoo.com",
}


def urlparse_host(c: dict) -> str:
    return SOURCE_HOSTS.get(c["source"], urlparse(c["source"]).netloc or c["source"])


if __name__ == "__main__":
    sys.exit(main())
