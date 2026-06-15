// Job Hunter service worker — scope is /app/ (served at /app/sw.js).
// It only ever sees /app/* requests; /api/* is out of scope and always hits
// the network live. Bump VERSION on each deploy to invalidate the old cache.
const VERSION = "jh-v2";
const CACHE = `jobhunter-${VERSION}`;
const SHELL = [
  "/app/",
  "/app/assets/index.js",
  "/app/assets/index.css",
  "/app/manifest.webmanifest",
  "/app/icon-192.png",
  "/app/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith("/app")) return; // belt-and-suspenders; scope already limits us

  // Page navigations: try network, fall back to cached shell when offline.
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/app/")));
    return;
  }
  // In-scope assets: network-first (stays fresh after deploys), cache fallback offline.
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req))
  );
});
