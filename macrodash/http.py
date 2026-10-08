"""Shared HTTP session: retries with exponential backoff and per-host rate limiting."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Browser-like UA: several official sites (BoE IADB, MOF Japan) reject default python UAs.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 macrodash/1.0"
)

# Minimum seconds between requests to the same host (FRED allows 120 req/min).
MIN_INTERVAL = {
    "api.stlouisfed.org": 0.55,
    "data-api.ecb.europa.eu": 0.3,
    "api.statistiken.bundesbank.de": 0.3,
    "www.bankofengland.co.uk": 1.0,
    "api.eia.gov": 0.3,
}
DEFAULT_INTERVAL = 0.2

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_session: requests.Session | None = None
# Hosts that failed at the connection level this run (DNS, proxy, refused).
# Later requests to them fail fast instead of burning retries on every series.
_dead_hosts: dict[str, str] = {}


class HostUnreachable(requests.ConnectionError):
    pass


_SECRET_RE = re.compile(r"((?:api_?key|apikey|token)=)[^&\s'\"]+", re.I)


def redact(msg: str) -> str:
    """Strip API keys from URLs embedded in error messages."""
    return _SECRET_RE.sub(r"\1***", msg)


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(
            total=4,
            connect=2,
            backoff_factor=1.5,  # 0, 3, 6, 12s
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers["User-Agent"] = USER_AGENT
        _session = s
    return _session


def _throttle(url: str) -> None:
    host = urlparse(url).netloc
    gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
    with _lock:
        wait = _last_hit.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout: float = 30) -> requests.Response:
    host = urlparse(url).netloc
    if host in _dead_hosts:
        raise HostUnreachable(f"{host} unreachable earlier this run: {_dead_hosts[host]}")
    _throttle(url)
    try:
        r = session().get(url, params=params, headers=headers, timeout=timeout)
    except requests.ConnectionError as e:
        msg = redact(str(e))
        _dead_hosts[host] = msg.splitlines()[0][:200]
        raise requests.ConnectionError(msg) from None
    except requests.RequestException as e:
        raise type(e)(redact(str(e))) from None
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(redact(str(e)), response=r) from None
    return r
