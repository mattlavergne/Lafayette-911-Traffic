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
    --radius: 26px;
    --radius-sm: 14px;

    --bg: #f5f6f8;
    --panel: rgba(255, 255, 255, 0.68);
    --panel-solid: #ffffff;
    --panel-border: rgba(255, 255, 255, 0.55);
    --edge: inset 0 1px 0 rgba(255, 255, 255, 0.85), inset 0 0 0 0.5px rgba(255, 255, 255, 0.35);
    --sheen: linear-gradient(155deg, rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0.06) 42%, transparent 60%);
    --glass-blur: blur(30px) saturate(1.7);
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
    --panel: rgba(15, 23, 42, 0.60);
    --panel-solid: #111827;
    --panel-border: rgba(148, 163, 184, 0.22);
    --edge: inset 0 1px 0 rgba(255, 255, 255, 0.12), inset 0 0 0 0.5px rgba(255, 255, 255, 0.05);
    --sheen: linear-gradient(155deg, rgba(255, 255, 255, 0.10), rgba(255, 255, 255, 0.02) 42%, transparent 60%);
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
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
  }
  .leaflet-bar { border: 1px solid var(--panel-border) !important; border-radius: 16px !important; overflow: hidden; box-shadow: var(--shadow-sm), var(--edge) !important; }
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
    background: var(--panel);
    color: var(--text);
    border-radius: 20px;
    border: 1px solid var(--panel-border);
    box-shadow: var(--shadow), var(--edge);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
  }
  .leaflet-popup-tip {
    background: var(--panel); border: 1px solid var(--panel-border);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
  }
  .leaflet-popup-content { margin: 12px 14px; font-family: var(--font); font-size: 13px; line-height: 1.45; }
  .leaflet-popup-close-button { color: var(--text-3) !important; font-size: 18px !important; padding: 6px 8px 0 0 !important; }
  .leaflet-container { font-family: var(--font); }
  /* Entrance animation on the card only — Leaflet positions .leaflet-popup
     itself with an inline transform, so that element must not be animated. */
  @keyframes popup-in {
    from { opacity: 0; transform: translateY(7px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }
  .leaflet-popup-content-wrapper, .leaflet-popup-tip-container {
    animation: popup-in 0.24s cubic-bezier(0.34, 1.3, 0.64, 1);
    transform-origin: bottom center;
  }

  /* ── Popup card ───────────────────────────────────────────────────── */
  .pc { min-width: 220px; max-width: 300px; }
  .pc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .pc-badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.01em; line-height: 1.3;
    padding: 3px 9px; border-radius: 999px;
    background: color-mix(in srgb, var(--cat, #64748b) 14%, transparent);
    color: var(--cat, #64748b);
  }
  .pc-badge::before { content: ""; flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--cat, #64748b); }
  .pc-time { font-size: 11px; color: var(--text-3); white-space: nowrap; padding-top: 2px; }
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
    font-family: var(--font);
  }
  .pc-nav button:disabled { opacity: 0.35; cursor: default; }
  .pc-nav .pc-nav-count { font-size: 11.5px; font-weight: 600; color: var(--text-2); white-space: nowrap; }
  .pc-nav .pc-back { font-size: 11.5px; font-weight: 600; white-space: nowrap; }
  .pc-nav-arrows { display: flex; gap: 4px; }

  /* multi-incident list browser inside popups */
  .pc-list-head { font-size: 13px; font-weight: 700; line-height: 1.3; }
  .pc-list-sub { font-size: 11px; color: var(--text-3); margin: 1px 0 7px 0; }
  .pc-list {
    max-height: 226px; overflow-y: auto; overscroll-behavior: contain;
    display: grid; gap: 2px; margin: 0 -5px; padding: 0 5px;
  }
  .pc-item {
    display: flex; gap: 8px; align-items: flex-start; width: 100%;
    padding: 6px 7px; border: none; border-radius: 9px; background: transparent;
    font-family: var(--font); text-align: left; cursor: pointer;
    transition: background 0.12s ease;
  }
  .pc-item:hover { background: var(--chip); }
  .pc-item .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--cat, #64748b); margin-top: 4px; flex: none; }
  .pc-item-body { flex: 1; min-width: 0; }
  .pc-item-cause { font-size: 12px; font-weight: 600; color: var(--text); line-height: 1.3; }
  .pc-item-sub { font-size: 10.5px; color: var(--text-3); display: flex; gap: 6px; flex-wrap: wrap; }
  .pc-item .chev { color: var(--text-3); font-size: 12px; margin-top: 2px; flex: none; }

  .incident-count-marker {
    border-radius: 999px;
    background: var(--panel-solid);
    color: var(--text);
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-family: var(--font);
    border: 2.5px solid var(--cat, #2563eb);
    /* offset second rim reads as a STACK of incidents, not one big point */
    box-shadow: 0 1px 6px rgba(0,0,0,0.25),
                -3px 3px 0 -1.5px var(--panel-solid),
                -3px 3px 0 0 var(--cat, #2563eb);
  }

  /* corridor detail dialog */
  .cd-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 10px 0 4px 0; }
  .cd-stat {
    background: var(--chip); border: 1px solid var(--panel-border);
    border-radius: var(--radius-sm); padding: 8px 10px;
  }
  .cd-stat b { display: block; font-size: 16px; font-variant-numeric: tabular-nums; }
  .cd-stat span { font-size: 10.5px; color: var(--text-2); }
  .cd-spot { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; padding: 3px 0; border-bottom: 1px dashed var(--panel-border); }
  .cd-spot:last-child { border-bottom: 0; }
  .cd-spot b { font-variant-numeric: tabular-nums; }
  .cd-actions { display: flex; gap: 8px; margin-top: 12px; }

  /* legend dialog samples */
  .lg-row { display: flex; gap: 10px; align-items: flex-start; margin: 7px 0; font-size: 12px; line-height: 1.4; }
  .lg-sym { flex: none; width: 34px; display: flex; align-items: center; justify-content: center; padding-top: 2px; }
  .lg-dot { width: 13px; height: 13px; border-radius: 50%; border: 2.2px solid var(--cat, #2563eb); background: color-mix(in srgb, var(--cat, #2563eb) 45%, transparent); display: inline-block; }
  .lg-count {
    width: 22px; height: 22px; border-radius: 999px; background: var(--panel-solid);
    border: 2.5px solid var(--cat, #2563eb); display: inline-flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700;
    box-shadow: -3px 3px 0 -1.5px var(--panel-solid), -3px 3px 0 0 var(--cat, #2563eb);
  }
  .lg-cluster { width: 22px; height: 22px; border-radius: 50%; border: 1.8px solid #2563eb; background: rgba(147, 197, 253, 0.45); display: inline-block; }
  .lg-beacon { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--good); display: inline-block; position: relative; }
  .lg-beacon::after { content: ""; position: absolute; inset: -5px; border-radius: 50%; border: 2px solid var(--good); opacity: 0.4; }
  .legend-help { color: var(--text-2); }
  .legend-help .dot { background: var(--text-3); }

  /* data-quality rows (analytics) */
  .dq-row { display: flex; align-items: center; gap: 7px; font-size: 11.5px; padding: 2.5px 0; }
  .dq-row .dot { flex: none; width: 8px; height: 8px; border-radius: 50%; }
  .dq-row .lbl { flex: 1; min-width: 0; }
  .dq-row .n { color: var(--text-3); font-variant-numeric: tabular-nums; }

  /* ── Sidebar ──────────────────────────────────────────────────────── */
  #sidebar {
    position: absolute; top: 14px; left: 14px; z-index: 1200;
    width: min(400px, calc(100vw - 28px));
    max-height: calc(100dvh - 28px);
    display: flex; flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: var(--radius);
    box-shadow: var(--shadow), var(--edge);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
    overflow: hidden;
    font-size: 13px;
  }
  /* refractive sheen sweeping the top of the glass slab */
  #sidebar::before {
    content: ""; position: absolute; inset: 0; border-radius: inherit;
    background: var(--sheen); pointer-events: none; z-index: 0;
  }
  #sidebar > * { position: relative; z-index: 1; }
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
    width: 34px; height: 34px; border-radius: 999px; border: 1px solid var(--panel-border);
    background: var(--chip); color: var(--text); cursor: pointer;
    display: flex; align-items: center; justify-content: center; padding: 0;
    box-shadow: var(--edge);
    transition: background 0.13s ease, transform 0.12s ease, box-shadow 0.12s ease;
  }
  .icon-btn:hover { background: var(--chip-hover); transform: translateY(-1px); box-shadow: var(--edge), 0 4px 10px rgba(0,0,0,0.12); }
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
  /* Stat tiles double as the time-range filter: each shows a live count and
     applies that range when tapped. A top accent rail + label mark them as
     interactive; the active range gets a filled accent border. */
  .stat-tile {
    position: relative; background: var(--chip); border-radius: var(--radius-sm);
    padding: 10px 6px 8px 6px; text-align: center; min-width: 0;
    box-shadow: var(--edge); border: 1px solid transparent; cursor: pointer;
    font-family: var(--font); color: inherit; overflow: hidden;
    transition: background 0.15s ease, transform 0.1s ease, border-color 0.15s ease;
  }
  .stat-tile::before {
    content: ""; position: absolute; top: 0; left: 50%; transform: translateX(-50%);
    width: 0; height: 2.5px; border-radius: 0 0 3px 3px; background: var(--accent);
    transition: width 0.2s ease;
  }
  .stat-tile:hover { background: var(--chip-hover); transform: translateY(-1px); }
  .stat-tile:hover::before { width: 40%; }
  .stat-tile.active { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: var(--accent-soft); }
  .stat-tile.active::before { width: 100%; }
  .stat-tile b { display: block; font-size: 17px; font-weight: 800; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
  .stat-tile span { display: block; font-size: 10px; color: var(--text-3); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 1px; }
  .stat-tile.active span { color: var(--accent); }
  .stat-tile.hot b { color: var(--bad); }
  .stat-tile.active.hot { border-color: color-mix(in srgb, var(--accent) 55%, transparent); }

  /* Collapsible filter sections */
  .acc .acc-head {
    width: 100%; display: flex; align-items: center; gap: 8px;
    padding: 11px 2px; background: none; border: none; cursor: pointer;
    font-family: var(--font); -webkit-tap-highlight-color: transparent;
  }
  .acc .acc-head .section-title { margin: 0; flex: 1; text-align: left; }
  .acc .acc-chev { color: var(--text-3); font-size: 16px; line-height: 1; transition: transform 0.2s ease; }
  .acc.open .acc-chev { transform: rotate(90deg); }
  .acc .acc-badge {
    min-width: 16px; height: 16px; padding: 0 5px; border-radius: 999px;
    background: var(--accent); color: #fff; font-size: 9.5px; font-weight: 700;
    display: none; align-items: center; justify-content: center;
  }
  .acc .acc-badge.show { display: inline-flex; }
  .acc .acc-body { display: none; padding-bottom: 10px; }
  .acc.open .acc-body { display: block; }
  .acc .acc-head:hover .section-title { color: var(--text); }

  .legend-chips {
    display: flex; flex-wrap: wrap; gap: 5px;
    padding: 0 14px 10px 14px;
  }
  .legend-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 999px;
    border: 1px solid var(--panel-border); background: var(--chip); color: var(--text-2);
    cursor: pointer; user-select: none; box-shadow: var(--edge);
    transition: background 0.13s ease, opacity 0.13s ease, border-color 0.13s ease, transform 0.12s ease;
  }
  .legend-chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--cat); flex: none; }
  .legend-chip .n { font-variant-numeric: tabular-nums; color: var(--text-3); font-weight: 500; }
  .legend-chip.off { opacity: 0.38; }
  .legend-chip:hover { background: var(--chip-hover); transform: translateY(-1px); }
  .legend-chip.on { border-color: color-mix(in srgb, var(--cat) 55%, transparent); color: var(--text); }

  .tabs {
    display: flex; gap: 4px; padding: 4px; margin: 0 14px 10px 14px;
    background: var(--chip); border-radius: 999px; box-shadow: var(--edge);
  }
  .tab {
    flex: 1; text-align: center; font-size: 12.5px; font-weight: 600; color: var(--text-2);
    padding: 7px 4px; border-radius: 999px; border: none; background: transparent; cursor: pointer;
    font-family: var(--font); position: relative;
    transition: background 0.16s ease, color 0.16s ease, box-shadow 0.16s ease;
  }
  .tab:hover { background: var(--chip-hover); }
  .tab.active {
    background: var(--panel-solid); color: var(--accent);
    box-shadow: 0 2px 8px rgba(0,0,0,0.14), inset 0 1px 0 rgba(255,255,255,0.35);
  }
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
  .sb-footer { flex-wrap: wrap; }
  #fitBtn,
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

  label.field, .field { display: block; font-size: 11.5px; color: var(--text-2); font-weight: 600; min-width: 0; }
  label.field select { margin-top: 4px; }
  .field-label { display: block; }
  .agency-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 5px; }
  .agency-chip {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11.5px; font-weight: 600; padding: 5px 11px 5px 9px; border-radius: 999px;
    border: 1px solid var(--panel-border); background: var(--chip); color: var(--text-2);
    cursor: pointer; user-select: none; box-shadow: var(--edge);
    transition: background 0.13s ease, border-color 0.13s ease, color 0.13s ease, transform 0.12s ease;
  }
  .agency-chip .tick {
    width: 14px; height: 14px; border-radius: 5px; flex: none;
    border: 1.5px solid var(--text-3); position: relative; transition: background 0.13s ease, border-color 0.13s ease;
  }
  .agency-chip .n { font-variant-numeric: tabular-nums; color: var(--text-3); font-weight: 500; }
  .agency-chip:hover { background: var(--chip-hover); transform: translateY(-1px); }
  .agency-chip.on { border-color: color-mix(in srgb, var(--accent) 55%, transparent); color: var(--text); }
  .agency-chip.on .tick { background: var(--accent); border-color: var(--accent); }
  .agency-chip.on .tick::after {
    content: ""; position: absolute; left: 4px; top: 1px; width: 3px; height: 7px;
    border: solid #fff; border-width: 0 2px 2px 0; transform: rotate(45deg);
  }
  .agency-chip.on .n { color: color-mix(in srgb, var(--text) 65%, transparent); }
  .facet-note { font-size: 10px; color: var(--text-3); margin-top: 5px; line-height: 1.35; }
  .mini-clear {
    display: inline-flex; align-items: center; font-size: 10.5px; font-weight: 600;
    padding: 3px 9px; border-radius: 999px; border: 1px solid var(--panel-border);
    background: var(--chip); color: var(--text-2); cursor: pointer; box-shadow: var(--edge);
    transition: background 0.13s ease, color 0.13s ease;
  }
  .mini-clear:hover { background: var(--chip-hover); color: var(--text); }

  /* active-filter chips: every live filter, each removable on its own */
  .active-chips { display: flex; flex-wrap: wrap; gap: 5px; padding: 0 14px 8px 14px; }
  .active-chips:empty { display: none; }
  .af-chip {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 10.5px; font-weight: 600; padding: 3px 4px 3px 9px; border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--accent) 42%, transparent);
    background: var(--chip); color: var(--text); box-shadow: var(--edge);
    max-width: 100%;
  }
  .af-chip .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px; }
  .af-chip .x {
    display: inline-flex; align-items: center; justify-content: center;
    width: 16px; height: 16px; border-radius: 50%; border: none; flex: none;
    background: transparent; color: var(--text-3); font-size: 12px; line-height: 1;
    cursor: pointer; padding: 0;
  }
  .af-chip .x:hover, .af-chip .x:focus-visible { background: var(--chip-hover); color: var(--text); }

  /* About / disclaimers modal (liquid glass) */
  .modal-overlay {
    position: fixed; inset: 0; z-index: 3000; display: none;
    align-items: center; justify-content: center; padding: 18px;
    background: color-mix(in srgb, var(--bg, #000) 40%, transparent);
    backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  }
  .modal-overlay.open { display: flex; }
  .modal-card {
    width: min(560px, 100%); max-height: min(78dvh, 640px); overflow-y: auto;
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: var(--radius); box-shadow: var(--shadow), var(--edge);
    backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur);
    padding: 18px 20px 20px 20px; font-size: 12.5px; color: var(--text-2); line-height: 1.5;
  }
  .modal-card h2 { font-size: 15px; color: var(--text); margin: 0 0 4px 0; display: flex; align-items: center; justify-content: space-between; }
  .modal-card h3 { font-size: 12px; color: var(--text); margin: 14px 0 4px 0; text-transform: uppercase; letter-spacing: 0.04em; }
  .modal-card p { margin: 5px 0; }
  .modal-card ul { margin: 5px 0; padding-left: 18px; }
  .modal-card a { color: var(--accent); }
  .modal-card .warn-box {
    border: 1px solid color-mix(in srgb, var(--bad) 45%, transparent);
    background: color-mix(in srgb, var(--bad) 9%, transparent);
    border-radius: 10px; padding: 8px 12px; margin: 8px 0; color: var(--text);
  }
  .modal-close {
    width: 26px; height: 26px; border-radius: 50%; border: 1px solid var(--panel-border);
    background: var(--chip); color: var(--text-2); cursor: pointer; font-size: 13px;
    line-height: 1; display: inline-flex; align-items: center; justify-content: center; padding: 0;
  }
  .modal-close:hover { background: var(--chip-hover); color: var(--text); }
  .rb-field { display: block; margin: 10px 0 2px; font-size: 11.5px; font-weight: 600; color: var(--text-2); }
  .rb-input, .rb-select { font-family: var(--font); font-size: 13px; color: var(--text); width: 100%;
    padding: 8px 10px; border-radius: 9px; border: 1px solid var(--panel-border); background: var(--input-bg); outline: none; }
  .rb-input:focus, .rb-select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .rb-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .rb-addrow { display: flex; gap: 6px; margin-top: 4px; }
  .rb-add { flex: none; padding: 8px 14px; border-radius: 9px; border: 1px solid var(--accent);
    background: var(--accent); color: #fff; font-weight: 700; font-size: 13px; cursor: pointer; }
  .rb-roads { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 2px; }
  .rb-road { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600;
    padding: 5px 6px 5px 11px; border-radius: 999px; border: 1px solid var(--panel-border);
    background: var(--chip); color: var(--text); }
  .rb-road .x { width: 16px; height: 16px; border: none; background: transparent; color: var(--text-3);
    cursor: pointer; font-size: 13px; line-height: 1; padding: 0; border-radius: 50%; }
  .rb-road .x:hover { background: var(--chip-hover); color: var(--text); }
  .rb-out { width: 100%; box-sizing: border-box; margin-top: 6px; font-family: ui-monospace, Menlo, Consolas, monospace;
    font-size: 11px; line-height: 1.5; color: var(--text); background: var(--chip); border: 1px solid var(--panel-border);
    border-radius: 10px; padding: 10px 12px; white-space: pre; overflow-x: auto; }
  .rb-copy { margin-top: 8px; }
  .rb-mail {
    display: inline-flex; align-items: center; gap: 7px; margin: 10px 8px 0 0;
    padding: 10px 20px; border-radius: 999px; border: none; cursor: pointer;
    background: var(--accent); color: #fff; font-weight: 700; font-size: 13.5px;
    text-decoration: none; box-shadow: var(--shadow-sm), var(--edge);
  }
  .rb-mail:hover { filter: brightness(1.08); }
  .rb-draw {
    display: inline-flex; align-items: center; gap: 6px; margin-top: 6px;
    padding: 8px 16px; border-radius: 999px; cursor: pointer;
    border: 1px solid var(--accent); background: transparent; color: var(--accent);
    font-weight: 700; font-size: 12.5px;
  }
  .rb-draw:hover { background: var(--accent-soft); }
  #rbPill {
    position: fixed; top: 14px; left: 50%; transform: translateX(-50%); z-index: 3200;
    display: none; align-items: center; gap: 8px; padding: 10px 14px;
    background: var(--panel); border: 1px solid var(--panel-border); border-radius: 999px;
    box-shadow: var(--shadow), var(--edge);
    backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur);
    font-size: 12.5px; color: var(--text); font-weight: 600; max-width: calc(100vw - 20px);
  }
  #rbPill.on { display: flex; }
  #rbPill button {
    padding: 6px 12px; border-radius: 999px; border: 1px solid var(--panel-border);
    background: var(--chip); color: var(--text); font-weight: 700; font-size: 12px; cursor: pointer;
  }
  #rbPill button.done { background: var(--accent); border-color: var(--accent); color: #fff; }
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
  input[type="date"] {
    font-family: var(--font); font-size: 12.5px; color: var(--text);
    padding: 6px 9px; border-radius: 9px; width: 100%;
    border: 1px solid var(--panel-border); background-color: var(--input-bg);
    outline: none; margin-top: 4px; color-scheme: light dark;
    transition: border-color 0.13s ease;
  }
  input[type="date"]:hover { border-color: var(--text-3); }
  input[type="date"]:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

  /* locate-me: glass disc under the weather chip */
  #locBtn {
    position: fixed; top: 62px; right: 14px; z-index: 1100;
    width: 38px; height: 38px; border-radius: 50%; padding: 0;
    display: flex; align-items: center; justify-content: center;
    background: var(--panel); border: 1px solid var(--panel-border);
    box-shadow: var(--shadow-sm), var(--edge);
    backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur);
    color: var(--text-2); cursor: pointer; transition: color 0.13s ease, transform 0.12s ease;
  }
  #locBtn:hover { color: var(--text); transform: scale(1.05); }
  #locBtn.busy svg { animation: ptr-spin 0.9s linear infinite; }

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
  .range-chip:hover { background: var(--chip-hover); transform: translateY(-1px); }
  .range-chip.active {
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 82%, #fff), var(--accent));
    border-color: transparent; color: #fff;
    box-shadow: 0 3px 10px color-mix(in srgb, var(--accent) 45%, transparent), inset 0 1px 0 rgba(255,255,255,0.45);
  }
  .range-chip { transition: all 0.15s ease; }

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
  .chart-svg .bar.sel, .chart-svg .hm-cell.sel { fill: var(--warn); stroke: var(--warn); stroke-width: 1; }
  .chart-svg .bar:focus-visible, .chart-svg .hm-cell:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .chart-svg .axis-label { font-size: 8.5px; fill: var(--text-3); font-family: var(--font); }
  .chart-svg .trend-line { stroke: var(--accent); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
  .chart-svg .trend-fill { fill: var(--accent); opacity: 0.12; }
  .chart-svg .trend-dot { fill: var(--accent); }
  .trend-delta { font-weight: 700; }
  .trend-delta.up { color: var(--bad); }
  .trend-delta.down { color: var(--good); }

  /* hour × day heatmap */
  .chart-svg .hm-cell { transition: fill-opacity 0.15s ease; }
  .chart-svg .hm-cell:hover { stroke: var(--accent); stroke-width: 1; }
  .chart-svg .hm-peak { stroke: var(--bad); stroke-width: 1.2; }

  /* category mix */
  .mix-bar { display: flex; height: 12px; border-radius: 7px; overflow: hidden; margin: 2px 0 8px 0; }
  .mix-seg { min-width: 3px; transition: flex-basis 0.35s ease; }
  .mix-legend { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 10px; }
  .mix-row { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-2); min-width: 0; }
  .mix-row .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .mix-row .lbl { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mix-row .n { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--text-3); white-space: nowrap; }

  /* corridor leaderboard */
  .cor-row {
    display: flex; align-items: center; gap: 8px; padding: 5px 6px; border-radius: 8px;
    cursor: pointer; transition: background 0.12s ease; border: none; width: 100%;
    background: transparent; font-family: var(--font); text-align: left;
  }
  .cor-row:hover { background: var(--chip); }
  .cor-rank { flex: 0 0 16px; font-size: 10px; font-weight: 700; color: var(--text-3); font-variant-numeric: tabular-nums; }
  .cor-name { flex: 1 1 108px; min-width: 88px; font-size: 11px; font-weight: 600; color: var(--text); line-height: 1.25; overflow-wrap: break-word; }
  .cor-row.on { background: color-mix(in srgb, var(--accent) 12%, transparent); border-radius: 8px; }
  .cor-row.on .cor-name { color: var(--accent); }
  .cor-pct { flex: 0 0 auto; font-size: 10px; color: var(--text-3); font-variant-numeric: tabular-nums; }
  .cor-bar-wrap { flex: 1; background: var(--bar-track); border-radius: 3px; height: 7px; overflow: hidden; min-width: 0; }
  .cor-bar { height: 7px; border-radius: 3px; background: var(--accent); transition: width 0.35s ease; min-width: 2px; }
  .cor-n { flex: 0 0 auto; font-size: 10.5px; color: var(--text-2); font-variant-numeric: tabular-nums; min-width: 26px; text-align: right; }

  /* feed date-group headers */
  .feed-group {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em;
    color: var(--text-3); padding: 10px 8px 4px 8px; position: sticky; top: 0;
    background: linear-gradient(var(--panel-solid) 70%, transparent); z-index: 1;
  }

  /* pulse beacons on very recent incidents */
  .beacon-wrap { pointer-events: none !important; }
  .beacon { position: relative; display: block; width: 18px; height: 18px; }
  .beacon::before, .beacon::after {
    content: ""; position: absolute; inset: 0; border-radius: 50%;
    border: 2px solid var(--cat, #e5484d); opacity: 0;
    animation: beacon-ping 2.4s cubic-bezier(0.2, 0.6, 0.4, 1) infinite;
  }
  .beacon::after { animation-delay: 1.2s; }
  @keyframes beacon-ping {
    0% { transform: scale(0.35); opacity: 0.85; }
    70% { transform: scale(1.6); opacity: 0; }
    100% { transform: scale(1.6); opacity: 0; }
  }

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
  .feed-meta { font-size: 11px; color: var(--text-3); }
  .feed-meta-row { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
  .feed-meta-row .feed-meta { flex: 1; margin: 0; }
  #feedRefreshBtn {
    flex: none; width: 26px; height: 26px; border-radius: 999px;
    border: 1px solid var(--panel-border); background: var(--chip); color: var(--text-2);
    font-size: 13px; cursor: pointer; box-shadow: var(--edge);
    transition: transform 0.3s ease, background 0.13s ease;
  }
  #feedRefreshBtn:hover { background: var(--chip-hover); }
  #feedRefreshBtn.spin { animation: ptr-spin 0.8s linear infinite; }

  /* pull-to-refresh */
  .ptr {
    height: 0; overflow: hidden; display: flex; align-items: center; justify-content: center;
    gap: 7px; color: var(--text-3); font-size: 11.5px; font-weight: 600;
    transition: height 0.18s ease;
  }
  .ptr.dragging { transition: none; }
  .ptr-icon { display: inline-block; transition: transform 0.15s ease; }
  .ptr.armed .ptr-icon { transform: rotate(180deg); }
  .ptr.loading .ptr-icon { animation: ptr-spin 0.8s linear infinite; }
  @keyframes ptr-spin { to { transform: rotate(360deg); } }

  .chart-note { font-size: 10.5px; color: var(--text-3); margin-top: 3px; line-height: 1.4; }
  .chart-svg .bar, .chart-svg .hm-cell { cursor: pointer; }
  .mix-row { cursor: pointer; border: none; background: transparent; font-family: var(--font); padding: 0; text-align: left; }
  .mix-row:hover .lbl { color: var(--text); }
  .rates-explain { font-size: 10.5px; color: var(--text-3); margin: 2px 0 6px 0; line-height: 1.4; }
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
  .feed-item.ghost { opacity: 0.8; }
  .feed-item.ghost .feed-dot { background: transparent; border: 2px dashed var(--cat, #64748b); width: 7px; height: 7px; }
  .feed-locating {
    font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
    padding: 1px 7px; border-radius: 999px;
    background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn);
  }

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
    border-radius: 999px; box-shadow: var(--shadow-sm), var(--edge);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
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
    border-radius: 18px; box-shadow: var(--shadow), var(--edge);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
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
    background: var(--panel); color: var(--text); border: 1px solid var(--panel-border);
    border-radius: 999px; padding: 10px 20px; font-size: 12.5px; font-weight: 600;
    box-shadow: var(--shadow), var(--edge); z-index: 3000; opacity: 0; pointer-events: none;
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
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

  /* Graceful fallback when backdrop-filter is unavailable: solid surfaces. */
  @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
    #sidebar, #weatherChip, #weatherPanel, #toast,
    .leaflet-popup-content-wrapper, .leaflet-popup-tip {
      background: var(--panel-solid);
    }
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
      border-radius: 28px 28px 0 0;
      max-height: 94dvh;
      height: 236px;
      transition: height 0.3s cubic-bezier(0.32, 0.72, 0, 1);
      padding-bottom: env(safe-area-inset-bottom);
    }
    /* Expanded: near-full-screen, and compact the fixed chrome above the
       scroll area — on short phones (iPhone mini) every saved pixel goes
       straight to the filters/analytics scroll room. */
    #sidebar.expanded { height: 94dvh; }
    #sidebar.expanded .sb-header { padding-top: 0; padding-bottom: 4px; }
    #sidebar.expanded .brand-sub { display: none; }
    #sidebar.expanded .stat-tile { padding: 7px 6px 6px 6px; }
    #sidebar.expanded .stat-tiles { padding-bottom: 4px; }
    #sidebar.expanded .legend-chips { padding-bottom: 6px; }
    #sidebar.expanded .tabs { margin-bottom: 6px; }
    #sidebar.dragging { transition: none; }
    /* Wide, finger-friendly grab target with a small visible bar. */
    #sbHandle {
      display: block; width: 100%; padding: 11px 0 5px; margin: 0;
      flex: none; cursor: grab; background: none; opacity: 1;
      touch-action: none; -webkit-tap-highlight-color: transparent;
    }
    #sbHandle:active { cursor: grabbing; }
    #sbHandle::before {
      content: ""; display: block; width: 42px; height: 5px; border-radius: 999px;
      background: var(--text-3); opacity: 0.5; margin: 0 auto;
    }
    .sb-header { padding-top: 4px; touch-action: none; }
    #sidebar:not(.expanded) .sb-body,
    #sidebar:not(.expanded) .tabs,
    #sidebar:not(.expanded) .legend-chips,
    #sidebar:not(.expanded) #alertBanner,
    #sidebar:not(.expanded) .kbd-hint { display: none; }
    #sidebar:not(.expanded) .sb-footer { border-top: none; padding-top: 2px; }
    .stat-tiles { padding-bottom: 6px; }
    .stat-tile { padding: 12px 6px 10px 6px; }
    .acc .acc-head { padding: 14px 2px; }
    select, .check { min-height: 42px; font-size: 14px; }
    .check input { width: 19px; height: 19px; }
    .agency-chip { min-height: 38px; font-size: 13px; padding: 7px 14px 7px 11px; }
    .agency-chip .tick { width: 17px; height: 17px; }
    .agency-chip.on .tick::after { left: 5px; top: 2px; width: 4px; height: 8px; }
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
      <a class="icon-btn" id="homeBtn" href="https://mattlavergne.com/" title="Back to mattlavergne.com" aria-label="Back to home page">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 11.5 12 4l9 7.5"/>
          <path d="M5.5 10v9.5a.5.5 0 0 0 .5.5h4V15a2 2 0 0 1 4 0v5h4a.5.5 0 0 0 .5-.5V10"/>
        </svg>
      </a>
      <button class="icon-btn" id="themeBtn" type="button" title="Toggle theme (auto / light / dark)" aria-label="Toggle color theme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="4"/>
          <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>
        </svg>
      </button>
      <button class="icon-btn" id="routeBtn" type="button" title="Build a commute alert" aria-label="Build a personal commute alert" aria-haspopup="dialog">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M6 19a2 2 0 1 1-.001-3.999A2 2 0 0 1 6 19zm12-11a2 2 0 1 1 .001-3.999A2 2 0 0 1 18 8z"/>
          <path d="M6 17V9a3 3 0 0 1 3-3h6M18 10v5a3 3 0 0 1-3 3H9"/>
        </svg>
      </button>
      <button class="icon-btn" id="aboutBtn" type="button" title="About, disclaimers &amp; privacy" aria-label="About this project, disclaimers and privacy" aria-haspopup="dialog">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="9"/>
          <path d="M12 11v5"/>
          <circle cx="12" cy="8" r="0.4" fill="currentColor"/>
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

  <div class="stat-tiles" id="rangeTiles" role="group" aria-label="Filter by time range — each tile shows its count and applies that range">
    <button class="stat-tile active" id="tile24h" data-range="24h" type="button"><b>–</b><span>Past 24h</span></button>
    <button class="stat-tile" data-range="7d" type="button"><b id="tileWeekVal">–</b><span>7 days</span></button>
    <button class="stat-tile" data-range="30d" type="button"><b id="tileMonthVal">–</b><span>30 days</span></button>
    <button class="stat-tile" data-range="all" type="button"><b id="tileTotalVal">–</b><span>All time</span></button>
  </div>

  <div class="legend-chips" id="legendChips" aria-label="Incident categories (tap to toggle)"></div>

  <div class="tabs" role="tablist">
    <button class="tab active" id="tabFilters" role="tab" aria-selected="true" type="button">Filters<span class="badge" id="filterBadge"></span></button>
    <button class="tab" id="tabAnalytics" role="tab" aria-selected="false" type="button">Analytics</button>
    <button class="tab" id="tabFeed" role="tab" aria-selected="false" type="button">Feed</button>
  </div>

  <div class="active-chips" id="activeChips" aria-label="Active filters"></div>

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
      </div>

      <!-- Collapsible sections keep the tab uncluttered; each header shows a
           badge with how many filters are active inside when collapsed. -->
      <div class="section acc open" data-acc="type" data-filter-acc>
        <button class="acc-head" type="button"><span class="section-title">Incident type</span><span class="acc-badge"></span><span class="acc-chev">›</span></button>
        <div class="acc-body">
          <div class="row row-grid">
            <label class="field">Group
              <select id="causeGroupSelect"><option value="__ALL__">All groups</option></select>
            </label>
            <label class="field">Type
              <select id="causeSelect"><option value="__ALL__">All types</option></select>
            </label>
          </div>
          <div class="row">
            <div class="field" style="flex:1">
              <span class="field-label">Responding agency</span>
              <div id="agencyChecklist" class="agency-chips" role="group" aria-label="Filter by responding agency — select any to narrow"></div>
              <div class="facet-note">Counts reflect your other filters. One incident can involve several
              agencies, so percentages can total more than 100%.</div>
            </div>
          </div>
        </div>
      </div>

      <div class="section acc" data-acc="datetime" data-filter-acc>
        <button class="acc-head" type="button"><span class="section-title">Date &amp; time</span><span class="acc-badge"></span><span class="acc-chev">›</span></button>
        <div class="acc-body">
          <div class="row row-checks">
            <label class="check"><input type="checkbox" id="chkRushHour"> Rush hour only</label>
            <label class="check"><input type="checkbox" id="chkSchoolDay"> School days only</label>
            <label class="check"><input type="checkbox" id="chkHoliday"> Holidays only</label>
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
          <div class="row" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <label class="field">From date
              <input type="date" id="dateFrom">
            </label>
            <label class="field">To date
              <input type="date" id="dateTo">
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
            <label class="field">Light
              <select id="lightSelect">
                <option value="any">Any</option>
                <option value="day">Daylight</option>
                <option value="dark">After dark</option>
              </select>
            </label>
          </div>
        </div>
      </div>

      <div class="section acc" data-acc="road" data-filter-acc>
        <button class="acc-head" type="button"><span class="section-title">Road</span><span class="acc-badge"></span><span class="acc-chev">›</span></button>
        <div class="acc-body">
          <div class="row">
            <label class="field" style="flex:1">Road type
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
          <div class="row row-checks">
            <label class="check" title="Hide incidents whose mapped point is only an approximate road match"><input type="checkbox" id="chkExcludeLowConf"> Exclude low-confidence locations</label>
          </div>
          <div class="row-note">Hides points placed with only an approximate road match, so hot spots and
          corridor rankings use trusted locations only. Precomputed all-time layers (hot spots,
          OSM intersections) are built server-side and unaffected.</div>
        </div>
      </div>

      <div class="section acc" data-acc="weather" data-filter-acc>
        <button class="acc-head" type="button"><span class="section-title">Conditions at time of incident</span><span class="acc-badge"></span><span class="acc-chev">›</span></button>
        <div class="acc-body">
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
          <div class="row-note">Weather filters ignore incidents without weather data unless a specific condition is chosen.</div>
        </div>
      </div>

      <div class="section acc" data-acc="nws" data-filter-acc>
        <button class="acc-head" type="button"><span class="section-title">Active NWS alerts at report time</span><span class="acc-badge"></span><span class="acc-chev">›</span></button>
        <div class="acc-body">
          <div class="row row-checks">
            <label class="check"><input type="checkbox" id="chkFloodWarning"> Flash flood</label>
            <label class="check"><input type="checkbox" id="chkThunderstormWarning"> Severe storm</label>
            <label class="check"><input type="checkbox" id="chkTornadoWatch"> Tornado watch</label>
          </div>
          <div class="row-note">Matches incidents recorded while that alert was active for Lafayette Parish (zone LAZ034).</div>
        </div>
      </div>

      <div class="section acc" data-acc="layers">
        <button class="acc-head" type="button"><span class="section-title">Map layers &amp; display</span><span class="acc-chev">›</span></button>
        <div class="acc-body">
          <div class="row row-checks">
            <label class="check"><input type="checkbox" id="chkPoints" checked> Incidents</label>
            <label class="check"><input type="checkbox" id="chkHeat"> Heatmap</label>
            <label class="check" title="Precomputed over the whole archive — not affected by filters"><input type="checkbox" id="chkHotSpots"> Hot spots (all-time)</label>
            <label class="check"><input type="checkbox" id="chkIntersections"> Rounded clusters</label>
            <label class="check" title="Precomputed over the whole archive — not affected by filters"><input type="checkbox" id="chkOsmIntersections"> OSM intersections (all-time)</label>
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
      </div>

      <div class="kbd-hint"><kbd>/</kbd> search &nbsp; <kbd>Esc</kbd> close &nbsp; <kbd>T</kbd> theme</div>
    </div>

    <!-- ═══ ANALYTICS ═══ -->
    <div class="panel" id="panelAnalytics">
      <div class="an-summary" id="analyticsSummary"></div>
      <div id="anClearRow" style="display:none;margin:0 0 8px 0">
        <button id="anClearBtn" type="button" class="mini-clear">Clear analytics selections</button>
      </div>

      <div class="chart-block">
        <div class="chart-title">By hour of day <span class="sub" id="chartHourSub"></span></div>
        <div id="chartHour"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">By day of week <span class="sub" id="chartDowSub"></span></div>
        <div id="chartDow"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">Hour × day heatmap <span class="sub" id="chartMatrixSub"></span></div>
        <div id="chartMatrix"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title"><span id="chartTrendTitle">Trend</span> <span class="sub" id="chartTrendSub"></span></div>
        <div id="chartTrend"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">Seasonality by month <span class="sub" id="chartMonthSub"></span></div>
        <div id="chartMonth"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">Category mix</div>
        <div id="chartMix"></div>
      </div>

      <div class="chart-block">
        <div class="chart-title">Top corridors <span class="sub">tap a corridor for details</span></div>
        <div id="corridorList"></div>
        <div class="facet-note">Corridors are normalized from raw locations (house numbers, suffixes and
        direction tags collapse). An incident at an intersection counts toward both roads.</div>
      </div>

      <div class="chart-block">
        <div class="chart-title">Data quality <span class="sub" id="dataQualitySub"></span></div>
        <div id="dataQuality"></div>
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
      <div class="ptr" id="ptrBar" aria-hidden="true"><span class="ptr-icon" id="ptrIcon">↓</span><span id="ptrText">Pull to refresh</span></div>
      <div class="feed-meta-row">
        <div class="feed-meta" id="feedMeta"></div>
        <button id="feedRefreshBtn" type="button" title="Check for new incidents now" aria-label="Refresh feed">↻</button>
      </div>
      <div id="feedList"></div>
    </div>

  </div>

  <div class="sb-footer">
    <div class="counts" aria-live="polite">
      <span title="Every incident in the archive">Archive <b id="countTotal">0</b></span>
      <span title="Incidents matching the current filters">Matching <b id="countFiltered">0</b></span>
      <span title="Matching incidents inside the current map view">Visible <b id="countInView">0</b></span>
    </div>
    <label title="Only incidents in current map view"><input type="checkbox" id="chkInViewOnly" style="accent-color:var(--accent);"> Only incidents in current map view</label>
    <button id="fitBtn" type="button" title="Fit the map to the matching incidents">Fit results</button>
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

