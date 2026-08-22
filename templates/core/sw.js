{% load static %}
// spotDL Web UI service worker (CLAUDE.md #16).
//
// Scope is deliberately narrow: it caches the static app shell (CSS/JS/
// icons) and one offline fallback page for navigation requests. It never
// caches API responses, HTML pages with user/session data, or anything
// under /accounts/, /admin/, /batches/, /share/, /library/ — caching those
// would risk serving stale or cross-session private data from the cache
// (CLAUDE.md #16: "Do not cache authenticated/private data").

const CACHE_VERSION = "v1";
const CACHE_NAME = "spotdl-ui-shell-" + CACHE_VERSION;

const OFFLINE_URL = "{% url 'core:offline' %}";

const PRECACHE_URLS = [
  OFFLINE_URL,
  "{% static 'vendor/bootstrap/bootstrap.min.css' %}",
  "{% static 'vendor/bootstrap/bootstrap.bundle.min.js' %}",
  "{% static 'vendor/bootstrap-icons/bootstrap-icons.min.css' %}",
  "{% static 'vendor/htmx/htmx.min.js' %}",
  "{% static 'css/theme.css' %}",
  "{% static 'img/icon-192.png' %}",
  "{% static 'img/icon-512.png' %}",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return; // never intercept cross-origin or non-GET requests
  }

  // Page navigations: always try the network first (this app is not meant
  // to work offline — downloads need a live server), fall back to the
  // cached offline shell only when the network is unreachable.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  // Static assets only: cache-first, populate the cache on first fetch.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        });
      })
    );
  }
  // Everything else (API/dynamic endpoints) passes straight through.
});
