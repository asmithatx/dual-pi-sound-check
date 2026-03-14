/**
 * sw.js — SoundCheck service worker
 * Caches the app shell for instant load on repeat visits.
 * Excludes Socket.IO and API endpoints from the cache.
 */

const CACHE_NAME = 'soundcheck-v1';

const SHELL_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/dashboard.js',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
];

// ── Install: pre-cache shell ───────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: remove old caches ────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: cache-first for shell, network-only for API/Socket.IO ───────────
self.addEventListener('fetch', event => {
  const url = event.request.url;

  // Never cache API calls, Socket.IO, or socket handshakes
  if (url.includes('/api/')    ||
      url.includes('/socket.io') ||
      url.includes('socket.io.min.js')) {
    return;   // fall through to network
  }

  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        // Only cache successful GET responses
        if (!response || response.status !== 200 ||
            event.request.method !== 'GET') {
          return response;
        }
        const toCache = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, toCache));
        return response;
      });
    })
  );
});
