"""Load series.yaml into typed objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

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
    sources: list[SourceSpec]
    scale: float = 1.0  # multiply raw values (e.g. FRED $mn -> $bn)
    stale_bdays: int | None = None
    note: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def stale_after(self) -> int:
        return self.stale_bdays or DEFAULT_STALE_BDAYS.get(self.freq, 3)


def load(path: Path | None = None) -> dict:
    cfg = yaml.safe_load((path or ROOT / "series.yaml").read_text())
    out = []
    seen = set()
    for raw in cfg["series"]:
        if raw["id"] in seen:
            raise ValueError(f"duplicate series id {raw['id']}")
        seen.add(raw["id"])
        srcs = [SourceSpec(**s) for s in raw.pop("sources")]
        out.append(Series(sources=srcs, **raw))
    cfg["series"] = out
    return cfg
