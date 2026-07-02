"""Standalone HTML template for the Lafayette 911 traffic map webpage.

The page is a complete, self-contained document (Leaflet from CDN + the
generated ``traffic_data.js`` payload).  It is intentionally kept as a plain
Python string with ``__TOKEN__`` placeholders instead of an f-string so the
embedded CSS/JS can use braces and template literals without escaping.

Design goals:
  * Fail-safe: the page degrades gracefully when the CDN, the tile server,
    the data file, or the NWS API is unreachable.
  * Zero paid API usage: basemaps are CARTO/OSM raster tiles, live weather
    and alerts come from the free NWS API, and the map/street-view buttons
    are plain Google Maps deep links (no key, no quota).
  * All analytics run client-side over the embedded incident array.
"""

from datetime import datetime, timezone


def render_map_html(center_lat: float, center_lng: float, datajs_src: str) -> str:
    """Render the full map page HTML with the given center and data script src."""
    current_year = datetime.now().year
    year_options = '<option value="">All</option>' + "".join(
        f'<option value="{y}">{y}</option>' for y in range(current_year - 2, current_year + 1)
    )
    day_options = '<option value="">All</option>' + "".join(
        f'<option value="{i:02d}">{i}</option>' for i in range(1, 32)
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    html = MAP_HTML_TEMPLATE
    html = html.replace("__CENTER_LAT__", f"{float(center_lat):.6f}")
    html = html.replace("__CENTER_LNG__", f"{float(center_lng):.6f}")
    html = html.replace("__DATAJS_SRC__", datajs_src)
    html = html.replace("__YEAR_OPTIONS__", year_options)
    html = html.replace("__DAY_OPTIONS__", day_options)
    html = html.replace("__GENERATED_AT__", generated_at)
    return html


MAP_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="description" content="Live map and analytics of Lafayette Parish 911 traffic incidents — accidents, hazards, and road conditions with weather context.">
<meta name="generated-at" content="__GENERATED_AT__">
<meta name="theme-color" content="#f5f6f8" id="metaThemeColor">
<title>Lafayette Traffic · Live 911 Incident Map</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='42' fill='%23e5484d'/%3E%3Ccircle cx='50' cy='50' r='20' fill='white'/%3E%3C/svg%3E">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<style>
  :root {
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    --radius: 18px;
    --radius-sm: 11px;

    --bg: #f5f6f8;
    --panel: rgba(255, 255, 255, 0.86);
    --panel-solid: #ffffff;
    --panel-border: rgba(15, 23, 42, 0.10);
    --hairline: rgba(15, 23, 42, 0.07);
    --text: #0f172a;
    --text-2: rgba(15, 23, 42, 0.62);
    --text-3: rgba(15, 23, 42, 0.42);
    --accent: #2563eb;
    --accent-soft: rgba(37, 99, 235, 0.12);
    --chip: rgba(15, 23, 42, 0.05);
    --chip-hover: rgba(15, 23, 42, 0.09);
    --input-bg: rgba(255, 255, 255, 0.92);
    --shadow: 0 18px 44px rgba(15, 23, 42, 0.16), 0 2px 8px rgba(15, 23, 42, 0.07);
    --shadow-sm: 0 4px 14px rgba(15, 23, 42, 0.10);
    --good: #16a34a;
    --warn: #d97706;
    --bad: #dc2626;
    --bar-track: rgba(15, 23, 42, 0.09);
  }

  html[data-theme="dark"] {
    --bg: #0b1120;
    --panel: rgba(17, 24, 39, 0.82);
    --panel-solid: #111827;
    --panel-border: rgba(148, 163, 184, 0.16);
    --hairline: rgba(148, 163, 184, 0.10);
    --text: #e5e9f0;
    --text-2: rgba(226, 232, 240, 0.64);
    --text-3: rgba(226, 232, 240, 0.40);
    --accent: #60a5fa;
    --accent-soft: rgba(96, 165, 250, 0.16);
    --chip: rgba(148, 163, 184, 0.10);
    --chip-hover: rgba(148, 163, 184, 0.18);
    --input-bg: rgba(30, 41, 59, 0.85);
    --shadow: 0 18px 44px rgba(0, 0, 0, 0.55), 0 2px 8px rgba(0, 0, 0, 0.35);
    --shadow-sm: 0 4px 14px rgba(0, 0, 0, 0.40);
    --good: #4ade80;
    --warn: #fbbf24;
    --bad: #f87171;
    --bar-track: rgba(148, 163, 184, 0.16);
  }

  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
  }
  #map { position: absolute; inset: 0; z-index: 1; background: var(--bg); }
  /* CARTO Dark Matter is too low-contrast on its own — lift the street
     network so roads stay clearly legible in dark mode. */
  html[data-theme="dark"] #map:not(.osm-fallback) .leaflet-tile {
    filter: brightness(4) contrast(1.2) saturate(0.8);
  }
  html[data-theme="dark"] #map.osm-fallback .leaflet-tile {
    filter: brightness(0.62) invert(1) contrast(0.88) hue-rotate(185deg) saturate(0.45) brightness(0.82);
  }

  /* ── Leaflet chrome overrides ─────────────────────────────────────── */
  .leaflet-control-zoom a {
    background: var(--panel) !important;
    color: var(--text) !important;
    border: none !important;
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
  }
  .leaflet-bar { border: 1px solid var(--panel-border) !important; border-radius: 12px !important; overflow: hidden; box-shadow: var(--shadow-sm) !important; }
  .leaflet-control-attribution {
    background: var(--panel) !important;
    color: var(--text-3) !important;
    font-size: 10px !important;
    border-radius: 8px 8px 0 0;
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
  }
  .leaflet-control-attribution a { color: var(--text-2) !important; }
  .leaflet-control-scale-line {
    background: var(--panel) !important;
    color: var(--text-2) !important;
    border-color: var(--panel-border) !important;
  }
  .leaflet-popup-content-wrapper {
    background: var(--panel-solid);
    color: var(--text);
    border-radius: 14px;
    border: 1px solid var(--panel-border);
    box-shadow: var(--shadow);
  }
  .leaflet-popup-tip { background: var(--panel-solid); border: 1px solid var(--panel-border); }
  .leaflet-popup-content { margin: 12px 14px; font-family: var(--font); font-size: 13px; line-height: 1.45; }
  .leaflet-popup-close-button { color: var(--text-3) !important; font-size: 18px !important; padding: 6px 8px 0 0 !important; }
  .leaflet-container { font-family: var(--font); }

  /* ── Popup card ───────────────────────────────────────────────────── */
  .pc { min-width: 220px; max-width: 300px; }
  .pc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .pc-badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 10.5px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
    padding: 3px 8px; border-radius: 999px;
    background: color-mix(in srgb, var(--cat, #64748b) 14%, transparent);
    color: var(--cat, #64748b);
  }
  .pc-badge::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--cat, #64748b); }
  .pc-time { font-size: 11px; color: var(--text-3); white-space: nowrap; }
  .pc-title { font-size: 13.5px; font-weight: 700; line-height: 1.3; margin-bottom: 8px; }
  .pc-rows { display: grid; gap: 3px; font-size: 12px; color: var(--text-2); }
  .pc-rows b { color: var(--text); font-weight: 600; }
  .pc-alert { color: var(--bad); font-weight: 700; font-size: 12px; margin-top: 5px; }
  .pc-wx { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .pc-wx span {
    font-size: 11px; padding: 2px 7px; border-radius: 999px;
    background: var(--chip); color: var(--text-2); white-space: nowrap;
  }
  .pc-links { display: flex; gap: 6px; margin-top: 10px; }
  .pc-links a {
    flex: 1; text-align: center; font-size: 11.5px; font-weight: 600; text-decoration: none;
    padding: 6px 8px; border-radius: 9px; color: var(--accent);
    background: var(--accent-soft); transition: filter 0.12s ease;
  }
  .pc-links a:hover { filter: brightness(1.08); }
  .pc-nav { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
  .pc-nav button {
    border: 1px solid var(--panel-border); background: var(--chip); color: var(--text);
    border-radius: 8px; padding: 3px 10px; cursor: pointer; font-size: 13px; line-height: 1.3;
  }
  .pc-nav button:disabled { opacity: 0.35; cursor: default; }
  .pc-nav .pc-nav-count { font-size: 11.5px; font-weight: 600; color: var(--text-2); }

  .incident-count-marker {
    border-radius: 999px;
    background: var(--panel-solid);
    color: var(--text);
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-family: var(--font);
    border: 2.5px solid var(--cat, #2563eb);
    box-shadow: 0 1px 6px rgba(0,0,0,0.25);
  }

  /* ── Sidebar ──────────────────────────────────────────────────────── */
  #sidebar {
    position: absolute; top: 14px; left: 14px; z-index: 1200;
    width: min(400px, calc(100vw - 28px));
    max-height: calc(100dvh - 28px);
    display: flex; flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    backdrop-filter: blur(22px) saturate(1.35);
    -webkit-backdrop-filter: blur(22px) saturate(1.35);
    overflow: hidden;
    font-size: 13px;
  }
  #sbHandle { display: none; }

  .sb-header { display: flex; align-items: center; gap: 10px; padding: 14px 14px 10px 16px; }
  .brand-mark {
    width: 36px; height: 36px; border-radius: 11px; flex: none;
    background: linear-gradient(135deg, #ef4444, #b91c1c);
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 4px 12px rgba(220, 38, 38, 0.35);
  }
  .brand-mark svg { display: block; }
  .brand-text { flex: 1; min-width: 0; }
  .brand-title { font-size: 15.5px; font-weight: 800; letter-spacing: -0.01em; line-height: 1.15; }
  .brand-sub { font-size: 11px; color: var(--text-2); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sb-actions { display: flex; gap: 6px; }
  .icon-btn {
    width: 32px; height: 32px; border-radius: 10px; border: 1px solid var(--panel-border);
    background: var(--chip); color: var(--text); cursor: pointer;
    display: flex; align-items: center; justify-content: center; padding: 0;
    transition: background 0.13s ease, transform 0.1s ease;
  }
  .icon-btn:hover { background: var(--chip-hover); }
  .icon-btn:active { transform: scale(0.94); }
  .icon-btn svg { width: 16px; height: 16px; }

  .status-row {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 0 16px 10px 16px; font-size: 11.5px; color: var(--text-2);
  }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--good); position: relative; flex: none; }
  .live-dot::after {
    content: ""; position: absolute; inset: -4px; border-radius: 50%;
    border: 2px solid var(--good); opacity: 0.5; animation: pulse 2.2s ease-out infinite;
  }
  @keyframes pulse { 0% { transform: scale(0.5); opacity: 0.7; } 80% { transform: scale(1.25); opacity: 0; } 100% { opacity: 0; } }
  .status-chip {
    padding: 2px 8px; border-radius: 999px; background: var(--chip); white-space: nowrap;
  }
  .status-chip.warn { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); font-weight: 600; }

  #alertBanner {
    display: none; margin: 0 14px 10px 14px; padding: 8px 12px;
    border-radius: var(--radius-sm); font-size: 12px; font-weight: 600; line-height: 1.35;
    background: color-mix(in srgb, var(--bad) 13%, transparent);
    color: var(--bad); border: 1px solid color-mix(in srgb, var(--bad) 30%, transparent);
  }

  .stat-tiles {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
    padding: 0 14px 10px 14px;
  }
  .stat-tile {
    background: var(--chip); border-radius: var(--radius-sm);
    padding: 8px 6px 7px 6px; text-align: center; min-width: 0;
    transition: background 0.15s ease;
  }
  .stat-tile b { display: block; font-size: 17px; font-weight: 800; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
  .stat-tile span { display: block; font-size: 10px; color: var(--text-3); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 1px; }
  .stat-tile.hot b { color: var(--bad); }

  .legend-chips {
    display: flex; flex-wrap: wrap; gap: 5px;
    padding: 0 14px 10px 14px;
  }
  .legend-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; padding: 4px 9px; border-radius: 999px;
    border: 1px solid var(--panel-border); background: var(--chip); color: var(--text-2);
    cursor: pointer; user-select: none;
    transition: background 0.13s ease, opacity 0.13s ease, border-color 0.13s ease;
  }
  .legend-chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--cat); flex: none; }
  .legend-chip .n { font-variant-numeric: tabular-nums; color: var(--text-3); font-weight: 500; }
  .legend-chip.off { opacity: 0.38; }
  .legend-chip:hover { background: var(--chip-hover); }
  .legend-chip.on { border-color: color-mix(in srgb, var(--cat) 55%, transparent); color: var(--text); }

  .tabs {
    display: flex; gap: 4px; padding: 0 14px 10px 14px;
  }
  .tab {
    flex: 1; text-align: center; font-size: 12.5px; font-weight: 600; color: var(--text-2);
    padding: 7px 4px; border-radius: 10px; border: none; background: transparent; cursor: pointer;
    font-family: var(--font); position: relative;
    transition: background 0.13s ease, color 0.13s ease;
  }
  .tab:hover { background: var(--chip); }
  .tab.active { background: var(--accent-soft); color: var(--accent); }
  .tab .badge {
    position: absolute; top: 2px; right: 6px;
    min-width: 15px; height: 15px; border-radius: 999px; padding: 0 4px;
    background: var(--accent); color: #fff; font-size: 9.5px; font-weight: 700;
    display: none; align-items: center; justify-content: center; line-height: 15px;
  }
  .tab .badge.show { display: inline-flex; }

  .sb-body {
    flex: 1 1 auto; overflow-y: auto; overscroll-behavior: contain;
    padding: 2px 14px 14px 14px;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
  }
  .panel { display: none; }
  .panel.active { display: block; }

  .sb-footer {
    display: flex; align-items: center; gap: 8px;
    padding: 9px 14px; border-top: 1px solid var(--hairline);
    font-size: 11.5px; color: var(--text-2);
  }
  .sb-footer .counts { flex: 1; display: flex; gap: 10px; font-variant-numeric: tabular-nums; }
  .sb-footer .counts b { color: var(--text); font-weight: 700; }
  .sb-footer label { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; white-space: nowrap; }
  #clearBtn {
    border: none; background: var(--chip); color: var(--text-2); font-family: var(--font);
    font-size: 11.5px; font-weight: 600; padding: 5px 10px; border-radius: 8px; cursor: pointer;
    transition: background 0.13s ease, color 0.13s ease;
  }
  #clearBtn:hover { background: var(--chip-hover); color: var(--text); }

  /* ── sections & controls ─────────────────────────────────────────── */
  .section { padding: 10px 0 4px 0; border-bottom: 1px solid var(--hairline); }
  .section:last-child { border-bottom: none; }
  .section-title {
    font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.09em;
    color: var(--text-3); font-weight: 700; margin-bottom: 8px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
  .row-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .row-checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
  .row-note { font-size: 11px; color: var(--text-3); margin: 2px 0 8px 0; line-height: 1.4; }

  label.field { display: block; font-size: 11.5px; color: var(--text-2); font-weight: 600; min-width: 0; }
  label.field select { margin-top: 4px; }
  select {
    font-family: var(--font); font-size: 12.5px; color: var(--text);
    padding: 7px 26px 7px 9px; border-radius: 9px; width: 100%;
    border: 1px solid var(--panel-border); background-color: var(--input-bg);
    outline: none; cursor: pointer; appearance: none; -webkit-appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 9px center;
    transition: border-color 0.13s ease;
  }
  select:hover:not(:disabled) { border-color: var(--text-3); }
  select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  select:disabled { opacity: 0.45; cursor: not-allowed; }

  .check {
    display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-2);
    padding: 6px 8px; border-radius: 9px; background: var(--chip); cursor: pointer;
    transition: background 0.12s ease, color 0.12s ease; user-select: none;
  }
  .check:hover { background: var(--chip-hover); color: var(--text); }
  .check input { width: 15px; height: 15px; accent-color: var(--accent); cursor: pointer; margin: 0; flex: none; }

  .range-chips { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 8px; }
  .range-chip {
    font-family: var(--font); font-size: 11.5px; font-weight: 600; color: var(--text-2);
    padding: 5px 11px; border-radius: 999px; border: 1px solid var(--panel-border);
    background: var(--chip); cursor: pointer; transition: all 0.13s ease;
  }
  .range-chip:hover { background: var(--chip-hover); }
  .range-chip.active { background: var(--accent); border-color: var(--accent); color: #fff; }

  .search-wrap { position: relative; display: flex; align-items: center; margin-bottom: 8px; }
  .search-icon { position: absolute; left: 10px; color: var(--text-3); display: flex; pointer-events: none; }
  #roadSearch {
    width: 100%; font-family: var(--font); font-size: 13px; color: var(--text);
    padding: 8px 30px 8px 32px; border-radius: 10px;
    border: 1px solid var(--panel-border); background: var(--input-bg); outline: none;
    transition: border-color 0.13s ease, box-shadow 0.13s ease;
  }
  #roadSearch::placeholder { color: var(--text-3); }
  #roadSearch:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .search-clear {
    position: absolute; right: 7px; width: 18px; height: 18px; border-radius: 50%;
    border: none; background: var(--chip-hover); color: var(--text-2); cursor: pointer;
    font-size: 10px; line-height: 1; display: none; align-items: center; justify-content: center; padding: 0;
  }
  .search-clear.show { display: flex; }
  .search-hint { font-size: 10.5px; color: var(--text-3); margin: -4px 0 8px 2px; }

  /* ── analytics ────────────────────────────────────────────────────── */
  .an-summary { font-size: 11.5px; color: var(--text-2); margin: 8px 0 4px 0; }
  .an-summary b { color: var(--text); }
  .chart-block { margin: 6px 0 12px 0; }
  .chart-title { font-size: 11px; font-weight: 700; color: var(--text-2); margin-bottom: 4px; display: flex; justify-content: space-between; align-items: baseline; }
  .chart-title .sub { font-weight: 500; color: var(--text-3); font-size: 10.5px; }
  .chart-svg { width: 100%; display: block; }
  .chart-svg .bar { fill: color-mix(in srgb, var(--accent) 38%, transparent); transition: fill 0.15s ease; }
  .chart-svg .bar:hover { fill: var(--accent); }
  .chart-svg .bar.peak { fill: var(--accent); }
  .chart-svg .axis-label { font-size: 8.5px; fill: var(--text-3); font-family: var(--font); }
  .chart-svg .trend-line { stroke: var(--accent); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
  .chart-svg .trend-fill { fill: var(--accent); opacity: 0.12; }
  .chart-svg .trend-dot { fill: var(--accent); }
  .trend-delta { font-weight: 700; }
  .trend-delta.up { color: var(--bad); }
  .trend-delta.down { color: var(--good); }

  .rates-group { margin-bottom: 12px; }
  .rates-group-title { font-size: 10.5px; font-weight: 700; color: var(--text-3); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; }
  .rates-span-note { font-weight: 500; font-size: 9.5px; color: var(--text-3); text-transform: none; letter-spacing: 0; }
  .rates-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .rates-label { flex: 0 0 92px; font-size: 11px; color: var(--text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .rates-bar-wrap { flex: 1; background: var(--bar-track); border-radius: 3px; height: 7px; overflow: hidden; min-width: 0; }
  .rates-bar-fill { height: 7px; border-radius: 3px; transition: width 0.35s ease; min-width: 2px; }
  .rates-val { flex: 0 0 auto; font-size: 10.5px; font-variant-numeric: tabular-nums; white-space: nowrap; min-width: 58px; text-align: right; color: var(--text-2); }
  .rates-n { color: var(--text-3); font-size: 9.5px; }
  .rates-ratio { font-size: 11px; font-style: italic; color: var(--text-2); margin: 2px 0 4px 0; line-height: 1.4; }
  .rates-subtitle { font-size: 11px; color: var(--text-3); margin-bottom: 8px; }
  .rates-no-data { font-size: 11.5px; color: var(--text-3); font-style: italic; }

  .insight-list { margin: 0; padding-left: 16px; display: grid; gap: 6px; color: var(--text-2); font-size: 12px; line-height: 1.4; }
  .insight-list li strong, .insight-list li b { color: var(--text); font-weight: 700; }

  /* ── feed ─────────────────────────────────────────────────────────── */
  .feed-meta { font-size: 11px; color: var(--text-3); margin: 8px 0; }
  .feed-item {
    display: flex; gap: 9px; padding: 8px 8px; border-radius: var(--radius-sm);
    cursor: pointer; transition: background 0.12s ease; align-items: flex-start;
  }
  .feed-item:hover { background: var(--chip); }
  .feed-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--cat, #64748b); margin-top: 4px; flex: none; }
  .feed-body { flex: 1; min-width: 0; }
  .feed-loc { font-size: 12.5px; font-weight: 600; line-height: 1.3; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .feed-sub { font-size: 11px; color: var(--text-3); margin-top: 1px; display: flex; gap: 6px; flex-wrap: wrap; }
  .feed-sub .cat-name { color: var(--cat, var(--text-3)); font-weight: 600; }
  .feed-more {
    width: 100%; margin-top: 6px; padding: 8px; font-family: var(--font); font-size: 12px; font-weight: 600;
    border: 1px dashed var(--panel-border); background: transparent; color: var(--text-2);
    border-radius: var(--radius-sm); cursor: pointer;
  }
  .feed-more:hover { background: var(--chip); }
  .feed-empty { text-align: center; color: var(--text-3); font-size: 12px; padding: 24px 8px; }

  /* ── weather chip ─────────────────────────────────────────────────── */
  #weatherChip {
    position: absolute; top: 14px; right: 14px; z-index: 1150;
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: 999px; box-shadow: var(--shadow-sm);
    backdrop-filter: blur(18px) saturate(1.3);
    -webkit-backdrop-filter: blur(18px) saturate(1.3);
    font-size: 12.5px; font-weight: 600; color: var(--text);
    padding: 7px 13px; cursor: pointer; user-select: none;
    display: flex; align-items: center; gap: 7px; max-width: min(70vw, 320px);
    transition: transform 0.12s ease;
  }
  #weatherChip:active { transform: scale(0.97); }
  #weatherChip .wx-icon { font-size: 15px; line-height: 1; }
  #weatherChip .wx-main { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #weatherPanel {
    position: absolute; top: 54px; right: 14px; z-index: 1150;
    width: min(290px, calc(100vw - 28px));
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: 14px; box-shadow: var(--shadow);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    padding: 12px 14px; font-size: 12.5px; display: none;
  }
  #weatherPanel.open { display: block; }
  #weatherPanel .wp-title { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-3); margin-bottom: 7px; }
  #weatherPanel .wp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; }
  #weatherPanel .wp-grid div { color: var(--text-2); }
  #weatherPanel .wp-grid b { color: var(--text); font-weight: 700; font-variant-numeric: tabular-nums; }
  #weatherPanel .wp-meta { margin-top: 9px; font-size: 10.5px; color: var(--text-3); line-height: 1.45; }
  #weatherPanel a { color: var(--accent); text-decoration: none; font-weight: 600; }

  /* ── toast / overlays ─────────────────────────────────────────────── */
  #toast {
    position: fixed; bottom: 26px; left: 50%; transform: translate(-50%, 18px);
    background: var(--panel-solid); color: var(--text); border: 1px solid var(--panel-border);
    border-radius: 999px; padding: 9px 18px; font-size: 12.5px; font-weight: 600;
    box-shadow: var(--shadow); z-index: 3000; opacity: 0; pointer-events: none;
    transition: opacity 0.22s ease, transform 0.22s ease;
  }
  #toast.show { opacity: 1; transform: translate(-50%, 0); }

  #noMapNotice {
    position: absolute; inset: 0; z-index: 900; display: none;
    align-items: center; justify-content: center; text-align: center; padding: 24px;
    color: var(--text-2); font-size: 14px; line-height: 1.6;
  }
  body.no-map #noMapNotice { display: flex; }
  body.no-map #map { display: none; }

  .kbd-hint { font-size: 10.5px; color: var(--text-3); text-align: center; padding: 8px 0 2px 0; }
  .kbd-hint kbd {
    font-family: var(--mono); font-size: 10px; background: var(--chip);
    border: 1px solid var(--panel-border); border-radius: 4px; padding: 1px 5px;
  }

  /* Keep bottom-left Leaflet controls clear of the sidebar on desktop. */
  @media (min-width: 701px) {
    .leaflet-bottom.leaflet-left { left: 430px; }
  }

  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-thumb { background: var(--bar-track); border-radius: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
  }

  /* ── mobile: bottom sheet ─────────────────────────────────────────── */
  @media (max-width: 700px) {
    #sidebar {
      top: auto; left: 0; right: 0; bottom: 0; width: 100%;
      border-radius: 20px 20px 0 0;
      max-height: 88dvh;
      height: 236px;
      transition: height 0.3s cubic-bezier(0.32, 0.72, 0, 1);
      padding-bottom: env(safe-area-inset-bottom);
    }
    #sidebar.expanded { height: 88dvh; }
    #sbHandle {
      display: block; width: 42px; height: 5px; border-radius: 999px;
      background: var(--text-3); opacity: 0.5; margin: 8px auto 0 auto; flex: none; cursor: grab;
    }
    .sb-header { padding-top: 8px; }
    #sidebar:not(.expanded) .sb-body,
    #sidebar:not(.expanded) .tabs,
    #sidebar:not(.expanded) .legend-chips,
    #sidebar:not(.expanded) #alertBanner,
    #sidebar:not(.expanded) .kbd-hint { display: none; }
    #sidebar:not(.expanded) .sb-footer { border-top: none; padding-top: 2px; }
    .stat-tiles { padding-bottom: 6px; }
    select, .check { min-height: 42px; font-size: 14px; }
    .check input { width: 19px; height: 19px; }
    #roadSearch { font-size: 16px; min-height: 44px; }
    .range-chip { padding: 8px 14px; font-size: 13px; }
    .tab { padding: 10px 4px; font-size: 13.5px; }
    #weatherChip { top: 10px; right: 10px; font-size: 12px; padding: 6px 11px; }
    #weatherPanel { top: 48px; right: 10px; }
    .kbd-hint { display: none; }
    .leaflet-control-zoom, .leaflet-control-scale { display: none; }
    .leaflet-bottom.leaflet-right .leaflet-control-attribution { margin-bottom: 244px; }
    body.sheet-expanded .leaflet-bottom.leaflet-right .leaflet-control-attribution { display: none; }
  }
