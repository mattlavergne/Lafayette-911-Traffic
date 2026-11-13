"""The full-screen "Insights" view of the map page.

The sidebar's Analytics tab describes whatever the map is currently filtered
to (by default the past 24 hours) in a 370 px column. This view answers a
different question — "what does the whole archive say about crashes here?" —
with room to read it, and independent of the map's filters.

Kept out of ``map_template.py`` only for size: the three strings below are
substituted into that template (``__INSIGHTS_CSS__`` / ``__INSIGHTS_HTML__`` /
``__INSIGHTS_JS__``) and the script runs inside the page's own closure, so it
shares the page's data, parsers and helpers.

Statistical ground rules, enforced in the code rather than left to captions:

* **Rates use days the collector was actually running.** The archive has
  months-long holes; a "per day" figure over the calendar span would be badly
  understated. A day counts as covered when it has at least one incident of
  any kind, and the first and last day of each stretch (collection started or
  stopped part-way through) are left out of per-day averages.
* **Each crash counts once**, under the road dispatch logged it on.
* **"Time on the feed" is not clearance time** — it is how long the incident
  stayed listed, measured between two 5-minute checks.
* **No weather claims the data cannot support.** Weather is a forecast value
  attached at report time and there is no record of dry hours, so the only
  comparison offered is whole wet days against whole dry days.
"""

