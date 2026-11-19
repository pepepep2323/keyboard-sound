"""Draws icon.ico (an RGB keyboard) - no extra packages needed. Run: python make_icon.py"""

import colorsys
import struct
import zlib

SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # supersampling for smooth edges


def in_round_rect(x, y, x1, y1, x2, y2, r):
    if not (x1 <= x <= x2 and y1 <= y <= y2):
        return False
    cx = min(max(x, x1 + r), x2 - r)
    cy = min(max(y, y1 + r), y2 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


# keyboard drawn in a 0..1 square
BODY = (0.02, 0.17, 0.98, 0.83, 0.12)
KEYS = []
rows = [(0.25, [1] * 6), (0.42, [1] * 6), (0.59, [1, 4, 1])]
for y, widths in rows:
    unit = (0.86 - 0.025 * (sum(widths) - 1)) / sum(widths)
    x = 0.07
    for w in widths:
        key_w = unit * w + 0.025 * (w - 1)
        KEYS.append((x, y, x + key_w, y + 0.14))
        x += key_w + 0.025
GLOW = 1  # this key is "pressed" (white)


def color_at(x, y):
    for i, (x1, y1, x2, y2) in enumerate(KEYS):
        if in_round_rect(x, y, x1, y1, x2, y2, 0.03):
            if i == GLOW:
                return (255, 255, 255, 255)
            r, g, b = colorsys.hsv_to_rgb((x * 0.85) % 1.0, 0.85, 1.0)
            return (int(r * 255), int(g * 255), int(b * 255), 255)
    if in_round_rect(x, y, *BODY):
        return (24, 24, 32, 255)
    return (0, 0, 0, 0)


def render(size):
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    c = color_at((px + (sx + 0.5) / SS) / size, (py + (sy + 0.5) / SS) / size)
                    a = c[3]
                    acc[0] += c[0] * a
                    acc[1] += c[1] * a
                    acc[2] += c[2] * a
                    acc[3] += a
            a = acc[3]
            if a:
                row += bytes((acc[0] // a, acc[1] // a, acc[2] // a, a // (SS * SS)))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main():
    images = [render(s) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, img in zip(SIZES, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(img), offset + len(data))
        data += img
    with open("icon.ico", "wb") as f:
        f.write(header + entries + data)
    with open("icon_preview.png", "wb") as f:
        f.write(images[-1])
    print("icon.ico written")


if __name__ == "__main__":
    main()