</style>
</head>
<body>

<div id="map" aria-label="Incident map"></div>

<div id="noMapNotice">
  <div>
    <div style="font-size:34px;margin-bottom:10px;">🗺️</div>
    <b>Map library unavailable.</b><br>
    The Leaflet CDN could not be reached, but incident stats and the feed below still work.<br>
    Check your connection and reload to restore the map.
  </div>
</div>

<div id="sidebar" role="region" aria-label="Incident controls">
  <div id="sbHandle" aria-hidden="true"></div>

  <div class="sb-header">
    <div class="brand-mark" aria-hidden="true">
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none">
        <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" fill="white"/>
        <circle cx="12" cy="9" r="2.6" fill="#dc2626"/>
      </svg>
    </div>
    <div class="brand-text">
      <div class="brand-title">Lafayette Traffic</div>
      <div class="brand-sub">911 incident intelligence · Lafayette Parish, LA</div>
    </div>
    <div class="sb-actions">
      <button class="icon-btn" id="themeBtn" type="button" title="Toggle theme (auto / light / dark)" aria-label="Toggle color theme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="4"/>
          <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>
        </svg>
      </button>
    </div>
  </div>

  <div class="status-row">
    <span class="live-dot" aria-hidden="true"></span>
    <span id="statusText">Loading incident data…</span>
    <span class="status-chip warn" id="unlocChip" style="display:none" title="These incidents are awaiting geocoding and will appear on the map automatically once located."></span>
  </div>

  <div id="alertBanner" role="status"></div>

  <div class="stat-tiles" aria-label="Incident counts">
    <div class="stat-tile" id="tileToday"><b>–</b><span>Today</span></div>
    <div class="stat-tile"><b id="tileWeekVal">–</b><span>7 days</span></div>
    <div class="stat-tile"><b id="tileMonthVal">–</b><span>30 days</span></div>
    <div class="stat-tile"><b id="tileTotalVal">–</b><span>All time</span></div>
  </div>

  <div class="legend-chips" id="legendChips" aria-label="Incident categories (tap to toggle)"></div>

  <div class="tabs" role="tablist">
    <button class="tab active" id="tabFilters" role="tab" aria-selected="true" type="button">Filters<span class="badge" id="filterBadge"></span></button>
    <button class="tab" id="tabAnalytics" role="tab" aria-selected="false" type="button">Analytics</button>
    <button class="tab" id="tabFeed" role="tab" aria-selected="false" type="button">Feed</button>
  </div>

  <div class="sb-body">

    <!-- ═══ FILTERS ═══ -->
    <div class="panel active" id="panelFilters">

      <div class="section">
        <div class="search-wrap">
          <span class="search-icon" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
              <circle cx="5.5" cy="5.5" r="4" stroke="currentColor" stroke-width="1.5"/>
              <line x1="8.6" y1="8.6" x2="12.5" y2="12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          </span>
          <input type="text" id="roadSearch" placeholder="Search roads — typos OK" autocomplete="off" spellcheck="false" aria-label="Filter by road name">
          <button class="search-clear" id="roadSearchClear" type="button" aria-label="Clear road search">✕</button>
        </div>

        <div class="range-chips" id="rangeChips" aria-label="Quick time range">
          <button class="range-chip active" data-range="" type="button">All time</button>
          <button class="range-chip" data-range="today" type="button">Today</button>
          <button class="range-chip" data-range="24h" type="button">24 h</button>
          <button class="range-chip" data-range="7d" type="button">7 days</button>
          <button class="range-chip" data-range="30d" type="button">30 days</button>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Incident type</div>
        <div class="row row-grid">
          <label class="field">Group
            <select id="causeGroupSelect"><option value="__ALL__">All groups</option></select>
          </label>
          <label class="field">Type
            <select id="causeSelect"><option value="__ALL__">All types</option></select>
          </label>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Date &amp; time</div>
        <div class="row row-checks">
          <label class="check"><input type="checkbox" id="chkRushHour"> Rush hour only</label>
          <label class="check"><input type="checkbox" id="chkSchoolDay"> School days only</label>
        </div>
        <div class="row" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;">
          <label class="field">Month
            <select id="monthSelect">
              <option value="">All</option>
              <option value="01">Jan</option><option value="02">Feb</option><option value="03">Mar</option>
              <option value="04">Apr</option><option value="05">May</option><option value="06">Jun</option>
              <option value="07">Jul</option><option value="08">Aug</option><option value="09">Sep</option>
              <option value="10">Oct</option><option value="11">Nov</option><option value="12">Dec</option>
            </select>
          </label>
          <label class="field">Day
            <select id="daySelect">__DAY_OPTIONS__</select>
          </label>
          <label class="field">Year
            <select id="yearSelect">__YEAR_OPTIONS__</select>
          </label>
        </div>
        <div class="row row-grid">
          <label class="field">Day of week
            <select id="dowSelect">
              <option value="all">All days</option>
              <option value="1">Monday</option><option value="2">Tuesday</option><option value="3">Wednesday</option>
              <option value="4">Thursday</option><option value="5">Friday</option>
              <option value="6">Saturday</option><option value="0">Sunday</option>
            </select>
          </label>
          <label class="field">Day type
            <select id="dayTypeSelect">
              <option value="all">All</option>
              <option value="weekday">Weekdays</option>
              <option value="weekend">Weekends</option>
            </select>
          </label>
        </div>
        <div class="row row-grid">
          <label class="field">Time of day
            <select id="timeBlockSelect">
              <option value="all">All hours</option>
              <option value="morning">Morning (6–10 am)</option>
              <option value="midday">Midday (10 am–3 pm)</option>
              <option value="evening">Evening (3–7 pm)</option>
              <option value="night">Night (7 pm–12 am)</option>
              <option value="latenight">Late night (12–6 am)</option>
            </select>
          </label>
          <label class="field">Road type
            <select id="roadTypeSelect">
              <option value="any">Any road</option>
              <option value="motorway">Interstate / motorway</option>
              <option value="trunk">US highway (trunk)</option>
              <option value="primary">Primary arterial</option>
              <option value="secondary">Secondary / tertiary</option>
              <option value="residential">Residential / local</option>
            </select>
          </label>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Weather at time of incident</div>
        <div class="row row-checks">
          <label class="check"><input type="checkbox" id="chkWeatherOnly"> Has weather data</label>
        </div>
        <div class="row row-grid">
          <label class="field">Temperature
            <select id="tempBand">
              <option value="any">Any</option>
              <option value="cold">Cold (≤50°F)</option>
              <option value="mild">Mild (50–70°F)</option>
              <option value="warm">Warm (70–85°F)</option>
              <option value="hot">Hot (≥85°F)</option>
            </select>
          </label>
          <label class="field">Precip chance
            <select id="precipBand">
              <option value="any">Any</option>
              <option value="low">Low (&lt;20%)</option>
              <option value="med">Medium (20–60%)</option>
              <option value="high">High (≥60%)</option>
            </select>
          </label>
        </div>
        <div class="row row-grid">
          <label class="field">Wind
            <select id="windBand">
              <option value="any">Any</option>
              <option value="calm">Calm (&lt;10 mph)</option>
              <option value="breezy">Breezy (10–20 mph)</option>
              <option value="windy">Windy (≥20 mph)</option>
            </select>
          </label>
          <label class="field">Visibility
            <select id="visBand">
              <option value="any">Any</option>
              <option value="low">Low (&lt;3 mi)</option>
              <option value="hazy">Hazy (3–10 mi)</option>
              <option value="clear">Clear (≥10 mi)</option>
            </select>
          </label>
        </div>
        <div class="row row-grid">
          <label class="field">Liquid precip
            <select id="precipAmountBand">
              <option value="any">Any</option>
              <option value="none">None (0 in)</option>
              <option value="light">Light (≤0.10 in)</option>
              <option value="moderate">Moderate (0.10–0.50 in)</option>
              <option value="heavy">Heavy (≥0.50 in)</option>
            </select>
          </label>
          <label class="field">Cloud cover
            <select id="cloudBand">
              <option value="any">Any</option>
              <option value="clear">Mostly clear (&lt;25%)</option>
              <option value="partly">Partly cloudy (25–70%)</option>
              <option value="overcast">Overcast (≥70%)</option>
            </select>
          </label>
        </div>
        <div class="row-note">Weather filters ignore incidents without weather unless a specific condition is chosen.</div>
      </div>

      <div class="section">
        <div class="section-title">Active NWS alerts at report time</div>
        <div class="row row-checks">
          <label class="check"><input type="checkbox" id="chkFloodWarning"> Flash flood</label>
          <label class="check"><input type="checkbox" id="chkThunderstormWarning"> Severe storm</label>
          <label class="check"><input type="checkbox" id="chkTornadoWatch"> Tornado watch</label>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Map layers</div>
        <div class="row row-checks">
          <label class="check"><input type="checkbox" id="chkPoints" checked> Incidents</label>
          <label class="check"><input type="checkbox" id="chkHeat"> Heatmap</label>
          <label class="check"><input type="checkbox" id="chkHotSpots"> Hot spots</label>
          <label class="check"><input type="checkbox" id="chkIntersections"> Rounded clusters</label>
          <label class="check"><input type="checkbox" id="chkOsmIntersections"> OSM intersections</label>
          <label class="check"><input type="checkbox" id="chkMicro"> Micro hotspots</label>
          <label class="check"><input type="checkbox" id="chkRings"> Distance rings</label>
        </div>
        <div class="row" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;">
          <label class="field">Top N
            <select id="topNSelect">
              <option value="5">5</option><option value="10" selected>10</option>
              <option value="20">20</option><option value="50">50</option>
            </select>
          </label>
          <label class="field">Cluster size
            <select id="precIntersections">
              <option value="3" selected>~100 m</option><option value="4">~10 m</option>
            </select>
          </label>
          <label class="field">Micro size
            <select id="precMicro">
              <option value="4" selected>~10 m</option><option value="5">~1 m</option>
            </select>
          </label>
        </div>
      </div>

      <div class="kbd-hint"><kbd>/</kbd> search &nbsp; <kbd>Esc</kbd> close &nbsp; <kbd>T</kbd> theme</div>
    </div>

    <!-- ═══ ANALYTICS ═══ -->
    <div class="panel" id="panelAnalytics">
      <div class="an-summary" id="analyticsSummary"></div>

      <div class="chart-block">
        <div class="chart-title">By hour of day <span class="sub" id="chartHourSub"></span></div>
        <div id="chartHour"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">By day of week <span class="sub" id="chartDowSub"></span></div>
        <div id="chartDow"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">12-week trend <span class="sub" id="chartTrendSub"></span></div>
        <div id="chartTrend"></div>
      </div>

      <div class="section">
        <div class="section-title">Normalized rates</div>
        <div id="ratesContent"><span class="rates-no-data">Waiting for data…</span></div>
      </div>

      <div class="section">
        <div class="section-title">Smart insights</div>
        <div id="insightsContent"><span class="rates-no-data">Waiting for data…</span></div>
      </div>
    </div>

    <!-- ═══ FEED ═══ -->
    <div class="panel" id="panelFeed">
      <div class="feed-meta" id="feedMeta"></div>
      <div id="feedList"></div>
    </div>

  </div>

  <div class="sb-footer">
    <div class="counts">
      <span>Total <b id="countTotal">0</b></span>
      <span>Filtered <b id="countFiltered">0</b></span>
      <span>In view <b id="countInView">0</b></span>
    </div>
    <label><input type="checkbox" id="chkInViewOnly" style="accent-color:var(--accent);"> In view</label>
    <button id="clearBtn" type="button">Reset</button>
  </div>
