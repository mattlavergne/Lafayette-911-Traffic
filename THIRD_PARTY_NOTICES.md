# Third-party notices

The MIT License in [LICENSE](LICENSE) applies **only to this project's
original source code**. This project does not claim ownership of any incident
data, government data, maps, map tiles, trademarks, APIs, or third-party
libraries. The components below are the property of their respective owners
and are used under their own licenses and terms.

## Libraries

| Component | Used for | License / terms |
| --- | --- | --- |
| [Leaflet](https://leafletjs.com/) 1.9.4 | Interactive map engine (loaded from the unpkg CDN with subresource integrity) | [BSD-2-Clause](https://github.com/Leaflet/Leaflet/blob/main/LICENSE) |
| [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) 0.2.0 | Heatmap layer | [BSD-2-Clause](https://github.com/Leaflet/Leaflet.heat/blob/gh-pages/LICENSE) |
| [pandas](https://pandas.pydata.org/) / [requests](https://requests.readthedocs.io/) | Backend data handling | BSD-3-Clause / Apache-2.0 |
| [OSMnx](https://osmnx.readthedocs.io/) (optional) | Road classification & intersection analysis | MIT |

## Map data and tiles

| Provider | Used for | Terms |
| --- | --- | --- |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | Map data, basemap tiles, road metadata | [ODbL](https://opendatacommons.org/licenses/odbl/); attribution required and kept visible on the map |
| [CARTO](https://carto.com/attribution/) | Light/dark basemap tiles, only when `LAF911_CARTO_API_KEY` is configured | Free basemap tier, requires a CARTO account; attribution required and kept visible on the map |

## Data sources and services

| Source | Used for | Notes |
| --- | --- | --- |
| lafayette911.org public feed | Incident reports | Public information feed operated by its respective agency; this project is not affiliated with or endorsed by it |
| [National Weather Service API](https://www.weather.gov/documentation/services-web-api) (NOAA) | Weather snapshots and active alerts | U.S. Government work; NWS/NOAA do not endorse this project |
| Google Geocoding API | Address → coordinate resolution (backend only; no key is ever published) | [Google Maps Platform Terms](https://cloud.google.com/maps-platform/terms) |
| [GitHub Pages](https://pages.github.com/) | Static hosting of the published map | [GitHub Terms of Service](https://docs.github.com/site-policy/github-terms/github-terms-of-service) |

All product names, logos, and brands are property of their respective owners
and are used for identification purposes only.
