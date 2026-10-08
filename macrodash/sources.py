"""Fetchers for every data source.

Each fetcher takes (code, start) and returns (pd.Series, meta). The Series has a
tz-naive DatetimeIndex (observation date) and float values, sorted ascending,
NaNs dropped. ``meta`` is a dict of optional extras (title, notes, ...).

Fetchers raise:
  MissingKey  -> an optional API key is not configured (source skipped, not failed)
  SourceError -> anything else that went wrong
"""
from __future__ import annotations

import io
import os
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

from . import http

NY = ZoneInfo("America/New_York")


class SourceError(Exception):
    pass


class MissingKey(SourceError):
    pass


def _clean(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    s.index = pd.DatetimeIndex(pd.to_datetime(s.index)).tz_localize(None).normalize()
    s = s[~s.index.duplicated(keep="last")].sort_index().dropna()
    if s.empty:
        raise SourceError("no observations returned")
    return s.astype(float)


def _key(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise MissingKey(f"{name} not set in .env")
    return v


# --------------------------------------------------------------------------- FRED
FRED = "https://api.stlouisfed.org/fred"


def fred_get(path: str, **params) -> dict:
    params.update(api_key=_key("FRED_API_KEY"), file_type="json")
    r = http.get(f"{FRED}/{path}", params=params)
    return r.json()


def fred(code: str, start: date):
    meta = {}
    try:
        info = fred_get("series", series_id=code)["seriess"][0]
        meta = {
            "title": info.get("title"),
            "frequency": info.get("frequency_short"),
            "units": info.get("units_short"),
            "observation_end": info.get("observation_end"),
            "last_updated": info.get("last_updated"),
            "discontinued": "DISCONTINUED" in info.get("title", "").upper(),
        }
    except Exception as e:  # metadata is nice-to-have; observations call decides
        if "400" in str(e):
            raise SourceError(f"FRED series {code} does not exist") from e
    obs = fred_get("series/observations", series_id=code,
                   observation_start=start.isoformat())["observations"]
    s = pd.Series({o["date"]: o["value"] for o in obs})  # "." -> NaN via _clean
    return _clean(s), meta


# ----------------------------------------------------------------- US Treasury
@lru_cache(maxsize=None)
def _treasury_year(year: int) -> pd.DataFrame:
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{year}/all")
    r = http.get(url, params={"type": "daily_treasury_yield_curve",
                              "field_tdr_date_value": year, "page": "", "_format": "csv"})
    df = pd.read_csv(io.StringIO(r.text))
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    return df.set_index("Date")


def treasury(code: str, start: date):
    """code = Treasury column header, e.g. '10 Yr', '3 Mo'."""
    frames = [_treasury_year(y) for y in range(start.year, date.today().year + 1)]
    df = pd.concat(frames)
    if code not in df.columns:
        raise SourceError(f"column {code!r} not in Treasury CSV ({list(df.columns)[:6]}...)")
    return _clean(df[code]), {"title": f"Treasury par yield {code}"}


# ------------------------------------------------------------------ NY Fed
def nyfed_rates(code: str, start: date):
    """code = path under /api/rates, e.g. 'secured/sofr' or 'unsecured/effr'."""
    url = f"https://markets.newyorkfed.org/api/rates/{code}/search.json"
    r = http.get(url, params={"startDate": start.isoformat(),
                              "endDate": date.today().isoformat()})
    rows = r.json().get("refRates", [])
    s = pd.Series({x["effectiveDate"]: x.get("percentRate") for x in rows})
    return _clean(s), {"title": f"NY Fed {code}"}


ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"


def nyfed_acm(code: str, start: date):
    """code = column in the ACM workbook, e.g. 'ACMTP10'. Uses the daily sheet."""
    r = http.get(ACM_URL, timeout=60)
    sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
    best = None
    for name, df in sheets.items():
        cols = {str(c).strip().upper(): c for c in df.columns}
        if code.upper() in cols:
            date_col = next((cols[c] for c in cols if "DATE" in c), df.columns[0])
            s = pd.Series(df[cols[code.upper()]].values,
                          index=pd.to_datetime(df[date_col], errors="coerce"))
            s = s[s.index.notna()]
            if best is None or len(s) > len(best[1]):  # daily sheet has most rows
                best = (name, s)
    if best is None:
        raise SourceError(f"{code} not found in ACM workbook sheets {list(sheets)}")
    s = _clean(best[1])
    return s[s.index >= pd.Timestamp(start)], {"title": f"ACM {code} (sheet {best[0]})"}


# ---------------------------------------------------------------------- ECB
def ecb(code: str, start: date):
    """code = 'FLOW/KEY', e.g. 'IRS/M.IT.L.L40.CI.0000.EUR.N.Z'."""
    flow, key = code.split("/", 1)
    url = f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}"
    r = http.get(url, params={"format": "csvdata", "startPeriod": start.isoformat(),
                              "detail": "dataonly"})
    if not r.text.strip():
        raise SourceError("empty response (no data for key)")
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.Series(df["OBS_VALUE"].values, index=_period_index(df["TIME_PERIOD"]))
    return _clean(s), {"title": f"ECB {code}"}


def _period_index(p: pd.Series) -> pd.DatetimeIndex:
    """SDMX periods: '2026-10-07', '2026-09' (monthly -> month end), '2026-Q2'."""
    p = p.astype(str)
    if p.str.len().eq(7).all():
        return pd.DatetimeIndex(pd.PeriodIndex(p, freq="M").to_timestamp(how="end")).normalize()
    if p.str.contains("Q").all():
        return pd.DatetimeIndex(pd.PeriodIndex(p.str.replace("-", ""), freq="Q")
                                .to_timestamp(how="end")).normalize()
    return pd.DatetimeIndex(pd.to_datetime(p))


# ---------------------------------------------------------------- Bundesbank
def bundesbank(code: str, start: date):
    """code = 'FLOW/KEY', e.g. 'BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A'."""
    flow, key = code.split("/", 1)
    url = f"https://api.statistiken.bundesbank.de/rest/data/{flow}/{key}"
    r = http.get(url, params={"startPeriod": start.isoformat(), "detail": "dataonly"},
                 headers={"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"})
    df = pd.read_csv(io.StringIO(r.text), sep=None, engine="python")
    if "OBS_VALUE" not in df.columns:
        raise SourceError(f"unexpected Bundesbank payload: {r.text[:120]!r}")
    s = pd.Series(df["OBS_VALUE"].values, index=_period_index(df["TIME_PERIOD"]))
    return _clean(s), {"title": f"Bundesbank {code}"}


# ----------------------------------------------------------- Banque de France
def banque_de_france(code: str, start: date):
    """Webstat Opendatasoft API (free key). code = series key, e.g.
    'FM.D.FR.EUR.FR2.BB.FR10YT_RR.YLD' (daily TEC10 / OAT 10y constant maturity)."""
    key = _key("BDF_API_KEY")
    url = ("https://webstat.banque-france.fr/api/explore/v2.1/catalog/datasets/"
           "observations/exports/json")
    r = http.get(url, params={
        "where": f'series_key="{code}" and time_period_start>=date\'{start.isoformat()}\'',
        "select": "time_period_start,obs_value",
    }, headers={"Authorization": f"Apikey {key}"}, timeout=60)
    rows = r.json()
    s = pd.Series({x["time_period_start"]: x["obs_value"] for x in rows})
    return _clean(s), {"title": f"Banque de France {code}"}


# ----------------------------------------------------------- Bank of England
def boe(code: str, start: date):
    """code = IADB series code, e.g. 'IUDMNPY' (10y nominal par gilt yield)."""
    url = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
    r = http.get(url, params={
        "csv.x": "yes", "Datefrom": start.strftime("%d/%b/%Y"), "Dateto": "now",
        "SeriesCodes": code, "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N",
    })
    if "<html" in r.text[:500].lower():
        raise SourceError("BoE returned HTML instead of CSV (blocked or bad code)")
    df = pd.read_csv(io.StringIO(r.text))
    if code not in df.columns:
        raise SourceError(f"{code} not in BoE response columns {list(df.columns)}")
    idx = pd.to_datetime(df["DATE"], format="%d %b %Y")
    return _clean(pd.Series(df[code].values, index=idx)), {"title": f"BoE IADB {code}"}


# ------------------------------------------------------------ MOF Japan (JGB)
MOF_BASE = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"


@lru_cache(maxsize=None)
def _mof_csv(name: str) -> pd.DataFrame:
    raw = http.get(MOF_BASE + name, timeout=60).content
    for enc in ("utf-8-sig", "shift_jis", "cp932", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.split(",")[0].strip().lower() == "date")
    df = pd.read_csv(io.StringIO("\n".join(lines[hdr:])), na_values=["-", ""])
    df["Date"] = pd.to_datetime(df["Date"], format="mixed", errors="coerce")
    return df.dropna(subset=["Date"]).set_index("Date")


def mof_jgb(code: str, start: date):
    """code = tenor column, e.g. '10Y'. Merges historical file and current month."""
    frames = []
    errors = []
    for f in ("historical/jgbcme_all.csv", "jgbcme.csv"):
        try:
            frames.append(_mof_csv(f))
        except Exception as e:
            errors.append(f"{f}: {e}")
    if not frames:
        raise SourceError("; ".join(errors))
    df = pd.concat(frames)
    if code not in df.columns:
        raise SourceError(f"{code} not in MOF columns {list(df.columns)}")
    s = _clean(df[code])
    return s[s.index >= pd.Timestamp(start)], {"title": f"MOF JGB {code}"}


# ----------------------------------------------------------------------- EIA
def eia(code: str, start: date):
    """code = EIA v1-style series id, e.g. 'PET.RBRTE.D' (Brent spot)."""
    key = _key("EIA_API_KEY")
    r = http.get(f"https://api.eia.gov/v2/seriesid/{code}",
                 params={"api_key": key, "start": start.isoformat()})
    rows = r.json()["response"]["data"]
    s = pd.Series({x["period"]: x["value"] for x in rows})
    return _clean(s), {"title": f"EIA {code}"}


# -------------------------------------------- Caldara-Iacoviello GPR index
GPR_FILES = {
    "daily": "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
    "monthly": "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls",
}


@lru_cache(maxsize=None)
def _gpr_frame(kind: str) -> pd.DataFrame:
    r = http.get(GPR_FILES[kind], timeout=60)
    df = pd.read_excel(io.BytesIO(r.content))
    df.columns = [str(c).strip() for c in df.columns]
    low = {c.lower(): c for c in df.columns}
    dcol = next((low[c] for c in ("date", "day", "month") if c in low), df.columns[0])
    d = df[dcol]
    if pd.api.types.is_numeric_dtype(d) and d.dropna().astype(int).astype(str).str.len().eq(8).all():
        idx = pd.to_datetime(d.astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    else:
        idx = pd.to_datetime(d, errors="coerce")
    return df.set_index(pd.DatetimeIndex(idx)).loc[lambda x: x.index.notna()]


def gpr(code: str, start: date):
    """code = 'daily:GPRD', 'daily:GPRD_MA30' or 'monthly:GPR'."""
    kind, col = code.split(":", 1)
    df = _gpr_frame(kind)
    if col not in df.columns:
        raise SourceError(f"{col} not in GPR {kind} file columns {list(df.columns)[:12]}")
    s = _clean(df[col])
    if kind == "monthly":  # stamp monthly obs at month end, like the other monthly series
        s.index = s.index + pd.offsets.MonthEnd(0)
    return s[s.index >= pd.Timestamp(start)], {"title": f"GPR {kind} {col}"}


# ---------------------------------------------- yfinance (unofficial fallback)
def yfinance(code: str, start: date):
    """Daily closes from Yahoo Finance. Drops today's bar unless that market's local
    clock is past 18:00, so an intraday print is never shown as a close."""
    import yfinance as yf

    t = yf.Ticker(code)
    df = t.history(start=start.isoformat(), interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise SourceError(f"yfinance returned no data for {code}")
    tz = df.index.tz or NY
    now_local = datetime.now(tz)
    last_bar = df.index[-1]
    if last_bar.date() == now_local.date() and now_local.hour < 18:
        df = df.iloc[:-1]
    s = df["Close"]
    s.index = s.index.tz_localize(None) if s.index.tz is not None else s.index
    return _clean(s), {"title": f"Yahoo Finance {code}", "exchange_tz": str(tz)}


FETCHERS = {
    "fred": fred,
    "treasury": treasury,
    "nyfed_rates": nyfed_rates,
    "nyfed_acm": nyfed_acm,
    "ecb": ecb,
    "bundesbank": bundesbank,
    "banque_de_france": banque_de_france,
    "boe": boe,
    "mof_jgb": mof_jgb,
    "eia": eia,
    "gpr": gpr,
    "yfinance": yfinance,
}

UNOFFICIAL_SOURCES = {"yfinance"}