</div>

<div id="weatherChip" role="button" tabindex="0" aria-label="Current weather (tap for details)">
  <span class="wx-icon" id="wxIcon">⛅</span>
  <span class="wx-main" id="wxMain">Weather…</span>
</div>
<div id="weatherPanel" aria-live="polite">
  <div class="wp-title">Current conditions</div>
  <div id="weatherPanelBody">Loading latest observation…</div>
</div>

<div id="toast" role="status" aria-live="polite"></div>

<script src="__DATAJS_SRC__"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<script>
(function () {
  "use strict";

  /* ═══════════════════════ data contract ═══════════════════════ */
  let INCIDENTS = window.INCIDENTS_DATA || [];
  let OSM_INTERSECTIONS = window.OSM_INTERSECTIONS_DATA || [];
  let HOT_SPOTS = window.HOT_SPOTS_DATA || [];
  let UNLOCATED_COUNT = window.INCIDENTS_UNLOCATED_COUNT || 0;

  const IDX_LAT = 0, IDX_LNG = 1, IDX_REPORTED = 2, IDX_LOCATION = 3, IDX_CAUSE = 4,
        IDX_ASSIST = 5, IDX_WEIGHT = 6, IDX_COUNT = 7, IDX_TEMP_F = 8, IDX_PRECIP_PROB = 9,
        IDX_PRECIP_IN = 10, IDX_WIND_SPEED = 11, IDX_WIND_GUST = 12, IDX_VISIBILITY = 13,
        IDX_SKY_COVER = 14, IDX_WEATHER_AT = 15, IDX_WEATHER_SOURCE = 16, IDX_HOUR = 17,
        IDX_DOW = 18, IDX_SCHOOL_DAY = 19, IDX_NWS_FLOOD = 20, IDX_NWS_STORM = 21,
        IDX_NWS_TORNADO = 22, IDX_HIGHWAY = 23, IDX_CREATED_AT = 24;

  const DATAJS_SRC = "__DATAJS_SRC__";
  const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isCoarsePointer = window.matchMedia ? window.matchMedia("(pointer: coarse)").matches : false;
  const HAS_LEAFLET = typeof L !== "undefined";
  const isTouch = (HAS_LEAFLET && L.Browser && L.Browser.touch) || isCoarsePointer;

  /* ═══════════════════════ categories ═══════════════════════ */
  const CATEGORIES = [
    { id: "accident", label: "Accidents",  color: "#e5484d", fill: "#fca5a5", kw: ["ACCIDENT", "CRASH", "COLLISION", "WRECK", "MVA", "MVC", "HIT AND RUN", "HIT & RUN"] },
    { id: "fire",     label: "Fire",       color: "#ea580c", fill: "#fdba74", kw: ["FIRE", "SMOKE"] },
    { id: "hazard",   label: "Hazards",    color: "#ca8a04", fill: "#fde047", kw: ["HAZARD", "DEBRIS", "SPILL", "FLOOD", "WATER", "TREE", "OBSTRUCT", "DOWN", "ANIMAL", "ICE"] },
    { id: "control",  label: "Signals",    color: "#2563eb", fill: "#93c5fd", kw: ["TRAFFIC CONTROL", "SIGNAL", "TRAFFIC LIGHT", "LIGHT OUT", "MALFUNCTION"] },
    { id: "vehicle",  label: "Stalled",    color: "#9333ea", fill: "#d8b4fe", kw: ["STALL", "DISABLED", "ABANDON"] },
    { id: "medical",  label: "Medical",    color: "#0d9488", fill: "#5eead4", kw: ["MEDICAL", "INJURY", "PEDESTRIAN"] },
    { id: "other",    label: "Other",      color: "#64748b", fill: "#cbd5e1", kw: [] }
  ];
  const CAT_BY_ID = {};
  CATEGORIES.forEach(function (c) { CAT_BY_ID[c.id] = c; });

  const _catCache = new Map();
  function categoryOf(cause) {
    const key = String(cause || "").trim().toUpperCase();
    let cat = _catCache.get(key);
    if (cat) return cat;
    cat = CATEGORIES[CATEGORIES.length - 1];
    outer:
    for (const c of CATEGORIES) {
      for (const kw of c.kw) {
        if (key.indexOf(kw) !== -1) { cat = c; break outer; }
      }
    }
    _catCache.set(key, cat);
    return cat;
  }

  /* ═══════════════════════ generic helpers ═══════════════════════ */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function normalizeText(value) {
    if (value == null) return "";
    const text = String(value).trim();
    if (!text) return "";
    const lower = text.toLowerCase();
    if (lower === "nan" || lower === "none" || lower === "null" || lower === "undefined") return "";
    return text;
  }

  function titleCase(s) {
    const KEEP_UPPER = /^(I|US|LA|N|S|E|W|NE|NW|SE|SW|JCT|HWY|II|III|IV|UL)$/i;
    return String(s || "").toLowerCase().replace(/[a-z0-9']+/gi, function (w) {
      if (KEEP_UPPER.test(w)) return w.toUpperCase();
      if (/^\d/.test(w)) return w.toUpperCase();
      return w.charAt(0).toUpperCase() + w.slice(1);
    });
  }

  function formatCentralTime(value) {
    const text = normalizeText(value);
    if (!text) return "";
    const dt = new Date(text);
    if (Number.isNaN(dt.getTime())) return text;
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Chicago", month: "short", day: "2-digit",
        year: "numeric", hour: "numeric", minute: "2-digit"
      }).format(dt) + " CT";
    } catch (e) { return text; }
  }

  function relTime(dt, nowMs) {
    if (!dt) return "";
    const now = nowMs || Date.now();
    let s = Math.floor((now - dt.getTime()) / 1000);
    if (s < 0) s = 0;
    if (s < 60) return "just now";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " min ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
    const d = Math.floor(h / 24);
    if (d < 30) return d + (d === 1 ? " day ago" : " days ago");
    const mo = Math.floor(d / 30);
    if (mo < 12) return mo + (mo === 1 ? " month ago" : " months ago");
    const y = Math.floor(d / 365);
    return y + (y === 1 ? " year ago" : " years ago");
  }

  /* ═══════════════════════ date parsing (mirrors backend) ═══════════════════════ */
  function parseReported(reported) {
    if (!reported) return null;
    const s = String(reported).trim();
    if (!s) return null;

    function normalizeYY(yyRaw) {
      if (!yyRaw) return null;
      if (yyRaw.length === 2) {
        const yyNum = parseInt(yyRaw, 10);
        return String(yyNum <= 69 ? (2000 + yyNum) : (1900 + yyNum));
      }
      return yyRaw;
    }

    function buildValidatedDate(yy, mm, dd, hh, mi) {
      const y = parseInt(yy, 10), m = parseInt(mm, 10), d = parseInt(dd, 10);
      const h = (hh == null ? 0 : parseInt(hh, 10)), n = (mi == null ? 0 : parseInt(mi, 10));
      if (![y, m, d, h, n].every(Number.isFinite)) return null;
      const dt = new Date(y, m - 1, d, h, n, 0, 0);
      if (isNaN(dt.getTime())) return null;
      if (dt.getFullYear() !== y || dt.getMonth() !== (m - 1) || dt.getDate() !== d) return null;
      return dt;
    }

    // MM/DD/YYYY or MM/DD/YY with optional time and AM/PM.
    const m1 = s.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:(?:\s+|\s*[T@-]\s*)(\d{1,2}):(\d{2})(?::\d{2})?(?:\s*([AaPp][Mm]))?)?/);
    if (m1) {
      const mm = m1[1].padStart(2, "0"), dd = m1[2].padStart(2, "0"), yy = normalizeYY(m1[3]);
      let hh = m1[4] != null ? parseInt(m1[4], 10) : 0;
      const mi = m1[5] != null ? parseInt(m1[5], 10) : 0;
      const ap = m1[6] ? String(m1[6]).toLowerCase() : null;
      if (ap === "pm" && hh < 12) hh += 12;
      if (ap === "am" && hh === 12) hh = 0;
      if (hh < 0 || hh > 23) return null;
      const dt = buildValidatedDate(yy, mm, dd, hh, mi);
      return dt ? { mm: mm, dd: dd, yy: yy, hh: hh, mi: mi, dt: dt } : null;
    }

    // ISO-like: YYYY-MM-DD[T ]HH:MM[:SS]
    const m2 = s.match(/(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2})?)?/);
    if (m2) {
      const yy = m2[1], mm = m2[2], dd = m2[3];
      const hh = m2[4] != null ? parseInt(m2[4], 10) : 0;
      const mi = m2[5] != null ? parseInt(m2[5], 10) : 0;
      const dt = buildValidatedDate(yy, mm, dd, hh, mi);
      return dt ? { mm: mm, dd: dd, yy: yy, hh: hh, mi: mi, dt: dt } : null;
    }

    // MM/DD without year (assume current year), optional time.
    const m3 = s.match(/^(\d{1,2})\/(\d{1,2})(?!\/)(?:(?:\s+|\s*[T@-]\s*)(\d{1,2}):(\d{2})(?::\d{2})?(?:\s*([AaPp][Mm]))?)?\s*$/);
    if (m3) {
      const now = new Date();
      const mm = m3[1].padStart(2, "0"), dd = m3[2].padStart(2, "0"), yy = String(now.getFullYear());
      let hh = m3[3] != null ? parseInt(m3[3], 10) : 0;
      const mi = m3[4] != null ? parseInt(m3[4], 10) : 0;
      const ap = m3[5] ? String(m3[5]).toLowerCase() : null;
      if (ap === "pm" && hh < 12) hh += 12;
      if (ap === "am" && hh === 12) hh = 0;
      if (hh < 0 || hh > 23) return null;
      const dt = buildValidatedDate(yy, mm, dd, hh, mi);
      return dt ? { mm: mm, dd: dd, yy: yy, hh: hh, mi: mi, dt: dt } : null;
    }

    return null;
  }

  // Best display/sort timestamp for a row. Aggregated TRAFFIC CONTROL rows
  // encode "… | last: <ts>" — prefer that; otherwise the reported string,
  // then created_at.
  const _rowDateCache = new WeakMap();
  function bestRowDate(row) {
    if (_rowDateCache.has(row)) return _rowDateCache.get(row);
    let dt = null;
    const rep = String(row[IDX_REPORTED] || "");
    if (rep.indexOf("last:") !== -1) {
      const pr = parseReported(rep.slice(rep.indexOf("last:") + 5));
      if (pr) dt = pr.dt;
    }
    if (!dt) {
      const pr = parseReported(rep);
      if (pr) dt = pr.dt;
    }
    if (!dt) {
      const created = normalizeText(row.length > IDX_CREATED_AT ? row[IDX_CREATED_AT] : "");
      if (created) {
        const c = new Date(created);
        if (!isNaN(c.getTime())) dt = c;
      }
    }
    _rowDateCache.set(row, dt);
    return dt;
  }

  function dayTypeOf(dt) {
    const d = dt.getDay();
    return (d === 0 || d === 6) ? "weekend" : "weekday";
  }

  function timeBlockOf(hh) {
    if (hh == null) return "unknown";
    if (hh >= 6 && hh < 10) return "morning";
    if (hh >= 10 && hh < 15) return "midday";
    if (hh >= 15 && hh < 19) return "evening";
    if (hh >= 19 && hh <= 23) return "night";
    if (hh >= 0 && hh < 6) return "latenight";
    return "unknown";
  }

  function isSchoolDayJS(dt) {
    if (!dt) return false;
    const dow = dt.getDay();
    if (dow === 0 || dow === 6) return false;
    const month = dt.getMonth() + 1, day = dt.getDate();
    if (month === 6 || month === 7) return false;
    if (month === 8 && day < 15) return false;
    if (month === 12 && day >= 20) return false;
    if (month === 1 && day <= 3) return false;
    if (month === 11 && day >= 21 && day <= 27) {
      const nov1 = new Date(dt.getFullYear(), 10, 1);
      const thu = (4 - nov1.getDay() + 7) % 7;
      const thanksgiving = 1 + thu + 21;
      if (day >= thanksgiving - 1 && day <= thanksgiving + 1) return false;
    }
    return true;
  }

  /* ═══════════════════════ elements ═══════════════════════ */
  function $(id) { return document.getElementById(id); }
  const els = {
    sidebar: $("sidebar"), sbHandle: $("sbHandle"),
    themeBtn: $("themeBtn"),
    statusText: $("statusText"), unlocChip: $("unlocChip"), alertBanner: $("alertBanner"),
    tileToday: $("tileToday"),
    tileWeekVal: $("tileWeekVal"), tileMonthVal: $("tileMonthVal"), tileTotalVal: $("tileTotalVal"),
    legendChips: $("legendChips"),
    tabFilters: $("tabFilters"), tabAnalytics: $("tabAnalytics"), tabFeed: $("tabFeed"),
    panelFilters: $("panelFilters"), panelAnalytics: $("panelAnalytics"), panelFeed: $("panelFeed"),
    filterBadge: $("filterBadge"),
    roadSearch: $("roadSearch"), roadSearchClear: $("roadSearchClear"),
    rangeChips: $("rangeChips"),
    causeGroupSelect: $("causeGroupSelect"), causeSelect: $("causeSelect"),
    monthSelect: $("monthSelect"), daySelect: $("daySelect"), yearSelect: $("yearSelect"),
    dowSelect: $("dowSelect"), dayTypeSelect: $("dayTypeSelect"), timeBlockSelect: $("timeBlockSelect"),
    roadTypeSelect: $("roadTypeSelect"),
    chkRushHour: $("chkRushHour"), chkSchoolDay: $("chkSchoolDay"),
    chkFloodWarning: $("chkFloodWarning"), chkThunderstormWarning: $("chkThunderstormWarning"),
    chkTornadoWatch: $("chkTornadoWatch"),
    chkWeatherOnly: $("chkWeatherOnly"), tempBand: $("tempBand"), precipBand: $("precipBand"),
    precipAmountBand: $("precipAmountBand"), windBand: $("windBand"), visBand: $("visBand"), cloudBand: $("cloudBand"),
    chkPoints: $("chkPoints"), chkHeat: $("chkHeat"), chkHotSpots: $("chkHotSpots"),
    chkIntersections: $("chkIntersections"), chkOsmIntersections: $("chkOsmIntersections"),
    chkMicro: $("chkMicro"), chkRings: $("chkRings"),
    topNSelect: $("topNSelect"), precIntersections: $("precIntersections"), precMicro: $("precMicro"),
    countTotal: $("countTotal"), countFiltered: $("countFiltered"), countInView: $("countInView"),
    chkInViewOnly: $("chkInViewOnly"), clearBtn: $("clearBtn"),
    analyticsSummary: $("analyticsSummary"),
    chartHour: $("chartHour"), chartHourSub: $("chartHourSub"),
    chartDow: $("chartDow"), chartDowSub: $("chartDowSub"),
    chartTrend: $("chartTrend"), chartTrendSub: $("chartTrendSub"),
    ratesContent: $("ratesContent"), insightsContent: $("insightsContent"),
    feedList: $("feedList"), feedMeta: $("feedMeta"),
    weatherChip: $("weatherChip"), wxIcon: $("wxIcon"), wxMain: $("wxMain"),
    weatherPanel: $("weatherPanel"), weatherPanelBody: $("weatherPanelBody"),
    toast: $("toast"), metaThemeColor: $("metaThemeColor")
  };

  /* ═══════════════════════ toast ═══════════════════════ */
  let toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.classList.remove("show"); }, 2300);
  }

  /* ═══════════════════════ theme ═══════════════════════ */
  const THEME_KEY = "laf911.theme";
  const mediaDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function themePref() {
    try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; }
  }

  function resolvedTheme() {
    const pref = themePref();
    if (pref === "light" || pref === "dark") return pref;
    return (mediaDark && mediaDark.matches) ? "dark" : "light";
  }

  function applyTheme() {
    const theme = resolvedTheme();
    document.documentElement.setAttribute("data-theme", theme);
    if (els.metaThemeColor) els.metaThemeColor.setAttribute("content", theme === "dark" ? "#0b1120" : "#f5f6f8");
    setBaseLayer(theme);
  }

  function cycleTheme() {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(themePref()) + 1) % order.length];
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    applyTheme();
    toast("Theme: " + next + (next === "auto" ? " (follows system)" : ""));
  }

  /* ═══════════════════════ map + tiles ═══════════════════════ */
  let map = null;
  let renderer = null;
  let baseLayers = { light: null, dark: null, fallback: null };
  let activeBase = null;
  let usingFallbackTiles = false;
  let tileErrorCount = 0;

  const CARTO_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';
  const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

  function makeTileLayer(kind) {
    if (kind === "fallback") {
      return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: OSM_ATTR });
    }
    const style = kind === "dark" ? "dark_all" : "rastertiles/voyager";
    const layer = L.tileLayer("https://{s}.basemaps.cartocdn.com/" + style + "/{z}/{x}/{y}{r}.png", {
      maxZoom: 20, subdomains: "abcd", attribution: CARTO_ATTR
    });
    layer.on("tileerror", function () {
      tileErrorCount += 1;
      if (tileErrorCount > 6 && !usingFallbackTiles) {
        usingFallbackTiles = true;
        setBaseLayer(resolvedTheme());
      }
    });
    return layer;
  }

  function setBaseLayer(theme) {
    if (!map) return;
    const kind = usingFallbackTiles ? "fallback" : theme;
    if (!baseLayers[kind]) baseLayers[kind] = makeTileLayer(kind);
    if (activeBase === baseLayers[kind]) {
      $("map").classList.toggle("osm-fallback", usingFallbackTiles);
      return;
    }
    if (activeBase) { try { map.removeLayer(activeBase); } catch (e) {} }
    activeBase = baseLayers[kind];
    activeBase.addTo(map);
    $("map").classList.toggle("osm-fallback", usingFallbackTiles);
  }

  if (HAS_LEAFLET) {
    map = L.map("map", {
      center: [__CENTER_LAT__, __CENTER_LNG__],
      zoom: 12,
      zoomControl: false,
      attributionControl: false,
      preferCanvas: true
    });
    renderer = L.canvas({ padding: 0.5 });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.attribution({ position: "bottomright", prefix: false }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: true, metric: false }).addTo(map);

    // Fit to data when there is enough of it.
    if (INCIDENTS.length > 5) {
      let latMin = Infinity, latMax = -Infinity, lngMin = Infinity, lngMax = -Infinity;
      for (const r of INCIDENTS) {
        if (r[IDX_LAT] < latMin) latMin = r[IDX_LAT];
        if (r[IDX_LAT] > latMax) latMax = r[IDX_LAT];
        if (r[IDX_LNG] < lngMin) lngMin = r[IDX_LNG];
        if (r[IDX_LNG] > lngMax) lngMax = r[IDX_LNG];
      }
      if (isFinite(latMin)) {
        try {
          map.fitBounds([[latMin, lngMin], [latMax, lngMax]], { padding: [40, 40], maxZoom: 13 });
        } catch (e) {}
      }
    }
  } else {
    document.body.classList.add("no-map");
  }

  /* ═══════════════════════ popup content ═══════════════════════ */
  function weatherNumber(row, idx) {
    if (!row || row.length <= idx) return null;
    const val = parseFloat(row[idx]);
    return Number.isFinite(val) ? val : null;
  }

  function hasWeatherData(row) {
    return (
      weatherNumber(row, IDX_TEMP_F) != null || weatherNumber(row, IDX_PRECIP_PROB) != null ||
      weatherNumber(row, IDX_PRECIP_IN) != null || weatherNumber(row, IDX_WIND_SPEED) != null ||
      weatherNumber(row, IDX_VISIBILITY) != null || weatherNumber(row, IDX_SKY_COVER) != null
    );
  }

  function popupHtml(row) {
    const cat = categoryOf(row[IDX_CAUSE]);
    const dt = bestRowDate(row);
    const occurrences = (row.length > IDX_COUNT && row[IDX_COUNT] != null) ? parseInt(row[IDX_COUNT], 10) : 1;

    const rows = [];
    const reported = normalizeText(row[IDX_REPORTED]);
    if (reported) rows.push("<div>Reported: <b>" + esc(reported) + "</b></div>");
    const assist = normalizeText(row[IDX_ASSIST]);
    if (assist) rows.push("<div>Assisting: <b>" + esc(assist) + "</b></div>");
    if (occurrences > 1) rows.push("<div>Occurrences: <b>" + occurrences + "</b></div>");
    const hw = normalizeText(row.length > IDX_HIGHWAY ? row[IDX_HIGHWAY] : "");
    if (hw) rows.push("<div>Road class: <b>" + esc(hw.replace(/_/g, " ")) + "</b></div>");

    const ctx = [];
    const dowNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const dowVal = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    const hourVal = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    if (dowVal !== null && dowNames[dowVal]) ctx.push(dowNames[dowVal]);
    if (hourVal !== null) ctx.push(String(hourVal).padStart(2, "0") + ":00 hour");
    if (row.length > IDX_SCHOOL_DAY && row[IDX_SCHOOL_DAY] === 1) ctx.push("school day");
    if (ctx.length) rows.push("<div>Context: <b>" + esc(ctx.join(" · ")) + "</b></div>");

    const alerts = [];
    if (row.length > IDX_NWS_FLOOD && (row[IDX_NWS_FLOOD] === 1 || row[IDX_NWS_FLOOD] === true)) alerts.push("Flash Flood Warning");
    if (row.length > IDX_NWS_STORM && (row[IDX_NWS_STORM] === 1 || row[IDX_NWS_STORM] === true)) alerts.push("Severe Thunderstorm Warning");
    if (row.length > IDX_NWS_TORNADO && (row[IDX_NWS_TORNADO] === 1 || row[IDX_NWS_TORNADO] === true)) alerts.push("Tornado Watch");
    const alertLine = alerts.length ? "<div class='pc-alert'>⚠ NWS active: " + esc(alerts.join(", ")) + "</div>" : "";

    const wx = [];
    const temp = weatherNumber(row, IDX_TEMP_F);
    const pop = weatherNumber(row, IDX_PRECIP_PROB);
    const precipIn = weatherNumber(row, IDX_PRECIP_IN);
    const wind = weatherNumber(row, IDX_WIND_SPEED);
    const gust = weatherNumber(row, IDX_WIND_GUST);
    const vis = weatherNumber(row, IDX_VISIBILITY);
    const sky = weatherNumber(row, IDX_SKY_COVER);
    if (temp != null) wx.push(temp.toFixed(0) + "°F");
    if (pop != null) wx.push(pop.toFixed(0) + "% precip");
    if (precipIn != null && precipIn > 0) wx.push(precipIn.toFixed(2) + " in rain");
    if (wind != null) wx.push(wind.toFixed(0) + " mph wind");
    if (gust != null) wx.push("gust " + gust.toFixed(0));
    if (vis != null) wx.push(vis.toFixed(1) + " mi vis");
    if (sky != null) wx.push(sky.toFixed(0) + "% clouds");
    const wAt = formatCentralTime(row.length > IDX_WEATHER_AT ? row[IDX_WEATHER_AT] : "");
    let wxHtml = "";
    if (wx.length) {
      wxHtml = "<div class='pc-wx'>" + wx.map(function (p) { return "<span>" + esc(p) + "</span>"; }).join("") + "</div>";
      if (wAt) wxHtml += "<div style='font-size:10px;color:var(--text-3);margin-top:3px;'>Conditions observed " + esc(wAt) + "</div>";
    }

    const lat = row[IDX_LAT], lng = row[IDX_LNG];
    const gmaps = "https://www.google.com/maps/search/?api=1&query=" + lat + "," + lng;
    const sview = "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=" + lat + "," + lng;

    return "<div class='pc' style='--cat:" + cat.color + "'>" +
      "<div class='pc-head'><span class='pc-badge'>" + esc(cat.label) + "</span>" +
      "<span class='pc-time'>" + esc(relTime(dt)) + "</span></div>" +
      "<div class='pc-title'>" + esc(titleCase(row[IDX_LOCATION])) + "</div>" +
      "<div class='pc-rows'>" + rows.join("") + "</div>" +
      alertLine + wxHtml +
      "<div class='pc-links'>" +
      "<a href='" + gmaps + "' target='_blank' rel='noopener'>Google Maps</a>" +
      "<a href='" + sview + "' target='_blank' rel='noopener'>Street View</a>" +
      "</div></div>";
  }

  function createIncidentPopup(rows) {
    let idx = 0;
    const container = document.createElement("div");
    if (HAS_LEAFLET && L.DomEvent) L.DomEvent.disableClickPropagation(container);

    function render() {
      const hasNav = rows.length > 1;
      container.innerHTML =
        (hasNav
          ? "<div class='pc-nav'>" +
            "<button class='pc-prev' type='button' aria-label='Previous incident'>&larr;</button>" +
            "<span class='pc-nav-count'>" + (idx + 1) + " of " + rows.length + "</span>" +
            "<button class='pc-next' type='button' aria-label='Next incident'>&rarr;</button>" +
            "</div>"
          : "") +
        popupHtml(rows[idx]);
      if (hasNav) {
        const prev = container.querySelector(".pc-prev");
        const next = container.querySelector(".pc-next");
        prev.disabled = idx <= 0;
        next.disabled = idx >= rows.length - 1;
        prev.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); if (idx > 0) { idx--; render(); } });
        next.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); if (idx < rows.length - 1) { idx++; render(); } });
      }
    }
    render();
    return container;
  }

  /* ═══════════════════════ filters ═══════════════════════ */
  const state = {
    cats: new Set(CATEGORIES.map(function (c) { return c.id; })),  // enabled category ids
    range: ""                                                       // '', today, 24h, 7d, 30d
  };

  const CAUSE_GROUPS = [
    { id: "fire", label: "Fire", keywords: ["FIRE"] },
    { id: "accident", label: "Accident", keywords: ["ACCIDENT", "CRASH", "COLLISION", "WRECK", "MVA", "MVC"] }
  ];

  function normalizeCause(cause) { return String(cause || "").trim().toUpperCase(); }

  function matchesCause(row, selected) {
    if (!selected || selected === "__ALL__") return true;
    return String(row[IDX_CAUSE] || "").trim() === selected;
  }

  function matchesCauseGroup(row, selectedGroup) {
    if (!selectedGroup || selectedGroup === "__ALL__") return true;
    const causeNorm = normalizeCause(row[IDX_CAUSE]);
    const group = CAUSE_GROUPS.find(function (g) { return g.id === selectedGroup; });
    if (!group) return true;
    return group.keywords.some(function (kw) { return causeNorm.indexOf(kw) !== -1; });
  }

  function matchesCats(row) {
    if (state.cats.size >= CATEGORIES.length) return true;
    return state.cats.has(categoryOf(row[IDX_CAUSE]).id);
  }

  function matchesRange(row, range, nowMs) {
    if (!range) return true;
    if (range === "today") {
      const now = new Date(nowMs);
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr && pr.dt.getFullYear() === now.getFullYear() && pr.dt.getMonth() === now.getMonth() && pr.dt.getDate() === now.getDate()) return true;
      const createdRaw = normalizeText(row.length > IDX_CREATED_AT ? row[IDX_CREATED_AT] : "");
      if (createdRaw) {
        const c = new Date(createdRaw);
        if (!isNaN(c.getTime()) && c.getFullYear() === now.getFullYear() && c.getMonth() === now.getMonth() && c.getDate() === now.getDate()) return true;
      }
      return false;
    }
    const hours = range === "24h" ? 24 : range === "7d" ? 168 : range === "30d" ? 720 : null;
    if (hours == null) return true;
    const dt = bestRowDate(row);
    if (!dt) return false;
    return (nowMs - dt.getTime()) <= hours * 3600 * 1000;
  }

  function matchesDateFilter(row, f) {
    if (!f.mm && !f.dd && !f.yy) return true;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr) return false;
    if (f.yy && pr.yy !== f.yy) return false;
    if (f.mm && pr.mm !== f.mm) return false;
    if (f.dd && pr.dd !== f.dd) return false;
    return true;
  }

  function matchesDayType(row, dayType) {
    if (dayType === "all") return true;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return dayTypeOf(pr.dt) === dayType;
  }

  function matchesTimeBlock(row, block) {
    if (block === "all") return true;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr) return false;
    const tb = timeBlockOf(pr.hh);
    return tb !== "unknown" && tb === block;
  }

  function matchesTempBand(temp, band) {
    if (band === "any") return true;
    if (temp == null) return false;
    if (band === "cold") return temp <= 50;
    if (band === "mild") return temp > 50 && temp < 70;
    if (band === "warm") return temp >= 70 && temp < 85;
    if (band === "hot") return temp >= 85;
    return true;
  }

  function matchesPrecipBand(pop, band) {
    if (band === "any") return true;
    if (pop == null) return false;
    if (band === "low") return pop < 20;
    if (band === "med") return pop >= 20 && pop < 60;
    if (band === "high") return pop >= 60;
    return true;
  }

  function matchesPrecipAmountBand(precipIn, band) {
    if (band === "any") return true;
    if (precipIn == null) return false;
    if (band === "none") return precipIn <= 0.01;
    if (band === "light") return precipIn > 0.01 && precipIn <= 0.10;
    if (band === "moderate") return precipIn > 0.10 && precipIn < 0.50;
    if (band === "heavy") return precipIn >= 0.50;
    return true;
  }

  function matchesWindBand(wind, band) {
    if (band === "any") return true;
    if (wind == null) return false;
    if (band === "calm") return wind < 10;
    if (band === "breezy") return wind >= 10 && wind < 20;
    if (band === "windy") return wind >= 20;
    return true;
  }

  function matchesVisibilityBand(vis, band) {
    if (band === "any") return true;
    if (vis == null) return false;
    if (band === "low") return vis < 3;
    if (band === "hazy") return vis >= 3 && vis < 10;
    if (band === "clear") return vis >= 10;
    return true;
  }

  function matchesCloudBand(cloud, band) {
    if (band === "any") return true;
    if (cloud == null) return false;
    if (band === "clear") return cloud < 25;
    if (band === "partly") return cloud >= 25 && cloud < 70;
    if (band === "overcast") return cloud >= 70;
    return true;
  }

  function matchesWeather(row, f) {
    if (f.weatherOnly && !hasWeatherData(row)) return false;
    if (!matchesTempBand(weatherNumber(row, IDX_TEMP_F), f.tempBand)) return false;
    if (!matchesPrecipBand(weatherNumber(row, IDX_PRECIP_PROB), f.precipBand)) return false;
    if (!matchesPrecipAmountBand(weatherNumber(row, IDX_PRECIP_IN), f.precipAmountBand)) return false;
    if (!matchesWindBand(weatherNumber(row, IDX_WIND_SPEED), f.windBand)) return false;
    if (!matchesVisibilityBand(weatherNumber(row, IDX_VISIBILITY), f.visBand)) return false;
    if (!matchesCloudBand(weatherNumber(row, IDX_SKY_COVER), f.cloudBand)) return false;
    return true;
  }

  function isRushHourFromParsed(pr) {
    if (!pr || pr.hh == null) return false;
    const dow = pr.dt ? pr.dt.getDay() : -1;
    if (dow === 0 || dow === 6) return false;
    return (pr.hh >= 7 && pr.hh < 9) || (pr.hh >= 16 && pr.hh < 19);
  }

  function matchesRushHour(row, checked) {
    if (!checked) return true;
    const storedHour = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    const storedDow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (storedHour !== null && storedDow !== null) {
      if (storedDow >= 5) return false;  // Python weekday: 5=Sat, 6=Sun
      return (storedHour >= 7 && storedHour < 9) || (storedHour >= 16 && storedHour < 19);
    }
    return isRushHourFromParsed(parseReported(row[IDX_REPORTED]));
  }

  function matchesSchoolDay(row, checked) {
    if (!checked) return true;
    const val = (row.length > IDX_SCHOOL_DAY) ? row[IDX_SCHOOL_DAY] : null;
    if (val === 1 || val === true) return true;
    if (val === 0 || val === false) return false;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return isSchoolDayJS(pr.dt);
  }

  function matchesDow(row, dowValue) {
    if (!dowValue || dowValue === "all") return true;
    const target = parseInt(dowValue, 10);
    const storedDow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (storedDow !== null) return ((storedDow + 1) % 7) === target;  // py→js dow
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return pr.dt.getDay() === target;
  }

  // Space-optimized Levenshtein for fuzzy road search.
  function levenshtein(a, b) {
    if (a === b) return 0;
    const m = a.length, n = b.length;
    if (m === 0) return n;
    if (n === 0) return m;
    let r0 = Array.from({ length: n + 1 }, function (_, i) { return i; });
    let r1 = new Array(n + 1);
    for (let i = 1; i <= m; i++) {
      r1[0] = i;
      for (let j = 1; j <= n; j++) {
        r1[j] = a[i - 1] === b[j - 1] ? r0[j - 1] : 1 + Math.min(r0[j], r1[j - 1], r0[j - 1]);
      }
      const t = r0; r0 = r1; r1 = t;
    }
    return r0[n];
  }

  function fuzzyWordMatch(word, loc) {
    if (loc.indexOf(word) !== -1) return true;
    if (word.length <= 2) return false;
    const maxDist = word.length <= 4 ? 1 : 2;
    const tokens = loc.split(/[\s\-\/,&.]+/);
    for (const tok of tokens) {
      if (tok.length === 0) continue;
      if (Math.abs(tok.length - word.length) > maxDist + 1) continue;
      if (levenshtein(word, tok) <= maxDist) return true;
    }
    return false;
  }

  function matchesRoadSearch(row, term) {
    if (!term) return true;
    const loc = String(row[IDX_LOCATION] || "").toLowerCase();
    const words = term.toLowerCase().split(/\s+/).filter(function (w) { return w.length > 0; });
    return words.every(function (w) { return fuzzyWordMatch(w, loc); });
  }

  function matchesRoadType(row, roadType) {
    if (!roadType || roadType === "any") return true;
    const hw = (row.length > IDX_HIGHWAY) ? row[IDX_HIGHWAY] : null;
    if (!hw) return false;
    const hwLower = String(hw).toLowerCase();
    if (roadType === "motorway") return hwLower.indexOf("motorway") !== -1;
    if (roadType === "trunk") return hwLower.indexOf("trunk") !== -1;
    if (roadType === "primary") return hwLower.indexOf("primary") !== -1;
    if (roadType === "secondary") return hwLower.indexOf("secondary") !== -1 || hwLower.indexOf("tertiary") !== -1;
    if (roadType === "residential") return (
      hwLower.indexOf("residential") !== -1 || hwLower === "unclassified" ||
      hwLower === "service" || hwLower === "living_street"
    );
    return true;
  }

  function matchesNwsAlerts(row, f) {
    if (!f.chkFlood && !f.chkStorm && !f.chkTornado) return true;
    const flood = (row.length > IDX_NWS_FLOOD) ? row[IDX_NWS_FLOOD] : null;
    const storm = (row.length > IDX_NWS_STORM) ? row[IDX_NWS_STORM] : null;
    const tornado = (row.length > IDX_NWS_TORNADO) ? row[IDX_NWS_TORNADO] : null;
    if (f.chkFlood && !(flood === 1 || flood === true)) return false;
    if (f.chkStorm && !(storm === 1 || storm === true)) return false;
    if (f.chkTornado && !(tornado === 1 || tornado === true)) return false;
    return true;
  }

  function currentFilterObj() {
    return {
      mm: (els.monthSelect.value || "").trim(),
      dd: (els.daySelect.value || "").trim(),
      yy: (els.yearSelect.value || "").trim(),
      dayType: (els.dayTypeSelect.value || "all").trim(),
      timeBlock: (els.timeBlockSelect.value || "all").trim(),
      cause: (els.causeSelect.value || "__ALL__").trim(),
      causeGroup: (els.causeGroupSelect.value || "__ALL__").trim(),
      inViewOnly: !!els.chkInViewOnly.checked,
      weatherOnly: !!els.chkWeatherOnly.checked,
      tempBand: (els.tempBand.value || "any").trim(),
      precipBand: (els.precipBand.value || "any").trim(),
      precipAmountBand: (els.precipAmountBand.value || "any").trim(),
      windBand: (els.windBand.value || "any").trim(),
      visBand: (els.visBand.value || "any").trim(),
      cloudBand: (els.cloudBand.value || "any").trim(),
      rushHour: !!els.chkRushHour.checked,
      schoolDay: !!els.chkSchoolDay.checked,
      dowValue: (els.dowSelect.value || "all").trim(),
      roadType: (els.roadTypeSelect.value || "any").trim(),
      roadSearch: (els.roadSearch.value || "").trim(),
      chkFlood: !!els.chkFloodWarning.checked,
      chkStorm: !!els.chkThunderstormWarning.checked,
      chkTornado: !!els.chkTornadoWatch.checked,
      range: state.range
    };
  }

  function countActiveFilters(f) {
    let n = 0;
    if (f.roadSearch) n++;
    if (f.range) n++;
    if (f.cause !== "__ALL__") n++;
    if (f.causeGroup !== "__ALL__") n++;
    if (f.mm || f.dd || f.yy) n++;
    if (f.dayType !== "all") n++;
    if (f.timeBlock !== "all") n++;
    if (f.dowValue !== "all") n++;
    if (f.roadType !== "any") n++;
    if (f.rushHour) n++;
    if (f.schoolDay) n++;
    if (f.weatherOnly) n++;
    if (f.tempBand !== "any") n++;
    if (f.precipBand !== "any") n++;
    if (f.precipAmountBand !== "any") n++;
    if (f.windBand !== "any") n++;
    if (f.visBand !== "any") n++;
    if (f.cloudBand !== "any") n++;
    if (f.chkFlood || f.chkStorm || f.chkTornado) n++;
    if (state.cats.size < CATEGORIES.length) n++;
    return n;
  }

  function filteredIncidents(f, mapObj) {
    const out = [];
    const bounds = (f.inViewOnly && mapObj) ? mapObj.getBounds() : null;
    const nowMs = Date.now();
    for (const row of INCIDENTS) {
      if (!matchesCats(row)) continue;
      if (!matchesCauseGroup(row, f.causeGroup)) continue;
      if (!matchesCause(row, f.cause)) continue;
      if (!matchesRange(row, f.range, nowMs)) continue;
      if (!matchesDateFilter(row, f)) continue;
      if (!matchesDayType(row, f.dayType)) continue;
      if (!matchesTimeBlock(row, f.timeBlock)) continue;
      if (!matchesWeather(row, f)) continue;
      if (!matchesRushHour(row, f.rushHour)) continue;
      if (!matchesSchoolDay(row, f.schoolDay)) continue;
      if (!matchesDow(row, f.dowValue)) continue;
      if (!matchesRoadType(row, f.roadType)) continue;
      if (!matchesRoadSearch(row, f.roadSearch)) continue;
      if (!matchesNwsAlerts(row, f)) continue;
      if (bounds && !bounds.contains(L.latLng(row[IDX_LAT], row[IDX_LNG]))) continue;
      out.push(row);
    }
    return out;
  }

  function computeInViewCount(rows, mapObj) {
    if (!mapObj) return 0;
    const b = mapObj.getBounds();
    let c = 0;
    for (const row of rows) {
      if (b.contains(L.latLng(row[IDX_LAT], row[IDX_LNG]))) c += 1;
    }
    return c;
  }

  /* ═══════════════════════ URL state (shareable views) ═══════════════════════ */
  const HASH_FIELDS = [
    ["q", "roadSearch", ""], ["cg", "causeGroupSelect", "__ALL__"], ["c", "causeSelect", "__ALL__"],
    ["mm", "monthSelect", ""], ["dd", "daySelect", ""], ["yy", "yearSelect", ""],
    ["dow", "dowSelect", "all"], ["dt", "dayTypeSelect", "all"], ["tb", "timeBlockSelect", "all"],
    ["rt", "roadTypeSelect", "any"],
    ["tmp", "tempBand", "any"], ["pp", "precipBand", "any"], ["pa", "precipAmountBand", "any"],
    ["wnd", "windBand", "any"], ["vis", "visBand", "any"], ["cld", "cloudBand", "any"]
  ];
  const HASH_CHECKS = [
    ["rush", "chkRushHour"], ["school", "chkSchoolDay"], ["wo", "chkWeatherOnly"],
    ["flood", "chkFloodWarning"], ["storm", "chkThunderstormWarning"], ["tor", "chkTornadoWatch"],
    ["heat", "chkHeat"], ["hot", "chkHotSpots"]
  ];

  let hashApplying = false;

  function updateHash() {
    if (hashApplying) return;
    const p = new URLSearchParams();
    for (const spec of HASH_FIELDS) {
      const el = $(spec[1]);
      const v = (el.value || "").trim();
      if (v && v !== spec[2]) p.set(spec[0], v);
    }
    for (const spec of HASH_CHECKS) {
      if ($(spec[1]).checked) p.set(spec[0], "1");
    }
    if (state.range) p.set("range", state.range);
    if (state.cats.size < CATEGORIES.length) {
      p.set("cats", Array.from(state.cats).join("."));
    }
    const str = p.toString();
    try {
      history.replaceState(null, "", str ? ("#" + str) : (location.pathname + location.search));
    } catch (e) {}
  }

  function applyHash() {
    const raw = (location.hash || "").replace(/^#/, "");
    if (!raw) return;
    let p;
    try { p = new URLSearchParams(raw); } catch (e) { return; }
    hashApplying = true;
    for (const spec of HASH_FIELDS) {
      if (p.has(spec[0])) $(spec[1]).value = p.get(spec[0]);
    }
    for (const spec of HASH_CHECKS) {
      if (p.has(spec[0])) $(spec[1]).checked = p.get(spec[0]) === "1";
    }
    if (p.has("range")) setRange(p.get("range"), true);
    if (p.has("cats")) {
      const ids = p.get("cats").split(".").filter(function (id) { return CAT_BY_ID[id]; });
      if (ids.length) state.cats = new Set(ids);
    }
    if (els.roadSearch.value) els.roadSearchClear.classList.add("show");
    hashApplying = false;
  }

  /* ═══════════════════════ stat tiles / status ═══════════════════════ */
  function renderStatTiles() {
    const now = new Date();
    const nowMs = now.getTime();
    let today = 0, week = 0, month = 0;
    for (const row of INCIDENTS) {
      const dt = bestRowDate(row);
      if (!dt) continue;
      const age = nowMs - dt.getTime();
      if (dt.getFullYear() === now.getFullYear() && dt.getMonth() === now.getMonth() && dt.getDate() === now.getDate()) today++;
      if (age <= 7 * 86400000) week++;
      if (age <= 30 * 86400000) month++;
    }
    els.tileToday.querySelector("b").textContent = today.toLocaleString();
    els.tileToday.classList.toggle("hot", today > 0);
    els.tileWeekVal.textContent = week.toLocaleString();
    els.tileMonthVal.textContent = month.toLocaleString();
    els.tileTotalVal.textContent = INCIDENTS.length.toLocaleString();
  }

  function newestDataDate() {
    let best = null;
    for (const row of INCIDENTS) {
      const created = normalizeText(row.length > IDX_CREATED_AT ? row[IDX_CREATED_AT] : "");
      if (created) {
        const c = new Date(created);
        if (!isNaN(c.getTime()) && (!best || c > best)) best = c;
      }
    }
    if (!best) {
      for (const row of INCIDENTS) {
        const dt = bestRowDate(row);
        if (dt && (!best || dt > best)) best = dt;
      }
    }
    return best;
  }

  function renderStatus() {
    const newest = newestDataDate();
    if (!INCIDENTS.length) {
      els.statusText.textContent = "No incident data loaded yet.";
    } else if (newest) {
      els.statusText.textContent = "Latest incident " + relTime(newest);
    } else {
      els.statusText.textContent = INCIDENTS.length.toLocaleString() + " incidents loaded";
    }
    if (UNLOCATED_COUNT > 0) {
      els.unlocChip.style.display = "";
      els.unlocChip.textContent = UNLOCATED_COUNT + " locating…";
    } else {
      els.unlocChip.style.display = "none";
    }
  }

  /* ═══════════════════════ legend chips ═══════════════════════ */
  function renderLegend() {
    const counts = {};
    for (const row of INCIDENTS) {
      const id = categoryOf(row[IDX_CAUSE]).id;
      counts[id] = (counts[id] || 0) + 1;
    }
    els.legendChips.innerHTML = "";
    for (const cat of CATEGORIES) {
      if (!counts[cat.id]) continue;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "legend-chip " + (state.cats.has(cat.id) ? "on" : "off");
      btn.style.setProperty("--cat", cat.color);
      btn.setAttribute("aria-pressed", state.cats.has(cat.id) ? "true" : "false");
      btn.title = "Toggle " + cat.label.toLowerCase() + " (long-press / double-click to isolate)";
      btn.innerHTML = "<span class='dot'></span>" + esc(cat.label) + " <span class='n'>" + counts[cat.id].toLocaleString() + "</span>";
      btn.addEventListener("click", function () {
        if (state.cats.has(cat.id) && state.cats.size === 1) {
          state.cats = new Set(CATEGORIES.map(function (c) { return c.id; }));  // un-isolate
        } else if (state.cats.has(cat.id)) {
          state.cats.delete(cat.id);
        } else {
          state.cats.add(cat.id);
        }
        renderLegend();
        scheduleRender(0);
      });
      btn.addEventListener("dblclick", function (e) {
        e.preventDefault();
        state.cats = new Set([cat.id]);  // isolate this category
        renderLegend();
        scheduleRender(0);
      });
      els.legendChips.appendChild(btn);
    }
  }

  /* ═══════════════════════ charts (inline SVG) ═══════════════════════ */
  function barChartSVG(values, opts) {
    const n = values.length;
    const W = 360, H = opts.height || 74, padB = 14, padT = 4;
    const gap = opts.gap != null ? opts.gap : 2;
    const bw = (W - gap * (n - 1)) / n;
    const max = Math.max.apply(null, values.concat([1]));
    const peakIdx = values.indexOf(Math.max.apply(null, values));
    let bars = "";
    for (let i = 0; i < n; i++) {
      const h = values[i] > 0 ? Math.max(2, (values[i] / max) * (H - padB - padT)) : 1.5;
      const x = i * (bw + gap);
      const y = H - padB - h;
      const cls = (i === peakIdx && values[i] > 0) ? "bar peak" : "bar";
      bars += "<rect class='" + cls + "' x='" + x.toFixed(1) + "' y='" + y.toFixed(1) +
        "' width='" + bw.toFixed(1) + "' height='" + h.toFixed(1) + "' rx='2'>" +
        "<title>" + esc(opts.titles ? opts.titles[i] : values[i]) + "</title></rect>";
    }
    let labels = "";
    if (opts.labels) {
      for (const spec of opts.labels) {
        const x = spec[0] * (bw + gap) + bw / 2;
        labels += "<text class='axis-label' x='" + x.toFixed(1) + "' y='" + (H - 3) + "' text-anchor='middle'>" + esc(spec[1]) + "</text>";
      }
    }
    return "<svg class='chart-svg' viewBox='0 0 " + W + " " + H + "' preserveAspectRatio='none' role='img' aria-label='" + esc(opts.aria || "bar chart") + "'>" + bars + labels + "</svg>";
  }

  function trendChartSVG(values, labels) {
    const n = values.length;
    const W = 360, H = 64, padB = 14, padT = 6, padX = 4;
    const max = Math.max.apply(null, values.concat([1]));
    const stepX = (W - padX * 2) / Math.max(1, n - 1);
    const pts = [];
    for (let i = 0; i < n; i++) {
      const x = padX + i * stepX;
      const y = padT + (1 - values[i] / max) * (H - padB - padT);
      pts.push([x, y]);
    }
    const line = pts.map(function (p, i) { return (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    const area = line + " L" + pts[pts.length - 1][0].toFixed(1) + " " + (H - padB) + " L" + pts[0][0].toFixed(1) + " " + (H - padB) + " Z";
    const last = pts[pts.length - 1];
    let labelStr = "";
    if (labels) {
      labelStr = "<text class='axis-label' x='" + padX + "' y='" + (H - 3) + "'>" + esc(labels[0]) + "</text>" +
        "<text class='axis-label' x='" + (W - padX) + "' y='" + (H - 3) + "' text-anchor='end'>" + esc(labels[1]) + "</text>";
    }
    return "<svg class='chart-svg' viewBox='0 0 " + W + " " + H + "' preserveAspectRatio='none' role='img' aria-label='trend chart'>" +
      "<path class='trend-fill' d='" + area + "'/>" +
      "<path class='trend-line' d='" + line + "'/>" +
      "<circle class='trend-dot' cx='" + last[0].toFixed(1) + "' cy='" + last[1].toFixed(1) + "' r='3'/>" +
      labelStr + "</svg>";
  }

  function fmtHour(h) {
    if (h === 0) return "12a";
    if (h < 12) return h + "a";
    if (h === 12) return "12p";
    return (h - 12) + "p";
  }

  function renderCharts(rows) {
    const byHour = new Array(24).fill(0);
    const byDow = new Array(7).fill(0);
    let datedCount = 0;
    for (const row of rows) {
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr && pr.dt) {
        datedCount++;
        if (Number.isInteger(pr.hh) && pr.hh >= 0 && pr.hh < 24) byHour[pr.hh]++;
        byDow[pr.dt.getDay()]++;
      }
    }

    els.analyticsSummary.innerHTML = "Analyzing <b>" + rows.length.toLocaleString() + "</b> filtered incident" +
      (rows.length === 1 ? "" : "s") + (datedCount < rows.length ? " (" + datedCount.toLocaleString() + " with timestamps)" : "");

    // Hour chart
    const hourTitles = byHour.map(function (v, h) { return fmtHour(h) + "–" + fmtHour((h + 1) % 24) + ": " + v; });
    els.chartHour.innerHTML = barChartSVG(byHour, {
      labels: [[0, "12a"], [6, "6a"], [12, "12p"], [18, "6p"], [23, "11p"]],
      titles: hourTitles, aria: "Incidents by hour of day"
    });
    const peakH = byHour.indexOf(Math.max.apply(null, byHour));
    els.chartHourSub.textContent = byHour[peakH] > 0 ? ("peak " + fmtHour(peakH) + " · " + byHour[peakH]) : "";

    // Day-of-week chart
    const dowNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    els.chartDow.innerHTML = barChartSVG(byDow, {
      labels: dowNames.map(function (nm, i) { return [i, nm]; }),
      titles: dowNames.map(function (nm, i) { return nm + ": " + byDow[i]; }),
      gap: 6, aria: "Incidents by day of week"
    });
    const peakD = byDow.indexOf(Math.max.apply(null, byDow));
    els.chartDowSub.textContent = byDow[peakD] > 0 ? ("peak " + dowNames[peakD] + " · " + byDow[peakD]) : "";

    // 12-week trend
    const WEEKS = 12;
    const weekMs = 7 * 86400000;
    const nowMs = Date.now();
    const weekly = new Array(WEEKS).fill(0);
    for (const row of rows) {
      const dt = bestRowDate(row);
      if (!dt) continue;
      const age = nowMs - dt.getTime();
      if (age < 0 || age >= WEEKS * weekMs) continue;
      weekly[WEEKS - 1 - Math.floor(age / weekMs)]++;
    }
    els.chartTrend.innerHTML = trendChartSVG(weekly, [WEEKS + " wks ago", "now"]);
    const lastW = weekly[WEEKS - 1], prevW = weekly[WEEKS - 2];
    if (prevW > 0) {
      const pct = ((lastW - prevW) / prevW) * 100;
      const dir = pct >= 0 ? "up" : "down";
      els.chartTrendSub.innerHTML = "this week <span class='trend-delta " + dir + "'>" + (pct >= 0 ? "▲" : "▼") + " " + Math.abs(pct).toFixed(0) + "%</span>";
    } else {
      els.chartTrendSub.textContent = lastW > 0 ? (lastW + " this week") : "";
    }
  }

  /* ═══════════════════════ normalized rates (denominator-aware) ═══════════════════════ */
  let _dataSpanCache = null;

  function computeDataSpan() {
    if (!INCIDENTS.length) return null;
    let minMs = Infinity, maxMs = -Infinity;
    for (const row of INCIDENTS) {
      const pr = parseReported(row[IDX_REPORTED]);
      if (!pr || !pr.dt) continue;
      const ms = pr.dt.getTime();
      if (ms < minMs) minMs = ms;
      if (ms > maxMs) maxMs = ms;
    }
    if (!isFinite(minMs)) return null;

    let rushHours = 0, nonRushWeekdayHours = 0, schoolDayHours = 0, nonSchoolWeekdayHours = 0;
    const d = new Date(minMs);
    d.setHours(0, 0, 0, 0);
    const end = new Date(maxMs);
    end.setHours(23, 59, 59, 999);
    while (d <= end) {
      const dow = d.getDay();
      if (dow !== 0 && dow !== 6) {
        rushHours += 5;
        nonRushWeekdayHours += 19;
        if (isSchoolDayJS(d)) schoolDayHours += 24; else nonSchoolWeekdayHours += 24;
      }
      d.setDate(d.getDate() + 1);
    }
    return { rushHours: rushHours, nonRushWeekdayHours: nonRushWeekdayHours, schoolDayHours: schoolDayHours, nonSchoolWeekdayHours: nonSchoolWeekdayHours };
  }

  function _getDataSpan() {
    if (!_dataSpanCache) _dataSpanCache = computeDataSpan();
    return _dataSpanCache;
  }

  function _isRushHourRow(row) {
    const h = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    const dow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (h !== null && dow !== null) {
      if (dow >= 5) return false;
      return (h >= 7 && h < 9) || (h >= 16 && h < 19);
    }
    return isRushHourFromParsed(parseReported(row[IDX_REPORTED]));
  }

  function _isSchoolDayRow(row) {
    const val = (row.length > IDX_SCHOOL_DAY) ? row[IDX_SCHOOL_DAY] : null;
    if (val === 1) return true;
    if (val === 0) return false;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return isSchoolDayJS(pr.dt);
  }

  function _isWeekendRow(row) {
    const dow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (dow !== null) return dow >= 5;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    const d = pr.dt.getDay();
    return d === 0 || d === 6;
  }

  function renderRatesPanel(rows) {
    const el = els.ratesContent;
    const span = _getDataSpan();
    if (!span || span.rushHours === 0) {
      el.innerHTML = '<span class="rates-no-data">Not enough data to compute rates.</span>';
      return;
    }

    let rushCount = 0, nonRushCount = 0, schoolCount = 0, nonSchoolCount = 0;
    let rainCount = 0, noRainCount = 0, windyCount = 0, calmCount = 0, lowVisCount = 0, goodVisCount = 0;

    for (const row of rows) {
      if (!_isWeekendRow(row)) {
        if (_isRushHourRow(row)) rushCount++; else nonRushCount++;
        if (_isSchoolDayRow(row)) schoolCount++; else nonSchoolCount++;
      }
      const precipIn = weatherNumber(row, IDX_PRECIP_IN);
      const precipProb = weatherNumber(row, IDX_PRECIP_PROB);
      const wind = weatherNumber(row, IDX_WIND_SPEED);
      const vis = weatherNumber(row, IDX_VISIBILITY);
      if (precipIn != null || precipProb != null || wind != null || vis != null) {
        const hasRain = (precipIn != null && precipIn > 0.005) || (precipProb != null && precipProb >= 20);
        if (hasRain) rainCount++; else noRainCount++;
        if (wind != null) { if (wind >= 20) windyCount++; else calmCount++; }
        if (vis != null) { if (vis < 5) lowVisCount++; else goodVisCount++; }
      }
    }

    function barRow(label, count, denominator, maxRate, colorVar, isRate) {
      const rate = denominator > 0 ? count / denominator : 0;
      const pct = maxRate > 0 ? Math.min(100, (rate / maxRate) * 100) : 0;
      let valText;
      if (isRate) {
        if (denominator <= 0 || count === 0) {
          valText = '<span class="rates-n">0</span>';
        } else {
          const rStr = rate >= 0.1 ? rate.toFixed(2) + "/hr" : (rate * 1000).toFixed(1) + "/1000 hr";
          valText = rStr + '<span class="rates-n"> (' + count + ')</span>';
        }
      } else {
        const p = denominator > 0 ? ((count / denominator) * 100).toFixed(1) + "%" : "—";
        valText = p + '<span class="rates-n"> (' + count + ')</span>';
      }
      return '<div class="rates-row">' +
        '<span class="rates-label">' + esc(label) + '</span>' +
        '<div class="rates-bar-wrap"><div class="rates-bar-fill" style="width:' + pct.toFixed(1) + '%;background:var(' + colorVar + ')"></div></div>' +
        '<span class="rates-val">' + valText + '</span></div>';
    }

    function relRisk(countA, hoursA, countB, hoursB, labelA, labelB) {
      if (hoursA <= 0 || hoursB <= 0) return "";
      const rA = countA / hoursA, rB = countB / hoursB;
      if (rA === 0 && rB === 0) return "";
      if (rB === 0) return labelA + " has incidents but " + labelB + " does not.";
      const ratio = rA / rB;
      if (Math.abs(ratio - 1) < 0.05) return labelA + " and " + labelB + " are about equal per hour.";
      if (ratio > 1) return labelA + " is <strong>" + ratio.toFixed(1) + "&times;</strong> more frequent per hour than " + labelB + ".";
      return labelB + " is <strong>" + (1 / ratio).toFixed(1) + "&times;</strong> more frequent per hour than " + labelA + ".";
    }

    const maxRR = Math.max(rushCount / span.rushHours, nonRushCount / span.nonRushWeekdayHours) || 1e-9;
    const rushHTML =
      barRow("Rush hour", rushCount, span.rushHours, maxRR, "--bad", true) +
      barRow("Off-peak", nonRushCount, span.nonRushWeekdayHours, maxRR, "--accent", true);
    const rushRatio = relRisk(rushCount, span.rushHours, nonRushCount, span.nonRushWeekdayHours, "Rush hour", "Off-peak");

    const maxSR = Math.max(schoolCount / span.schoolDayHours, nonSchoolCount / span.nonSchoolWeekdayHours) || 1e-9;
    const schoolHTML =
      barRow("School day", schoolCount, span.schoolDayHours, maxSR, "--bad", true) +
      barRow("No school", nonSchoolCount, span.nonSchoolWeekdayHours, maxSR, "--accent", true);
    const schoolRatio = relRisk(schoolCount, span.schoolDayHours, nonSchoolCount, span.nonSchoolWeekdayHours, "School days", "No-school days");

    const wTotal = rainCount + noRainCount;
    let weatherHTML = "";
    if (wTotal === 0) {
      weatherHTML = '<div class="rates-row"><span class="rates-no-data">No weather data in this selection.</span></div>';
    } else {
      const maxW = Math.max(rainCount, noRainCount) || 1;
      weatherHTML +=
        barRow("Rain / precip", rainCount, wTotal, maxW, "--accent", false) +
        barRow("No rain", noRainCount, wTotal, maxW, "--good", false);
      if (windyCount + calmCount > 0) {
        const wndT = windyCount + calmCount;
        const maxWnd = Math.max(windyCount, calmCount) || 1;
        weatherHTML +=
          barRow("Windy (≥20 mph)", windyCount, wndT, maxWnd, "--warn", false) +
          barRow("Calm wind", calmCount, wndT, maxWnd, "--good", false);
      }
      if (lowVisCount + goodVisCount > 0) {
        const visT = lowVisCount + goodVisCount;
        const maxVis = Math.max(lowVisCount, goodVisCount) || 1;
        weatherHTML +=
          barRow("Low vis (<5 mi)", lowVisCount, visT, maxVis, "--warn", false) +
          barRow("Good vis", goodVisCount, visT, maxVis, "--good", false);
      }
    }

    el.innerHTML =
      '<div class="rates-subtitle">From ' + rows.length.toLocaleString() + ' filtered incident' + (rows.length !== 1 ? 's' : '') + ' — denominators are hours in the dataset’s date range</div>' +
      '<div class="rates-group"><div class="rates-group-title">Rush hour vs. off-peak ' +
      '<span class="rates-span-note">(' + span.rushHours.toLocaleString() + ' rush hrs · ' + span.nonRushWeekdayHours.toLocaleString() + ' off-peak hrs)</span></div>' +
      rushHTML + (rushRatio ? '<div class="rates-ratio">' + rushRatio + '</div>' : '') + '</div>' +
      '<div class="rates-group"><div class="rates-group-title">School day vs. no school ' +
      '<span class="rates-span-note">(' + span.schoolDayHours.toLocaleString() + ' school hrs · ' + span.nonSchoolWeekdayHours.toLocaleString() + ' no-school hrs)</span></div>' +
      schoolHTML + (schoolRatio ? '<div class="rates-ratio">' + schoolRatio + '</div>' : '') + '</div>' +
      '<div class="rates-group"><div class="rates-group-title">Weather breakdown ' +
      '<span class="rates-span-note">% of incidents with weather data</span></div>' + weatherHTML + '</div>';
  }

  /* ═══════════════════════ smart insights ═══════════════════════ */
  function inferCorridor(location) {
    const raw = String(location || "").trim().toUpperCase();
    if (!raw) return "Unknown location";
    const primary = raw.split(/\s+(?:AT|&|\/|NEAR|@)\s+/)[0].trim();
    if (!primary) return "Unknown location";
    return primary.replace(/\s+/g, " ");
  }

  function renderInsightsPanel(rows) {
    const el = els.insightsContent;
    if (!rows || rows.length === 0) {
      el.innerHTML = '<span class="rates-no-data">No incidents match the current filters.</span>';
      return;
    }

    const byHour = new Array(24).fill(0);
    const byDow = new Array(7).fill(0);
    const byRoadType = new Map();
    const byCorridor = new Map();
    const dated = [];

    for (const row of rows) {
      const parsed = parseReported(row[IDX_REPORTED]);
      if (parsed && parsed.dt) {
        if (Number.isInteger(parsed.hh) && parsed.hh >= 0 && parsed.hh < 24) byHour[parsed.hh]++;
        byDow[parsed.dt.getDay()]++;
        dated.push(parsed.dt);
      }
      const roadType = String(row[IDX_HIGHWAY] || "unknown").trim().toLowerCase() || "unknown";
      byRoadType.set(roadType, (byRoadType.get(roadType) || 0) + 1);
      const corridor = inferCorridor(row[IDX_LOCATION]);
      byCorridor.set(corridor, (byCorridor.get(corridor) || 0) + 1);
    }

    const bestHour = byHour.reduce(function (best, count, hour) { return count > best.count ? { hour: hour, count: count } : best; }, { hour: -1, count: 0 });
    const dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    const bestDow = byDow.reduce(function (best, count, jsDow) { return count > best.count ? { jsDow: jsDow, count: count } : best; }, { jsDow: -1, count: 0 });
    const topRoadType = Array.from(byRoadType.entries()).sort(function (a, b) { return b[1] - a[1]; })[0] || ["unknown", 0];
    const topCorridor = Array.from(byCorridor.entries()).sort(function (a, b) { return b[1] - a[1]; })[0] || ["Unknown location", 0];

    let trendText = "Not enough date coverage for a 7-day trend.";
    if (dated.length >= 4) {
      dated.sort(function (a, b) { return a - b; });
      const end = dated[dated.length - 1];
      const recentStart = new Date(end.getTime() - 7 * 86400000);
      const priorStart = new Date(end.getTime() - 14 * 86400000);
      let recent = 0, prior = 0;
      for (const dt of dated) {
        if (dt > recentStart) recent++;
        else if (dt > priorStart) prior++;
      }
      if (prior === 0 && recent > 0) {
        trendText = "Recent 7 days show activity, but the prior 7-day window had none.";
      } else if (prior > 0) {
        const pct = ((recent - prior) / prior) * 100;
        trendText = "Recent 7 days are <strong>" + (pct >= 0 ? "up" : "down") + " " + Math.abs(pct).toFixed(0) + "%</strong> vs the prior 7-day window.";
      }
    }

    const hourText = bestHour.hour >= 0
      ? (String(bestHour.hour).padStart(2, "0") + ":00–" + String((bestHour.hour + 1) % 24).padStart(2, "0") + ":00")
      : "Unknown";
    const roadTypeLabel = topRoadType[0] === "unknown" ? "Unknown / uncategorized" : topRoadType[0].replace(/_/g, " ");
    const dayLabel = bestDow.jsDow >= 0 ? dayNames[bestDow.jsDow] : "Unknown day";

    el.innerHTML =
      '<ul class="insight-list">' +
      '<li>Peak hour: <strong>' + esc(hourText) + '</strong> (' + bestHour.count + ' incidents).</li>' +
      '<li>Busiest day: <strong>' + esc(dayLabel) + '</strong> (' + bestDow.count + ' incidents).</li>' +
      '<li>Most common road type: <strong>' + esc(roadTypeLabel) + '</strong> (' + topRoadType[1] + ').</li>' +
      '<li>Top corridor: <strong>' + esc(titleCase(topCorridor[0])) + '</strong> (' + topCorridor[1] + ' incidents).</li>' +
      '<li>' + trendText + '</li></ul>';
  }

  /* ═══════════════════════ feed ═══════════════════════ */
  let feedLimit = 30;

  function renderFeed(rows) {
    const nowMs = Date.now();
    const sorted = rows.slice().sort(function (a, b) {
      const da = bestRowDate(a), db = bestRowDate(b);
      return (db ? db.getTime() : 0) - (da ? da.getTime() : 0);
    });
    const shown = sorted.slice(0, feedLimit);

    els.feedMeta.textContent = rows.length
      ? "Most recent " + shown.length.toLocaleString() + " of " + rows.length.toLocaleString() + " filtered incidents — tap to fly to it"
      : "";

    if (!rows.length) {
      els.feedList.innerHTML = "<div class='feed-empty'>No incidents match the current filters.</div>";
      return;
    }

    const frag = document.createDocumentFragment();
    for (const row of shown) {
      const cat = categoryOf(row[IDX_CAUSE]);
      const dt = bestRowDate(row);
      const item = document.createElement("div");
      item.className = "feed-item";
      item.style.setProperty("--cat", cat.color);
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      const occurrences = (row.length > IDX_COUNT && row[IDX_COUNT] != null) ? parseInt(row[IDX_COUNT], 10) : 1;
      item.innerHTML =
        "<span class='feed-dot'></span>" +
        "<span class='feed-body'>" +
        "<span class='feed-loc'>" + esc(titleCase(row[IDX_LOCATION])) + "</span>" +
        "<span class='feed-sub'><span class='cat-name'>" + esc(String(row[IDX_CAUSE] || cat.label).trim() || cat.label) + "</span>" +
        (occurrences > 1 ? "<span>× " + occurrences + "</span>" : "") +
        (dt ? "<span>" + esc(relTime(dt, nowMs)) + "</span>" : "") +
        "</span></span>";
      function activate() {
        if (!map) return;
        const latlng = L.latLng(row[IDX_LAT], row[IDX_LNG]);
        const targetZoom = Math.max(map.getZoom(), 15);
        let opened = false;
        function openIt() {
          if (opened) return;
          opened = true;
          L.popup({ maxWidth: 320, autoPan: false })
            .setLatLng(latlng)
            .setContent(createIncidentPopup([row]))
            .openOn(map);
        }
        map.once("moveend", openIt);
        // If the map is already at the target view, no move event fires.
        setTimeout(openIt, REDUCED_MOTION ? 150 : 800);
        if (REDUCED_MOTION) map.setView(latlng, targetZoom); else map.flyTo(latlng, targetZoom, { duration: 0.6 });
        if (window.innerWidth <= 700) setSheetExpanded(false);
      }
      item.addEventListener("click", activate);
      item.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
      frag.appendChild(item);
    }
    els.feedList.innerHTML = "";
    els.feedList.appendChild(frag);

    if (sorted.length > feedLimit) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "feed-more";
      more.textContent = "Show " + Math.min(30, sorted.length - feedLimit) + " more";
      more.addEventListener("click", function () {
        feedLimit += 30;
        renderFeed(lastFiltered);
      });
      els.feedList.appendChild(more);
    }
  }

  /* ═══════════════════════ marker layers ═══════════════════════ */
  const layers = { points: null, heat: null, intersections: null, osmIntersections: null, micro: null, rings: null, hotSpots: null };
  let lastFiltered = [];
  let renderTimer = null;
  let pointRenderMode = "exact";
  let pointSymbolCount = 0;
  let pointMarkers = { singles: [], counts: [] };

  function groupByRounded(points, decimals) {
    const m = new Map();
    for (const row of points) {
      const key = row[IDX_LAT].toFixed(decimals) + "," + row[IDX_LNG].toFixed(decimals);
      let v = m.get(key);
      if (!v) {
        v = { key: key, lat: parseFloat(row[IDX_LAT].toFixed(decimals)), lng: parseFloat(row[IDX_LNG].toFixed(decimals)), count: 0, sample: row };
        m.set(key, v);
      }
      v.count += 1;
    }
    return Array.from(m.values()).sort(function (a, b) { return b.count - a.count; });
  }

  function groupByExactLocation(points) {
    const m = new Map();
    for (const row of points) {
      const key = row[IDX_LAT].toFixed(6) + "," + row[IDX_LNG].toFixed(6);
      let v = m.get(key);
      if (!v) {
        v = { key: key, lat: row[IDX_LAT], lng: row[IDX_LNG], rows: [] };
        m.set(key, v);
      }
      v.rows.push(row);
    }
    return Array.from(m.values());
  }

  function getCenterFromData() {
    if (INCIDENTS.length === 0) return [__CENTER_LAT__, __CENTER_LNG__];
    let sLat = 0, sLng = 0;
    for (const r of INCIDENTS) { sLat += r[IDX_LAT]; sLng += r[IDX_LNG]; }
    return [sLat / INCIDENTS.length, sLng / INCIDENTS.length];
  }

  function getPointSizing(zoomOverride) {
    const zoom = Number.isFinite(zoomOverride) ? zoomOverride : (map && map.getZoom ? map.getZoom() : 12);
    const inverse = 12 - zoom;
    let radius = 5.6 + inverse * 0.7;
    radius = Math.max(3.6, Math.min(10.8, radius));
    if (isTouch) radius = Math.min(13.5, radius * 1.35 + 1.2);
    const countSize = Math.max(16, Math.round(radius * 2.05 + (isTouch ? 5 : 3)));
    const countFont = Math.max(10, Math.round(countSize * 0.55));
    return { radius: radius, countSize: countSize, countFont: countFont };
  }

  function countIconHtml(count, size, fontSize, color) {
    return "<div class='incident-count-marker' style='--cat:" + color + ";width:" + size + "px;height:" + size + "px;font-size:" + fontSize + "px;'>" + count + "</div>";
  }

  function updatePointSizing(zoomOverride) {
    if (!map) return;
    if (pointRenderMode !== "exact" || pointSymbolCount > 1800) return;
    const sizing = getPointSizing(zoomOverride);
    for (const mk of pointMarkers.singles) {
      if (mk && mk.setRadius) mk.setRadius(sizing.radius);
    }
    for (const mk of pointMarkers.counts) {
      if (!mk || !mk.setIcon) continue;
      const icon = L.divIcon({
        className: "",
        html: countIconHtml(mk.__count || 1, sizing.countSize, sizing.countFont, mk.__catColor || "#2563eb"),
        iconSize: [sizing.countSize, sizing.countSize],
        iconAnchor: [sizing.countSize / 2, sizing.countSize / 2]
      });
      mk.setIcon(icon);
    }
  }

  function focusMarker(marker) {
    if (!map || !marker || !marker.getLatLng) return;
    const latlng = marker.getLatLng();
    const currentZoom = map.getZoom ? map.getZoom() : 12;
    const targetZoom = Math.max(currentZoom, 15);
    if (currentZoom >= targetZoom) {
      if (marker.openPopup) marker.openPopup();
      map.panTo(latlng, { animate: !REDUCED_MOTION, duration: 0.25 });
    } else {
      map.once("moveend", function () { if (marker.openPopup) marker.openPopup(); });
      map.setView(latlng, targetZoom, { animate: !REDUCED_MOTION, duration: 0.35 });
    }
  }

  function clearLayers() {
    if (!map) return;
    for (const k of Object.keys(layers)) {
      if (layers[k]) {
        try { map.removeLayer(layers[k]); } catch (e) {}
        layers[k] = null;
      }
    }
  }

  function dominantCategory(rows) {
    const counts = {};
    let best = null, bestN = 0;
    for (const row of rows) {
      const cat = categoryOf(row[IDX_CAUSE]);
      const n = (counts[cat.id] || 0) + 1;
      counts[cat.id] = n;
      if (n > bestN) { bestN = n; best = cat; }
    }
    return best || CATEGORIES[CATEGORIES.length - 1];
  }

  function drawLayers(filtered) {
    if (!map) return;
    clearLayers();
    pointMarkers = { singles: [], counts: [] };

    const showPoints = !!els.chkPoints.checked;
    const showHeat = !!els.chkHeat.checked;
    const showInter = !!els.chkIntersections.checked;
    const showOsmInter = !!els.chkOsmIntersections.checked;
    const showMicro = !!els.chkMicro.checked;
    const showRings = !!els.chkRings.checked;
    const showHotSpots = !!els.chkHotSpots.checked;

    const topN = parseInt(els.topNSelect.value || "10", 10);
    const dInter = parseInt(els.precIntersections.value || "3", 10);
    const dMicro = parseInt(els.precMicro.value || "4", 10);

    if (showPoints) {
      // Performance mode for very large result sets: restrict to viewport
      // (+padding) and aggregate by rounded coordinates.
      const usePerfMode = filtered.length > 2000;
      pointRenderMode = usePerfMode ? "aggregated" : "exact";
      const zoom = map.getZoom ? map.getZoom() : 12;
      let pointRows = filtered;
      if (usePerfMode && map.getBounds) {
        const bounds = map.getBounds().pad(0.35);
        pointRows = filtered.filter(function (row) {
          return bounds.contains(L.latLng(row[IDX_LAT], row[IDX_LNG]));
        });
      }
      const grouped = usePerfMode
        ? groupByRounded(pointRows, zoom >= 14 ? 4 : 3)
        : groupByExactLocation(pointRows);
      const sizing = getPointSizing();
      const mkList = [];
      for (const group of grouped) {
        let mk = null;
        const groupCount = usePerfMode ? group.count : group.rows.length;
        if (groupCount > 1) {
          if (usePerfMode) {
            // Canvas circles scale far better than divIcons for thousands of symbols.
            const cat = categoryOf(group.sample[IDX_CAUSE]);
            const radius = Math.max(4, Math.min(12, 3 + Math.sqrt(groupCount)));
            mk = L.circleMarker([group.lat, group.lng], {
              radius: radius, renderer: renderer,
              color: cat.color, weight: isTouch ? 2.4 : 1.8,
              fillColor: cat.fill, fillOpacity: 0.72
            });
            mk.bindPopup(
              "<b>" + groupCount + " nearby incidents</b><br><br>" + popupHtml(group.sample),
              { maxWidth: 340, autoPan: false }
            );
          } else {
            const cat = dominantCategory(group.rows);
            const icon = L.divIcon({
              className: "",
              html: countIconHtml(groupCount, sizing.countSize, sizing.countFont, cat.color),
              iconSize: [sizing.countSize, sizing.countSize],
              iconAnchor: [sizing.countSize / 2, sizing.countSize / 2]
            });
            mk = L.marker([group.lat, group.lng], { icon: icon, riseOnHover: true });
            mk.__count = groupCount;
            mk.__catColor = cat.color;
            pointMarkers.counts.push(mk);
            (function (rows) {
              mk.bindPopup(function () { return createIncidentPopup(rows); }, { maxWidth: 320, autoPan: false });
            })(group.rows);
          }
          (function (m) { mk.on("click", function () { focusMarker(m); }); })(mk);
        } else {
          const singleRow = usePerfMode ? group.sample : group.rows[0];
          const cat = categoryOf(singleRow[IDX_CAUSE]);
          mk = L.circleMarker([group.lat, group.lng], {
            radius: sizing.radius, renderer: renderer,
            color: cat.color, weight: isTouch ? 2.2 : 1.4,
            fillColor: cat.fill, fillOpacity: 0.6
          });
          pointMarkers.singles.push(mk);
          (function (row) {
            mk.bindPopup(function () { return popupHtml(row); }, { maxWidth: 320, autoPan: false });
          })(singleRow);
          (function (m) { mk.on("click", function () { focusMarker(m); }); })(mk);
        }
        mkList.push(mk);
      }
      pointSymbolCount = mkList.length;
      layers.points = L.layerGroup(mkList).addTo(map);
    } else {
      pointRenderMode = "exact";
      pointSymbolCount = 0;
    }

    if (showHeat && typeof L.heatLayer === "function") {
      const heatPts = filtered.map(function (r) {
        return [r[IDX_LAT], r[IDX_LNG], (r.length > IDX_WEIGHT ? (parseFloat(r[IDX_WEIGHT]) || 1.0) : 1.0)];
      });
      layers.heat = L.heatLayer(heatPts, { radius: 18, blur: 14, maxZoom: 17 }).addTo(map);
    }

    if (showInter) {
      const groups = groupByRounded(filtered, dInter).slice(0, topN);
      const layer = L.layerGroup().addTo(map);
      for (const g of groups) {
        const radius = Math.max(7, Math.min(30, 4 + Math.sqrt(g.count) * 3));
        const c = L.circleMarker([g.lat, g.lng], { radius: radius, renderer: renderer, color: "#2563eb", fillColor: "#93c5fd", fillOpacity: 0.4, weight: 1.5 });
        c.bindPopup("<b>Rounded cluster</b><br>Count: " + g.count + "<br><br>" + popupHtml(g.sample), { maxWidth: 340 });
        c.addTo(layer);
      }
      layers.intersections = layer;
    }

    if (showOsmInter) {
      const layer = L.layerGroup().addTo(map);
      if (!OSM_INTERSECTIONS || OSM_INTERSECTIONS.length === 0) {
        const center = getCenterFromData();
        const mk = L.circleMarker(center, { radius: 8, renderer: renderer });
        mk.bindPopup("OSM intersection data unavailable.<br>Install osmnx server-side and rebuild once.");
        mk.addTo(layer);
      } else {
        const top = OSM_INTERSECTIONS.slice(0, topN);
        for (const it of top) {
          const radius = Math.max(8, Math.min(34, 4 + Math.sqrt(it[2]) * 3));
          const c = L.circleMarker([it[0], it[1]], { radius: radius, renderer: renderer, color: "#0d9488", fillColor: "#5eead4", fillOpacity: 0.4, weight: 1.5 });
          c.bindPopup("<b>OSM intersection hotspot</b><br>Count: " + it[2] + "<br>Node: " + esc(it[3]), { maxWidth: 320 });
          c.addTo(layer);
        }
      }
      layers.osmIntersections = layer;
    }

    if (showMicro) {
      const groups = groupByRounded(filtered, dMicro).slice(0, topN);
      const layer = L.layerGroup().addTo(map);
      for (const g of groups) {
        const radius = Math.max(6, Math.min(26, 3 + Math.sqrt(g.count) * 2.5));
        const c = L.circleMarker([g.lat, g.lng], { radius: radius, renderer: renderer, color: "#9333ea", fillColor: "#d8b4fe", fillOpacity: 0.4, weight: 1.5 });
        c.bindPopup("<b>Micro-hotspot</b><br>Count: " + g.count + "<br><br>" + popupHtml(g.sample), { maxWidth: 340 });
        c.addTo(layer);
      }
      layers.micro = layer;
    }

    if (showRings) {
      const center = getCenterFromData();
      const ringLayer = L.layerGroup().addTo(map);
      for (const rk of [1, 2, 3, 5, 8]) {
        L.circle(center, { radius: rk * 1000, weight: 1, fill: false, color: "#64748b", dashArray: "4 5" }).addTo(ringLayer);
      }
      const centerMarker = L.circleMarker(center, { radius: 7, renderer: renderer, color: "#64748b" });
      centerMarker.bindPopup("Dataset center<br>" + center[0].toFixed(5) + ", " + center[1].toFixed(5));
      centerMarker.addTo(ringLayer);
      layers.rings = ringLayer;
    }

    if (showHotSpots && HOT_SPOTS.length > 0) {
      const hsLayer = L.layerGroup().addTo(map);
      const maxScore = HOT_SPOTS[0][3] || 1;
      const sliced = HOT_SPOTS.slice(0, topN);
      for (const hs of sliced) {
        const t = Math.max(0, Math.min(1, hs[3] / maxScore));
        const r = Math.max(10, Math.min(40, 8 + Math.sqrt(hs[3]) * 4));
        const red = Math.round(255 * t), blue = Math.round(255 * (1 - t));
        const fillColor = "rgb(" + red + ",60," + blue + ")";
        const c = L.circleMarker([hs[0], hs[1]], {
          radius: r, renderer: renderer, color: fillColor, weight: 2, fillColor: fillColor, fillOpacity: 0.35
        });
        c.bindPopup(
          "<b>Hot spot</b><br>" + esc(titleCase(hs[4] || "Unknown location")) +
          "<br>Incidents: " + hs[2] + "<br>Recency score: " + hs[3].toFixed(2) +
          "<br><span style='color:var(--text-3);font-size:11px;'>Recent incidents weigh more.</span>",
          { maxWidth: 320 }
        );
        c.addTo(hsLayer);
      }
      layers.hotSpots = hsLayer;
    }
  }

  /* ═══════════════════════ render pipeline ═══════════════════════ */
  function setCount(el, value) {
    const next = Number(value).toLocaleString();
    if (el.textContent !== next) el.textContent = next;
  }

  function renderAll() {
    const f = currentFilterObj();
    const filtered = filteredIncidents(f, map);
    lastFiltered = filtered;
    feedLimit = 30;

    setCount(els.countTotal, INCIDENTS.length);
    setCount(els.countFiltered, filtered.length);
    setCount(els.countInView, map ? computeInViewCount(filtered, map) : 0);

    const nActive = countActiveFilters(f);
    els.filterBadge.textContent = String(nActive);
    els.filterBadge.classList.toggle("show", nActive > 0);

    drawLayers(filtered);
    renderCharts(filtered);
    renderRatesPanel(filtered);
    renderInsightsPanel(filtered);
    renderFeed(filtered);
    updateHash();
  }

  function scheduleRender(delayMs) {
    const delay = Number.isFinite(delayMs) ? delayMs : 0;
    if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
    renderTimer = setTimeout(function () {
      renderTimer = null;
      requestAnimationFrame(renderAll);
    }, delay);
  }

  function updateInViewOnly() {
    if (!map) return;
    const f = currentFilterObj();
    let rows = lastFiltered;
    if (f.inViewOnly) {
      rows = filteredIncidents(f, map);
      lastFiltered = rows;
      setCount(els.countFiltered, rows.length);
    }
    setCount(els.countInView, computeInViewCount(rows, map));
  }

  /* ═══════════════════════ quick range chips ═══════════════════════ */
  function setRange(range, silent) {
    state.range = range || "";
    const chips = els.rangeChips.querySelectorAll(".range-chip");
    chips.forEach(function (chip) {
      chip.classList.toggle("active", (chip.getAttribute("data-range") || "") === state.range);
    });
    if (!silent) scheduleRender(0);
  }

  /* ═══════════════════════ dropdown population ═══════════════════════ */
  function buildCauseDropdown() {
    const set = new Set();
    for (const r of INCIDENTS) {
      const c = String(r[IDX_CAUSE] || "").trim();
      if (c) set.add(c);
    }
    const causes = Array.from(set.values()).sort(function (a, b) { return a.localeCompare(b); });
    const prev = els.causeSelect.value;
    while (els.causeSelect.options.length > 1) els.causeSelect.remove(1);
    for (const c of causes) {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = titleCase(c);
      els.causeSelect.appendChild(opt);
    }
    if (prev && causes.indexOf(prev) !== -1) els.causeSelect.value = prev;
  }

  function buildCauseGroupDropdown() {
    while (els.causeGroupSelect.options.length > 1) els.causeGroupSelect.remove(1);
    for (const g of CAUSE_GROUPS) {
      const opt = document.createElement("option");
      opt.value = g.id;
      opt.textContent = g.label;
      els.causeGroupSelect.appendChild(opt);
    }
  }

  /* ═══════════════════════ reset ═══════════════════════ */
  function clearAll() {
    els.causeSelect.value = "__ALL__";
    els.causeGroupSelect.value = "__ALL__";
    els.chkInViewOnly.checked = false;
    els.monthSelect.value = "";
    els.daySelect.value = "";
    els.yearSelect.value = "";
    els.dayTypeSelect.value = "all";
    els.timeBlockSelect.value = "all";
    els.chkWeatherOnly.checked = false;
    els.tempBand.value = "any";
    els.precipBand.value = "any";
    els.precipAmountBand.value = "any";
    els.windBand.value = "any";
    els.visBand.value = "any";
    els.cloudBand.value = "any";
    els.chkRushHour.checked = false;
    els.chkSchoolDay.checked = false;
    els.dowSelect.value = "all";
    els.roadTypeSelect.value = "any";
    els.roadSearch.value = "";
    els.roadSearchClear.classList.remove("show");
    els.chkFloodWarning.checked = false;
    els.chkThunderstormWarning.checked = false;
    els.chkTornadoWatch.checked = false;
    els.chkPoints.checked = true;
    els.chkHeat.checked = false;
    els.chkIntersections.checked = false;
    els.chkOsmIntersections.checked = false;
    els.chkMicro.checked = false;
    els.chkRings.checked = false;
    els.chkHotSpots.checked = false;
    els.topNSelect.value = "10";
    els.precIntersections.value = "3";
    els.precMicro.value = "4";
    state.cats = new Set(CATEGORIES.map(function (c) { return c.id; }));
    setRange("", true);
    renderLegend();
  }

  /* ═══════════════════════ live weather (NWS, free + CORS) ═══════════════════════ */
  function wxIconFor(skyCover, precipIn, windMph) {
    if (precipIn != null && precipIn > 0.005) return "🌧";
    if (windMph != null && windMph >= 20) return "💨";
    if (skyCover != null) {
      if (skyCover >= 70) return "☁️";
      if (skyCover >= 25) return "⛅";
      return "☀️";
    }
    return "⛅";
  }

  function latestWeatherRow() {
    let best = null, bestTs = null;
    for (const row of INCIDENTS) {
      if (!hasWeatherData(row)) continue;
      const raw = normalizeText(row.length > IDX_WEATHER_AT ? row[IDX_WEATHER_AT] : "");
      const dt = raw ? new Date(raw) : null;
      const ts = dt && !Number.isNaN(dt.getTime()) ? dt.getTime() : null;
      if (!best || (ts != null && (bestTs == null || ts > bestTs))) {
        best = row;
        bestTs = ts;
      }
    }
    return best;
  }

  function setWeatherUI(parts, grid, metaHtml) {
    if (parts.tempF != null) {
      els.wxMain.textContent = parts.tempF.toFixed(0) + "°" + (parts.windMph != null ? " · " + parts.windMph.toFixed(0) + " mph" : "");
    } else if (parts.windMph != null) {
      els.wxMain.textContent = parts.windMph.toFixed(0) + " mph wind";
    } else {
      els.wxMain.textContent = "Weather n/a";
    }
    els.wxIcon.textContent = wxIconFor(parts.skyCover, parts.precipIn, parts.windMph);
    let gridHtml = "<div class='wp-grid'>";
    for (const g of grid) gridHtml += "<div>" + esc(g[0]) + " <b>" + esc(g[1]) + "</b></div>";
    gridHtml += "</div>";
    els.weatherPanelBody.innerHTML = gridHtml + (metaHtml || "");
  }

  function showStoredWeather() {
    const row = latestWeatherRow();
    if (!row) {
      els.wxMain.textContent = "Weather…";
      els.weatherPanelBody.textContent = "No weather observations yet.";
      return;
    }
    const parts = {
      tempF: weatherNumber(row, IDX_TEMP_F),
      windMph: weatherNumber(row, IDX_WIND_SPEED),
      precipIn: weatherNumber(row, IDX_PRECIP_IN),
      skyCover: weatherNumber(row, IDX_SKY_COVER)
    };
    const grid = [];
    if (parts.tempF != null) grid.push(["Temp", parts.tempF.toFixed(0) + "°F"]);
    const pop = weatherNumber(row, IDX_PRECIP_PROB);
    if (pop != null) grid.push(["Precip", pop.toFixed(0) + "%"]);
    if (parts.windMph != null) grid.push(["Wind", parts.windMph.toFixed(0) + " mph"]);
    const vis = weatherNumber(row, IDX_VISIBILITY);
    if (vis != null) grid.push(["Visibility", vis.toFixed(1) + " mi"]);
    if (parts.skyCover != null) grid.push(["Clouds", parts.skyCover.toFixed(0) + "%"]);
    const wAt = formatCentralTime(row.length > IDX_WEATHER_AT ? row[IDX_WEATHER_AT] : "");
    setWeatherUI(parts, grid, "<div class='wp-meta'>Stored snapshot" + (wAt ? " · observed " + esc(wAt) : "") + "</div>");
  }

  function fetchLiveNWSWeather() {
    fetch("https://api.weather.gov/stations/KLFT/observations/latest", { headers: { "Accept": "application/geo+json" } })
      .then(function (resp) { return resp.ok ? resp.json() : Promise.reject(resp.status); })
      .then(function (data) {
        const props = data && data.properties;
        if (!props) return;
        function toF(c) { return (c != null && !isNaN(c)) ? (c * 9 / 5) + 32 : null; }
        function toMph(kmh) { return (kmh != null && !isNaN(kmh)) ? kmh / 1.609344 : null; }
        function toMi(m) { return (m != null && !isNaN(m)) ? m / 1609.344 : null; }
        function toIn(mm) { return (mm != null && !isNaN(mm)) ? mm / 25.4 : null; }

        const tempF = toF(props.temperature && props.temperature.value);
        const windMph = toMph(props.windSpeed && props.windSpeed.value);
        const gustMph = toMph(props.windGust && props.windGust.value);
        const visMi = toMi(props.visibility && props.visibility.value);
        const precipIn = toIn(props.precipitationLastHour && props.precipitationLastHour.value);
        const humidity = props.relativeHumidity && props.relativeHumidity.value;

        let skyCover = null;
        const coverMap = { CLR: 0, SKC: 0, FEW: 15, SCT: 38, BKN: 75, OVC: 100 };
        const cloudLayers = props.cloudLayers || [];
        for (const cl of cloudLayers) {
          const pct = coverMap[cl.amount];
          if (pct != null && (skyCover == null || pct > skyCover)) skyCover = pct;
        }

        if (tempF == null && windMph == null && visMi == null) return;

        const grid = [];
        if (tempF != null) grid.push(["Temp", tempF.toFixed(0) + "°F"]);
        if (humidity != null && !isNaN(humidity)) grid.push(["Humidity", Number(humidity).toFixed(0) + "%"]);
        if (windMph != null) grid.push(["Wind", windMph.toFixed(0) + " mph"]);
        if (gustMph != null) grid.push(["Gusts", gustMph.toFixed(0) + " mph"]);
        if (visMi != null) grid.push(["Visibility", visMi.toFixed(1) + " mi"]);
        if (skyCover != null) grid.push(["Clouds", skyCover.toFixed(0) + "%"]);
        if (precipIn != null && precipIn > 0) grid.push(["Rain (1h)", precipIn.toFixed(2) + " in"]);

        const obsAt = props.timestamp ? formatCentralTime(props.timestamp) : "";
        const meta = "<div class='wp-meta'>Live from NWS KLFT (Lafayette Regional)" +
          (obsAt ? " · observed " + esc(obsAt) : "") +
          "<br><a href='https://forecast.weather.gov/zipcity.php?inputstring=Lafayette,LA' target='_blank' rel='noopener'>Full forecast →</a></div>";
        setWeatherUI({ tempF: tempF, windMph: windMph, precipIn: precipIn, skyCover: skyCover }, grid, meta);
      })
      .catch(function () { /* NWS unreachable — stored snapshot stays visible */ });
  }

  function fetchLiveAlerts() {
    fetch("https://api.weather.gov/alerts/active?zone=LAZ034", { headers: { "Accept": "application/geo+json" } })
      .then(function (resp) { return resp.ok ? resp.json() : Promise.reject(resp.status); })
      .then(function (data) {
        const features = (data && data.features) || [];
        if (!features.length) {
          els.alertBanner.style.display = "none";
          return;
        }
        const titles = [];
        for (const f of features.slice(0, 3)) {
          const ev = f && f.properties && f.properties.event;
          if (ev && titles.indexOf(ev) === -1) titles.push(ev);
        }
        if (!titles.length) { els.alertBanner.style.display = "none"; return; }
        els.alertBanner.innerHTML = "⚠ NWS: " + esc(titles.join(" · ")) +
          " <a href='https://alerts.weather.gov/search?zone=LAZ034' target='_blank' rel='noopener' style='color:inherit'>details</a>";
        els.alertBanner.style.display = "block";
      })
      .catch(function () { /* leave banner as-is */ });
  }

  /* ═══════════════════════ live data refresh ═══════════════════════ */
  // The server rewrites the data file every few minutes; re-import it in the
  // background so new incidents appear without a page reload.
  let refreshBusy = false;

  function onDataReplaced() {
    _dataSpanCache = null;
    _catCache.clear();
    renderStatTiles();
    renderStatus();
    renderLegend();
    buildCauseDropdown();
    scheduleRender(0);
  }

  function reloadData() {
    if (refreshBusy || document.visibilityState !== "visible") return;
    refreshBusy = true;
    const prevCount = INCIDENTS.length;
    const s = document.createElement("script");
    s.src = DATAJS_SRC + (DATAJS_SRC.indexOf("?") === -1 ? "?" : "&") + "v=" + Date.now();
    s.onload = function () {
      refreshBusy = false;
      s.remove();
      const fresh = window.INCIDENTS_DATA || [];
      if (fresh === INCIDENTS) return;
      INCIDENTS = fresh;
      OSM_INTERSECTIONS = window.OSM_INTERSECTIONS_DATA || [];
      HOT_SPOTS = window.HOT_SPOTS_DATA || [];
      UNLOCATED_COUNT = window.INCIDENTS_UNLOCATED_COUNT || 0;
      onDataReplaced();
      const delta = INCIDENTS.length - prevCount;
      if (delta > 0) toast(delta + " new incident" + (delta === 1 ? "" : "s") + " loaded");
    };
    s.onerror = function () { refreshBusy = false; s.remove(); };
    document.body.appendChild(s);
  }

  /* ═══════════════════════ tabs + mobile sheet ═══════════════════════ */
  function setTab(name) {
    const tabs = { filters: [els.tabFilters, els.panelFilters], analytics: [els.tabAnalytics, els.panelAnalytics], feed: [els.tabFeed, els.panelFeed] };
    for (const key of Object.keys(tabs)) {
      const on = key === name;
      tabs[key][0].classList.toggle("active", on);
      tabs[key][0].setAttribute("aria-selected", on ? "true" : "false");
      tabs[key][1].classList.toggle("active", on);
    }
  }

  function setSheetExpanded(expanded) {
    els.sidebar.classList.toggle("expanded", expanded);
    document.body.classList.toggle("sheet-expanded", expanded);
  }

  /* ═══════════════════════ wiring ═══════════════════════ */
  function wireUI() {
    els.themeBtn.addEventListener("click", cycleTheme);
    if (mediaDark && mediaDark.addEventListener) {
      mediaDark.addEventListener("change", function () { if (themePref() === "auto") applyTheme(); });
    }

    els.tabFilters.addEventListener("click", function () { setTab("filters"); });
    els.tabAnalytics.addEventListener("click", function () { setTab("analytics"); });
    els.tabFeed.addEventListener("click", function () { setTab("feed"); });

    els.clearBtn.addEventListener("click", function () {
      clearAll();
      scheduleRender(0);
      toast("Filters reset");
    });

    const changeIds = [
      "causeGroupSelect", "causeSelect", "chkInViewOnly",
      "monthSelect", "daySelect", "yearSelect",
      "dayTypeSelect", "timeBlockSelect",
      "chkRushHour", "chkSchoolDay", "dowSelect", "roadTypeSelect",
      "chkFloodWarning", "chkThunderstormWarning", "chkTornadoWatch",
      "chkWeatherOnly", "tempBand", "precipBand", "precipAmountBand", "windBand", "visBand", "cloudBand",
      "chkPoints", "chkHeat", "chkIntersections", "chkOsmIntersections", "chkMicro", "chkRings", "chkHotSpots",
      "topNSelect", "precIntersections", "precMicro"
    ];
    for (const id of changeIds) {
      const el = $(id);
      if (el) el.addEventListener("change", function () { scheduleRender(0); });
    }

    els.rangeChips.addEventListener("click", function (e) {
      const btn = e.target.closest(".range-chip");
      if (btn) setRange(btn.getAttribute("data-range") || "");
    });

    els.roadSearch.addEventListener("input", function () {
      els.roadSearchClear.classList.toggle("show", els.roadSearch.value.length > 0);
      scheduleRender(80);
    });
    els.roadSearchClear.addEventListener("mousedown", function (e) {
      e.preventDefault();
      els.roadSearch.value = "";
      els.roadSearchClear.classList.remove("show");
      scheduleRender(0);
    });

    // Weather chip toggles the detail panel.
    function toggleWeatherPanel() { els.weatherPanel.classList.toggle("open"); }
    els.weatherChip.addEventListener("click", toggleWeatherPanel);
    els.weatherChip.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleWeatherPanel(); }
    });

    // Mobile bottom sheet: tap the handle (or drag) to expand/collapse.
    let touchStartY = null;
    els.sbHandle.addEventListener("click", function () {
      setSheetExpanded(!els.sidebar.classList.contains("expanded"));
    });
    els.sidebar.addEventListener("touchstart", function (e) {
      if (e.target === els.sbHandle) touchStartY = e.touches[0].clientY;
    }, { passive: true });
    els.sidebar.addEventListener("touchmove", function (e) {
      if (touchStartY == null) return;
      const dy = e.touches[0].clientY - touchStartY;
      if (dy < -34) { setSheetExpanded(true); touchStartY = null; }
      else if (dy > 34) { setSheetExpanded(false); touchStartY = null; }
    }, { passive: true });
    els.sidebar.addEventListener("touchend", function () { touchStartY = null; }, { passive: true });

    // Keyboard shortcuts.
    document.addEventListener("keydown", function (e) {
      const tag = (e.target && e.target.tagName) || "";
      const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
      if (e.key === "Escape") {
        if (map) map.closePopup();
        els.weatherPanel.classList.remove("open");
        if (typing && e.target.blur) e.target.blur();
        return;
      }
      if (typing) return;
      if (e.key === "/") {
        e.preventDefault();
        setTab("filters");
        if (window.innerWidth <= 700) setSheetExpanded(true);
        els.roadSearch.focus();
      } else if (e.key === "t" || e.key === "T") {
        cycleTheme();
      }
    });

    if (map) {
      map.on("moveend", function () {
        if (els.chkInViewOnly.checked || pointRenderMode === "aggregated") {
          scheduleRender(80);
        } else {
          updateInViewOnly();
        }
      });
      map.on("zoomend", function () {
        if (els.chkInViewOnly.checked || pointRenderMode === "aggregated") {
          scheduleRender(80);
        } else {
          updatePointSizing();
          updateInViewOnly();
        }
      });

      let zoomAnimRaf = null;
      let pendingAnimZoom = null;
      map.on("zoomanim", function (e) {
        if (!els.chkPoints.checked) return;
        if (pointRenderMode === "aggregated" || pointSymbolCount > 1800) return;
        if (!e || !Number.isFinite(e.zoom)) return;
        pendingAnimZoom = e.zoom;
        if (zoomAnimRaf != null) return;
        zoomAnimRaf = requestAnimationFrame(function () {
          zoomAnimRaf = null;
          updatePointSizing(pendingAnimZoom);
        });
      });
    }

    // Periodic upkeep: status clock, weather, alerts, and background data refresh.
    setInterval(renderStatus, 30000);
    setInterval(function () {
      if (document.visibilityState === "visible") {
        fetchLiveNWSWeather();
        fetchLiveAlerts();
      }
    }, 10 * 60 * 1000);
    setInterval(reloadData, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") reloadData();
    });
  }

  /* ═══════════════════════ boot ═══════════════════════ */
  applyTheme();
  buildCauseDropdown();
  buildCauseGroupDropdown();
  applyHash();
  renderLegend();
  renderStatTiles();
  renderStatus();
  wireUI();
  showStoredWeather();
  fetchLiveNWSWeather();
  fetchLiveAlerts();
  renderAll();
})();
</script>
</body>
</html>
"""
