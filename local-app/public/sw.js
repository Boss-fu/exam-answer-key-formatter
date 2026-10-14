// 校正室 App 的離線殼：一律「先抓網路最新版」，斷線才用快取（避免手機永遠卡在舊版）。
const CACHE = "label-shell-v2";
const SHELL = ["/label.html","/manifest.webmanifest","/logo.png","/apple-touch-icon.png","/api-client.js","/server-config.js","/patch-notes.js","/dehand-web.js"];
self.addEventListener("install", e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request, { cache: "no-store" }).then(r => {
    if (r.ok && url.origin === location.origin) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request)));
});
