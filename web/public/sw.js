// Throughline service worker: lets the latest edition be read without a connection.
// Hand-written and small on purpose (no build step, no library), so every line can be
// explained. Bump VERSION to drop old caches after a change to this file.
//
// - Pages: network first. A copy of every page that loads is kept. The copy is served
//   when the network fails, answers with a server error, or takes longer than
//   NETWORK_PATIENCE_MS; if there is no copy, the /offline page is.
// - The Today screen sends the edition's id and its story pages ("cache-edition").
//   The first time an edition is seen they are fetched in the background, so stories
//   open offline even if the reader never opened them online. Story pages from older
//   editions are dropped, and so are build files no stored page uses any more.
// - Each stored page is kept together with the scripts, styles and fonts its HTML
//   refers to. The message also lists what the page actually loaded, because on a
//   first visit those files arrived before this worker was in control.
// - Build assets under /_next/static/ have content-hashed names, so they are served
//   cache first.
// - An error page is never stored: pages mark themselves with data-edition-state.
// Nothing here stores anything about the reader; it only stores public pages. When
// signed-in pages arrive (M6), they must be excluded from the page cache.

const VERSION = "v2";
const PAGES = `throughline-pages-${VERSION}`;
const ASSETS = `throughline-assets-${VERSION}`;
const OFFLINE_URL = "/offline";
const EDITION_MARK = "/__edition-id"; // a tiny cache entry remembering which edition is stored
const ERROR_MARK = 'data-edition-state="error"';
const MAX_EDITION_PAGES = 20;
const MAX_ASSETS = 80;
const NETWORK_PATIENCE_MS = 4000;

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

// A stored page is only useful offline together with the scripts, styles and fonts
// it loads; without them it shows an error instead of the page.
function assetUrls(html) {
  // Direct references: <script src>, <link href>, url(...) in inline styles.
  const direct = html.match(/\/_next\/static\/[^"'\\\s)<>]+/g) || [];
  // Next's inline page data names lazy chunks without the "/_next/" prefix.
  const lazy = (html.match(/(?<![\w/])static\/chunks\/[^"'\\\s)<>]+\.js/g) || []).map(
    (path) => `/_next/${path}`,
  );
  return [...new Set([...direct, ...lazy])];
}

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

// Returns true when the page was stored. Error pages and failed requests are not.
async function storePage(cache, path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) return false;
  const html = await response.clone().text();
  if (html.includes(ERROR_MARK)) return false;
  await cache.put(path, response);
  await cacheAssets(assetUrls(html));
  return true;
}

// Build files from earlier deploys would otherwise pile up for ever.
async function dropUnusedAssets() {
  const pages = await caches.open(PAGES);
  const used = new Set();
  for (const request of await pages.keys()) {
    const response = await pages.match(request);
    if (response) for (const url of assetUrls(await response.text())) used.add(url);
  }
  if (used.size === 0) return; // nothing to compare against: keep what we have
  const assets = await caches.open(ASSETS);
  for (const request of await assets.keys()) {
    if (!used.has(new URL(request.url).pathname)) await assets.delete(request);
  }
}

async function cacheEdition(editionId, urls) {
  const cache = await caches.open(PAGES);
  const mark = await cache.match(EDITION_MARK);
  if (mark && (await mark.text()) === String(editionId)) return; // already stored: do nothing

  const wanted = new Set();
  for (const raw of urls.slice(0, MAX_EDITION_PAGES)) {
    const url = new URL(raw, self.location.origin);
    if (url.origin === self.location.origin && url.pathname.startsWith("/story/")) {
      wanted.add(url.pathname);
    }
  }
  // Stories that are no longer in the edition would otherwise sit in the cache forever.
  for (const request of await cache.keys()) {
    const path = new URL(request.url).pathname;
    if (path.startsWith("/story/") && !wanted.has(path)) await cache.delete(request);
  }
  // "/" is the edition itself; on a first visit it loaded before this worker existed.
  let complete = true;
  for (const path of ["/", ...wanted]) {
    try {
      complete = (await storePage(cache, path)) && complete;
    } catch {
      return; // offline or flaky: keep what we have and try again on the next visit
    }
  }
  // Only remember the edition as stored when every page made it.
  if (complete) await cache.put(EDITION_MARK, new Response(String(editionId)));
  await dropUnusedAssets();
}

self.addEventListener("message", (event) => {
  const data = event.data;
  if (data && data.type === "cache-edition" && Array.isArray(data.urls)) {
    const assets = Array.isArray(data.assets) ? data.assets : [];
    event.waitUntil(cacheAssets(assets).then(() => cacheEdition(data.editionId, data.urls)));
  }
});

function timeout(ms) {
  return new Promise((resolve) => setTimeout(() => resolve(null), ms));
}

async function pageFromNetworkFirst(event) {
  const request = event.request;
  const path = new URL(request.url).pathname;
  const cache = await caches.open(PAGES);
  const stored = await cache.match(path);

  // Keep a good answer for next time without making the reader wait for the write.
  const fromNetwork = fetch(request).then(async (response) => {
    if (!response.ok) return response;
    const copy = response.clone();
    event.waitUntil(
      copy.text().then((html) =>
        html.includes(ERROR_MARK)
          ? undefined
          : cache.put(path, new Response(html, { status: 200, headers: copy.headers })),
      ),
    );
    return response;
  });

  try {
    // With a stored copy, do not let a weak signal keep the reader on a blank tab.
    const response = stored
      ? await Promise.race([fromNetwork, timeout(NETWORK_PATIENCE_MS)])
      : await fromNetwork;
    if (response && response.status < 500) return response;
    if (stored) return stored;
    if (response) return response;
  } catch {
    if (stored) return stored;
  }
  fromNetwork.catch(() => {}); // the race may have left it pending; never an unhandled rejection
  return stored || (await cache.match(OFFLINE_URL)) || Response.error();
}

async function assetFromCacheFirst(event) {
  const cache = await caches.open(ASSETS);
  const hit = await cache.match(event.request);
  if (hit) return hit;
  const response = await fetch(event.request);
  if (response.ok) event.waitUntil(cache.put(event.request, response.clone()));
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(assetFromCacheFirst(event));
  } else if (request.mode === "navigate") {
    event.respondWith(pageFromNetworkFirst(event));
  }
  // Everything else (including Next's in-app navigation requests) goes to the network.
  // When that fails offline, Next falls back to a full page load, which is served above.
});
