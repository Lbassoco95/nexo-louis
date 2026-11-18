#!/usr/bin/env python3
"""probar-traduccion-en-vivo-2.py — segunda sonda: encuentra la forma correcta de mandar audio a `gpt-realtime-translate`.

  python3 probar-traduccion-en-vivo-2.py            # español → inglés
  python3 probar-traduccion-en-vivo-2.py --lang zh  # español → chino

Prueba, una por una, configuraciones de sesión y nombres de evento de audio, e imprime el MENSAJE COMPLETO de cada error
(los mensajes de error no llevan llaves). Luego manda la frase completa con la combinación que no dio error y muestra qué
eventos regresan (los audios, solo como tamaño). Reutiliza el cliente WebSocket de probar-traduccion-en-vivo.py."""
import sys, json, time, base64, argparse, importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("s1", Path(__file__).with_name("probar-traduccion-en-vivo.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def drenar(ws, seg=1.2):
    """Eventos que llegan en `seg` segundos → lista de dicts."""
    out, fin = [], time.time() + seg
    while time.time() < fin:
        m = ws.recv(timeout=0.4)
        if m is None:
            continue
        if m[0] == "close":
            out.append({"type": "CIERRE", "detalle": m[1]})
            break
        try:
            out.append(json.loads(m[1]))
        except ValueError:
            out.append({"type": "?", "raw": m[1][:200]})
    return out


def errores(evs):
    return [(e.get("error") or {}).get("message", "") or e.get("detalle", "") for e in evs if e.get("type") in ("error", "CIERRE")]


def descr(e):
    t = e.get("type", "?")
    if "delta" in e and isinstance(e["delta"], str) and len(e["delta"]) > 60:
        return f"{t}  (delta de {len(e['delta'])} caracteres)"
    if "audio" in e and isinstance(e["audio"], str) and len(e["audio"]) > 60:
        return f"{t}  (audio de {len(e['audio'])} caracteres)"
    txt = json.dumps({k: v for k, v in e.items() if k != "event_id"}, ensure_ascii=False)
    return txt if len(txt) < 400 else txt[:400] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en")
    ap.add_argument("--frase", default="Buenos días. Necesitamos confirmar el precio unitario del lote y la fecha de entrega para el viernes.")
    a = ap.parse_args()
    key = S.api_key()
    if not key:
        sys.exit("✗ No encuentro OPENAI_API_KEY")
    pcm = S.tts_pcm(key, a.frase)
    print(f"Audio de prueba: {len(pcm) / 48000:.1f} s. Conectando…")
    ws = S.WS("/v1/realtime/translations?model=gpt-realtime-translate", {"Authorization": f"Bearer {key}"})
    print("Conexión:", ws.status)
    if "101" not in ws.status:
        return print(ws.body[:300])
    drenar(ws, 0.8)

    print("\n[A] Configuración de la sesión (cada intento; si falla se descarta):")
    base = {"audio": {"output": {"language": a.lang}}}
    candidatos = [
        ("transcripción del audio de entrada", {"audio": {"input": {"transcription": {"model": "gpt-realtime-whisper"}}}}),
        ("formato PCM 24 kHz de entrada", {"audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}}}}),
    ]
    cfg = json.loads(json.dumps(base))
    ws.send({"type": "session.update", "session": cfg})
    ev = drenar(ws)
    print("  idioma de salida:", "OK" if not errores(ev) else errores(ev))
    for nombre, parte in candidatos:
        prueba = json.loads(json.dumps(cfg))
        prueba["audio"].setdefault("input", {}).update(parte["audio"]["input"])
        ws.send({"type": "session.update", "session": prueba})
        ev = drenar(ws)
        if errores(ev):
            print(f"  {nombre}: ✗", errores(ev)[0])
        else:
            cfg = prueba
            print(f"  {nombre}: OK")

    print("\n[B] Nombre del evento para mandar audio (un trocito de 100 ms por intento):")
    trozo = base64.b64encode(pcm[:4800]).decode()
    ganador = None
    for tipo in ("input_audio_buffer.append", "session.input_audio_buffer.append", "input_audio.append",
                 "session.input_audio.append", "audio.append", "session.audio.append"):
        ws.send({"type": tipo, "audio": trozo})
        ev = drenar(ws, 1.0)
        er = errores(ev)
        print(f"  {tipo}: " + ("✓ sin error" if not er else "✗ " + er[0]))
        if not er and ganador is None:
            ganador = tipo
    if not ganador:
        print("\nNingún nombre funcionó: pásame esta salida completa y lo resuelvo con los mensajes de arriba.")
        return ws.close()

    print(f"\n[C] Frase completa en vivo con «{ganador}»:")
    t0 = time.time()
    chunk = 4800
    for i in range(0, len(pcm), chunk):
        ws.send({"type": ganador, "audio": base64.b64encode(pcm[i:i + chunk]).decode()})
        time.sleep(0.1)
    vistos = {}
    fin = time.time() + 14
    while time.time() < fin:
        m = ws.recv(timeout=1.5)
        if m is None:
            continue
        if m[0] == "close":
            print("  CIERRE:", m[1])
            break
        try:
            e = json.loads(m[1])
        except ValueError:
            continue
        vistos[e.get("type", "?")] = vistos.get(e.get("type", "?"), 0) + 1
        if vistos[e.get("type", "?")] <= 4:
            print(f"  [{time.time() - t0:5.1f}s] {descr(e)}")
    print("\nResumen de eventos:", json.dumps(vistos, ensure_ascii=False))
    ws.close()


if __name__ == "__main__":
    main()
