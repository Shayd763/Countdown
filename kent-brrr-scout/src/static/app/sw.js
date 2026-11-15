/* Gatehouse service worker: offline app shell, cached data, push notifications. */
const VERSION = "brrr-v7";
const SHELL = ["/app/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/badge-96.png"];
const CACHED_API = [/^\/api\/digest$/, /^\/api\/deals(\?.*)?$/, /^\/api\/deals\/[^/]+$/, /^\/api\/auth\/me$/,
                    /^\/api\/meta$/, /^\/api\/summary$/];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok) {
      const copy = res.clone();
      const headers = new Headers(copy.headers);
      headers.set("X-Cached-At", new Date().toISOString());
      cache.put(req, new Response(await copy.blob(), { status: copy.status, headers }));
    }
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) {
      const headers = new Headers(hit.headers);
      headers.set("X-From-Cache", "1");
      return new Response(await hit.blob(), { status: hit.status, headers });
    }
    if (req.mode === "navigate") return (await cache.match("/app/")) || Response.error();
    return new Response(JSON.stringify({ detail: "offline" }), { status: 503, headers: { "Content-Type": "application/json", "X-Offline": "1" } });
  }
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || e.request.method !== "GET") return;
  const path = url.pathname + url.search;
  if (CACHED_API.some((re) => re.test(path))) return e.respondWith(networkFirst(e.request));
  if (url.pathname.startsWith("/api/")) return;  // live-only endpoints
  if (url.pathname === "/app/" || SHELL.includes(url.pathname) || url.pathname.startsWith("/icons/")) {
    return e.respondWith(networkFirst(e.request));
  }
});

self.addEventListener("push", (e) => {
  let msg = {};
  try { msg = e.data ? e.data.json() : {}; } catch { msg = { title: "Gatehouse", body: e.data && e.data.text() }; }
  const title = msg.title || "Gatehouse";
  const work = [self.registration.showNotification(title, {
    body: msg.body || "", tag: msg.tag || "brrr", renotify: true,
    icon: "/icons/icon-192.png", badge: "/icons/badge-96.png",
    data: { url: msg.url || "/app/#/today" },
  })];
  // Keep the digest summary so the app can show it even if the laptop goes to sleep afterwards,
  // and try to refresh the full digest while the laptop is (just) awake after its run.
  work.push(caches.open(VERSION).then(async (c) => {
    if (msg.digest) await c.put("/api/push-digest", new Response(JSON.stringify({ ...msg.digest, received_at: new Date().toISOString() }), { headers: { "Content-Type": "application/json" } }));
    try { await networkFirst(new Request("/api/digest", { credentials: "same-origin" })); } catch {}
  }));
  e.waitUntil(Promise.all(work));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/app/#/today";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if (c.url.includes("/app/")) { c.navigate(target); return c.focus(); } }
    return self.clients.openWindow(target);
  }));
});
