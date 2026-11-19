"""Renders docs/preview.png: the real overlay (same drawing code as the app) with a few keys lit.
Run: python make_preview.py"""

import ctypes
import os
import struct
import time
import zlib

import keyboard_sound as ks

ctypes.windll.shcore.SetProcessDpiAwareness(2)
cfg = dict(ks.DEFAULTS, key_size=int(40 / (ctypes.windll.user32.GetDpiForSystem() / 96)), x=-4000, y=-4000,
           welcomed=True)
ks.save_settings = lambda cfg: None      # never touch the real settings file
ov = ks.Overlay(cfg)

# keys that look like someone is playing: W + A held, Shift, Space, left mouse button, wheel
now = time.monotonic()
for kid in (ks.S(0x11), ks.S(0x1E), ks.V(0xA0), ks.M("L")):
    ov.held.add(kid)
for kid, ago in ((ks.S(0x39), 0.15), (ks.M("WU"), 0.25), (ks.S(0x12), 0.3)):
    ov.released_at[kid] = now - ago
for kid in list(ov.held) + list(ov.released_at):
    ov.active.update(ov.by_id.get(kid, ()))
ov.render(force_base=True)

w, h = ov.w, ov.h
buf = ctypes.create_string_buffer(w * h * 4)
ctypes.windll.gdi32.GetBitmapBits(ctypes.c_void_p(ov.bitmaps[1][1]), len(buf), buf)
raw = bytearray()
data = buf.raw
for y in range(h):
    raw.append(0)
    row = data[y * w * 4:(y + 1) * w * 4]
    for x in range(0, w * 4, 4):
        b, g, r = row[x], row[x + 1], row[x + 2]
        raw += bytes((r, g, b, 0 if (r, g, b) == (1, 2, 3) else 255))
ov.close()

def png_rgba(width, height, rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "preview.png")
with open(out, "wb") as f:
    f.write(png_rgba(w, h, bytes(raw)))
print(f"{out} ({w}x{h})")
