# Macro Monitor

A personal macro / markets / news dashboard, rebuilt once each weekday morning from free public data. It shows **daily-close values only, never intraday**. Every row shows its own as-of date. Anything older than its staleness threshold is grayed out and tagged `STALE`.

`python run.py` fetches every series and caches it in SQLite. It then computes the derived metrics, pulls news headlines, and writes:

- `output/dashboard.html`: one self-contained file with all CSS, JS, charts and data inline. No server or CDN needed, so you can open it offline or email it.
- `output/snapshot.json`: the same data as JSON.

## Setup

```bash
cd Economics-and-Markets-Dashboard
python3 -m venv .venv && source .venv/bin/activate      # Python 3.9+ (tested 3.9-3.13)
pip install -r requirements.txt
cp .env.example .env        # then put your FRED key in .env
python verify_sources.py    # checks every series ID / feed and prints a status table
python run.py --open        # build and open the dashboard
```

To preview the layout without network access, run `python run.py --demo`. It renders the same page from **synthetic** data, writes to a separate `data/demo.sqlite`, and puts a red DEMO banner across the top.

### `.env`

```
FRED_API_KEY=...        # required - free at https://fred.stlouisfed.org/docs/api/api_key.html
EIA_API_KEY=            # optional - official EIA fallback for Brent/WTI/Henry Hub
BDF_API_KEY=            # optional - daily OAT 10y from Banque de France Webstat
```

`.env` is gitignored. Keys are never hardcoded, and they are scrubbed from any error message that ends up in the logs or on the page.

## Files

| Path | What it is |
|---|---|
| `series.yaml` | Every series: id, display name, panel, units, frequency, change convention, and an ordered list of sources (primary first, then fallbacks). Also holds the derived metrics and the FRED release IDs used for the calendar. |
| `feeds.yaml` | RSS feeds by news group, with optional keyword filters |
| `calendar.yaml` | FOMC / ECB / BoE / BoJ meeting dates (**update yearly**) |
| `run.py` | One command that runs everything |
| `verify_sources.py` | Checks every source, every fallback, the release IDs and the RSS feeds |
| `macrodash/sources.py` | One fetcher per data source |
| `macrodash/pipeline.py` | Fetch with fallbacks, cache, derived metrics |
| `macrodash/metrics.py` | Changes, z-scores, staleness, curve, heatmap |
| `macrodash/templates/dashboard.html` | Page template with a small inline SVG chart library |
| `scheduling/` | launchd and cron setup |
| `data/cache.sqlite` | Raw observation cache; history accumulates over time (gitignored) |
| `logs/run.log` | Rotating run log (gitignored) |

## Adding a series

Add one line to `series.yaml` under `series:`. For example, to add the 3-year UST yield:

```yaml
  - {id: ust_3y, name: UST 3Y, panel: us_rates, units: "%", freq: daily, change: bps,
     sources: [{source: fred, code: DGS3}, {source: treasury, code: "3 Yr"}]}
```

- `change`: `bps` for yields and spreads quoted in %, `pct` for prices, `abs` for indices, levels, and spreads already in bps.
- `freq`: sets the staleness threshold. Defaults are 3 business days for daily, 8 for weekly and 45 for monthly; override per series with `stale_bdays`.
- `scale`: multiplies the raw values (e.g. `0.001` for FRED $mn → $bn).
- `dp`: forces the number of decimal places shown.
- `tags`:
  - `curve` adds the series to the UST curve chart;
  - `sector` puts it in the sector heatmap instead of the table;
  - `hidden` fetches it only as an input to derived metrics.
- Sources: `fred`, `treasury`, `nyfed_rates`, `nyfed_acm`, `ecb` (`FLOW/KEY`), `bundesbank` (`FLOW/KEY`), `banque_de_france`, `boe` (IADB code), `mof_jgb` (tenor column), `eia`, `gpr` (`daily:COL` / `monthly:COL`), `yfinance` (ticker; shown as **UNOFF**). Add `note: monthly` to a source that stands in for a daily series at lower frequency.

To add a derived metric, add an entry under `derived:`, e.g. `expr: "(btp_10y - bund_10y) * 100"`. List any inputs that should be carried forward as-of (rather than inner-joined) under `ffill`.

Run `python verify_sources.py --only ust_3y` to check a new series. Remove a series by deleting its line. Panels pick up the change automatically, though charts that name a removed id will show "No data".

## Scheduling: weekdays 06:30 America/New_York

`scheduling/run_daily.sh` runs `run.py` with the project's `.venv` and appends output to `logs/scheduler.log`. Pick one of the two options below.

### Option A: launchd (recommended on macOS)

```bash
./scheduling/install_launchd.sh
launchctl kickstart -k gui/$(id -u)/com.macrodash.daily   # run once now to test
```

This installs `~/Library/LaunchAgents/com.macrodash.daily.plist`, which fires Mon–Fri at 06:30.

