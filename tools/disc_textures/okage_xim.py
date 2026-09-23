"""Extract every texture from an Okage: Shadow King disc image (SCUS-97129) to PNG.

    python okage_xim.py okage.iso OUT_DIR

No emulator, no gameplay: the textures are read straight off the disc. Needs pycdlib, numpy and
pillow. A CHD image first goes through MAME's chdman (`chdman extractcd`) and a 2352 -> 2048
sector strip (user data of each MODE2 sector starts at byte 24); see docs/games/okage.md.

The disc's formats (worked out 2026-09-22):

- `.XPF` - archive. Header `XPFX`, u32 data offset, u32 entry count, u32 pad; then 32-byte entries
  (name[24], u32 offset from the data offset, u32 compressed length). Every entry is compressed.
- Compression - bit-flag LZ. Byte 0 is zero, bytes 1..3 the decoded size big-endian, byte 4 the
  first flag byte, data from byte 5. Flag bits MSB first: 0 = literal byte; 1 = reference, whose
  next bit picks a one-byte distance (-256..-1; a zero byte ends the stream) or a long one (that
  byte plus four more flag bits, minus 0xFF); then a gamma-coded length n, copying n + 1 bytes.
  The scheme was documented by simontime/xpftool; this is an independent implementation.
- `.XIM` - image, inside XPF archives or standalone. u32 at 0: a GS TEX0 value (bits 20-25 are the
  PSM: 0x13 PSMT8, 0x14 PSMT4, 0x01 PSMCT24 and friends). True-colour images have no palette
  block: the image block below starts at 0x10 (PSMCT24 is packed RGB). Indexed ones have, at 0x10,
  the palette block: u32 block size including its 16-byte
  header, u32 0, u32 palette count, u32 entry count, then RGBA entries in index order (not CSM1
  swizzled), alpha on the PS2 scale (0x80 = opaque). Then the image block: u32 block size including
  its 16-byte header (in some files the width instead), u32 0, u32 height, u32 width, then linear
  indices (PSMT4: low nibble first).

Output: one PNG per unique texture (per palette when an XIM carries several), named
`<sha1 of the decoded XIM, 12 hex>[_p<N>].png`, RGBA with alpha scaled to 0..255, plus
`manifest.json` mapping each PNG to its size, PSM, the raw PS2 palette alpha range and every disc
path it appears under.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
from pathlib import Path


def lz_decode(src: bytes) -> bytes:
    size = int.from_bytes(src[1:4], "big")
    out = bytearray()
    pos = 5
    flag = src[4]
    mask = 0x80

    def bit() -> int:
        nonlocal flag, mask, pos
        if mask == 0:
            flag = src[pos]
            pos += 1
            mask = 0x80
        b = flag & mask
        mask >>= 1
        return 1 if b else 0

    while True:
        while bit() == 0:
            out.append(src[pos])
            pos += 1
        long_ref = bit()
        ch = src[pos]
        pos += 1
        if not long_ref:
            if ch == 0:
                break
            distance = ch - 256
        else:
            v = ch - 256
            for _ in range(4):
                v = (v << 1) | bit()
            distance = v - 0xFF
        n = 1
        while bit():
            n = (n << 1) | bit()
        start = len(out) + distance
        for i in range(n + 1):  # byte by byte: a reference may overlap what it is producing
            out.append(out[start + i])
    return bytes(out[:size])


def xpf_entries(data: bytes):
    magic, data_off, count, _ = struct.unpack_from("<4sIII", data, 0)
    if magic != b"XPFX":
        raise ValueError("not an XPF archive")
    for i in range(count):
        name, off, length = struct.unpack_from("<24sII", data, 16 + 32 * i)
        yield name.split(b"\0")[0].decode("ascii", "replace"), data[data_off + off : data_off + off + length]


def decode_xim(d: bytes):
    """Yield (palette_index, RGBA ndarray, info) for each palette of an XIM."""
    import numpy as np

    tex0 = struct.unpack_from("<I", d, 0)[0]
    psm = (tex0 >> 20) & 0x3F
    if psm in (0x00, 0x01, 0x02):
        # True colour: no palette block, the image block starts at 0x10.
        _, _, h, w = struct.unpack_from("<IIII", d, 0x10)
        raw = np.frombuffer(d, np.uint8, len(d) - 0x20, 0x20)
        if psm == 0x00:  # PSMCT32, alpha on the PS2 scale
            rgba = raw[: w * h * 4].reshape(h, w, 4).astype(np.uint16)
        elif psm == 0x01:  # PSMCT24, alpha comes from TEXA at draw time; opaque here
            rgb = raw[: w * h * 3].reshape(h, w, 3)
            rgba = np.concatenate([rgb, np.full((h, w, 1), 0x80, np.uint8)], axis=2).astype(np.uint16)
        else:  # PSMCT16: 5-5-5-1
            v = raw[: w * h * 2].view("<u2").reshape(h, w).astype(np.uint16)
            rgba = np.stack([(v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3, (v >> 15) * 0x80], axis=2)
        alpha = rgba[..., 3]
        info = {"psm": f"0x{psm:02X}", "width": int(w), "height": int(h), "palettes": 1,
                "ps2_alpha_min": int(alpha.min()), "ps2_alpha_max": int(alpha.max())}
        rgba[..., 3] = np.minimum(alpha * 2, 255)
        yield 0, rgba.astype(np.uint8), info
        return
    if psm not in (0x13, 0x14):
        raise ValueError(f"unhandled PSM 0x{psm:02X}")
    pal_size, _, pal_count, entries = struct.unpack_from("<IIII", d, 0x10)
    img_off = 0x10 + pal_size
    # The block's first field is its size in most files and the width in some (an older build
    # of the game's tools?); height and width are right in both, so size the data from them.
    _, _, h, w = struct.unpack_from("<IIII", d, img_off)
    px = np.frombuffer(d, np.uint8, len(d) - img_off - 0x10, img_off + 0x10)
    if psm == 0x14:
        idx = np.empty(px.size * 2, np.uint8)
        idx[0::2] = px & 0x0F
        idx[1::2] = px >> 4
        px = idx
    px = px[: w * h]
    pal_count = max(pal_count, 1)
    for p in range(pal_count):
        pal = np.frombuffer(d, np.uint8, entries * 4, 0x20 + p * entries * 4).reshape(entries, 4)
        rgba = pal[px].reshape(h, w, 4).astype(np.uint16)
        alpha = rgba[..., 3]
        info = {"psm": f"0x{psm:02X}", "width": int(w), "height": int(h), "palettes": int(pal_count),
                "ps2_alpha_min": int(alpha.min()), "ps2_alpha_max": int(alpha.max())}
        rgba[..., 3] = np.minimum(alpha * 2, 255)  # PS2 0x80 is opaque
        yield p, rgba.astype(np.uint8), info


def main() -> None:
    import pycdlib
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("iso", type=Path)
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    iso = pycdlib.PyCdlib()
    iso.open(str(a.iso))

    def read(path: str) -> bytes:
        b = io.BytesIO()
        iso.get_file_from_iso_fp(b, iso_path=path)
        return b.getvalue()

    manifest: dict[str, dict] = {}
    seen: dict[bytes, str] = {}  # compressed/raw bytes digest -> decoded key
    failures = []

    def take(where: str, blob: bytes, compressed: bool) -> None:
        digest = hashlib.sha1(blob).digest()
        if digest in seen:
            for name in manifest:
                if name.startswith(seen[digest]):
                    manifest[name]["paths"].append(where)
            return
        try:
            d = lz_decode(blob) if compressed else blob
            key = hashlib.sha1(d).hexdigest()[:12]
            seen[digest] = key
            for p, rgba, info in decode_xim(d):
                name = f"{key}.png" if info["palettes"] == 1 else f"{key}_p{p}.png"
                if name not in manifest:
                    Image.fromarray(rgba, "RGBA").save(a.out / name)
                    manifest[name] = {**info, "paths": []}
                manifest[name]["paths"].append(where)
        except Exception as e:  # keep going; the manifest lists what could not be read
            failures.append({"path": where, "error": str(e)})

    for root, _dirs, files in iso.walk(iso_path="/"):
        for f in files:
            path = root.rstrip("/") + "/" + f
            upper = f.upper()
            if ".XIM" in upper:
                take(path.split(";")[0], read(path), compressed=False)
            elif ".XPF" in upper:
                for name, blob in xpf_entries(read(path)):
                    if name.lower().endswith(".xim"):
                        take(path.split(";")[0] + "/" + name, blob, compressed=True)
    iso.close()

    (a.out / "manifest.json").write_text(json.dumps({"textures": manifest, "failures": failures}, indent=1))
    print(f"{len(manifest)} textures written to {a.out}, {len(failures)} failures")


if __name__ == "__main__":
    main()
