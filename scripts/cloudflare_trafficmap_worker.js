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

const ORIGIN = "https://mattlavergne.github.io/Lafayette-911-Traffic";
const PREFIX = "/trafficmap";

export default {
  async fetch(request) {
    const url = new URL(request.url);

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
