"""Headlines from public RSS feeds, grouped, de-duplicated, newest first."""
from __future__ import annotations

import calendar
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import feedparser

from . import config, http

log = logging.getLogger("macrodash.news")


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _ts(entry) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        t = entry.get(k)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def _fetch(feed: dict) -> tuple[dict, list[dict], str]:
    try:
        r = http.get(feed["url"], timeout=20)
        parsed = feedparser.parse(r.content)
        items = []
        inc = [w.lower() for w in feed.get("include", [])]
        for e in parsed.entries:
            title = re.sub(r"\s+", " ", e.get("title", "")).strip()
            if not title or not e.get("link"):
                continue
            if inc:
                text = (title + " " + e.get("summary", "")).lower()
                if not any(re.search(rf"\b{re.escape(w)}", text) for w in inc):
                    continue
            ts = _ts(e)
            items.append({"title": title, "link": e["link"], "source": feed["name"],
                          "official": bool(feed.get("official")),
                          "published": ts.isoformat() if ts else None})
        return feed, items, ""
    except Exception as e:  # noqa: BLE001
        return feed, [], f"{type(e).__name__}: {str(e).splitlines()[0][:150]}"


def _dedupe(items: list[dict]) -> list[dict]:
    out, seen_links, seen_titles = [], set(), []
    for it in items:
        link = it["link"].split("?")[0].rstrip("/")
        t = _norm(it["title"])
        if link in seen_links or any(SequenceMatcher(None, t, s).ratio() > 0.85
                                     for s in seen_titles):
            continue
        seen_links.add(link)
        seen_titles.append(t)
        out.append(it)
    return out


def build() -> dict:
    cfg = config.load_feeds()
    n = cfg.get("max_items_per_group", 10)
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("max_age_days", 5))
    feeds = [(g, f) for g, fs in cfg["groups"].items() for f in fs]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda gf: (gf[0], *_fetch(gf[1])), feeds))
    groups, failed = [], []
    seen_global: set[str] = set()
    for g in cfg["groups"]:
        items = []
        for grp, feed, its, err in results:
            if grp != g:
                continue
            if err:
                log.warning("feed %s failed: %s", feed["name"], err)
                failed.append({"group": g, "name": feed["name"], "error": err})
            items += its
        items = [i for i in items if i["published"] is None
                 or datetime.fromisoformat(i["published"]) >= cutoff]
        items.sort(key=lambda i: i["published"] or "", reverse=True)
        items = [i for i in _dedupe(items) if _norm(i["title"]) not in seen_global][:n]
        seen_global.update(_norm(i["title"]) for i in items)
        groups.append({"name": g, "items": items,
                       "feeds": [f["name"] for gg, f in feeds if gg == g]})
    return {"groups": groups, "failed": failed}
