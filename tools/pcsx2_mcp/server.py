"""MCP server (stdio) and CLI for driving desktop PCSX2 while authoring cheats.

    python server.py                 # MCP over stdio (what .mcp.json runs)
    python server.py cli status      # same tools from a shell
    python server.py cli press cross --hold-ms 100
    python server.py cli search_start --width 16
    python server.py cli search_filter decreased

Every tool is a thin wrapper over pcsx2ctl.py, so the workflow is scriptable without MCP.
"""

from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pcsx2ctl as ctl  # noqa: E402
from pine import Pine, PineError  # noqa: E402

CFG = ctl.load_config()
SNAPSHOT_SLOT = 10  # save-state slot used for memory snapshots; keep your own states in 1-9
_last_game: str | None = None
_search = ctl.Search()
_snapshots: dict[str, bytes] = {}
_latest_ram: bytes | None = None


def _pine() -> Pine:
    return Pine(port=CFG.pine_port).connect()


def _snapshot(label: str) -> bytes:
    global _latest_ram
    with _pine() as p:
        path = ctl.save_state_file(CFG, p, SNAPSHOT_SLOT)
    ram = ctl.ee_memory_from_state(path)
    _snapshots[label] = ram
    _latest_ram = ram
    return ram


def _ram(snapshot: str | None) -> bytes:
    if snapshot:
        return _snapshots[snapshot]
    if _latest_ram is None:
        return _snapshot(f"auto_{int(time.time())}")
    return _latest_ram


# -- tools (plain functions; registered below for MCP and CLI) ---------------------------------


def status() -> dict:
    """Emulator state: PINE reachability, VM status, title/serial/CRC, window, last launched game."""
    out: dict[str, Any] = {"pcsx2_dir": str(CFG.pcsx2_dir), "window": None, "pine": False}
    hwnd = ctl.find_window(CFG)
    if hwnd:
        out["window"] = ctl.window_title(hwnd)
    try:
        with _pine() as p:
            out["pine"] = True
            out["version"] = p.version()
            out["vm"] = p.status()
            try:
                out["title"], out["serial"], out["crc"] = p.title(), p.serial(), p.uuid().upper()
            except PineError:
                out["vm"] = "no game"
    except OSError as e:
        out["pine_error"] = str(e)
    out["search"] = _search.history[-1] if _search.history else None
    out["snapshots"] = list(_snapshots)
    return out


def launch(game: str, state_slot: int | None = None, fast_boot: bool = True, wait: bool = True) -> dict:
    """Start PCSX2 with `game` (path, or substring of a file in the games folder). `state_slot` loads that state on boot."""
    global _last_game
    cmd = ctl.launch(CFG, game, state_slot, fast_boot)
    _last_game = game
    out = {"command": cmd}
    if wait:
        ctl.wait_for_vm(CFG)
        out.update(status())
    return out


def quit() -> dict:
    """Close PCSX2 (WM_CLOSE, then taskkill after 15 s)."""
    return {"closed": ctl.quit_pcsx2(CFG)}


def restart(state_slot: int | None = None) -> dict:
    """Quit and relaunch the last game, optionally into a save-state slot. This is how PNACH edits get reloaded."""
    if _last_game is None:
        raise RuntimeError("no game launched from this server yet; use launch(game)")
    ctl.quit_pcsx2(CFG)
    time.sleep(1.0)
    return launch(_last_game, state_slot)


def save_state(slot: int) -> dict:
    """Save to slot 1-9 (10 is reserved for memory snapshots)."""
    with _pine() as p:
        path = ctl.save_state_file(CFG, p, slot)
    return {"path": str(path)}


def load_state(slot: int) -> dict:
    """Load save-state slot."""
    with _pine() as p:
        p.load_state(slot)
    time.sleep(0.5)
    return status()


def toggle_pause() -> dict:
    """Pause/resume via the Space hotkey (PINE has no pause command); returns the new state."""
    ctl.press(CFG, ["pause"], hold_ms=60)
    time.sleep(0.3)
    return status()


def screenshot(mode: str = "frame") -> dict:
    """Capture the current frame. mode='frame' uses PCSX2's F8 (exact render, snaps/); 'window' grabs the window from screen."""
    return {"path": ctl.screenshot(CFG, mode)}


def press(buttons: list[str] | str, hold_ms: int = 80, repeat: int = 1, gap_ms: int = 120) -> dict:
    """Press PS2 buttons together (names from [Pad1]: up/down/left/right/cross/circle/square/triangle/start/select/l1/r1/l2/r2/lup/ldown/lleft/lright...) or hotkeys (screenshot/save_state/load_state/pause). hold_ms is how long they stay down."""
    if isinstance(buttons, str):
        buttons = [b.strip() for b in buttons.replace("+", ",").split(",") if b.strip()]
    ctl.press(CFG, buttons, hold_ms, repeat, gap_ms)
    return {"pressed": buttons, "hold_ms": hold_ms, "repeat": repeat}


