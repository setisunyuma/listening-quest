/* Listening Quest service worker: app shell only.
   - Never handles /api/*: no API data, cookies or audio pass through here.
   - Page HTML holds no personal data (everything personal is fetched by the page after login), so
     caching it does not mix credentials into a public cache.
   - Unsent answers live in IndexedDB and downloaded audio in a per-user cache; this worker never
     opens or deletes either, so updating it cannot lose them. */
const SHELL = "lq-shell-v1";
const PAGES = ["/", "/login", "/play", "/library", "/radio", "/growth", "/history", "/settings", "/admin", "/check", "/more"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => Promise.allSettled(PAGES.map((p) => c.add(new Request(p, { credentials: "omit" }))))));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("lq-shell-") && k !== SHELL).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});
self.addEventListener("message", (e) => {
  if (e.data === "skipWaiting") self.skipWaiting();
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) caches.open(SHELL).then((c) => c.put(url.pathname, res.clone()));
          return res.clone();
        })
        .catch(() => caches.match(url.pathname).then((hit) => hit || caches.match("/")))
    );
    return;
  }
  if (url.pathname.startsWith("/_next/static/") || /\.(png|svg|webmanifest|ico)$/.test(url.pathname)) {
    e.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) caches.open(SHELL).then((c) => c.put(req, res.clone()));
            return res.clone();
          })
      )
    );
  }
});
