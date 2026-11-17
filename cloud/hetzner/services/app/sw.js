// Service worker: solo cachea el cascarón de la app (nunca datos). Los datos siempre van a la red.
const V = 'louis-app-v6';
const SHELL = ['/app/', '/app/manifest.webmanifest', '/app/icon.svg'];
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

// ---- Push: el servidor manda {title, body, tab, tag}; el payload viaja cifrado de extremo a extremo.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = { title: '{{AGENT_NAME}}', body: e.data ? e.data.text() : '' }; }
  e.waitUntil((async () => {
    // contador en el ícono de la app (iOS 16.4+ instalada / Android)
    try { if (d.badge && self.navigator && self.navigator.setAppBadge) await self.navigator.setAppBadge(d.badge); } catch (_) {}
    // si la app está abierta, que refresque la campana y «Hoy» sin esperar
    const cs = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    cs.forEach(c => c.postMessage({ type: 'push' }));
    await self.registration.showNotification(d.title || '{{AGENT_NAME}}', {
      body: d.body || '', tag: d.tag || 'louis', renotify: true,
      icon: '/app/icon.svg', badge: '/app/icon.svg', data: { tab: d.tab || 'chat', nid: d.nid || '' }
    });
  })());
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const tab = (e.notification.data && e.notification.data.tab) || 'chat';
  const nid = (e.notification.data && e.notification.data.nid) || '';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    for (const c of cs) { if ('focus' in c) { c.postMessage({ type: 'tab', tab, nid }); return c.focus(); } }
    return self.clients.openWindow('/app/?tab=' + tab + (nid ? '&nid=' + encodeURIComponent(nid) : ''));
  }));
});
