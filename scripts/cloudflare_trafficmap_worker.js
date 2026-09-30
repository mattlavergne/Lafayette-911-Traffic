// Cloudflare Worker: reverse-proxies mattlavergne.com/trafficmap* to the
// project's GitHub Pages site, so the map lives at a subpath of the domain
// instead of needing the whole domain (or a subdomain) to itself.
//
// Setup: see README "Custom domain via Cloudflare" section. In short —
// paste this into a new Worker in the Cloudflare dashboard, then attach a
// Workers Route for mattlavergne.com/trafficmap* to it.
//
// No origin server is needed on the mattlavergne.com side: this Worker
// fetches directly from GitHub Pages and streams the response back.
//
// Optional: commute-route settings API (/trafficmap/api/routes)
// -------------------------------------------------------------
// Lets the map page view and edit your commute routes, and the Pi pick them
// up, with no email round-trip. The Pi never accepts inbound connections:
// the page writes here, the Pi polls here. Enable it by giving the Worker
//   - a KV namespace binding named  ROUTES_KV
//   - a secret named                ROUTES_TOKEN  (a long random passcode;
//                                                 the same value goes in the
//                                                 Pi's LAF911_ROUTE_SYNC_TOKEN
//                                                 and, once, in the page)
// Without both, the API answers 503 and the page falls back to email.
//
//   GET  /trafficmap/api/routes        → the settings document + Pi status
//   PUT  /trafficmap/api/routes        {routes, base_version, by} → saves
//                                      (409 if someone saved in between)
//   POST /trafficmap/api/routes/ack    {version} — the Pi reports it applied
//   POST /trafficmap/api/routes/test   {slot, at} — page asks for a test email
//                                      "as if it were <at>"
//   POST /trafficmap/api/routes/test/result {id, result} — the Pi's answer
//
// Every call needs "Authorization: Bearer <ROUTES_TOKEN>".

const ORIGIN = "https://mattlavergne.github.io/Lafayette-911-Traffic";
const PREFIX = "/trafficmap";
const API = PREFIX + "/api/routes";
const DOC_KEY = "routes_doc_v1";
const STATUS_KEY = "routes_status_v1";
const TEST_KEY = "routes_test_v1";
const ALLOWED_ORIGINS = ["https://mattlavergne.com", "https://mattlavergne.github.io"];
const ROUTE_KEYS = new Set([
  "NAME", "CORRIDORS", "PATH", "RADIUS_M", "DEPART", "DAYS",
  "DEPART_MON", "DEPART_TUE", "DEPART_WED", "DEPART_THU", "DEPART_FRI", "DEPART_SAT", "DEPART_SUN",
]);

function cors(request) {
  const origin = request.headers.get("Origin") || "";
  const h = {
    "Cache-Control": "no-store",
    "Vary": "Origin",
  };
  if (ALLOWED_ORIGINS.includes(origin)) {
    h["Access-Control-Allow-Origin"] = origin;
    h["Access-Control-Allow-Headers"] = "Authorization, Content-Type";
    h["Access-Control-Allow-Methods"] = "GET, PUT, POST, OPTIONS";
    h["Access-Control-Max-Age"] = "600";
  }
  return h;
}

function json(request, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: Object.assign({ "Content-Type": "application/json" }, cors(request)),
  });
}

// Constant-time string comparison (the passcode is the only lock).
function sameSecret(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(String(a || "")), y = enc.encode(String(b || ""));
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0 && y.length >= 16;
}

function validRoutes(routes) {
  if (!routes || typeof routes !== "object" || Array.isArray(routes)) return false;
  const slots = Object.keys(routes);
  if (slots.length > 20) return false;
  for (const slot of slots) {
    if (!/^([1-9]|1[0-9]|20)$/.test(slot)) return false;
    const kv = routes[slot];
    if (!kv || typeof kv !== "object" || Array.isArray(kv)) return false;
    for (const [k, v] of Object.entries(kv)) {
      if (!ROUTE_KEYS.has(k) || typeof v !== "string" || v.length > 60000) return false;
    }
  }
  return true;
}

