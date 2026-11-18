// Prueba el script del widget con un simulador mínimo de la API de Scriptable (no es un iPhone real).
const fs = require('fs'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'services', 'app', 'widget-scriptable.js'), 'utf8')
  .replace(/\{\{BASE_URL\}\}/g, 'https://donna.kawiil.mx').replace(/\{\{AGENT_NAME\}\}/g, 'Donna');

async function correr({ keychain = {}, fetchImpl, runsInWidget = true, family = 'medium', cache = null }) {
  const out = { lines: [], url: null, setWidget: false, keychain: { ...keychain }, presented: false };
  const files = cache ? { '/doc/donna-widget-cache.json': cache } : {};
  const env = {
    Keychain: { contains: k => k in out.keychain, get: k => out.keychain[k], set: (k, v) => { out.keychain[k] = v; }, remove: k => { delete out.keychain[k]; } },
    FileManager: { local: () => ({ documentsDirectory: () => '/doc', joinPath: (a, b) => a + '/' + b,
      fileExists: p => p in files, readString: p => files[p], writeString: (p, s) => { files[p] = s; } }) },
    Color: class { constructor(h) { this.h = h; } },
    Font: { boldSystemFont: s => ({ b: s }), systemFont: s => ({ s }) },
    config: { runsInWidget, widgetFamily: family },
    Alert: class { addSecureTextField() {} addAction() {} addCancelAction() {} async present() { return -1; } },
    Request: class { constructor(u) { this.u = u; this.response = {}; } async loadJSON() { const r = await fetchImpl(this.u, this.headers); this.response = { statusCode: r.status }; return r.json; } },
    ListWidget: class { constructor() { out.w = this; }
      addText(t) { const o = { t }; out.lines.push(o); return o; } addSpacer() {} set url(v) { out.url = v; } get url() { return out.url; }
      addStack() { return { addText: t => ({ t }), set url(v) { out.hablarUrl = v; } }; } },
    Script: { setWidget: () => { out.setWidget = true; }, complete: () => {} },
  };
  const AsyncFn = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFn(...Object.keys(env), src)(...Object.values(env));
  out.text = out.lines.map(l => l.t);
  return out;
}
const HOY = { generado: '16 Nov 23:50', abiertos: 105, cerrados: 3, confirmaciones: 2,
  urgentes: [{ texto: 'HOY 17-nov · Enviar carta a Vizum  · [auto 2026-11-17]' }, { texto: 'Llamar a Fernando 16:30' }],
  vencimientos: [{ texto: 'Informe del Calpulli  · [visto 2026-10-15]' }, { texto: 'a'.repeat(120) }, { texto: 'cinco' }, { texto: 'seis' }] };

(async () => {
  // 1) camino feliz, widget mediano
  let r = await correr({ keychain: { donna_token: 'tok' }, fetchImpl: async (u, h) => { assert.equal(u, 'https://donna.kawiil.mx/v1/app/hoy'); assert.equal(h.Authorization, 'Bearer tok'); return { status: 200, json: HOY }; } });
  assert.ok(r.setWidget); assert.equal(r.url, 'https://donna.kawiil.mx/app/?tab=hoy');
  assert.equal(r.text[0], 'Donna · Pendientes');
  assert.equal(r.hablarUrl, 'https://donna.kawiil.mx/app/?hablar=1');          // el widget trae un toque directo a «hablar con Donna»
  assert.match(r.text[1], /🔴 2 hoy · 105 abiertos · ⚠️ 2 por confirmar/);
  assert.equal(r.text[2], '• HOY 17-nov · Enviar carta a Vizum');              // quita la etiqueta [auto …]
  assert.equal(r.text.filter(t => /^[•◦] /.test(t)).length, 5);                // máx 5 en mediano
  assert.ok(!r.text.some(t => t.length > 75));                                  // recorta lo largo
  // 2) pequeño muestra 3; grande 10
  assert.equal((await correr({ keychain: { donna_token: 'tok' }, family: 'small', fetchImpl: async () => ({ status: 200, json: HOY }) })).text.filter(t => /^[•◦] /.test(t)).length, 3);
  // 3) sin token dentro del widget → instrucción, sin llamar a la red
  r = await correr({ fetchImpl: async () => { throw new Error('no debe llamar'); } });
  assert.match(r.text.join(' '), /Abre este script en Scriptable/);
  // 4) token inválido (401) → se borra del llavero y avisa
  r = await correr({ keychain: { donna_token: 'malo' }, fetchImpl: async () => ({ status: 401, json: {} }) });
  assert.match(r.text.join(' '), /Token inválido/); assert.ok(!('donna_token' in r.keychain));
  // 5) sin conexión con datos guardados → usa el caché y lo dice
  r = await correr({ keychain: { donna_token: 'tok' }, cache: JSON.stringify(HOY), fetchImpl: async () => { throw new Error('offline'); } });
  assert.match(r.text.join(' '), /sin conexión · actualizado 16 Nov 23:50/); assert.match(r.text[2], /Vizum/);
  // 6) sin conexión y sin caché
  r = await correr({ keychain: { donna_token: 'tok' }, fetchImpl: async () => { throw new Error('offline'); } });
  assert.match(r.text.join(' '), /Sin conexión y sin datos guardados/);
  // 7) nada urgente
  r = await correr({ keychain: { donna_token: 'tok' }, fetchImpl: async () => ({ status: 200, json: { abiertos: 0, urgentes: [], vencimientos: [] } }) });
  assert.match(r.text.join(' '), /Nada urgente/);
  console.log('widget: 7 casos ok');
})().catch(e => { console.error('FALLÓ:', e.message); process.exit(1); });
