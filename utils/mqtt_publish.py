"""Minimal MQTT 3.1.1 client for hooks: QoS-0 publish and read-one (stdlib only).

Hooks run on every tool call, so this stays small and fast: one TCP
connection per call, no background threads, no third-party packages.

Broker: MADNESS_MQTT_HOST / MADNESS_MQTT_PORT (default localhost:1883).

If the broker can't be reached, a marker file (one per host:port) makes every
hook skip MQTT for that broker for DOWN_BACKOFF_SECONDS, so an unreachable
broker (offline laptop) costs one timeout per minute instead of one per tool
call, and a session with a stale config can't silence one with the right one. Every function fails
silently: publish returns False, get returns None.
"""

import json
import os
import socket
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Optional

TIMEOUT_SECONDS = 0.5
DOWN_BACKOFF_SECONDS = 60
MARKER_DIR = Path.home() / ".claude"


def broker():
    host = os.getenv("MADNESS_MQTT_HOST") or "localhost"
    port = int(os.getenv("MADNESS_MQTT_PORT") or 1883)
    return host, port


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _utf8(s: str) -> bytes:
    data = s.encode("utf-8")
    return struct.pack("!H", len(data)) + data


def _packet(header: int, body: bytes) -> bytes:
    return bytes([header]) + _varint(len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("broker closed the connection")
        data += chunk
    return data


def _read_packet(sock: socket.socket):
    header = _recv_exact(sock, 1)[0]
    length, shift = 0, 0
    while True:
        byte = _recv_exact(sock, 1)[0]
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return header, _recv_exact(sock, length)


def down_marker() -> Path:
    host, port = broker()
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in f"{host}-{port}")
    return MARKER_DIR / f".mqtt_down-{safe}"


def _broker_marked_down() -> bool:
    try:
        return time.time() - down_marker().stat().st_mtime < DOWN_BACKOFF_SECONDS
    except OSError:
        return False


def _mark_down() -> None:
    try:
        MARKER_DIR.mkdir(parents=True, exist_ok=True)
        down_marker().touch()
    except OSError:
        pass


def _connect(timeout: float) -> socket.socket:
    host, port = broker()
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    client_id = f"hook-{uuid.uuid4().hex[:12]}"
    # protocol "MQTT" level 4 (3.1.1), clean session, keepalive 30s
    body = _utf8("MQTT") + bytes([4, 0x02]) + struct.pack("!H", 30) + _utf8(client_id)
    sock.sendall(_packet(0x10, body))
    header, ack = _read_packet(sock)
    if header >> 4 != 2 or len(ack) < 2 or ack[1] != 0:
        sock.close()
        raise ConnectionError("broker refused the connection")
    return sock


def _encode(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload).encode("utf-8")


def publish(topic: str, payload: Any = b"", retain: bool = False, timeout: float = TIMEOUT_SECONDS) -> bool:
    """Publish once at QoS 0. Dicts and lists are sent as JSON."""
    if _broker_marked_down():
        return False
    try:
        sock = _connect(timeout)
    except OSError:
        _mark_down()
        return False
    try:
        sock.sendall(_packet(0x30 | (0x01 if retain else 0), _utf8(topic) + _encode(payload)))
        sock.sendall(b"\xe0\x00")  # DISCONNECT
        return True
    except OSError:
        return False
    finally:
        sock.close()


def get(topic: str, timeout: float = TIMEOUT_SECONDS) -> Optional[str]:
    """Return the retained message on `topic` (as text), or None."""
    if _broker_marked_down():
        return None
    try:
        sock = _connect(timeout)
    except OSError:
        _mark_down()
        return None
    try:
        # SUBSCRIBE, packet id 1, requested QoS 0
        sock.sendall(_packet(0x82, struct.pack("!H", 1) + _utf8(topic) + b"\x00"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            header, body = _read_packet(sock)
            if header >> 4 == 3:  # PUBLISH (QoS 0: topic, then payload)
                topic_len = struct.unpack("!H", body[:2])[0]
                return body[2 + topic_len:].decode("utf-8", errors="replace")
        return None
    except OSError:
        return None
    finally:
        try:
            sock.sendall(b"\xe0\x00")
        except OSError:
            pass
        sock.close()