<button id="locBtn" type="button" title="Show my location on the map (stays in your browser)" aria-label="Show my location on the map">
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
    <circle cx="12" cy="12" r="3"/>
    <path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>
    <circle cx="12" cy="12" r="8"/>
  </svg>
</button>

<div id="rbPill" role="status">
  <span id="rbPillText">Tap the map along your route</span>
  <button type="button" id="rbUndo">Undo</button>
  <button type="button" id="rbCancel">Cancel</button>
  <button type="button" class="done" id="rbDone">Done</button>
</div>

<div class="modal-overlay" id="routeModal" role="dialog" aria-modal="true" aria-labelledby="routeTitle" aria-hidden="true">
  <div class="modal-card">
    <h2 id="routeTitle">Build a commute alert <button class="modal-close" id="routeClose" type="button" aria-label="Close">×</button></h2>
    <p>Get an email shortly before you leave, listing the <strong>current</strong> 911 incidents on the
    exact stretches of road you drive (plus active NWS alerts). Draw your route, then
    <strong>email it to the same Gmail account your collector uses</strong> — it spots the message, saves
    the route, and replies to confirm. Nothing to configure on the Pi, and nothing you enter here is
    sent anywhere by this page.</p>
    <div id="rbSavedWrap" style="display:none">
      <label class="rb-field">My saved routes (stored only in this browser)</label>
      <div class="rb-roads" id="rbSaved" aria-live="polite"></div>
      <div class="facet-note">Tap <b>map</b> to see a route on the map — it renders only on this device
      and is never uploaded. Removing a route here only forgets it on this device; to remove it from the
      Pi, email <code>LAF911_ROUTE_&lt;n&gt;_DELETE=true</code>.</div>
    </div>
    <div class="rb-row">
      <div><label class="rb-field" for="rbSlot">Route slot</label>
        <select class="rb-select" id="rbSlot"><option value="1">1 (e.g. to work)</option><option value="2">2 (e.g. home)</option><option value="3">3</option></select></div>
      <div><label class="rb-field" for="rbName">Name</label>
        <input class="rb-input" id="rbName" type="text" placeholder="To work" maxlength="40"></div>
    </div>
    <div class="rb-row">
      <div><label class="rb-field" for="rbDepart">Usual departure</label>
        <input class="rb-input" id="rbDepart" type="time" value="07:20"></div>
      <div><label class="rb-field" for="rbDays">Days</label>
        <select class="rb-select" id="rbDays"><option value="mon-fri">Weekdays</option><option value="daily">Every day</option><option value="weekends">Weekends</option><option value="mon,wed,fri">Mon/Wed/Fri</option></select></div>
    </div>
    <label class="rb-field">Your route — the sections you actually drive</label>
    <button class="rb-draw" id="rbDraw" type="button">✏️ Draw route on the map</button>
    <div class="facet-note" id="rbPathStatus" aria-live="polite">No route drawn yet. Tap a few stops along
    your drive — the line <strong>snaps to the actual roads</strong> between taps (curves and turns included),
    so side roads you merely cross never join the route. Only incidents close to that snapped line will alert; nearby or crossed roads are not added automatically.</div>
    <label class="rb-field" for="rbRoad">Road-only fallback labels (optional — ignored once a route is drawn)</label>
    <div class="rb-addrow">
      <input class="rb-input" id="rbRoad" type="text" placeholder="e.g. Ambassador Caffery" list="rbRoadList" autocomplete="off">
      <button class="rb-add" id="rbAdd" type="button">Add</button>
    </div>
    <datalist id="rbRoadList"></datalist>
    <div class="rb-roads" id="rbRoads" aria-live="polite"></div>
    <label class="rb-field" for="rbPiEmail">Your collector&#39;s Gmail (optional — remembered only on this device)</label>
    <input class="rb-input" id="rbPiEmail" type="email" placeholder="yourname@gmail.com" autocomplete="off">
    <a class="rb-mail" id="rbMail" href="#" rel="noopener">📧 Email this route to your Pi</a>
    <button class="mini-clear rb-copy" id="rbCopy" type="button">Copy instead</button>
    <p class="facet-note">Send it <strong>from and to the same Gmail address</strong> the collector uses —
    it only trusts messages from itself. You&#39;ll get a &ldquo;route saved&rdquo; reply within a few
    minutes. Repeat per slot (to-work and home can differ); to remove one, email
    <code>LAF911_ROUTE_1_DELETE=true</code>. While drawing, your tapped points are sent to the public
    <a href="https://project-osrm.org/" target="_blank" rel="noopener noreferrer">OSRM</a> router to snap the
    line to real roads — the only time this page sends anything, and only while you draw.</p>
    <div class="rb-out" id="rbOut" aria-live="polite"></div>
  </div>
</div>

<div class="modal-overlay" id="corridorModal" role="dialog" aria-modal="true" aria-labelledby="corridorTitle" aria-hidden="true">
  <div class="modal-card">
    <h2 id="corridorTitle"><span id="corridorTitleText">Corridor</span> <button class="modal-close" id="corridorClose" type="button" aria-label="Close">&times;</button></h2>
    <div id="corridorBody"></div>
  </div>
</div>

<div class="modal-overlay" id="legendModal" role="dialog" aria-modal="true" aria-labelledby="legendTitle" aria-hidden="true">
  <div class="modal-card">
    <h2 id="legendTitle">Map legend <button class="modal-close" id="legendClose" type="button" aria-label="Close">&times;</button></h2>
    <div id="legendBody"></div>
  </div>
</div>

