"""Desktop PCSX2 control: process, window, keys, screenshots, save-state memory dumps,
memory search, disassembly and PNACH output. Windows only (ctypes/user32).

Everything here is plain functions on a `Config`; server.py wraps them as MCP tools and
a CLI so the same code runs with or without an MCP client.
"""

from __future__ import annotations

import configparser
import ctypes
import ctypes.wintypes as wt
import glob
import io
import json
import os
import re
import struct
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pine import Pine, PineError

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

EE_RAM_SIZE = 32 * 1024 * 1024

# -- configuration -----------------------------------------------------------------------------


@dataclass
class Config:
    pcsx2_dir: Path = Path(r"F:\Projects\pcsx2-desktop\pcsx2")
    games_dir: Path = Path(r"F:\Projects\pcsx2-desktop\games")
    pine_port: int = 28011
    exe_name: str = "pcsx2-qt.exe"

    @property
    def exe(self) -> Path:
        return self.pcsx2_dir / self.exe_name

    @property
    def ini(self) -> Path:
        return self.pcsx2_dir / "inis" / "PCSX2.ini"

    @property
    def sstates(self) -> Path:
        return self.pcsx2_dir / "sstates"

    @property
    def snaps(self) -> Path:
        return self.pcsx2_dir / "snaps"

    @property
    def cheats(self) -> Path:
        return self.pcsx2_dir / "cheats"

    @property
    def logs(self) -> Path:
        return self.pcsx2_dir / "logs" / "emulog.txt"


def load_config() -> Config:
    """config.local.json next to this file overrides the defaults (it is gitignored)."""
    cfg = Config()
    local = HERE / "config.local.json"
    if local.exists():
        data = json.loads(local.read_text(encoding="utf-8"))
        for key in ("pcsx2_dir", "games_dir"):
            if key in data:
                setattr(cfg, key, Path(data[key]))
        for key in ("pine_port", "exe_name"):
            if key in data:
                setattr(cfg, key, data[key])
    return cfg


# -- window / process (user32) -----------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _pids_of(exe_name: str) -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"], capture_output=True, text=True
    ).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == exe_name.lower():
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def find_window(cfg: Config) -> int | None:
    """Top-level visible window of pcsx2-qt with the largest client area (the main window)."""
    pids = _pids_of(cfg.exe_name)
    if not pids:
        return None
    best: tuple[int, int] | None = None

    @_EnumWindowsProc
    def cb(hwnd, _lparam):
        nonlocal best
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if best is None or area > best[1]:
                best = (hwnd, area)
        return True

    user32.EnumWindows(cb, 0)
    return best[0] if best else None


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(n)
    user32.GetWindowTextW(hwnd, buf, n)
    return buf.value


def focus_window(hwnd: int) -> bool:
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows refuses SetForegroundWindow from a background process unless an input event
    # was just synthesised; the ALT tap is the usual way to earn that right.
    _key(0x12, down=True)
    _key(0x12, down=False)
    user32.SetForegroundWindow(hwnd)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd


