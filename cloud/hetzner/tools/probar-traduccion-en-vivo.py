#!/usr/bin/env python3
"""probar-traduccion-en-vivo.py — descubre cómo habla `gpt-realtime-translate` (eventos y configuración) usando TU cuenta.

  python3 probar-traduccion-en-vivo.py            # español → inglés (por defecto)
  python3 probar-traduccion-en-vivo.py --lang zh  # español → chino

Genera una frase hablada en español con el TTS de OpenAI, la manda en vivo al traductor por WebSocket y muestra los TIPOS de eventos
que regresan (los audios solo como tamaño; nunca imprime llaves). Sirve para construir la traducción simultánea sin adivinar nombres.
Solo stdlib."""
import os, sys, ssl, json, time, base64, socket, struct, hashlib, argparse, urllib.request
from pathlib import Path

OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))
HOST = "api.openai.com"


# ---------------- WebSocket mínimo (RFC 6455) ----------------
def encode_frame(payload: bytes, opcode: int = 1, mask: bool = True) -> bytes:
    head = bytearray([0x80 | opcode])
    n = len(payload)
    m = 0x80 if mask else 0
    if n < 126:
        head.append(m | n)
    elif n < 65536:
        head += bytes([m | 126]) + struct.pack(">H", n)
    else:
        head += bytes([m | 127]) + struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        return bytes(head) + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + payload


def decode_frame(buf: bytes):
    """(opcode, payload, resto) o None si faltan bytes."""
    if len(buf) < 2:
        return None
    op, n, i = buf[0] & 0x0F, buf[1] & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, i = struct.unpack(">H", buf[2:4])[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, i = struct.unpack(">Q", buf[2:10])[0], 10
    if buf[1] & 0x80:
        if len(buf) < i + 4 + n:
            return None
        key = buf[i:i + 4]
        data = bytes(b ^ key[j % 4] for j, b in enumerate(buf[i + 4:i + 4 + n]))
        return op, data, buf[i + 4 + n:]
    if len(buf) < i + n:
        return None
    return op, buf[i:i + n], buf[i + n:]


class WS:
    def __init__(self, path: str, headers: dict):
        raw = socket.create_connection((HOST, 443), timeout=20)
        self.s = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
        k = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {k}\r\nSec-WebSocket-Version: 13\r\n" + "".join(f"{a}: {b}\r\n" for a, b in headers.items()) + "\r\n")
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.s.recv(4096)
            if not c:
                break
            buf += c
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        self.status = head.split(b"\r\n")[0].decode()
        self.body = head.decode(errors="ignore")
        self.msgs, self.frag = [], b""

    def send(self, obj):
        self.s.sendall(encode_frame(json.dumps(obj).encode()))

    def recv(self, timeout=10):
        self.s.settimeout(timeout)
        while True:
            fr = decode_frame(self.buf)
            if fr is None:
                try:
                    c = self.s.recv(65536)
                except (socket.timeout, ssl.SSLError):
                    return None
                if not c:
                    return None
                self.buf += c
                continue
            op, data, self.buf = fr
            if op == 8:
                return ("close", data[2:].decode(errors="ignore"))
            if op == 9:
                self.s.sendall(encode_frame(data, 10))
                continue
            if op in (1, 2):
                return ("text", data.decode("utf-8", "ignore"))

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def api_key():
    k = os.environ.get("OPENAI_API_KEY", "")
    for f in (OC / "credentials" / "openai.env", OC / "openclaw.env"):
        if k:
            break
        try:
            for l in f.read_text().splitlines():
                if l.startswith("OPENAI_API_KEY="):
                    k = l.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return k


def tts_pcm(key: str, texto: str) -> bytes:
    body = json.dumps({"model": "gpt-4o-mini-tts", "voice": "alloy", "input": texto, "response_format": "pcm"}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()            # PCM 16-bit mono 24 kHz


def resumen(msg: str) -> str:
    try:
        j = json.loads(msg)
    except ValueError:
        return "(no JSON) " + msg[:160]

    def corto(v, d=0):
        if isinstance(v, str):
            return f"<{len(v)} chars>" if len(v) > 80 else repr(v)
        if isinstance(v, dict) and d < 3:
            return "{" + ", ".join(f"{k}: {corto(x, d + 1)}" for k, x in v.items()) + "}"
        if isinstance(v, list):
            return f"[{len(v)} items]"
        return repr(v)
    return corto(j)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en")
    ap.add_argument("--frase", default="Buenos días. Necesitamos confirmar el precio unitario del lote y la fecha de entrega para el viernes.")
    ap.add_argument("--esperar", type=int, default=15)
    a = ap.parse_args()
    key = api_key()
    if not key:
        sys.exit("✗ No encuentro OPENAI_API_KEY")
    pcm = tts_pcm(key, a.frase)
    print(f"Audio de prueba: {len(pcm) / 48000:.1f} s (PCM 24 kHz). Conectando al traductor en vivo…")
    ws = WS("/v1/realtime/translations?model=gpt-realtime-translate", {"Authorization": f"Bearer {key}"})
    print("Conexión:", ws.status)
    if "101" not in ws.status:
        print(ws.body[:400])
        return
    # configuración: probamos la forma más probable; si el servidor se queja, el error dice cómo es
    ws.send({"type": "session.update", "session": {"audio": {"output": {"language": a.lang}}}})
    t0, enviado = time.time(), False
    chunk = 4800                                       # 100 ms de audio a 24 kHz
    for i in range(0, len(pcm), chunk):
        ws.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + chunk]).decode()})
        time.sleep(0.1)                                # en tiempo real, como un micrófono
    ws.send({"type": "input_audio_buffer.commit"})
    print("Audio enviado. Eventos recibidos (tiempo desde el inicio):")
    fin = time.time() + a.esperar
    vistos = {}
    while time.time() < fin:
        m = ws.recv(timeout=2)
        if m is None:
            continue
        if m[0] == "close":
            print(f"  [{time.time() - t0:5.1f}s] CIERRE:", m[1])
            break
        try:
            tipo = json.loads(m[1]).get("type", "?")
        except ValueError:
            tipo = "?"
        vistos[tipo] = vistos.get(tipo, 0) + 1
        if vistos[tipo] <= 3:
            print(f"  [{time.time() - t0:5.1f}s] {resumen(m[1])[:300]}")
    print("\nResumen de tipos de evento:", json.dumps(vistos, ensure_ascii=False))
    ws.close()


if __name__ == "__main__":
    main()