<div class="modal-overlay" id="aboutModal" role="dialog" aria-modal="true" aria-labelledby="aboutTitle" aria-hidden="true">
  <div class="modal-card">
    <h2 id="aboutTitle">About this map <button class="modal-close" id="aboutClose" type="button" aria-label="Close">×</button></h2>
    <p>An <strong>independent, unofficial</strong> hobby project that visualizes publicly posted
    911 traffic incident reports for Lafayette Parish, Louisiana. It is <strong>not affiliated with or
    endorsed by</strong> Lafayette 911, Lafayette Parish government, LPD/LPSO/LFD, NOAA, the National
    Weather Service, or any other agency, and it is <strong>not an official dispatch service</strong>.</p>
    <div class="warn-box"><strong>Do not use this map for emergency response, navigation, routing, or
    safety decisions.</strong> Incident, map, weather, and alert information can be delayed, incomplete,
    approximate, unavailable, or plain wrong. <strong>In an emergency, call 911.</strong> For authoritative
    information use official local sources and <a href="https://www.weather.gov/lch/" target="_blank" rel="noopener noreferrer">weather.gov</a>.</div>
    <h3>Data sources</h3>
    <ul>
      <li>Incident reports: the public lafayette911.org feed, polled every few minutes. Locations are
      approximate (geocoded from street descriptions).</li>
      <li>Weather &amp; alerts: <a href="https://www.weather.gov/documentation/services-web-api" target="_blank" rel="noopener noreferrer">National Weather Service API</a> (live, no key).</li>
      <li>Basemaps: <a href="https://carto.com/attribution/" target="_blank" rel="noopener noreferrer">CARTO</a> &amp; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors.</li>
    </ul>
    <p id="aboutGenerated" class="facet-note"></p>
    <h3>Privacy</h3>
    <p>This page has <strong>no accounts, no advertising, no behavioral tracking, no fingerprinting, and
    no analytics</strong>. It stores only a theme preference in your browser's local storage. Your browser
    does contact external services to work — map tiles (CARTO/OpenStreetMap), the Leaflet library CDN
    (unpkg), weather/alerts (api.weather.gov), and the site host (GitHub Pages) — and those providers,
    like any web host, may keep standard technical access logs. Links out to Google Maps, Street View,
    and Waze open only when you tap them. The locate button uses your device's location only inside
    your browser to move the map — it is never transmitted or stored. If you draw a commute route in the
    route builder, the points you tap are sent to the public OSRM demo router
    (router.project-osrm.org) to snap the line to real roads — only while drawing, never otherwise.</p>
    <h3>Licenses</h3>
    <p>Original code is MIT-licensed
    (<a href="https://github.com/mattlavergne/Lafayette-911-Traffic" target="_blank" rel="noopener noreferrer">source on GitHub</a>).
    Incident data, map data/tiles, and weather data belong to their providers — map data
    © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>
    contributors, tiles by <a href="https://carto.com/attribution/" target="_blank" rel="noopener noreferrer">CARTO</a>,
    mapping by <a href="https://leafletjs.com/" target="_blank" rel="noopener noreferrer">Leaflet</a>,
    weather by <a href="https://www.weather.gov/" target="_blank" rel="noopener noreferrer">NWS/NOAA</a>.</p>
  </div>
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
  let UNLOCATED_LIST = window.INCIDENTS_UNLOCATED_LIST || [];
  let UNMAPPABLE_COUNT = window.INCIDENTS_UNMAPPABLE_COUNT || 0;

  const IDX_LAT = 0, IDX_LNG = 1, IDX_REPORTED = 2, IDX_LOCATION = 3, IDX_CAUSE = 4,
        IDX_ASSIST = 5, IDX_WEIGHT = 6, IDX_COUNT = 7, IDX_TEMP_F = 8, IDX_PRECIP_PROB = 9,
        IDX_PRECIP_IN = 10, IDX_WIND_SPEED = 11, IDX_WIND_GUST = 12, IDX_VISIBILITY = 13,
        IDX_SKY_COVER = 14, IDX_WEATHER_AT = 15, IDX_WEATHER_SOURCE = 16, IDX_HOUR = 17,
        IDX_DOW = 18, IDX_SCHOOL_DAY = 19, IDX_NWS_FLOOD = 20, IDX_NWS_STORM = 21,
        IDX_NWS_TORNADO = 22, IDX_HIGHWAY = 23, IDX_CREATED_AT = 24, IDX_HOLIDAY = 25,
        IDX_CORRIDORS = 26,  // canonical corridor ids (array), absent in old data
        IDX_CONFIDENCE = 27, // v4: geocode confidence string (v3 files carry the old TC-history Array here)
        IDX_VISIBLE_MIN = 28,// v4: minutes visible in the public feed (NOT response/clearance time)
        IDX_ACTIVE = 29,     // v4: 1 = listed in the most recent feed check
        IDX_OBS_COUNT = 30,  // v4: number of feed checks that listed this incident
        IDX_TC_HISTORY = 31, // aggregated TRAFFIC CONTROL only: [["YYYY-MM-DD", n], ...] (index 27 in v3 files)
        IDX_EPISODE = 32;    // v5: episode primaries carry folded duplicate listings [[cause, reported], ...]

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
    if (lower === "nan" || lower === "none" || lower === "null" || lower === "undefined" || lower === "<na>") return "";
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

  // ~100 m grid index over the full dataset (ignores filters) powering the
  // "location history" line in popups. Rebuilt whenever the data reloads.
  let locHistory = new Map();

  function locHistoryKey(row) {
    return row[IDX_LAT].toFixed(3) + "," + row[IDX_LNG].toFixed(3);
  }

  function buildLocHistory() {
    locHistory = new Map();
    for (const row of INCIDENTS) {
      const key = locHistoryKey(row);
      const n = incidentCount(row);
      let entry = locHistory.get(key);
      if (!entry) {
        entry = { count: 0, firstMs: null };
        locHistory.set(key, entry);
      }
      entry.count += n;
      const dt = bestRowDate(row);
      if (dt && (entry.firstMs == null || dt.getTime() < entry.firstMs)) entry.firstMs = dt.getTime();
    }
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

  // Major US federal + Louisiana holidays. Mirrors the backend _is_holiday
  // heuristic and is the fallback for rows lacking the stored is_holiday flag.
  function nthWeekdayDay(year, month0, weekday, n) {
    const first = new Date(year, month0, 1).getDay();
    return 1 + ((7 + weekday - first) % 7) + (n - 1) * 7;
  }
  function lastWeekdayDay(year, month0, weekday) {
    const last = new Date(year, month0 + 1, 0);
    return last.getDate() - ((7 + last.getDay() - weekday) % 7);
  }
  function isHolidayJS(dt) {
    if (!dt) return false;
    const y = dt.getFullYear(), m = dt.getMonth() + 1, d = dt.getDate();
    if ((m === 1 && d === 1) || (m === 6 && d === 19) || (m === 7 && d === 4) ||
        (m === 11 && d === 11) || (m === 12 && d === 25)) return true;
    if (m === 1 && d === nthWeekdayDay(y, 0, 1, 3)) return true;   // MLK
    if (m === 2 && d === nthWeekdayDay(y, 1, 1, 3)) return true;   // Presidents
    if (m === 5 && d === lastWeekdayDay(y, 4, 1)) return true;     // Memorial
    if (m === 9 && d === nthWeekdayDay(y, 8, 1, 1)) return true;   // Labor
    if (m === 10 && d === nthWeekdayDay(y, 9, 1, 2)) return true;  // Columbus
    if (m === 11 && d === nthWeekdayDay(y, 10, 4, 4)) return true; // Thanksgiving
    return false;
  }

  /* ═══════════════════════ elements ═══════════════════════ */
  function $(id) { return document.getElementById(id); }
  const els = {
    sidebar: $("sidebar"), sbHandle: $("sbHandle"),
    themeBtn: $("themeBtn"),
    statusText: $("statusText"), unlocChip: $("unlocChip"), alertBanner: $("alertBanner"),
    tile24h: $("tile24h"),
    tileWeekVal: $("tileWeekVal"), tileMonthVal: $("tileMonthVal"), tileTotalVal: $("tileTotalVal"),
    legendChips: $("legendChips"),
    tabFilters: $("tabFilters"), tabAnalytics: $("tabAnalytics"), tabFeed: $("tabFeed"),
    panelFilters: $("panelFilters"), panelAnalytics: $("panelAnalytics"), panelFeed: $("panelFeed"),
    filterBadge: $("filterBadge"),
    roadSearch: $("roadSearch"), roadSearchClear: $("roadSearchClear"),
    rangeTiles: $("rangeTiles"),
    agencyChecklist: $("agencyChecklist"), chkHoliday: $("chkHoliday"), lightSelect: $("lightSelect"),
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
    chkInViewOnly: $("chkInViewOnly"), clearBtn: $("clearBtn"), fitBtn: $("fitBtn"),
    dateFrom: $("dateFrom"), dateTo: $("dateTo"),
    activeChips: $("activeChips"), anClearRow: $("anClearRow"), anClearBtn: $("anClearBtn"),
    chartTrendTitle: $("chartTrendTitle"),
    aboutBtn: $("aboutBtn"), aboutModal: $("aboutModal"), aboutClose: $("aboutClose"),
    routeBtn: $("routeBtn"), routeModal: $("routeModal"), routeClose: $("routeClose"),
    corridorModal: $("corridorModal"), corridorClose: $("corridorClose"),
    corridorBody: $("corridorBody"), corridorTitleText: $("corridorTitleText"),
    legendModal: $("legendModal"), legendClose: $("legendClose"), legendBody: $("legendBody"),
    dataQuality: $("dataQuality"), dataQualitySub: $("dataQualitySub"),
    chkExcludeLowConf: $("chkExcludeLowConf"),
    aboutGenerated: $("aboutGenerated"),
    analyticsSummary: $("analyticsSummary"),
    chartHour: $("chartHour"), chartHourSub: $("chartHourSub"),
    chartDow: $("chartDow"), chartDowSub: $("chartDowSub"),
    chartTrend: $("chartTrend"), chartTrendSub: $("chartTrendSub"),
    chartMatrix: $("chartMatrix"), chartMatrixSub: $("chartMatrixSub"),
    chartMonth: $("chartMonth"), chartMonthSub: $("chartMonthSub"),
    chartMix: $("chartMix"), corridorList: $("corridorList"),
    ratesContent: $("ratesContent"), insightsContent: $("insightsContent"),
    feedList: $("feedList"), feedMeta: $("feedMeta"),
    feedRefreshBtn: $("feedRefreshBtn"), sbBody: document.querySelector(".sb-body"),
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
      preferCanvas: true,
      // Keep popups open when the map itself is clicked. On touch devices the
      // synthesized "ghost" click that follows a tap would otherwise land on
      // empty map and dismiss the popup the same tap just opened. Popups are
      // closed via their × button, the Esc key, or opening another one.
      closePopupOnClick: false
    });
    // tolerance widens the hit-test radius around canvas markers so taps
    // don't have to land dead-center — especially forgiving on touch.
    renderer = L.canvas({ padding: 0.5, tolerance: isTouch ? 16 : 6 });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.attribution({ position: "bottomright", prefix: false }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: true, metric: false }).addTo(map);

    // The first renderAll() call fits the view to the DEFAULT FILTERED
    // incidents (see fitToResults), not the whole archive.
  } else {
    document.body.classList.add("no-map");
  }

  /* ═══════════════════════ popup content ═══════════════════════ */
  function weatherNumber(row, idx) {
    if (!row || row.length <= idx) return null;
    const val = parseFloat(row[idx]);
    return Number.isFinite(val) ? val : null;
  }

  // Occurrence count carried by aggregated rows (e.g. TRAFFIC CONTROL
  // groups); plain rows count as 1. The ONE way to read IDX_COUNT, so older
  // data files without the field stay compatible.
  function incidentCount(row) {
    if (row.length > IDX_COUNT && row[IDX_COUNT] != null) {
      return Math.max(1, parseInt(row[IDX_COUNT], 10) || 1);
    }
    return 1;
  }

  // Aggregated routine TRAFFIC CONTROL group (e.g. daily school car-rider
  // control): recorded and counted, but deliberately second-class on the
  // map so it can never crowd out or steal taps from real incidents.
  function isRoutineTC(row) {
    return incidentCount(row) > 1 &&
      String(row[IDX_CAUSE] || "").trim().toUpperCase() === "TRAFFIC CONTROL";
  }

  // v4 row fields, tolerant of v3 data files: in v3 the aggregated
  // TRAFFIC CONTROL history Array lived at index 27, so a string there is
  // v4 confidence and an Array is the old history.
  function confidenceOf(row) {
    const v = (row.length > IDX_CONFIDENCE) ? row[IDX_CONFIDENCE] : null;
    return (typeof v === "string" && v) ? v : null;
  }
  function tcHistoryOf(row) {
    if (row.length > IDX_TC_HISTORY && Array.isArray(row[IDX_TC_HISTORY])) return row[IDX_TC_HISTORY];
    const legacy = (row.length > IDX_CONFIDENCE) ? row[IDX_CONFIDENCE] : null;
    return Array.isArray(legacy) ? legacy : null;
  }
  function visibleMinutesOf(row) {
    const v = (row.length > IDX_VISIBLE_MIN) ? row[IDX_VISIBLE_MIN] : null;
    return (typeof v === "number" && isFinite(v) && v >= 0) ? v : null;
  }
  function isInFeedNow(row) {
    return row.length > IDX_ACTIVE && row[IDX_ACTIVE] === 1;
  }
  function obsCountOf(row) {
    const v = (row.length > IDX_OBS_COUNT) ? row[IDX_OBS_COUNT] : null;
    return (typeof v === "number" && isFinite(v) && v > 0) ? v : null;
  }
  // v5: duplicate feed listings of this same event, folded at export time
  // into this (primary) row: [[cause, reported], ...].
  function episodeExtrasOf(row) {
    const v = (row.length > IDX_EPISODE) ? row[IDX_EPISODE] : null;
    return Array.isArray(v) && v.length ? v : null;
  }

  function fmtVisibleMin(min) {
    if (min < 5) return "a few minutes";
    if (min < 90) return "~" + min + " min";
    const h = Math.floor(min / 60), m = min % 60;
    if (min < 1440) return "~" + h + " h" + (m >= 10 ? " " + m + " min" : "");
    const d = Math.round(min / 1440);
    return "~" + d + " day" + (d === 1 ? "" : "s");
  }

  // How each mapped point was placed, mirroring the collector's labels.
  const CONFIDENCE_LEVELS = {
    precise:      { label: "Precise address",           color: "#16a34a", desc: "Matched to an exact address or building." },
    intersection: { label: "Intersection",              color: "#0d9488", desc: "Pinned to a named road crossing." },
    place:        { label: "Known place",               color: "#2563eb", desc: "A named establishment — school, business, park…" },
    cached:       { label: "Reused earlier geocode",    color: "#9333ea", desc: "Same address as an earlier incident, so its already-validated coordinates were reused." },
    approximate:  { label: "Approximate road location", color: "#ca8a04", desc: "Only a general road match — treat the exact spot with caution." }
  };
  const CONFIDENCE_ORDER = ["precise", "intersection", "place", "cached", "approximate"];

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
    const occurrences = incidentCount(row);
    // Show the exact incident type from the source feed; the badge colour still
    // encodes the broad category. Fall back to the category label if the feed
    // gave us no cause string.
    const causeText = normalizeText(row[IDX_CAUSE]) || cat.label;

    const rows = [];
    const reported = normalizeText(row[IDX_REPORTED]);
    if (reported) rows.push("<div>Reported: <b>" + esc(reported) + "</b></div>");
    const assist = normalizeText(row[IDX_ASSIST]);
    if (assist) rows.push("<div>Assisting: <b>" + esc(assist) + "</b></div>");
    if (occurrences > 1) {
      rows.push("<div>Occurrences at this spot: <b>" + occurrences.toLocaleString() + "</b></div>");
      // Recurrence pattern: last 14 calendar days from the exported per-day
      // history, as a tiny sparkline (routine school traffic control shows
      // up as a school-day comb).
      const hist = tcHistoryOf(row);
      if (hist && hist.length) {
        const byDay = {};
        let histMax = 1;
        for (const h of hist) { byDay[h[0]] = h[1]; if (h[1] > histMax) histMax = h[1]; }
        const BLOCKS = "▁▂▃▄▅▆▇█";
        let spark = "", activeDays = 0;
        for (let i = 13; i >= 0; i--) {
          const d = new Date(Date.now() - i * 86400000);
          const key = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
          const v = byDay[key] || 0;
          if (v > 0) activeDays++;
          spark += v > 0 ? BLOCKS[Math.min(7, Math.round((v / histMax) * 7))] : "·";
        }
        rows.push("<div>Last 14 days: <b style='font-family:ui-monospace,Menlo,monospace;letter-spacing:1px;color:var(--accent)'>" +
          spark + "</b> <span style='color:var(--text-3)'>(" + activeDays + " active day" + (activeDays === 1 ? "" : "s") + ")</span></div>");
      }
      if (isRoutineTC(row)) {
        rows.push("<div style='font-size:10.5px;color:var(--text-3);margin-top:2px;'>Routine recurring activity " +
          "(e.g. daily school traffic control) — every occurrence is archived, but it's grouped into one point " +
          "so it can't crowd out real incidents.</div>");
      }
    }
    const hw = normalizeText(row.length > IDX_HIGHWAY ? row[IDX_HIGHWAY] : "");
    if (hw) rows.push("<div>Road class: <b>" + esc(hw.replace(/_/g, " ")) + "</b></div>");

    const conf = confidenceOf(row);
    if (conf && CONFIDENCE_LEVELS[conf]) {
      const cl = CONFIDENCE_LEVELS[conf];
      rows.push("<div title='" + esc(cl.desc) + "'>Location precision: <b style='color:" + cl.color + "'>" + esc(cl.label) + "</b></div>");
    }

    // Feed visibility: how long this incident stayed listed on the public
    // 911 feed. Labeled explicitly, because it is NOT response time and NOT
    // clearance time — only how long our collector kept seeing it.
    const visMin = visibleMinutesOf(row);
    const obsN = obsCountOf(row);
    const feedNote = "<div style='font-size:10px;color:var(--text-3)'>Time visible in the public feed — not response or clearance time.</div>";
    const checksTxt = obsN ? " <span style='color:var(--text-3)'>(" + obsN + " feed check" + (obsN === 1 ? "" : "s") + ")</span>" : "";
    if (isInFeedNow(row)) {
      rows.push("<div>In the public feed <b style='color:var(--good)'>now</b>" +
        (visMin != null ? " · visible for approximately <b>" + esc(fmtVisibleMin(visMin)) + "</b> so far" : "") +
        checksTxt + "</div>" + feedNote);
    } else if (visMin != null && !isRoutineTC(row)) {
      rows.push("<div>Was visible in the public feed for approximately <b>" + esc(fmtVisibleMin(visMin)) + "</b>" +
        checksTxt + "</div>" + feedNote);
    }

    // One real-world event, several feed entries (reclassification, a
    // hit-and-run also logged as an accident type…) — folded into this
    // incident, with the other entries disclosed here.
    const epExtras = episodeExtrasOf(row);
    if (epExtras) {
      const parts = epExtras.map(function (x) {
        const c = normalizeText(x && x[0]);
        const t = normalizeText(x && x[1]);
        return "<b>" + esc(titleCase(c)) + "</b>" +
          (t ? " <span style='color:var(--text-3)'>(" + esc(t) + ")</span>" : "");
      });
      rows.push("<div>Also logged in the feed as: " + parts.join(", ") + "</div>" +
        "<div style='font-size:10px;color:var(--text-3)'>Duplicate feed entries for the same event are counted once.</div>");
    }

    const ctx = [];
    const dowNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const dowVal = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    const hourVal = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    if (dowVal !== null && dowNames[dowVal]) ctx.push(dowNames[dowVal]);
    if (hourVal !== null) ctx.push(String(hourVal).padStart(2, "0") + ":00 hour");
    if (row.length > IDX_SCHOOL_DAY && row[IDX_SCHOOL_DAY] === 1) ctx.push("school day");
    if (ctx.length) rows.push("<div>Context: <b>" + esc(ctx.join(" · ")) + "</b></div>");

    // Location history: how active is this ~100 m grid cell across the whole
    // dataset, regardless of current filters.
    const hist = locHistory.get(locHistoryKey(row));
    if (hist && hist.count > 1) {
      rows.push("<div>This spot: <b>" + hist.count + " incidents on record</b>" +
        (hist.firstMs ? " <span style='color:var(--text-3)'>· first " + esc(relTime(new Date(hist.firstMs))) + "</span>" : "") + "</div>");
    }

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
    const waze = "https://waze.com/ul?ll=" + lat + "%2C" + lng + "&navigate=yes";

    return "<div class='pc' style='--cat:" + cat.color + "'>" +
      "<div class='pc-head'><span class='pc-badge'>" + esc(titleCase(causeText)) + "</span>" +
      "<span class='pc-time'>" + esc(relTime(dt)) + "</span></div>" +
      "<div class='pc-title'>" + esc(titleCase(row[IDX_LOCATION])) + "</div>" +
      "<div class='pc-rows'>" + rows.join("") + "</div>" +
      alertLine + wxHtml +
      "<div class='pc-links'>" +
      "<a href='" + gmaps + "' target='_blank' rel='noopener'>Maps</a>" +
      "<a href='" + sview + "' target='_blank' rel='noopener'>Street View</a>" +
      "<a href='" + waze + "' target='_blank' rel='noopener'>Waze</a>" +
      "</div></div>";
  }

  // Popup content for one or many incidents at (or near) a location.
  // Multiple incidents open as a scrollable list — newest first, workable
  // even with 50+ entries — and tapping one shows its full card with
  // back / prev / next controls.
  function createIncidentPopup(rows, placeLabel) {
    const container = document.createElement("div");
    if (HAS_LEAFLET && L.DomEvent) {
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.disableScrollPropagation(container);
    }

    if (rows.length === 1) {
      container.innerHTML = popupHtml(rows[0]);
      return container;
    }

    const sorted = rows.slice().sort(function (a, b) {
      const da = bestRowDate(a), db = bestRowDate(b);
      return (db ? db.getTime() : 0) - (da ? da.getTime() : 0);
    });
    // Show each item's street when the group spans multiple locations
    // (rounded cells can merge nearby addresses).
    const locs = new Set(sorted.map(function (r) { return String(r[IDX_LOCATION] || "").trim().toUpperCase(); }));
    const showLoc = locs.size > 1;
    const nowMs = Date.now();
    let idx = 0;

    function renderList() {
      let html = "<div class='pc-list-head'>" + sorted.length + " incidents " + esc(placeLabel || "at this location") + "</div>" +
        "<div class='pc-list-sub'>Newest first — tap one for full details</div>" +
        "<div class='pc-list' role='list'>";
      for (let i = 0; i < sorted.length; i++) {
        const row = sorted[i];
        const cat = categoryOf(row[IDX_CAUSE]);
        const dt = bestRowDate(row);
        const cause = normalizeText(row[IDX_CAUSE]) || cat.label;
        html += "<button class='pc-item' type='button' role='listitem' data-i='" + i + "' style='--cat:" + cat.color + "'>" +
          "<span class='dot'></span><span class='pc-item-body'>" +
          "<span class='pc-item-cause'>" + esc(titleCase(cause)) + "</span>" +
          "<span class='pc-item-sub'>" +
          (showLoc ? "<span>" + esc(titleCase(row[IDX_LOCATION])) + "</span>" : "") +
          (dt ? "<span>" + esc(relTime(dt, nowMs)) + "</span>" : "") +
          "</span></span><span class='chev'>&rsaquo;</span></button>";
      }
      html += "</div>";
      container.innerHTML = html;
      container.querySelectorAll(".pc-item").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          idx = parseInt(btn.getAttribute("data-i"), 10) || 0;
          renderDetail();
        });
      });
    }

    function renderDetail() {
      container.innerHTML =
        "<div class='pc-nav'>" +
        "<button class='pc-back' type='button'>&lsaquo; All " + sorted.length + "</button>" +
        "<span class='pc-nav-count'>" + (idx + 1) + " of " + sorted.length + "</span>" +
        "<span class='pc-nav-arrows'>" +
        "<button class='pc-prev' type='button' aria-label='Previous incident'>&larr;</button>" +
        "<button class='pc-next' type='button' aria-label='Next incident'>&rarr;</button>" +
        "</span></div>" +
        popupHtml(sorted[idx]);
      container.querySelector(".pc-back").addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation(); renderList();
      });
      const prev = container.querySelector(".pc-prev");
      const next = container.querySelector(".pc-next");
      prev.disabled = idx <= 0;
      next.disabled = idx >= sorted.length - 1;
      prev.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); if (idx > 0) { idx--; renderDetail(); } });
      next.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); if (idx < sorted.length - 1) { idx++; renderDetail(); } });
      relayout();
    }

    // Content swaps (list ↔ detail, prev/next) change the card's size, but
    // Leaflet only measures a popup when it OPENS — without a re-measure the
    // detail card keeps the list's dimensions, overflows its bubble, and can
    // sit clipped against a screen edge. Re-measure and re-center after
    // every swap. The initial renderList happens before the popup exists
    // (container not in the DOM yet), so it's skipped by the guard.
    function relayout() {
      if (!container.isConnected || !activePopup) return;
      if (activePopup.update) { try { activePopup.update(); } catch (e) {} }
      requestAnimationFrame(function () { positionActivePopup(activePopup); });
    }

    renderList();
    return container;
  }

  /* ═══════════════════════ filters ═══════════════════════ */
  // The feed's free-text "assisting" field names one or more responding
  // agencies, but the source never formats it consistently: order varies
  // ("POLICE FIRE" vs "FIRE POLICE"), words get glued ("SHERIFFFIRE"), and
  // abbreviations appear (LPD/LPSO/LFD). Rather than list every raw string,
  // we reduce each incident to the canonical agencies it mentions by matching
  // these patterns (case-insensitive, no global flag so .test is stateless).
  // Each agency owns a bit so an incident's agencies fit in one integer mask.
  const AGENCIES = [
    { id: "police",  label: "Police",  re: /POLICE|\bLPD\b/ },
    { id: "sheriff", label: "Sheriff", re: /SHERIFF|\bLPSO\b/ },
    { id: "fire",    label: "Fire",    re: /FIRE|\bLFD\b/ },
    { id: "ems",     label: "EMS",     re: /\bEMS\b|AMBULANCE|ACADIAN|\bMEDIC/ }
  ];
  // "No agency listed": the assisting field named none of the known
  // agencies (usually it's blank). Its own bit makes it filterable.
  const AGENCY_NONE = { id: "none", label: "No agency listed" };
  const AGENCY_ALL = AGENCIES.concat([AGENCY_NONE]);
  const AGENCY_BY_ID = {};
  AGENCY_ALL.forEach(function (a, i) { a.bit = 1 << i; AGENCY_BY_ID[a.id] = a; });

  const state = {
    cats: new Set(CATEGORIES.map(function (c) { return c.id; })),  // enabled category ids
    range: "24h",                      // default landing view; "all" = every incident
    agencies: new Set(),               // selected agency ids; empty = any (no filter)
    exactHour: null,                   // 0-23 from the hour chart / heatmap; null = off
    corridorId: null                   // canonical corridor id from the leaderboard; null = off
  };

  // Bitmask of the agencies mentioned in a row's assisting text (cached on the
  // row object the first time it's needed, since INCIDENTS is large).
  function agencyMaskOf(row) {
    if (row.__agmask !== undefined) return row.__agmask;
    const s = String(row[IDX_ASSIST] || "").toUpperCase();
    let m = 0;
    for (const a of AGENCIES) { if (a.re.test(s)) m |= a.bit; }
    if (!m) m = AGENCY_NONE.bit;
    row.__agmask = m;
    return m;
  }

  // Combined bitmask of the currently selected agencies (0 = filter inactive).
  function selectedAgencyMask() {
    let m = 0;
    state.agencies.forEach(function (id) { if (AGENCY_BY_ID[id]) m |= AGENCY_BY_ID[id].bit; });
    return m;
  }

  // Canonical corridors for a row. The backend exports them at IDX_CORRIDORS;
  // older data files won't have the field, so fall back to a rough in-browser
  // normalizer (split intersections, strip house numbers and NB/SB tags).
  const CITY_STATE_TOKENS = { LAFAYETTE: 1, BROUSSARD: 1, YOUNGSVILLE: 1, SCOTT: 1, CARENCRO: 1, DUSON: 1, MILTON: 1, MAURICE: 1, LA: 1, LOUISIANA: 1, USA: 1, PARISH: 1 };

  function corridorsOfJS(location) {
    const raw = String(location || "").trim().toUpperCase();
    if (!raw) return [];
    const out = [];
    raw.split(/\s+(?:AT|&+|\/|@|NEAR|AND)\s+|\s*[&\/]\s*/).forEach(function (part) {
      let s = part.replace(/[.,;:()"']/g, " ").replace(/\s+/g, " ").trim();
      s = s.replace(/^\d+[A-Z]?(\s*-\s*\d+[A-Z]?)?\s+(BLK\s+(OF\s+)?|BLOCK\s+(OF\s+)?)?/, "");
      s = s.replace(/\b(NB|SB|EB|WB|NBD|SBD|EBD|WBD)\b/g, " ").replace(/\s+/g, " ").trim();
      // Trailing municipality/state tags are not roads ("MOSS ST LAFAYETTE
      // LA" groups as "MOSS ST"); a city-only location yields no corridor.
      const toks = s.split(" ");
      while (toks.length && CITY_STATE_TOKENS[toks[toks.length - 1]]) toks.pop();
      s = toks.join(" ");
      // Canonicalize highway ids so "I-49 N" / "I 49" / "US HWY 90" group.
      let hw = s.match(/^(?:I|INT|INTERSTATE)[\s.-]*(\d{1,3})(?:\s+[NSEW])?$/);
      if (hw) s = "I-" + hw[1];
      else if ((hw = s.match(/^US[\s.-]*(?:HWY[\s.-]*)?(\d{1,3})(?:\s+[NSEW])?$/))) s = "US " + hw[1];
      else if ((hw = s.match(/^LA[\s.-]*(?:HWY[\s.-]*)?(\d{1,4})(?:\s+[NSEW])?$/))) s = "LA " + hw[1];
      if (!s || /^\d+[A-Z]?(\s*-\s*\d+[A-Z]?)?$/.test(s) || /^(N|S|E|W|NE|NW|SE|SW)$/.test(s)) return;
      if (out.indexOf(s) === -1) out.push(s);
    });
    return out;
  }

  function corridorsOf(row) {
    if (row.__cors !== undefined) return row.__cors;
    const c = (row.length > IDX_CORRIDORS && Array.isArray(row[IDX_CORRIDORS]))
      ? row[IDX_CORRIDORS]
      : corridorsOfJS(row[IDX_LOCATION]);
    row.__cors = c;
    return c;
  }

  function matchesCorridor(row, cid) {
    if (!cid) return true;
    return corridorsOf(row).indexOf(cid) !== -1;
  }

  // Optional data-quality gate: hide points whose geocode was only an
  // approximate road match, so hot spots and corridor rankings can be
  // computed from trusted locations only.
  function matchesConfidence(row, excludeLowConf) {
    if (!excludeLowConf) return true;
    return confidenceOf(row) !== "approximate";
  }

  // Local hour of a row: prefer the stored enrichment hour, fall back to the
  // reported text. Used by the exact-hour filter and the hour charts alike so
  // a bar's count always equals what clicking it selects.
  function rowHourOf(row) {
    const h = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? parseInt(row[IDX_HOUR], 10) : NaN;
    if (!isNaN(h) && h >= 0 && h < 24) return h;
    const pr = parseReported(row[IDX_REPORTED]);
    return (pr && Number.isInteger(pr.hh) && pr.hh >= 0 && pr.hh < 24) ? pr.hh : null;
  }

  function matchesExactHour(row, h) {
    if (h == null) return true;
    return rowHourOf(row) === h;
  }

  const CAUSE_GROUPS = [
    { id: "accident", label: "Accidents", keywords: ["ACCIDENT", "CRASH", "COLLISION", "WRECK", "MVA", "MVC"] },
    { id: "fire", label: "Fire", keywords: ["FIRE", "SMOKE"] },
    { id: "hazard", label: "Hazards", keywords: ["HAZARD", "SPILL", "DEBRIS", "OBSTRUCT", "FLOOD", "TREE", "POWER LINE", "LINE DOWN", "ANIMAL"] },
    { id: "signal", label: "Signals & traffic control", keywords: ["SIGNAL", "TRAFFIC CONTROL", "TRAFFIC LIGHT", "MALFUNCTION"] },
    { id: "stalled", label: "Stalled / disabled", keywords: ["STALL", "DISABLED", "STUCK", "ABANDONED"] },
    { id: "medical", label: "Medical / rescue", keywords: ["MEDICAL", "RESCUE", "INJURY", "AMBULANCE", "OVERDOSE"] },
    { id: "other", label: "Other", keywords: [] }
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
    if (group.id === "other") {
      // "Other" = matches none of the named groups.
      return !CAUSE_GROUPS.some(function (g) {
        return g.keywords.some(function (kw) { return causeNorm.indexOf(kw) !== -1; });
      });
    }
    return group.keywords.some(function (kw) { return causeNorm.indexOf(kw) !== -1; });
  }

  function matchesCats(row) {
    if (state.cats.size >= CATEGORIES.length) return true;
    return state.cats.has(categoryOf(row[IDX_CAUSE]).id);
  }

  function matchesRange(row, range, nowMs) {
    if (!range || range === "all") return true;
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

  // From/To calendar range. input[type=date] values are parsed as LOCAL
  // days (new Date("2026-07-13") would be UTC midnight and shift a day).
  function parseDateInput(v, endOfDay) {
    if (!v) return null;
    const p = String(v).split("-");
    if (p.length !== 3) return null;
    const d = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
    if (isNaN(d.getTime())) return null;
    if (endOfDay) d.setHours(23, 59, 59, 999);
    return d.getTime();
  }

  function matchesCustomDateRange(row, f) {
    if (f.dateFromMs == null && f.dateToMs == null) return true;
    const dt = bestRowDate(row);
    if (!dt) return false;
    const ms = dt.getTime();
    if (f.dateFromMs != null && ms < f.dateFromMs) return false;
    if (f.dateToMs != null && ms > f.dateToMs) return false;
    return true;
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

  function matchesAgency(row, mask) {
    if (!mask) return true;                    // no agency selected → no filter
    return (agencyMaskOf(row) & mask) !== 0;   // row mentions any selected agency
  }

  function matchesHoliday(row, checked) {
    if (!checked) return true;
    const v = (row.length > IDX_HOLIDAY) ? row[IDX_HOLIDAY] : null;
    if (v === 1 || v === true) return true;
    if (v === 0 || v === false) return false;
    // Unknown (older/CSV rows without the flag): fall back to the same
    // holiday heuristic the backend uses, from the reported date.
    const pr = parseReported(row[IDX_REPORTED]);
    return pr && pr.dt ? isHolidayJS(pr.dt) : false;
  }

  // Approximate Lafayette (30.2°N) sunrise/sunset in local Central time, by
  // month. A day/night FILTER doesn't need astronomical precision, and a
  // fixed table is TZ/DST-proof: reported times are already local Central.
  // [sunriseMinutes, sunsetMinutes] from local midnight.
  const SUN_TABLE = [
    [423, 1042], [400, 1075], [447, 1155], [400, 1177], [369, 1200], [361, 1215],
    [372, 1216], [393, 1188], [412, 1147], [432, 1108], [388, 1039], [412, 1024]
  ];
  function isDaylight(pr) {
    if (!pr || pr.dt == null || pr.hh == null) return null;  // no time → unknown
    const m = pr.dt.getMonth();
    const mins = pr.hh * 60 + (pr.dt.getMinutes ? pr.dt.getMinutes() : 0);
    const band = SUN_TABLE[m] || SUN_TABLE[0];
    return mins >= band[0] && mins < band[1];
  }
  function matchesLight(row, light) {
    if (!light || light === "any") return true;
    const pr = parseReported(row[IDX_REPORTED]);
    const day = isDaylight(pr);
    if (day == null) return false;
    return light === "day" ? day : !day;
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
      dateFrom: (els.dateFrom.value || "").trim(),
      dateTo: (els.dateTo.value || "").trim(),
      dateFromMs: parseDateInput((els.dateFrom.value || "").trim(), false),
      dateToMs: parseDateInput((els.dateTo.value || "").trim(), true),
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
      excludeLowConf: !!els.chkExcludeLowConf.checked,
      roadSearch: (els.roadSearch.value || "").trim(),
      chkFlood: !!els.chkFloodWarning.checked,
      chkStorm: !!els.chkThunderstormWarning.checked,
      chkTornado: !!els.chkTornadoWatch.checked,
      agencyMask: selectedAgencyMask(),
      exactHour: state.exactHour,
      corridorId: state.corridorId,
      holiday: !!els.chkHoliday.checked,
      light: (els.lightSelect.value || "any").trim(),
      range: state.range
    };
  }

  function countActiveFilters(f) {
    let n = 0;
    if (f.roadSearch) n++;
    if (f.cause !== "__ALL__") n++;
    if (f.causeGroup !== "__ALL__") n++;
    if (f.mm || f.dd || f.yy) n++;
    if (f.dateFrom || f.dateTo) n++;
    if (f.dayType !== "all") n++;
    if (f.timeBlock !== "all") n++;
    if (f.dowValue !== "all") n++;
    if (f.roadType !== "any") n++;
    if (f.excludeLowConf) n++;
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
    if (f.agencyMask) n++;
    if (f.exactHour != null) n++;
    if (f.corridorId) n++;
    if (f.holiday) n++;
    if (f.light !== "any") n++;
    if (state.cats.size < CATEGORIES.length) n++;
    return n;
  }

  // The one incident-filtering function. `excl` (optional) names dimensions
  // to SKIP so a control can count its own alternatives without filtering
  // them away — e.g. the agency chips count with {agency:1}, the hour chart
  // with {hour:1}. Skippable: cats, range, hour (exact hour AND broad time
  // block), dow, agency, corridor, bounds.
  function filteredIncidents(f, mapObj, excl) {
    const out = [];
    const x = excl || null;
    const bounds = (f.inViewOnly && mapObj && !(x && x.bounds)) ? mapObj.getBounds() : null;
    const nowMs = Date.now();
    for (const row of INCIDENTS) {
      if (!(x && x.cats) && !matchesCats(row)) continue;
      if (!matchesCauseGroup(row, f.causeGroup)) continue;
      if (!matchesCause(row, f.cause)) continue;
      if (!(x && x.range) && !matchesRange(row, f.range, nowMs)) continue;
      if (!matchesDateFilter(row, f)) continue;
      if (!matchesCustomDateRange(row, f)) continue;
      if (!matchesDayType(row, f.dayType)) continue;
      if (!(x && x.hour) && !matchesExactHour(row, f.exactHour)) continue;
      if (!(x && x.hour) && !matchesTimeBlock(row, f.timeBlock)) continue;
      if (!matchesWeather(row, f)) continue;
      if (!matchesRushHour(row, f.rushHour)) continue;
      if (!matchesSchoolDay(row, f.schoolDay)) continue;
      if (!(x && x.dow) && !matchesDow(row, f.dowValue)) continue;
      if (!matchesRoadType(row, f.roadType)) continue;
      if (!matchesConfidence(row, f.excludeLowConf)) continue;
      if (!matchesRoadSearch(row, f.roadSearch)) continue;
      if (!(x && x.corridor) && !matchesCorridor(row, f.corridorId)) continue;
      if (!matchesNwsAlerts(row, f)) continue;
      if (!(x && x.agency) && !matchesAgency(row, f.agencyMask)) continue;
      if (!matchesHoliday(row, f.holiday)) continue;
      if (!matchesLight(row, f.light)) continue;
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
    ["df", "dateFrom", ""], ["dt2", "dateTo", ""],
    ["dow", "dowSelect", "all"], ["dt", "dayTypeSelect", "all"], ["tb", "timeBlockSelect", "all"],
    ["rt", "roadTypeSelect", "any"],
    ["tmp", "tempBand", "any"], ["pp", "precipBand", "any"], ["pa", "precipAmountBand", "any"],
    ["wnd", "windBand", "any"], ["vis", "visBand", "any"], ["cld", "cloudBand", "any"],
    ["lt", "lightSelect", "any"]
  ];
  const HASH_CHECKS = [
    ["rush", "chkRushHour"], ["school", "chkSchoolDay"], ["wo", "chkWeatherOnly"],
    ["flood", "chkFloodWarning"], ["storm", "chkThunderstormWarning"], ["tor", "chkTornadoWatch"],
    ["hol", "chkHoliday"], ["heat", "chkHeat"], ["hot", "chkHotSpots"],
    ["xlc", "chkExcludeLowConf"]
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
    if (state.range && state.range !== "24h") p.set("range", state.range);
    if (state.cats.size < CATEGORIES.length) {
      p.set("cats", Array.from(state.cats).join("."));
    }
    if (state.agencies.size) p.set("ag", Array.from(state.agencies).join("."));
    if (state.exactHour != null) p.set("h", String(state.exactHour));
    if (state.corridorId) p.set("cid", state.corridorId);
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
    if (p.has("ag")) {
      const ids = p.get("ag").split(".").filter(function (id) { return AGENCY_BY_ID[id]; });
      state.agencies = new Set(ids);
    } else {
      state.agencies = new Set();
    }
    if (p.has("h")) {
      const hv = parseInt(p.get("h"), 10);
      state.exactHour = (hv >= 0 && hv <= 23) ? hv : null;
      if (state.exactHour != null) els.timeBlockSelect.value = "all";
    } else {
      state.exactHour = null;
    }
    state.corridorId = p.has("cid") ? (p.get("cid") || null) : null;
    if (els.roadSearch.value) els.roadSearchClear.classList.add("show");
    hashApplying = false;
  }

  // Chart clicks push one history entry BEFORE changing state, so the
  // browser's Back button returns to the previous view (renderAll then
  // rewrites the new state onto the new entry via replaceState).
  function pushHashEntry() {
    if (hashApplying) return;
    try { history.pushState(null, "", location.pathname + location.search + location.hash); } catch (e) {}
  }

  function setExactHour(h) {
    pushHashEntry();
    state.exactHour = (state.exactHour === h) ? null : h;
    if (state.exactHour != null) els.timeBlockSelect.value = "all";
    toast(state.exactHour != null
      ? "Hour: " + fmtHour(h) + "\u2013" + fmtHour((h + 1) % 24)
      : "Hour filter cleared");
    scheduleRender(0);
  }

  function setCorridor(cid) {
    pushHashEntry();
    state.corridorId = (state.corridorId === cid) ? null : cid;
    toast(state.corridorId ? ("Corridor: " + titleCase(cid)) : "Corridor filter cleared");
    scheduleRender(0);
  }

  /* ═══════════════════════ stat tiles / status ═══════════════════════ */
  // Animate a numeric element from its current value to the target.
  function countUp(el, target) {
    const from = parseInt(String(el.textContent).replace(/[^0-9]/g, ""), 10) || 0;
    if (from === target) { el.textContent = target.toLocaleString(); return; }
    if (REDUCED_MOTION || Math.abs(target - from) < 2) { el.textContent = target.toLocaleString(); return; }
    const t0 = performance.now(), dur = 650;
    if (el.__countRaf) cancelAnimationFrame(el.__countRaf);
    function step(now) {
      const t = Math.min(1, (now - t0) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(from + (target - from) * eased).toLocaleString();
      if (t < 1) el.__countRaf = requestAnimationFrame(step);
      else el.__countRaf = null;
    }
    el.__countRaf = requestAnimationFrame(step);
  }

  function renderStatTiles() {
    // Facet-aware: each tile counts incidents matching every OTHER filter,
    // so picking one range never zeroes out its alternatives.
    const base = filteredIncidents(currentFilterObj(), map, { range: 1 });
    const nowMs = Date.now();
    let last24h = 0, week = 0, month = 0;
    for (const row of base) {
      const dt = bestRowDate(row);
      if (!dt) continue;
      const age = nowMs - dt.getTime();
      if (age < 0) continue;
      if (age <= 86400000) last24h++;
      if (age <= 7 * 86400000) week++;
      if (age <= 30 * 86400000) month++;
    }
    countUp(els.tile24h.querySelector("b"), last24h);
    els.tile24h.classList.toggle("hot", last24h > 0);
    countUp(els.tileWeekVal, week);
    countUp(els.tileMonthVal, month);
    countUp(els.tileTotalVal, base.length);
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

  // Second-granular "ago" for the data-refresh note.
  function refreshedAgoText() {
    const s = Math.max(0, Math.floor((Date.now() - lastRefreshMs) / 1000));
    if (s < 10) return "refreshed just now";
    if (s < 60) return "refreshed " + s + " s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return "refreshed " + m + " min ago";
    return "refreshed " + Math.floor(m / 60) + " h ago";
  }

  let dataMeta = null;  // parsed traffic_meta.json, when the host provides it
  const PAGE_GENERATED_AT = (function () {
    const m = document.querySelector('meta[name="generated-at"]');
    return m ? m.getAttribute("content") : "";
  })();

  function agoShort(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    if (s < 10) return "just now";
    if (s < 60) return s + " s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " min ago";
    const h = Math.floor(m / 60);
    if (h < 48) return h + " h ago";
    return Math.floor(h / 24) + " d ago";
  }

  function renderStatus() {
    const newest = newestDataDate();
    // "Data generated" (backend render time) is a different fact from
    // "browser last checked" — show both when known.
    let gen = "";
    const genIso = (dataMeta && dataMeta.generated_at) || PAGE_GENERATED_AT;
    if (genIso) {
      const g = new Date(genIso);
      if (!isNaN(g.getTime())) gen = " · data " + agoShort(Date.now() - g.getTime());
    }
    const refreshed = gen + " · checked " + agoShort(Date.now() - lastRefreshMs);
    if (!INCIDENTS.length) {
      els.statusText.textContent = "No incident data loaded yet." + refreshed;
    } else if (newest) {
      els.statusText.textContent = "Latest incident " + relTime(newest) + refreshed;
    } else {
      els.statusText.textContent = INCIDENTS.length.toLocaleString() + " incidents loaded" + refreshed;
    }
    if (UNLOCATED_COUNT > 0) {
      els.unlocChip.style.display = "";
      els.unlocChip.textContent = UNLOCATED_COUNT + " locating…";
      els.unlocChip.title = UNLOCATED_COUNT + (UNLOCATED_COUNT === 1 ? " incident is" : " incidents are") +
        " queued for geocoding and will appear on the map as the daily API budget allows" +
        (UNMAPPABLE_COUNT > 0
          ? ". " + UNMAPPABLE_COUNT + (UNMAPPABLE_COUNT === 1 ? " older incident" : " older incidents") + " could not be located and are excluded."
          : ".");
    } else {
      els.unlocChip.style.display = "none";
    }
  }

  /* ═══════════════════════ legend chips ═══════════════════════ */
  function renderLegend() {
    // Presence comes from the whole archive (a category never disappears
    // just because current filters exclude it); displayed counts are
    // facet-aware — every filter applies EXCEPT the category toggles.
    const allTime = {};
    for (const row of INCIDENTS) {
      const id = categoryOf(row[IDX_CAUSE]).id;
      allTime[id] = (allTime[id] || 0) + 1;
    }
    const facet = {};
    for (const row of filteredIncidents(currentFilterObj(), map, { cats: 1 })) {
      const id = categoryOf(row[IDX_CAUSE]).id;
      facet[id] = (facet[id] || 0) + 1;
    }
    els.legendChips.innerHTML = "";
    for (const cat of CATEGORIES) {
      if (!allTime[cat.id]) continue;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "legend-chip " + (state.cats.has(cat.id) ? "on" : "off");
      btn.style.setProperty("--cat", cat.color);
      btn.setAttribute("aria-pressed", state.cats.has(cat.id) ? "true" : "false");
      btn.title = "Toggle " + cat.label.toLowerCase() + " (long-press / double-click to isolate)";
      btn.innerHTML = "<span class='dot'></span>" + esc(cat.label) + " <span class='n'>" + (facet[cat.id] || 0).toLocaleString() + "</span>";
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
    // Trailing "Legend" chip: the one obvious place that explains every
    // color, shape and badge on the map.
    const helpBtn = document.createElement("button");
    helpBtn.type = "button";
    helpBtn.className = "legend-chip legend-help";
    helpBtn.title = "What do the marker colors, shapes and badges mean?";
    helpBtn.setAttribute("aria-haspopup", "dialog");
    helpBtn.innerHTML = "<span class='dot'></span>Legend";
    helpBtn.addEventListener("click", openLegend);
    els.legendChips.appendChild(helpBtn);
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
      bars += "<rect class='" + cls + "' data-i='" + i + "' x='" + x.toFixed(1) + "' y='" + y.toFixed(1) +
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

  // 7×24 hour-by-day heatmap. Cell intensity is count/max; the peak cell is
  // outlined. Rendered as one SVG so it scales with the panel.
  function heatmapSVG(matrix) {
    const W = 360, labelW = 22, gap = 1.5, ch = 10.5, labelH = 11;
    const cw = (W - labelW - 23 * gap) / 24;
    const H = 7 * (ch + gap) - gap + labelH + 3;
    const dayLetters = ["S", "M", "T", "W", "T", "F", "S"];
    const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let max = 0, peakR = -1, peakC = -1;
    for (let r = 0; r < 7; r++) {
      for (let c = 0; c < 24; c++) {
        if (matrix[r][c] > max) { max = matrix[r][c]; peakR = r; peakC = c; }
      }
    }
    let cells = "";
    for (let r = 0; r < 7; r++) {
      const y = r * (ch + gap);
      cells += "<text class='axis-label' x='" + (labelW - 7) + "' y='" + (y + ch - 2.2) + "' text-anchor='middle'>" + dayLetters[r] + "</text>";
      for (let c = 0; c < 24; c++) {
        const x = labelW + c * (cw + gap);
        const v = matrix[r][c];
        const t = max > 0 ? v / max : 0;
        const cls = (r === peakR && c === peakC && max > 0) ? "hm-cell hm-peak" : "hm-cell";
        const fill = v > 0 ? "var(--accent)" : "var(--bar-track)";
        const op = v > 0 ? (0.14 + 0.86 * t).toFixed(2) : "0.35";
        cells += "<rect class='" + cls + "' data-d='" + r + "' data-h='" + c + "' x='" + x.toFixed(1) + "' y='" + y.toFixed(1) +
          "' width='" + cw.toFixed(1) + "' height='" + ch + "' rx='2' fill='" + fill + "' fill-opacity='" + op + "'>" +
          "<title>" + dayNames[r] + " " + fmtHour(c) + ": " + v + " — tap to filter</title></rect>";
      }
    }
    let hourLabels = "";
    for (const spec of [[0, "12a"], [6, "6a"], [12, "12p"], [18, "6p"]]) {
      const x = labelW + spec[0] * (cw + gap) + cw / 2;
      hourLabels += "<text class='axis-label' x='" + x.toFixed(1) + "' y='" + (H - 1) + "' text-anchor='middle'>" + spec[1] + "</text>";
    }
    return {
      svg: "<svg class='chart-svg' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Incidents by hour and day of week'>" + cells + hourLabels + "</svg>",
      peak: max > 0 ? { day: dayNames[peakR], hour: fmtHour(peakC), count: max } : null
    };
  }

  // Archive-wide geocoding coverage + per-point confidence breakdown.
  // Deliberately NOT filter-dependent: it describes the quality of the
  // dataset itself, not of the current selection.
  function renderDataQuality() {
    let located = 0;
    const byConf = { precise: 0, intersection: 0, place: 0, cached: 0, approximate: 0 };
    let untracked = 0;
    for (const row of INCIDENTS) {
      located += incidentCount(row);
      const c = confidenceOf(row);
      if (c && byConf[c] != null) byConf[c] += 1;
      else untracked += 1;
    }
    const total = located + UNLOCATED_COUNT + UNMAPPABLE_COUNT;
    if (!total) {
      els.dataQuality.innerHTML = "<span class='rates-no-data'>Waiting for data…</span>";
      els.dataQualitySub.textContent = "";
      return;
    }
    const pct = Math.round((located / total) * 100);
    els.dataQualitySub.textContent = pct + "% located";
    let html = "<div class='mix-bar'>";
    html += "<span class='mix-seg' style='flex:0 0 " + ((located / total) * 100).toFixed(2) + "%;background:var(--good)' title='Located: " + located.toLocaleString() + "'></span>";
    if (UNLOCATED_COUNT > 0) html += "<span class='mix-seg' style='flex:0 0 " + ((UNLOCATED_COUNT / total) * 100).toFixed(2) + "%;background:#ca8a04' title='Still locating: " + UNLOCATED_COUNT.toLocaleString() + "'></span>";
    if (UNMAPPABLE_COUNT > 0) html += "<span class='mix-seg' style='flex:0 0 " + ((UNMAPPABLE_COUNT / total) * 100).toFixed(2) + "%;background:var(--text-3)' title='Could not be located: " + UNMAPPABLE_COUNT.toLocaleString() + "'></span>";
    html += "</div>";
    html += "<div class='chart-note'>Geocoding coverage: <b>" + located.toLocaleString() + "</b> of " +
      total.toLocaleString() + " incidents placed on the map (" + pct + "%)." +
      (UNLOCATED_COUNT > 0 ? " <b>" + UNLOCATED_COUNT.toLocaleString() + "</b> still locating." : "") +
      (UNMAPPABLE_COUNT > 0 ? " <b>" + UNMAPPABLE_COUNT.toLocaleString() + "</b> could not be located and are excluded." : "") +
      "</div>";
    const trackedPts = INCIDENTS.length - untracked;
    if (trackedPts > 0) {
      html += "<div style='margin-top:7px'>";
      for (const key of CONFIDENCE_ORDER) {
        const n = byConf[key];
        if (!n) continue;
        const cl = CONFIDENCE_LEVELS[key];
        const p = Math.round((n / INCIDENTS.length) * 100);
        html += "<div class='dq-row' title='" + esc(cl.desc) + "'>" +
          "<span class='dot' style='background:" + cl.color + "'></span>" +
          "<span class='lbl'>" + esc(cl.label) + "</span>" +
          "<span class='n'>" + n.toLocaleString() + " · " + p + "%</span></div>";
      }
      if (untracked > 0) {
        html += "<div class='dq-row' title='Mapped before precision tracking was added'>" +
          "<span class='dot' style='background:var(--text-3)'></span>" +
          "<span class='lbl'>Recorded before precision tracking</span>" +
          "<span class='n'>" + untracked.toLocaleString() + "</span></div>";
      }
      html += "</div><div class='facet-note'>Per mapped point. Use “Exclude low-confidence locations” " +
        "(Filters → Road) to keep approximate road matches out of the map, hot spots and corridor rankings.</div>";
    }
    els.dataQuality.innerHTML = html;
  }

  function renderCharts(rows, f) {
    f = f || currentFilterObj();
    // Facet-aware sources: each chart is computed WITHOUT its own
    // dimension's filter, so all 24 bars / 7 days / 168 cells stay visible
    // while every other filter still applies. When no time selection is
    // active these are the same rows, so skip the extra passes.
    const hourRows = (f.exactHour != null || f.timeBlock !== "all")
      ? filteredIncidents(f, map, { hour: 1 }) : rows;
    const dowRows = (f.dowValue !== "all")
      ? filteredIncidents(f, map, { dow: 1 }) : rows;
    const hmRows = (f.exactHour != null || f.timeBlock !== "all" || f.dowValue !== "all")
      ? filteredIncidents(f, map, { hour: 1, dow: 1 }) : rows;

    const byHour = new Array(24).fill(0);
    const byDow = new Array(7).fill(0);
    const byMonth = new Array(12).fill(0);
    const matrix = [];
    for (let r = 0; r < 7; r++) matrix.push(new Array(24).fill(0));
    let datedCount = 0;
    for (const row of hourRows) {
      const h = rowHourOf(row);
      if (h != null) byHour[h]++;
    }
    for (const row of dowRows) {
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr && pr.dt) byDow[pr.dt.getDay()]++;
    }
    for (const row of hmRows) {
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr && pr.dt && Number.isInteger(pr.hh) && pr.hh >= 0 && pr.hh < 24) {
        matrix[pr.dt.getDay()][pr.hh]++;
      }
    }
    for (const row of rows) {
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr && pr.dt) {
        datedCount++;
        byMonth[pr.dt.getMonth()]++;
      }
    }

    const span = computeSpanFor(rows.length ? rows : INCIDENTS);
    els.analyticsSummary.innerHTML = "Analyzing <b>" + rows.length.toLocaleString() + "</b> filtered incident" +
      (rows.length === 1 ? "" : "s") +
      (span ? " across <b>" + span.totalDays.toLocaleString() + "</b> days" : "") +
      (datedCount < rows.length ? " (" + datedCount.toLocaleString() + " with timestamps)" : "") +
      ". Tap any bar, cell, or category to filter the map.";

    // Clickable AND keyboard-operable bars (Enter/Space); `selIdx`
    // highlights the active selection and drives aria-pressed.
    function makeBarsInteractive(container, onPick, selIdx, labelFn) {
      container.querySelectorAll(".bar").forEach(function (rect) {
        const i = parseInt(rect.getAttribute("data-i"), 10);
        rect.setAttribute("tabindex", "0");
        rect.setAttribute("role", "button");
        rect.setAttribute("aria-pressed", i === selIdx ? "true" : "false");
        if (labelFn) rect.setAttribute("aria-label", labelFn(i));
        if (i === selIdx) rect.classList.add("sel");
        function pick() { onPick(i); }
        rect.addEventListener("click", pick);
        rect.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
        });
      });
    }

    // Hour chart
    const hourTitles = byHour.map(function (v, h) { return fmtHour(h) + "–" + fmtHour((h + 1) % 24) + ": " + v + " — tap to filter"; });
    els.chartHour.innerHTML = barChartSVG(byHour, {
      labels: [[0, "12a"], [6, "6a"], [12, "12p"], [18, "6p"], [23, "11p"]],
      titles: hourTitles, aria: "Incidents by hour of day"
    });
    const peakH = byHour.indexOf(Math.max.apply(null, byHour));
    if (f.exactHour != null) {
      els.chartHourSub.innerHTML = "filtering " + esc(fmtHour(f.exactHour)) + "\u2013" +
        esc(fmtHour((f.exactHour + 1) % 24)) +
        " <button type='button' class='mini-clear' id='hourClearBtn'>Clear</button>";
      const hcb = document.getElementById("hourClearBtn");
      if (hcb) hcb.addEventListener("click", function () { setExactHour(f.exactHour); });
    } else {
      els.chartHourSub.textContent = byHour[peakH] > 0 ? ("peak " + fmtHour(peakH) + " · " + byHour[peakH]) : "";
    }
    makeBarsInteractive(els.chartHour, setExactHour, f.exactHour, function (i) {
      return fmtHour(i) + " to " + fmtHour((i + 1) % 24) + ", " + byHour[i] +
        " incidents" + (i === f.exactHour ? ", selected" : "");
    });

    // Day-of-week chart — normalized to the number of times each weekday
    // occurs in the filtered range, so 5 weekdays vs 2 weekend days can't
    // skew the picture.
    const dowNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const dowRates = byDow.map(function (n, i) {
      const occ = span ? span.perDow[i] : 0;
      return occ > 0 ? n / occ : 0;
    });
    els.chartDow.innerHTML = barChartSVG(dowRates, {
      labels: dowNames.map(function (nm, i) { return [i, nm]; }),
      titles: dowNames.map(function (nm, i) {
        const occ = span ? span.perDow[i] : 0;
        return nm + ": " + dowRates[i].toFixed(1) + "/day avg (" + byDow[i] + " total over " + occ + " " + nm + "s) — tap to filter";
      }),
      gap: 6, aria: "Average incidents per day of week"
    }) + "<div class='chart-note'>Average per day — normalized so each weekday counts once no matter how many occurred in the range.</div>";
    const peakD = dowRates.indexOf(Math.max.apply(null, dowRates));
    els.chartDowSub.textContent = dowRates[peakD] > 0 ? ("peak " + dowNames[peakD] + " · " + dowRates[peakD].toFixed(1) + "/day") : "";
    const selDow = f.dowValue !== "all" ? parseInt(f.dowValue, 10) : null;
    makeBarsInteractive(els.chartDow, function (i) {
      pushHashEntry();
      els.dowSelect.value = (String(i) === els.dowSelect.value) ? "all" : String(i);
      toast(els.dowSelect.value === "all" ? "Day filter cleared"
        : "Filtered to " + ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"][i]);
      scheduleRender(0);
    }, selDow, function (i) {
      return dowNames[i] + ", " + dowRates[i].toFixed(1) + " per day" + (i === selDow ? ", selected" : "");
    });

    // Trend, binned to match the selected range: hourly for a day, daily
    // for a week or month, weekly beyond that.
    const nowMs = Date.now();
    let bins, binMs, binName, trendTitle, firstLbl;
    if (f.range === "24h") { bins = 24; binMs = 3600000; binName = "hour"; trendTitle = "24-hour trend"; firstLbl = "24 hrs ago"; }
    else if (f.range === "7d") { bins = 7; binMs = 86400000; binName = "day"; trendTitle = "7-day trend"; firstLbl = "7 days ago"; }
    else if (f.range === "30d") { bins = 30; binMs = 86400000; binName = "day"; trendTitle = "30-day trend"; firstLbl = "30 days ago"; }
    else { bins = 12; binMs = 7 * 86400000; binName = "week"; trendTitle = "12-week trend"; firstLbl = "12 wks ago"; }
    els.chartTrendTitle.textContent = trendTitle;
    const series = new Array(bins).fill(0);
    for (const row of rows) {
      const dt = bestRowDate(row);
      if (!dt) continue;
      const age = nowMs - dt.getTime();
      if (age < 0 || age >= bins * binMs) continue;
      series[bins - 1 - Math.floor(age / binMs)]++;
    }
    els.chartTrend.innerHTML = trendChartSVG(series, [firstLbl, "now"]);
    const lastW = series[bins - 1], prevW = series[bins - 2];
    if (prevW > 0) {
      const pct = ((lastW - prevW) / prevW) * 100;
      const dir = pct >= 0 ? "up" : "down";
      els.chartTrendSub.innerHTML = "this " + binName + " <span class='trend-delta " + dir + "'>" + (pct >= 0 ? "▲" : "▼") + " " + Math.abs(pct).toFixed(0) + "%</span>";
    } else {
      els.chartTrendSub.textContent = lastW > 0 ? (lastW + " this " + binName) : "";
    }

    // Hour × day heatmap
    const hm = heatmapSVG(matrix);
    els.chartMatrix.innerHTML = hm.svg + "<div class='chart-note'>Every cell covers the same amount of clock time, so raw counts compare fairly here. Tap a cell to filter to that exact day + hour.</div>";
    const hmSelD = f.dowValue !== "all" ? parseInt(f.dowValue, 10) : null;
    const hmDayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    if (hmSelD !== null && f.exactHour != null) {
      els.chartMatrixSub.innerHTML = "filtering " + esc(hmDayNames[hmSelD]) + " " + esc(fmtHour(f.exactHour)) +
        " <button type='button' class='mini-clear' id='hmClearBtn'>Clear</button>";
      const mcb = document.getElementById("hmClearBtn");
      if (mcb) mcb.addEventListener("click", function () {
        pushHashEntry();
        els.dowSelect.value = "all";
        state.exactHour = null;
        toast("Day + hour filter cleared");
        scheduleRender(0);
      });
    } else {
      els.chartMatrixSub.textContent = hm.peak ? ("hottest " + hm.peak.day + " " + hm.peak.hour + " · " + hm.peak.count) : "";
    }
    els.chartMatrix.querySelectorAll(".hm-cell").forEach(function (cell) {
      const d = parseInt(cell.getAttribute("data-d"), 10);
      const h = parseInt(cell.getAttribute("data-h"), 10);
      const isSel = (hmSelD === d && f.exactHour === h);
      if (isSel) cell.classList.add("sel");
      cell.setAttribute("tabindex", "0");
      cell.setAttribute("role", "button");
      cell.setAttribute("aria-pressed", isSel ? "true" : "false");
      cell.setAttribute("aria-label", hmDayNames[d] + " " + fmtHour(h) + ", " + matrix[d][h] +
        " incidents" + (isSel ? ", selected" : ""));
      function pickCell() {
        pushHashEntry();
        if (hmSelD === d && f.exactHour === h) {
          els.dowSelect.value = "all";
          state.exactHour = null;
          toast("Day + hour filter cleared");
        } else {
          els.dowSelect.value = String(d);
          state.exactHour = h;
          els.timeBlockSelect.value = "all";
          toast("Filtered to " + hmDayNames[d] + "s " + fmtHour(h) + "\u2013" + fmtHour((h + 1) % 24));
        }
        scheduleRender(0);
      }
      cell.addEventListener("click", pickCell);
      cell.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickCell(); }
      });
    });

    // Seasonality by month — normalized per covered day, because the range
    // rarely contains every month equally (or at all).
    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const monthRates = byMonth.map(function (n, i) {
      const days = span ? span.perMonthDays[i] : 0;
      return days > 0 ? n / days : 0;
    });
    els.chartMonth.innerHTML = barChartSVG(monthRates, {
      labels: monthNames.map(function (nm, i) { return [i, nm.charAt(0)]; }),
      titles: monthNames.map(function (nm, i) {
        const days = span ? span.perMonthDays[i] : 0;
        return days > 0
          ? nm + ": " + monthRates[i].toFixed(1) + "/day avg (" + byMonth[i] + " total over " + days + " covered days)"
          : nm + ": no coverage in the filtered range";
      }),
      gap: 4, aria: "Average incidents per day, by month"
    }) + "<div class='chart-note'>Average per covered day — months the range doesn't include show empty rather than misleading zeros-as-quiet.</div>";
    const peakM = monthRates.indexOf(Math.max.apply(null, monthRates));
    els.chartMonthSub.textContent = monthRates[peakM] > 0 ? ("peak " + monthNames[peakM] + " · " + monthRates[peakM].toFixed(1) + "/day") : "";

    // Category mix: stacked proportion bar + legend
    const catCounts = new Map();
    for (const row of rows) {
      const cat = categoryOf(row[IDX_CAUSE]);
      catCounts.set(cat.id, (catCounts.get(cat.id) || 0) + 1);
    }
    let mixHtml = "";
    if (rows.length) {
      let segs = "", legend = "";
      for (const cat of CATEGORIES) {
        const n = catCounts.get(cat.id) || 0;
        if (!n) continue;
        const pct = (n / rows.length) * 100;
        segs += "<span class='mix-seg' style='flex:0 0 " + pct.toFixed(2) + "%;background:" + cat.color + "' title='" +
          esc(cat.label) + ": " + n + " (" + pct.toFixed(1) + "%)'></span>";
        legend += "<button type='button' class='mix-row' data-cat='" + cat.id + "' title='Tap to isolate " + esc(cat.label.toLowerCase()) + " on the map'>" +
          "<span class='dot' style='background:" + cat.color + "'></span>" +
          "<span class='lbl'>" + esc(cat.label) + "</span><span class='n'>" + n.toLocaleString() + " · " + pct.toFixed(0) + "%</span></button>";
      }
      mixHtml = "<div class='mix-bar'>" + segs + "</div><div class='mix-legend'>" + legend + "</div>";
    } else {
      mixHtml = "<span class='rates-no-data'>No incidents in this selection.</span>";
    }
    els.chartMix.innerHTML = mixHtml;
    els.chartMix.querySelectorAll(".mix-row").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const id = btn.getAttribute("data-cat");
        if (state.cats.size === 1 && state.cats.has(id)) {
          state.cats = new Set(CATEGORIES.map(function (c) { return c.id; }));
          toast("Showing all categories");
        } else {
          state.cats = new Set([id]);
          toast("Isolated: " + (CAT_BY_ID[id] ? CAT_BY_ID[id].label : id));
        }
        renderLegend();
        scheduleRender(0);
      });
    });

    // Corridor leaderboard: CANONICAL corridors (an intersection credits
    // both roads; every spelling of a road groups once). Facet-aware —
    // computed without the corridor filter so alternatives stay visible.
    const corRows = f.corridorId ? filteredIncidents(f, map, { corridor: 1 }) : rows;
    const byCorr = new Map();
    for (const row of corRows) {
      const cors = corridorsOf(row);
      for (let ci = 0; ci < cors.length; ci++) {
        byCorr.set(cors[ci], (byCorr.get(cors[ci]) || 0) + 1);
      }
    }
    const topCorrs = Array.from(byCorr.entries()).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 8);
    // The selected corridor stays listed even when it drops out of the top 8.
    if (f.corridorId && !topCorrs.some(function (e) { return e[0] === f.corridorId; })) {
      topCorrs.push([f.corridorId, byCorr.get(f.corridorId) || 0]);
    }
    if (topCorrs.length) {
      const maxC = topCorrs[0][1] || 1;
      const cfrag = document.createDocumentFragment();
      topCorrs.forEach(function (entry, i) {
        const isSel = entry[0] === f.corridorId;
        const pct = corRows.length ? Math.round((entry[1] / corRows.length) * 100) : 0;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "cor-row" + (isSel ? " on" : "");
        btn.setAttribute("aria-pressed", isSel ? "true" : "false");
        btn.title = "Details for " + titleCase(entry[0]) + (isSel ? " (currently filtering the map)" : "");
        btn.innerHTML = "<span class='cor-rank'>" + (i + 1) + "</span>" +
          "<span class='cor-name'>" + esc(titleCase(entry[0])) + "</span>" +
          "<span class='cor-bar-wrap'><span class='cor-bar' style='width:" + ((entry[1] / maxC) * 100).toFixed(1) + "%'></span></span>" +
          "<span class='cor-n'>" + entry[1].toLocaleString() + "</span>" +
          "<span class='cor-pct'>" + pct + "%</span>";
        btn.addEventListener("click", function () { openCorridorDetail(entry[0]); });
        cfrag.appendChild(btn);
      });
      els.corridorList.innerHTML = "";
      els.corridorList.appendChild(cfrag);
      if (f.corridorId) {
        const clr = document.createElement("button");
        clr.type = "button";
        clr.className = "mini-clear";
        clr.style.marginTop = "4px";
        clr.textContent = "Clear corridor";
        clr.addEventListener("click", function () { setCorridor(f.corridorId); });
        els.corridorList.appendChild(clr);
      }
    } else {
      els.corridorList.innerHTML = "<span class='rates-no-data'>No corridor data in this selection.</span>";
    }

    renderDataQuality();

    // One obvious escape hatch for chart-created selections.
    els.anClearRow.style.display =
      (f.exactHour != null || f.dowValue !== "all" || f.corridorId) ? "" : "none";
  }

  /* ═══════════════════════ exposure normalization ═══════════════════════
     Raw incident counts are misleading: a date range contains ~5× more
     weekday hours than weekend days, more school days than breaks, etc.
     computeSpanFor() walks the FILTERED rows' own date range once and counts
     how much of each kind of time actually occurred, so every comparison in
     the Analytics tab can be a fair rate (per hour / per day). */
  let _dataSpanCache = new Map();

  function computeSpanFor(rows) {
    let minMs = Infinity, maxMs = -Infinity;
    for (const row of rows) {
      const dt = bestRowDate(row);
      if (!dt) continue;
      const ms = dt.getTime();
      if (ms < minMs) minMs = ms;
      if (ms > maxMs) maxMs = ms;
    }
    if (!isFinite(minMs)) return null;
    return computeSpanWindow(minMs, maxMs);
  }

  // Exposure over an EXPLICIT window — used for the fixed 24h/7d/30d ranges,
  // where the observation period is the range itself, never the coincidental
  // span between the first and last matching incident.
  function computeSpanWindow(minMs, maxMs) {
    const key = minMs + "|" + maxMs;
    if (_dataSpanCache.has(key)) return _dataSpanCache.get(key);

    let rushHours = 0, nonRushWeekdayHours = 0, schoolDayHours = 0, nonSchoolWeekdayHours = 0;
    let weekdayDays = 0, weekendDays = 0, totalDays = 0;
    const perDow = [0, 0, 0, 0, 0, 0, 0];
    const perMonthDays = new Array(12).fill(0);
    const d = new Date(minMs);
    d.setHours(0, 0, 0, 0);
    const end = new Date(maxMs);
    end.setHours(23, 59, 59, 999);
    while (d <= end) {
      const dow = d.getDay();
      totalDays += 1;
      perDow[dow] += 1;
      perMonthDays[d.getMonth()] += 1;
      if (dow !== 0 && dow !== 6) {
        weekdayDays += 1;
        rushHours += 5;
        nonRushWeekdayHours += 19;
        if (isSchoolDayJS(d)) schoolDayHours += 24; else nonSchoolWeekdayHours += 24;
      } else {
        weekendDays += 1;
      }
      d.setDate(d.getDate() + 1);
    }
    const span = {
      minMs: minMs, maxMs: maxMs, totalDays: totalDays,
      weekdayDays: weekdayDays, weekendDays: weekendDays,
      perDow: perDow, perMonthDays: perMonthDays,
      rushHours: rushHours, nonRushWeekdayHours: nonRushWeekdayHours,
      schoolDayHours: schoolDayHours, nonSchoolWeekdayHours: nonSchoolWeekdayHours
    };
    if (_dataSpanCache.size > 16) _dataSpanCache.clear();
    _dataSpanCache.set(key, span);
    return span;
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

  function renderRatesPanel(rows, f) {
    const el = els.ratesContent;
    f = f || currentFilterObj();
    if (!rows.length) {
      el.innerHTML = '<span class="rates-no-data">No incidents match the current filters — nothing to normalize. Widen the time range or remove a filter.</span>';
      return;
    }
    // The exposure period is the RANGE, not the matching incidents' own
    // span: non-time filters shrink the numerator, never the denominator.
    const nowMsR = Date.now();
    let span = null, windowNote = "";
    if (f.range === "24h") { span = computeSpanWindow(nowMsR - 86400000, nowMsR); windowNote = "the past 24 hours"; }
    else if (f.range === "7d") { span = computeSpanWindow(nowMsR - 7 * 86400000, nowMsR); windowNote = "the past 7 days"; }
    else if (f.range === "30d") { span = computeSpanWindow(nowMsR - 30 * 86400000, nowMsR); windowNote = "the past 30 days"; }
    else if (f.mm || f.dd || f.yy || f.dateFrom || f.dateTo) { span = computeSpanFor(rows); windowNote = "the selected dates"; }
    else { span = computeSpanFor(INCIDENTS); windowNote = "the full collection period"; }
    if (!span || span.totalDays === 0) {
      el.innerHTML = '<span class="rates-no-data">Not enough dated incidents to compute rates.</span>';
      return;
    }

    let rushCount = 0, nonRushCount = 0, schoolCount = 0, nonSchoolCount = 0;
    let weekdayCount = 0, weekendCount = 0;
    let rainCount = 0, noRainCount = 0, windyCount = 0, calmCount = 0, lowVisCount = 0, goodVisCount = 0;

    for (const row of rows) {
      if (_isWeekendRow(row)) {
        weekendCount++;
      } else {
        weekdayCount++;
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

    // One labelled bar. `value` is the already-normalized rate or share;
    // `maxValue` (same units) sets the 100% fill so bars are comparable
    // within their group.
    function barRow(label, value, maxValue, colorVar, valText) {
      const pct = maxValue > 0 ? Math.min(100, (value / maxValue) * 100) : 0;
      return '<div class="rates-row">' +
        '<span class="rates-label">' + esc(label) + '</span>' +
        '<div class="rates-bar-wrap"><div class="rates-bar-fill" style="width:' + pct.toFixed(1) + '%;background:var(' + colorVar + ')"></div></div>' +
        '<span class="rates-val">' + valText + '</span></div>';
    }
    function rateText(count, hours) {
      if (hours <= 0 || count === 0) return '<span class="rates-n">0</span>';
      const r = count / hours;
      const s = r >= 0.1 ? r.toFixed(2) + "/hr" : (r * 1000).toFixed(1) + "/1000 hr";
      return s + '<span class="rates-n"> (' + count + ')</span>';
    }
    function ratio(a, ha, b, hb, la, lb) {
      if (ha <= 0 || hb <= 0) return "";
      const ra = a / ha, rb = b / hb;
      if (ra === 0 && rb === 0) return "";
      if (rb === 0) return la + " has incidents but " + lb + " has none.";
      const q = ra / rb;
      if (Math.abs(q - 1) < 0.05) return la + " and " + lb + " are about equal.";
      if (q > 1) return la + " runs <strong>" + q.toFixed(1) + "&times;</strong> the rate of " + lb + ".";
      return lb + " runs <strong>" + (1 / q).toFixed(1) + "&times;</strong> the rate of " + la + ".";
    }
    function group(title, note, body, explain) {
      return '<div class="rates-group"><div class="rates-group-title">' + title +
        (note ? ' <span class="rates-span-note">' + note + '</span>' : '') + '</div>' +
        body + (explain ? '<div class="rates-explain">' + explain + '</div>' : '') + '</div>';
    }

    // Rush hour vs off-peak: per-HOUR rates.
    const rushRate = span.rushHours > 0 ? rushCount / span.rushHours : 0;
    const offRate = span.nonRushWeekdayHours > 0 ? nonRushCount / span.nonRushWeekdayHours : 0;
    const maxRR = Math.max(rushRate, offRate) || 1;
    const rushHTML =
      barRow("Rush hour", rushRate, maxRR, "--bad", rateText(rushCount, span.rushHours)) +
      barRow("Off-peak", offRate, maxRR, "--accent", rateText(nonRushCount, span.nonRushWeekdayHours)) +
      '<div class="rates-ratio">' + ratio(rushCount, span.rushHours, nonRushCount, span.nonRushWeekdayHours, "Rush hour", "off-peak") + '</div>';

    // Weekday vs weekend: per-DAY rates (raw totals would favor weekdays 5:2).
    const wdRate = span.weekdayDays > 0 ? weekdayCount / span.weekdayDays : 0;
    const weRate = span.weekendDays > 0 ? weekendCount / span.weekendDays : 0;
    const maxWD = Math.max(wdRate, weRate) || 1;
    function perDayText(count, days) {
      if (days <= 0) return '<span class="rates-n">—</span>';
      return (count / days).toFixed(1) + "/day" + '<span class="rates-n"> (' + count + ')</span>';
    }
    const wdHTML =
      barRow("Weekdays", wdRate, maxWD, "--bad", perDayText(weekdayCount, span.weekdayDays)) +
      barRow("Weekends", weRate, maxWD, "--accent", perDayText(weekendCount, span.weekendDays)) +
      '<div class="rates-ratio">' + ratio(weekdayCount, span.weekdayDays, weekendCount, span.weekendDays, "A weekday", "a weekend day") + '</div>';

    // School day vs no school: per-HOUR rates over weekdays.
    const schRate = span.schoolDayHours > 0 ? schoolCount / span.schoolDayHours : 0;
    const noSchRate = span.nonSchoolWeekdayHours > 0 ? nonSchoolCount / span.nonSchoolWeekdayHours : 0;
    const maxSR = Math.max(schRate, noSchRate) || 1;
    const schHTML =
      barRow("School day", schRate, maxSR, "--bad", rateText(schoolCount, span.schoolDayHours)) +
      barRow("No school", noSchRate, maxSR, "--accent", rateText(nonSchoolCount, span.nonSchoolWeekdayHours)) +
      '<div class="rates-ratio">' + ratio(schoolCount, span.schoolDayHours, nonSchoolCount, span.nonSchoolWeekdayHours, "School days", "no-school days") + '</div>';

    // Conditions at incident time: SHARES of weather-tagged incidents, bars
    // filled relative to the larger share in each pair so both are readable.
    const wTotal = rainCount + noRainCount;
    let weatherHTML = "";
    if (wTotal === 0) {
      weatherHTML = '<div class="rates-row"><span class="rates-no-data">No weather data in this selection.</span></div>';
    } else {
      function shareText(count, total) {
        return ((count / total) * 100).toFixed(1) + "%" + '<span class="rates-n"> (' + count + ')</span>';
      }
      const rainShare = rainCount / wTotal, dryShare = noRainCount / wTotal;
      const maxRain = Math.max(rainShare, dryShare) || 1;
      weatherHTML +=
        barRow("Rain / precip", rainShare, maxRain, "--accent", shareText(rainCount, wTotal)) +
        barRow("No rain", dryShare, maxRain, "--good", shareText(noRainCount, wTotal));
      const wndT = windyCount + calmCount;
      if (wndT > 0) {
        const a = windyCount / wndT, b = calmCount / wndT, mx = Math.max(a, b) || 1;
        weatherHTML +=
          barRow("Windy (≥20 mph)", a, mx, "--warn", shareText(windyCount, wndT)) +
          barRow("Calm wind", b, mx, "--good", shareText(calmCount, wndT));
      }
      const visT = lowVisCount + goodVisCount;
      if (visT > 0) {
        const a = lowVisCount / visT, b = goodVisCount / visT, mx = Math.max(a, b) || 1;
        weatherHTML +=
          barRow("Low vis (<5 mi)", a, mx, "--warn", shareText(lowVisCount, visT)) +
          barRow("Good vis", b, mx, "--good", shareText(goodVisCount, visT));
      }
    }

    const d1 = new Date(span.minMs), d2 = new Date(span.maxMs);
    const fmt = function (d) { return (d.getMonth() + 1) + "/" + d.getDate() + "/" + d.getFullYear(); };
    el.innerHTML =
      '<div class="rates-subtitle">Exposure window: ' + windowNote + ' (' + fmt(d1) + ' – ' + fmt(d2) + '), ' +
      span.totalDays.toLocaleString() + ' days (' + span.weekdayDays.toLocaleString() + ' weekdays, ' +
      span.weekendDays.toLocaleString() + ' weekend days). All comparisons below are normalized to that exposure.</div>' +
      group("Rush hour vs. off-peak",
        "(" + span.rushHours.toLocaleString() + " rush hrs · " + span.nonRushWeekdayHours.toLocaleString() + " off-peak hrs)",
        rushHTML,
        "Incidents per hour of each kind — rush hour is only 5 of 24 weekday hours, so raw counts would understate it.") +
      group("Weekday vs. weekend", "(per-day averages)", wdHTML,
        "Averages per calendar day. The range has " + span.weekdayDays + " weekdays but only " + span.weekendDays + " weekend days, so totals alone would exaggerate weekdays.") +
      group("School day vs. no school",
        "(" + span.schoolDayHours.toLocaleString() + " school hrs · " + span.nonSchoolWeekdayHours.toLocaleString() + " no-school hrs)",
        schHTML,
        "Per-hour rates over weekdays only, using the LPSS calendar heuristic.") +
      group("Conditions at incident time", "(share of incidents carrying weather data)", weatherHTML,
        "Descriptive shares, not risk: we don't measure how many hours it rained overall, so a fair rainy-vs-dry rate isn't possible from this data alone.");
  }

  /* ═══════════════════════ smart insights ═══════════════════════ */
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
      for (const corridor of corridorsOf(row)) {
        byCorridor.set(corridor, (byCorridor.get(corridor) || 0) + 1);
      }
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

  // Incidents still awaiting geocoding are shown in the feed as ghost
  // entries so nothing silently disappears while the geocode budget catches
  // up. They are filtered with the non-geographic matchers only.
  function pendingFeedRows(f) {
    const out = [];
    for (const u of UNLOCATED_LIST) {
      // u = [location, cause, reported, assisting, created_at]
      const pseudo = [
        null, null, u[2] || "", u[0] || "", u[1] || "", u[3] || "", 1, 1,
        null, null, null, null, null, null, null, "", "",
        null, null, null, null, null, null, null, u[4] || ""
      ];
      if (!matchesCats(pseudo)) continue;
      if (!matchesCauseGroup(pseudo, f.causeGroup)) continue;
      if (!matchesCause(pseudo, f.cause)) continue;
      if (!matchesRange(pseudo, f.range, Date.now())) continue;
      if (!matchesDateFilter(pseudo, f)) continue;
      if (!matchesRoadSearch(pseudo, f.roadSearch)) continue;
      out.push(pseudo);
    }
    return out;
  }

  function renderFeed(rows) {
    const nowMs = Date.now();
    const pending = pendingFeedRows(currentFilterObj());
    const entries = rows.map(function (r) { return { row: r, ghost: false }; })
      .concat(pending.map(function (r) { return { row: r, ghost: true }; }));
    entries.sort(function (a, b) {
      const da = bestRowDate(a.row), db = bestRowDate(b.row);
      return (db ? db.getTime() : 0) - (da ? da.getTime() : 0);
    });
    const shown = entries.slice(0, feedLimit);

    els.feedMeta.textContent = entries.length
      ? "Most recent " + shown.length.toLocaleString() + " of " + rows.length.toLocaleString() + " filtered incidents" +
        (pending.length ? " + " + pending.length + " awaiting location" : "") + " — tap to fly to it"
      : "";

    if (!entries.length) {
      els.feedList.innerHTML = "<div class='feed-empty'>No incidents match the current filters.</div>";
      return;
    }

    // Date-group buckets (rows are already sorted newest first, so buckets
    // are contiguous and each header is emitted once).
    function bucketOf(dt) {
      if (!dt) return "Undated";
      const now = new Date(nowMs);
      const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
      const t = dt.getTime();
      if (t >= startToday) return "Today";
      if (t >= startToday - 86400000) return "Yesterday";
      if (t >= startToday - 6 * 86400000) return "This week";
      if (t >= startToday - 29 * 86400000) return "This month";
      return "Earlier";
    }

    const frag = document.createDocumentFragment();
    let lastBucket = null;
    for (const entry of shown) {
      const row = entry.row;
      const ghost = entry.ghost;
      const cat = categoryOf(row[IDX_CAUSE]);
      const dt = bestRowDate(row);
      const bucket = bucketOf(dt);
      if (bucket !== lastBucket) {
        lastBucket = bucket;
        const head = document.createElement("div");
        head.className = "feed-group";
        head.textContent = bucket;
        frag.appendChild(head);
      }
      const item = document.createElement("div");
      item.className = ghost ? "feed-item ghost" : "feed-item";
      item.style.setProperty("--cat", cat.color);
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      const occurrences = incidentCount(row);
      item.innerHTML =
        "<span class='feed-dot'></span>" +
        "<span class='feed-body'>" +
        "<span class='feed-loc'>" + esc(titleCase(row[IDX_LOCATION])) + "</span>" +
        "<span class='feed-sub'><span class='cat-name'>" + esc(String(row[IDX_CAUSE] || cat.label).trim() || cat.label) + "</span>" +
        (occurrences > 1 ? "<span>× " + occurrences + "</span>" : "") +
        (dt ? "<span>" + esc(relTime(dt, nowMs)) + "</span>" : "") +
        (ghost ? "<span class='feed-locating'>locating…</span>" : "") +
        "</span></span>";
      function activate() {
        if (ghost) {
          toast("Awaiting geolocation — it will appear on the map automatically");
          return;
        }
        if (!map) return;
        // Collapse the sheet first so the popup is positioned for the
        // uncovered map area.
        if (window.innerWidth <= 700) setSheetExpanded(false);
        openIncidentPopup([row[IDX_LAT], row[IDX_LNG]], [row], { zoom: 15 });
      }
      item.addEventListener("click", activate);
      item.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
      frag.appendChild(item);
    }
    els.feedList.innerHTML = "";
    els.feedList.appendChild(frag);

    if (entries.length > feedLimit) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "feed-more";
      more.textContent = "Show " + Math.min(30, entries.length - feedLimit) + " more";
      more.addEventListener("click", function () {
        feedLimit += 30;
        renderFeed(lastFiltered);
      });
      els.feedList.appendChild(more);
    }
  }

  /* ═══════════════════════ marker layers ═══════════════════════ */
  const layers = { points: null, beacons: null, heat: null, intersections: null, osmIntersections: null, micro: null, rings: null, hotSpots: null };
  let lastFiltered = [];
  let renderTimer = null;
  let pointRenderMode = "exact";
  let pointSymbolCount = 0;
  let pointMarkers = { singles: [], counts: [] };
  // Tap targets for the map-level click resolver: one entry per drawn incident
  // group. Clicks are resolved to the nearest target within a radius instead
  // of relying on Leaflet's canvas hit-test (which picks the last-drawn layer
  // and lets an overlapping single marker steal taps from a big group).
  let hitTargets = [];

  function groupByRounded(points, decimals) {
    const m = new Map();
    for (const row of points) {
      const key = row[IDX_LAT].toFixed(decimals) + "," + row[IDX_LNG].toFixed(decimals);
      let v = m.get(key);
      if (!v) {
        v = { key: key, lat: parseFloat(row[IDX_LAT].toFixed(decimals)), lng: parseFloat(row[IDX_LNG].toFixed(decimals)), count: 0, sample: row, rows: [] };
        m.set(key, v);
      }
      v.count += 1;
      v.rows.push(row);
    }
    return Array.from(m.values()).sort(function (a, b) { return b.count - a.count; });
  }

  // Human label for the grid size that grouped a set of incidents.
  function cellLabel(decimals) {
    if (decimals >= 5) return "within ~1 m";
    if (decimals >= 4) return "within ~10 m";
    return "within ~100 m";
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

  /* ── standalone popup engine ──────────────────────────────────────────
     Popups are owned by the app, never bound to marker layers. In aggregated
     performance mode every map move rebuilds the marker layers, and a popup
     bound to a destroyed marker closes with it — which made incident details
     unreadable on large datasets. A standalone popup anchored to coordinates
     survives those rebuilds, and the map is panned so the tapped incident is
     centered in the visible area (above the bottom sheet on mobile). */
  let activePopup = null;
  let activePopupOpenedAt = 0;

  function positionActivePopup(popup) {
    if (!map || !popup || popup !== activePopup || !popup.getLatLng) return;
    const latlng = popup.getLatLng();
    const size = map.getSize();
    let sheetH = 0;
    if (window.innerWidth <= 700) {
      sheetH = els.sidebar.classList.contains("expanded")
        ? Math.round(window.innerHeight * 0.94)
        : 236;
    }
    const el = popup.getElement();
    const popH = el ? el.offsetHeight : 220;
    const tip = 18;        // popup tip below the card
    const topSafe = 58;    // clear the floating weather chip
    const visibleH = size.y - sheetH;

    // Anchor y that vertically centers the whole card in the visible strip,
    // clamped so it never rides under the weather chip or behind the sheet.
    let targetY = (visibleH + popH + tip) / 2;
    targetY = Math.min(targetY, visibleH - 18);
    targetY = Math.max(targetY, Math.min(popH + tip + topSafe, visibleH - 18));

    // Horizontally: center of the area not covered by the desktop sidebar.
    const sidebarW = window.innerWidth > 700 ? 430 : 0;
    let targetX = (sidebarW + size.x) / 2;
    targetX = Math.min(targetX, size.x - 170);

    const p = map.latLngToContainerPoint(latlng);
    const dx = p.x - targetX;
    const dy = p.y - targetY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
      map.panBy([dx, dy], { animate: !REDUCED_MOTION, duration: 0.3 });
    }
  }

  // content: an incident-row array (single card, or a browsable list when
  // there are several) or a prebuilt HTML string / DOM node for analyst
  // layers. opts.label describes the grouping ("at this location", …).
  function openIncidentPopup(latlng, content, opts) {
    if (!map) return;
    opts = opts || {};
    const node = Array.isArray(content) ? createIncidentPopup(content, opts.label) : content;
    const popup = L.popup({ maxWidth: 320, autoPan: false, closeOnClick: false })
      .setLatLng(latlng)
      .setContent(node);
    activePopup = popup;
    activePopupOpenedAt = Date.now();
    popup.openOn(map);

    let positioned = false;
    function position() {
      if (positioned) return;
      positioned = true;
      positionActivePopup(popup);
    }

    const currentZoom = map.getZoom();
    const targetZoom = Math.max(currentZoom, opts.zoom || 0);
    if (targetZoom > currentZoom) {
      // Feed fly-to: zoom onto the incident first, then fit the card.
      map.once("moveend", position);
      setTimeout(position, REDUCED_MOTION ? 150 : 800);
      map.setView(latlng, targetZoom, { animate: !REDUCED_MOTION, duration: 0.5 });
    } else {
      // Defer a frame so the popup is laid out and its height measurable.
      requestAnimationFrame(position);
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
    hitTargets = [];

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
      // Draw routine traffic-control groups FIRST so real incidents render
      // on top of them, never underneath.
      grouped.sort(function (a, b) {
        const ar = a.rows && a.rows.length ? (a.rows.every(isRoutineTC) ? 0 : 1) : 1;
        const br = b.rows && b.rows.length ? (b.rows.every(isRoutineTC) ? 0 : 1) : 1;
        return ar - br;
      });
      const sizing = getPointSizing();
      const mkList = [];
      // In aggregated mode, give the busiest clusters a real numbered stack
      // marker (bounded — DOM markers are expensive at this scale); the long
      // tail stays as cheap canvas circles.
      let bigStackBudget = usePerfMode ? 150 : 0;
      for (const group of grouped) {
        let mk = null;
        const groupCount = usePerfMode ? group.count : group.rows.length;
        const anchor = [group.lat, group.lng];
        if (groupCount > 1) {
          if (usePerfMode && groupCount >= 5 && bigStackBudget > 0) {
            // Big cluster: show the combined count on the marker itself.
            bigStackBudget--;
            const cat = dominantCategory(group.rows);
            const digits = String(groupCount).length;
            const size = sizing.countSize + (digits > 2 ? 9 : digits > 1 ? 3 : 0);
            const icon = L.divIcon({
              className: "",
              html: countIconHtml(groupCount, size, sizing.countFont, cat.color),
              iconSize: [size, size],
              iconAnchor: [size / 2, size / 2]
            });
            mk = L.marker(anchor, { icon: icon, riseOnHover: true });
            (function (rows, label) {
              mk.on("click", function () { openIncidentPopup(anchor, rows, { label: label }); });
            })(group.rows, cellLabel(zoom >= 14 ? 4 : 3));
            hitTargets.push({ lat: group.lat, lng: group.lng, rows: group.rows, label: cellLabel(zoom >= 14 ? 4 : 3), routine: group.rows.every(isRoutineTC) });
          } else if (usePerfMode) {
            // Canvas circles scale far better than divIcons for thousands of symbols.
            const cat = categoryOf(group.sample[IDX_CAUSE]);
            const radius = Math.max(4, Math.min(12, 3 + Math.sqrt(groupCount)));
            mk = L.circleMarker(anchor, {
              radius: radius, renderer: renderer,
              color: cat.color, weight: isTouch ? 2.4 : 1.8,
              fillColor: cat.fill, fillOpacity: 0.72
            });
            hitTargets.push({ lat: group.lat, lng: group.lng, rows: group.rows, label: cellLabel(zoom >= 14 ? 4 : 3), routine: group.rows.every(isRoutineTC) });
          } else {
            const cat = dominantCategory(group.rows);
            const icon = L.divIcon({
              className: "",
              html: countIconHtml(groupCount, sizing.countSize, sizing.countFont, cat.color),
              iconSize: [sizing.countSize, sizing.countSize],
              iconAnchor: [sizing.countSize / 2, sizing.countSize / 2]
            });
            mk = L.marker(anchor, { icon: icon, riseOnHover: true });
            mk.__count = groupCount;
            mk.__catColor = cat.color;
            pointMarkers.counts.push(mk);
            // DOM markers swallow their own clicks (no map propagation), so
            // they keep a direct handler; they're also registered as hit
            // targets so near-miss taps resolve to them.
            (function (rows) {
              mk.on("click", function () { openIncidentPopup(anchor, rows); });
            })(group.rows);
            hitTargets.push({ lat: group.lat, lng: group.lng, rows: group.rows, label: null, routine: group.rows.every(isRoutineTC) });
          }
        } else {
          const singleRow = usePerfMode ? group.sample : group.rows[0];
          const cat = categoryOf(singleRow[IDX_CAUSE]);
          mk = L.circleMarker(anchor, {
            radius: sizing.radius, renderer: renderer,
            color: cat.color, weight: isTouch ? 2.2 : 1.4,
            fillColor: cat.fill, fillOpacity: 0.6
          });
          pointMarkers.singles.push(mk);
          hitTargets.push({ lat: group.lat, lng: group.lng, rows: [singleRow], label: null, routine: isRoutineTC(singleRow) });
        }
        mkList.push(mk);
      }
      pointSymbolCount = mkList.length;
      layers.points = L.layerGroup(mkList).addTo(map);

      // Pulse beacons on incidents reported within the last 2 hours, so live
      // activity is visible at a glance. Non-interactive: taps pass through
      // to the incident marker underneath.
      if (!REDUCED_MOTION) {
        const nowMs = Date.now();
        const beacons = [];
        for (const row of filtered) {
          const dt = bestRowDate(row);
          if (!dt || nowMs - dt.getTime() > 2 * 3600 * 1000) continue;
          const cat = categoryOf(row[IDX_CAUSE]);
          beacons.push(L.marker([row[IDX_LAT], row[IDX_LNG]], {
            interactive: false,
            keyboard: false,
            icon: L.divIcon({
              className: "beacon-wrap",
              html: "<span class='beacon' style='--cat:" + cat.color + "'></span>",
              iconSize: [18, 18],
              iconAnchor: [9, 9]
            })
          }));
          if (beacons.length >= 20) break;
        }
        if (beacons.length) layers.beacons = L.layerGroup(beacons).addTo(map);
      }
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
        const c = L.circleMarker([g.lat, g.lng], { radius: radius, renderer: renderer, color: "#2563eb", fillColor: "#93c5fd", fillOpacity: 0.4, weight: 1.5, bubblingMouseEvents: false });
        (function (g) {
          c.on("click", function () {
            openIncidentPopup([g.lat, g.lng], g.rows, { label: cellLabel(dInter) });
          });
        })(g);
        c.addTo(layer);
      }
      layers.intersections = layer;
    }

    if (showOsmInter) {
      const layer = L.layerGroup().addTo(map);
      if (!OSM_INTERSECTIONS || OSM_INTERSECTIONS.length === 0) {
        const center = getCenterFromData();
        const mk = L.circleMarker(center, { radius: 8, renderer: renderer, bubblingMouseEvents: false });
        mk.on("click", function () {
          openIncidentPopup(center, "OSM intersection data unavailable.<br>Install osmnx server-side and rebuild once.");
        });
        mk.addTo(layer);
      } else {
        const top = OSM_INTERSECTIONS.slice(0, topN);
        for (const it of top) {
          const radius = Math.max(8, Math.min(34, 4 + Math.sqrt(it[2]) * 3));
          const c = L.circleMarker([it[0], it[1]], { radius: radius, renderer: renderer, color: "#0d9488", fillColor: "#5eead4", fillOpacity: 0.4, weight: 1.5, bubblingMouseEvents: false });
          (function (it) {
            c.on("click", function () {
              openIncidentPopup([it[0], it[1]], "<b>OSM intersection hotspot</b><br>Count: " + it[2] + "<br>Node: " + esc(it[3]));
            });
          })(it);
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
        const c = L.circleMarker([g.lat, g.lng], { radius: radius, renderer: renderer, color: "#9333ea", fillColor: "#d8b4fe", fillOpacity: 0.4, weight: 1.5, bubblingMouseEvents: false });
        (function (g) {
          c.on("click", function () {
            openIncidentPopup([g.lat, g.lng], g.rows, { label: cellLabel(dMicro) });
          });
        })(g);
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
      const centerMarker = L.circleMarker(center, { radius: 7, renderer: renderer, color: "#64748b", bubblingMouseEvents: false });
      centerMarker.on("click", function () {
        openIncidentPopup(center, "Dataset center<br>" + center[0].toFixed(5) + ", " + center[1].toFixed(5));
      });
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
          radius: r, renderer: renderer, color: fillColor, weight: 2, fillColor: fillColor, fillOpacity: 0.35,
          bubblingMouseEvents: false
        });
        (function (hs) {
          c.on("click", function () {
            openIncidentPopup([hs[0], hs[1]],
              "<b>Hot spot</b><br>" + esc(titleCase(hs[4] || "Unknown location")) +
              "<br>Incidents: " + hs[2] + "<br>Recency score: " + hs[3].toFixed(2) +
              "<br><span style='color:var(--text-3);font-size:11px;'>Recent incidents weigh more.</span>");
          });
        })(hs);
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

  // Show, on each collapsed filter section, how many of its controls are
  // active — so a filter hidden inside a closed section is never a surprise.
  function updateAccordionBadges() {
    document.querySelectorAll("#panelFilters .acc[data-filter-acc]").forEach(function (acc) {
      const badge = acc.querySelector(".acc-badge");
      if (!badge) return;
      let n = 0;
      acc.querySelectorAll(".acc-body select").forEach(function (s) { if (s.selectedIndex > 0) n++; });
      acc.querySelectorAll(".acc-body input[type=checkbox]").forEach(function (c) { if (c.checked) n++; });
      acc.querySelectorAll(".acc-body input[type=date]").forEach(function (d2) { if (d2.value) n++; });
      // The agency checklist is chips, not a <select>/checkbox — count it once
      // if any agency is selected.
      if (acc.querySelector("#agencyChecklist") && state.agencies.size) n++;
      badge.textContent = String(n);
      badge.classList.toggle("show", n > 0);
    });
  }

  let didInitialFit = false;

  // Fit the map viewport to a set of incident rows (never automatic after
  // filter changes — only on first load and via the "Fit results" button).
  function fitToResults(rows) {
    if (!map || !rows || !rows.length) return;
    let latMin = Infinity, latMax = -Infinity, lngMin = Infinity, lngMax = -Infinity;
    for (const r of rows) {
      if (r[IDX_LAT] < latMin) latMin = r[IDX_LAT];
      if (r[IDX_LAT] > latMax) latMax = r[IDX_LAT];
      if (r[IDX_LNG] < lngMin) lngMin = r[IDX_LNG];
      if (r[IDX_LNG] > lngMax) lngMax = r[IDX_LNG];
    }
    if (!isFinite(latMin)) return;
    try {
      map.fitBounds([[latMin, lngMin], [latMax, lngMax]], { padding: [40, 40], maxZoom: 14 });
    } catch (e) {}
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
    updateAccordionBadges();

    // Facet-aware companions: every visible count tracks the OTHER filters.
    renderStatTiles();
    renderLegend();
    renderAgencyChecklist();
    renderActiveChips(f);

    drawLayers(filtered);
    renderCharts(filtered, f);
    renderRatesPanel(filtered, f);
    renderInsightsPanel(filtered);
    renderFeed(filtered);

    // First render: frame the default (Past 24h) results, not the archive.
    if (!didInitialFit && map) {
      didInitialFit = true;
      fitToResults(filtered.length ? filtered : INCIDENTS);
    }
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
    els.rangeTiles.querySelectorAll(".stat-tile").forEach(function (tile) {
      tile.classList.toggle("active", (tile.getAttribute("data-range") || "") === state.range);
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

  // Count how many incidents mention each canonical agency (collapsing every
  // spelling/order/glued variation of the "assisting" field, see AGENCIES).
  let agencyCounts = {};
  function computeAgencyCounts() {
    agencyCounts = {};
    for (const a of AGENCY_ALL) agencyCounts[a.id] = 0;
    for (const r of INCIDENTS) {
      const m = agencyMaskOf(r);
      for (const a of AGENCY_ALL) { if (m & a.bit) agencyCounts[a.id]++; }
    }
  }

  // Render the responding-agency checklist. Only agencies that actually appear
  // in the data get a chip, so absent responders never clutter the filter.
  function renderAgencyChecklist() {
    // Facet-aware: counts reflect every OTHER active filter (never the
    // agency selection itself), so selecting Fire doesn't zero out Police.
    const rows = filteredIncidents(currentFilterObj(), map, { agency: 1 });
    const facet = {};
    for (const r of rows) {
      const m = agencyMaskOf(r);
      for (const a of AGENCY_ALL) { if (m & a.bit) facet[a.id] = (facet[a.id] || 0) + 1; }
    }
    els.agencyChecklist.innerHTML = "";
    let shown = 0;
    for (const a of AGENCY_ALL) {
      if (!agencyCounts[a.id]) continue;  // never appears anywhere in the archive
      shown++;
      const n = facet[a.id] || 0;
      const pct = rows.length ? Math.round((n / rows.length) * 100) : 0;
      const on = state.agencies.has(a.id);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "agency-chip" + (on ? " on" : "");
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.innerHTML = "<span class='tick'></span>" + esc(a.label) +
        " <span class='n'>" + n.toLocaleString() + (rows.length ? " · " + pct + "%" : "") + "</span>";
      btn.addEventListener("click", function () {
        if (state.agencies.has(a.id)) state.agencies.delete(a.id);
        else state.agencies.add(a.id);
        renderAgencyChecklist();
        scheduleRender(0);
      });
      els.agencyChecklist.appendChild(btn);
    }
    if (!shown) {
      els.agencyChecklist.innerHTML = "<span class='n' style='color:var(--text-3)'>No agency data</span>";
      return;
    }
    if (state.agencies.size) {
      const clr = document.createElement("button");
      clr.type = "button";
      clr.className = "mini-clear";
      clr.textContent = "Clear agencies";
      clr.addEventListener("click", function () {
        state.agencies.clear();
        renderAgencyChecklist();
        scheduleRender(0);
      });
      els.agencyChecklist.appendChild(clr);
    }
  }

  function buildAgencyChecklist() {
    computeAgencyCounts();
    // Drop any selected agency that isn't present in the current data.
    state.agencies.forEach(function (id) {
      if (!agencyCounts[id]) state.agencies.delete(id);
    });
    renderAgencyChecklist();
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
    els.dateFrom.value = "";
    els.dateTo.value = "";
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
    els.chkExcludeLowConf.checked = false;
    els.roadSearch.value = "";
    els.roadSearchClear.classList.remove("show");
    els.chkFloodWarning.checked = false;
    els.chkThunderstormWarning.checked = false;
    els.chkTornadoWatch.checked = false;
    state.agencies.clear();
    state.exactHour = null;
    state.corridorId = null;
    renderAgencyChecklist();
    els.chkHoliday.checked = false;
    els.lightSelect.value = "any";
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
    setRange("24h", true);
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
  let lastRefreshMs = Date.now();  // page load counts as the first refresh

  function onDataReplaced() {
    _dataSpanCache.clear();
    _catCache.clear();
    buildLocHistory();
    renderStatTiles();
    renderStatus();
    renderLegend();
    buildCauseDropdown();
    buildAgencyChecklist();
    buildYearOptions();
    scheduleRender(0);
  }

  const META_SRC = DATAJS_SRC.replace(/[^\/]*$/, "traffic_meta.json");
  let lastDataVersion = null;

  function fetchMeta(cb) {
    let called = false;
    function fin(m) { if (!called) { called = true; cb(m); } }
    try {
      if (typeof fetch !== "function") { fin(null); return; }
      fetch(META_SRC + "?v=" + Date.now(), { cache: "no-store" })
        .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
        .then(function (m) { fin(m && m.data_version ? m : null); })
        .catch(function () { fin(null); });
    } catch (e) { fin(null); }
  }

  function reloadData(manual, done) {
    if (refreshBusy) { if (done) done(); return; }
    if (!manual && document.visibilityState !== "visible") { if (done) done(); return; }
    refreshBusy = true;
    // Poll the tiny metadata file first: when the data version is unchanged
    // the multi-hundred-KB data file isn't downloaded at all. If the meta
    // file is missing (older deploys, file://), fall back to a full reload.
    fetchMeta(function (meta) {
      if (meta) {
        dataMeta = meta;
        if (lastDataVersion && meta.data_version === lastDataVersion) {
          refreshBusy = false;
          lastRefreshMs = Date.now();
          renderStatus();
          if (manual) toast("Feed is up to date");
          if (done) done();
          return;
        }
      }
      loadFullData(manual, done, meta);
    });
  }

  function loadFullData(manual, done, meta) {
    const prevCount = INCIDENTS.length;
    const s = document.createElement("script");
    s.src = DATAJS_SRC + (DATAJS_SRC.indexOf("?") === -1 ? "?" : "&") + "v=" + Date.now();
    s.onload = function () {
      refreshBusy = false;
      s.remove();
      lastRefreshMs = Date.now();
      if (meta) lastDataVersion = meta.data_version;
      renderStatus();
      const fresh = window.INCIDENTS_DATA || [];
      if (fresh === INCIDENTS) {
        if (manual) toast("Feed is up to date");
        if (done) done();
        return;
      }
      INCIDENTS = fresh;
      OSM_INTERSECTIONS = window.OSM_INTERSECTIONS_DATA || [];
      HOT_SPOTS = window.HOT_SPOTS_DATA || [];
      UNLOCATED_COUNT = window.INCIDENTS_UNLOCATED_COUNT || 0;
      UNLOCATED_LIST = window.INCIDENTS_UNLOCATED_LIST || [];
      UNMAPPABLE_COUNT = window.INCIDENTS_UNMAPPABLE_COUNT || 0;
      onDataReplaced();
      const delta = INCIDENTS.length - prevCount;
      if (delta > 0) toast(delta + " new incident" + (delta === 1 ? "" : "s") + " loaded");
      else if (manual) toast("Feed is up to date");
      if (done) done();
    };
    s.onerror = function () {
      refreshBusy = false;
      s.remove();
      if (manual) toast("Refresh failed — will retry automatically");
      if (done) done();
    };
    document.body.appendChild(s);
  }

  /* ═══════════ active-filter chips ═══════════ */
  // One removable chip per live filter. Each chip's × clears ONLY its own
  // filter; the full Reset button still lives in the footer.
  function renderActiveChips(f) {
    const c = els.activeChips;
    c.innerHTML = "";
    function chip(label, remove) {
      const el = document.createElement("span");
      el.className = "af-chip";
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = label;
      el.appendChild(t);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "x";
      x.setAttribute("aria-label", "Remove filter: " + label);
      x.textContent = "\u00d7";
      x.addEventListener("click", function () { remove(); scheduleRender(0); });
      el.appendChild(x);
      c.appendChild(el);
    }
    function selText(sel) {
      return sel && sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : "";
    }
    if (state.range && state.range !== "all") {
      const lbl = state.range === "24h" ? "Past 24h" : state.range === "7d" ? "7 days" : "30 days";
      chip(lbl, function () { setRange("all", true); });
    }
    if (state.exactHour != null) chip("Hour: " + fmtHour(state.exactHour), function () { state.exactHour = null; });
    if (f.timeBlock !== "all") chip(selText(els.timeBlockSelect), function () { els.timeBlockSelect.value = "all"; });
    if (f.dowValue !== "all") chip(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][parseInt(f.dowValue, 10)] + "s", function () { els.dowSelect.value = "all"; });
    if (f.dayType !== "all") chip(selText(els.dayTypeSelect), function () { els.dayTypeSelect.value = "all"; });
    if (f.mm || f.dd || f.yy) {
      chip("Date: " + (f.mm || "*") + "/" + (f.dd || "*") + "/" + (f.yy || "*"), function () {
        $("monthSelect").value = ""; $("daySelect").value = ""; $("yearSelect").value = "";
      });
    }
    if (f.dateFrom || f.dateTo) {
      chip((f.dateFrom || "…") + " → " + (f.dateTo || "…"), function () {
        els.dateFrom.value = ""; els.dateTo.value = "";
      });
    }
    if (state.cats.size < CATEGORIES.length) {
      chip("Categories: " + state.cats.size + "/" + CATEGORIES.length, function () {
        state.cats = new Set(CATEGORIES.map(function (cc) { return cc.id; }));
        renderLegend();
      });
    }
    if (f.causeGroup !== "__ALL__") chip("Group: " + selText(els.causeGroupSelect), function () { els.causeGroupSelect.value = "__ALL__"; });
    if (f.cause !== "__ALL__") chip("Type: " + selText(els.causeSelect), function () { els.causeSelect.value = "__ALL__"; });
    state.agencies.forEach(function (id) {
      const a = AGENCY_BY_ID[id];
      if (a) chip(a.label, function () { state.agencies.delete(id); });
    });
    if (f.roadSearch) chip("Road: " + f.roadSearch, function () { els.roadSearch.value = ""; els.roadSearchClear.classList.remove("show"); });
    if (state.corridorId) chip("Corridor: " + titleCase(state.corridorId), function () { state.corridorId = null; });
    if (f.roadType !== "any") chip("Road type: " + selText(els.roadTypeSelect), function () { els.roadTypeSelect.value = "any"; });
    if (f.rushHour) chip("Rush hour", function () { els.chkRushHour.checked = false; });
    if (f.schoolDay) chip("School days", function () { els.chkSchoolDay.checked = false; });
    if (f.holiday) chip("Holidays", function () { els.chkHoliday.checked = false; });
    if (f.weatherOnly) chip("Has weather data", function () { els.chkWeatherOnly.checked = false; });
    [["tempBand", "Temp"], ["precipBand", "Rain chance"], ["precipAmountBand", "Rainfall"],
     ["windBand", "Wind"], ["visBand", "Visibility"], ["cloudBand", "Sky"]].forEach(function (spec) {
      const sel = $(spec[0]);
      if (sel && sel.value !== "any") chip(spec[1] + ": " + selText(sel), function () { sel.value = "any"; });
    });
    if (f.chkFlood) chip("Flash flood warning", function () { els.chkFloodWarning.checked = false; });
    if (f.chkStorm) chip("Severe storm warning", function () { els.chkThunderstormWarning.checked = false; });
    if (f.chkTornado) chip("Tornado watch", function () { els.chkTornadoWatch.checked = false; });
    if (f.light !== "any") chip(selText(els.lightSelect), function () { els.lightSelect.value = "any"; });
    if (f.inViewOnly) chip("Only in map view", function () { els.chkInViewOnly.checked = false; });
  }

  /* ═══════════ locate me (privacy: never leaves the browser) ═══════════ */
  let locLayer = null;
  function locateMe() {
    if (!map) return;
    if (!navigator.geolocation) { toast("Location is not available in this browser"); return; }
    const btn = $("locBtn");
    btn.classList.add("busy");
    navigator.geolocation.getCurrentPosition(function (pos) {
      btn.classList.remove("busy");
      const ll = [pos.coords.latitude, pos.coords.longitude];
      if (locLayer) { try { map.removeLayer(locLayer); } catch (e) {} }
      locLayer = L.layerGroup([
        L.circle(ll, { radius: Math.min(pos.coords.accuracy || 50, 1500), color: "#2f6fed", weight: 1, fillColor: "#2f6fed", fillOpacity: 0.08 }),
        L.circleMarker(ll, { radius: 7, color: "#ffffff", weight: 2, fillColor: "#2f6fed", fillOpacity: 1 })
      ]).addTo(map);
      try { map.setView(ll, Math.max(map.getZoom(), 14), { animate: !REDUCED_MOTION }); } catch (e) {}
      toast("Showing your location — it stays in your browser");
    }, function (err) {
      btn.classList.remove("busy");
      toast(err && err.code === 1 ? "Location permission denied" : "Couldn't get your location");
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
  }

  /* ═══════════ commute-alert builder ═══════════ */
  const rbRoads = [];  // {label, cid}
  let rbKnown = null;  // canonical corridor ids seen in the data (Python-normalized)
  function rbKnownCorridors() {
    if (rbKnown) return rbKnown;
    const set = new Set();
    for (const row of INCIDENTS) { for (const c of corridorsOf(row)) set.add(c); }
    rbKnown = Array.from(set).sort(function (a, b) { return a.length - b.length; });
    return rbKnown;
  }
  function rbNormalize(text) {
    const cs = corridorsOfJS(text);   // reuse the map's canonical normalizer
    let base = cs.length ? cs[0] : "";
    if (!base) return "";
    // Prefer a canonical corridor already in the data, so a loosely typed
    // "ambassador caffery" resolves to the exact id the Pi will match
    // ("AMBASSADOR CAFFERY PKWY") — the backend applies the same alias table.
    const known = rbKnownCorridors();
    if (known.indexOf(base) !== -1) return base;
    for (const c of known) {
      if (c === base || c.indexOf(base + " ") === 0 || base.indexOf(c + " ") === 0) return c;
    }
    return base;
  }
  // Traced route state: waypoints + preview polyline + derived corridors.
  let rbPath = [];             // [[lat, lng], ...]
  let rbDrawing = false;
  let rbLine = null;

  function rbPtSegDistM(plat, plng, a, b) {
    const lat0 = ((a[0] + b[0] + plat) / 3) * Math.PI / 180;
    const kx = 111320 * Math.cos(lat0), ky = 110540;
    const px = plng * kx, py = plat * ky;
    const ax = a[1] * kx, ay = a[0] * ky, bx = b[1] * kx, by = b[0] * ky;
    const dx = bx - ax, dy = by - ay;
    let t = 0;
    if (dx !== 0 || dy !== 0) t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }
  function rbUpdateLine() {
    if (rbLine) { try { map.removeLayer(rbLine); } catch (e) {} rbLine = null; }
    if (map && rbPath.length >= 1) {
      rbLine = L.polyline(rbPath, { color: "#2f6fed", weight: 5, opacity: 0.75, dashArray: "1 8", lineCap: "round" }).addTo(map);
    }
  }

  function rbRender() {
    const wrap = els.routeModal.querySelector("#rbRoads");
    wrap.innerHTML = "";
    rbRoads.forEach(function (r, i) {
      const chip = document.createElement("span");
      chip.className = "rb-road";
      chip.innerHTML = "<span>" + esc(titleCase(r.cid)) + "</span>";
      const x = document.createElement("button");
      x.type = "button"; x.className = "x"; x.setAttribute("aria-label", "Remove " + r.cid);
      x.textContent = "\u00d7";
      x.addEventListener("click", function () { rbRoads.splice(i, 1); rbRender(); });
      chip.appendChild(x);
      wrap.appendChild(chip);
    });
    const status = els.routeModal.querySelector("#rbPathStatus");
    if (rbPath.length >= 2) {
      status.innerHTML = "✅ <b>" + rbPath.length + " points drawn.</b> Only incidents on your drawn route will alert. Draw again to replace.";
    } else {
      status.textContent = "No route drawn yet. Tap along your route (each tap adds a point); " +
        "only incidents on your drawn route will alert.";
    }
    // Generated route config (env-line format the Pi's inbox reader parses)
    const slot = els.routeModal.querySelector("#rbSlot").value;
    const name = (els.routeModal.querySelector("#rbName").value || "Route " + slot).trim();
    const depart = els.routeModal.querySelector("#rbDepart").value || "07:20";
    const days = els.routeModal.querySelector("#rbDays").value;
    const roadSet = [];
    rbRoads.forEach(function (r) { if (roadSet.indexOf(titleCase(r.cid)) === -1) roadSet.push(titleCase(r.cid)); });
    let out =
      "LAF911_ROUTE_" + slot + "_NAME=" + name + "\n" +
      "LAF911_ROUTE_" + slot + "_DEPART=" + depart + "\n" +
      "LAF911_ROUTE_" + slot + "_DAYS=" + days;
    if (rbPath.length >= 2) {
      out += "\nLAF911_ROUTE_" + slot + "_PATH=" +
        rbPath.map(function (pt) { return pt[0].toFixed(5) + "," + pt[1].toFixed(5); }).join("; ");
    }
    // A drawn route is section-precise by PATH; do not add CORRIDORS,
    // because a corridor name means whole-road matching for non-drawn routes.
    if (roadSet.length && rbPath.length < 2) {
      out += "\nLAF911_ROUTE_" + slot + "_CORRIDORS=" + roadSet.join(" | ");
    }
    els.routeModal.querySelector("#rbOut").textContent = out;
    els.routeModal.__config = out;
    const mail = els.routeModal.querySelector("#rbMail");
    const piAddr = (els.routeModal.querySelector("#rbPiEmail").value || "").trim();
    mail.href = "mailto:" + piAddr + "?subject=" + encodeURIComponent("LAF911 route") +
      "&body=" + encodeURIComponent(out);
  }

  /* draw mode: modal hides, a floating pill guides the tracing */
  // Road snapping: each tap routes ALONG the real road network from the
  // previous stop (via the public OSRM demo router), so the line follows
  // curves and turns exactly and never sweeps across side roads. Falls back
  // to a straight segment when the router is unreachable.
  let rbAnchors = [];      // the user's tapped stops
  let rbLegs = [];         // snapped geometry per leg
  let rbBusy = false;      // one routing request at a time keeps it coherent
  let rbSnapWarned = false;

  function rbRebuildPath() {
    rbPath = [];
    for (let i = 0; i < rbLegs.length; i++) {
      const leg = rbLegs[i];
      for (let j = 0; j < leg.length; j++) {
        if (rbPath.length && j === 0) continue;   // legs share their joint
        rbPath.push(leg[j]);
      }
    }
    rbUpdateLine();
    rbSetPill();
  }

  function rbSnapLeg(from, to, cb) {
    const url = "https://router.project-osrm.org/route/v1/driving/" +
      from[1].toFixed(6) + "," + from[0].toFixed(6) + ";" +
      to[1].toFixed(6) + "," + to[0].toFixed(6) +
      "?overview=full&geometries=geojson&steps=false&alternatives=false";
    let finished = false;
    function fin(coords) { if (!finished) { finished = true; cb(coords); } }
    try {
      if (typeof fetch !== "function") { fin(null); return; }
      const timer = setTimeout(function () { fin(null); }, 6000);
      fetch(url).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        clearTimeout(timer);
        const g = j && j.routes && j.routes[0] && j.routes[0].geometry && j.routes[0].geometry.coordinates;
        fin(g && g.length >= 2 ? g.map(function (c) { return [c[1], c[0]]; }) : null);
      }).catch(function () { clearTimeout(timer); fin(null); });
    } catch (e) { fin(null); }
  }

  // Douglas-Peucker: OSRM geometry is dense; ~10 m tolerance keeps the
  // emailed config small without visibly changing the line.
  function rbSimplify(path, tolM) {
    if (path.length <= 2) return path.slice();
    const keep = new Array(path.length).fill(false);
    keep[0] = keep[path.length - 1] = true;
    const stack = [[0, path.length - 1]];
    while (stack.length) {
      const seg = stack.pop();
      let worst = -1, worstD = tolM;
      for (let i = seg[0] + 1; i < seg[1]; i++) {
        const d = rbPtSegDistM(path[i][0], path[i][1], path[seg[0]], path[seg[1]]);
        if (d > worstD) { worstD = d; worst = i; }
      }
      if (worst !== -1) { keep[worst] = true; stack.push([seg[0], worst], [worst, seg[1]]); }
    }
    return path.filter(function (_, i) { return keep[i]; });
  }

  function rbSetPill() {
    $("rbPillText").textContent = rbBusy ? "Snapping to roads…" :
      rbAnchors.length === 0 ? "Tap the map along your route" :
      rbAnchors.length + " stop" + (rbAnchors.length === 1 ? "" : "s") + " — keep tapping, then Done";
  }
  function rbStartDraw() {
    if (!map) { toast("Map unavailable — can't draw"); return; }
    rbDrawing = true;
    rbPath = [];
    rbAnchors = [];
    rbLegs = [];
    rbBusy = false;
    rbSnapWarned = false;
    rbUpdateLine();
    els.routeModal.classList.remove("open");
    $("rbPill").classList.add("on");
    rbSetPill();
    if (window.innerWidth <= 700) setSheetExpanded(false);
  }
  function rbFinishDraw(save) {
    rbDrawing = false;
    $("rbPill").classList.remove("on");
    if (!save || rbPath.length < 2) {
      rbPath = [];
      rbAnchors = [];
      rbLegs = [];
      rbUpdateLine();
      if (save) toast("Need at least 2 points — route not saved");
    } else {
      rbPath = rbSimplify(rbPath, 10);
      rbUpdateLine();
      toast("Route captured — " + rbPath.length + " points along the roads");
    }
    els.routeModal.classList.add("open");
    rbRender();
  }
  function rbMapClick(latlng) {
    if (rbBusy) return;   // wait for the current leg to snap
    const pt = [latlng.lat, latlng.lng];
    if (!rbAnchors.length) {
      rbAnchors.push(pt);
      rbLegs.push([pt]);
      rbRebuildPath();
      return;
    }
    const from = rbAnchors[rbAnchors.length - 1];
    rbBusy = true;
    rbSetPill();
    rbSnapLeg(from, pt, function (coords) {
      rbBusy = false;
      if (!rbDrawing) return;   // cancelled while the request was in flight
      if (coords) {
        // The router snaps both ends onto the road; adopt its endpoints so
        // the next leg starts exactly on the roadway (and the very first
        // tap's off-road stub is replaced too).
        if (rbLegs.length === 1 && rbLegs[0].length === 1) {
          rbLegs[0] = [coords[0]];
          rbAnchors[0] = coords[0];
        }
        rbAnchors.push(coords[coords.length - 1]);
        rbLegs.push(coords);
      } else {
        rbAnchors.push(pt);
        rbLegs.push([from, pt]);
        if (!rbSnapWarned) {
          rbSnapWarned = true;
          toast("Road snapping unavailable — using straight lines");
        }
      }
      rbRebuildPath();
    });
  }
  function rbAdd() {
    const input = els.routeModal.querySelector("#rbRoad");
    const cid = rbNormalize(input.value);
    if (!cid) { toast("Couldn't read that road name"); return; }
    if (!rbRoads.some(function (r) { return r.cid === cid; })) rbRoads.push({ label: input.value, cid: cid });
    input.value = "";
    rbRender();
  }
  function rbFillRoadSuggestions() {
    // Offer the corridors already seen in the loaded data as autocomplete.
    const dl = els.routeModal.querySelector("#rbRoadList");
    if (dl.__filled) return;
    const seen = new Set();
    for (const row of INCIDENTS) {
      for (const c of corridorsOf(row)) { if (!seen.has(c)) seen.add(c); }
      if (seen.size > 300) break;
    }
    const frag = document.createDocumentFragment();
    Array.from(seen).sort().forEach(function (c) {
      const o = document.createElement("option"); o.value = titleCase(c); frag.appendChild(o);
    });
    dl.appendChild(frag);
    dl.__filled = true;
  }
  /* Device-local saved routes: captured when you email/copy a route config.
     Rendered on the map only in THIS browser — never uploaded, never part of
     the public page's data. */
  let rbSavedLayers = {};
  const RB_ROUTE_COLORS = ["#7c3aed", "#0d9488", "#ea580c", "#db2777"];

  function rbLoadSaved() {
    try { return JSON.parse(localStorage.getItem("laf911_saved_routes") || "{}") || {}; } catch (e) { return {}; }
  }
  function rbStoreSaved(obj) {
    try { localStorage.setItem("laf911_saved_routes", JSON.stringify(obj)); } catch (e) {}
  }
  function rbSaveCurrent() {
    if (rbPath.length < 2) return;
    const slot = els.routeModal.querySelector("#rbSlot").value;
    const saved = rbLoadSaved();
    saved[slot] = {
      name: (els.routeModal.querySelector("#rbName").value || "Route " + slot).trim(),
      depart: els.routeModal.querySelector("#rbDepart").value || "",
      days: els.routeModal.querySelector("#rbDays").value || "",
      path: rbPath.map(function (pt) { return [Number(pt[0].toFixed(5)), Number(pt[1].toFixed(5))]; }),
      ts: Date.now()
    };
    rbStoreSaved(saved);
    rbRenderSaved();
  }
  function rbToggleShow(slot) {
    if (rbSavedLayers[slot]) {
      try { map.removeLayer(rbSavedLayers[slot]); } catch (e) {}
      delete rbSavedLayers[slot];
    } else {
      const r = rbLoadSaved()[slot];
      if (!r || !r.path || r.path.length < 2 || !map) return;
      rbSavedLayers[slot] = L.polyline(r.path, {
        color: RB_ROUTE_COLORS[(parseInt(slot, 10) - 1 || 0) % RB_ROUTE_COLORS.length],
        weight: 5, opacity: 0.8
      }).addTo(map);
      try { map.fitBounds(rbSavedLayers[slot].getBounds(), { padding: [60, 60] }); } catch (e) {}
    }
    rbRenderSaved();
  }
  function rbRenderSaved() {
    const wrap = $("rbSavedWrap"), list = $("rbSaved");
    const saved = rbLoadSaved();
    const slots = Object.keys(saved).sort();
    wrap.style.display = slots.length ? "" : "none";
    list.innerHTML = "";
    slots.forEach(function (slot) {
      const r = saved[slot] || {};
      const chip = document.createElement("span");
      chip.className = "rb-road";
      chip.innerHTML = "<span>" + esc(r.name || ("Route " + slot)) +
        (r.depart ? " <span style='color:var(--text-3);font-weight:500'>· " + esc(r.depart) + "</span>" : "") + "</span>";
      const show = document.createElement("button");
      show.type = "button";
      show.className = "x";
      show.style.width = "auto";
      show.style.fontSize = "10.5px";
      show.style.padding = "0 6px";
      show.textContent = rbSavedLayers[slot] ? "hide" : "map";
      show.setAttribute("aria-label", (rbSavedLayers[slot] ? "Hide " : "Show ") + (r.name || slot) + " on the map");
      show.addEventListener("click", function () { rbToggleShow(slot); });
      chip.appendChild(show);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "x";
      x.textContent = "\u00d7";
      x.setAttribute("aria-label", "Forget " + (r.name || slot) + " on this device");
      x.addEventListener("click", function () {
        const s2 = rbLoadSaved();
        delete s2[slot];
        rbStoreSaved(s2);
        if (rbSavedLayers[slot]) { try { map.removeLayer(rbSavedLayers[slot]); } catch (e) {} delete rbSavedLayers[slot]; }
        rbRenderSaved();
      });
      chip.appendChild(x);
      list.appendChild(chip);
    });
  }

  let routePrevFocus = null;
  function openRoute() {
    routePrevFocus = document.activeElement;
    rbFillRoadSuggestions();
    try {
      const saved = localStorage.getItem("laf911_pi_email");
      if (saved) els.routeModal.querySelector("#rbPiEmail").value = saved;
    } catch (e) {}
    rbRenderSaved();
    rbRender();
    els.routeModal.classList.add("open");
    els.routeModal.setAttribute("aria-hidden", "false");
    els.routeModal.querySelector("#rbName").focus();
  }
  function closeRoute() {
    els.routeModal.classList.remove("open");
    els.routeModal.setAttribute("aria-hidden", "true");
    if (routePrevFocus && routePrevFocus.focus) { try { routePrevFocus.focus(); } catch (e) {} }
  }

  /* ═══════════ About dialog ═══════════ */
  let aboutPrevFocus = null;
  function openAbout() {
    aboutPrevFocus = document.activeElement;
    const genIso = (dataMeta && dataMeta.generated_at) || PAGE_GENERATED_AT;
    if (genIso) {
      const g = new Date(genIso);
      els.aboutGenerated.textContent = isNaN(g.getTime()) ? "" :
        "Data last generated " + agoShort(Date.now() - g.getTime()) + " (" + g.toLocaleString() + "). " +
        "Archive: " + INCIDENTS.length.toLocaleString() + " mapped incidents.";
    }
    els.aboutModal.classList.add("open");
    els.aboutModal.setAttribute("aria-hidden", "false");
    els.aboutClose.focus();
  }
  function closeAbout() {
    els.aboutModal.classList.remove("open");
    els.aboutModal.setAttribute("aria-hidden", "true");
    if (aboutPrevFocus && aboutPrevFocus.focus) { try { aboutPrevFocus.focus(); } catch (e) {} }
  }

  /* ═══════════ corridor detail dialog ═══════════ */
  let corridorPrevFocus = null;

  // Per-day counts for a row between fromMs (inclusive) and toMs (exclusive).
  // Aggregated
  // TRAFFIC CONTROL rows spread across their exported per-day history;
  // ordinary rows count once by their best date.
  function countInWindow(row, fromMs, toMs) {
    if (isRoutineTC(row)) {
      const hist = tcHistoryOf(row) || [];
      let n = 0;
      for (const h of hist) {
        const d = new Date(h[0] + "T12:00:00");
        const ms = d.getTime();
        if (!isNaN(ms) && ms >= fromMs && ms < toMs) n += (parseInt(h[1], 10) || 0);
      }
      return n;
    }
    const dt = bestRowDate(row);
    if (!dt) return 0;
    const ms = dt.getTime();
    return (ms >= fromMs && ms < toMs) ? 1 : 0;
  }

  function corridorDetailHtml(cid, f) {
    // Everything here respects the active filters — except the corridor
    // filter itself, so details open the same way whether or not the map is
    // already filtered to a corridor.
    const base = filteredIncidents(f, map, { corridor: 1 });
    const rows = [];
    for (const r of base) { if (corridorsOf(r).indexOf(cid) !== -1) rows.push(r); }

    let total = 0, accidents = 0;
    const byHour = new Array(24).fill(0);
    const byDow = new Array(7).fill(0);
    const byLoc = new Map();
    for (const row of rows) {
      const n = incidentCount(row);
      total += n;
      if (categoryOf(row[IDX_CAUSE]).id === "accident") accidents += n;
      const h = rowHourOf(row);
      if (h != null) byHour[h] += n;
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr && pr.dt) byDow[pr.dt.getDay()] += n;
      const loc = String(row[IDX_LOCATION] || "").trim().toUpperCase();
      if (loc) byLoc.set(loc, (byLoc.get(loc) || 0) + n);
    }

    const dowNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    const peakH = byHour.indexOf(Math.max.apply(null, byHour));
    const peakHourTxt = byHour[peakH] > 0 ? (fmtHour(peakH) + "–" + fmtHour((peakH + 1) % 24)) : "—";
    const peakD = byDow.indexOf(Math.max.apply(null, byDow));
    const peakDayTxt = byDow[peakD] > 0 ? dowNames[peakD] : "—";

    // 30-day comparison: computed WITHOUT the quick-range filter (it needs
    // both windows), but every other active filter still applies.
    const cmpBase = filteredIncidents(f, map, { corridor: 1, range: 1 });
    const nowMs = Date.now();
    const D30 = 30 * 86400000;
    let cur30 = 0, prev30 = 0;
    for (const row of cmpBase) {
      if (corridorsOf(row).indexOf(cid) === -1) continue;
      cur30 += countInWindow(row, nowMs - D30, nowMs + 86400000);
      prev30 += countInWindow(row, nowMs - 2 * D30, nowMs - D30);
    }
    let cmpTxt;
    if (prev30 > 0) {
      const chg = ((cur30 - prev30) / prev30) * 100;
      const dir = chg >= 0 ? "up" : "down";
      cmpTxt = "<b>" + cur30.toLocaleString() + "</b> vs <b>" + prev30.toLocaleString() + "</b> " +
        "<span class='trend-delta " + dir + "'>" + (chg >= 0 ? "▲" : "▼") + " " + Math.abs(chg).toFixed(0) + "%</span>";
    } else if (cur30 > 0) {
      cmpTxt = "<b>" + cur30.toLocaleString() + "</b> vs <b>0</b> — no baseline in the previous 30 days";
    } else {
      cmpTxt = "none in either window";
    }

    const topSpots = Array.from(byLoc.entries()).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 5);
    let spotsHtml = "";
    if (topSpots.length) {
      spotsHtml = "<h3>Busiest reported spots</h3>" + topSpots.map(function (e) {
        return "<div class='cd-spot'><span>" + esc(titleCase(e[0])) + "</span><b>" + e[1].toLocaleString() + "</b></div>";
      }).join("");
    }

    const isSel = f.corridorId === cid;
    return "<div class='cd-grid'>" +
      "<div class='cd-stat'><b>" + total.toLocaleString() + "</b><span>total incidents</span></div>" +
      "<div class='cd-stat'><b>" + accidents.toLocaleString() + "</b><span>accidents</span></div>" +
      "<div class='cd-stat'><b>" + esc(peakHourTxt) + "</b><span>peak hour</span></div>" +
      "<div class='cd-stat'><b>" + esc(peakDayTxt) + "</b><span>busiest day</span></div>" +
      "</div>" +
      "<p>Last 30 days vs previous 30: " + cmpTxt + "</p>" +
      spotsHtml +
      "<div class='cd-actions'>" +
      "<button type='button' class='mini-clear' id='cdFilterBtn'>" +
      (isSel ? "Clear the corridor map filter" : "Filter the map to this corridor") + "</button></div>" +
      "<p class='facet-note'>All numbers respect your current filters (the 30-day comparison ignores the " +
      "quick date range so both windows stay comparable). Routine traffic-control groups count every " +
      "recorded occurrence.</p>";
  }

  function openCorridorDetail(cid) {
    corridorPrevFocus = document.activeElement;
    const f = currentFilterObj();
    els.corridorTitleText.textContent = titleCase(cid);
    els.corridorBody.innerHTML = corridorDetailHtml(cid, f);
    const fb = document.getElementById("cdFilterBtn");
    if (fb) fb.addEventListener("click", function () {
      closeCorridorDetail();
      setCorridor(cid);   // toggles: applies the filter, or clears it if set
    });
    els.corridorModal.classList.add("open");
    els.corridorModal.setAttribute("aria-hidden", "false");
    els.corridorClose.focus();
  }
  function closeCorridorDetail() {
    els.corridorModal.classList.remove("open");
    els.corridorModal.setAttribute("aria-hidden", "true");
    if (corridorPrevFocus && corridorPrevFocus.focus) { try { corridorPrevFocus.focus(); } catch (e) {} }
  }

  /* ═══════════ map legend dialog ═══════════ */
  let legendPrevFocus = null;

  const CATEGORY_DESCS = {
    accident: "Crashes, collisions, hit-and-runs.",
    fire: "Vehicle fires and smoke reports.",
    hazard: "Debris, flooding, trees or lines down, spills, animals.",
    control: "Traffic control and signal problems.",
    vehicle: "Stalled, disabled or abandoned vehicles.",
    medical: "Medical emergencies and pedestrian incidents.",
    other: "Anything that doesn't match the groups above."
  };

  function legendBodyHtml() {
    let html = "<h3>Incident colors</h3>";
    for (const cat of CATEGORIES) {
      html += "<div class='lg-row'><span class='lg-sym'><span class='lg-dot' style='--cat:" + cat.color + "'></span></span>" +
        "<div><b>" + esc(cat.label) + "</b> — " + esc(CATEGORY_DESCS[cat.id] || "") + "</div></div>";
    }
    html += "<h3>Marker shapes</h3>" +
      "<div class='lg-row'><span class='lg-sym'><span class='lg-dot' style='--cat:#e5484d'></span></span>" +
      "<div><b>Small circle</b> — one incident; the color is its category above.</div></div>" +
      "<div class='lg-row'><span class='lg-sym'><span class='lg-count' style='--cat:#e5484d'>3</span></span>" +
      "<div><b>Numbered stack</b> — several incidents share this exact point; the number is the combined count " +
      "and the border shows the most common category. <b>Tap it to browse each incident separately.</b></div></div>" +
      "<div class='lg-row'><span class='lg-sym'><span class='lg-cluster'></span></span>" +
      "<div><b>Translucent circle</b> — on very large result sets, nearby incidents are grouped (roughly a city block); " +
      "tap for the full list, and the busiest groups show their combined count. Zoom in for exact points.</div></div>" +
      "<div class='lg-row'><span class='lg-sym'><span class='lg-beacon'></span></span>" +
      "<div><b>Pulsing halo</b> — reported within the last 2 hours.</div></div>" +
      "<div class='lg-row'><span class='lg-sym'><span class='lg-count' style='--cat:#2563eb'>12</span></span>" +
      "<div><b>Blue numbered stack</b> — routine recurring traffic control (e.g. daily school car-rider duty) collapsed " +
      "into one point so it can't crowd out real incidents; every occurrence stays counted.</div></div>";
    html += "<h3>Duplicate feed listings</h3>" +
      "<p style='font-size:12px'>Dispatch often logs one crash more than once within a few minutes — " +
      "a hit-and-run also logged as an accident type, or a minor accident upgraded to major. " +
      "The map counts those as <b>one incident</b>; its popup discloses the other entries under " +
      "\u201calso logged in the feed as\u201d. Every raw record stays in the archive.</p>";
    html += "<h3>Optional layers</h3><ul>" +
      "<li><b>Heatmap</b> — density glow of the filtered incidents.</li>" +
      "<li><b>Hot spots (all-time)</b> — precomputed circles scored by recency-weighted history; red = hottest, blue = cooler. Not affected by filters.</li>" +
      "<li><b>Rounded clusters</b> (blue) and <b>micro hotspots</b> (purple) — the filtered incidents grouped at ~100 m / ~10 m.</li>" +
      "<li><b>OSM intersections (all-time)</b> (teal) — incidents snapped to the nearest real road junction. Not affected by filters.</li>" +
      "<li><b>Distance rings</b> — 1/2/3/5/8 km around the dataset center.</li></ul>";
    html += "<h3>Location precision</h3>";
    for (const key of CONFIDENCE_ORDER) {
      const cl = CONFIDENCE_LEVELS[key];
      html += "<div class='lg-row'><span class='lg-sym'><span class='dot' style='background:" + cl.color + ";width:10px;height:10px;border-radius:50%;display:inline-block'></span></span>" +
        "<div><b>" + esc(cl.label) + "</b> — " + esc(cl.desc) + "</div></div>";
    }
    html += "<p class='facet-note'>Each incident popup shows its own precision. “In the public feed” times " +
      "measure how long an incident stayed listed on the public 911 feed — they are not response or " +
      "clearance times.</p>";
    return html;
  }

  function openLegend() {
    legendPrevFocus = document.activeElement;
    els.legendBody.innerHTML = legendBodyHtml();
    els.legendModal.classList.add("open");
    els.legendModal.setAttribute("aria-hidden", "false");
    els.legendClose.focus();
  }
  function closeLegend() {
    els.legendModal.classList.remove("open");
    els.legendModal.setAttribute("aria-hidden", "true");
    if (legendPrevFocus && legendPrevFocus.focus) { try { legendPrevFocus.focus(); } catch (e) {} }
  }

  /* ═══════════ data-driven year options ═══════════ */
  // The archive decides which years are offered — not a hardcoded window.
  function buildYearOptions() {
    let minY = null;
    const nowY = new Date().getFullYear();
    for (const row of INCIDENTS) {
      const dt = bestRowDate(row);
      if (!dt) continue;
      const y = dt.getFullYear();
      if (y >= 2000 && y <= nowY + 1 && (minY === null || y < minY)) minY = y;
    }
    if (minY === null) return;
    const sel = $("yearSelect");
    const prev = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    for (let y = nowY; y >= minY; y--) {
      const o = document.createElement("option");
      o.value = String(y);
      o.textContent = String(y);
      sel.appendChild(o);
    }
    if (prev) sel.value = prev;
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

    els.fitBtn.addEventListener("click", function () {
      fitToResults(lastFiltered && lastFiltered.length ? lastFiltered : INCIDENTS);
    });

    els.anClearBtn.addEventListener("click", function () {
      pushHashEntry();
      state.exactHour = null;
      state.corridorId = null;
      els.dowSelect.value = "all";
      toast("Analytics selections cleared");
      scheduleRender(0);
    });

    // A broad time block replaces any exact-hour pick (and vice versa —
    // the charts reset this dropdown), so they never silently AND together.
    els.timeBlockSelect.addEventListener("change", function () {
      if (els.timeBlockSelect.value !== "all" && state.exactHour != null) {
        state.exactHour = null;
      }
    });

    $("locBtn").addEventListener("click", locateMe);

    els.aboutBtn.addEventListener("click", openAbout);
    els.aboutClose.addEventListener("click", closeAbout);
    els.aboutModal.addEventListener("click", function (e) {
      if (e.target === els.aboutModal) closeAbout();
    });
    els.corridorClose.addEventListener("click", closeCorridorDetail);
    els.corridorModal.addEventListener("click", function (e) {
      if (e.target === els.corridorModal) closeCorridorDetail();
    });
    els.legendClose.addEventListener("click", closeLegend);
    els.legendModal.addEventListener("click", function (e) {
      if (e.target === els.legendModal) closeLegend();
    });
    els.routeBtn.addEventListener("click", openRoute);
    els.routeClose.addEventListener("click", closeRoute);
    els.routeModal.addEventListener("click", function (e) {
      if (e.target === els.routeModal) closeRoute();
    });
    els.routeModal.querySelector("#rbAdd").addEventListener("click", rbAdd);
    els.routeModal.querySelector("#rbDraw").addEventListener("click", rbStartDraw);
    $("rbUndo").addEventListener("click", function () {
      if (rbBusy) return;
      rbAnchors.pop();
      rbLegs.pop();
      rbRebuildPath();
    });
    els.routeModal.querySelector("#rbPiEmail").addEventListener("input", function () {
      try { localStorage.setItem("laf911_pi_email", this.value.trim()); } catch (e) {}
      rbRender();
    });
    $("rbCancel").addEventListener("click", function () { rbFinishDraw(false); });
    $("rbDone").addEventListener("click", function () { rbFinishDraw(true); });
    els.routeModal.querySelector("#rbRoad").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); rbAdd(); }
    });
    ["#rbSlot", "#rbName", "#rbDepart", "#rbDays"].forEach(function (sel) {
      els.routeModal.querySelector(sel).addEventListener("input", rbRender);
    });
    els.routeModal.querySelector("#rbMail").addEventListener("click", function () {
      rbSaveCurrent();   // keep a device-local copy so it can be shown on the map
    });
    els.routeModal.querySelector("#rbCopy").addEventListener("click", function () {
      rbSaveCurrent();
      const text = els.routeModal.__config || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { toast("Settings copied"); },
          function () { toast("Copy failed — select the text manually"); });
      } else { toast("Copy not supported — select the text manually"); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && els.aboutModal.classList.contains("open")) closeAbout();
      if (e.key === "Escape" && els.routeModal.classList.contains("open")) closeRoute();
      if (e.key === "Escape" && els.corridorModal.classList.contains("open")) closeCorridorDetail();
      if (e.key === "Escape" && els.legendModal.classList.contains("open")) closeLegend();
    });

    // Back/forward (chart selections push history entries) and hand-edited
    // hashes restore the full filter state.
    window.addEventListener("hashchange", function () {
      if (hashApplying) return;
      applyHash();
      renderLegend();
      renderAgencyChecklist();
      scheduleRender(0);
    });

    const changeIds = [
      "causeGroupSelect", "causeSelect", "chkInViewOnly",
      "monthSelect", "daySelect", "yearSelect", "dateFrom", "dateTo",
      "dayTypeSelect", "timeBlockSelect",
      "chkRushHour", "chkSchoolDay", "dowSelect", "roadTypeSelect", "chkExcludeLowConf",
      "chkFloodWarning", "chkThunderstormWarning", "chkTornadoWatch",
      "chkHoliday", "lightSelect",
      "chkWeatherOnly", "tempBand", "precipBand", "precipAmountBand", "windBand", "visBand", "cloudBand",
      "chkPoints", "chkHeat", "chkIntersections", "chkOsmIntersections", "chkMicro", "chkRings", "chkHotSpots",
      "topNSelect", "precIntersections", "precMicro"
    ];
    for (const id of changeIds) {
      const el = $(id);
      if (el) el.addEventListener("change", function () { scheduleRender(0); });
    }

    // Stat tiles double as the time-range filter.
    els.rangeTiles.addEventListener("click", function (e) {
      const tile = e.target.closest(".stat-tile");
      if (tile) setRange(tile.getAttribute("data-range") || "");
    });

    // Collapsible filter sections; remembers open/closed per section.
    let accState = {};
    try { accState = JSON.parse(localStorage.getItem("laf911.acc") || "{}"); } catch (e) {}
    document.querySelectorAll("#panelFilters .acc").forEach(function (acc) {
      const id = acc.getAttribute("data-acc");
      if (accState[id] === true) acc.classList.add("open");
      else if (accState[id] === false) acc.classList.remove("open");
      const head = acc.querySelector(".acc-head");
      if (head) head.addEventListener("click", function () {
        const open = acc.classList.toggle("open");
        accState[id] = open;
        try { localStorage.setItem("laf911.acc", JSON.stringify(accState)); } catch (e) {}
      });
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

    // Mobile bottom sheet: drag the grab handle (or header) up/down to size it,
    // following the finger and snapping open/closed on release. A plain tap on
    // the handle still toggles.
    (function () {
      const COLLAPSED_H = 236;
      function expandedH() { return Math.round(window.innerHeight * 0.94); }
      const header = els.sidebar.querySelector(".sb-header");
      let drag = null;

      function startDrag(clientY, target) {
        if (window.innerWidth > 700) return;
        if (target && target.closest && target.closest("button")) return;  // don't hijack controls
        drag = { startY: clientY, startH: els.sidebar.getBoundingClientRect().height, moved: false };
        els.sidebar.classList.add("dragging");
      }
      function moveDrag(clientY, ev) {
        if (!drag) return;
        const dy = clientY - drag.startY;
        if (Math.abs(dy) > 5) drag.moved = true;
        let h = drag.startH - dy;                     // drag up → taller
        h = Math.max(COLLAPSED_H, Math.min(expandedH(), h));
        els.sidebar.style.height = h + "px";
        // Suppress the page/map from scrolling while an actual drag is underway.
        if (drag.moved && ev && ev.cancelable) ev.preventDefault();
      }
      function endDrag() {
        if (!drag) return;
        const h = els.sidebar.getBoundingClientRect().height;
        const moved = drag.moved;
        drag = null;
        els.sidebar.classList.remove("dragging");     // restore the snap transition
        if (!moved) {
          els.sidebar.style.height = "";              // a tap: let the click handler toggle
          return;
        }
        setSheetExpanded(h > (COLLAPSED_H + expandedH()) / 2);
        els.sidebar.style.height = "";                // hand height back to the CSS class (animated)
      }

      [els.sbHandle, header].forEach(function (zone) {
        if (!zone) return;
        zone.addEventListener("touchstart", function (e) {
          startDrag(e.touches[0].clientY, e.target);
        }, { passive: true });
      });
      // Track move/end on the document so the gesture survives the finger
      // sliding off the small handle.
      document.addEventListener("touchmove", function (e) {
        if (drag) moveDrag(e.touches[0].clientY, e);
      }, { passive: false });
      document.addEventListener("touchend", endDrag, { passive: true });
      document.addEventListener("touchcancel", endDrag, { passive: true });

      // Plain tap / click on the handle toggles (fires only when no drag moved,
      // since a real drag calls preventDefault and suppresses the click).
      els.sbHandle.addEventListener("click", function () {
        if (window.innerWidth <= 700) setSheetExpanded(!els.sidebar.classList.contains("expanded"));
      });
    })();

    // Pull-to-refresh on the Feed tab: drag down from the top of the list to
    // fetch the latest data file immediately. A tap on ↻ does the same.
    (function () {
      const bar = $("ptrBar"), icon = $("ptrIcon"), text = $("ptrText");
      const ARM_AT = 52, MAX_H = 68;
      let startY = null, pulling = false, loading = false;

      function setBar(h) { bar.style.height = Math.max(0, Math.min(MAX_H, h)) + "px"; }
      function finish() {
        loading = false;
        bar.classList.remove("loading", "armed", "dragging");
        icon.textContent = "↓";
        text.textContent = "Pull to refresh";
        setBar(0);
      }
      function trigger() {
        if (loading) return;
        loading = true;
        bar.classList.add("loading");
        bar.classList.remove("dragging");
        icon.textContent = "↻";
        text.textContent = "Refreshing…";
        setBar(44);
        reloadData(true, function () { setTimeout(finish, 350); });
      }

      els.sbBody.addEventListener("touchstart", function (e) {
        if (loading) return;
        if (!els.panelFeed.classList.contains("active")) return;
        if (els.sbBody.scrollTop > 0) return;
        startY = e.touches[0].clientY;
        pulling = false;
      }, { passive: true });

      els.sbBody.addEventListener("touchmove", function (e) {
        if (startY == null || loading) return;
        const dy = e.touches[0].clientY - startY;
        if (dy <= 0) { if (!pulling) startY = null; return; }
        pulling = true;
        bar.classList.add("dragging");
        const h = dy * 0.45;
        setBar(h);
        bar.classList.toggle("armed", h >= ARM_AT);
        if (e.cancelable) e.preventDefault();
      }, { passive: false });

      function endPull() {
        if (startY == null || loading) { startY = null; return; }
        startY = null;
        if (!pulling) return;
        pulling = false;
        bar.classList.remove("dragging");
        if (bar.classList.contains("armed")) trigger();
        else setBar(0);
      }
      els.sbBody.addEventListener("touchend", endPull, { passive: true });
      els.sbBody.addEventListener("touchcancel", endPull, { passive: true });

      els.feedRefreshBtn.addEventListener("click", function () {
        if (loading) return;
        els.feedRefreshBtn.classList.add("spin");
        reloadData(true, function () {
          setTimeout(function () { els.feedRefreshBtn.classList.remove("spin"); }, 350);
        });
      });
    })();

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
      map.on("popupclose", function (e) {
        if (e && e.popup === activePopup) activePopup = null;
      });

      // Map-level tap resolver: every click is matched against the drawn
      // incident groups and resolved to the nearest one within a generous
      // radius. This makes taps forgiving (no need to hit a dot dead-center)
      // and guarantees the nearest group wins — Leaflet's own canvas hit-test
      // picks the last-drawn layer, which let an overlapping single marker
      // steal taps from a large same-coordinates group.
      const TAP_RADIUS = isTouch ? 28 : 16;

      map.on("click", function (e) {
        if (!e || !e.containerPoint) return;
        if (rbDrawing) { rbMapClick(e.latlng); return; }
        // During the post-open grace window ignore clicks entirely: the touch
        // browser's synthesized ghost click (~300 ms after a tap, at the old
        // screen position while the map is panning) would otherwise re-target
        // or dismiss the popup the tap just opened.
        if (activePopup && Date.now() - activePopupOpenedAt < 700) return;

        let best = null, bestDist = Infinity;
        for (const t of hitTargets) {
          const p = map.latLngToContainerPoint([t.lat, t.lng]);
          const d = Math.hypot(p.x - e.containerPoint.x, p.y - e.containerPoint.y);
          if (d > TAP_RADIUS) continue;
          // Routine traffic-control groups yield to real incidents: they
          // carry a 12px handicap, so an overlapping accident wins the tap
          // unless the TC point is decisively closer.
          const eff = t.routine ? d + 12 : d;
          if (eff < bestDist - 0.5 || (Math.abs(eff - bestDist) <= 0.5 && best && t.rows.length > best.rows.length)) {
            best = t;
            bestDist = eff;
          }
        }
        if (best) {
          openIncidentPopup([best.lat, best.lng], best.rows, { label: best.label });
        } else if (activePopup) {
          map.closePopup();
        }
      });

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
    setInterval(renderStatus, 15000);
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
  buildLocHistory();
  buildCauseDropdown();
  buildAgencyChecklist();
  buildCauseGroupDropdown();
  buildYearOptions();
  applyHash();
  renderLegend();
  renderAgencyChecklist();   // reflect any agencies restored from the URL hash
  // Learn the live data version so the first background refresh can skip an
  // unchanged download; also feeds "data …" in the status row.
  fetchMeta(function (m) {
    if (m) { dataMeta = m; lastDataVersion = m.data_version; renderStatus(); }
  });
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
