// Job Hunter service worker — scope is /app/ (served at /app/sw.js).
// It only ever sees /app/* requests; /api/* is out of scope and always hits
// the network live. Bump VERSION on each deploy to invalidate the old cache.
const VERSION = "jh-v4";
const CACHE = `jobhunter-${VERSION}`;
const SHELL = [
  "/app/",
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

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) { data = {}; }
  const title = data.title || "Job Hunter";
  const body = data.body || "You have a new update.";
  const url = data.url || "/app";
  e.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/app/icon-192.png",
      badge: "/app/icon-192.png",
      data: { url },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/app";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cls) => {
      for (const c of cls) { if (c.url.includes("/app") && "focus" in c) return c.focus(); }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
