const CACHE_NAME = 'mach-cache-v2';
const urlsToCache = [
  './index.html',
  './src/css/main.css',
  './src/css/variables.css',
  './src/css/base.css'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
});

self.addEventListener('fetch', event => {
  event.respondWith(caches.match(event.request).then(response => response || fetch(event.request)));
});
