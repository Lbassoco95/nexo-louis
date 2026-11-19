// La app instalada (PWA) abre sin conexión con el cascarón en caché y se actualiza sola al subir de versión (no hay que borrar nada).
// Uso:  NODE_PATH=$(npm root -g) node tests/test_pwa_offline.js http://127.0.0.1:PUERTO
const { chromium } = require('playwright'); const assert = require('assert');
const base = process.argv[2] || 'http://127.0.0.1:18773';
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 390, height: 844 } }); const pg = await ctx.newPage();
  await pg.goto(base + '/app/'); await pg.evaluate(() => localStorage.setItem('token', JSON.stringify('master-secret-123'))); await pg.reload();
  await pg.evaluate(() => navigator.serviceWorker.ready); await pg.waitForTimeout(1200);
  const sw = await pg.evaluate(async () => { const k = await caches.keys(); return { keys: k, shell: (await (await caches.open(k[0])).keys()).map(r => new URL(r.url).pathname) }; });
  assert.ok(sw.keys.some(k => /^louis-app-v\d+$/.test(k)), 'hay caché versionada ' + sw.keys);
  assert.ok(sw.shell.includes('/app/'), 'el cascarón está en caché ' + sw.shell);
  await ctx.setOffline(true);
  await pg.reload({ waitUntil: 'domcontentloaded' });
  await pg.waitForSelector('#app', { timeout: 8000 });
  assert.ok(await pg.isVisible('nav'), 'la app abre sin conexión');
  await ctx.setOffline(false);
  console.log('pwa: ok (caché', sw.keys[0] + ', abre sin conexión)');
  await b.close();
})().catch(e => { console.error('FALLÓ:', e.message); process.exit(1); });
