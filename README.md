# Lafayette 911 Traffic

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A self-hosted service that collects live 911 traffic incidents for Lafayette
Parish, Louisiana, stores them permanently without duplicates, and publishes
an interactive incident-intelligence map.

> **Independent & unofficial.** This project is not affiliated with or
> endorsed by Lafayette 911, Lafayette Parish, NOAA/NWS, or any government
> agency. Information may be delayed, incomplete, or incorrect — never use it
> for emergency response or safety decisions. **Call 911 for emergencies.**

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
analytics tab (hour × day heatmap with exact day+hour selection, adaptive
trends, seasonality, category mix, canonical top corridors, normalized
rush-hour/school-day rates over fixed exposure windows), a live feed with
pending "locating…" entries, live NWS weather and alerts, pulse beacons on
incidents from the last 2 hours, and background data refresh. Filters are
facet-aware (every count reflects the other active filters), surfaced as
removable chips, and persist in the URL hash — including exact hour (`h=`),
corridor (`cid=`), and agencies (`ag=`). The page polls a tiny
`traffic_meta.json` first and only re-downloads the full data file when its
version hash changes. An About dialog covers disclaimers, data sources,
privacy, and licenses.

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

## Daily digest email (optional)

The service can email you a **daily 24-hour summary** — new incidents by
category (with emoji + bars), an hour-by-hour sparkline, top corridors,
responding agencies, geocoding accounting (API calls used, queue, known-bad
addresses), and service health (uptime, cycles, errors, memory). It sends at
most once per local day; the "sent" marker lives in SQLite so restarts can't
double-send, and a failing mail setup stops retrying after 3 attempts per
day so it can never spam or crash collection.

**Credentials never touch this repository.** SMTP settings come only from
environment variables on the machine that runs the service. For Gmail,
create an [App Password](https://myaccount.google.com/apppasswords)
(requires 2-Step Verification) — never your real password.

1. Put the settings in a root-owned env file **outside the repo**:
   ```bash
   sudo tee /etc/laf911-secrets.env >/dev/null <<'EOF'
   LAF911_DIGEST_ENABLED=true
   LAF911_DIGEST_SMTP_HOST=smtp.gmail.com
   LAF911_DIGEST_SMTP_PORT=587
   LAF911_DIGEST_SMTP_USER=yourname@gmail.com
   LAF911_DIGEST_SMTP_PASS=abcd efgh ijkl mnop
   LAF911_DIGEST_TO=yourname@gmail.com
   LAF911_DIGEST_HOUR=7
   LAF911_MAP_URL=https://<user>.github.io/<repo>/
   EOF
   sudo chmod 600 /etc/laf911-secrets.env
   ```
2. Point the systemd service at it (`sudo systemctl edit myscript.service`):
   ```ini
   [Service]
   EnvironmentFile=/etc/laf911-secrets.env
   ```
   then `sudo systemctl daemon-reload && sudo systemctl restart myscript.service`.
3. **Test without waiting** (uses the same env file):
   ```bash
   set -a; source /etc/laf911-secrets.env; set +a
   python -m lafayette911.daily_digest --preview digest.html   # look at it
   python -m lafayette911.daily_digest --send                  # send one now
   ```

| Variable | Default | Meaning |
| --- | --- | --- |
| `LAF911_DIGEST_ENABLED` | `false` | Master switch |
| `LAF911_DIGEST_SMTP_HOST` / `_PORT` | `smtp.gmail.com` / `587` | 587 = STARTTLS, 465 = SSL |
| `LAF911_DIGEST_SMTP_USER` / `_PASS` | *(empty)* | SMTP login (Gmail: app password) |
| `LAF911_DIGEST_TO` / `_FROM` | *(SMTP user)* | Recipient / sender |
| `LAF911_DIGEST_HOUR` | `7` | Local hour to send at/after |
| `LAF911_MAP_URL` | *(empty)* | "Open the live map" button target |

## Personal route alerts (optional)

Get an email shortly before you leave, listing the **current** 911 incidents
on the exact stretches of road you drive (plus any active NWS alert).
Different routes for the drive in and home, each on its own schedule.

**Zero Pi configuration.** If the daily digest already works, there is
nothing to set up on the Pi. Open the map, tap the **route icon**, and tap a
few stops along your drive — the line **snaps to the actual roads between
taps** (via the free [OSRM](https://project-osrm.org/) router; curves and
turns included, so side roads you merely cross never join the route; straight
lines are used if the router is unreachable). Pick departure time and days,
then tap **"Email this route to your Pi"** — the builder can remember your
collector's Gmail on your device so the compose window opens pre-addressed;
send it **from and to the same Gmail account the collector uses**. The service checks its own
mailbox every cycle (a Gmail App Password works for IMAP just like SMTP),
saves the route into SQLite, and replies "route saved" within a few minutes.
Replace a route by sending a new email for the same slot; remove one with a
body line `LAF911_ROUTE_1_DELETE=true`. Only messages **from the account
itself** with `LAF911` in the subject are honored; everything else is
ignored.

**Section-precise matching.** With a drawn route, located incidents match by
distance to your line (default 150 m, choose 100/150/250/400 in the builder) —
an accident five miles down a road you only briefly touch does **not**
alert. Incidents still awaiting geocoding can't be distance-tested, so they
fall back to the roads your line touches and are flagged *"not yet located —
may be outside your section"*.

**What it uses — and doesn't.** There is no free, keyless source of
Google/Waze-style live traffic *speed* data. This matches the actual 911
incident feed against a freshness window (default 90 min), plus the NWS
alerts already collected. The feed reports when an incident *started*, not
when it cleared, so each item shows how long ago it was reported — minor
accidents are often already gone. A paid traffic-flow provider can be added
later (`fetch_traffic_flow` in `route_alerts.py`) without changing the free
path.

Everything is also configurable by environment variables (see
`route_alerts.py` for `LAF911_ROUTE_*`; env slots are overridden by
email-configured slots). Useful knobs and tools:

```bash
LAF911_ROUTE_LEAD_MIN=10      # email this many minutes before departure
LAF911_ROUTE_WINDOW_MIN=90    # only incidents newer than this
LAF911_ROUTE_INBOX=off        # disable the mailbox config channel

python -m lafayette911.route_inbox --check     # poll the mailbox once, now
python -m lafayette911.route_inbox --list      # show email-configured routes
python -m lafayette911.route_alerts --preview route.html --route 1
python -m lafayette911.route_alerts --send --route 1
```

Alerts send at most once per route per day (SQLite-guarded), only on
configured days, and back off after repeated failures — the same fail-safe
rules as the digest.

## External services

| Service | Used for | Cost notes |
| --- | --- | --- |
| lafayette911.org feed | Incident source | public feed, polled gently |
| Google Geocoding API | Address → coordinates | budgeted; aggressive caching & blacklist |
| NWS api.weather.gov | Weather snapshots, active alerts, live map weather | free, no key |
| CARTO / OpenStreetMap tiles | Basemaps | free tiers; attribution kept |
| GitHub Pages (optional) | Public hosting of the map | free for public repos |

## License

This project's original source code is released under the
[MIT License](LICENSE). The license covers **only the code** — incident data,
map data/tiles, weather data, and third-party libraries remain the property of
their respective owners; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution and terms.

## Development

```bash
python -m pytest tests/ -q
```

The test suite covers dedupe (including feed formatting jitter), geocode
budgeting/blacklisting/queueing, date parsing, CSV/DB rendering, and template
generation. When changing the web page, `map_template.py` is a plain Python
string with `__TOKEN__` substitution — no build step required.