INSIGHTS_CSS = r"""
  /* ── Insights view ────────────────────────────────────────────────── */
  #insightsView {
    position: fixed; inset: 0; z-index: 3500; display: none; overflow-y: auto;
    background: var(--bg); color: var(--text); -webkit-overflow-scrolling: touch;
    overscroll-behavior: contain;
  }
  #insightsView.open { display: block; }
  .iv-wrap { max-width: 1180px; margin: 0 auto; padding: 0 20px calc(40px + env(safe-area-inset-bottom, 0px)) 20px; }
  .iv-head {
    position: sticky; top: 0; z-index: 2; background: var(--bg);
    padding: calc(14px + env(safe-area-inset-top, 0px)) 0 12px 0;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    border-bottom: 1px solid var(--hairline); margin-bottom: 16px;
  }
  .iv-head h2 { font-size: 20px; margin: 0; letter-spacing: -0.01em; flex: 1 1 auto; min-width: 0; }
  .iv-head h2 small { display: block; font-size: 12px; font-weight: 500; color: var(--text-2); letter-spacing: 0; margin-top: 2px; }
  .iv-seg { display: inline-flex; background: var(--chip); border-radius: 999px; padding: 3px; }
  .iv-seg button {
    font: 600 12px var(--font); color: var(--text-2); background: transparent; border: 0;
    border-radius: 999px; padding: 6px 12px; cursor: pointer; min-height: 32px;
  }
  .iv-seg button[aria-pressed="true"] { background: var(--panel-solid); color: var(--text); box-shadow: var(--shadow-sm); }
  .iv-seg button:focus-visible, .iv-x:focus-visible, .iv-link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .iv-x {
    font: 600 13px var(--font); color: var(--text); background: var(--chip); border: 0; border-radius: 999px;
    padding: 0 14px; min-height: 38px; cursor: pointer;
  }
  .iv-x:hover { background: var(--chip-hover); }
  .iv-note { font-size: 12px; color: var(--text-2); line-height: 1.5; margin: 0 0 16px 0; }
  .iv-note b { color: var(--text); font-weight: 600; }

  .iv-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .iv-kpi { background: var(--panel-solid); border: 1px solid var(--hairline); border-radius: 16px; padding: 14px 16px; min-width: 0; }
  .iv-kpi .k-label { font-size: 11.5px; color: var(--text-2); font-weight: 600; }
  .iv-kpi .k-val { font-size: 28px; font-weight: 700; letter-spacing: -0.02em; margin: 4px 0 2px 0; font-variant-numeric: tabular-nums; }
  .iv-kpi .k-val small { font-size: 14px; font-weight: 600; color: var(--text-2); letter-spacing: 0; }
  .iv-kpi .k-sub { font-size: 11.5px; color: var(--text-2); line-height: 1.4; }
  .iv-delta { font-weight: 700; color: var(--text); white-space: nowrap; }

  .iv-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 12px; }
  .iv-card { grid-column: span 6; background: var(--panel-solid); border: 1px solid var(--hairline); border-radius: 18px; padding: 16px 18px 18px 18px; min-width: 0; }
  .iv-card.wide { grid-column: span 12; }
  .iv-card.third { grid-column: span 4; }
  .iv-card h3 { font-size: 14px; margin: 0 0 2px 0; letter-spacing: -0.005em; }
  .iv-card .iv-sub { font-size: 12px; color: var(--text-2); margin: 0 0 12px 0; line-height: 1.45; }
  .iv-foot { font-size: 11px; color: var(--text-3); margin: 10px 0 0 0; line-height: 1.45; }
  .iv-empty { font-size: 12.5px; color: var(--text-2); padding: 18px 0; }

  .iv-findings { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px 22px; }
  .iv-findings li { font-size: 13px; line-height: 1.5; color: var(--text-2); padding-left: 14px; position: relative; }
  .iv-findings li::before { content: ""; position: absolute; left: 0; top: 8px; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
  .iv-findings b { color: var(--text); font-weight: 650; }

  .iv-svg { width: 100%; display: block; overflow: visible; }
  .iv-hm { max-width: 780px; min-width: 560px; }
  .iv-hm .ax { font-size: 10px; }
  .iv-svg .b { fill: var(--accent); opacity: 0.42; }
  .iv-svg .b.pk { opacity: 1; }
  .iv-svg .b:hover, .iv-svg .b:focus { opacity: 1; outline: none; }
  .iv-svg .ax { font: 11.5px var(--font); fill: var(--text-3); }
  .iv-svg .vl { font: 600 12px var(--font); fill: var(--text-2); }
  .iv-svg .gl { stroke: var(--hairline); stroke-width: 1; }
  .iv-svg .hm { fill: var(--accent); stroke: var(--panel-solid); stroke-width: 2; }
  .iv-svg .hm:hover, .iv-svg .hm:focus { stroke: var(--text); stroke-width: 1.5; outline: none; }
  .iv-svg .hm0 { fill: var(--bar-track); stroke: var(--panel-solid); stroke-width: 2; }
  .iv-scale { display: flex; align-items: center; gap: 6px; font-size: 10.5px; color: var(--text-3); margin-top: 8px; }
  .iv-scale i { display: inline-block; width: 14px; height: 10px; border-radius: 2px; background: var(--accent); }
  .iv-scale .nd { background: repeating-linear-gradient(135deg, var(--bar-track) 0 2px, transparent 2px 4px); border: 1px solid var(--hairline); }

  .iv-cal { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 16px 18px; }
  .iv-mon .m-name { font-size: 11.5px; font-weight: 650; color: var(--text); margin-bottom: 5px; display: flex; justify-content: space-between; }
  .iv-mon .m-name span { font-weight: 500; color: var(--text-3); font-variant-numeric: tabular-nums; }
  .iv-days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
  .iv-day { aspect-ratio: 1; border-radius: 3px; border: 0; padding: 0; background: transparent; position: relative; }
  .iv-day.d { cursor: pointer; background: var(--bar-track); overflow: hidden; }
  .iv-day.d i { position: absolute; inset: 0; background: var(--accent); }
  .iv-day.d:hover, .iv-day.d:focus-visible { outline: 2px solid var(--text); outline-offset: 1px; }
  .iv-day.nd { background: repeating-linear-gradient(135deg, var(--bar-track) 0 2px, transparent 2px 4px); }
  .iv-dow { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; font-size: 9px; color: var(--text-3); text-align: center; margin-bottom: 2px; }

  .iv-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .iv-table th { text-align: right; font-size: 10.5px; font-weight: 600; color: var(--text-3); padding: 0 0 6px 10px; white-space: nowrap; }
  .iv-table th:first-child, .iv-table td:first-child { text-align: left; padding-left: 0; }
  .iv-table td { padding: 7px 0 7px 10px; border-top: 1px solid var(--hairline); text-align: right; font-variant-numeric: tabular-nums; color: var(--text-2); vertical-align: middle; }
  .iv-table td.n { color: var(--text); font-weight: 650; }
  .iv-table td:first-child { position: relative; padding-left: 20px; }
  .iv-table .rk { color: var(--text-3); font-size: 11px; position: absolute; left: 0; top: 9px; }
  .iv-link { font: 600 12.5px var(--font); color: var(--text); background: none; border: 0; padding: 0; cursor: pointer; text-align: left; }
  .iv-link:hover { color: var(--accent); text-decoration: underline; }
  .iv-table .loc-sub { display: block; font-size: 10.5px; color: var(--text-3); font-weight: 500; }
  .iv-mini { display: inline-block; width: 64px; height: 6px; border-radius: 3px; background: var(--bar-track); vertical-align: middle; margin-right: 6px; overflow: hidden; }
  .iv-mini i { display: block; height: 100%; background: var(--accent); border-radius: 3px; }
  .iv-scroll { overflow-x: auto; }

  .iv-hbar { display: grid; grid-template-columns: minmax(96px, 150px) 1fr auto; gap: 6px 10px; align-items: center; font-size: 12.5px; }
  .iv-hbar .t { background: var(--bar-track); border-radius: 4px; height: 12px; position: relative; overflow: hidden; }
  .iv-hbar .t i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--accent); border-radius: 0 4px 4px 0; }
  .iv-hbar .t i.rng { background: var(--accent); opacity: 0.28; border-radius: 4px; }
  .iv-hbar .v { font-variant-numeric: tabular-nums; color: var(--text); font-weight: 650; white-space: nowrap; }
  .iv-hbar .v small { color: var(--text-3); font-weight: 500; }
  .iv-hbar .l { color: var(--text-2); }

  #ivTip {
    position: fixed; z-index: 3600; pointer-events: none; display: none; max-width: 240px;
    background: var(--text); color: var(--bg); font: 500 11.5px/1.4 var(--font);
    padding: 6px 9px; border-radius: 8px; box-shadow: var(--shadow-sm);
  }
  #ivTip b { font-weight: 700; }

  .iv-cta {
    width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; cursor: pointer;
    background: var(--accent-soft); border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-radius: var(--radius-sm); padding: 10px 12px; margin: 0 0 12px 0; color: var(--text); font-family: var(--font);
  }
  .iv-cta:hover { background: color-mix(in srgb, var(--accent) 18%, transparent); }
  .iv-cta b { display: block; font-size: 12.5px; }
  .iv-cta span.s { display: block; font-size: 11px; color: var(--text-2); margin-top: 1px; }
  .iv-cta .go { margin-left: auto; font-size: 16px; color: var(--accent); }
  .an-scope { font-size: 11px; color: var(--text-2); background: var(--chip); border-radius: 10px; padding: 7px 10px; margin: 0 0 10px 0; line-height: 1.45; }
  .an-scope button { font: 600 11px var(--font); color: var(--accent); background: none; border: 0; padding: 0; cursor: pointer; text-decoration: underline; }

  @media (max-width: 900px) {
    .iv-card, .iv-card.third { grid-column: span 12; }
  }
  @media (max-width: 700px) {
    .iv-wrap { padding-left: 14px; padding-right: 14px; }
    .iv-head h2 { font-size: 17px; flex-basis: calc(100% - 140px); }
    .iv-head .iv-seg { order: 3; width: 100%; }
    .iv-head .iv-seg button { flex: 1; }
    .iv-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .iv-kpi .k-val { font-size: 23px; }
    .iv-card { padding: 14px; border-radius: 16px; }
    .iv-cal { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .iv-table .opt { display: none; }
  }
"""

INSIGHTS_HTML = r"""
<div id="insightsView" role="dialog" aria-modal="true" aria-labelledby="ivTitle" aria-hidden="true">
  <div class="iv-wrap">
    <div class="iv-head">
      <h2 id="ivTitle">Crash insights<small id="ivSubtitle">Lafayette Parish</small></h2>
      <div class="iv-seg" id="ivPeriod" role="group" aria-label="Period analysed">
        <button type="button" data-p="all" aria-pressed="true">All data</button>
        <button type="button" data-p="90" aria-pressed="false">Last 90 days</button>
        <button type="button" data-p="30" aria-pressed="false">Last 30 days</button>
      </div>
      <button type="button" class="iv-x" id="ivClose">Back to map</button>
    </div>
    <div id="ivBody"></div>
  </div>
</div>
<div id="ivTip" role="tooltip"></div>
"""

