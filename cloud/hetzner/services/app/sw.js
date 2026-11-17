// Service worker: solo cachea el cascarón de la app (nunca datos). Los datos siempre van a la red.
const V = 'louis-app-v1';
const SHELL = ['/app', '/app/manifest.webmanifest', '/app/icon.svg'];
self.addEventListener('install', e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || !SHELL.includes(u.pathname)) return;
  e.respondWith(fetch(e.request).then(r => { caches.open(V).then(c => c.put(e.request, r.clone())); return r; })
    .catch(() => caches.match(e.request)));
});