def hold(buttons: list[str] | str, ms: int) -> dict:
    """Hold buttons for `ms` milliseconds (walking: hold lup/ldown/lleft/lright or the d-pad)."""
    return press(buttons, hold_ms=ms)


def buttons() -> dict:
    """Button -> key bindings read from PCSX2.ini."""
    return ctl.pad_bindings(CFG)


def mem_read(addr: int | str, width: int = 32, count: int = 1) -> dict:
    """Read `count` values of `width` bits from EE RAM at addr (int or hex string) via PINE."""
    addr = _int(addr)
    with _pine() as p:
        if count == 1:
            v = p.read(addr, width)
            return {"addr": f"{addr:08X}", "value": v, "hex": f"{v:0{width // 4}X}"}
        step = width // 8
        vals = [p.read(addr + i * step, width) for i in range(count)]
    return {"addr": f"{addr:08X}", "values": vals, "hex": [f"{v:0{width // 4}X}" for v in vals]}


def mem_read_block(addr: int | str, length: int) -> dict:
    """Read a byte range (hex dump) via PINE."""
    addr = _int(addr)
    with _pine() as p:
        data = p.read_block(addr, length)
    rows = [f"{addr + i:08X}  {data[i:i+16].hex(' ')}" for i in range(0, len(data), 16)]
    return {"addr": f"{addr:08X}", "hexdump": rows}


def mem_write(addr: int | str, value: int | str, width: int = 32) -> dict:
    """Write one value into EE RAM via PINE (live poke; the recompiler picks code writes up)."""
    addr, value = _int(addr), _int(value)
    with _pine() as p:
        p.write(addr, value, width)
        back = p.read(addr, width)
    return {"addr": f"{addr:08X}", "written": value, "readback": back}


def snapshot(label: str) -> dict:
    """Save a state to the snapshot slot and keep its 32 MB EE RAM in memory under `label` (for diffing/disasm)."""
    ram = _snapshot(label)
    return {"label": label, "bytes": len(ram), "snapshots": list(_snapshots)}


def search_start(width: int = 32, value: int | str | None = None, signed: bool = False, aligned: bool = True, label: str | None = None) -> dict:
    """Begin a memory search: take a snapshot and seed candidates (all addresses, or those equal to `value`)."""
    global _search
    _search = ctl.Search(width=width, signed=signed, aligned=aligned)
    label = label or f"s0_{int(time.time())}"
    n = _search.start(_snapshot(label), label, None if value is None else _int(value))
    return {"candidates": n, "history": _search.history}


def search_filter(op: str, value: int | str | None = None, label: str | None = None) -> dict:
    """Take a new snapshot and keep candidates matching op: eq/ne/gt/lt/ge/le (vs value), changed/unchanged/increased/decreased (vs previous snapshot), increased_by/decreased_by (exact delta)."""
    label = label or f"s{len(_search.snapshots)}_{int(time.time())}"
    n = _search.filter(_snapshot(label), label, op, None if value is None else _int(value))
    out = {"candidates": n, "history": _search.history}
    if n <= 50:
        out["results"] = _search.results(50)
    return out


def search_results(limit: int = 50) -> dict:
    """Current candidates with their values in the last few snapshots."""
    return {"candidates": int(_search.offsets.size) if _search.offsets is not None else 0, "results": _search.results(limit), "history": _search.history}


def compare_snapshots(a: str, b: str, width: int = 32, limit: int = 200, region: str | None = None) -> dict:
    """Addresses whose value differs between two named snapshots (optionally within 'lo-hi' hex region)."""
    import numpy as np

    ra, rb = _snapshots[a], _snapshots[b]
    lo, hi = 0, len(ra)
    if region:
        lo, hi = (int(x, 16) for x in region.split("-"))
    dt = {8: np.uint8, 16: np.uint16, 32: np.uint32}[width]
    va, vb = np.frombuffer(ra[lo:hi], dtype=dt), np.frombuffer(rb[lo:hi], dtype=dt)
    idx = np.flatnonzero(va != vb)
    step = width // 8
    return {"differences": int(idx.size), "results": [{"addr": f"{lo + int(i) * step:08X}", a: int(va[i]), b: int(vb[i])} for i in idx[:limit]]}


def disasm(addr: int | str, count: int = 16, snapshot: str | None = None) -> dict:
    """Disassemble MIPS code from the latest (or named) snapshot."""
    addr = _int(addr)
    return {"lines": ctl.disasm(_ram(snapshot), addr, count)}


