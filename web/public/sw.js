// Throughline service worker: lets the latest edition be read without a connection.
// Hand-written and small on purpose (no build step, no library), so every line can be
// explained. Bump VERSION to drop old caches after a change to this file.
//
// - Pages: network first. A copy of every page that loads is kept; when the network
//   fails, the copy is served, and if there is none, the /offline page.
// - The Today screen sends the list of story pages in the edition ("cache-edition"),
//   and they are fetched in the background, so stories open offline even if the
//   reader never opened them online. Story pages from older editions are dropped.
//   Each stored page is kept together with the scripts, styles and fonts its HTML
//   refers to. The message also lists what the page actually loaded, because on a
//   first visit those files arrived before this worker was in control.
// - Build assets under /_next/static/ (scripts, styles, fonts) have content-hashed
//   names, so they are cached forever, cache first.
// Nothing here stores anything about the reader; it only stores pages.

const VERSION = "v1";
const PAGES = `throughline-pages-${VERSION}`;
const ASSETS = `throughline-assets-${VERSION}`;
const OFFLINE_URL = "/offline";
const MAX_EDITION_PAGES = 20;
const MAX_ASSETS = 60;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(PAGES)
      .then((cache) => storePage(cache, OFFLINE_URL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== PAGES && k !== ASSETS).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

async function cacheAssets(urls) {
  const cache = await caches.open(ASSETS);
  for (const raw of urls.slice(0, MAX_ASSETS)) {
    const url = new URL(raw, self.location.origin);
    if (url.origin !== self.location.origin || !url.pathname.startsWith("/_next/static/")) continue;
    if (await cache.match(url.pathname)) continue;
    try {
      const response = await fetch(url.pathname);
      if (response.ok) await cache.put(url.pathname, response);
    } catch {
      return;
    }
  }
}

// A stored page is only useful offline together with the scripts, styles and fonts
// it loads; without them it shows an error instead of the page. So every page is
// stored with the build assets its HTML refers to.
function assetUrls(html) {
  // Direct references: <script src>, <link href>, url(...) in inline styles.
  const direct = html.match(/\/_next\/static\/[^"'\\\s)<>]+/g) || [];
  // Next's inline page data names lazy chunks without the "/_next/" prefix.
  const lazy = (html.match(/(?<![\w/])static\/chunks\/[^"'\\\s)<>]+\.js/g) || []).map(
    (path) => `/_next/${path}`,
  );
  return [...new Set([...direct, ...lazy])];
}

async function storePage(cache, path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) return;
  const html = await response.clone().text();
  await cache.put(path, response);
  await cacheAssets(assetUrls(html));
}

async function cacheEdition(urls) {
  const wanted = new Set();
  for (const raw of urls.slice(0, MAX_EDITION_PAGES)) {
    const url = new URL(raw, self.location.origin);
    if (url.origin === self.location.origin && url.pathname.startsWith("/story/")) {
      wanted.add(url.pathname);
    }
  }
  const cache = await caches.open(PAGES);
  // Stories that are no longer in the edition would otherwise sit in the cache forever.
  for (const request of await cache.keys()) {
    const path = new URL(request.url).pathname;
    if (path.startsWith("/story/") && !wanted.has(path)) await cache.delete(request);
  }
  // "/" is the edition itself; on a first visit it loaded before this worker existed.
  for (const path of ["/", ...wanted]) {
    try {
      await storePage(cache, path);
    } catch {
      return; // offline or flaky: keep what we have and try again on the next visit
    }
  }
}

self.addEventListener("message", (event) => {
  const data = event.data;
  if (data && data.type === "cache-edition" && Array.isArray(data.urls)) {
    const assets = Array.isArray(data.assets) ? data.assets : [];
    event.waitUntil(cacheAssets(assets).then(() => cacheEdition(data.urls)));
  }
});

async function pageFromNetworkFirst(request) {
  const cache = await caches.open(PAGES);
  const path = new URL(request.url).pathname;
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(path, response.clone());
    return response;
  } catch {
    return (await cache.match(path)) || (await cache.match(OFFLINE_URL)) || Response.error();
  }
}

async function assetFromCacheFirst(request) {
  const cache = await caches.open(ASSETS);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(assetFromCacheFirst(request));
  } else if (request.mode === "navigate") {
    event.respondWith(pageFromNetworkFirst(request));
  }
  // Everything else (including Next's in-app navigation requests) goes to the network.
  // When that fails offline, Next falls back to a full page load, which is served above.
});
