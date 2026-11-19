"""
Keyboard click sounds + RGB keyboard & mouse overlay for Windows.

Plays a click every time you press a key (anywhere in Windows) and shows a
small RGB keyboard + mouse on screen. Pressed keys / buttons light up and fade.
No extra packages needed - only Python's standard library.

Run:    python keyboard_sound.py   (or KeyboardSound.exe)
Tray:   right-click the tray icon for options
Move:   Ctrl+Alt+F11  -> drag the overlay, scroll to resize, right-click for options,
                         Ctrl+Alt+F11 again to lock it (position is saved)
Stop:   Ctrl+Alt+F12 anywhere, tray icon -> Quit, or Ctrl+C in the console

Build the .exe:  build.bat
"""

import array
import colorsys
import ctypes
import json
import os
import queue
import random
import signal
import sys
import threading
import time
import wave
import winreg
from ctypes import wintypes

# ---------------------------------------------------------------- settings
APP_NAME = "Keyboard Sound"
FROZEN = getattr(sys, "frozen", False)               # True when running as KeyboardSound.exe
HERE = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", HERE)             # where bundled files are inside the .exe
SOUND_FILE = os.path.join(RES_DIR, "click_loud.wav") # boosted version of click.mp3 (must be .wav)
ICON_FILE = os.path.join(RES_DIR, "icon.ico")
DATA_DIR = os.path.join(os.environ.get("APPDATA", HERE), "KeyboardSound") if FROZEN else HERE
SETTINGS_FILE = os.path.join(DATA_DIR, "overlay_settings.json")

VOLUME = 1.0           # 0.0 .. 1.0
VOICES = 8             # how many clicks can overlap when typing fast
PITCH_VARIATION = 0.04 # small random pitch change per click, sounds natural (0 = off)
MOUSE_SOUND = False    # also click on mouse buttons

# defaults for things you can change with Ctrl+Alt+F11 (saved to overlay_settings.json)
DEFAULTS = {
    "x": None, "y": None,        # None = use the corner below
    "corner": "bottom-left",     # "top-left", "top-right", "bottom-left", "bottom-right"
    "key_size": 26,              # pixels per key at 100% display scaling
    "numpad": True,
    "mouse": True,
    "mouse_side": "right",       # "right" or "left" of the keyboard
    "opacity": 0.9,
    "background": False,         # dark board behind the keys (False = see-through)
    "visible": True,             # overlay shown (tray icon -> Show keyboard overlay)
    "sound": True,               # click sound on (tray icon -> Click sound)
    "welcomed": False,           # first-start tip already shown
}
ISO_LAYOUT = True      # True = European keyboard (tall Enter), False = US

GLOW_TIME = 0.5        # seconds a key keeps glowing after you let go
RGB_SPEED = 0.15       # how fast the rainbow moves (0 = still)
RGB_SPREAD = 1.0       # how many rainbows fit across the overlay
IDLE_BRIGHTNESS = 0.55 # brightness of keys that are not pressed (0..1)
FPS = 30               # glow animation frame rate
RGB_FPS = 10           # rainbow update rate (lower = less CPU)

HOTKEY_EDIT = 0x57     # F11 (with Ctrl+Alt)
HOTKEY_QUIT = 0x58     # F12 (with Ctrl+Alt)

# ---------------------------------------------------------------- win32 setup
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")
LRESULT = ctypes.c_ssize_t
WM_QUIT, WM_INPUT = 0x0012, 0x00FF

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
                ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class _MOUSEBUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _MOUSEUNION(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("b", _MOUSEBUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _MOUSEUNION), ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class _RAWDATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWDATA)]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte),
                               wintypes.LPWSTR, ctypes.c_int, wintypes.UINT, wintypes.HKL]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
winmm.waveOutOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                              ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
winmm.waveOutPrepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveOutUnprepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveOutWrite.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveOutReset.argtypes = [wintypes.HANDLE]
winmm.waveOutClose.argtypes = [wintypes.HANDLE]


# ---------------------------------------------------------------- audio
play_requests = queue.Queue()


