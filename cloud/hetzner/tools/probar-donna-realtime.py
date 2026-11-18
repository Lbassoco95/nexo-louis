#!/usr/bin/env python3
"""probar-donna-realtime.py — descubre cómo se monta a Donna sobre la voz en tiempo real de OpenAI (gpt-realtime-2.1).

  python3 probar-donna-realtime.py                    # modelo gpt-realtime-2.1
  python3 probar-donna-realtime.py --model gpt-realtime-mini

Con TU cuenta, y sin mostrar llaves, averigua:
  [A] la sesión por defecto (formato de audio, detección de turnos) y qué configuración acepta (voz, instrucciones, herramientas)
  [B] los eventos de una respuesta hablada (voz, texto) a un mensaje escrito
  [C] cómo llega una LLAMADA A HERRAMIENTA (así Donna usará su agenda, Patio, Slack…) y cómo se le devuelve el resultado
  [D] qué pasa cuando le hablas (audio en vivo): detección de turnos, transcripción de lo que dijiste y respuesta
Los mensajes de error se imprimen completos (no llevan llaves); los audios, solo como tamaño."""
import sys, json, time, base64, argparse, importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("s1", Path(__file__).with_name("probar-traduccion-en-vivo.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def leer(ws, seg, parar=None):
    out, fin = [], time.time() + seg
    while time.time() < fin:
        m = ws.recv(timeout=0.5)
        if m is None:
            continue
        if m[0] == "close":
            out.append({"type": "CIERRE", "detalle": m[1]})
            break
        try:
            e = json.loads(m[1])
        except ValueError:
            continue
        out.append(e)
        if parar and parar(e):
            out += leer(ws, 0.6)
            break
    return out


def corto(e, n=420):
    def rec(v, d=0):
        if isinstance(v, str):
            return f"<{len(v)} caracteres>" if len(v) > 140 else v
        if isinstance(v, dict) and d < 4:
            return {k: rec(x, d + 1) for k, x in v.items() if k != "event_id"}
        if isinstance(v, list):
            return [rec(x, d + 1) for x in v[:4]] + ([f"…(+{len(v) - 4})"] if len(v) > 4 else [])
        return v
    t = json.dumps(rec(e), ensure_ascii=False)
    return t if len(t) <= n else t[:n] + "…"


def errs(evs):
    return [(e.get("error") or {}).get("message", "?") for e in evs if e.get("type") == "error"] + \
           [e.get("detalle", "") for e in evs if e.get("type") == "CIERRE"]


def mostrar(evs, max_por_tipo=2, t0=None):
    vistos = {}
    for e in evs:
        t = e.get("type", "?")
        vistos[t] = vistos.get(t, 0) + 1
        if vistos[t] <= max_por_tipo:
            print("   ", corto(e))
    print("    → tipos:", json.dumps(vistos, ensure_ascii=False))
    return vistos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-realtime-2.1")
    a = ap.parse_args()
    key = S.api_key()
    if not key:
        sys.exit("✗ No encuentro OPENAI_API_KEY")
    ws = S.WS(f"/v1/realtime?model={a.model}", {"Authorization": f"Bearer {key}"})
    print("Conexión:", ws.status)
    if "101" not in ws.status:
        return print(ws.body[:300])

    print("\n[A] Sesión por defecto y configuración:")
    ev = leer(ws, 1.5)
    for e in ev:
        if e.get("type") == "session.created":
            print("    sesión por defecto:", corto(e.get("session", {}), 900))
    tool = {"type": "function", "name": "consultar_agenda", "description": "Devuelve los pendientes de hoy de Polo.",
            "parameters": {"type": "object", "properties": {"dia": {"type": "string", "description": "hoy o mañana"}}, "required": []}}
    variantes = [
        ("GA con type=realtime",
         {"type": "realtime", "instructions": "Eres Donna, asistente de Polo. Habla en español de México, de tú, breve y natural.",
          "audio": {"input": {"transcription": {"model": "gpt-4o-mini-transcribe"}, "turn_detection": {"type": "server_vad"}},
                    "output": {"voice": "marin"}}, "tools": [tool], "tool_choice": "auto"}),
        ("sin type",
         {"instructions": "Eres Donna, asistente de Polo. Habla en español de México, de tú, breve y natural.",
          "audio": {"input": {"transcription": {"model": "gpt-4o-mini-transcribe"}, "turn_detection": {"type": "server_vad"}},
                    "output": {"voice": "marin"}}, "tools": [tool], "tool_choice": "auto"}),
    ]
    ok = None
    for nombre, cfg in variantes:
        ws.send({"type": "session.update", "session": cfg})
        r = leer(ws, 1.6)
        er = errs(r)
        print(f"    {nombre}: " + ("✓ aceptada" if not er else "✗ " + er[0]))
        if not er and ok is None:
            ok = cfg
            for e in r:
                if e.get("type") == "session.updated":
                    print("      sesión ahora:", corto(e.get("session", {}), 700))
            break
    if ok is None:
        print("\nNinguna configuración funcionó; pásame esta salida completa.")
        return ws.close()

    print("\n[B] Respuesta hablada a un mensaje escrito:")
    ws.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "Hola Donna, ¿cómo estás? Dime solo hola en una frase corta."}]}})
    ws.send({"type": "response.create"})
    ev = leer(ws, 14, lambda e: e.get("type") == "response.done")
    mostrar(ev)

    print("\n[C] Llamada a herramienta:")
    ws.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "Consulta mi agenda de hoy con la herramienta y dime qué tengo."}]}})
    ws.send({"type": "response.create"})
    ev = leer(ws, 14, lambda e: e.get("type") == "response.done" and any(
        (o.get("type") == "function_call") for o in (e.get("response", {}).get("output") or [])))
    mostrar(ev)
    llamada = None
    for e in ev:
        if e.get("type") == "response.function_call_arguments.done":
            llamada = e
        if e.get("type") == "response.output_item.done" and (e.get("item") or {}).get("type") == "function_call":
            llamada = llamada or {"call_id": e["item"].get("call_id"), "name": e["item"].get("name"), "arguments": e["item"].get("arguments")}
    if llamada:
        print("    ✓ llamada recibida:", corto({k: llamada.get(k) for k in ("name", "arguments", "call_id")}))
        ws.send({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": llamada.get("call_id"),
                                                               "output": json.dumps({"pendientes": ["Junta con Dazon a las 10", "Llamar a Kailash"]}, ensure_ascii=False)}})
        ws.send({"type": "response.create"})
        print("    …devolví el resultado; la respuesta con ese dato:")
        mostrar(leer(ws, 14, lambda e: e.get("type") == "response.done"))
    else:
        print("    (el modelo no llamó la herramienta en este intento)")

    print("\n[D] Audio en vivo (le hablo con una frase en español):")
    pcm = S.tts_pcm(key, "Buenos días Donna. ¿Qué pendientes tengo hoy?")
    t0 = time.time()
    for i in range(0, len(pcm), 4800):
        ws.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + 4800]).decode()})
        time.sleep(0.1)
    for _ in range(12):                                   # silencio para que la detección de turnos cierre la frase
        ws.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(b"\x00" * 4800).decode()})
        time.sleep(0.1)
    ev = leer(ws, 16, lambda e: e.get("type") == "response.done")
    vistos = mostrar(ev, 2)
    print(f"    ({time.time() - t0:.1f} s en total)")
    ws.close()
    print("\nListo. Pégame TODA la salida (de [A] a [D]).")


if __name__ == "__main__":
    main()
