#!/usr/bin/env python3
"""ws_lite.py — WebSocket mínimo (RFC 6455) con solo stdlib: servidor (para la app) y cliente (hacia OpenAI).

  Conn(sock, cliente=False)   conexión ya abierta; recv_message(timeout) → ("text"|"binary"|"close", dato) | None si no llegó nada;
                              send_text / send_binary son seguras entre hilos.
  accept_key(clave)           valor de Sec-WebSocket-Accept para el apretón de manos del servidor.
  connect_url(url, headers)   abre un cliente (wss:// o ws://) y devuelve Conn.
Solo se usa para el traductor en vivo: audio PCM y eventos JSON, mensajes de hasta 8 MB."""
import os
import ssl
import json
import base64
import hashlib
import socket
import struct
import threading
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MSG = 8 * 1024 * 1024


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 1, mask: bool = False) -> bytes:
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
        # enmascarar con enteros grandes es mucho más rápido que byte a byte
        k = int.from_bytes((key * (n // 4 + 1))[:n], "big") if n else 0
        body = (int.from_bytes(payload, "big") ^ k).to_bytes(n, "big") if n else b""
        return bytes(head) + key + body
    return bytes(head) + payload


def decode_frame(buf: bytes):
    """(fin, opcode, payload, resto) o None si faltan bytes."""
    if len(buf) < 2:
        return None
    fin, op, n, i = bool(buf[0] & 0x80), buf[0] & 0x0F, buf[1] & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, i = struct.unpack(">H", buf[2:4])[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, i = struct.unpack(">Q", buf[2:10])[0], 10
    if n > MAX_MSG:
        raise ValueError("mensaje WebSocket demasiado grande")
    masked = bool(buf[1] & 0x80)
    need = i + (4 if masked else 0) + n
    if len(buf) < need:
        return None
    if masked:
        key = buf[i:i + 4]
        raw = buf[i + 4:i + 4 + n]
        k = int.from_bytes((key * (n // 4 + 1))[:n], "big") if n else 0
        data = (int.from_bytes(raw, "big") ^ k).to_bytes(n, "big") if n else b""
    else:
        data = buf[i:i + n]
    return fin, op, data, buf[need:]


class Conn:
    def __init__(self, sock, cliente: bool = False, buf: bytes = b""):
        self.s, self.cliente, self.buf, self.cerrada = sock, cliente, buf, False
        self._lock = threading.Lock()
        self._frag, self._frag_op = b"", 1

    def _send(self, op: int, payload: bytes) -> None:
        with self._lock:
            self.s.sendall(encode_frame(payload, op, mask=self.cliente))

    def send_text(self, texto: str) -> None:
        self._send(1, texto.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send(2, data)

    def recv_message(self, timeout: float = 1.0):
        """("text", str) | ("binary", bytes) | ("close", motivo) | None (no llegó nada en `timeout`)."""
        self.s.settimeout(timeout)
        while True:
            try:
                fr = decode_frame(self.buf)
            except ValueError as e:
                self.cerrada = True
                return ("close", str(e))
            if fr is None:
                try:
                    c = self.s.recv(65536)
                except (socket.timeout, ssl.SSLWantReadError):
                    return None
                except (OSError, ssl.SSLError):
                    self.cerrada = True
                    return ("close", "conexión perdida")
                if not c:
                    self.cerrada = True
                    return ("close", "cerrada por el otro lado")
                self.buf += c
                continue
            fin, op, data, self.buf = fr
            if op == 8:
                self.cerrada = True
                try:
                    self._send(8, data[:2])
                except OSError:
                    pass
                return ("close", data[2:].decode("utf-8", "ignore") if len(data) > 2 else "")
            if op == 9:
                try:
                    self._send(10, data)
                except OSError:
                    pass
                continue
            if op == 10:
                continue
            if op in (1, 2):
                self._frag, self._frag_op = data, op
            elif op == 0:
                self._frag += data
                if len(self._frag) > MAX_MSG:
                    self.cerrada = True
                    return ("close", "mensaje demasiado grande")
            if fin:
                out, self._frag = self._frag, b""
                return ("text", out.decode("utf-8", "ignore")) if self._frag_op == 1 else ("binary", out)

    def close(self) -> None:
        if not self.cerrada:
            try:
                self._send(8, b"\x03\xe8")
            except OSError:
                pass
        self.cerrada = True
        try:
            self.s.close()
        except OSError:
            pass


def connect_url(url: str, headers: dict = None, timeout: float = 20):
    """Cliente: wss://host/ruta?x=y o ws://host:puerto/ruta. Lanza RuntimeError si no hay 101."""
    u = urlparse(url)
    tls = u.scheme == "wss"
    host, port = u.hostname, u.port or (443 if tls else 80)
    raw = socket.create_connection((host, port), timeout=timeout)
    s = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if tls else raw
    key = base64.b64encode(os.urandom(16)).decode()
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    req = (f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
           + "".join(f"{a}: {b}\r\n" for a, b in (headers or {}).items()) + "\r\n")
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        c = s.recv(4096)
        if not c:
            break
        buf += c
        if len(buf) > 65536:
            break
    head, _, resto = buf.partition(b"\r\n\r\n")
    linea = head.split(b"\r\n")[0].decode("latin-1", "ignore")
    if " 101 " not in linea + " ":
        s.close()
        raise RuntimeError("el servidor no aceptó el WebSocket: " + linea[:80])
    return Conn(s, cliente=True, buf=resto)
