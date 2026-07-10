# Lafayette 911 Traffic

A self-hosted service that collects live 911 traffic incidents for Lafayette
Parish, Louisiana, stores them permanently without duplicates, and publishes
an interactive incident-intelligence map.

The map is a single static HTML page (plus one data file) that can be served
by any web server — no application server, no database exposed to the web.

## How it works

```
                        ┌──────────────────────────────────────────────┐
                        │                 service loop                 │
                        │              (lafayette911/main.py)          │
                        └──────────────┬───────────────┬───────────────┘
                                       │ every cycle   │ when new data
                        ┌──────────────▼──────────┐ ┌──▼───────────────────┐
   lafayette911.org ───►│        COLLECTOR        │ │       RENDERER       │
   (public feed)        │ fetch → dedupe → geocode│ │ SQLite/CSV → data.js │
   Google Geocoding ───►│ → enrich → persist      │ │ → traffic_map.html   │
   NWS weather/alerts──►│ (collector.py,          │ │ (map_render.py,      │
                        │  fetch_incidents.py,    │ │  map_template.py)    │
                        │  state_store.py,        │ └──────────────────────┘
                        │  enrichment.py)         │
                        └─────────────────────────┘
```

**The collector is the product.** Everything else is derived from the data it
writes. It runs on a fixed interval (default: every 5 minutes) and provides
three hard guarantees:

1. **No duplicates.** Incidents are keyed twice: by an exact synthesized id
   *and* by a whitespace/case-normalized content key, so even feed formatting
   changes cannot re-insert an incident (`state_store.py`).
2. **Bounded API spend.** Google Geocoding calls draw from a persistent
   rolling-24-hour budget. Addresses that repeatedly fail are blacklisted
   per-address; a budget reserve keeps brand-new incidents ahead of backlog
   retries.
3. **Nothing is lost.** Incidents that can't be geocoded yet (budget
   exhausted, API trouble) are stored without coordinates, shown in the map's
   feed as "locating…", and drained automatically when budget frees up.

## Module map

| Module | Responsibility |
| --- | --- |
| `lafayette911/config.py` | All configuration, from environment variables |
| `lafayette911/collector.py` | One collection cycle: fetch → dedupe → geocode → enrich → persist |
| `lafayette911/fetch_incidents.py` | Feed HTTP/parsing and Google Geocoding client (with caching) |
| `lafayette911/state_store.py` | SQLite + CSV persistence, dedupe indexes, geocode budget & blacklist |
| `lafayette911/enrichment.py` | Pure derived fields: time context, holidays, school days, road type |
| `lafayette911/weather.py` | NWS weather snapshots and active-alert flags |
| `lafayette911/map_render.py` | Builds `traffic_data.js` + `traffic_map.html` from stored data |
| `lafayette911/map_template.py` | The self-contained interactive web page (Leaflet, no build step) |
| `lafayette911/main.py` | Service loop wiring collector + renderer |

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_API_KEY=your-geocoding-api-key   # optional but recommended
python lafayette911org.py
```

Outputs land next to the script: `traffic_incidents.csv` (the permanent
archive), `incident_index.sqlite` (working store), `traffic_map.html` +
`traffic_data.js` (the website — serve these two files).

Optional: `pip install osmnx` enables OSM-derived road classifications and
intersection hotspots.

### Running as a systemd service

```ini
[Unit]
Description=Lafayette 911 Traffic Service
After=network-online.target

[Service]
WorkingDirectory=/home/pi/Lafayette-911-Traffic
Environment=GOOGLE_API_KEY=...
ExecStart=/home/pi/Lafayette-911-Traffic/.venv/bin/python lafayette911org.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Configuration

