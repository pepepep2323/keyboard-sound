"""Renders the overlay pictures for the web page (docs/*.png) with the app's own drawing code.
Run: python make_preview.py"""

import ctypes
import os
import struct
import time
import zlib

import keyboard_sound as ks

HERE = os.path.dirname(os.path.abspath(__file__))
ks.save_settings = lambda cfg: None      # never touch the real settings file


def png_rgba(width, height, rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


def snapshot(ov, path):
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
    with open(path, "wb") as f:
        f.write(png_rgba(w, h, bytes(raw)))
    print(f"{path} ({w}x{h})")


def render(name, effect="wave", color="rainbow", held=(), released=(), ripple_from=None, ripple_age=0.3,
           breathe_phase=None):
    cfg = dict(ks.DEFAULTS, key_size=int(40 / (ctypes.windll.user32.GetDpiForSystem() / 96)),
               x=-4000, y=-4000, welcomed=True, effect=effect, color=color)
    ov = ks.Overlay(cfg)
    now = time.monotonic()
    if breathe_phase is not None:                 # freeze the breathing at a nice moment
        ov.idle_level = lambda _now, p=breathe_phase: p
    for kid in held:
        ov.held.add(kid)
        ov.active.update(ov.by_id.get(kid, ()))
    for kid, ago in released:
        ov.released_at[kid] = now - ago
        ov.active.update(ov.by_id.get(kid, ()))
    if ripple_from is not None:
        idx = ov.by_id[ripple_from][0]
        key = ov.keys[idx]
        ov.ripples = [(key["cpx"], key["cpy"], now - ripple_age)]
        ov.released_at[ripple_from] = now - ripple_age
        ov.active.update(ov.by_id[ripple_from])
    ov.render(force_base=True)
    snapshot(ov, os.path.join(HERE, "docs", name))
    ov.close()


if __name__ == "__main__":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    S, V, M = ks.S, ks.V, ks.M
    gaming = dict(held=(S(0x11), S(0x1E), V(0xA0), M("L")), released=((S(0x39), 0.15), (M("WU"), 0.25)))
    render("preview.png", **gaming)
    render("fx-wave.png", **gaming)
    render("fx-static.png", color="cyan", **gaming)
    render("fx-breathe.png", effect="breathe", color="purple", breathe_phase=0.6, **gaming)
    render("fx-ripple.png", effect="ripple", ripple_from=S(0x22), ripple_age=0.35)
    render("fx-reactive.png", effect="reactive", color="red", **gaming)
