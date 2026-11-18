// {{AGENT_NAME}} · widget de pendientes para iPhone — se usa con la app gratuita "Scriptable".
// Muestra lo urgente y lo que tiene fecha, y cuántas confirmaciones esperan. Al tocarlo abre la app.
// Este script NO contiene tu token: la primera vez que lo ejecutas dentro de Scriptable te lo pide
// y lo guarda en el llavero (Keychain) de tu iPhone.
const BASE = "{{BASE_URL}}";
const KEY = "donna_token";
const fm = FileManager.local();
const CACHE = fm.joinPath(fm.documentsDirectory(), "donna-widget-cache.json");

async function getToken() {
  if (Keychain.contains(KEY)) return Keychain.get(KEY);
  if (config.runsInWidget) return null;
  const a = new Alert();
  a.title = "Token de {{AGENT_NAME}}";
  a.message = "Pégalo una sola vez; se guarda en el llavero de tu iPhone.";
  a.addSecureTextField("Token");
  a.addAction("Guardar");
  a.addCancelAction("Cancelar");
  if ((await a.present()) === -1) return null;
  const t = (a.textFieldValue(0) || "").trim();
  if (t) Keychain.set(KEY, t);
  return t || null;
}

function limpiar(t) {
  return String(t || "")
    .replace(/\s*·?\s*\[(?:auto|visto|capturado)[^\]]*\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function formar(d, max) {
  const urg = (d.urgentes || []).map((x) => ({ t: limpiar(x.texto), hot: true }));
  const otros = (d.vencimientos || []).map((x) => ({ t: limpiar(x.texto), hot: false }));
  return {
    items: urg.concat(otros).slice(0, max),
    abiertos: d.abiertos || 0,
    hoy: (d.urgentes || []).length,
    conf: d.confirmaciones || 0,
    generado: d.generado || "",
  };
}

async function cargar(token) {
  const r = new Request(BASE + "/v1/app/hoy");
  r.headers = { Authorization: "Bearer " + token };
  r.timeoutInterval = 15;
  const j = await r.loadJSON();
  if (r.response && r.response.statusCode === 401) {
    Keychain.remove(KEY);
    throw new Error("token");
  }
  fm.writeString(CACHE, JSON.stringify(j));
  return j;
}

async function construir() {
  const fam = config.widgetFamily || "medium";
  const max = fam === "small" ? 3 : fam === "large" ? 10 : 5;
  const w = new ListWidget();
  w.backgroundColor = new Color("#0b1220");
  w.url = BASE + "/app/?tab=hoy";
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);

  const linea = (txt, size, color, bold, lines) => {
    const t = w.addText(txt);
    t.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
    t.textColor = new Color(color);
    if (lines) t.lineLimit = lines;
    return t;
  };

  const token = await getToken();
  if (!token) {
    linea("{{AGENT_NAME}}", 14, "#e8edf7", true);
    linea("Abre este script en Scriptable para guardar tu token.", 12, "#fbbf24", false, 4);
    return w;
  }

  let data = null, offline = false, tokenMalo = false;
  try {
    data = await cargar(token);
  } catch (e) {
    tokenMalo = String(e && e.message) === "token";
    offline = !tokenMalo;
    try {
      if (fm.fileExists(CACHE)) data = JSON.parse(fm.readString(CACHE));
    } catch (_) {}
  }
  if (tokenMalo || !data) {
    linea("{{AGENT_NAME}}", 14, "#e8edf7", true);
    linea(tokenMalo ? "Token inválido. Ejecuta el script en Scriptable para ponerlo de nuevo."
                    : "Sin conexión y sin datos guardados.", 12, "#f87171", false, 4);
    return w;
  }

  const v = formar(data, max);
  linea("{{AGENT_NAME}} · Pendientes", 14, "#e8edf7", true, 1);
  let resumen = (v.hoy ? "🔴 " + v.hoy + " hoy · " : "") + v.abiertos + " abiertos";
  if (v.conf) resumen += " · ⚠️ " + v.conf + " por confirmar";
  linea(resumen, 11, "#8b97b3", false, 1);
  // Un toque aquí abre {{AGENT_NAME}} directo en modo voz (sin entrar, buscar ni picar nada)
  const hab = w.addStack();
  hab.url = BASE + "/app/?hablar=1";
  const th = hab.addText("🎙 Hablar con {{AGENT_NAME}}");
  th.font = Font.boldSystemFont(12);
  th.textColor = new Color("#9db6ff");
  w.addSpacer(6);
  if (!v.items.length) linea("🎉 Nada urgente", 13, "#34d399", false, 1);
  v.items.forEach((it) => {
    const txt = it.t.length > 70 ? it.t.slice(0, 69) + "…" : it.t;
    linea((it.hot ? "• " : "◦ ") + txt, 12, it.hot ? "#f87171" : "#e8edf7", false, fam === "small" ? 2 : 1);
  });
  w.addSpacer();
  linea((offline ? "sin conexión · " : "") + "actualizado " + (v.generado || ""), 9, "#8b97b3", false, 1);
  return w;
}

const widget = await construir();
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