def find_refs(addr: int | str, snapshot: str | None = None, code_lo: int | str = 0x100000, code_hi: int | str | None = None) -> dict:
    """Static scan for code that loads/stores `addr` through lui/lo pairs. Start here to find who writes a found variable."""
    return {"refs": ctl.find_refs(_ram(snapshot), _int(addr), _int(code_lo), None if code_hi is None else _int(code_hi))}


def pnach_write(sections: dict[str, list[str]] | str, target: str = "desktop", crc: str | None = None, gametitle: str | None = None) -> dict:
    """Write a PNACH. sections = {"No Encounters": ["patch=1,EE,201B9468,extended,00000000"]} (or JSON string).
    target: 'desktop' -> <pcsx2>/cheats/<CRC>.pnach with lines active (restart() to reload);
            'repo'    -> platforms/android/app/src/main/assets/cheats/<CRC>.pnach with lines commented (bundle convention);
            'thor'    -> adb push an active copy to /sdcard/armsxdata/cheats/<CRC>.pnach for a live test on the device."""
    if isinstance(sections, str):
        sections = json.loads(sections)
    crc = (crc or _crc()).upper()
    if target == "desktop":
        return {"path": ctl.write_pnach(CFG.cheats / f"{crc}.pnach", sections, gametitle, enabled=True), "note": "call restart(state_slot) so PCSX2 reloads patches"}
    if target == "repo":
        path = ctl.REPO / "platforms/android/app/src/main/assets/cheats" / f"{crc}.pnach"
        return {"path": ctl.write_pnach(path, sections, gametitle, enabled=False), "note": "add the CRC to assets/cheats/index.tsv"}
    if target == "thor":
        tmp = Path(ctl.HERE / "_thor.pnach")
        ctl.write_pnach(tmp, sections, gametitle, enabled=True)
        remote = f"/sdcard/armsxdata/cheats/{crc}.pnach"
        r = subprocess.run(["adb", "push", str(tmp), remote], capture_output=True, text=True)
        return {"path": remote, "adb": (r.stdout + r.stderr).strip()}
    raise ValueError("target must be desktop, repo or thor")


def pnach_line(addr: int | str, value: int | str, width: int = 32) -> dict:
    """Format one patch line (addr/value ints or hex strings)."""
    return {"line": ctl.pnach_line(_int(addr), _int(value), width)}


def log(lines: int = 100, contains: str | None = None) -> dict:
    """Tail PCSX2's emulog.txt, optionally filtered."""
    return {"lines": ctl.read_log(CFG, lines, contains)}


TOOLS = [status, launch, quit, restart, save_state, load_state, toggle_pause, screenshot, press, hold, buttons,
         mem_read, mem_read_block, mem_write, snapshot, search_start, search_filter, search_results, compare_snapshots,
         disasm, find_refs, pnach_write, pnach_line, log]


def _int(v: int | str) -> int:
    if isinstance(v, int):
        return v
    s = str(v).strip().lower()
    return int(s, 16) if s.startswith("0x") or any(c in "abcdef" for c in s) else int(s, 0)


def _crc() -> str:
    try:
        with _pine() as p:
            return p.uuid()
    except (OSError, PineError):
        crc = ctl.game_crc_from_log(CFG)
        if not crc:
            raise RuntimeError("no running game and no CRC in the log; pass crc=")
        return crc


# -- entry points ------------------------------------------------------------------------------


def run_mcp() -> None:
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    mcp = Server("pcsx2", instructions="Drive desktop PCSX2 for cheat authoring: launch, save/load states, screenshots, button presses, PINE memory read/write, snapshot-diff memory search, disassembly, PNACH output.")
    for fn in TOOLS:
        mcp.tool()(fn)
    mcp.run()


def run_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="server.py cli")
    sub = parser.add_subparsers(dest="tool", required=True)
    for fn in TOOLS:
        sp = sub.add_parser(fn.__name__, help=(fn.__doc__ or "").split("\n")[0])
        for name, param in inspect.signature(fn).parameters.items():
            flag = name if param.default is inspect._empty else f"--{name.replace('_', '-')}"
            kwargs: dict[str, Any] = {}
            if param.default is not inspect._empty:
                kwargs["default"] = param.default
            if param.annotation is bool or isinstance(param.default, bool):
                kwargs["type"] = lambda s: s.lower() in ("1", "true", "yes", "on")
            sp.add_argument(flag, **kwargs)
    ns = parser.parse_args(argv)
    fn = next(f for f in TOOLS if f.__name__ == ns.tool)
    kwargs = {k: v for k, v in vars(ns).items() if k != "tool"}
    for k, v in list(kwargs.items()):
        if isinstance(v, str) and v.isdigit() and inspect.signature(fn).parameters[k].annotation in (int, "int", "int | None", "int | str | None"):
            kwargs[k] = int(v)
    print(json.dumps(fn(**kwargs), indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        run_cli(sys.argv[2:])
    else:
        run_mcp()
