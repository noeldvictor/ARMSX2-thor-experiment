"""Minimal PINE (PCSX2 IPC) client over TCP.

Works against desktop PCSX2 on Windows and against ARMSX2 on Android through
`adb forward tcp:28011 tcp:28011` - both use the TCP transport on 127.0.0.1.

Wire format (see pcsx2/PINE.cpp):
  request : u32 total_size (includes these 4 bytes) | commands...
  command : u8 opcode | args
  reply   : u32 total_size | u8 result (0 ok, 0xFF fail) | payloads...
Several commands can be packed into one request; replies come back in order.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

DEFAULT_SLOT = 28011

# Opcodes shared by upstream PCSX2 and ARMSX2.
READ8, READ16, READ32, READ64 = 0, 1, 2, 3
WRITE8, WRITE16, WRITE32, WRITE64 = 4, 5, 6, 7
VERSION, SAVESTATE, LOADSTATE, TITLE, ID, UUID, GAMEVERSION, STATUS = 8, 9, 10, 11, 12, 13, 14, 15

_WIDTH = {8: (READ8, WRITE8, "<B"), 16: (READ16, WRITE16, "<H"), 32: (READ32, WRITE32, "<I"), 64: (READ64, WRITE64, "<Q")}
STATUS_NAMES = {0: "running", 1: "paused", 2: "shutdown"}

# Server-side limits (MAX_IPC_SIZE / MAX_IPC_RETURN_SIZE), kept with headroom.
_MAX_REQUEST = 640_000
_MAX_REPLY = 440_000


class PineError(RuntimeError):
    pass


@dataclass
class Pine:
    host: str = "127.0.0.1"
    port: int = DEFAULT_SLOT
    timeout: float = 5.0

    def __post_init__(self) -> None:
        self._sock: socket.socket | None = None

    # -- transport -----------------------------------------------------------------------------
    def connect(self) -> "Pine":
        if self._sock is None:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = s
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "Pine":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise PineError("connection closed by emulator")
            buf += chunk
        return bytes(buf)

    def _exchange(self, body: bytes) -> bytes:
        """Send one request holding `body` (commands) and return the reply payload after the result byte."""
        self.connect()
        assert self._sock is not None
        self._sock.sendall(struct.pack("<I", len(body) + 4) + body)
        (size,) = struct.unpack("<I", self._recv_exact(4))
        rest = self._recv_exact(size - 4)
        if not rest or rest[0] != 0:
            # A failed batch desyncs nothing on our side, but the server resets its state; reconnect.
            self.close()
            raise PineError("emulator reported IPC failure (no VM running, or bad address?)")
        return rest[1:]

    # -- memory --------------------------------------------------------------------------------
    def read(self, addr: int, width: int = 32) -> int:
        op, _, fmt = _WIDTH[width]
        payload = self._exchange(struct.pack("<BI", op, addr))
        return struct.unpack(fmt, payload)[0]

    def write(self, addr: int, value: int, width: int = 32) -> None:
        _, op, fmt = _WIDTH[width]
        self._exchange(struct.pack("<BI", op, addr) + struct.pack(fmt, value & ((1 << width) - 1)))

    def read_block(self, addr: int, length: int) -> bytes:
        """Read `length` bytes starting at `addr` using batched 64-bit reads (8-byte aligned chunks)."""
        out = bytearray()
        # 9 bytes per request command, 8 bytes per reply payload.
        per_msg = min(_MAX_REQUEST // 9, _MAX_REPLY // 8)
        pos = addr
        end = addr + length
        while pos < end:
            n = min(per_msg, (end - pos + 7) // 8)
            body = b"".join(struct.pack("<BI", READ64, pos + 8 * i) for i in range(n))
            out += self._exchange(body)
            pos += 8 * n
        return bytes(out[:length])

    def write_block(self, addr: int, data: bytes) -> None:
        body = bytearray()
        for i, b in enumerate(data):
            body += struct.pack("<BIB", WRITE8, addr + i, b)
            if len(body) > _MAX_REQUEST - 16:
                self._exchange(bytes(body))
                body.clear()
        if body:
            self._exchange(bytes(body))

    # -- emulator ------------------------------------------------------------------------------
    def status(self) -> str:
        (code,) = struct.unpack("<I", self._exchange(bytes([STATUS])))
        return STATUS_NAMES.get(code, str(code))

    def version(self) -> str:
        return self._string(VERSION)

    def title(self) -> str:
        return self._string(TITLE)

    def serial(self) -> str:
        return self._string(ID)

    def uuid(self) -> str:
        return self._string(UUID)

    def game_version(self) -> str:
        return self._string(GAMEVERSION)

    def save_state(self, slot: int) -> None:
        self._exchange(struct.pack("<BB", SAVESTATE, slot))

    def load_state(self, slot: int) -> None:
        self._exchange(struct.pack("<BB", LOADSTATE, slot))

    def _string(self, op: int) -> str:
        payload = self._exchange(bytes([op]))
        (size,) = struct.unpack("<I", payload[:4])
        return payload[4 : 4 + size].split(b"\0", 1)[0].decode("utf-8", "replace")


if __name__ == "__main__":  # quick smoke test: python pine.py [port]
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SLOT
    with Pine(port=port) as p:
        print("version:", p.version())
        print("status :", p.status())
        try:
            print("title  :", p.title(), "| serial:", p.serial(), "| uuid:", p.uuid())
            print("mem[0x100000]:", hex(p.read(0x100000)))
        except PineError as e:
            print("no VM:", e)
