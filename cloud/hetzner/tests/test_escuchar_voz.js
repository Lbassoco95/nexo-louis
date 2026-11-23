// Prueba el detector de tono y el agrupador de voces del modo «Escuchar y subtitular» con señales sintéticas (no es una persona real).
const fs = require('fs'), path = require('path'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '..', 'services', 'app', 'index.html'), 'utf8');
const src = html.split('/*ES-VOZ-BEGIN*/')[1].split('/*ES-VOZ-END*/')[0];
const { esF0, esVozDe, esTono, esMed, laAgc } = new Function(src + '; return {esF0, esVozDe, esTono, esMed, laAgc};')();
const SR = 24000;
function voz(f0, n = 1024, amp = 0.2, fase = 0, brillo = 1) {      // vocal sintética: fundamental + armónicos que decaen
  const x = new Float32Array(n);
  for (let i = 0; i < n; i++) { let v = 0; for (let h = 1; h <= 6; h++) v += Math.sin(2 * Math.PI * f0 * h * (i + fase) / SR) / Math.pow(h, brillo); x[i] = amp * v / 2; }
  return x;
}
const aInt = f => Int16Array.from(f, v => Math.round(v * 32767));
// 1) tono correcto (±3 %) en voz grave, media y aguda, con Float32 e Int16
for (const f0 of [95, 120, 165, 210, 260, 330]) {
  const r = esF0(voz(f0), SR), r2 = esF0(aInt(voz(f0)), SR);
  assert.ok(Math.abs(r.f0 - f0) / f0 < 0.03, `Float32 ${f0} → ${r.f0}`);
  assert.ok(Math.abs(r2.f0 - f0) / f0 < 0.03, `Int16 ${f0} → ${r2.f0}`);
}
// 2) silencio y ruido no cuentan como voz
assert.equal(esF0(new Float32Array(1024), SR).f0, 0);
assert.equal(esF0(voz(150, 1024, 0.002), SR).f0, 0);                         // muy bajito
const ruido = Float32Array.from({ length: 1024 }, () => (Math.random() * 2 - 1) * 0.3);
assert.equal(esF0(ruido, SR).f0, 0);
// 3) etiquetas de tono
assert.deepEqual([esTono(110), esTono(180), esTono(250), esTono(0)], ['grave', 'media', 'aguda', '']);
// 4) agrupa en voces: grave, grave, aguda, aguda, grave, otra media… (con variación natural ±8 %)
const spk = [], jit = f => f * (1 + (Math.random() - 0.5) * 0.16);
const seq = [110, 110, 230, 230, 110, 230, 110].map(f => esVozDe(spk, jit(f), 0.5, 6));
assert.deepEqual(seq, [0, 0, 1, 1, 0, 1, 0]);
assert.equal(spk.length, 2);
assert.equal(esVozDe(spk, 0, 0.5, 6), -1);                                   // sin tono no se asigna nadie
// 5) nunca más de 6 voces
const s2 = []; [90, 120, 160, 200, 260, 340, 420, 80].forEach(f => esVozDe(s2, f, 0.5, 6)); assert.ok(s2.length <= 6);
// 6) mediana
assert.equal(esMed([5, 1, 9]), 5); assert.equal(esMed([]), 0);
// 7) ganancia automática: una voz lejana (RMS≈0.01) se sube, ya se identifica y no recorta; el ruido de fondo no se amplifica ni cuenta como voz
{
  const st = { g: 1, floor: 0.002 }, bloques = [];
  for (let i = 0; i < 40; i++) bloques.push(laAgc(Float32Array.from({ length: 2048 }, () => (Math.random() * 2 - 1) * 0.0015), st));   // cuarto en silencio
  assert.ok(bloques.every(b => !b.speech) && st.g === 1, 'el ruido no debe contar como voz ni subir la ganancia');
  let ult;
  for (let i = 0; i < 60; i++) ult = laAgc(voz(150, 2048, 0.025, i * 2048), st);                                                       // voz baja (amp 0.025)
  assert.ok(ult.speech && st.g > 4 && st.g <= 12, 'sube la voz baja: g=' + st.g);
  assert.ok(ult.rms > 0.05 && ult.rms < 0.2, 'rms objetivo: ' + ult.rms);
  assert.ok(esF0(ult.out, SR).f0 > 0, 'la voz subida ya se identifica');
  assert.equal(esF0(voz(150, 2048, 0.025), SR).f0, 0, '(sin ganancia esa misma voz no se identificaba)');
  const fuerte = laAgc(voz(150, 2048, 0.9, 0), st);                                                                                      // de repente alguien grita
  assert.ok(Math.max(...fuerte.out.map(Math.abs)) <= 0.96, 'no recorta');
}
console.log('voz: ok (tono, silencio/ruido, agrupación en voces)');