def launch(cfg: Config, game: str | None = None, state_slot: int | None = None, fast_boot: bool = True) -> str:
    args = [str(cfg.exe)]
    if fast_boot:
        args.append("-fastboot")
    if state_slot is not None:
        args += ["-state", str(state_slot)]
    if game:
        args += ["--", resolve_game(cfg, game)]
    subprocess.Popen(args, cwd=str(cfg.pcsx2_dir), creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    return " ".join(args)


def resolve_game(cfg: Config, game: str) -> str:
    p = Path(game)
    if p.exists():
        return str(p)
    hits = [f for f in cfg.games_dir.iterdir() if game.lower() in f.name.lower()]
    if len(hits) != 1:
        raise FileNotFoundError(f"{game!r}: {len(hits)} matches in {cfg.games_dir}: {[h.name for h in hits]}")
    return str(hits[0])


def quit_pcsx2(cfg: Config, timeout: float = 15.0) -> bool:
    hwnd = find_window(cfg)
    if hwnd is None:
        return True
    WM_CLOSE = 0x0010
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    end = time.time() + timeout
    while time.time() < end:
        if not _pids_of(cfg.exe_name):
            return True
        time.sleep(0.25)
    subprocess.run(["taskkill", "/F", "/IM", cfg.exe_name], capture_output=True)
    return not _pids_of(cfg.exe_name)


def wait_for_pine(cfg: Config, timeout: float = 60.0) -> Pine:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            p = Pine(port=cfg.pine_port).connect()
            p.status()
            return p
        except (OSError, PineError) as e:
            last = e
            time.sleep(0.5)
    raise TimeoutError(f"PINE not reachable on {cfg.pine_port}: {last}")


def wait_for_vm(cfg: Config, timeout: float = 90.0) -> Pine:
    """PINE up and a VM running (title readable)."""
    p = wait_for_pine(cfg, timeout)
    end = time.time() + timeout
    while time.time() < end:
        try:
            p.title()
            return p
        except PineError:
            time.sleep(0.5)
            p = Pine(port=cfg.pine_port)
    raise TimeoutError("no VM running")


# -- keyboard input (SendInput) ----------------------------------------------------------------

VK = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "return": 0x0D, "enter": 0x0D, "backspace": 0x08,
    "space": 0x20, "tab": 0x09, "escape": 0x1B, "shift": 0x10, "alt": 0x12, "control": 0x11,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{d: ord(d) for d in "0123456789"},
}
EXTENDED = {0x26, 0x28, 0x25, 0x27}  # arrow keys need the extended flag to be seen as arrows


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _key(vk: int, down: bool) -> None:
    KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0001
    scan = user32.MapVirtualKeyW(vk, 0)
    flags = (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    inp = _INPUT(type=1)
    inp.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
        raise OSError(f"SendInput failed: {ctypes.get_last_error()}")


def pad_bindings(cfg: Config) -> dict[str, str]:
    """PS2 button -> key name, from [Pad1] in PCSX2.ini (e.g. {'cross': 'k', 'lup': 'w'})."""
    ini = configparser.RawConfigParser(strict=False)
    ini.optionxform = str  # keep case
    ini.read(cfg.ini, encoding="utf-8")
    out = {}
    if ini.has_section("Pad1"):
        for k, v in ini.items("Pad1"):
            if v.startswith("Keyboard/"):
                out[k.lower()] = v.split("/", 1)[1].lower()
    return out


HOTKEYS = {"screenshot": "f8", "save_state": "f1", "load_state": "f3", "next_slot": "f2", "pause": "space", "turbo": "tab", "menu": "escape"}


def _resolve_keys(cfg: Config, names: list[str]) -> list[int]:
    binds = pad_bindings(cfg)
    vks = []
    for n in names:
        n = n.lower()
        key = binds.get(n) or HOTKEYS.get(n) or n
        if key not in VK:
            raise KeyError(f"unknown button/key {n!r}; buttons: {sorted(binds)}; keys: {sorted(VK)}")
        vks.append(VK[key])
    return vks


def press(cfg: Config, names: list[str], hold_ms: int = 80, repeat: int = 1, gap_ms: int = 120) -> None:
    hwnd = find_window(cfg)
    if hwnd is None:
        raise RuntimeError("PCSX2 window not found")
    if not focus_window(hwnd):
        raise RuntimeError("could not focus the PCSX2 window (another window is holding foreground)")
    vks = _resolve_keys(cfg, names)
    for i in range(repeat):
        for vk in vks:
            _key(vk, True)
        time.sleep(hold_ms / 1000)
        for vk in reversed(vks):
            _key(vk, False)
        if i + 1 < repeat:
            time.sleep(gap_ms / 1000)


# -- screenshots -------------------------------------------------------------------------------


def screenshot(cfg: Config, mode: str = "frame", timeout: float = 5.0) -> str:
    """'frame': PCSX2's own screenshot hotkey (exact rendered frame, lands in snaps/).
    'window': grab the window rectangle from the screen with Pillow (includes Qt chrome)."""
    if mode == "window":
        from PIL import ImageGrab

        hwnd = find_window(cfg)
        if hwnd is None:
            raise RuntimeError("PCSX2 window not found")
        focus_window(hwnd)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
        cfg.snaps.mkdir(exist_ok=True)
        path = cfg.snaps / f"window_{int(time.time())}.png"
        img.save(path)
        return str(path)
    cfg.snaps.mkdir(exist_ok=True)
    before = {p: p.stat().st_mtime for p in cfg.snaps.glob("*.png")}
    press(cfg, ["screenshot"], hold_ms=60)
    end = time.time() + timeout
    while time.time() < end:
        for p in cfg.snaps.glob("*.png"):
            if p not in before or p.stat().st_mtime > before[p]:
                # wait until the file stops growing
                size = -1
                while p.stat().st_size != size:
                    size = p.stat().st_size
                    time.sleep(0.1)
                return str(p)
        time.sleep(0.1)
    raise TimeoutError("no screenshot appeared in snaps/ (is the game running and the window focused?)")


# -- save states as memory dumps ----------------------------------------------------------------


def _newest_state(cfg: Config, slot: int, after: float) -> Path | None:
    cands = [p for p in cfg.sstates.glob(f"*.{slot:02d}.p2s") if p.stat().st_mtime >= after]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def save_state_file(cfg: Config, pine: Pine, slot: int, timeout: float = 20.0) -> Path:
    """Save to `slot` over PINE and return the .p2s once it is fully written."""
    cfg.sstates.mkdir(exist_ok=True)
    t0 = time.time() - 1.0
    pine.save_state(slot)
    end = time.time() + timeout
    while time.time() < end:
        p = _newest_state(cfg, slot, t0)
        if p is not None:
            size = -1
            stable = 0
            while stable < 3:
                s = p.stat().st_size
                stable = stable + 1 if s == size and s > 0 else 0
                size = s
                time.sleep(0.15)
            try:
                with zipfile.ZipFile(p) as z:
                    z.getinfo("eeMemory.bin")
                return p
            except (zipfile.BadZipFile, KeyError):
                pass
        time.sleep(0.2)
    raise TimeoutError(f"save state slot {slot} did not appear in {cfg.sstates}")


def ee_memory_from_state(path: Path) -> bytes:
    """eeMemory.bin from a .p2s (stored/deflate via zipfile; zstd entries via the zstandard package)."""
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("eeMemory.bin")
        if info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            data = z.read(info)
        elif info.compress_type == 93:  # zstd
            import zstandard

            with open(path, "rb") as f:
                f.seek(info.header_offset)
                # local file header: 30 bytes + name + extra
                hdr = f.read(30)
                name_len, extra_len = struct.unpack("<HH", hdr[26:30])
                f.seek(info.header_offset + 30 + name_len + extra_len)
                raw = f.read(info.compress_size)
            data = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
        else:
            raise ValueError(f"unsupported compression {info.compress_type} in {path}")
    if len(data) < EE_RAM_SIZE:
        raise ValueError(f"eeMemory.bin is {len(data)} bytes, expected {EE_RAM_SIZE}")
    return data[:EE_RAM_SIZE]


# -- memory search -----------------------------------------------------------------------------

_DTYPES = {8: np.uint8, 16: np.uint16, 32: np.uint32}
_SIGNED = {8: np.int8, 16: np.int16, 32: np.int32}


@dataclass
class Search:
    width: int = 32
    signed: bool = False
    aligned: bool = True
    offsets: np.ndarray | None = None  # candidate byte offsets
    prev: np.ndarray | None = None  # values at those offsets in the previous snapshot
    snapshots: list[tuple[str, bytes]] = field(default_factory=list)  # (label, ram)
    history: list[str] = field(default_factory=list)

    def _view(self, ram: bytes) -> np.ndarray:
        dt = _SIGNED[self.width] if self.signed else _DTYPES[self.width]
        if self.aligned:
            return np.frombuffer(ram, dtype=dt)
        # unaligned: build overlapping windows via stride tricks on the byte view
        b = np.frombuffer(ram, dtype=np.uint8)
        n = self.width // 8
        win = np.lib.stride_tricks.sliding_window_view(b, n)
        return win.view(dt).reshape(-1)

    def _values(self, ram: bytes, offsets: np.ndarray) -> np.ndarray:
        v = self._view(ram)
        idx = offsets // (self.width // 8) if self.aligned else offsets
        return v[idx]

    def start(self, ram: bytes, label: str, value: int | None = None) -> int:
        v = self._view(ram)
        step = self.width // 8 if self.aligned else 1
        if value is None:
            idx = np.arange(v.size, dtype=np.int64)
        else:
            idx = np.flatnonzero(v == np.array(value).astype(v.dtype))
        self.offsets = idx * step
        self.prev = v[idx].copy()
        self.snapshots = [(label, ram)]
        self.history = [f"start width={self.width} value={value} -> {self.offsets.size} candidates"]
        return int(self.offsets.size)

    def filter(self, ram: bytes, label: str, op: str, value: int | None = None) -> int:
        if self.offsets is None:
            raise RuntimeError("no search in progress; call start first")
        cur = self._values(ram, self.offsets)
        prev = self.prev
        assert prev is not None
        ops = {
            "eq": lambda: cur == value, "ne": lambda: cur != value, "gt": lambda: cur > value, "lt": lambda: cur < value,
            "ge": lambda: cur >= value, "le": lambda: cur <= value,
            "changed": lambda: cur != prev, "unchanged": lambda: cur == prev,
            "increased": lambda: cur > prev, "decreased": lambda: cur < prev,
            "increased_by": lambda: cur.astype(np.int64) - prev.astype(np.int64) == value,
            "decreased_by": lambda: prev.astype(np.int64) - cur.astype(np.int64) == value,
        }
        if op not in ops:
            raise ValueError(f"unknown op {op!r}; one of {sorted(ops)}")
        if op in ("eq", "ne", "gt", "lt", "ge", "le", "increased_by", "decreased_by") and value is None:
            raise ValueError(f"op {op!r} needs a value")
        keep = ops[op]()
        self.offsets = self.offsets[keep]
        self.prev = cur[keep].copy()
        self.snapshots.append((label, ram))
        self.history.append(f"{op} {value if value is not None else ''} -> {self.offsets.size}")
        return int(self.offsets.size)

    def results(self, limit: int = 50) -> list[dict]:
        if self.offsets is None:
            return []
        out = []
        for i, off in enumerate(self.offsets[:limit]):
            row = {"addr": f"{int(off):08X}"}
            for label, ram in self.snapshots[-4:]:
                row[label] = int(self._values(ram, np.array([off]))[0])
            out.append(row)
        return out


# -- disassembly / cross references -------------------------------------------------------------


def disasm(ram: bytes, addr: int, count: int = 16) -> list[str]:
    from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS64, Cs

    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_LITTLE_ENDIAN)
    md.skipdata = True
    code = ram[addr : addr + 4 * count]
    return [f"{i.address:08X}  {int.from_bytes(ram[i.address:i.address+4], 'little'):08X}  {i.mnemonic} {i.op_str}" for i in md.disasm(code, addr)]


