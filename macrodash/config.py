"""Load series.yaml (and the other yaml configs) into typed objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

# Business days after which a series is flagged stale, by frequency.
# Lower-frequency series are judged against their normal publication cadence.
DEFAULT_STALE_BDAYS = {"daily": 3, "weekly": 8, "monthly": 45, "quarterly": 95}


@dataclass
class SourceSpec:
    source: str
    code: str
    note: str = ""


@dataclass
class Series:
    id: str
    name: str
    panel: str
    units: str          # display units, e.g. "%", "bps", "$/bbl", "index"
    freq: str           # daily | weekly | monthly | quarterly
    change: str         # how to express changes: bps | pct | abs
    sources: list[SourceSpec] = field(default_factory=list)
    scale: float = 1.0  # multiply raw values (e.g. FRED $mn -> $bn)
    stale_bdays: int | None = None
    dp: int | None = None
    note: str = ""
    tags: list[str] = field(default_factory=list)
    # derived-only fields
    inputs: list[str] = field(default_factory=list)
    ffill: list[str] = field(default_factory=list)
    expr: str = ""
    fn: str = ""
    window: int = 20

    @property
    def derived(self) -> bool:
        return bool(self.inputs)

    @property
    def stale_after(self) -> int:
        return self.stale_bdays or DEFAULT_STALE_BDAYS.get(self.freq, 3)


def _yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def load(path: Path | None = None) -> dict:
    cfg = yaml.safe_load((path or ROOT / "series.yaml").read_text())
    seen: set[str] = set()

    def build(raw: dict) -> Series:
        if raw["id"] in seen:
            raise ValueError(f"duplicate series id {raw['id']}")
        seen.add(raw["id"])
        raw = dict(raw)
        srcs = [SourceSpec(**s) for s in raw.pop("sources", [])]
        return Series(sources=srcs, **raw)

    cfg["series"] = [build(r) for r in cfg["series"]]
    cfg["derived"] = [build(r) for r in cfg.get("derived", [])]
    for d in cfg["derived"]:
        missing = [i for i in d.inputs if i not in seen]
        if missing:
            raise ValueError(f"derived {d.id}: unknown inputs {missing}")
    return cfg


def load_feeds() -> dict:
    return _yaml("feeds.yaml")


def load_calendar() -> dict:
    return _yaml("calendar.yaml")
