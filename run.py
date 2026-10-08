#!/usr/bin/env python3
"""Fetch all series, compute metrics, pull news, and write output/dashboard.html.

Usage:
  python run.py              # full run
  python run.py --no-news    # skip RSS feeds
  python run.py --demo       # synthetic data (layout preview, no network needed)
  python run.py --open       # open the dashboard in a browser when done
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from datetime import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from macrodash import preflight

preflight.check()

from macrodash import calendar, config, news, pipeline, render
from macrodash.store import Store


def setup_logging() -> None:
    config.LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(config.LOG_DIR / "run.log", maxBytes=2_000_000, backupCount=3)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh])
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="use synthetic data (no network)")
    ap.add_argument("--no-news", action="store_true", help="skip RSS headlines")
    ap.add_argument("--open", action="store_true", help="open dashboard when done")
    args = ap.parse_args()

    setup_logging()
    log = logging.getLogger("macrodash")
    t0 = time.time()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    cfg = config.load()

    store = Store(config.DATA_DIR / ("demo.sqlite" if args.demo else "cache.sqlite"))
    if args.demo:
        from macrodash import demo
        demo.install(store, today)
        log.warning("DEMO MODE: synthetic data, not market prices")

    log.info("fetching %d series", len(cfg["series"]))
    results = pipeline.fetch_all(cfg, store)
    results = pipeline.compute_derived(cfg, results)
    n_fail = sum(r.failed for r in results.values() if not r.spec.derived)
    log.info("series fetched: %d ok, %d failed", len(cfg["series"]) - n_fail, n_fail)

    if args.demo:
        cal = {"releases": demo.calendar(today) + calendar.ism_dates(today, 14),
               "central_banks": calendar.central_banks(today), "errors": []}
        cal["releases"].sort(key=lambda x: x["date"])
    else:
        cal = calendar.build(cfg, today)

    headlines = None
    if not args.no_news:
        headlines = demo.news(config.load_feeds()) if args.demo else news.build()

    snap = render.build_snapshot(cfg, results, cal, headlines, args.demo, today)
    out = render.write(snap, config.OUT_DIR)
    log.info("wrote %s in %.1fs (%d failures)", out, time.time() - t0, len(snap["failures"]))
    if args.open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
