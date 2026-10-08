"""Fetch every configured series, caching to SQLite, never letting one failure stop the run."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from . import config, sources
from .http import redact
from .store import Store

log = logging.getLogger("macrodash.pipeline")

HISTORY_YEARS = 5     # first-run backfill
REFETCH_OVERLAP = 14  # days re-pulled on each run to pick up revisions


@dataclass
class Result:
    spec: config.Series
    series: pd.Series | None        # scaled values, full cached history
    source: str = ""
    code: str = ""
    failed: bool = False            # every candidate failed this run
    error: str = ""
    from_cache: bool = False        # values come from an earlier run
    fallback: bool = False          # a non-primary candidate was used
    monthly: bool = False           # monthly fallback standing in for a daily series

    @property
    def unofficial(self) -> bool:
        return self.source in sources.UNOFFICIAL_SOURCES


def _fetch_one(s: config.Series, store: Store, today: date) -> Result:
    errors = []
    for rank, spec in enumerate(s.sources):
        last = store.last_date(s.id, spec.source, spec.code)
        start = (last.date() - timedelta(days=REFETCH_OVERLAP) if last is not None
                 else today - timedelta(days=365 * HISTORY_YEARS + 10))
        try:
            ser, _ = sources.FETCHERS[spec.source](spec.code, start)
            store.upsert(s.id, spec.source, spec.code, ser)
            full = store.load(s.id, spec.source, spec.code) * s.scale
            return Result(s, full, spec.source, spec.code, fallback=rank > 0,
                          monthly=spec.note == "monthly",
                          error="; ".join(errors))
        except sources.MissingKey as e:
            errors.append(f"{spec.source}: {e}")
        except Exception as e:  # noqa: BLE001 - any source failure is contained
            msg = redact(f"{type(e).__name__}: {e}".splitlines()[0])[:300]
            log.warning("%s via %s:%s failed: %s", s.id, spec.source, spec.code, msg)
            store.log_error(s.id, spec.source, spec.code, msg)
            errors.append(f"{spec.source}: {msg}")

    err = " | ".join(errors) or "no sources configured"
    cached = store.best_cached(s.id)
    if cached:
        src, code = cached
        spec = next((x for x in s.sources if (x.source, x.code) == cached), None)
        full = store.load(s.id, src, code) * s.scale
        log.error("%s: all sources failed, using cached %s:%s", s.id, src, code)
        return Result(s, full, src, code, failed=True, error=err, from_cache=True,
                      monthly=bool(spec and spec.note == "monthly"))
    log.error("%s: all sources failed and nothing cached", s.id)
    return Result(s, None, failed=True, error=err)


def _guarded(s: config.Series, store: Store, today: date) -> Result:
    try:
        return _fetch_one(s, store, today)
    except Exception as e:  # noqa: BLE001 - last line of defence for one series
        log.exception("%s: unexpected error", s.id)
        return Result(s, None, failed=True, error=redact(f"{type(e).__name__}: {e}")[:300])


def fetch_all(cfg: dict, store: Store, workers: int = 6) -> dict[str, Result]:
    sources.FRED_METADATA = False
    today = date.today()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {s.id: ex.submit(_guarded, s, store, today) for s in cfg["series"]}
        return {sid: f.result() for sid, f in futs.items()}


# ----------------------------------------------------------------- derived
def realized_vol(s: pd.Series, window: int) -> pd.Series:
    """Annualized stdev of daily changes, in bp/yr (input in %)."""
    return (s.diff() * 100).rolling(window).std() * (252 ** 0.5)


def compute_derived(cfg: dict, results: dict[str, Result]) -> dict[str, Result]:
    for d in cfg["derived"]:
        ins = {i: results.get(i) for i in d.inputs}
        bad = [i for i, r in ins.items() if r is None or r.series is None or r.series.empty]
        if bad:
            results[d.id] = Result(d, None, "derived", d.expr or d.fn, failed=True,
                                   error=f"missing inputs: {', '.join(bad)}")
            continue
        try:
            if d.fn == "realized_vol":
                out = realized_vol(ins[d.inputs[0]].series, d.window)
            else:
                ser = {i: r.series for i, r in ins.items()}
                if any(r.monthly for r in ins.values()):
                    # a monthly stand-in can only be compared month-end to month-end
                    ser = {i: s.resample("ME").last().dropna() for i, s in ser.items()}
                joined = [i for i in d.inputs if i not in d.ffill]
                df = pd.concat({i: ser[i] for i in joined}, axis=1, join="inner")
                for i in d.ffill:  # as-of join: carry the latest known value forward
                    df[i] = ser[i].reindex(df.index.union(ser[i].index)).ffill().reindex(df.index)
                out = df.eval(d.expr)
            out = out.dropna()
            parents = list(ins.values())
            results[d.id] = Result(
                d, out, "derived", d.expr or f"{d.fn}({d.inputs[0]}, {d.window})",
                failed=any(p.failed for p in parents),
                from_cache=any(p.from_cache for p in parents),
                monthly=any(p.monthly for p in parents),
                error="; ".join(f"{p.spec.id}: input failed" for p in parents if p.failed))
            # an "unofficial" input taints the derived metric
            results[d.id].source = ("derived*" if any(p.unofficial for p in parents)
                                    else "derived")
        except Exception as e:  # noqa: BLE001
            results[d.id] = Result(d, None, "derived", failed=True, error=str(e)[:200])
    return results


sources.UNOFFICIAL_SOURCES.add("derived*")
