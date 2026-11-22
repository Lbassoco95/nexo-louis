#!/usr/bin/env python3
"""donna_personas.py — personas que Donna conoce por lo que escucha: quién dijo qué, en qué sesión, y el cruce con otras transcripciones y Kawiil Central.

Al guardar una escucha, cada voz con nombre se vuelve (o se enlaza a) una PERSONA; sus frases quedan en `intervenciones` (con búsqueda de texto), los nombres
que se mencionan en `menciones`, y cada grabación en `sesiones_escucha`. Así se arma, persona por persona, la base de sus aportaciones y pláticas.
Privacidad: solo se guarda texto y, de la voz, dos números aproximados (tono y brillo) para reconocerla la próxima vez — y solo de quien Polo nombró.
Cualquier persona se puede editar, fusionar o eliminar (sus frases quedan sin nombre).
"""
import json
import re
import time
import unicodedata

import donna_db

SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS personas (
  id TEXT PRIMARY KEY, usuario TEXT NOT NULL DEFAULT 'admin', nombre TEXT NOT NULL, norm TEXT NOT NULL, alias TEXT NOT NULL DEFAULT '[]',
  empresa TEXT NOT NULL DEFAULT '', rol TEXT NOT NULL DEFAULT '', kc_user TEXT NOT NULL DEFAULT '', notas TEXT NOT NULL DEFAULT '',
  voz_f0 REAL NOT NULL DEFAULT 0, voz_hf REAL NOT NULL DEFAULT 0, voz_n INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL, actualizado INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_personas_norm ON personas(usuario, norm);
CREATE TABLE IF NOT EXISTS sesiones_escucha (
  id TEXT PRIMARY KEY, usuario TEXT NOT NULL DEFAULT 'admin', ts INTEGER NOT NULL, fecha TEXT NOT NULL, titulo TEXT NOT NULL, duracion INTEGER NOT NULL DEFAULT 0,
  mid TEXT NOT NULL DEFAULT '', archivo TEXT NOT NULL DEFAULT '', resumen TEXT NOT NULL DEFAULT '', lineas INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS intervenciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sesion_id TEXT NOT NULL, persona_id TEXT, voz_n INTEGER NOT NULL DEFAULT 0, hora TEXT NOT NULL DEFAULT '',
  texto TEXT NOT NULL, original TEXT NOT NULL DEFAULT '', idioma TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_interv_persona ON intervenciones(persona_id);
CREATE INDEX IF NOT EXISTS ix_interv_sesion ON intervenciones(sesion_id, voz_n);
CREATE TABLE IF NOT EXISTS menciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sesion_id TEXT NOT NULL, interv_id INTEGER, persona_id TEXT, nombre TEXT NOT NULL, tipo TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_menc_persona ON menciones(persona_id);
"""


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def norm(nombre: str) -> str:
    return re.sub(r"\s+", " ", _strip(nombre).lower()).strip()


# ------------------------------------------------------------------ detección de nombres (texto en español)
_CAP = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,}"
_NOMBRE = r"(%s(?:\s+(?:de\s+la\s+|de\s+|del\s+)?%s)?)" % (_CAP, _CAP)
_PRESENTA = re.compile(r"(?i:\bme\s+llamo|\bmi\s+nombre\s+(?:completo\s+)?es|\byo\s+soy|\bsoy|\bte\s+habla|\bles\s+habla|\bhabla)\s+" + _NOMBRE)
_VOCATIVO_A = re.compile(r"(?i:\bgracias|\bperd[oó]n|\bdisculpa|\boye|\bhola|\bmira|\bdime|\bbuenos\s+d[ií]as|\bbuenas\s+(?:tardes|noches))[,]?\s+" + _NOMBRE)
_VOCATIVO_B = re.compile(r"(?:^|[.!?¿]\s*)(?:(?i:oye|hola|bueno|mira|dime|ok|okey|entonces)[,]?\s+)?" + _NOMBRE + r"\s*[,?]")
_VOCATIVO_C = re.compile(r"[,]\s+" + _NOMBRE + r"\s*[?!.]?\s*$")

# Palabras con mayúscula que NO son nombres de persona (tras «soy», «habla», o al abrir una frase).
_NO_NOMBRE = set(map(norm, """Español Inglés Chino Mandarín Cantonés Francés Alemán Japonés Mexicano Mexicana Chino China Dios Señor Señora Doctor Doctora Licenciado Licenciada
Ingeniero Ingeniera Lunes Martes Miércoles Jueves Viernes Sábado Domingo Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre
Entonces Bueno Claro Además Pero Sí Pues Mira Oye Hola Gracias Perfecto Exacto Listo Ahora Aquí Allá Todo Todos Nada Algo Alguien Nadie Cada Como Porque Cuando Donde Quien
Mucho Poco Bien Mal Más Menos Solo Sólo Tengo Tienes Tiene Vamos Vale Fíjate Imagínate Mexico México China Estados Unidos Kawiil Yoltik Donna""".split()))

# Nombres de pila comunes (México): permiten aceptar un vocativo al inicio de frase («Fernando, ¿tú qué opinas?») sin confundirlo con «Entonces,».
_COMUNES = set(map(norm, """Fernando Sebastián Sebastian Virginia Viri Luis Luisa Carlos Carla Juan Juana José Jose María Maria Miguel Ángel Angel Pedro Pablo Paola Paula Ana Andrea Alejandro Alejandra
Eduardo Eduardo Ricardo Roberto Rafael Raúl Raul Jorge Javier Daniel Daniela David Diego Diana Gabriel Gabriela Guillermo Gustavo Héctor Hector Hugo Ignacio Isabel Jesús Jesus Laura Lucía Lucia
Manuel Marcela Marco Marcos Mariana Mario Martha Marta Mauricio Mónica Monica Nancy Natalia Nicolás Nicolas Óscar Oscar Patricia Rodrigo Rosa Samuel Sandra Santiago Sara Sofía Sofia Sergio
Teresa Tomás Tomas Valeria Verónica Veronica Víctor Victor Ximena Yolanda Abelardo Armando Arturo Beatriz Claudia Cristina Elena Emilio Enrique Esteban Felipe Francisco Gerardo Gloria
Irene Iván Ivan Julián Julian Karla Leonardo Lorena Lupita Magdalena Maribel Mayra Mercedes Norma Olga Omar Rocío Rocio Salvador Silvia Susana Tania Vanessa Yadira Zoe""".split()))


def detectar_nombres(texto: str, conocidos=()) -> list:
    """[{nombre, tipo}] de un texto en español. tipo: 'presenta' (alguien dice quién es: es el que habla) · 'dirige' (le hablan a esa persona) ·
    'menciona' (nombre de una persona ya conocida). Conservador: mejor no sugerir que sugerir mal."""
    out, vistos = [], set()
    known = {norm(k): k for k in conocidos}
    known_first = {}
    for k in conocidos:
        known_first.setdefault(norm(k).split(" ")[0], k)

    def ok_token(n: str, estricto: bool) -> bool:
        first = norm(n).split(" ")[0]
        if first in _NO_NOMBRE or norm(n) in _NO_NOMBRE:
            return False
        return (not estricto) or first in _COMUNES or norm(n) in known or first in known_first

    def add(n: str, tipo: str):
        n = re.sub(r"\s+", " ", n).strip()
        k = (norm(n), tipo)
        if k not in vistos:
            vistos.add(k)
            out.append({"nombre": n, "tipo": tipo})

    for m in _PRESENTA.finditer(texto or ""):
        if ok_token(m.group(1), False):
            add(m.group(1), "presenta")
    for rx in (_VOCATIVO_A, _VOCATIVO_B, _VOCATIVO_C):
        for m in rx.finditer(texto or ""):
            if ok_token(m.group(1), True):
                add(m.group(1), "dirige")
    pres = {norm(x["nombre"]) for x in out}
    for k, orig in list(known.items()) + [(f, v) for f, v in known_first.items()]:
        if k in pres or len(k) < 3:
            continue
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", norm(texto or "")):
            add(orig, "menciona")
    return out


# ------------------------------------------------------------------ personas
def _p(r) -> dict:
    d = {k: r[k] for k in r.keys()}
    d["alias"] = json.loads(d.get("alias") or "[]")
    d["voz"] = {"f0": d.pop("voz_f0"), "hf": d.pop("voz_hf"), "n": d.pop("voz_n")}
    return d


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA_EXTRA)
    return c


def asegurar_esquema():
    c = _c()
    try:
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS interv_fts USING fts5(texto, interv_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
        except Exception:
            pass
        c.commit()
    finally:
        c.close()


def resolver(nombre: str, usuario: str = "admin", crear: bool = True, c=None):
    """Devuelve el id de la persona con ese nombre (o alias; o el mismo primer nombre si es único); si no existe y `crear`, la crea."""
    nombre = re.sub(r"\s+", " ", nombre or "").strip()[:60]
    if len(nombre) < 2:
        return None
    propia = c is None
    c = c or _c()
    try:
        n = norm(nombre)
        rows = c.execute("SELECT * FROM personas WHERE usuario=?", (usuario,)).fetchall()
        for r in rows:
            if r["norm"] == n or n in [norm(a) for a in json.loads(r["alias"] or "[]")]:
                return r["id"]
        if " " not in n:                                   # «Fernando» → el único Fernando conocido
            cand = [r for r in rows if r["norm"].split(" ")[0] == n]
            if len(cand) == 1:
                return cand[0]["id"]
        if not crear:
            return None
        pid = donna_db._id()
        now = int(time.time())
        c.execute("INSERT INTO personas(id,usuario,nombre,norm,creado,actualizado) VALUES(?,?,?,?,?,?)", (pid, usuario, nombre, n, now, now))
        c.commit()
        return pid
    finally:
        if propia:
            c.close()


def listar(usuario: str = "admin", q: str = "") -> list:
    c = _c()
    try:
        out = []
        for r in c.execute("SELECT * FROM personas WHERE usuario=? ORDER BY actualizado DESC", (usuario,)):
            d = _p(r)
            if q and norm(q) not in norm(d["nombre"] + " " + " ".join(d["alias"]) + " " + d["empresa"]):
                continue
            d["intervenciones"] = c.execute("SELECT COUNT(*) FROM intervenciones WHERE persona_id=?", (d["id"],)).fetchone()[0]
            d["sesiones"] = c.execute("SELECT COUNT(DISTINCT sesion_id) FROM intervenciones WHERE persona_id=?", (d["id"],)).fetchone()[0]
            out.append(d)
        return out
    finally:
        c.close()


def detalle(pid: str, limite: int = 40):
    c = _c()
    try:
        r = c.execute("SELECT * FROM personas WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        d = _p(r)
        d["aportaciones"] = [dict(x) for x in c.execute(
            "SELECT i.id, i.hora, i.texto, s.fecha, s.titulo, s.id AS sesion_id FROM intervenciones i JOIN sesiones_escucha s ON s.id=i.sesion_id "
            "WHERE i.persona_id=? ORDER BY s.ts DESC, i.id DESC LIMIT ?", (pid, limite))]
        d["sesiones"] = [dict(x) for x in c.execute(
            "SELECT s.id, s.fecha, s.titulo, COUNT(*) AS frases FROM intervenciones i JOIN sesiones_escucha s ON s.id=i.sesion_id WHERE i.persona_id=? "
            "GROUP BY s.id ORDER BY s.ts DESC LIMIT 20", (pid,))]
        d["menciones"] = c.execute("SELECT COUNT(*) FROM menciones WHERE persona_id=?", (pid,)).fetchone()[0]
        return d
    finally:
        c.close()


def editar(pid: str, **campos) -> dict:
    ok = {"nombre", "empresa", "rol", "kc_user", "notas", "alias"}
    sets, args = [], []
    for k, v in campos.items():
        if k not in ok or v is None:
            continue
        if k == "alias":
            v = json.dumps([str(a).strip()[:60] for a in (v if isinstance(v, list) else str(v).split(",")) if str(a).strip()][:10], ensure_ascii=False)
        else:
            v = str(v).strip()[:300 if k == "notas" else 80]
        if k == "nombre":
            if len(v) < 2:
                raise ValueError("Falta el nombre.")
            sets.append("norm=?"); args.append(norm(v))
        sets.append(k + "=?"); args.append(v)
    if not sets:
        raise ValueError("nada que cambiar")
    c = _c()
    try:
        cur = c.execute("UPDATE personas SET %s, actualizado=? WHERE id=?" % ",".join(sets), args + [int(time.time()), pid])
        if cur.rowcount == 0:
            raise ValueError("persona no encontrada")
        c.commit()
    finally:
        c.close()
    return detalle(pid, 0)


def fusionar(origen: str, destino: str) -> dict:
    """Une dos personas duplicadas (p. ej. «Fer» y «Fernando»): sus frases y menciones pasan al destino y el nombre viejo queda como alias."""
    if origen == destino:
        raise ValueError("Elige dos personas distintas.")
    c = _c()
    try:
        a = c.execute("SELECT * FROM personas WHERE id=?", (origen,)).fetchone()
        b = c.execute("SELECT * FROM personas WHERE id=?", (destino,)).fetchone()
        if not a or not b:
            raise ValueError("persona no encontrada")
        alias = json.loads(b["alias"] or "[]")
        for x in [a["nombre"]] + json.loads(a["alias"] or "[]"):
            if norm(x) != b["norm"] and x not in alias:
                alias.append(x)
        c.execute("UPDATE intervenciones SET persona_id=? WHERE persona_id=?", (destino, origen))
        c.execute("UPDATE menciones SET persona_id=? WHERE persona_id=?", (destino, origen))
        c.execute("UPDATE personas SET alias=?, empresa=CASE WHEN empresa='' THEN ? ELSE empresa END, rol=CASE WHEN rol='' THEN ? ELSE rol END, "
                  "kc_user=CASE WHEN kc_user='' THEN ? ELSE kc_user END, actualizado=? WHERE id=?",
                  (json.dumps(alias[:10], ensure_ascii=False), a["empresa"], a["rol"], a["kc_user"], int(time.time()), destino))
        c.execute("DELETE FROM personas WHERE id=?", (origen,))
        c.commit()
    finally:
        c.close()
    return detalle(destino, 0)


def eliminar(pid: str) -> bool:
    """Borra el perfil (y el perfil de voz). Sus frases se conservan en las transcripciones pero ya sin nombre."""
    c = _c()
    try:
        c.execute("UPDATE intervenciones SET persona_id=NULL WHERE persona_id=?", (pid,))
        c.execute("UPDATE menciones SET persona_id=NULL WHERE persona_id=?", (pid,))
        cur = c.execute("DELETE FROM personas WHERE id=?", (pid,))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


# ------------------------------------------------------------------ guardar una escucha
_VOZ_GENERICA = re.compile(r"^\s*voz\s*\d*\s*$|^\s*voz por identificar\s*$", re.I)


def _aprende_voz(c, pid: str, f0: float, hf: float):
    if not (f0 and f0 > 50):
        return
    r = c.execute("SELECT voz_f0, voz_hf, voz_n FROM personas WHERE id=?", (pid,)).fetchone()
    k = min(int(r["voz_n"]), 8)                          # promedio móvil: la voz se afina con las sesiones sin dejar de moverse
    c.execute("UPDATE personas SET voz_f0=?, voz_hf=?, voz_n=? WHERE id=?",
              ((r["voz_f0"] * k + f0) / (k + 1), (r["voz_hf"] * k + hf) / (k + 1), int(r["voz_n"]) + 1, pid))


def guardar_sesion(titulo: str, duracion: int, voces: list, lineas: list, mid: str = "", archivo: str = "", resumen: str = "", usuario: str = "admin") -> dict:
    """voces: [{n, nombre, f0?, hf?}] · lineas: [{t, voz, es, orig, idioma}]. Devuelve {sesion_id, personas:[{id,nombre,voz}], sugeridos:[...]}."""
    asegurar_esquema()
    c = _c()
    try:
        sid = donna_db._id()
        now = int(time.time())
        c.execute("INSERT INTO sesiones_escucha(id,usuario,ts,fecha,titulo,duracion,mid,archivo,resumen,lineas) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (sid, usuario, now, time.strftime("%Y-%m-%d", time.localtime(now)), titulo[:120], int(duracion or 0), mid, archivo, resumen[:2000], len(lineas)))
        voz_persona, personas = {}, []
        for v in voces:
            n = int(v.get("n") or 0)
            nombre = str(v.get("nombre") or "").strip()
            if not n or not nombre or _VOZ_GENERICA.match(nombre):
                continue
            pid = resolver(nombre, usuario, True, c)
            if pid:
                voz_persona[n] = pid
                try:
                    _aprende_voz(c, pid, float(v.get("f0") or 0), float(v.get("hf") or 0))
                except (TypeError, ValueError):
                    pass
                c.execute("UPDATE personas SET actualizado=? WHERE id=?", (now, pid))
                personas.append({"id": pid, "nombre": nombre, "voz": n})
        conocidos = [r["nombre"] for r in c.execute("SELECT nombre FROM personas WHERE usuario=?", (usuario,))]
        asignados = {norm(p["nombre"]) for p in personas} | {norm(p["nombre"]).split(" ")[0] for p in personas}
        sug = {}
        for l in lineas:
            es = str(l.get("es", ""))[:3000]
            orig = str(l.get("orig", ""))[:3000]
            if not (es.strip() or orig.strip()):
                continue
            vn = int(l.get("voz") or 0)
            cur = c.execute("INSERT INTO intervenciones(sesion_id,persona_id,voz_n,hora,texto,original,idioma) VALUES(?,?,?,?,?,?,?)",
                            (sid, voz_persona.get(vn), vn, str(l.get("t", ""))[:8], es, orig, str(l.get("idioma", ""))[:4]))
            iid = cur.lastrowid
            try:
                c.execute("INSERT INTO interv_fts(texto, interv_id) VALUES(?,?)", (es, iid))
            except Exception:
                pass
            for d in detectar_nombres(es, conocidos):
                pid = resolver(d["nombre"], usuario, False, c)
                c.execute("INSERT INTO menciones(sesion_id,interv_id,persona_id,nombre,tipo) VALUES(?,?,?,?,?)", (sid, iid, pid, d["nombre"], d["tipo"]))
                if d["tipo"] in ("presenta", "dirige") and norm(d["nombre"]) not in asignados and norm(d["nombre"]).split(" ")[0] not in asignados:
                    e = sug.setdefault(norm(d["nombre"]), {"nombre": d["nombre"], "tipo": d["tipo"], "veces": 0, "voz": vn if d["tipo"] == "presenta" else 0})
                    e["veces"] += 1
        c.commit()
        return {"sesion_id": sid, "personas": personas, "sugeridos": sorted(sug.values(), key=lambda x: -x["veces"])[:8]}
    finally:
        c.close()


def asignar(sesion_id: str, voz_n: int, nombre: str, usuario: str = "admin") -> dict:
    """Después de guardar: «la Voz 2 de esta sesión era Fernando». Actualiza sus frases y su perfil."""
    c = _c()
    try:
        if not c.execute("SELECT 1 FROM sesiones_escucha WHERE id=?", (sesion_id,)).fetchone():
            raise ValueError("sesión no encontrada")
        pid = resolver(nombre, usuario, True, c)
        if not pid:
            raise ValueError("Falta el nombre.")
        cur = c.execute("UPDATE intervenciones SET persona_id=? WHERE sesion_id=? AND voz_n=?", (pid, sesion_id, int(voz_n)))
        c.execute("UPDATE personas SET actualizado=? WHERE id=?", (int(time.time()), pid))
        c.commit()
        return {"persona_id": pid, "frases": cur.rowcount}
    finally:
        c.close()


def sesiones(usuario: str = "admin", limite: int = 20) -> list:
    """Sesiones con las voces que quedaron SIN nombre (para asignarlas después)."""
    c = _c()
    try:
        out = []
        for s in c.execute("SELECT * FROM sesiones_escucha WHERE usuario=? ORDER BY ts DESC LIMIT ?", (usuario, limite)):
            d = {k: s[k] for k in s.keys()}
            d["sin_nombre"] = [dict(x) for x in c.execute(
                "SELECT voz_n, COUNT(*) AS frases, MIN(texto) AS ejemplo FROM intervenciones WHERE sesion_id=? AND persona_id IS NULL AND voz_n>0 GROUP BY voz_n", (s["id"],))]
            d["personas"] = [dict(x) for x in c.execute(
                "SELECT p.id, p.nombre, COUNT(*) AS frases FROM intervenciones i JOIN personas p ON p.id=i.persona_id WHERE i.sesion_id=? GROUP BY p.id", (s["id"],))]
            out.append(d)
        return out
    finally:
        c.close()


# ------------------------------------------------------------------ búsqueda y cruce
def buscar(texto: str, persona_id: str = "", limite: int = 20, usuario: str = "admin") -> list:
    """Frases (de cualquier sesión) que contienen el texto; opcionalmente solo las de una persona."""
    asegurar_esquema()
    c = _c()
    try:
        q = " ".join(re.findall(r"\w+", _strip(texto))[:8])
        if not q:
            return []
        try:
            ids = [r[0] for r in c.execute("SELECT interv_id FROM interv_fts WHERE interv_fts MATCH ? LIMIT 200", (" ".join(w + "*" for w in q.split()),))]
        except Exception:
            ids = [r[0] for r in c.execute("SELECT id FROM intervenciones WHERE texto LIKE ? LIMIT 200", ("%" + q.split()[0] + "%",))]
        if not ids:
            return []
        qs = "SELECT i.id, i.hora, i.texto, i.persona_id, p.nombre AS persona, s.fecha, s.titulo, s.id AS sesion_id FROM intervenciones i " \
             "JOIN sesiones_escucha s ON s.id=i.sesion_id LEFT JOIN personas p ON p.id=i.persona_id WHERE s.usuario=? AND i.id IN (%s)" % ",".join("?" * len(ids))
        args = [usuario] + ids
        if persona_id:
            qs += " AND i.persona_id=?"
            args.append(persona_id)
        return [dict(x) for x in c.execute(qs + " ORDER BY s.ts DESC, i.id DESC LIMIT ?", args + [limite])]
    finally:
        c.close()


def cruce_transcripciones(pid: str, limite: int = 12) -> list:
    """Otras sesiones donde se HABLA de la persona aunque no haya participado (menciones), con la frase."""
    c = _c()
    try:
        return [dict(x) for x in c.execute(
            "SELECT m.tipo, i.texto, i.hora, s.fecha, s.titulo, s.id AS sesion_id FROM menciones m JOIN intervenciones i ON i.id=m.interv_id "
            "JOIN sesiones_escucha s ON s.id=m.sesion_id WHERE m.persona_id=? AND (i.persona_id IS NULL OR i.persona_id!=?) "
            "ORDER BY s.ts DESC LIMIT ?", (pid, pid, limite))]
    finally:
        c.close()


def cruce_kawiil_central(core, nombre: str, empresa: str = "", limite: int = 12) -> dict:
    """Solo lectura en Kawiil Central: ¿hay un usuario con ese nombre?, sus tareas abiertas, y el cliente si hay empresa. Nunca falla: devuelve {error}."""
    try:
        conn, err = core._kawiil_central_pg()
        if err:
            return {"error": err.replace("❌ ", "")}
        conn.autocommit = True
        cur = conn.cursor()
        q = _strip(nombre).lower()
        cur.execute("SELECT user_id, full_name, email FROM public.profiles WHERE is_active IS NOT FALSE")
        usuarios = [(str(u), fn or "", em or "") for u, fn, em in cur.fetchall() if u and q and q in _strip(fn or "").lower()]
        res = {"usuarios": [{"user_id": u, "nombre": fn, "email": em} for u, fn, em in usuarios[:3]], "tareas": [], "cliente": None}
        if len(usuarios) == 1:
            cur.execute("SELECT id::text, title, status, due_date FROM public.tasks WHERE assigned_to::text=%s AND COALESCE(status,'') NOT IN ('hecho','done','completed','completada') "
                        "ORDER BY due_date NULLS LAST LIMIT %s", (usuarios[0][0], limite))
            res["tareas"] = [{"id": a, "titulo": b, "estado": c_, "vence": str(d) if d else ""} for a, b, c_, d in cur.fetchall()]
        if empresa:
            cur.execute("SELECT id::text, name FROM public.clients")
            e = _strip(empresa).lower()
            m = [(i, n) for i, n in cur.fetchall() if e in _strip(n or "").lower()]
            res["cliente"] = {"id": m[0][0], "nombre": m[0][1]} if m else None
        conn.close()
        return res
    except Exception as e:
        return {"error": str(e)[:160]}


def resumen_texto(pid: str, core=None) -> str:
    """Para que Donna lo lea: perfil + lo que más ha aportado + dónde se habla de la persona + Kawiil Central."""
    d = detalle(pid, 12)
    if not d:
        return "No encontré esa persona."
    out = ["**%s**%s%s" % (d["nombre"], (" · " + d["rol"]) if d["rol"] else "", (" · " + d["empresa"]) if d["empresa"] else "")]
    if d["alias"]:
        out.append("También le dicen: " + ", ".join(d["alias"]))
    out.append("Participó en %d sesión(es); %d mención(es) en total." % (len(d["sesiones"]), d["menciones"]))
    if d["notas"]:
        out.append("Notas: " + d["notas"])
    for a in d["aportaciones"][:8]:
        out.append("• [%s %s] %s" % (a["fecha"], a["hora"], a["texto"][:220]))
    cr = cruce_transcripciones(pid, 5)
    if cr:
        out.append("Otras sesiones donde hablan de esa persona:")
        out += ["• [%s] %s" % (x["fecha"], x["texto"][:200]) for x in cr]
    if core is not None:
        kc = cruce_kawiil_central(core, d["nombre"], d["empresa"])
        if kc.get("tareas"):
            out.append("Tareas abiertas en Kawiil Central:")
            out += ["• %s (%s%s)" % (t["titulo"], t["estado"], ", vence " + t["vence"] if t["vence"] else "") for t in kc["tareas"][:8]]
    return "\n".join(out)