async function readJson(kv, key, fallback) {
  try {
    const raw = await kv.get(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}

async function handleApi(request, env, url) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(request) });
  if (!env.ROUTES_KV || !env.ROUTES_TOKEN) {
    return json(request, 503, { error: "Route sync is not set up on this Worker (needs ROUTES_KV and ROUTES_TOKEN)." });
  }
  const auth = request.headers.get("Authorization") || "";
  if (!sameSecret(auth.replace(/^Bearer\s+/i, ""), env.ROUTES_TOKEN)) {
    return json(request, 401, { error: "Wrong passcode." });
  }

  const doc = await readJson(env.ROUTES_KV, DOC_KEY, { version: 0, routes: {} });
  const status = await readJson(env.ROUTES_KV, STATUS_KEY, {});
  const test = await readJson(env.ROUTES_KV, TEST_KEY, null);
  const now = new Date().toISOString();

  if (url.pathname === API && request.method === "GET") {
    return json(request, 200, Object.assign({}, doc, { pi: status, test: test }));
  }

  const len = parseInt(request.headers.get("Content-Length") || "0", 10);
  if (len > 262144) return json(request, 413, { error: "Too large." });
  let body;
  try { body = await request.json(); } catch (e) { return json(request, 400, { error: "Bad JSON." }); }

  if (url.pathname === API && request.method === "PUT") {
    if (!validRoutes(body.routes)) return json(request, 400, { error: "Invalid route settings." });
    if (Number(body.base_version) !== Number(doc.version || 0)) {
      return json(request, 409, Object.assign({ error: "Changed elsewhere — reload and try again." }, doc, { pi: status }));
    }
    const next = {
      version: Number(doc.version || 0) + 1,
      updated_at: now,
      updated_by: body.by === "pi" ? "pi" : "web",
      routes: body.routes,
    };
    await env.ROUTES_KV.put(DOC_KEY, JSON.stringify(next));
    return json(request, 200, Object.assign({}, next, { pi: status }));
  }

  if (url.pathname === API + "/ack" && request.method === "POST") {
    const next = {
      applied_version: Number(body.version || 0),
      applied_at: body.applied ? now : (status.applied_at || null),
      seen_at: now,
    };
    await env.ROUTES_KV.put(STATUS_KEY, JSON.stringify(next));
    return json(request, 200, next);
  }

  if (url.pathname === API + "/test" && request.method === "POST") {
    const slot = String(body.slot || "");
    const at = String(body.at || "");
    if (!/^([1-9]|1[0-9]|20)$/.test(slot) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(at)) {
      return json(request, 400, { error: "Pick a route and a date/time." });
    }
    const next = { id: crypto.randomUUID(), slot: slot, at: at, requested_at: now, result: null };
    await env.ROUTES_KV.put(TEST_KEY, JSON.stringify(next));
    return json(request, 200, next);
  }

  if (url.pathname === API + "/test/result" && request.method === "POST") {
    if (!test || body.id !== test.id) return json(request, 409, { error: "No such test." });
    const result = body.result && typeof body.result === "object" ? body.result : {};
    const clean = {};
    for (const [k, v] of Object.entries(result).slice(0, 20)) {
      if (["string", "number", "boolean"].includes(typeof v)) clean[k] = typeof v === "string" ? v.slice(0, 500) : v;
    }
    const next = Object.assign({}, test, { result: clean, done_at: now });
    await env.ROUTES_KV.put(TEST_KEY, JSON.stringify(next));
    return json(request, 200, next);
  }

  return json(request, 404, { error: "Not found." });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === API || url.pathname.startsWith(API + "/")) {
      return handleApi(request, env, url);
    }

    // /trafficmap -> /trafficmap/ so the page's relative asset URLs
    // (traffic_data.js, traffic_meta.json) resolve under the subpath.
    if (url.pathname === PREFIX) {
      return Response.redirect(url.origin + PREFIX + "/" + url.search, 301);
    }

    if (!url.pathname.startsWith(PREFIX + "/")) {
      return fetch(request);
    }

    const originUrl = ORIGIN + url.pathname.slice(PREFIX.length) + url.search;
    const originResponse = await fetch(originUrl, {
      cf: { cacheTtl: 300, cacheEverything: true },
    });

    return new Response(originResponse.body, {
      status: originResponse.status,
      headers: originResponse.headers,
    });
  },
};
