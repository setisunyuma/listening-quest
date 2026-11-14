/* Listening Quest service worker: app shell only.
   - Never handles /api/*: no API data, cookies or audio pass through here.
   - Page HTML holds no personal data (everything personal is fetched by the page after login), so
     caching it does not mix credentials into a public cache.
   - Unsent answers live in IndexedDB and downloaded audio in a per-user cache; this worker never
     opens or deletes either, so updating it cannot lose them. */
const SHELL = "lq-shell-v2";
const PAGES = ["/", "/login", "/play", "/library", "/radio", "/growth", "/history", "/settings", "/admin", "/check", "/more", "/missed", "/task"];

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

// Notifications (Web Push). The message is encrypted end to end; it carries only a title, a line of text and
// the page to open - never answers or personal data.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Listening Quest", { body: d.body || "", icon: "/icon-192.png", badge: "/icon-192.png", data: { url: d.url || "/" } }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) { if ("focus" in c) { c.navigate(target); return c.focus(); } }
      return self.clients.openWindow(target);
    })
  );
});
