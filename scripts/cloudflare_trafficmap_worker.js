// Cloudflare Worker for mattlavergne.com.
//
// - /trafficmap* reverse-proxies to the project's GitHub Pages site, so the
//   map lives at a subpath of the domain instead of needing the whole
//   domain (or a subdomain) to itself.
// - Everything else gets a plain "coming soon" placeholder instead of
//   falling through to GitHub's origin (which would otherwise serve
//   GitHub's own 404 page, since no repo has this domain configured as a
//   custom domain).
//
// Setup: see README "Custom domain via Cloudflare" section. In short —
// paste this into a new Worker in the Cloudflare dashboard, then attach a
// Workers Route for mattlavergne.com/* to it (the whole domain, not just
// /trafficmap*, so this Worker also owns the placeholder fallback).
//
// No origin server is needed on the mattlavergne.com side: this Worker
// fetches directly from GitHub Pages for /trafficmap and builds every
// other response itself.

const ORIGIN = "https://mattlavergne.github.io/Lafayette-911-Traffic";
const PREFIX = "/trafficmap";

const COMING_SOON_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mattlavergne.com</title>
<style>
  html, body { height: 100%; margin: 0; }
  body {
    display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0b0d12; color: #e8eaed; text-align: center;
  }
  main { padding: 2rem; }
  h1 { font-size: 1.5rem; font-weight: 600; margin: 0 0 0.5rem; }
  p { color: #9aa0a6; margin: 0; }
  a { color: #8ab4f8; text-decoration: none; }
</style>
</head>
<body>
<main>
  <h1>Coming soon</h1>
  <p>In the meantime, check out the <a href="/trafficmap/">live traffic map</a>.</p>
</main>
</body>
</html>`;

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // /trafficmap -> /trafficmap/ so the page's relative asset URLs
    // (traffic_data.js, traffic_meta.json) resolve under the subpath.
    if (url.pathname === PREFIX) {
      return Response.redirect(url.origin + PREFIX + "/" + url.search, 301);
    }

    if (!url.pathname.startsWith(PREFIX + "/")) {
      return new Response(COMING_SOON_HTML, {
        status: 200,
        headers: { "content-type": "text/html; charset=utf-8" },
      });
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