Everything is an environment variable with a working default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GOOGLE_API_KEY` | *(empty)* | Google Geocoding key; without it incidents queue as unlocated |
| `LAF911_MODE` | `all` | `all`, `fetcher` (collect only) or `renderer` (page only) |
| `LAF911_SLEEP_SECONDS` | `300` | Cycle interval |
| `LAF911_RENDER_SOURCE` | `db` | Render from `db` (SQLite) or `csv` |
| `LAF911_RENDER_ONLY_ON_NEW` | `true` | Skip re-rendering when nothing changed |
| `LAF911_GEOCODE_MAX_REQUESTS_PER_24H` | `100` | Rolling 24 h Google call budget (persisted, survives restarts) |
| `LAF911_GEOCODE_RETRY_UNLOCATED_ENABLED` | `true` | Drain the unlocated backlog automatically |
| `LAF911_GEOCODE_RETRY_BATCH` | `25` | Backlog incidents examined per cycle |
| `LAF911_GEOCODE_RETRY_RESERVE` | `25` | Budget slots reserved for brand-new incidents |
| `LAF911_GEOCODE_FAILURE_MAX_ATTEMPTS` | `3` | Failures before an address is blacklisted |
| `LAF911_GEOCODE_FAILURE_RETRY_DAYS` | `7` | Blacklist expiry |
| `LAF911_WEATHER_ENABLED` | `true` | Attach NWS weather snapshots to new incidents |
| `LAF911_ALERTS_ENABLED` | `true` | Attach NWS active-alert flags (zone LAZ034) |
| `LAF911_BASE_DIR` / `LAF911_CSV_PATH` / `LAF911_MAP_PATH` / `LAF911_DATAJS_PATH` / `LAF911_DB_PATH` / `LAF911_OSM_CACHE` | *(script dir)* | File locations |
| `LAF911_LOG_LEVEL` | `INFO` | Structured JSON logs on stdout |

Less common: `LAF911_FETCH_TIMEOUT`, `LAF911_GEOCODE_SLEEP`,
`LAF911_RENDER_SUBPROCESS`, `LAF911_RENDER_SUBPROCESS_TIMEOUT`,
`LAF911_WEATHER_LAT/LON`, `LAF911_WEATHER_CACHE_TTL_SECONDS`,
`LAF911_ALERTS_CACHE_TTL_SECONDS`, `LAF911_TRACEMALLOC_*`,
`LAF911_GC_COLLECT`, `LAF911_OSM_CACHE_TTL_SECONDS`,
`LAF911_OSM_INTERSECTION_SUBPROCESS`.

## The map

Dark/light Liquid-Glass UI with category-colored incidents, browsable
same-location incident stacks, fuzzy road search, quick time ranges, a full
analytics tab (hour × day heatmap, trends, seasonality, category mix, top
corridors, normalized rush-hour/school-day rates), a live feed with pending
"locating…" entries, live NWS weather and alerts, pulse beacons on incidents
from the last 2 hours, and background data refresh every 5 minutes. Filters
persist in the URL hash.

Basemaps are CARTO (light/dark) with an OpenStreetMap fallback; popups link
out to Google Maps, Street View and Waze via plain URLs. None of the map's
runtime features consume paid API quota.

## Publish to GitHub Pages (free)

The map is two static files, so it can be hosted for free on GitHub Pages at
`https://<user>.github.io/<repo>/`. The running service keeps them fresh by
force-pushing them to a dedicated `gh-pages` branch via
[`scripts/publish_pages.sh`](scripts/publish_pages.sh) — the source stays on
`main`, the published site stays a single flat commit, and nothing sensitive
is ever exposed (the map contains no API keys).

**One-time setup**

1. **Make the repo public** (Pages is free for public repos) — Settings →
   General → Danger Zone → Change visibility.
2. **Give the machine push access** to the repo without a password prompt.
   The simplest is a repo deploy key:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/laf911_deploy -N ""       # on the Pi
   cat ~/.ssh/laf911_deploy.pub
   ```
   Add that public key under repo Settings → Deploy keys → **Add deploy key**,
   with **Allow write access** checked. Then point the repo's `origin` at SSH
   and use the key:
   ```bash
   git -C /path/to/Lafayette-911-Traffic remote set-url origin \
       git@github.com:<user>/<repo>.git
   echo 'Host github.com
     IdentityFile ~/.ssh/laf911_deploy
     IdentitiesOnly yes' >> ~/.ssh/config
   ```
3. **Publish once** to create the branch, then enable Pages:
   ```bash
   scripts/publish_pages.sh
   ```
   Settings → Pages → Build and deployment → **Deploy from a branch** →
   `gh-pages` / `/ (root)` → Save. Your map appears at
   `https://<user>.github.io/<repo>/` within a minute.

**Keep it updated** with a cron entry (publishes only when the data actually
changed, so it's safe to run often):

```cron
*/15 * * * * cd /path/to/Lafayette-911-Traffic && scripts/publish_pages.sh >> /tmp/laf911_pages.log 2>&1
```

The public map then trails the live data by at most ~15 minutes. Every
publish records a deployment under the repo's `github-pages` environment, so
a shorter interval means more of those history entries; 15 minutes keeps the
public map fresh while holding the deployment count to a handful per hour.
(The entries are harmless — only the latest deployment is ever live — so pick
whatever interval you prefer.)

## External services

| Service | Used for | Cost notes |
| --- | --- | --- |
| lafayette911.org feed | Incident source | public feed, polled gently |
| Google Geocoding API | Address → coordinates | budgeted; aggressive caching & blacklist |
| NWS api.weather.gov | Weather snapshots, active alerts, live map weather | free, no key |
| CARTO / OpenStreetMap tiles | Basemaps | free tiers; attribution kept |
| GitHub Pages (optional) | Public hosting of the map | free for public repos |

## Development

```bash
python -m pytest tests/ -q
```

The test suite covers dedupe (including feed formatting jitter), geocode
budgeting/blacklisting/queueing, date parsing, CSV/DB rendering, and template
generation. When changing the web page, `map_template.py` is a plain Python
string with `__TOKEN__` substitution — no build step required.