- **launchd uses the Mac's local time zone.** If your Mac isn't on Eastern time, edit `Hour` in the plist (e.g. 11 for London; mind DST differences) and re-run the installer.
- If the Mac is asleep at 06:30, launchd runs the job once on wake. Cron does not.
- To remove it: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.macrodash.daily.plist`.
- macOS may ask for Full Disk Access if the repo lives in `~/Documents` or `~/Desktop`. Keeping it elsewhere (e.g. `~/code`) avoids that.

### Option B: cron

```
crontab -e
# Linux (cronie honours CRON_TZ):
CRON_TZ=America/New_York
30 6 * * 1-5  /full/path/to/Economics-and-Markets-Dashboard/scheduling/run_daily.sh
```

macOS cron ignores `CRON_TZ`, uses local time, and skips runs while the machine sleeps. See `scheduling/crontab.example`.

Why 06:30 ET? US closes from the prior day are final, and FRED has posted the previous day's H.15, OAS and VIX. European and Asian markets are mid-session for *today*, so the yfinance fetcher drops any bar dated today until 18:00 in that exchange's time zone. The result is that an intraday print is never shown as a close.

## Display conventions

- **Row:** name · last value · as-of date · 1d / 1w / 1m / YTD change · 1-year sparkline. Yields and spreads change in **bp**, prices in **%**.
  - 1d is shown for daily series only.
  - 1w is computed from the value as of 7 calendar days before the last observation.
  - YTD is measured from the last value of the prior year.
- **Stale:** a row is grayed out and tagged `STALE` when its last observation is older than its threshold (3 business days for daily series), or when this run's fetch failed and the cached value is shown (`FAIL`).
- **Anomalies:** the latest change is scored as a z-score against the trailing one-year distribution of changes (daily, or weekly for weekly series). Cells with |z| > 2 are highlighted. The **What moved** strip lists the 8 largest |z| among fresh series. Click a chip to jump to its row.
- **Tags:**
  - `W` weekly, `M` monthly;
  - `UNOFF` unofficial source (Yahoo Finance);
  - `FB` a fallback source was used;
  - `FAIL` the fetch failed this run.
- **Header:** the timestamp of the last update and a count of failed sources. Click the count for details.

## Robustness

- Each series is fetched independently, in a thread pool, with a final catch-all around each one. A failed source is logged and the next candidate source is tried. If every source fails, the last cached value is shown, marked `FAIL`/`STALE`.
- HTTP: retries with exponential backoff on 429/5xx (honouring `Retry-After`), per-host rate limits (FRED below 120 req/min), and a per-run circuit breaker so an unreachable host fails fast instead of retrying for every series.
- Each run re-pulls the last 14 days to pick up revisions. The first run backfills 5 years.
- The cache is keyed by source as well as series, so a monthly fallback never mixes into a daily history.
- Run the tests with `python -m pytest -q tests`.

## Data sources and known gaps

| Panel | Series | Source | Notes / gaps |
|---|---|---|---|
| US Rates | DGS1MO…DGS30, T10Y2Y, T10Y3M, DFII10, T10YIE, T5YIFR, DFF | FRED (Treasury fallback for the curve) | |
| | SOFR | NY Fed Markets API (FRED fallback) | |
| | 10y term premium | NY Fed ACM `ACMTP10` (daily sheet) | Falls back to Kim-Wright `THREEFYTP10` on FRED |
| Sovereigns | Bund 10y | Bundesbank `BBSIS` daily | Svensson-estimated yield, not one benchmark bond |
| | Gilt 10y | BoE IADB `IUDMNPY` (par), `IUDMNZC` fallback | |
| | JGB 10y | MOF Japan CSV | |
| | OAT 10y | Banque de France (needs `BDF_API_KEY`) → ECB monthly | **Monthly unless the BdF key is set** |
| | BTP 10y | ECB `IRS` monthly | **No free daily official source found** |
| Credit | IG/HY OAS, AAA…CCC, EM | FRED (ICE BofA) | **US sector OAS not on FRED**. FRED's ICE history may be limited to recent years |
| Vol | VIXCLS, NFCI | FRED | MOVE not free → 20d realized vol of DGS10 as a proxy |
| Liquidity | WALCL, WTREGEN, RRPONTSYD | FRED | Net liquidity computed weekly (Wed) |
| FX | DTWEXBGS (Fed broad index, **not DXY**), DEX* | FRED (H.10) | H.10 is published weekly, so values lag up to ~1 week |
| | USDTRY | yfinance `TRY=X` | Unofficial |
| Commodities | Brent, WTI, Henry Hub | FRED (EIA), EIA API fallback | EIA spot lags several days |
| | TTF, gold, copper, wheat | yfinance front-month futures | Unofficial. Gold/copper daily are not on FRED |
| Equities | S&P 500, Nasdaq, Nikkei | FRED | |
| | Russell 2000, Stoxx 600, EEM, RSP, SPY, sector SPDRs | yfinance | Unofficial. Russell series were removed from FRED |
| Macro | GDPNow | FRED `GDPNOW` | |
| | Release calendar | FRED `releases/dates` | ISM isn't on FRED, so its dates come from a rule (1st/3rd business day) |
| | Central bank meetings | `calendar.yaml` | Hardcoded. **Update yearly** |
| Geopolitics | GPR daily and monthly | matteoiacoviello.com XLS | |
| News | see `feeds.yaml` | Fed, ECB, BoE, BoJ, BLS, BEA, EIA official RSS + CNBC, Guardian, BBC, Nikkei Asia, SCMP, Al Jazeera, Al-Monitor, MEE, OilPrice, MarketWatch, Yahoo, FT | Reuters discontinued public RSS |
