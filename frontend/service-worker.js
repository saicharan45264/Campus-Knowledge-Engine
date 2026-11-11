const CACHE_NAME = 'mach-cache-v7';
const urlsToCache = [
  './index.html',
  './css/main.css',
  './css/variables.css',
  './css/base.css'
];

self.addEventListener('install', event => {
  self.skipWaiting(); // Force the waiting service worker to become the active service worker
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const isAppCode = url.origin === self.location.origin && /\.(?:html|js|css)$/.test(url.pathname);
  if (isAppCode) {
    // Always fetch app code fresh so UI fixes are not hidden by an old browser cache.
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
    return;
  }
  event.respondWith(caches.match(event.request).then(response => response || fetch(event.request)));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.filter(cacheName => cacheName !== CACHE_NAME).map(cacheName => caches.delete(cacheName))
      );
    }).then(() => self.clients.claim()) // Force all clients to use this new service worker immediately
  );
});
