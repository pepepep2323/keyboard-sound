"""Renders the widget pictures for the web page (docs/*.png) with the app's own drawing code.
Run: python make_preview.py"""

import ctypes
import os
import struct
import tempfile
import time
import zlib
from collections import deque

import keyboard_sound as ks

HERE = os.path.dirname(os.path.abspath(__file__))
ks.save_settings = lambda cfg: None      # never touch the real settings file


def png_rgba(width, height, rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


def snapshot(w, name, height=None):
    """Saves a widget's current picture as a PNG with transparency (optionally only the top `height` rows)."""
    rows = bytearray()
    out_h = min(w.h, height or w.h)
    if w.argb:
        data = ctypes.string_at(w.canvas.bits, w.w * w.h * 4)
        for y in range(out_h):
            rows.append(0)
            line = data[y * w.w * 4:(y + 1) * w.w * 4]
            for x in range(0, w.w * 4, 4):
                b, g, r, a = line[x], line[x + 1], line[x + 2], line[x + 3]
                if a:
                    r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
                rows += bytes((r, g, b, a))
    else:
        buf = ctypes.create_string_buffer(w.w * w.h * 4)
        ctypes.windll.gdi32.GetBitmapBits(ctypes.c_void_p(w.buf_bmp), len(buf), buf)
        for y in range(w.h):
            rows.append(0)
            line = buf.raw[y * w.w * 4:(y + 1) * w.w * 4]
            for x in range(0, w.w * 4, 4):
                b, g, r = line[x], line[x + 1], line[x + 2]
                rows += bytes((r, g, b, 0 if (r, g, b) == (1, 2, 3) else 255))
    path = os.path.join(HERE, "docs", name)
    with open(path, "wb") as f:
        f.write(png_rgba(w.w, out_h, bytes(rows)))
    print(f"{path} ({w.w}x{out_h})")


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    ctypes.windll.ole32.CoInitializeEx(None, 2)
    ks.DATA_DIR = tempfile.mkdtemp()
    ks.SETTINGS_FILE = os.path.join(ks.DATA_DIR, "overlay_settings.json")
    cfg = ks.load_settings()
    size = int(40 / (ctypes.windll.user32.GetDpiForSystem() / 96))
    for i, w in enumerate(cfg["widgets"].values()):
        w.update(size=size, x=-6000 + i * 10, y=-6000)
    cfg["welcomed"] = True
    cfg["widgets"]["text1"] = {"type": "text", "x": -6000, "y": -6000, "size": size, "visible": True,
                               "text": "GG WP!", "color": "effect", "bold": True, "background": False}
    app = ks.App(cfg)
    S, V, M = ks.S, ks.V, ks.M
    kb, mouse = app.widgets["keyboard"], app.widgets["mouse"]
    now = time.monotonic()

    def press(widget, held=(), released=()):
        widget.held, widget.released_at, widget.active, widget.ripples = set(held), {}, set(), []
        for kid in held:
            widget.active.update(widget.by_id.get(kid, ()))
        for kid, ago in released:
            widget.released_at[kid] = now - ago
            widget.active.update(widget.by_id.get(kid, ()))

    gaming = dict(held=(S(0x11), S(0x1E), V(0xA0)), released=((S(0x39), 0.15),))
    for name, effect, color in (("fx-wave.png", "wave", "rainbow"), ("fx-static.png", "static", "cyan"),
                                ("fx-breathe.png", "breathe", "purple"), ("fx-ripple.png", "ripple", "rainbow"),
                                ("fx-reactive.png", "reactive", "red")):
        cfg["effect"], cfg["color"] = effect, color
        press(kb, **({} if effect == "ripple" else gaming))
        if effect == "ripple":
            g = kb.keys[kb.by_id[S(0x22)][0]]
            kb.ripples = [(g["cpx"], g["cpy"], time.monotonic() - 0.35)]
            kb.released_at[S(0x22)] = time.monotonic() - 0.35
            kb.active.update(kb.by_id[S(0x22)])
        if effect == "breathe":
            app.idle_level = lambda _now: 0.6
        kb.redraw()
        snapshot(kb, name)
        app.idle_level = ks.App.idle_level.__get__(app)
    cfg["effect"], cfg["color"] = "wave", "rainbow"
    press(kb, **gaming)
    kb.redraw()
    snapshot(kb, "preview.png")
    press(mouse, held=(M("L"),), released=((M("WU"), 0.2),))
    mouse.redraw()
    snapshot(mouse, "mouse.png")

    with open(os.path.join(HERE, "docs", "icon.png"), "rb") as f:
        cover = f.read()
    player = app.player
    player.set_state({"app": "Spotify.exe", "title": "Your Favourite Song", "artist": "Some Artist",
                      "playing": True, "pos": 83.0, "end": 215.0, "t": time.monotonic(), "cover": cover,
                      "cover_key": "demo", "can_seek": True, "volume": 0.7, "muted": False, "ad": False})
    player.redraw()
    snapshot(player, "player.png")

    st = app.stats
    st.data.update(keys=12431, clicks=3209, best_cps=14, cat=48210)
    t = time.monotonic()
    st.clicks = deque([t - 0.1 * i for i in range(9)])
    st.actions = deque([t - 0.25 * i for i in range(214)])
    st.started = t - 120
    app.widgets["stats"].redraw()
    snapshot(app.widgets["stats"], "stats.png")

    cat = app.widgets["cat"]
    cat.paw_until["left"] = time.monotonic() + 5
    cat.redraw()
    snapshot(cat, "cat.png")
    app.widgets["text1"].redraw()
    snapshot(app.widgets["text1"], "text.png")

    for style in ("pastel", "black"):                        # the other keyboard looks
        kb.wcfg["style"] = style
        kb.build()
        press(kb, **gaming)
        kb.redraw()
        snapshot(kb, f"kb-{style}.png")
    kb.wcfg["style"] = "rgb"

    app.session_start = time.monotonic() - 5025
    app.game_since = {"rocketleague.exe": time.monotonic() - 2534}
    app.widgets["session"].redraw()
    snapshot(app.widgets["session"], "session.png")

    st.data.update(rl_w=3, rl_l=1, gd_jumps=1287, gd_total=48211)
    rocket = app.widgets["rocket"]
    rocket.on_rl("Connected", None)
    me = {"Name": "Player", "Shortcut": 1, "TeamNum": 0, "Boost": 67, "Goals": 1, "Shots": 3, "Assists": 1,
          "Saves": 2, "Speed": 1800}
    rocket.on_rl("UpdateState", {"Players": [me], "Game": {
        "Teams": [{"TeamNum": 0, "Score": 2}, {"TeamNum": 1, "Score": 1}], "TimeSeconds": 162,
        "bOvertime": False, "Ball": {"Speed": 2417}, "bHasTarget": True, "Target": me}})
    rocket.redraw()
    snapshot(rocket, "rocket.png")

    sb = app.widgets["scoreboard"]
    sb.wcfg["size"] = size
    sb.build()
    sb.on_rl("UpdateState", {"Game": {"Teams": [{"TeamNum": 0, "Score": 2}, {"TeamNum": 1, "Score": 1}],
                                      "TimeSeconds": 167, "bOvertime": False}})
    for mode in ks.SB_MODES:
        sb.wcfg["mode"] = mode
        sb.redraw()
        snapshot(sb, f"score-{mode}.png", int(sb.H + 0.3 * sb.u))
    sb.wcfg["mode"] = "enhanced"

    bm = app.widgets["boost"]
    bm.wcfg["size"] = size
    bm.build()
    me = {"Name": "Player", "Shortcut": 1, "TeamNum": 0, "Boost": 67}
    bm.on_rl("UpdateState", {"Players": [me], "Game": {"Teams": [{"TeamNum": 0, "Score": 2, "ColorPrimary": "1873FF"},
             {"TeamNum": 1, "Score": 1}], "bHasTarget": True, "Target": me}})
    for mode in ks.SB_MODES:
        bm.wcfg["mode"] = mode
        bm.shown_boost, bm.last_draw = 67, 0.0
        bm.redraw()
        snapshot(bm, f"boost-{mode}.png")

    clock = app.widgets["clock"]
    clock.redraw()
    snapshot(clock, "clock.png")

    app.running = app.running | {ks.FORTNITE_EXE}
    st.data.update(fnm_count=4, fnm_time=4 * 812, fnm_best=1102, drinks=3, fn_match=5, fn_today=17, fn_total=203,
                   fn_best=9)
    fm = app.widgets["fnmatch"]
    fm.phase, fm.bus_at = "game", time.time() - 754
    fm.redraw()
    snapshot(fm, "fnmatch.png")
    drink = app.widgets["drink"]
    drink.left = 11 * 60
    drink.redraw()
    snapshot(drink, "drink.png")
    chests = app.widgets["chests"]
    chests.redraw()
    snapshot(chests, "chests.png")
    for theme in ("pastel", "neon"):                       # the same widget in two other themes
        cfg["theme"] = theme
        app.__dict__.pop("_themes", None)
        app.widgets["stats"].redraw()
        snapshot(app.widgets["stats"], f"theme-{theme}.png")
    cfg["theme"] = "classic"
    app.__dict__.pop("_themes", None)

    dash = app.widgets["dash"]
    app.gd_seconds = 754
    app.running = app.running | {"geometrydash.exe"}
    dash.jumped_at = time.monotonic() - 0.15
    dash.redraw()
    snapshot(dash, "dash.png")
    app.close()


if __name__ == "__main__":
    main()