def load_variants():
    """Loads the click and makes a few slightly different versions so repeated
    clicks don't sound robotic / phasey when they overlap."""
    with wave.open(SOUND_FILE, "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        if width != 2:
            raise RuntimeError("click sound must be a 16-bit WAV")
        samples = array.array("h", w.readframes(w.getnframes()))
    frames = len(samples) // ch
    variants = []
    steps = [0.0] if PITCH_VARIATION <= 0 else [-1, -0.5, 0, 0.5, 1]
    for s in steps:
        speed = 1 + s * PITCH_VARIATION
        gain = VOLUME * (0.92 + 0.08 * random.random())
        n = int(frames / speed)
        out = array.array("h", bytes(n * ch * 2))
        for i in range(n):
            pos = i * speed
            j = int(pos)
            f = pos - j
            j2 = min(j + 1, frames - 1)
            for c in range(ch):
                v = samples[j * ch + c] * (1 - f) + samples[j2 * ch + c] * f
                out[i * ch + c] = max(-32768, min(32767, int(v * gain)))
        variants.append(out.tobytes())
    fmt = WAVEFORMATEX(1, ch, rate, rate * ch * 2, ch * 2, 16, 0)
    return fmt, variants


def audio_worker(ready):
    """Several waveOut streams = clicks overlap cleanly and start instantly."""
    try:
        fmt, variants = load_variants()
        buffers = [ctypes.create_string_buffer(v, len(v)) for v in variants]
        voices = []
        for _ in range(VOICES):
            h = wintypes.HANDLE()
            if winmm.waveOutOpen(ctypes.byref(h), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0):
                raise RuntimeError("could not open audio output")
            hdrs = []
            for b in buffers:
                hdr = WAVEHDR(ctypes.cast(b, ctypes.c_void_p), len(b.raw), 0, 0, 0, 0, None, 0)
                winmm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                hdrs.append(hdr)
            voices.append((h, hdrs))
    except Exception as e:
        ready.append(e)
        return
    ready.append(None)

    voice, last = 0, -1
    while True:
        if play_requests.get() is None:
            break
        h, hdrs = voices[voice]
        pick = random.randrange(len(hdrs))
        if pick == last and len(hdrs) > 1:       # never the same variant twice in a row
            pick = (pick + 1) % len(hdrs)
        last = pick
        winmm.waveOutReset(h)
        winmm.waveOutWrite(h, ctypes.byref(hdrs[pick]), ctypes.sizeof(WAVEHDR))
        voice = (voice + 1) % VOICES

    for h, hdrs in voices:
        winmm.waveOutReset(h)
        for hdr in hdrs:
            winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
        winmm.waveOutClose(h)


# ---------------------------------------------------------------- input (raw input, never slows down the game)
# Keys are identified by their physical position (scan code), so the overlay
# matches your real keyboard whatever layout (CZ, US, ...) you type with.
def S(scan):
    return ("sc", scan, 0)


def E(scan):
    return ("sc", scan, 1)


def V(vk):
    return ("vk", vk)


def M(name):
    return ("m", name)


CTRL_IDS = {V(0xA2), V(0xA3)}
ALT_IDS = {V(0xA4), V(0xA5)}

sound_enabled = True
ui_events = queue.Queue()     # ("down"|"up"|"tap", id) / ("edit"|"quit", None) / ("wheel", delta)
input_thread_id = None
held = set()

MOUSE_FLAGS = [(0x0001, 0x0002, M("L")), (0x0004, 0x0008, M("R")), (0x0010, 0x0020, M("M")),
               (0x0040, 0x0080, M("X1")), (0x0100, 0x0200, M("X2"))]


def raw_key_id(vk, make, e0):
    if vk == 0x10:
        return V(0xA1) if make == 0x36 else V(0xA0)
    if vk == 0x11:
        return V(0xA3) if e0 else V(0xA2)
    if vk == 0x12:
        return V(0xA5) if e0 else V(0xA4)
    if vk in (0x5B, 0x5C, 0x5D, 0x90, 0x13, 0x2C):
        return V(vk)
    return ("sc", make, 1 if e0 else 0)


def wake_overlay():
    if overlay_hwnd:
        user32.PostMessageW(overlay_hwnd, WM_APP, 0, 0)


def handle_keyboard(kb):
    vk, make, flags = kb.VKey, kb.MakeCode, kb.Flags
    e0 = flags & 2
    if vk == 0xFF or (make in (0x2A, 0x36) and e0):    # fake keys Windows adds internally
        return
    kid = raw_key_id(vk, make, e0)
    if flags & 1:                                        # key released
        if kid in held:
            held.discard(kid)
            ui_events.put(("up", kid))
            wake_overlay()
        return
    if kid in held:                                      # auto-repeat while held
        return
    held.add(kid)
    if sound_enabled:
        play_requests.put(1)
    ui_events.put(("down", kid))
    wake_overlay()
    if held & CTRL_IDS and held & ALT_IDS:
        if kid == S(HOTKEY_EDIT):
            ui_events.put(("edit", None))
        elif kid == S(HOTKEY_QUIT):
            ui_events.put(("quit", None))


def handle_mouse(ms):
    bf = ms.u.b.usButtonFlags
    if not bf:
        return
    for down, up, mid in MOUSE_FLAGS:
        if bf & down:
            ui_events.put(("down", mid))
            if MOUSE_SOUND and sound_enabled:
                play_requests.put(1)
        if bf & up:
            ui_events.put(("up", mid))
    if bf & 0x0400:                                      # wheel
        delta = ctypes.c_short(ms.u.b.usButtonData).value
        ui_events.put(("tap", M("WU") if delta > 0 else M("WD")))
        ui_events.put(("wheel", delta))
    wake_overlay()


_raw = RAWINPUT()
_raw_size = wintypes.UINT()


@WNDPROC
def input_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        _raw_size.value = ctypes.sizeof(RAWINPUT)
        n = user32.GetRawInputData(lparam, 0x10000003, ctypes.byref(_raw), ctypes.byref(_raw_size),
                                   ctypes.sizeof(RAWINPUTHEADER))
        if n != 0xFFFFFFFF:
            try:
                if _raw.header.dwType == 1:
                    handle_keyboard(_raw.data.keyboard)
                elif _raw.header.dwType == 0:
                    handle_mouse(_raw.data.mouse)
            except Exception:
                pass
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def input_loop(ready, want_mouse):
    global input_thread_id
    input_thread_id = kernel32.GetCurrentThreadId()
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW(0, input_wndproc, 0, 0, hinst, None, None, None, None, "KeyboardSoundRawInput")
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, "KeyboardSoundRawInput", None, 0, 0, 0, 0, 0,
                                  wintypes.HWND(-3), None, hinst, None)      # message-only window
    devices = [RAWINPUTDEVICE(1, 6, 0x100, hwnd)]                             # keyboard, RIDEV_INPUTSINK
    if want_mouse:
        devices.append(RAWINPUTDEVICE(1, 2, 0x100, hwnd))                     # mouse
    arr = (RAWINPUTDEVICE * len(devices))(*devices)
    if not hwnd or not user32.RegisterRawInputDevices(arr, len(devices), ctypes.sizeof(RAWINPUTDEVICE)):
        ready.append(ctypes.WinError(ctypes.get_last_error()))
        return
    ready.append(None)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# ---------------------------------------------------------------- layouts
GAP = 0.07       # space between keys (in key units)
RADIUS = 0.18    # key corner rounding (in key units)


def rect_key(kid, label, x, y, w=1.0, h=1.0, char_scan=None, radius=RADIUS):
    g = GAP
    pts = [(x + g, y + g), (x + w - g, y + g), (x + w - g, y + h - g), (x + g, y + h - g)]
    return {"id": kid, "label": label, "pts": pts, "radii": [radius] * 4,
            "center": (x + w / 2, y + h / 2), "char_scan": char_scan}


