from datetime import date

import numpy as np
import pandas as pd

from macrodash import config, metrics, pipeline
from macrodash.http import redact


def _spec(**kw):
    base = dict(id="x", name="X", panel="us_rates", units="%", freq="daily", change="bps")
    base.update(kw)
    return config.Series(**base)


def _bdays(vals, end="2026-10-07"):
    return pd.Series(vals, index=pd.bdate_range(end=end, periods=len(vals)), dtype=float)


def test_changes_bps_and_staleness():
    s = _bdays(np.linspace(4.0, 4.5, 300))
    row = metrics.build_row(pipeline.Result(_spec(), s, "fred", "DGS10"), date(2026, 10, 8))
    assert row["as_of"] == "2026-10-07" and not row["stale"]
    step = 0.5 / 299 * 100
    assert abs(row["chg"]["d1"] - step) < 1e-3  # display rounding: 4 sig figs
    assert abs(row["chg"]["w1"] - 5 * step) < 1e-3
    old = metrics.build_row(pipeline.Result(_spec(), s, "fred", "DGS10"), date(2026, 10, 14))
    assert old["stale"] and old["bdays_old"] == 5


def test_pct_change_and_ytd():
    s = _bdays([100.0] * 250 + [110.0])
    row = metrics.build_row(pipeline.Result(_spec(change="pct", units="index"), s, "y", "Z"),
                            date(2026, 10, 8))
    assert abs(row["chg"]["d1"] - 10) < 1e-9 and abs(row["chg"]["ytd"] - 10) < 1e-9


def test_zscore_flags_outlier():
    rng = np.random.default_rng(0)
    vals = np.cumsum(rng.normal(0, 0.05, 400)) + 4
    vals[-1] = vals[-2] + 0.40  # ~8 sigma
    z = metrics.zscore("bps", _bdays(vals))
    assert z is not None and z > 5


def test_failed_series_is_stale_even_if_recent():
    s = _bdays(np.ones(50))
    row = metrics.build_row(pipeline.Result(_spec(), s, "fred", "X", failed=True,
                                            from_cache=True), date(2026, 10, 8))
    assert row["stale"]


def test_derived_spread_and_monthly_alignment():
    cfg = {"derived": [_spec(id="sp", inputs=["a", "b"], expr="(a - b) * 100", units="bps",
                             change="abs")]}
    daily = _bdays(np.full(100, 2.5))
    monthly = pd.Series([3.0, 3.1], index=pd.DatetimeIndex(["2026-08-31", "2026-09-30"]))
    res = {"a": pipeline.Result(_spec(id="a"), monthly, "ecb", "m", monthly=True),
           "b": pipeline.Result(_spec(id="b"), daily, "bundesbank", "d")}
    out = pipeline.compute_derived(cfg, res)["sp"]
    assert out.monthly and list(out.series.round(6)) == [50.0, 60.0]


def test_redact_hides_api_key():
    msg = "400 for url: https://api.stlouisfed.org/fred/x?series_id=A&api_key=SECRET123&file_type=json"
    assert "SECRET123" not in redact(msg)


def test_config_loads():
    cfg = config.load()
    assert len(cfg["series"]) > 50 and cfg["derived"]
