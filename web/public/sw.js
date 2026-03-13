/* RichBot Service Worker — Offline-Fallback */
const CACHE = "richbot-offline-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => {
      return cache.addAll(["/offline.html"]);
    }).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("push", (e) => {
  const d = e.data?.json();
  if (d) {
    self.registration.showNotification(d.title || "RichBot", {
      body: d.body || "",
      icon: "/icon.svg",
      badge: "/icon.svg",
      tag: d.tag || "richbot-alert",
      requireInteraction: d.severity === "critical",
    });
  }
});

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(
    fetch(e.request).catch(() =>
      caches.match("/offline.html")
    )
  );
});