LOADSTORE = {0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu", 0x27: "lwu", 0x37: "ld", 0x28: "sb", 0x29: "sh", 0x2B: "sw", 0x3F: "sd", 0x09: "addiu", 0x0D: "ori"}


def find_refs(ram: bytes, target: int, code_lo: int = 0x100000, code_hi: int | None = None, window: int = 24) -> list[dict]:
    """Instructions that address `target` as lui(hi) + load/store/addiu(lo) pairs.
    Cheap static scan; misses register-relative accesses, but usually finds the owner."""
    hi = (target + 0x8000) >> 16
    lo = target - (hi << 16)
    lo_u = lo & 0xFFFF
    words = np.frombuffer(ram, dtype=np.uint32)
    end = words.size if code_hi is None else code_hi // 4
    start = code_lo // 4
    ops = words[start:end] >> 26
    lui = np.flatnonzero((ops == 0x0F) & (((words[start:end]) & 0xFFFF) == hi)) + start
    hits = []
    for li in lui:
        rt = (int(words[li]) >> 16) & 0x1F
        for j in range(li + 1, min(li + 1 + window, words.size)):
            w = int(words[j])
            op = w >> 26
            base = (w >> 21) & 0x1F
            if op in LOADSTORE and base == rt and (w & 0xFFFF) == lo_u:
                rt2 = (w >> 16) & 0x1F
                hits.append({"addr": f"{j*4:08X}", "instr": LOADSTORE[op], "word": f"{w:08X}", "lui_at": f"{li*4:08X}", "reg": f"${rt}", "dst": f"${rt2}"})
            # the register got overwritten: stop following this lui
            if op == 0x0F and ((w >> 16) & 0x1F) == rt:
                break
            if op in (0x09, 0x0D) and ((w >> 16) & 0x1F) == rt and (w & 0xFFFF) != lo_u:
                break
    return hits


# -- PNACH ----------------------------------------------------------------------------------------


def pnach_line(addr: int, value: int, width: int = 32, enabled: bool = True) -> str:
    prefix = {8: "0", 16: "1", 32: "2"}[width]
    line = f"patch=1,EE,{prefix}{addr & 0x0FFFFFFF:07X},extended,{value & 0xFFFFFFFF:08X}"
    return line if enabled else f"// {line}"


def write_pnach(path: Path, sections: dict[str, list[str]], gametitle: str | None = None, enabled: bool = True) -> str:
    """Write [Cheats/<name>] sections. Desktop: enabled lines apply. Thor bundle: pass enabled=False
    so every switch starts off, per the repo's cheat conventions."""
    out = []
    if gametitle:
        out.append(f"gametitle={gametitle}")
        out.append("")
    for name, lines in sections.items():
        out.append(f"[Cheats/{name}]")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("//"):
                line = line[2:].strip()
            out.append(line if enabled else f"// {line}")
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")
    return str(path)


def read_log(cfg: Config, lines: int = 100, contains: str | None = None) -> list[str]:
    if not cfg.logs.exists():
        return []
    text = cfg.logs.read_text(encoding="utf-8", errors="replace").splitlines()
    if contains:
        text = [t for t in text if contains.lower() in t.lower()]
    return text[-lines:]


def game_crc_from_log(cfg: Config) -> str | None:
    for line in reversed(read_log(cfg, 5000, "Game CRC")):
        m = re.search(r"Game CRC = ([0-9A-Fa-f]{8})", line)
        if m:
            return m.group(1).upper()
    return None