INSIGHTS_JS = r"""
  /* ═══════════════════════ insights view ═══════════════════════ */
  const IV = { period: "all", prevFocus: null, prep: null, actions: [] };
  const IV_DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const IV_DOW3 = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const IV_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAY_MS = 86400000;

  function ivKey(dt) { return dt.getFullYear() * 10000 + (dt.getMonth() + 1) * 100 + dt.getDate(); }
  function ivKeyDate(key) { return new Date(Math.floor(key / 10000), Math.floor(key / 100) % 100 - 1, key % 100); }
  function ivFmtDay(d, withYear) {
    return IV_MON[d.getMonth()] + " " + d.getDate() + (withYear ? ", " + d.getFullYear() : "");
  }
  function ivNum(n, digits) {
    return Number(n).toLocaleString(undefined, { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });
  }
  function ivPct(part, whole) { return whole ? Math.round(100 * part / whole) : 0; }
  function ivMedian(sorted) {
    if (!sorted.length) return null;
    const m = sorted.length >> 1;
    return sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2;
  }
  function ivQuantile(sorted, q) {
    if (!sorted.length) return null;
    return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  }
  function ivMins(m) {
    if (m == null) return "–";
    if (m < 60) return Math.round(m) + " min";
    return Math.floor(m / 60) + " h " + String(Math.round(m % 60)).padStart(2, "0");
  }
  function ivHourSpan(h) { return fmtHour(h) + "–" + fmtHour((h + 1) % 24); }
  function ivPlace(location) {
    // "3142 AMBASSADOR CAFFERY PKWY/CURRAN PKWY LAFAYETTE, LA" → block + cross street, town.
    let s = String(location || "").replace(/\s+/g, " ").trim();
    let town = "";
    const m = s.match(/^(.*?)[\s,]+((?:LAF PARISH|[A-Z]+)),\s*LA$/);
    if (m) { s = m[1].trim(); town = m[2] === "LAF PARISH" ? "Parish" : m[2]; }
    const parts = s.split("/");
    const main = titleCase(parts[0].trim());
    const cross = parts.length > 1 ? titleCase(parts.slice(1).join("/").trim()) : "";
    return { main: main, sub: (cross ? "near " + cross : "") + (cross && town ? " · " : "") + (town ? titleCase(town) : "") };
  }

  // Which calendar days the collector was actually running. A parish that
  // averages dozens of incidents a day does not have a real zero-incident
  // day, so a day with nothing at all is a day with no collection — and a
  // rate that divides by it is wrong. Shared with the sidebar's analytics.
  let _coverage = null;
  function dataCoverage() {
    if (_coverage) return _coverage;
    const days = new Set();
    for (const row of INCIDENTS) {
      if (incidentCount(row) > 1) continue;            // aggregated rows span many days
      const pr = parseReported(row[IDX_REPORTED]);
      if (pr) days.add(ivKey(pr.dt));
    }
    const keys = Array.from(days).sort(function (a, b) { return a - b; });
    const segments = [], partial = new Set();
    let start = null, prev = null;
    function closeSeg() {
      if (start == null) return;
      segments.push({ from: start, to: prev });
      partial.add(start); partial.add(prev);           // collection began / ended mid-day
    }
    for (const k of keys) {
      if (prev != null && (ivKeyDate(k) - ivKeyDate(prev)) > 1.5 * DAY_MS) { closeSeg(); start = k; }
      if (start == null) start = k;
      prev = k;
    }
    closeSeg();
    partial.add(ivKey(new Date()));                    // today is still filling in
    const full = new Set(keys.filter(function (k) { return !partial.has(k); }));
    const gaps = [];
    for (let i = 1; i < segments.length; i++) {
      const a = ivKeyDate(segments[i - 1].to), b = ivKeyDate(segments[i].from);
      gaps.push({ from: a, to: b, days: Math.round((b - a) / DAY_MS) - 1 });
    }
    _coverage = { days: days, full: full, segments: segments, gaps: gaps, keys: keys };
    return _coverage;
  }

  const IV_SEVERE = /FATAL|W\/\s*INJ|INJURIE|RESCUE|MAJOR/;
  function ivPrep() {
    if (IV.prep) return IV.prep;
    const crashes = [], feed = [];
    let maxMs = 0;
    for (const row of INCIDENTS) {
      if (incidentCount(row) > 1) continue;
      const pr = parseReported(row[IDX_REPORTED]);
      if (!pr) continue;
      const cause = String(row[IDX_CAUSE] || "").toUpperCase();
      const cat = categoryOf(cause).id;
      const ms = pr.dt.getTime();
      if (ms > maxMs) maxMs = ms;
      const vis = visibleMinutesOf(row);
      const done = vis != null && !isInFeedNow(row);
      if (cat === "accident") {
        const hitrun = cause.indexOf("HIT") !== -1;
        const severe = IV_SEVERE.test(cause) && cause.indexOf("NO INJ") === -1;
        crashes.push({ row: row, ms: ms, key: ivKey(pr.dt), hh: pr.hh, dow: pr.dt.getDay(),
                       severe: severe, hitrun: hitrun, vis: done ? vis : null });
        if (done) feed.push({ ms: ms, vis: vis, g: hitrun ? "Hit-and-run" : (severe ? "Major / injury crash" : "Minor crash") });
      } else if (done) {
        const g = cat === "vehicle" ? "Stalled vehicle" : cat === "hazard" ? "Road hazard"
                : cat === "control" ? "Signal problem" : cat === "fire" ? "Fire call" : null;
        if (g) feed.push({ ms: ms, vis: vis, g: g });
      }
    }
    IV.prep = { crashes: crashes, feed: feed, maxMs: maxMs };
    return IV.prep;
  }

  // Shared tooltip: any element with data-tip inside the view.
  function ivTipShow(target, x, y) {
    const tip = document.getElementById("ivTip");
    tip.innerHTML = target.getAttribute("data-tip");
    tip.style.display = "block";
    const r = tip.getBoundingClientRect();
    let left = x + 12, top = y - r.height - 10;
    if (left + r.width > window.innerWidth - 8) left = Math.max(8, x - r.width - 12);
    if (top < 8) top = y + 16;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  function ivTipHide() { document.getElementById("ivTip").style.display = "none"; }

  function ivBars(values, o) {
    // values: numbers or null (no data). o: {labels, tips, labelEvery, fmt, height, unit}
    const n = values.length, W = 460, H = o.height || 150, top = 18, base = H - 18;
    let max = 0, peak = -1;
    values.forEach(function (v, i) { if (v != null && v > max) { max = v; peak = i; } });
    const slot = W / n, bw = Math.max(2, Math.min(slot - 3, 40));
    let s = '<svg class="iv-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(o.aria || "") + '">';
    s += '<line class="gl" x1="0" x2="' + W + '" y1="' + base + '" y2="' + base + '"/>';
    for (let i = 0; i < n; i++) {
      const v = values[i], cx = slot * i + slot / 2;
      if (v != null) {
        const h = max > 0 ? Math.max(v > 0 ? 1.5 : 0, (v / max) * (base - top)) : 0;
        const r = Math.min(4, bw / 2, h);
        const x = cx - bw / 2, y = base - h;
        s += '<path class="b' + (i === peak ? " pk" : "") + '" tabindex="0" data-tip="' + esc(o.tips[i]) + '" d="M' +
             x + ' ' + base + 'V' + (y + r) + 'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y + 'H' + (x + bw - r) +
             'Q' + (x + bw) + ' ' + y + ' ' + (x + bw) + ' ' + (y + r) + 'V' + base + 'Z"/>';
        if (i === peak) s += '<text class="vl" text-anchor="middle" x="' + cx + '" y="' + (y - 4) + '">' + esc(o.fmt(v)) + '</text>';
      } else {
        s += '<text class="ax" text-anchor="middle" x="' + cx + '" y="' + (base - 4) + '">·</text>';
      }
      if (o.labels[i] && (!o.labelEvery || i % o.labelEvery === 0)) {
        s += '<text class="ax" text-anchor="middle" x="' + cx + '" y="' + (H - 4) + '">' + esc(o.labels[i]) + '</text>';
      }
    }
    return s + "</svg>";
  }

  function ivHeatmap(matrix, exposure) {
    // matrix[dow][hour] = crashes; exposure[dow] = full days of that weekday.
    const cw = 20, ch = 20, left = 30, top = 4, W = left + 24 * cw, H = top + 7 * ch + 16;
    let max = 0;
    const rate = matrix.map(function (r, d) { return r.map(function (v) { const x = exposure[d] ? v / exposure[d] : 0; if (x > max) max = x; return x; }); });
    let s = '<svg class="iv-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Average crashes for each hour of each weekday">';
    const order = [1, 2, 3, 4, 5, 6, 0];
    order.forEach(function (d, ri) {
      s += '<text class="ax" x="0" y="' + (top + ri * ch + ch / 2 + 3.5) + '">' + IV_DOW3[d] + '</text>';
      for (let h = 0; h < 24; h++) {
        const v = rate[d][h];
        const tip = "<b>" + IV_DOW[d] + " " + ivHourSpan(h) + "</b><br>" + ivNum(v, 2) + " crashes on an average " +
                    IV_DOW[d] + " (" + ivNum(matrix[d][h]) + " in total)";
        s += '<rect class="' + (v > 0 ? "hm" : "hm0") + '" tabindex="0" data-tip="' + esc(tip) + '" x="' + (left + h * cw) + '" y="' + (top + ri * ch) +
             '" width="' + cw + '" height="' + ch + '" rx="4"' +
             (v > 0 ? ' style="fill-opacity:' + (0.16 + 0.84 * v / max).toFixed(3) + '"' : "") + '/>';
      }
    });
    for (let h = 0; h < 24; h += 3) {
      s += '<text class="ax" text-anchor="middle" x="' + (left + h * cw + cw / 2) + '" y="' + (H - 3) + '">' + fmtHour(h) + '</text>';
    }
    return s + "</svg>" + '<div class="iv-scale">Fewer <i style="opacity:.12"></i><i style="opacity:.4"></i><i style="opacity:.7"></i><i></i> More · peak cell ' +
           ivNum(max, 1) + ' crashes in the hour</div>';
  }

  function ivCalendar(perDay, cov, fromMs) {
    // Month blocks for every month that has collection in the period.
    let max = 0;
    perDay.forEach(function (v, k) { if (cov.full.has(k) && v > max) max = v; });
    const months = new Map();
    cov.keys.forEach(function (k) {
      if (ivKeyDate(k).getTime() < fromMs - DAY_MS) return;
      const mk = Math.floor(k / 100);
      if (!months.has(mk)) months.set(mk, 0);
      months.set(mk, months.get(mk) + (perDay.get(k) || 0));
    });
    let s = '<div class="iv-cal">';
    months.forEach(function (total, mk) {
      const y = Math.floor(mk / 100), m = mk % 100 - 1;
      const first = new Date(y, m, 1), dim = new Date(y, m + 1, 0).getDate();
      s += '<div class="iv-mon"><div class="m-name">' + IV_MON[m] + " " + y + '<span>' + ivNum(total) + '</span></div>' +
           '<div class="iv-dow"><span>S</span><span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span></div><div class="iv-days">';
      for (let i = 0; i < first.getDay(); i++) s += '<span class="iv-day"></span>';
      for (let d = 1; d <= dim; d++) {
        const k = y * 10000 + (m + 1) * 100 + d, date = new Date(y, m, d);
        const label = IV_DOW3[date.getDay()] + " " + ivFmtDay(date, true);
        if (!cov.days.has(k)) {
          s += '<span class="iv-day nd" data-tip="<b>' + label + '</b><br>No data collected"></span>';
        } else {
          const v = perDay.get(k) || 0;
          const a = IV.actions.push({ t: "day", d: date }) - 1;
          s += '<button type="button" class="iv-day d" data-act="' + a + '" aria-label="' + label + ": " + v + ' crashes" data-tip="<b>' + label +
               "</b><br>" + v + " crash" + (v === 1 ? "" : "es") + (cov.full.has(k) ? "" : " (partial day)") +
               '<br>Tap to see them on the map"><i style="opacity:' + (max ? (0.08 + 0.92 * Math.min(1, v / max)).toFixed(3) : 0) + '"></i></button>';
        }
      }
      s += "</div></div>";
    });
    return s + '</div><div class="iv-scale">Fewer <i style="opacity:.12"></i><i style="opacity:.4"></i><i style="opacity:.7"></i><i></i> More · busiest day ' +
           max + ' · <i class="nd"></i> no data collected</div>';
  }

  function ivRender() {
    const body = document.getElementById("ivBody");
    const prep = ivPrep(), cov = dataCoverage();
    IV.actions = [];
    if (!prep.crashes.length || !cov.full.size) {
      body.innerHTML = '<p class="iv-empty">Not enough crash data yet. This fills in once a few full days have been collected.</p>';
      return;
    }
    const days = IV.period === "all" ? null : parseInt(IV.period, 10);
    const endMs = prep.maxMs;
    const fromMs = days ? endMs - days * DAY_MS : 0;
    const prevFrom = days ? fromMs - days * DAY_MS : 0;
    const inP = function (ms) { return ms > fromMs; };
    const rows = prep.crashes.filter(function (c) { return inP(c.ms); });

    // Exposure: full collected days in the period, overall and per weekday.
    let fullDays = 0, prevFullDays = 0;
    const dowDays = [0, 0, 0, 0, 0, 0, 0];
    cov.full.forEach(function (k) {
      const d = ivKeyDate(k), ms = d.getTime() + DAY_MS / 2;
      if (ms > fromMs) { fullDays++; dowDays[d.getDay()]++; }
      else if (days && ms > prevFrom) prevFullDays++;
    });
    if (!fullDays) {
      body.innerHTML = '<p class="iv-empty">No full day of data in this period yet.</p>';
      return;
    }

    // One pass over the period's crashes.
    const perDay = new Map(), matrix = [], byDow = [0, 0, 0, 0, 0, 0, 0];
    for (let d = 0; d < 7; d++) matrix.push(new Array(24).fill(0));
    const hourAll = new Array(24).fill(0), hourSev = new Array(24).fill(0), hourHit = new Array(24).fill(0);
    const locs = new Map(), cors = new Map();
    let onFull = 0, severe = 0, hitrun = 0, night = 0, nightSev = 0, day = 0, daySev = 0;
    const visAll = [];
    const recentCut = endMs - 60 * DAY_MS;
    for (const c of rows) {
      perDay.set(c.key, (perDay.get(c.key) || 0) + 1);
      const full = cov.full.has(c.key);
      if (full) { onFull++; matrix[c.dow][c.hh]++; byDow[c.dow]++; }
      hourAll[c.hh]++;
      if (c.severe) { severe++; hourSev[c.hh]++; }
      if (c.hitrun) { hitrun++; hourHit[c.hh]++; }
      if (c.hh >= 22 || c.hh < 5) { night++; if (c.severe) nightSev++; }
      else if (c.hh >= 7 && c.hh < 19) { day++; if (c.severe) daySev++; }
      if (c.vis != null) visAll.push(c.vis);
      const lk = String(c.row[IDX_LOCATION] || "").replace(/\s+/g, " ").trim().toUpperCase();
      let L_ = locs.get(lk);
      if (!L_) { L_ = { n: 0, sev: 0, hit: 0, lat: 0, lng: 0, hrs: new Array(24).fill(0), loc: lk }; locs.set(lk, L_); }
      L_.n++; if (c.severe) L_.sev++; if (c.hitrun) L_.hit++; L_.lat += c.row[IDX_LAT]; L_.lng += c.row[IDX_LNG]; L_.hrs[c.hh]++;
      for (const cid of corridorsOf(c.row)) {
        let C_ = cors.get(cid);
        if (!C_) { C_ = { n: 0, sev: 0, hit: 0, vis: [], hrs: new Array(24).fill(0), recent: 0, earlier: 0, id: cid }; cors.set(cid, C_); }
        C_.n++; if (c.severe) C_.sev++; if (c.hitrun) C_.hit++; if (c.vis != null) C_.vis.push(c.vis); C_.hrs[c.hh]++;
        if (full) { if (c.ms > recentCut) C_.recent++; else C_.earlier++; }
      }
    }
    let prevOnFull = 0;
    if (days) for (const c of prep.crashes) if (c.ms <= fromMs && c.ms > prevFrom && cov.full.has(c.key)) prevOnFull++;
    let recentDays = 0, earlierDays = 0;
    cov.full.forEach(function (k) {
      const ms = ivKeyDate(k).getTime() + DAY_MS / 2;
      if (ms > fromMs) { if (ms > recentCut) recentDays++; else earlierDays++; }
    });

    const perDayAvg = onFull / fullDays;
    visAll.sort(function (a, b) { return a - b; });
    const medVis = ivMedian(visAll);

    // Busiest hour of the week, by rate.
    let best = { r: 0, d: 0, h: 0 }, cellSum = 0, cells = 0;
    for (let d = 0; d < 7; d++) for (let h = 0; h < 24; h++) {
      if (!dowDays[d]) continue;
      const r = matrix[d][h] / dowDays[d];
      cellSum += r; cells++;
      if (r > best.r) best = { r: r, d: d, h: h };
    }
    const cellAvg = cells ? cellSum / cells : 0;

    /* ── header + coverage note ── */
    const firstDay = ivKeyDate(cov.keys[0]), lastDay = new Date(endMs);
    const periodFrom = days ? new Date(fromMs) : firstDay;
    document.getElementById("ivSubtitle").textContent =
      ivNum(rows.length) + " crashes · " + ivFmtDay(periodFrom, true) + " – " + ivFmtDay(lastDay, true);
    let note = "Averages use the <b>" + ivNum(fullDays) + " full days</b> the collector was running in this period.";
    const gapsIn = cov.gaps.filter(function (g) { return g.days >= 3 && g.to.getTime() > fromMs; });
    if (gapsIn.length) {
      note += " Nothing was collected " + gapsIn.map(function (g) {
        return "<b>" + ivFmtDay(g.from, true) + " – " + ivFmtDay(g.to, true) + "</b> (" + g.days + " days)";
      }).join(" or ") + ", so those days are left out rather than counted as quiet.";
    }
    note += " Crashes are 911 traffic-accident and hit-and-run calls; each one is counted once.";

    /* ── KPI tiles ── */
    let deltaHtml = "the typical day in this period";
    if (days && prevFullDays >= days * 0.6) {
      const prevAvg = prevOnFull / prevFullDays;
      if (prevAvg > 0) {
        const pct = Math.round(100 * (perDayAvg - prevAvg) / prevAvg);
        deltaHtml = '<span class="iv-delta">' + (pct > 0 ? "▲ " : pct < 0 ? "▼ " : "") + Math.abs(pct) + "%</span> vs the " +
                    days + " days before (" + ivNum(prevAvg, 1) + ")";
      }
    } else if (days) {
      deltaHtml = "no comparable earlier period on record";
    }
    function kpi(label, val, sub) {
      return '<div class="iv-kpi"><div class="k-label">' + label + '</div><div class="k-val">' + val + '</div><div class="k-sub">' + sub + '</div></div>';
    }
    let html = '<p class="iv-note">' + note + '</p><div class="iv-kpis">' +
      kpi("Crashes per day", ivNum(perDayAvg, 1), deltaHtml) +
      kpi("Major or with injuries", ivPct(severe, rows.length) + "<small>%</small>", ivNum(severe) + " of " + ivNum(rows.length) + " crashes") +
      kpi("Hit-and-run", ivPct(hitrun, rows.length) + "<small>%</small>", ivNum(hitrun) + " crashes where a driver left") +
      kpi("Worst hour of the week", IV_DOW3[best.d] + " " + fmtHour(best.h),
          ivNum(best.r, 1) + " crashes in that hour, " + (cellAvg ? ivNum(best.r / cellAvg, 1) + "× the average hour" : "")) +
      kpi("Time on the 911 feed", medVis == null ? "–" : ivMins(medVis),
          medVis == null ? "not measured in this period" : "median for a crash (" + ivNum(visAll.length) + " measured)") +
      "</div>";

    /* ── findings ── */
    const finds = [];
    const dowRate = byDow.map(function (v, d) { return dowDays[d] ? v / dowDays[d] : null; });
    let hiD = -1, loD = -1;
    dowRate.forEach(function (v, d) { if (v == null) return; if (hiD < 0 || v > dowRate[hiD]) hiD = d; if (loD < 0 || v < dowRate[loD]) loD = d; });
    if (hiD >= 0 && hiD !== loD) {
      finds.push("<b>" + IV_DOW[hiD] + "s are the worst day</b>: " + ivNum(dowRate[hiD], 1) + " crashes on average, against " +
                 ivNum(dowRate[loD], 1) + " on " + IV_DOW[loD] + "s.");
    }
    finds.push("<b>" + IV_DOW[best.d] + " " + ivHourSpan(best.h) + "</b> is the single most crash-prone hour of the week (" +
               ivNum(best.r, 1) + " on average).");
    if (night >= 50 && day >= 50) {
      const a = ivPct(nightSev, night), b = ivPct(daySev, day);
      if (Math.abs(a - b) >= 3) {
        finds.push("<b>Night crashes are " + (a > b ? "more" : "less") + " serious</b>: " + a + "% of crashes between 10 pm and 5 am are major or involve injuries, versus " +
                   b + "% during the day.");
      }
    }
    let hitPeak = -1;
    for (let h = 0; h < 24; h++) if (hourAll[h] >= 30 && (hitPeak < 0 || hourHit[h] / hourAll[h] > hourHit[hitPeak] / hourAll[hitPeak])) hitPeak = h;
    if (hitPeak >= 0 && rows.length) {
      finds.push("<b>1 in " + Math.max(1, Math.round(rows.length / Math.max(1, hitrun))) + " crashes is a hit-and-run</b>, rising to " +
                 ivPct(hourHit[hitPeak], hourAll[hitPeak]) + "% around " + fmtHour(hitPeak) + ".");
    }
    const corList = Array.from(cors.values()).sort(function (a, b) { return b.n - a.n; });
    if (corList.length >= 3) {
      const top3 = corList[0].n + corList[1].n + corList[2].n;
      finds.push("<b>Three roads carry " + ivPct(top3, rows.length) + "% of all crashes</b>: " +
                 corList.slice(0, 3).map(function (c) { return esc(titleCase(c.id)); }).join(", ") + ".");
    }
    // Wet days vs dry days (weekdays only, so the weekend dip can't pose as weather).
    const wx = new Map();
    for (const c of rows) {
      if (!cov.full.has(c.key) || c.dow === 0 || c.dow === 6) continue;
      const p = c.row[IDX_PRECIP_IN];
      if (c.row[IDX_TEMP_F] == null) continue;
      let w = wx.get(c.key);
      if (!w) { w = { n: 0, wet: 0 }; wx.set(c.key, w); }
      w.n++; if (p != null && p >= 0.01) w.wet++;
    }
    let wetDays = 0, wetSum = 0, dryDays = 0, drySum = 0;
    wx.forEach(function (w, k) {
      if (w.n < 10) return;
      const share = w.wet / w.n, total = perDay.get(k) || 0;
      if (share >= 0.5) { wetDays++; wetSum += total; } else if (share <= 0.1) { dryDays++; drySum += total; }
    });
    let wetHtml = "";
    if (wetDays >= 8 && dryDays >= 8) {
      const wa = wetSum / wetDays, da = drySum / dryDays, diff = Math.round(100 * (wa - da) / da);
      finds.push(Math.abs(diff) < 8
        ? "<b>Rain does not show up in the totals</b>: weekdays forecast wet average " + ivNum(wa, 1) + " crashes, dry weekdays " + ivNum(da, 1) + "."
        : "<b>Wet weekdays see " + Math.abs(diff) + "% " + (diff > 0 ? "more" : "fewer") + " crashes</b> (" + ivNum(wa, 1) + " vs " + ivNum(da, 1) + " on dry weekdays).");
      wetHtml = "Rain comparison: " + wetDays + " weekdays where most reports carried forecast rain against " + dryDays +
                " dry weekdays. It uses the forecast attached to each report, not a rain gauge, so treat it as a rough check.";
    }
    if (corList.length && recentDays >= 20 && earlierDays >= 20) {
      // Only the busiest roads, and only with real counts on both sides:
      // among hundreds of small roads something always "doubles" by chance.
      const movers = corList.slice(0, 15).filter(function (c) { return c.recent >= 20 && c.earlier >= 20; }).map(function (c) {
        return { id: c.id, a: 7 * c.recent / recentDays, b: 7 * c.earlier / earlierDays };
      }).sort(function (x, y) { return y.a / y.b - x.a / x.b; });
      if (movers.length && movers[0].a / movers[0].b >= 1.25) {
        finds.push("<b>" + esc(titleCase(movers[0].id)) + " is trending up</b>: " + ivNum(movers[0].a, 1) +
                   " crashes a week over the last 60 days, against " + ivNum(movers[0].b, 1) + " before.");
      }
    }
    html += '<div class="iv-grid"><section class="iv-card wide"><h3>What stands out</h3><p class="iv-sub">Worked out from the period selected above.</p>' +
            '<ul class="iv-findings">' + finds.map(function (f) { return "<li>" + f + "</li>"; }).join("") + "</ul>" +
            (wetHtml ? '<p class="iv-foot">' + wetHtml + "</p>" : "") + "</section>";

    /* ── when ── */
    html += '<section class="iv-card wide"><h3>When crashes happen</h3><p class="iv-sub">Average crashes in each hour of each weekday. Darker is worse.</p>' +
            '<div class="iv-scroll"><div class="iv-hm">' + ivHeatmap(matrix, dowDays) + "</div></div></section>";

    html += '<section class="iv-card"><h3>By day of the week</h3><p class="iv-sub">Average crashes per day.</p>' +
      ivBars([1, 2, 3, 4, 5, 6, 0].map(function (d) { return dowRate[d]; }), {
        labels: [1, 2, 3, 4, 5, 6, 0].map(function (d) { return IV_DOW3[d]; }), height: 150, aria: "Average crashes per day by weekday",
        fmt: function (v) { return ivNum(v, 1); },
        tips: [1, 2, 3, 4, 5, 6, 0].map(function (d) {
          return "<b>" + IV_DOW[d] + "</b><br>" + (dowRate[d] == null ? "no data" : ivNum(dowRate[d], 1) + " crashes a day over " + dowDays[d] + " " + IV_DOW[d] + "s");
        })
      }) + "</section>";

    // Monthly rate, oldest → newest, months without enough full days shown as no data.
    const monAgg = new Map();
    cov.keys.forEach(function (k) {
      if (ivKeyDate(k).getTime() + DAY_MS / 2 <= fromMs) return;
      const mk = Math.floor(k / 100);
      let m = monAgg.get(mk);
      if (!m) { m = { days: 0, n: 0 }; monAgg.set(mk, m); }
      if (cov.full.has(k)) { m.days++; m.n += perDay.get(k) || 0; }
    });
    const monKeys = Array.from(monAgg.keys()).sort(function (a, b) { return a - b; });
    const monSeries = [], monLabels = [], monTips = [];
    let prevMk = null;
    monKeys.forEach(function (mk) {
      if (prevMk != null && (Math.floor(mk / 100) * 12 + mk % 100) - (Math.floor(prevMk / 100) * 12 + prevMk % 100) > 1) {
        monSeries.push(null); monLabels.push(""); monTips.push("");
      }
      const m = monAgg.get(mk), name = IV_MON[mk % 100 - 1] + " ’" + String(Math.floor(mk / 100)).slice(2);
      monSeries.push(m.days >= 5 ? m.n / m.days : null);
      monLabels.push(name);
      monTips.push("<b>" + IV_MON[mk % 100 - 1] + " " + Math.floor(mk / 100) + "</b><br>" + (m.days >= 5
        ? ivNum(m.n / m.days, 1) + " crashes a day over " + m.days + " collected days" : "too few collected days"));
      prevMk = mk;
    });
    html += '<section class="iv-card"><h3>Month by month</h3><p class="iv-sub">Average crashes per collected day, so short or interrupted months compare fairly. A dot marks a break in collection.</p>' +
      ivBars(monSeries, { labels: monLabels, tips: monTips, height: 150, labelEvery: monSeries.length > 9 ? 2 : 1,
                          aria: "Average crashes per day for each month", fmt: function (v) { return ivNum(v, 1); } }) + "</section>";

    /* ── calendar ── */
    html += '<section class="iv-card wide"><h3>Every day on record</h3><p class="iv-sub">Crashes per day. Tap a day to open it on the map.</p>' +
            ivCalendar(perDay, cov, fromMs) + "</section>";

    /* ── where ── */
    const locList = Array.from(locs.values()).filter(function (l) { return l.n >= 3; }).sort(function (a, b) { return b.n - a.n; }).slice(0, 12);
    function peakHour(hrs) { let p = 0; for (let h = 1; h < 24; h++) if (hrs[h] > hrs[p]) p = h; return p; }
    if (locList.length) {
      const lmax = locList[0].n;
      html += '<section class="iv-card"><h3>Most crash-prone spots</h3><p class="iv-sub">The exact blocks dispatch is sent to most. Tap one to go there.</p><div class="iv-scroll"><table class="iv-table"><thead><tr>' +
              "<th>Block</th><th>Crashes</th><th>Major / injury</th><th class='opt'>Hit-and-run</th><th class='opt'>Peak hour</th></tr></thead><tbody>" +
        locList.map(function (l, i) {
          const p = ivPlace(l.loc), a = IV.actions.push({ t: "loc", lat: l.lat / l.n, lng: l.lng / l.n, name: p.main }) - 1;
          return '<tr><td><span class="rk">' + (i + 1) + '</span><button type="button" class="iv-link" data-act="' + a + '">' + esc(p.main) +
                 '</button><span class="loc-sub">' + esc(p.sub) + '</span></td><td class="n"><span class="iv-mini"><i style="width:' +
                 Math.round(100 * l.n / lmax) + '%"></i></span>' + l.n + "</td><td>" + ivPct(l.sev, l.n) + "%</td><td class='opt'>" + ivPct(l.hit, l.n) +
                 "%</td><td class='opt'>" + fmtHour(peakHour(l.hrs)) + "</td></tr>";
        }).join("") + "</tbody></table></div></section>";
    }
    const corTop = corList.slice(0, 12);
    if (corTop.length) {
      const cmax = corTop[0].n;
      html += '<section class="iv-card"><h3>Roads with the most crashes</h3><p class="iv-sub">Tap a road to filter the map to it.</p><div class="iv-scroll"><table class="iv-table"><thead><tr>' +
              "<th>Road</th><th>Crashes</th><th>Per week</th><th>Major / injury</th><th class='opt'>Median on feed</th></tr></thead><tbody>" +
        corTop.map(function (c, i) {
          const a = IV.actions.push({ t: "cor", id: c.id }) - 1;
          c.vis.sort(function (x, y) { return x - y; });
          return '<tr><td><span class="rk">' + (i + 1) + '</span><button type="button" class="iv-link" data-act="' + a + '">' + esc(titleCase(c.id)) +
                 '</button></td><td class="n"><span class="iv-mini"><i style="width:' + Math.round(100 * c.n / cmax) + '%"></i></span>' + ivNum(c.n) +
                 "</td><td>" + ivNum(7 * c.n / Math.max(1, fullDays), 1) + "</td><td>" + ivPct(c.sev, c.n) + "%</td><td class='opt'>" +
                 (c.vis.length >= 10 ? ivMins(ivMedian(c.vis)) : "–") + "</td></tr>";
        }).join("") + "</tbody></table></div><p class=\"iv-foot\">A crash is counted on the road dispatch logged it on, not on the cross street named beside it.</p></section>";
    }

    /* ── severity by hour (two small multiples, one measure each) ── */
    function shareBars(num, title, sub, word) {
      const vals = hourAll.map(function (n, h) { return n >= 20 ? 100 * num[h] / n : null; });
      return '<section class="iv-card"><h3>' + title + '</h3><p class="iv-sub">' + sub + "</p>" +
        ivBars(vals, { labels: hourAll.map(function (_, h) { return fmtHour(h); }), labelEvery: 3, height: 150, aria: title,
          fmt: function (v) { return Math.round(v) + "%"; },
          tips: hourAll.map(function (n, h) {
            return "<b>" + ivHourSpan(h) + "</b><br>" + (n >= 20 ? Math.round(100 * num[h] / n) + "% " + word + " (" + num[h] + " of " + n + " crashes)" : "too few crashes to say");
          }) }) + "</section>";
    }
    html += shareBars(hourSev, "How serious, by hour", "Share of crashes logged as major or with injuries.", "major or with injuries");
    html += shareBars(hourHit, "Hit-and-runs, by hour", "Share of crashes where a driver left the scene.", "hit-and-run");

    /* ── time on feed ── */
    const groups = new Map();
    for (const f of prep.feed) {
      if (!inP(f.ms)) continue;
      if (!groups.has(f.g)) groups.set(f.g, []);
      groups.get(f.g).push(f.vis);
    }
    const order = ["Major / injury crash", "Hit-and-run", "Minor crash", "Stalled vehicle", "Road hazard", "Fire call", "Signal problem"];
    const gl = order.filter(function (g) { return groups.has(g) && groups.get(g).length >= 15; }).map(function (g) {
      const v = groups.get(g).sort(function (a, b) { return a - b; });
      return { g: g, n: v.length, med: ivMedian(v), q1: ivQuantile(v, 0.25), q3: ivQuantile(v, 0.75) };
    });
    html += '<section class="iv-card wide"><h3>How long incidents tie things up</h3><p class="iv-sub">Minutes an incident stays listed on the 911 feed before dispatch closes it. Solid bar: the median. Faint band: the middle half of incidents.</p>';
    if (gl.length) {
      const scale = Math.max.apply(null, gl.map(function (g) { return g.q3; })) || 1;
      html += '<div class="iv-hbar">' + gl.map(function (g) {
        const tip = "<b>" + g.g + "</b><br>Median " + ivMins(g.med) + "<br>Middle half: " + ivMins(g.q1) + " – " + ivMins(g.q3) + "<br>" + ivNum(g.n) + " incidents measured";
        return '<span class="l">' + g.g + '</span><span class="t" tabindex="0" data-tip="' + esc(tip) + '"><i class="rng" style="left:' + (100 * g.q1 / scale).toFixed(1) +
               "%;width:" + (100 * (g.q3 - g.q1) / scale).toFixed(1) + '%"></i><i style="width:' + (100 * g.med / scale).toFixed(1) + '%"></i></span><span class="v">' +
               ivMins(g.med) + " <small>" + Math.round(g.q1) + "–" + Math.round(g.q3) + "</small></span>";
      }).join("") + "</div>" +
      '<p class="iv-foot">This is time on the public feed, checked every 5 minutes. It is not response time, and a road can reopen before or after the entry is closed. Only incidents collected since feed tracking was added are measured.</p>';
    } else {
      html += '<p class="iv-empty">Not enough measured incidents in this period yet.</p>';
    }
    html += "</section></div>";
    body.innerHTML = html;
  }

  function ivAct(a) {
    if (!a) return;
    closeInsights();
    pushHashEntry();
    if (a.t === "day") {
      const iso = a.d.getFullYear() + "-" + String(a.d.getMonth() + 1).padStart(2, "0") + "-" + String(a.d.getDate()).padStart(2, "0");
      els.dateFrom.value = iso; els.dateTo.value = iso;
      setRange("all", true);
      toast("Showing " + ivFmtDay(a.d, true));
      scheduleRender(0);
    } else if (a.t === "cor") {
      setRange("all", true);
      state.corridorId = a.id;
      toast("Road: " + titleCase(a.id));
      scheduleRender(0);
    } else if (a.t === "loc") {
      setRange("all", true);
      scheduleRender(0);
      if (map) { try { map.setView([a.lat, a.lng], 17, { animate: false }); } catch (e) {} }
      if (window.innerWidth <= 700) setSheetExpanded(false);
      toast(a.name);
    }
  }

  function openInsights() {
    const view = document.getElementById("insightsView");
    IV.prevFocus = document.activeElement;
    ivRender();
    view.classList.add("open");
    view.setAttribute("aria-hidden", "false");
    view.scrollTop = 0;
    document.getElementById("ivClose").focus();
  }
  function closeInsights() {
    const view = document.getElementById("insightsView");
    if (!view.classList.contains("open")) return;
    view.classList.remove("open");
    view.setAttribute("aria-hidden", "true");
    ivTipHide();
    if (IV.prevFocus && IV.prevFocus.focus) { try { IV.prevFocus.focus(); } catch (e) {} }
  }
  function insightsDataReplaced() {
    _coverage = null;
    IV.prep = null;
    if (document.getElementById("insightsView").classList.contains("open")) ivRender();
  }

  function wireInsights() {
    const view = document.getElementById("insightsView");
    document.getElementById("ivClose").addEventListener("click", closeInsights);
    document.getElementById("ivPeriod").addEventListener("click", function (e) {
      const b = e.target.closest("button[data-p]");
      if (!b) return;
      IV.period = b.getAttribute("data-p");
      this.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      ivRender();
    });
    view.addEventListener("click", function (e) {
      const b = e.target.closest("[data-act]");
      if (b) ivAct(IV.actions[parseInt(b.getAttribute("data-act"), 10)]);
    });
    view.addEventListener("mousemove", function (e) {
      const t = e.target.closest("[data-tip]");
      if (t) ivTipShow(t, e.clientX, e.clientY); else ivTipHide();
    });
    view.addEventListener("mouseleave", ivTipHide);
    view.addEventListener("focusin", function (e) {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (!t) return ivTipHide();
      const r = t.getBoundingClientRect();
      ivTipShow(t, r.left + r.width / 2, r.top);
    });
    view.addEventListener("scroll", ivTipHide, { passive: true });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && view.classList.contains("open")) { e.stopPropagation(); closeInsights(); }
    }, true);
    ["insightsBtn", "insightsCta"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener("click", openInsights);
    });
    const scope = document.getElementById("anScope");
    if (scope) scope.addEventListener("click", function (e) {
      if (e.target.closest("[data-all]")) setRange("all");
    });
  }
"""
