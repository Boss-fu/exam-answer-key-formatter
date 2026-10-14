const SHELL=["/label.html","/manifest.webmanifest","/logo.png","/apple-touch-icon.png","/api-client.js","/server-config.js","/patch-notes.js","/dehand-web.js"];
self.addEventListener("install",e=>e.waitUntil(caches.open("label-shell-v1").then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("fetch",e=>{if(new URL(e.request.url).pathname.startsWith("/api/"))return;e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));});