def keyboard_layout(ox, numpad):
    keys = []

    def k(kid, label, x, y, w=1.0, h=1.0, char_scan=None):
        keys.append(rect_key(kid, label, ox + x, y, w, h, char_scan))

    r0, r1, r2, r3, r4, r5 = 0, 1.25, 2.25, 3.25, 4.25, 5.25

    k(S(0x01), "Esc", 0, r0)
    for i, sc in enumerate(list(range(0x3B, 0x45)) + [0x57, 0x58]):
        k(S(sc), f"F{i + 1}", 2 + i + 0.5 * (i // 4), r0)

    k(S(0x29), "`", 0, r1, char_scan=0x29)
    for i, sc in enumerate(range(0x02, 0x0E)):
        k(S(sc), "1234567890-="[i], 1 + i, r1, char_scan=sc)
    k(S(0x0E), "⌫", 13, r1, 2)

    k(S(0x0F), "Tab", 0, r2, 1.5)
    for i, sc in enumerate(range(0x10, 0x1C)):
        k(S(sc), "QWERTYUIOP[]"[i], 1.5 + i, r2, char_scan=sc)
    if not ISO_LAYOUT:
        k(S(0x2B), "\\", 13.5, r2, 1.5, char_scan=0x2B)

    k(S(0x3A), "Caps", 0, r3, 1.75)
    for i, sc in enumerate(range(0x1E, 0x29)):
        k(S(sc), "ASDFGHJKL;'"[i], 1.75 + i, r3, char_scan=sc)
    if ISO_LAYOUT:
        k(S(0x2B), "\\", 12.75, r3, char_scan=0x2B)
        g, a, c, e = GAP, ox + 13.5, ox + 15, ox + 13.75
        keys.append({"id": S(0x1C), "label": "Enter", "char_scan": None, "radii": [RADIUS] * 6,
                     "center": (ox + 14.25, r2 + 0.5),
                     "pts": [(a + g, r2 + g), (c - g, r2 + g), (c - g, r3 + 1 - g),
                             (e + g, r3 + 1 - g), (e + g, r3 - g), (a + g, r3 - g)]})
    else:
        k(S(0x1C), "Enter", 12.75, r3, 2.25)

    if ISO_LAYOUT:
        k(V(0xA0), "Shift", 0, r4, 1.25)
        k(S(0x56), "\\", 1.25, r4, char_scan=0x56)
    else:
        k(V(0xA0), "Shift", 0, r4, 2.25)
    for i, sc in enumerate(range(0x2C, 0x36)):
        k(S(sc), "ZXCVBNM,./"[i], 2.25 + i, r4, char_scan=sc)
    k(V(0xA1), "Shift", 12.25, r4, 2.75)

    k(V(0xA2), "Ctrl", 0, r5, 1.25)
    k(V(0x5B), "Win", 1.25, r5, 1.25)
    k(V(0xA4), "Alt", 2.5, r5, 1.25)
    k(S(0x39), "", 3.75, r5, 6.25)
    k(V(0xA5), "AltGr", 10, r5, 1.25)
    k(V(0x5C), "Win", 11.25, r5, 1.25)
    k(V(0x5D), "☰", 12.5, r5, 1.25)
    k(V(0xA3), "Ctrl", 13.75, r5, 1.25)

    for label, kid, x, y in (("PrtSc", V(0x2C), 15.5, r0), ("ScrLk", S(0x46), 16.5, r0),
                             ("Pause", V(0x13), 17.5, r0), ("Ins", E(0x52), 15.5, r1),
                             ("Home", E(0x47), 16.5, r1), ("PgUp", E(0x49), 17.5, r1),
                             ("Del", E(0x53), 15.5, r2), ("End", E(0x4F), 16.5, r2),
                             ("PgDn", E(0x51), 17.5, r2), ("↑", E(0x48), 16.5, r4),
                             ("←", E(0x4B), 15.5, r5), ("↓", E(0x50), 16.5, r5), ("→", E(0x4D), 17.5, r5)):
        k(kid, label, x, y)

    width = 18.5
    if numpad:
        k(V(0x90), "Num", 19, r1)
        k(E(0x35), "/", 20, r1)
        k(S(0x37), "*", 21, r1)
        k(S(0x4A), "-", 22, r1)
        for row, (y, scans) in enumerate(((r2, (0x47, 0x48, 0x49)), (r3, (0x4B, 0x4C, 0x4D)),
                                          (r4, (0x4F, 0x50, 0x51)))):
            for i, sc in enumerate(scans):
                k(S(sc), "789456123"[row * 3 + i], 19 + i, y)
        k(S(0x4E), "+", 22, r2, 1, 2)
        k(E(0x1C), "⏎", 22, r4, 1, 2)
        k(S(0x52), "0", 19, r5, 2)
        k(S(0x53), ".", 21, r5)
        width = 23
    return keys, width


MOUSE_W = 3.2


def mouse_layout(ox):
    """A mouse drawn in key units: left/right/middle button, wheel up/down, two side buttons."""
    y0, h = 0.55, 5.7
    shapes = []
    body = rect_key(None, "", ox, y0, MOUSE_W, h, radius=1.45)
    body["body"] = True
    shapes.append(body)
    bh = 2.3
    lmb = rect_key(M("L"), "", ox, y0, 1.35, bh)
    lmb["radii"] = [1.35, 0.12, 0.12, 0.12]
    rmb = rect_key(M("R"), "", ox + MOUSE_W - 1.35, y0, 1.35, bh)
    rmb["radii"] = [0.12, 1.35, 0.12, 0.12]
    shapes += [lmb, rmb,
               rect_key(M("M"), "", ox + 1.35, y0 + 0.35, MOUSE_W - 2.7, 1.5, radius=0.25),
               rect_key(M("WU"), "▲", ox + 1.1, y0 + 2.6, 1.0, 0.8),
               rect_key(M("WD"), "▼", ox + 1.1, y0 + 3.45, 1.0, 0.8),
               rect_key(M("X2"), "M5", ox + 0.1, y0 + 2.6, 0.85, 0.8),
               rect_key(M("X1"), "M4", ox + 0.1, y0 + 3.45, 0.85, 0.8)]
    return shapes


# ---------------------------------------------------------------- helpers
def layout_char(scan, hkl):
    """The character printed on a key in the given keyboard layout (e.g. Ě on Czech)."""
    vk = user32.MapVirtualKeyExW(scan, 1, hkl)
    if not vk:
        return None
    state = (ctypes.c_ubyte * 256)()
    buf = ctypes.create_unicode_buffer(8)
    n = user32.ToUnicodeEx(vk, scan, state, buf, 8, 0x4, hkl)
    ch = buf.value[:1] if n else ""
    return ch.upper() if ch and ch.isprintable() and not ch.isspace() else None


def foreground_layout():
    tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    return user32.GetKeyboardLayout(tid)


def rounded_points(pts, radii, segs=3):
    """Polygon with rounded corners as plain points (much cheaper to draw than smooth=True)."""
    out = []
    n = len(pts)
    for i in range(n):
        px, py = pts[i - 1]
        vx, vy = pts[i]
        nx, ny = pts[(i + 1) % n]
        l1 = ((vx - px) ** 2 + (vy - py) ** 2) ** 0.5 or 1
        l2 = ((nx - vx) ** 2 + (ny - vy) ** 2) ** 0.5 or 1
        r = min(radii[i], l1 / 2, l2 / 2)
        ax, ay = vx + (px - vx) / l1 * r, vy + (py - vy) / l1 * r
        bx, by = vx + (nx - vx) / l2 * r, vy + (ny - vy) / l2 * r
        for s in range(segs + 1):
            t = s / segs
            out += [(1 - t) ** 2 * ax + 2 * (1 - t) * t * vx + t * t * bx,
                    (1 - t) ** 2 * ay + 2 * (1 - t) * t * vy + t * t * by]
    return out


def hex_color(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c * 255))) for c in rgb)


def mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


DARK = (0.06, 0.06, 0.08)
WHITE = (1.0, 1.0, 1.0)
INK = (0.05, 0.05, 0.05)


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8-sig") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_settings(cfg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- overlay (plain Win32/GDI drawing: fast)
gdi32 = ctypes.WinDLL("gdi32")
HDC = wintypes.HDC
for name, args, res in (
    ("CreateCompatibleDC", [HDC], HDC),
    ("CreateCompatibleBitmap", [HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
    ("SelectObject", [HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
    ("DeleteObject", [wintypes.HGDIOBJ], wintypes.BOOL),
    ("DeleteDC", [HDC], wintypes.BOOL),
    ("GetStockObject", [ctypes.c_int], wintypes.HGDIOBJ),
    ("SetDCBrushColor", [HDC, wintypes.COLORREF], wintypes.COLORREF),
    ("SetDCPenColor", [HDC, wintypes.COLORREF], wintypes.COLORREF),
    ("Polygon", [HDC, ctypes.POINTER(wintypes.POINT), ctypes.c_int], wintypes.BOOL),
    ("SetBkMode", [HDC, ctypes.c_int], ctypes.c_int),
    ("SetTextColor", [HDC, wintypes.COLORREF], wintypes.COLORREF),
    ("CreateFontW", [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR], wintypes.HFONT),
    ("CreatePen", [ctypes.c_int, ctypes.c_int, wintypes.COLORREF], wintypes.HPEN),
    ("BitBlt", [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HDC, ctypes.c_int,
                ctypes.c_int, wintypes.DWORD], wintypes.BOOL),
):
    getattr(gdi32, name).argtypes, getattr(gdi32, name).restype = args, res
for name, args, res in (
    ("GetDC", [wintypes.HWND], HDC),
    ("ReleaseDC", [wintypes.HWND, HDC], ctypes.c_int),
    ("DrawTextW", [HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT], ctypes.c_int),
    ("FillRect", [HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH], ctypes.c_int),
    ("SetLayeredWindowAttributes", [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD], wintypes.BOOL),
    ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
    ("SetWindowPos", [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      wintypes.UINT], wintypes.BOOL),
    ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
    ("GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
    ("SetTimer", [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p], ctypes.c_size_t),
    ("PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
    ("PostQuitMessage", [ctypes.c_int], None),
    ("CreatePopupMenu", [], wintypes.HMENU),
    ("AppendMenuW", [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
    ("TrackPopupMenu", [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        wintypes.HWND, ctypes.c_void_p], ctypes.c_int),
    ("DestroyMenu", [wintypes.HMENU], wintypes.BOOL),
    ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
    ("LoadCursorW", [wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HANDLE),
    ("DestroyWindow", [wintypes.HWND], wintypes.BOOL),
    ("GetDpiForSystem", [], wintypes.UINT),
    ("BeginPaint", [wintypes.HWND, ctypes.c_void_p], HDC),
    ("EndPaint", [wintypes.HWND, ctypes.c_void_p], wintypes.BOOL),
):
    getattr(user32, name).argtypes, getattr(user32, name).restype = args, res

msimg32 = ctypes.WinDLL("msimg32")


class TRIVERTEX(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("Red", wintypes.USHORT),
                ("Green", wintypes.USHORT), ("Blue", wintypes.USHORT), ("Alpha", wintypes.USHORT)]


class GRADIENT_RECT(ctypes.Structure):
    _fields_ = [("UpperLeft", wintypes.ULONG), ("LowerRight", wintypes.ULONG)]


msimg32.GradientFill.argtypes = [HDC, ctypes.POINTER(TRIVERTEX), wintypes.ULONG, ctypes.c_void_p,
                                 wintypes.ULONG, wintypes.ULONG]
SRCAND, SRCPAINT = 0x008800C6, 0x00EE0086
GRADIENT_STEPS = 32

shell32 = ctypes.WinDLL("shell32")


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wintypes.HICON
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
WM_TRAY = 0x8001
WM_TASKBARCREATED = user32.RegisterWindowMessageW("TaskbarCreated")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


WM_APP, WM_TIMER, WM_PAINT, WM_NCHITTEST = 0x8000, 0x0113, 0x000F, 0x0084
WM_NCRBUTTONUP, WM_EXITSIZEMOVE, WM_DESTROY = 0x00A5, 0x0232, 0x0002
SRCCOPY = 0x00CC0020
DT_FLAGS = 0x1 | 0x4 | 0x20 | 0x100          # CENTER | VCENTER | SINGLELINE | NOCLIP
TRANSPARENT_KEY = 0x00030201                 # this exact color is see-through
overlay_hwnd = None


def cref(rgb):
    r, g, b = (max(0, min(255, int(c * 255))) for c in rgb)
    return r | (g << 8) | (b << 16)


class Overlay:
    ACCENT = cref((0.49, 0.61, 1.0))

    def __init__(self, cfg):
        self.cfg = cfg
        self.dpi = user32.GetDpiForSystem() / 96.0
        self.held = set()
        self.released_at = {}
        self.active = set()
        self.editing = False
        self.hkl = None
        self.dirty = True
        self.last_rgb = 0.0
        self.ticks = 0
        self.fonts, self.bitmaps = [], []
        self.base_dc = self.back_dc = None

        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self.wndproc)
        wc = WNDCLASSW(0, self._wndproc, 0, 0, hinst, None,
                       user32.LoadCursorW(None, ctypes.c_void_p(32646)), None, None, "KeyboardSoundOverlay")
        user32.RegisterClassW(ctypes.byref(wc))
        ex = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000 | 0x20   # LAYERED|TOPMOST|TOOLWINDOW|NOACTIVATE|TRANSPARENT
        self.hwnd = user32.CreateWindowExW(ex, "KeyboardSoundOverlay", "Keyboard overlay", 0x80000000,
                                           0, 0, 10, 10, None, None, hinst, None)
        self.screen_dc = user32.GetDC(self.hwnd)
        self.build()
        if cfg["visible"]:
            user32.ShowWindow(self.hwnd, 4)                      # SW_SHOWNOACTIVATE
        user32.SetTimer(self.hwnd, 1, int(1000 / FPS), None)
        self.add_tray_icon()
        if not cfg["welcomed"]:
            self.balloon(f"{APP_NAME} is running",
                         "Right-click this icon for options.\n"
                         "Ctrl+Alt+F11 = move/resize overlay, Ctrl+Alt+F12 = quit.")
            cfg["welcomed"] = True
            save_settings(cfg)

    # ---------- tray icon
    def tray_data(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID = self.hwnd, 1
        return nid

    def add_tray_icon(self):
        size = user32.GetSystemMetrics(49)                       # small icon size
        self.icon = user32.LoadImageW(None, ICON_FILE, 1, size, size, 0x10) if os.path.exists(ICON_FILE) else None
        if not self.icon:
            self.icon = user32.LoadIconW(None, ctypes.c_void_p(32512))
        nid = self.tray_data()
        nid.uFlags = 0x1 | 0x2 | 0x4                             # MESSAGE | ICON | TIP
        nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, self.icon, APP_NAME
        shell32.Shell_NotifyIconW(0, ctypes.byref(nid))          # NIM_ADD

    def balloon(self, title, text):
        nid = self.tray_data()
        nid.uFlags, nid.szInfoTitle, nid.szInfo, nid.dwInfoFlags = 0x10, title, text, 0x1
        shell32.Shell_NotifyIconW(1, ctypes.byref(nid))          # NIM_MODIFY

    def set_visible(self, on):
        self.cfg["visible"] = on
        user32.ShowWindow(self.hwnd, 4 if on else 0)
        if on:
            self.render(force_base=True)
        save_settings(self.cfg)

    def show_tray_menu(self):
        global sound_enabled
        cfg, menu, add = self.cfg, user32.CreatePopupMenu(), user32.AppendMenuW
        checked = lambda on: 0x8 if on else 0
        add(menu, checked(self.editing), 101, "Move / resize overlay\tCtrl+Alt+F11")
        add(menu, checked(cfg["visible"]), 102, "Show keyboard overlay")
        add(menu, checked(cfg["sound"]), 103, "Click sound")
        if FROZEN:
            add(menu, checked(autostart_enabled()), 104, "Start with Windows")
        add(menu, 0x800, 0, None)
        add(menu, 0, 105, "Quit\tCtrl+Alt+F12")
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, 0x100 | 0x2, p.x, p.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == 101:
            self.toggle_edit()
        elif cmd == 102:
            self.set_visible(not cfg["visible"])
        elif cmd == 103:
            cfg["sound"] = sound_enabled = not cfg["sound"]
            save_settings(cfg)
        elif cmd == 104:
            set_autostart(not autostart_enabled())
        elif cmd == 105:
            user32.PostQuitMessage(0)

    # ---------- building
    def build(self):
        cfg = self.cfg
        u = self.u = cfg["key_size"] * self.dpi
        pad = 0.35 * u
        kb_x = MOUSE_W + 0.5 if cfg["mouse"] and cfg["mouse_side"] == "left" else 0
        keys, kb_w = keyboard_layout(kb_x, cfg["numpad"])
        width_u = kb_w + kb_x
        if cfg["mouse"]:
            keys += mouse_layout(0 if cfg["mouse_side"] == "left" else kb_w + 0.5)
            width_u = kb_w + MOUSE_W + 0.5
        self.keys = keys
        self.w, self.h = int(width_u * u + 2 * pad), int(6.25 * u + 2 * pad)

        def poly(pts, radii, segs=3):
            flat = rounded_points(pts, radii, segs)
            arr = (wintypes.POINT * (len(flat) // 2))(*[wintypes.POINT(int(round(flat[i])), int(round(flat[i + 1])))
                                                        for i in range(0, len(flat), 2)])
            return arr, len(arr)

        self.plate = poly([(0, 0), (self.w - 1, 0), (self.w - 1, self.h - 1), (0, self.h - 1)], [0.4 * u] * 4, 5)
        self.by_id = {}
        for i, key in enumerate(keys):
            key["poly"] = poly([(pad + x * u, pad + y * u) for x, y in key["pts"]], [r * u for r in key["radii"]])
            xs = [pad + x * u for x, _ in key["pts"]]
            ys = [pad + y * u for _, y in key["pts"]]
            cx, cy = pad + key["center"][0] * u, pad + key["center"][1] * u
            half_w = (max(xs) - min(xs)) / 2
            key["rect"] = wintypes.RECT(int(cx - half_w), int(cy - u / 2), int(cx + half_w), int(cy + u / 2))
            key["font"] = 0 if len(key["label"]) <= 2 else 1
            key["text"] = key["label"]
            key["hue_pos"] = (cx / self.w) * RGB_SPREAD
            if not key.get("body"):
                self.by_id.setdefault(key["id"], []).append(i)

        for f in self.fonts:
            gdi32.DeleteObject(f)
        mk = lambda px: gdi32.CreateFontW(-max(7, int(px)), 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI Semibold")
        self.fonts = [mk(u * 0.42), mk(u * 0.27), mk(u * 0.36)]
        for dc, bmp in self.bitmaps:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(dc)
        self.bitmaps = []
        for _ in range(6):
            dc = gdi32.CreateCompatibleDC(self.screen_dc)
            bmp = gdi32.CreateCompatibleBitmap(self.screen_dc, self.w, self.h)
            gdi32.SelectObject(dc, bmp)
            gdi32.SelectObject(dc, gdi32.GetStockObject(18))     # DC_BRUSH
            gdi32.SelectObject(dc, gdi32.GetStockObject(19))     # DC_PEN
            gdi32.SetBkMode(dc, 1)                               # transparent text background
            self.bitmaps.append((dc, bmp))
        (self.base_dc, self.back_dc, self.static_dc, self.text_dc,
         self.outline_dc, self.tmp_dc) = [dc for dc, _ in self.bitmaps]
        self.tri = (TRIVERTEX * (2 * GRADIENT_STEPS))()
        self.mesh = (GRADIENT_RECT * GRADIENT_STEPS)(*[(2 * i, 2 * i + 1) for i in range(GRADIENT_STEPS)])

        user32.SetLayeredWindowAttributes(self.hwnd, TRANSPARENT_KEY, int(cfg["opacity"] * 255), 3)
        self.place()
        self.hkl = None
        self.refresh_labels()
        self.build_layers()
        self.active = {i for kid in self.held for i in self.by_id.get(kid, ())}
        self.dirty = True
        self.render(force_base=True)

    def place(self):
        cfg = self.cfg
        if cfg["x"] is None or cfg["y"] is None:
            area = wintypes.RECT()
            user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)
            m = int(20 * self.dpi)
            x = area.left + m if "left" in cfg["corner"] else area.right - self.w - m
            y = area.top + m if "top" in cfg["corner"] else area.bottom - self.h - m
        else:
            x, y = int(cfg["x"]), int(cfg["y"])
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, y, self.w, self.h, 0x10)   # TOPMOST, NOACTIVATE

    # ---------- drawing
    def key_colors(self, key, now, glow):
        color = colorsys.hsv_to_rgb((key["hue_pos"] - now * RGB_SPEED) % 1.0, 1.0, 1.0)
        idle = mix(DARK, color, IDLE_BRIGHTNESS)
        if not glow:
            return cref(DARK), cref(idle), cref(mix(color, WHITE, 0.3))
        return (cref(mix(DARK, mix(color, WHITE, 0.2), glow)), cref(mix(idle, WHITE, glow)),
                cref(mix(mix(color, WHITE, 0.3), INK, glow)))

    def draw_key(self, dc, key, colors):
        fill, outline, text = colors
        gdi32.SetDCBrushColor(dc, fill)
        gdi32.SetDCPenColor(dc, outline)
        gdi32.Polygon(dc, *key["poly"])
        if key["text"]:
            gdi32.SelectObject(dc, self.fonts[key["font"]])
            gdi32.SetTextColor(dc, text)
            user32.DrawTextW(dc, key["text"], -1, ctypes.byref(key["rect"]), DT_FLAGS)

    def fill_all(self, dc, color):
        gdi32.SetDCBrushColor(dc, color)
        gdi32.SetDCPenColor(dc, color)
        gdi32.Polygon(dc, (wintypes.POINT * 4)((0, 0), (self.w + 1, 0), (self.w + 1, self.h + 1),
                                               (0, self.h + 1)), 4)

    def build_layers(self):
        """Pre-draws everything that doesn't change: the dark keys, plus white masks of
        all key outlines and labels. Each frame those masks just get tinted with a rainbow."""
        dc = self.static_dc
        self.fill_all(dc, TRANSPARENT_KEY)
        if self.cfg["background"]:
            gdi32.SetDCBrushColor(dc, cref((0.043, 0.043, 0.06)))
            gdi32.SetDCPenColor(dc, cref((0.15, 0.15, 0.18)))
            gdi32.Polygon(dc, *self.plate)
        dark = cref(DARK)
        for key in self.keys:
            if key.get("body"):
                gdi32.SetDCBrushColor(dc, cref((0.07, 0.07, 0.095)))
                gdi32.SetDCPenColor(dc, cref((0.17, 0.17, 0.21)))
            else:
                gdi32.SetDCBrushColor(dc, dark)
                gdi32.SetDCPenColor(dc, dark)
            gdi32.Polygon(dc, *key["poly"])

        for dc in (self.text_dc, self.outline_dc):
            self.fill_all(dc, 0)
        gdi32.SetDCBrushColor(self.outline_dc, 0)
        gdi32.SetDCPenColor(self.outline_dc, 0xFFFFFF)
        gdi32.SetTextColor(self.text_dc, 0xFFFFFF)
        for key in self.keys:
            if key.get("body"):
                continue
            gdi32.Polygon(self.outline_dc, *key["poly"])
            if key["text"]:
                gdi32.SelectObject(self.text_dc, self.fonts[key["font"]])
                user32.DrawTextW(self.text_dc, key["text"], -1, ctypes.byref(key["rect"]), DT_FLAGS)

    def rainbow(self, dc, now, lift, brightness):
        n = GRADIENT_STEPS
        for i in range(n + 1):
            r, g, b = mix(colorsys.hsv_to_rgb((i / n * RGB_SPREAD - now * RGB_SPEED) % 1.0, 1.0, 1.0), WHITE, lift)
            col = (int(r * brightness * 0xFF00), int(g * brightness * 0xFF00), int(b * brightness * 0xFF00))
            x = int(self.w * i / n)
            for j, y in ((2 * i - 1, self.h), (2 * i, 0)):
                if 0 <= j < 2 * n:
                    v = self.tri[j]
                    v.x, v.y, v.Red, v.Green, v.Blue = x, y, *col
        msimg32.GradientFill(dc, self.tri, 2 * n, self.mesh, n, 0)       # horizontal gradient

    def render_base(self, now):
        w, h = self.w, self.h
        gdi32.BitBlt(self.base_dc, 0, 0, w, h, self.static_dc, 0, 0, SRCCOPY)
        for mask, lift, brightness in ((self.outline_dc, 0.0, IDLE_BRIGHTNESS), (self.text_dc, 0.3, 1.0)):
            self.rainbow(self.tmp_dc, now, lift, brightness)
            gdi32.BitBlt(self.tmp_dc, 0, 0, w, h, mask, 0, 0, SRCAND)          # keep only outline / text pixels
            gdi32.BitBlt(self.base_dc, 0, 0, w, h, self.tmp_dc, 0, 0, SRCPAINT)  # add them onto the keys

    def render(self, force_base=False):
        now = time.monotonic()
        base_changed = False
        if force_base or (RGB_SPEED and now - self.last_rgb >= 1.0 / RGB_FPS):
            self.render_base(now)
            self.last_rgb = now
            base_changed = True
        if not (base_changed or self.active or self.dirty or self.editing):
            return
        self.dirty = False
        dc = self.back_dc
        gdi32.BitBlt(dc, 0, 0, self.w, self.h, self.base_dc, 0, 0, SRCCOPY)
        for i in list(self.active):
            key = self.keys[i]
            kid = key["id"]
            glow = 1.0 if kid in self.held else max(0.0, 1.0 - (now - self.released_at.get(kid, -99.0)) / GLOW_TIME)
            if glow <= 0:
                self.active.discard(i)
                self.dirty = True        # draw once more without it
                continue
            self.draw_key(dc, key, self.key_colors(key, now, glow))
        if self.editing:
            self.draw_edit_hint(dc)
        gdi32.BitBlt(self.screen_dc, 0, 0, self.w, self.h, dc, 0, 0, SRCCOPY)

    def draw_edit_hint(self, dc):
        gdi32.SelectObject(dc, gdi32.GetStockObject(5))                     # hollow brush
        gdi32.SetDCPenColor(dc, self.ACCENT)
        gdi32.Polygon(dc, *self.plate)
        gdi32.SelectObject(dc, gdi32.GetStockObject(18))
        text = "Drag to move  •  Scroll to resize  •  Right-click: options  •  Ctrl+Alt+F11: done"
        r = wintypes.RECT(int(self.w * 0.08), int(self.h / 2 - self.u * 0.45),
                          int(self.w * 0.92), int(self.h / 2 + self.u * 0.45))
        gdi32.SetDCBrushColor(dc, 0)
        gdi32.SetDCPenColor(dc, self.ACCENT)
        gdi32.Polygon(dc, (wintypes.POINT * 4)((r.left, r.top), (r.right, r.top), (r.right, r.bottom),
                                               (r.left, r.bottom)), 4)
        gdi32.SelectObject(dc, self.fonts[2])
        gdi32.SetTextColor(dc, 0xFFFFFF)
        user32.DrawTextW(dc, text, -1, ctypes.byref(r), DT_FLAGS)

    # ---------- events
    def wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg in (WM_TIMER, WM_APP):
                self.tick(msg == WM_TIMER)
                return 0
            if msg == WM_PAINT:
                ps = ctypes.create_string_buffer(72)
                hdc = user32.BeginPaint(hwnd, ps)
                gdi32.BitBlt(hdc, 0, 0, self.w, self.h, self.back_dc, 0, 0, SRCCOPY)
                user32.EndPaint(hwnd, ps)
                return 0
            if msg == WM_NCHITTEST and self.editing:
                return 2                                   # HTCAPTION -> Windows lets you drag it
            if msg == WM_NCRBUTTONUP and self.editing:
                self.show_menu()
                return 0
            if msg == WM_EXITSIZEMOVE:                     # finished dragging
                self.remember_position()
                save_settings(self.cfg)
                return 0
            if msg == WM_TRAY:
                if lparam & 0xFFFF in (0x0202, 0x0205):           # left / right click on the tray icon
                    self.show_tray_menu()
                return 0
            if msg == WM_TASKBARCREATED:                          # Explorer restarted -> icon is gone
                self.add_tray_icon()
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            print("overlay error:", e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def tick(self, from_timer):
        try:
            while True:
                kind, val = ui_events.get_nowait()
                if kind == "down":
                    self.held.add(val)
                    self.active.update(self.by_id.get(val, ()))
                elif kind in ("up", "tap"):
                    self.held.discard(val)
                    self.released_at[val] = time.monotonic()
                    self.active.update(self.by_id.get(val, ()))
                elif kind == "wheel":
                    self.on_wheel(val)
                elif kind == "edit":
                    self.toggle_edit()
                elif kind == "quit":
                    user32.PostQuitMessage(0)
                    return
        except queue.Empty:
            pass
        if not self.cfg["visible"]:
            return
        self.render()
        if from_timer:
            self.ticks += 1
            if self.ticks % FPS == 0:                     # once a second
                if self.refresh_labels():
                    self.build_layers()
                    self.render(force_base=True)
                user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

    def refresh_labels(self):
        """Show the characters of your current keyboard layout (e.g. Czech ě š č)."""
        hkl = foreground_layout()
        if hkl == self.hkl:
            return False
        self.hkl = hkl
        for key in self.keys:
            if key.get("char_scan"):
                key["text"] = layout_char(key["char_scan"], hkl) or key["label"]
        return True

    def set_click_through(self, on):
        style = user32.GetWindowLongPtrW(self.hwnd, -20)
        style = style | 0x20 if on else style & ~0x20
        user32.SetWindowLongPtrW(self.hwnd, -20, style)

    def remember_position(self):
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        self.cfg["x"], self.cfg["y"] = r.left, r.top

    def toggle_edit(self):
        if not self.cfg["visible"]:
            self.set_visible(True)
        self.editing = not self.editing
        self.set_click_through(not self.editing)
        if not self.editing:
            self.remember_position()
            save_settings(self.cfg)
        self.render(force_base=True)

    def on_wheel(self, delta):
        if not self.editing:
            return
        p, r = wintypes.POINT(), wintypes.RECT()
        user32.GetCursorPos(ctypes.byref(p))
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        if r.left <= p.x < r.right and r.top <= p.y < r.bottom:
            self.resize(1 if delta > 0 else -1)

    def resize(self, step):
        self.remember_position()
        self.cfg["key_size"] = max(12, min(60, self.cfg["key_size"] + 2 * step))
        self.build()
        save_settings(self.cfg)

    def show_menu(self):
        cfg = self.cfg
        menu, opacity, corners = user32.CreatePopupMenu(), user32.CreatePopupMenu(), user32.CreatePopupMenu()
        add = user32.AppendMenuW
        add(menu, 0, 1, ("Hide" if cfg["numpad"] else "Show") + " numpad")
        add(menu, 0, 2, ("Hide" if cfg["mouse"] else "Show") + " mouse")
        add(menu, 0, 3, "Mouse on the " + ("left" if cfg["mouse_side"] == "right" else "right"))
        add(menu, 0, 6, ("Hide" if cfg["background"] else "Show") + " background")
        add(menu, 0x800, 0, None)
        add(menu, 0, 4, "Bigger")
        add(menu, 0, 5, "Smaller")
        levels = (1.0, 0.9, 0.75, 0.6, 0.45)
        for i, v in enumerate(levels):
            add(opacity, 0x8 if abs(cfg["opacity"] - v) < 0.01 else 0, 10 + i, f"{int(v * 100)} %")
        add(menu, 0x10, opacity, "Opacity")
        names = ("top-left", "top-right", "bottom-left", "bottom-right")
        for i, c in enumerate(names):
            add(corners, 0, 20 + i, c.replace("-", " "))
        add(menu, 0x10, corners, "Snap to corner")
        add(menu, 0x800, 0, None)
        add(menu, 0, 30, "Done (lock position)")
        add(menu, 0, 31, "Quit")
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, 0x100 | 0x2, p.x, p.y, 0, self.hwnd, None)   # RETURNCMD|RIGHTBUTTON
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

        self.remember_position()
        if cmd == 1:
            cfg["numpad"] = not cfg["numpad"]
        elif cmd == 2:
            cfg["mouse"] = not cfg["mouse"]
        elif cmd == 3:
            cfg["mouse_side"] = "left" if cfg["mouse_side"] == "right" else "right"
        elif cmd == 6:
            cfg["background"] = not cfg["background"]
        elif cmd in (4, 5):
            self.resize(1 if cmd == 4 else -1)
            return
        elif 10 <= cmd < 10 + len(levels):
            cfg["opacity"] = levels[cmd - 10]
        elif 20 <= cmd < 24:
            cfg["corner"], cfg["x"], cfg["y"] = names[cmd - 20], None, None
        elif cmd == 30:
            self.toggle_edit()
            return
        elif cmd == 31:
            user32.PostQuitMessage(0)
            return
        else:
            return
        self.build()
        save_settings(self.cfg)

    def close(self):
        if self.editing:
            self.toggle_edit()
        shell32.Shell_NotifyIconW(2, ctypes.byref(self.tray_data()))   # NIM_DELETE
        user32.ReleaseDC(self.hwnd, self.screen_dc)
        user32.DestroyWindow(self.hwnd)


# ---------------------------------------------------------------- main
def start(target, *args):
    ready = []
    t = threading.Thread(target=target, args=(ready, *args), daemon=True)
    t.start()
    while not ready:
        time.sleep(0.01)
    if ready[0] is not None:
        raise ready[0]
    return t


def error_box(text):
    user32.MessageBoxW(None, text, APP_NAME, 0x10)


def main():
    global overlay_hwnd, sound_enabled
    mutex = kernel32.CreateMutexW(None, False, "KeyboardSound_SingleInstance")
    if ctypes.get_last_error() == 183:                   # ERROR_ALREADY_EXISTS
        user32.MessageBoxW(None, f"{APP_NAME} is already running.\nLook for its icon in the tray "
                                 "(bottom right, near the clock).", APP_NAME, 0x40)
        return
    if not os.path.exists(SOUND_FILE):
        error_box(f"Sound file not found:\n{SOUND_FILE}")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # crisp overlay on scaled displays
    except Exception:
        pass
    sys.setswitchinterval(0.001)                         # react to keys faster

    cfg = load_settings()
    sound_enabled = cfg["sound"]
    audio = start(audio_worker)
    overlay = Overlay(cfg)
    overlay_hwnd = overlay.hwnd
    start(input_loop, True)

    signal.signal(signal.SIGINT, lambda *_: user32.PostQuitMessage(0))
    print("Keyboard sounds + RGB overlay ON.")
    print("  Ctrl+Alt+F11  move / resize / options      Ctrl+Alt+F12 (or Ctrl+C here)  quit")
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        overlay.close()
        if input_thread_id:
            user32.PostThreadMessageW(input_thread_id, WM_QUIT, 0, 0)
        play_requests.put(None)
        audio.join(timeout=2)
        print("Stopped.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_box(f"{APP_NAME} stopped because of an error:\n\n{e!r}")
