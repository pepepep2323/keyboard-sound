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
Music:  Ctrl+Alt+Down play/pause, Ctrl+Alt+Left/Right previous/next,
        Ctrl+Alt+Shift+Left/Right seek (works with Spotify, YouTube, ... - needs the
        "winrt-Windows.Media.Control" and "winrt-Windows.Storage.Streams" packages when run from source)

Build the .exe:  build.bat
"""

import array
import copy
import colorsys
import ctypes
import json
import math
import os
import queue
import random
import shutil
import signal
import sys
import threading
import time
import wave
import importlib.util
from datetime import datetime, timezone
import winreg
from ctypes import wintypes

# ---------------------------------------------------------------- settings
APP_NAME = "Keyboard Sound"
FROZEN = getattr(sys, "frozen", False)               # True when running as KeyboardSound.exe
HERE = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", HERE)             # where bundled files are inside the .exe
SOUNDS_DIR = os.path.join(RES_DIR, "sounds")        # built-in click sounds
ICON_FILE = os.path.join(RES_DIR, "icon.ico")
DATA_DIR = os.path.join(os.environ.get("APPDATA", HERE), "KeyboardSound") if FROZEN else HERE
SETTINGS_FILE = os.path.join(DATA_DIR, "overlay_settings.json")

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
    "sound": True,               # click sound on (tray icon -> Sound)
    "sound_choice": "creamy-1",  # one of SOUNDS below, or "custom"
    "custom_sound": None,        # path of your own sound (copied into the settings folder)
    "custom_name": None,         # original file name of your own sound (shown in the menu)
    "volume": 1.0,               # 0.25 .. 1.0
    "effect": "wave",            # wave, static, breathe, ripple, reactive
    "color": "rainbow",          # rainbow or one of COLORS below
    "media_widget": True,        # now-playing panel (Spotify, YouTube, ...) above the keyboard
    "seek_step": 10,             # seconds to jump with the seek shortcuts
    "binds": {                   # [modifiers, virtual key]; modifiers: Alt=1, Ctrl=2, Shift=4, Win=8
        "play": [3, 0x28],       # Ctrl+Alt+Down
        "next": [3, 0x27],       # Ctrl+Alt+Right
        "prev": [3, 0x25],       # Ctrl+Alt+Left
        "fwd": [7, 0x27],        # Ctrl+Alt+Shift+Right
        "back": [7, 0x25],       # Ctrl+Alt+Shift+Left
    },
    "welcomed": False,           # first-start tip already shown
}
ISO_LAYOUT = True      # True = European keyboard (tall Enter), False = US

GLOW_TIME = 0.5        # seconds a key keeps glowing after you let go
RGB_SPEED = 0.15       # how fast the rainbow moves (0 = still)
RGB_SPREAD = 1.0       # how many rainbows fit across the overlay
IDLE_BRIGHTNESS = 0.55 # brightness of keys that are not pressed (0..1)
FPS = 30               # glow animation frame rate
RGB_FPS = 12           # rainbow / breathing update rate (lower = less CPU)
BREATHE_PERIOD = 3.0   # seconds for one breath in the "breathe" effect
RIPPLE_SPEED = 16      # keys per second the "ripple" ring travels
RIPPLE_LIFE = 1.1      # seconds a ripple lasts

SOUNDS = [("creamy-1", "Creamy 1"), ("creamy-2", "Creamy 2"), ("creamy-3", "Creamy 3")]
EFFECTS = ["wave", "static", "breathe", "ripple", "reactive"]
COLORS = {
    "rainbow": None, "red": (1.0, 0.15, 0.15), "orange": (1.0, 0.5, 0.05), "yellow": (1.0, 0.85, 0.1),
    "green": (0.15, 1.0, 0.3), "cyan": (0.1, 0.9, 1.0), "blue": (0.2, 0.4, 1.0), "purple": (0.6, 0.25, 1.0),
    "pink": (1.0, 0.3, 0.75), "white": (1.0, 1.0, 1.0),
}
CUSTOM_MAX_SECONDS = 2.0
MEDIA_ACTIONS = ["play", "next", "prev", "fwd", "back"]
MEDIA_OK = importlib.util.find_spec("winrt.windows.media.control") is not None

HOTKEY_EDIT = 0x57     # F11 (with Ctrl+Alt)
HOTKEY_QUIT = 0x58     # F12 (with Ctrl+Alt)

# ---------------------------------------------------------------- texts (Czech or English, from Windows language)
def ui_language():
    try:
        return "cs" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF in (0x05, 0x1B) else "en"
    except Exception:
        return "en"


HELP_CS = """KEYBOARD SOUND - NÁPOVĚDA

MENU
  Pravý klik na ikonu klávesnice vpravo dole u hodin.
  (Když ji nevidíš, klikni na šipku ^ vedle hodin.)
  Najdeš tam: zvuk, výběr zvuku, vlastní zvuk, hlasitost,
  efekty, barvy, spouštění s Windows a ukončení.

PŘESUNUTÍ A VELIKOST
  Ctrl+Alt+F11 (nebo menu > Přesunout / změnit velikost)
  - táhni klávesnici myší
  - kolečkem myši měníš velikost
  - pravý klik na klávesnici: numpad, myš, pozadí,
    průhlednost, roh obrazovky
  - znovu Ctrl+Alt+F11 = hotovo (uloží se)

VLASTNÍ ZVUK
  Menu > Zvuk > Nahrát vlastní zvuk...
  (wav, mp3, m4a, ogg, flac, wma - použijí se první 2 sekundy)

HUDBA (SPOTIFY, YOUTUBE...)
  Ctrl+Alt+Šipka dolů    přehrát / pauza
  Ctrl+Alt+Šipka vpravo  další skladba
  Ctrl+Alt+Šipka vlevo   předchozí skladba
  Ctrl+Alt+Shift+Šipky   posunout skladbu o 10 s
  Nad klávesnicí je přehrávač: klikni na tlačítka,
  nebo na lištu a skladba skočí na to místo.
  Zkratky změníš v menu > Hudba > Změnit zkratky.

SKRÝT KLÁVESNICI
  Menu > Zobrazit klávesnici (zvuk hraje dál)

VYPNOUT
  Ctrl+Alt+F12, nebo menu > Ukončit

HRY
  Klávesnice je vidět jen v režimu "okno bez okraje"
  (borderless), ne v exkluzivní celé obrazovce.

Nastavení se ukládá do %APPDATA%\\KeyboardSound."""

HELP_EN = """KEYBOARD SOUND - HELP

MENU
  Right-click the keyboard icon near the clock (bottom right).
  (If you can't see it, click the ^ arrow next to the clock.)
  There you'll find: sound on/off, sound choice, your own sound,
  volume, effects, colors, start with Windows and quit.

MOVE AND RESIZE
  Ctrl+Alt+F11 (or menu > Move / resize)
  - drag the keyboard with the mouse
  - scroll the mouse wheel to resize
  - right-click the keyboard: numpad, mouse, background,
    opacity, screen corner
  - Ctrl+Alt+F11 again = done (it's saved)

YOUR OWN SOUND
  Menu > Sound > Load your own sound...
  (wav, mp3, m4a, ogg, flac, wma - the first 2 seconds are used)

MUSIC (SPOTIFY, YOUTUBE...)
  Ctrl+Alt+Down arrow    play / pause
  Ctrl+Alt+Right arrow   next track
  Ctrl+Alt+Left arrow    previous track
  Ctrl+Alt+Shift+Arrows  seek 10 s
  There's a player above the keyboard: click the buttons,
  or click the bar to jump to that point of the song.
  Change the shortcuts in menu > Music > Change shortcuts.

HIDE THE KEYBOARD
  Menu > Show keyboard (the sound keeps playing)

QUIT
  Ctrl+Alt+F12, or menu > Quit

GAMES
  The keyboard is only visible in borderless / windowed
  fullscreen, not in exclusive fullscreen.

Settings are saved in %APPDATA%\\KeyboardSound."""

TEXTS = {
    "cs": {
        "move": "Přesunout / změnit velikost\tCtrl+Alt+F11", "show": "Zobrazit klávesnici",
        "sound": "Zvuk", "sound_on": "Zvuk zapnutý", "own": "Vlastní: {}", "upload": "Nahrát vlastní zvuk...",
        "volume": "Hlasitost", "effect": "Efekt", "color": "Barva", "autostart": "Spouštět s Windows",
        "help": "Nápověda", "quit": "Ukončit\tCtrl+Alt+F12",
        "fx_wave": "Duhová vlna", "fx_static": "Statické světlo", "fx_breathe": "Dýchání",
        "fx_ripple": "Vlna od klávesy", "fx_reactive": "Jen stisknuté klávesy",
        "c_rainbow": "Duha", "c_red": "Červená", "c_orange": "Oranžová", "c_yellow": "Žlutá",
        "c_green": "Zelená", "c_cyan": "Tyrkysová", "c_blue": "Modrá", "c_purple": "Fialová",
        "c_pink": "Růžová", "c_white": "Bílá",
        "running_title": "Keyboard Sound běží",
        "running_text": "Pravý klik na tuhle ikonu = menu a nápověda.\nCtrl+Alt+F11 = přesunout, Ctrl+Alt+F12 = vypnout.",
        "already": "Keyboard Sound už běží.\nNajdeš ho jako ikonu klávesnice vpravo dole u hodin.",
        "sound_error": "Tenhle zvuk nejde přehrát:\n{}\n\nZkus jiný soubor (wav, mp3, m4a, ogg, flac, wma).",
        "pick_title": "Vyber zvuk kláves", "pick_filter": "Zvuky",
        "edit_hint": "Táhni = přesun  •  Kolečko = velikost  •  Pravý klik = možnosti  •  Ctrl+Alt+F11 = hotovo",
        "numpad_hide": "Skrýt numpad", "numpad_show": "Zobrazit numpad",
        "mouse_hide": "Skrýt myš", "mouse_show": "Zobrazit myš",
        "mouse_left": "Myš vlevo", "mouse_right": "Myš vpravo",
        "bg_hide": "Skrýt pozadí", "bg_show": "Zobrazit pozadí",
        "bigger": "Větší", "smaller": "Menší", "opacity": "Průhlednost", "corner": "Do rohu obrazovky",
        "top-left": "vlevo nahoře", "top-right": "vpravo nahoře",
        "bottom-left": "vlevo dole", "bottom-right": "vpravo dole",
        "done": "Hotovo (zamknout pozici)", "quit_plain": "Ukončit", "help_title": "Keyboard Sound - nápověda",
        "help_text": HELP_CS,
        "media": "Hudba (Spotify, YouTube...)", "media_widget": "Zobrazit přehrávač",
        "ma_play": "Přehrát / pauza", "ma_next": "Další skladba", "ma_prev": "Předchozí skladba",
        "ma_fwd": "Posunout vpřed", "ma_back": "Posunout zpět",
        "change_binds": "Změnit zkratky", "reset_binds": "Obnovit výchozí zkratky", "seek_step": "Krok posunu",
        "bind_prompt": "Stiskni novou zkratku pro: {}\n(s Ctrl / Alt / Shift, nebo F-klávesu.  Esc = zrušit)",
        "bind_busy": "Zkratku {} už používá jiný program.\nZkus jinou.", "bind_taken": "obsazeno",
        "media_missing": "Ovládání hudby není k dispozici", "space": "Mezerník",
        "nothing_playing": "Nic nehraje", "no_seek": "Tenhle přehrávač neumí posouvat",
    },
    "en": {
        "move": "Move / resize\tCtrl+Alt+F11", "show": "Show keyboard",
        "sound": "Sound", "sound_on": "Sound on", "own": "Own: {}", "upload": "Load your own sound...",
        "volume": "Volume", "effect": "Effect", "color": "Color", "autostart": "Start with Windows",
        "help": "Help", "quit": "Quit\tCtrl+Alt+F12",
        "fx_wave": "Rainbow wave", "fx_static": "Static light", "fx_breathe": "Breathing",
        "fx_ripple": "Ripple from key", "fx_reactive": "Only pressed keys",
        "c_rainbow": "Rainbow", "c_red": "Red", "c_orange": "Orange", "c_yellow": "Yellow",
        "c_green": "Green", "c_cyan": "Cyan", "c_blue": "Blue", "c_purple": "Purple",
        "c_pink": "Pink", "c_white": "White",
        "running_title": "Keyboard Sound is running",
        "running_text": "Right-click this icon for the menu and help.\nCtrl+Alt+F11 = move, Ctrl+Alt+F12 = quit.",
        "already": "Keyboard Sound is already running.\nLook for the keyboard icon near the clock (bottom right).",
        "sound_error": "This sound can't be played:\n{}\n\nTry another file (wav, mp3, m4a, ogg, flac, wma).",
        "pick_title": "Choose a key sound", "pick_filter": "Sounds",
        "edit_hint": "Drag = move  •  Scroll = size  •  Right-click = options  •  Ctrl+Alt+F11 = done",
        "numpad_hide": "Hide numpad", "numpad_show": "Show numpad",
        "mouse_hide": "Hide mouse", "mouse_show": "Show mouse",
        "mouse_left": "Mouse on the left", "mouse_right": "Mouse on the right",
        "bg_hide": "Hide background", "bg_show": "Show background",
        "bigger": "Bigger", "smaller": "Smaller", "opacity": "Opacity", "corner": "Snap to corner",
        "top-left": "top left", "top-right": "top right", "bottom-left": "bottom left", "bottom-right": "bottom right",
        "done": "Done (lock position)", "quit_plain": "Quit", "help_title": "Keyboard Sound - help",
        "help_text": HELP_EN,
        "media": "Music (Spotify, YouTube...)", "media_widget": "Show player",
        "ma_play": "Play / pause", "ma_next": "Next track", "ma_prev": "Previous track",
        "ma_fwd": "Seek forward", "ma_back": "Seek back",
        "change_binds": "Change shortcuts", "reset_binds": "Reset shortcuts", "seek_step": "Seek step",
        "bind_prompt": "Press a new shortcut for: {}\n(with Ctrl / Alt / Shift, or an F-key.  Esc = cancel)",
        "bind_busy": "The shortcut {} is already used by another program.\nTry a different one.",
        "bind_taken": "taken", "media_missing": "Music control is not available", "space": "Space",
        "nothing_playing": "Nothing is playing", "no_seek": "This player can't seek",
    },
}
LANG = ui_language()


def T(key):
    return TEXTS[LANG].get(key) or TEXTS["en"][key]


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
play_requests = queue.Queue()     # 1 = click, ("load", path, volume, preview) = change sound, None = quit


def sound_path(cfg):
    if cfg.get("sound_choice") == "custom" and cfg.get("custom_sound") and os.path.exists(cfg["custom_sound"]):
        return cfg["custom_sound"]
    name = cfg.get("sound_choice")
    if name not in dict(SOUNDS):
        name = SOUNDS[0][0]
    return os.path.join(SOUNDS_DIR, name + ".wav")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        h = text.replace("-", "")
        super().__init__(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                         (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


MF_MT_MAJOR_TYPE = GUID("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
MF_MT_SUBTYPE = GUID("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
MF_MT_AUDIO_BITS_PER_SAMPLE = GUID("f2deb57f-40fa-4764-aa33-ed4f2d1ff669")
MF_MT_AUDIO_NUM_CHANNELS = GUID("37e48bf5-645e-4c5b-89de-ada9e29b696a")
MF_MT_AUDIO_SAMPLES_PER_SECOND = GUID("5faeeae7-0290-4c31-9e8a-c534f68d9dba")
MFMediaType_Audio = GUID("73647561-0000-0010-8000-00AA00389B71")
MFAudioFormat_PCM = GUID("00000001-0000-0010-8000-00AA00389B71")


def com(obj, index, *args, argtypes=(), restype=ctypes.c_long):
    """Calls method number `index` of a COM object (no comtypes needed)."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    result = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])(obj, *args)
    if restype is ctypes.c_long and result < 0:
        raise OSError(f"Media Foundation error 0x{result & 0xFFFFFFFF:08X}")
    return result


def mf_decode(path, max_seconds):
    """Decodes mp3 / m4a / ogg / flac / wma / wav to 16-bit PCM with Windows Media Foundation."""
    vp, dw = ctypes.c_void_p, wintypes.DWORD
    ref = ctypes.byref
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    mfplat, mfrw = ctypes.WinDLL("mfplat"), ctypes.WinDLL("mfreadwrite")
    if mfplat.MFStartup(0x00020070, 0) < 0:
        raise OSError("Media Foundation is not available")
    reader, mtype, cur = vp(), vp(), vp()
    first_audio = 0xFFFFFFFD
    try:
        if mfrw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), None, ref(reader)) < 0:
            raise OSError("unknown or damaged audio file")
        com(reader, 4, 0xFFFFFFFE, False, argtypes=(dw, wintypes.BOOL))          # deselect all streams
        com(reader, 4, first_audio, True, argtypes=(dw, wintypes.BOOL))          # select the audio stream
        mfplat.MFCreateMediaType(ref(mtype))
        com(mtype, 24, ref(MF_MT_MAJOR_TYPE), ref(MFMediaType_Audio), argtypes=(vp, vp))   # SetGUID
        com(mtype, 24, ref(MF_MT_SUBTYPE), ref(MFAudioFormat_PCM), argtypes=(vp, vp))
        com(mtype, 21, ref(MF_MT_AUDIO_BITS_PER_SAMPLE), 16, argtypes=(vp, wintypes.UINT))  # SetUINT32
        com(reader, 7, first_audio, None, mtype, argtypes=(dw, vp, vp))          # SetCurrentMediaType
        com(reader, 6, first_audio, ref(cur), argtypes=(dw, vp))                 # GetCurrentMediaType
        ch, rate = wintypes.UINT(), wintypes.UINT()
        com(cur, 7, ref(MF_MT_AUDIO_NUM_CHANNELS), ref(ch), argtypes=(vp, vp))   # GetUINT32
        com(cur, 7, ref(MF_MT_AUDIO_SAMPLES_PER_SECOND), ref(rate), argtypes=(vp, vp))
        limit = int(max_seconds * rate.value) * ch.value * 2
        chunks, total = [], 0
        while total < limit:
            idx, flags, stamp, sample = dw(), dw(), ctypes.c_longlong(), vp()
            com(reader, 9, first_audio, 0, ref(idx), ref(flags), ref(stamp), ref(sample),    # ReadSample
                argtypes=(dw, dw, vp, vp, vp, vp))
            if sample.value:
                buf, ptr, max_len, cur_len = vp(), vp(), dw(), dw()
                com(sample, 41, ref(buf), argtypes=(vp,))                        # ConvertToContiguousBuffer
                com(buf, 3, ref(ptr), ref(max_len), ref(cur_len), argtypes=(vp, vp, vp))      # Lock
                chunks.append(ctypes.string_at(ptr, cur_len.value))
                total += cur_len.value
                com(buf, 4)                                                      # Unlock
                com(buf, 2, restype=ctypes.c_ulong)
                com(sample, 2, restype=ctypes.c_ulong)
            if flags.value & 0x2:                                                # end of stream
                break
        return ch.value, rate.value, b"".join(chunks)
    finally:
        for obj in (cur, mtype, reader):
            if obj.value:
                com(obj, 2, restype=ctypes.c_ulong)
        mfplat.MFShutdown()


def load_sound(path, max_seconds=CUSTOM_MAX_SECONDS + 1):
    """-> (channels, rate, 16-bit samples). Plain 16-bit WAV is read directly, everything else via Windows."""
    data = None
    if path.lower().endswith(".wav"):
        try:
            with wave.open(path, "rb") as w:
                if w.getsampwidth() == 2:
                    data = w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())
        except (wave.Error, EOFError):
            data = None
    if data is None:
        data = mf_decode(path, max_seconds)
    ch, rate, raw = data
    if ch < 1 or rate < 1:
        raise OSError("no audio found")
    return ch, rate, array.array("h", raw[:len(raw) - len(raw) % (2 * ch)])


def prepare_custom(ch, rate, samples):
    """Your own sound: skip silence at the start, keep max 2 s, make it about as loud as the built-in ones."""
    start = 0
    for i in range(0, len(samples) - ch + 1, ch):
        if max(abs(samples[i + c]) for c in range(ch)) > 100:          # louder than ~ -50 dB
            start = max(0, i - ch * int(rate * 0.002))
            break
    samples = samples[start:start + int(CUSTOM_MAX_SECONDS * rate) * ch]
    if not samples:
        return samples, 1.0
    rms = (sum(v * v for v in samples) / len(samples)) ** 0.5
    if rms < 1:
        return samples, 1.0
    # same loudness as the built-in clicks (about -21.5 dB RMS), with a soft limiter instead of hard clipping
    gain = max(0.25, min(8.0, 10 ** ((-21.5 - 20 * math.log10(rms / 32768)) / 20)))
    knee = 0.7 * 32767
    out = array.array("h", bytes(2 * len(samples)))
    for i, v in enumerate(samples):
        x = v * gain
        a = abs(x)
        if a > knee:
            a = knee + (a - knee) / (1 + (a - knee) / (32767 - knee))
            x = a if x > 0 else -a
        out[i] = int(x)
    return out, 1.0


def make_variants(ch, samples, gain):
    """A few slightly different pitches so fast typing doesn't sound robotic."""
    frames = len(samples) // ch
    if PITCH_VARIATION <= 0:
        steps = [0.0]
    else:
        steps = [-1, -0.5, 0, 0.5, 1] if frames < 20000 else [-1, 0, 1]
    variants = []
    for s in steps:
        speed = 1 + s * PITCH_VARIATION
        g = gain * (0.92 + 0.08 * random.random())
        n = int(frames / speed)
        out = array.array("h", bytes(n * ch * 2))
        for i in range(n):
            pos = i * speed
            j = int(pos)
            f = pos - j
            j2 = min(j + 1, frames - 1)
            for c in range(ch):
                v = samples[j * ch + c] * (1 - f) + samples[j2 * ch + c] * f
                out[i * ch + c] = max(-32768, min(32767, int(v * g)))
        variants.append(out.tobytes())
    return variants


def open_voices(path, volume):
    ch, rate, samples = load_sound(path)
    gain = volume
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(SOUNDS_DIR):
        samples, g = prepare_custom(ch, rate, samples)
        gain *= g
    if not samples:
        raise OSError("the sound is silent or empty")
    fmt = WAVEFORMATEX(1, ch, rate, rate * ch * 2, ch * 2, 16, 0)
    buffers = [ctypes.create_string_buffer(v, len(v)) for v in make_variants(ch, samples, gain)]
    voices = []
    for _ in range(VOICES):
        h = wintypes.HANDLE()
        if winmm.waveOutOpen(ctypes.byref(h), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0):
            close_voices(voices)
            raise OSError("could not open the audio output")
        hdrs = []
        for b in buffers:
            hdr = WAVEHDR(ctypes.cast(b, ctypes.c_void_p), len(b.raw), 0, 0, 0, 0, None, 0)
            winmm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
            hdrs.append(hdr)
        voices.append((h, hdrs))
    return voices, buffers


def close_voices(voices):
    for h, hdrs in voices:
        winmm.waveOutReset(h)
        for hdr in hdrs:
            winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
        winmm.waveOutClose(h)


def audio_worker(ready, path, volume):
    """Several waveOut streams = clicks overlap cleanly and start instantly."""
    try:
        voices, buffers = open_voices(path, volume)
    except Exception as e:
        ready.append(e)
        return
    ready.append(None)

    voice, last = 0, -1
    while True:
        req = play_requests.get()
        if req is None:
            break
        if isinstance(req, tuple):                       # switch to another sound
            _, new_path, new_volume, preview = req
            try:
                new_voices, new_buffers = open_voices(new_path, new_volume)
            except Exception as e:
                ui_events.put(("sound_error", str(e)))
                wake_overlay()
                continue
            close_voices(voices)
            voices, buffers, voice, last = new_voices, new_buffers, 0, -1
            if not preview:
                continue
        h, hdrs = voices[voice]
        pick = random.randrange(len(hdrs))
        if pick == last and len(hdrs) > 1:               # never the same variant twice in a row
            pick = (pick + 1) % len(hdrs)
        last = pick
        winmm.waveOutReset(h)
        winmm.waveOutWrite(h, ctypes.byref(hdrs[pick]), ctypes.sizeof(WAVEHDR))
        voice = (voice + 1) % VOICES
    close_voices(voices)


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
    cfg = copy.deepcopy(DEFAULTS)
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


# ---------------------------------------------------------------- music control (Spotify, YouTube, ... via Windows)
media_commands = queue.Queue()    # ("play" | "next" | "prev" | "seek_by" | "seek_to", value), None = stop


def pick_session(manager):
    """Spotify if it's open, otherwise whatever Windows thinks is playing right now."""
    sessions = list(manager.get_sessions())
    for session in sessions:
        if "spotify" in (session.source_app_user_model_id or "").lower():
            return session
    return manager.get_current_session() or (sessions[0] if sessions else None)


def timeline_now(timeline, playing):
    pos, end = timeline.position.total_seconds(), timeline.end_time.total_seconds()
    updated = timeline.last_updated_time
    if playing and updated and updated.year > 2000:          # players only report now and then
        pos += max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())
    return (min(pos, end) if end > 0 else pos), end


async def read_cover(props):
    from winrt.windows.storage.streams import DataReader
    try:
        if props.thumbnail is None:
            return None
        stream = await props.thumbnail.open_read_async()
        if not stream.size or stream.size > 8_000_000:
            return None
        reader = DataReader(stream.get_input_stream_at(0))
        n = await reader.load_async(stream.size)
        data = bytearray(n)
        reader.read_bytes(data)
        return bytes(data)
    except Exception:
        return None


async def run_media_command(session, cmd, value):
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status
    if cmd == "play":
        await session.try_toggle_play_pause_async()
    elif cmd == "next":
        await session.try_skip_next_async()
    elif cmd == "prev":
        await session.try_skip_previous_async()
    elif cmd in ("seek_by", "seek_to"):
        playing = session.get_playback_info().playback_status == Status.PLAYING
        pos, end = timeline_now(session.get_timeline_properties(), playing)
        target = pos + value if cmd == "seek_by" else value
        target = max(0.0, min(target, end - 1) if end > 1 else target)
        await session.try_change_playback_position_async(int(target * 10_000_000))


async def media_main():
    import asyncio
    from winrt.windows.media.control import (GlobalSystemMediaTransportControlsSessionManager as Manager,
                                             GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status)
    manager = await Manager.request_async()
    cover_key, cover = None, None
    while True:
        session = pick_session(manager)
        while True:                                       # do what the shortcuts / buttons asked for
            try:
                cmd = media_commands.get_nowait()
            except queue.Empty:
                break
            if cmd is None:
                return
            if session:
                try:
                    await run_media_command(session, *cmd)
                except Exception:
                    pass
        state = None
        if session:
            try:
                props = await session.try_get_media_properties_async()
                info = session.get_playback_info()
                playing = info.playback_status == Status.PLAYING
                pos, end = timeline_now(session.get_timeline_properties(), playing)
                key = (session.source_app_user_model_id, props.title, props.artist)
                if key != cover_key:
                    cover_key, cover = key, await read_cover(props)
                state = {"app": session.source_app_user_model_id or "", "title": props.title or "",
                         "artist": props.artist or props.album_artist or "", "playing": playing,
                         "pos": pos, "end": end, "t": time.monotonic(), "cover": cover, "cover_key": key,
                         "can_seek": bool(info.controls.is_playback_position_enabled) and end > 0}
            except Exception:
                state = None
        ui_events.put(("media", state))
        wake_overlay()
        for _ in range(10):                               # check again in 0.5 s, or sooner for a command
            if not media_commands.empty():
                break
            await asyncio.sleep(0.05)


def media_worker(ready):
    ready.append(None)
    if not MEDIA_OK:
        return
    import asyncio
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    try:
        asyncio.run(media_main())
    except Exception as e:
        print("music control stopped:", e)


# ---------------------------------------------------------------- shortcuts
MOD_NAMES = ((2, "Ctrl"), (1, "Alt"), (4, "Shift"), (8, "Win"))
MODIFIER_IDS = {("vk", v): bit for v, bit in ((0xA0, 4), (0xA1, 4), (0xA2, 2), (0xA3, 2), (0xA4, 1), (0xA5, 1),
                                              (0x5B, 8), (0x5C, 8))}
VK_NAMES = {0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x21: "PgUp", 0x22: "PgDn", 0x23: "End", 0x24: "Home",
            0x2D: "Ins", 0x2E: "Del", 0x0D: "Enter", 0x08: "Backspace", 0x09: "Tab", 0x13: "Pause",
            0xB0: "Media Next", 0xB1: "Media Prev", 0xB3: "Media Play"}


def bind_text(mods, vk):
    if vk == 0x20:
        name = T("space")
    elif vk in VK_NAMES:
        name = VK_NAMES[vk]
    elif 0x70 <= vk <= 0x87:
        name = f"F{vk - 0x6F}"
    elif 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        name = chr(vk)
    else:
        buf = ctypes.create_unicode_buffer(64)
        sc = user32.MapVirtualKeyW(vk, 0)
        name = buf.value if ctypes.windll.user32.GetKeyNameTextW(ctypes.c_long(sc << 16), buf, 64) else f"#{vk}"
    return "+".join([n for bit, n in MOD_NAMES if mods & bit] + [name])


def vk_from_key_id(kid):
    if kid[0] == "vk":
        return kid[1]
    if kid[0] == "sc":
        return user32.MapVirtualKeyW(kid[1] | (0xE000 if kid[2] else 0), 3)     # MAPVK_VSC_TO_VK_EX
    return 0


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
WM_HOTKEY, WM_MOVE = 0x0312, 0x0003
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
gdi32.Ellipse.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Rectangle.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("hwndTrack", wintypes.HWND),
                ("dwHoverTime", wintypes.DWORD)]


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL), ("SuppressExternalCodecs", wintypes.BOOL)]


gdiplus = ctypes.WinDLL("gdiplus")
shlwapi = ctypes.WinDLL("shlwapi")
shlwapi.SHCreateMemStream.argtypes = [ctypes.c_char_p, wintypes.UINT]
shlwapi.SHCreateMemStream.restype = ctypes.c_void_p
_gdiplus_token = ctypes.c_size_t()


class Cover:
    """Album cover decoded with GDI+ (jpg / png), drawn center-cropped into a square."""

    def __init__(self, data):
        if not _gdiplus_token.value:
            gdiplus.GdiplusStartup(ctypes.byref(_gdiplus_token), ctypes.byref(GdiplusStartupInput(1, None, 0, 0)), None)
        self.stream = ctypes.c_void_p(shlwapi.SHCreateMemStream(data, len(data)))
        self.image = ctypes.c_void_p()
        if not self.stream.value or gdiplus.GdipCreateBitmapFromStream(self.stream, ctypes.byref(self.image)):
            self.close()
            raise OSError("cover can't be decoded")
        w, h = ctypes.c_uint(), ctypes.c_uint()
        gdiplus.GdipGetImageWidth(self.image, ctypes.byref(w))
        gdiplus.GdipGetImageHeight(self.image, ctypes.byref(h))
        self.w, self.h = w.value, h.value

    def draw(self, hdc, x, y, size):
        g = ctypes.c_void_p()
        gdiplus.GdipCreateFromHDC(wintypes.HDC(hdc), ctypes.byref(g))
        gdiplus.GdipSetInterpolationMode(g, 7)                     # high quality bicubic
        side = min(self.w, self.h)
        gdiplus.GdipDrawImageRectRectI(g, self.image, x, y, size, size, (self.w - side) // 2, (self.h - side) // 2,
                                       side, side, 2, None, None, None)
        gdiplus.GdipDeleteGraphics(g)

    def close(self):
        if self.image.value:
            gdiplus.GdipDisposeImage(self.image)
            self.image = ctypes.c_void_p()
        if self.stream.value:
            com(self.stream, 2, restype=ctypes.c_ulong)
            self.stream = ctypes.c_void_p()


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
                ("lpstrFilter", wintypes.LPCWSTR), ("lpstrCustomFilter", wintypes.LPWSTR),
                ("nMaxCustFilter", wintypes.DWORD), ("nFilterIndex", wintypes.DWORD),
                ("lpstrFile", wintypes.LPWSTR), ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
                ("nFileExtension", wintypes.WORD), ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR), ("pvReserved", ctypes.c_void_p),
                ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD)]


def pick_sound_file():
    """Standard Windows 'Open' dialog. Returns the chosen path or None."""
    filt = f"{T('pick_filter')}\0*.wav;*.mp3;*.m4a;*.ogg;*.flac;*.wma;*.aac\0\0"
    filt_buf = (ctypes.c_wchar * len(filt))(*filt)
    file_buf = ctypes.create_unicode_buffer(1024)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFilter = ctypes.cast(filt_buf, wintypes.LPCWSTR)
    ofn.lpstrFile = ctypes.cast(file_buf, wintypes.LPWSTR)
    ofn.nMaxFile = 1024
    ofn.lpstrTitle = T("pick_title")
    ofn.Flags = 0x1000 | 0x800 | 0x8                         # FILEMUSTEXIST | PATHMUSTEXIST | NOCHANGEDIR
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return file_buf.value
    return None


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
        self.ripples = []            # (x, y, start time) of "ripple" effect rings
        self.widget = None           # music player panel (created after the keyboard)
        self.capturing = None        # media action waiting for a new shortcut
        self.bind_ok = {}

        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self.wndproc)
        self.class_name = f"KeyboardSoundOverlay{id(self)}"
        wc = WNDCLASSW(0, self._wndproc, 0, 0, hinst, None,
                       user32.LoadCursorW(None, ctypes.c_void_p(32646)), None, None, self.class_name)
        user32.RegisterClassW(ctypes.byref(wc))
        ex = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000 | 0x20   # LAYERED|TOPMOST|TOOLWINDOW|NOACTIVATE|TRANSPARENT
        self.hwnd = user32.CreateWindowExW(ex, self.class_name, "Keyboard overlay", 0x80000000,
                                           0, 0, 10, 10, None, None, hinst, None)
        self.screen_dc = user32.GetDC(self.hwnd)
        self.build()
        if cfg["visible"]:
            user32.ShowWindow(self.hwnd, 4)                      # SW_SHOWNOACTIVATE
        self.widget = MediaWidget(self)
        self.register_binds()
        user32.SetTimer(self.hwnd, 1, int(1000 / FPS), None)
        self.add_tray_icon()
        if not cfg["welcomed"]:
            self.balloon(T("running_title"), T("running_text"))
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

    # ---------- music control
    def binds(self):
        return {**DEFAULTS["binds"], **(self.cfg.get("binds") or {})}

    def register_binds(self):
        for i in range(len(MEDIA_ACTIONS)):
            user32.UnregisterHotKey(self.hwnd, 1000 + i)
        if not MEDIA_OK or self.capturing:
            return
        binds = self.binds()
        for i, act in enumerate(MEDIA_ACTIONS):
            mods, vk = binds[act]
            norepeat = 0 if act in ("fwd", "back") else 0x4000        # holding seek keeps seeking
            self.bind_ok[act] = bool(user32.RegisterHotKey(self.hwnd, 1000 + i, mods | norepeat, vk))

    def media_action(self, act):
        if act in ("fwd", "back"):
            delta = self.cfg["seek_step"] * (1 if act == "fwd" else -1)
            media_commands.put(("seek_by", delta))
            self.widget.nudge(delta)
        else:
            media_commands.put((act, None))
            if act == "play":
                self.widget.toggle_playing()

    def start_capture(self, act):
        self.capturing = act
        self.register_binds()                                     # pause all shortcuts while choosing
        self.widget.prompt = T("bind_prompt").format(T("ma_" + act))
        self.widget.refresh()

    def finish_capture(self, kid):
        vk = vk_from_key_id(kid)
        mods = 0
        for held in self.held:
            mods |= MODIFIER_IDS.get(held, 0)
        if not mods and vk != 0x1B and not (0x70 <= vk <= 0x87 or 0x60 <= vk <= 0x6F or 0xA6 <= vk <= 0xB7
                                            or vk in (0x13, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x91)):
            return                     # a plain letter would block typing -> wait for a combo with Ctrl/Alt/...
        act, self.capturing = self.capturing, None
        self.widget.prompt = None
        if vk and not (vk == 0x1B and not mods):                  # Esc = cancel
            old = self.cfg.get("binds") or {}
            self.cfg["binds"] = {**self.binds(), act: [mods, vk]}
            self.register_binds()
            if not self.bind_ok.get(act):
                user32.MessageBoxW(None, T("bind_busy").format(bind_text(mods, vk)), APP_NAME, 0x30)
                self.cfg["binds"] = old
            save_settings(self.cfg)
        self.register_binds()
        self.widget.refresh()

    def set_visible(self, on):
        self.cfg["visible"] = on
        user32.ShowWindow(self.hwnd, 4 if on else 0)
        if self.widget:
            self.widget.refresh()
        if on:
            self.render(force_base=True)
        save_settings(self.cfg)

    def show_tray_menu(self):
        global sound_enabled
        cfg, add = self.cfg, user32.AppendMenuW
        menu, snd, vol, fx, col = (user32.CreatePopupMenu() for _ in range(5))
        checked = lambda on: 0x8 if on else 0

        add(snd, checked(cfg["sound"]), 103, T("sound_on"))
        add(snd, 0x800, 0, None)
        for i, (key, name) in enumerate(SOUNDS):
            add(snd, checked(cfg["sound_choice"] == key), 200 + i, name)
        if cfg.get("custom_sound") and os.path.exists(cfg["custom_sound"]):
            name = os.path.basename(cfg.get("custom_name") or cfg["custom_sound"])
            add(snd, checked(cfg["sound_choice"] == "custom"), 210, T("own").format(name))
        add(snd, 0, 211, T("upload"))
        add(snd, 0x800, 0, None)
        volumes = (1.0, 0.75, 0.5, 0.25)
        for i, v in enumerate(volumes):
            add(vol, checked(abs(cfg["volume"] - v) < 0.01), 220 + i, f"{int(v * 100)} %")
        add(snd, 0x10, vol, T("volume"))
        for i, name in enumerate(EFFECTS):
            add(fx, checked(cfg["effect"] == name), 300 + i, T("fx_" + name))
        color_names = list(COLORS)
        for i, name in enumerate(color_names):
            add(col, checked(cfg["color"] == name), 400 + i, T("c_" + name))

        add(menu, checked(self.editing), 101, T("move"))
        add(menu, checked(cfg["visible"]), 102, T("show"))
        add(menu, 0x800, 0, None)
        add(menu, 0x10, snd, T("sound"))
        add(menu, 0x10, fx, T("effect"))
        add(menu, 0x10, col, T("color"))
        music, keys_menu, steps = (user32.CreatePopupMenu() for _ in range(3))
        binds = self.binds()
        if MEDIA_OK:
            add(music, checked(cfg["media_widget"]), 500, T("media_widget"))
            add(music, 0x800, 0, None)
            for i, act in enumerate(MEDIA_ACTIONS):
                label = T("ma_" + act)
                if act in ("fwd", "back"):
                    label += f" ({'+' if act == 'fwd' else '-'}{cfg['seek_step']} s)"
                shortcut = bind_text(*binds[act]) + ("" if self.bind_ok.get(act, True) else f" ({T('bind_taken')})")
                add(music, 0, 510 + i, f"{label}\t{shortcut}")
                add(keys_menu, 0, 520 + i, f"{T('ma_' + act)}:  {bind_text(*binds[act])}...")
            add(keys_menu, 0x800, 0, None)
            add(keys_menu, 0, 530, T("reset_binds"))
            add(music, 0x800, 0, None)
            add(music, 0x10, keys_menu, T("change_binds"))
            seek_steps = (5, 10, 15, 30)
            for i, v in enumerate(seek_steps):
                add(steps, checked(cfg["seek_step"] == v), 540 + i, f"{v} s")
            add(music, 0x10, steps, T("seek_step"))
        else:
            add(music, 0x1, 0, T("media_missing"))                # greyed out
        add(menu, 0x10, music, T("media"))
        add(menu, 0x800, 0, None)
        if FROZEN:
            add(menu, checked(autostart_enabled()), 104, T("autostart"))
        add(menu, 0, 106, T("help"))
        add(menu, 0, 105, T("quit"))
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
        elif cmd == 106:
            show_help()
        elif 200 <= cmd < 200 + len(SOUNDS):
            self.set_sound(SOUNDS[cmd - 200][0])
        elif cmd == 210:
            self.set_sound("custom")
        elif cmd == 211:
            self.upload_sound()
        elif 220 <= cmd < 220 + len(volumes):
            cfg["volume"] = volumes[cmd - 220]
            self.set_sound(cfg["sound_choice"])
        elif 300 <= cmd < 300 + len(EFFECTS):
            cfg["effect"] = EFFECTS[cmd - 300]
            self.effect_changed()
        elif 400 <= cmd < 400 + len(color_names):
            cfg["color"] = color_names[cmd - 400]
            self.effect_changed()
        elif cmd == 500:
            cfg["media_widget"] = not cfg["media_widget"]
            save_settings(cfg)
            self.widget.refresh()
        elif 510 <= cmd < 510 + len(MEDIA_ACTIONS):
            self.media_action(MEDIA_ACTIONS[cmd - 510])
        elif 520 <= cmd < 520 + len(MEDIA_ACTIONS):
            self.start_capture(MEDIA_ACTIONS[cmd - 520])
        elif cmd == 530:
            cfg["binds"] = copy.deepcopy(DEFAULTS["binds"])
            save_settings(cfg)
            self.register_binds()
        elif 540 <= cmd < 544:
            cfg["seek_step"] = (5, 10, 15, 30)[cmd - 540]
            save_settings(cfg)

    # ---------- sound & effect settings
    def set_sound(self, choice):
        global sound_enabled
        self.cfg["sound_choice"] = choice
        self.cfg["sound"] = sound_enabled = True
        save_settings(self.cfg)
        play_requests.put(("load", sound_path(self.cfg), self.cfg["volume"], True))   # True = play it once

    def upload_sound(self):
        path = pick_sound_file()
        if not path:
            return
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            ext = os.path.splitext(path)[1].lower() or ".wav"
            target = os.path.join(DATA_DIR, "custom_sound" + ext)
            for old in os.listdir(DATA_DIR):
                if old.startswith("custom_sound") and os.path.join(DATA_DIR, old) != target:
                    os.remove(os.path.join(DATA_DIR, old))
            if os.path.abspath(path) != os.path.abspath(target):
                shutil.copyfile(path, target)
        except OSError as e:
            user32.MessageBoxW(None, T("sound_error").format(e), APP_NAME, 0x30)
            return
        self.cfg["custom_sound"], self.cfg["custom_name"] = target, os.path.basename(path)
        self.set_sound("custom")

    def sound_failed(self, message):
        user32.MessageBoxW(None, T("sound_error").format(message), APP_NAME, 0x30)
        if self.cfg["sound_choice"] == "custom":
            self.set_sound(SOUNDS[0][0])

    def effect_changed(self):
        self.ripples.clear()
        if self.widget:
            self.widget.refresh()
        save_settings(self.cfg)
        self.render(force_base=True)

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
            key["xfrac"] = cx / self.w
            key["cpx"], key["cpy"] = cx, cy
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
        if self.widget:
            self.widget.build()

    # ---------- drawing
    def glow_color(self, xfrac, now):
        """Full-brightness color of a key at this horizontal position (0..1)."""
        c = COLORS.get(self.cfg["color"])
        if c is None:
            moving = self.cfg["effect"] == "wave"
            return colorsys.hsv_to_rgb((xfrac * RGB_SPREAD - (now * RGB_SPEED if moving else 0)) % 1.0, 1.0, 1.0)
        return c

    def base_color(self, xfrac, now):
        """Color of a key that is not pressed (before the idle dimming)."""
        c = self.glow_color(xfrac, now)
        if self.cfg["effect"] == "wave" and COLORS.get(self.cfg["color"]) is not None:
            k = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(2 * math.pi * (xfrac * RGB_SPREAD - now * RGB_SPEED)))
            c = (c[0] * k, c[1] * k, c[2] * k)
        return c

    def idle_level(self, now):
        eff = self.cfg["effect"]
        if eff == "breathe":
            return 0.12 + 0.88 * (0.5 - 0.5 * math.cos(2 * math.pi * now / BREATHE_PERIOD))
        if eff in ("ripple", "reactive"):
            return 0.25
        return 1.0

    def animated(self):
        eff = self.cfg["effect"]
        return eff == "breathe" or (eff == "wave" and RGB_SPEED)

    def add_ripple(self, kid):
        idx = self.by_id.get(kid)
        if idx:
            key = self.keys[idx[0]]
            self.ripples = self.ripples[-11:] + [(key["cpx"], key["cpy"], time.monotonic())]

    def key_colors(self, key, now, glow):
        color = self.glow_color(key["xfrac"], now)
        idle = mix(DARK, color, IDLE_BRIGHTNESS * self.idle_level(now))
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
            r, g, b = mix(self.base_color(i / n, now), WHITE, lift)
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
        level = self.idle_level(now)
        for mask, lift, brightness in ((self.outline_dc, 0.0, IDLE_BRIGHTNESS * level),
                                       (self.text_dc, 0.3, max(level, 0.35))):
            self.rainbow(self.tmp_dc, now, lift, brightness)
            gdi32.BitBlt(self.tmp_dc, 0, 0, w, h, mask, 0, 0, SRCAND)          # keep only outline / text pixels
            gdi32.BitBlt(self.base_dc, 0, 0, w, h, self.tmp_dc, 0, 0, SRCPAINT)  # add them onto the keys

    def render(self, force_base=False):
        now = time.monotonic()
        base_changed = False
        if force_base or (self.animated() and now - self.last_rgb >= 1.0 / RGB_FPS):
            self.render_base(now)
            self.last_rgb = now
            base_changed = True
        if not (base_changed or self.active or self.ripples or self.dirty or self.editing):
            return
        self.dirty = False
        dc = self.back_dc
        gdi32.BitBlt(dc, 0, 0, self.w, self.h, self.base_dc, 0, 0, SRCCOPY)
        glows = {}
        for i in list(self.active):
            kid = self.keys[i]["id"]
            glow = 1.0 if kid in self.held else max(0.0, 1.0 - (now - self.released_at.get(kid, -99.0)) / GLOW_TIME)
            if glow <= 0:
                self.active.discard(i)
                self.dirty = True        # draw once more without it
                continue
            glows[i] = glow
        if self.ripples:                 # rings of light travelling out from pressed keys
            self.ripples = [r for r in self.ripples if now - r[2] < RIPPLE_LIFE]
            band, speed = 1.3 * self.u, RIPPLE_SPEED * self.u
            for rx, ry, t0 in self.ripples:
                age = now - t0
                radius, fade = age * speed, 1.0 - age / RIPPLE_LIFE
                for i, key in enumerate(self.keys):
                    if key.get("body"):
                        continue
                    x = 1.0 - abs(math.hypot(key["cpx"] - rx, key["cpy"] - ry) - radius) / band
                    if x > 0 and 0.85 * x * fade > glows.get(i, 0.0):
                        glows[i] = 0.85 * x * fade
            if not self.ripples:
                self.dirty = True
        for i, glow in glows.items():
            self.draw_key(dc, self.keys[i], self.key_colors(self.keys[i], now, glow))
        if self.editing:
            self.draw_edit_hint(dc)
        gdi32.BitBlt(self.screen_dc, 0, 0, self.w, self.h, dc, 0, 0, SRCCOPY)

    def draw_edit_hint(self, dc):
        gdi32.SelectObject(dc, gdi32.GetStockObject(5))                     # hollow brush
        gdi32.SetDCPenColor(dc, self.ACCENT)
        gdi32.Polygon(dc, *self.plate)
        gdi32.SelectObject(dc, gdi32.GetStockObject(18))
        text = T("edit_hint")
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
            if msg == WM_MOVE and self.widget:             # player follows the keyboard while dragging
                self.widget.follow()
            if msg == WM_HOTKEY and 1000 <= wparam < 1000 + len(MEDIA_ACTIONS):
                self.media_action(MEDIA_ACTIONS[wparam - 1000])
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
                if kind == "down" and self.capturing and val not in MODIFIER_IDS and val[0] != "m":
                    self.finish_capture(val)
                    self.held.add(val)
                elif kind == "down":
                    self.held.add(val)
                    self.active.update(self.by_id.get(val, ()))
                    if self.cfg["effect"] == "ripple":
                        self.add_ripple(val)
                elif kind in ("up", "tap"):
                    self.held.discard(val)
                    self.released_at[val] = time.monotonic()
                    self.active.update(self.by_id.get(val, ()))
                    if kind == "tap" and self.cfg["effect"] == "ripple":
                        self.add_ripple(val)
                elif kind == "sound_error":
                    self.sound_failed(val)
                elif kind == "media":
                    self.widget.set_state(val)
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
        self.widget.update()
        self.render()
        if from_timer:
            self.ticks += 1
            if self.ticks % FPS == 0:                     # once a second
                if self.refresh_labels():
                    self.build_layers()
                    self.render(force_base=True)
                user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
                self.widget.keep_on_top()

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
        add(menu, 0, 1, T("numpad_hide" if cfg["numpad"] else "numpad_show"))
        add(menu, 0, 2, T("mouse_hide" if cfg["mouse"] else "mouse_show"))
        add(menu, 0, 3, T("mouse_left" if cfg["mouse_side"] == "right" else "mouse_right"))
        add(menu, 0, 6, T("bg_hide" if cfg["background"] else "bg_show"))
        add(menu, 0x800, 0, None)
        add(menu, 0, 4, T("bigger"))
        add(menu, 0, 5, T("smaller"))
        levels = (1.0, 0.9, 0.75, 0.6, 0.45)
        for i, v in enumerate(levels):
            add(opacity, 0x8 if abs(cfg["opacity"] - v) < 0.01 else 0, 10 + i, f"{int(v * 100)} %")
        add(menu, 0x10, opacity, T("opacity"))
        names = ("top-left", "top-right", "bottom-left", "bottom-right")
        for i, c in enumerate(names):
            add(corners, 0, 20 + i, T(c))
        add(menu, 0x10, corners, T("corner"))
        add(menu, 0x800, 0, None)
        add(menu, 0, 30, T("done"))
        add(menu, 0, 32, T("help"))
        add(menu, 0, 31, T("quit_plain"))
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
        elif cmd == 32:
            show_help()
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
        for i in range(len(MEDIA_ACTIONS)):
            user32.UnregisterHotKey(self.hwnd, 1000 + i)
        if self.widget:
            self.widget.close()
        shell32.Shell_NotifyIconW(2, ctypes.byref(self.tray_data()))   # NIM_DELETE
        user32.ReleaseDC(self.hwnd, self.screen_dc)
        user32.DestroyWindow(self.hwnd)
        ctypes.windll.user32.UnregisterClassW(ctypes.c_wchar_p(self.class_name),
                                              ctypes.c_void_p(kernel32.GetModuleHandleW(None)))


# ---------------------------------------------------------------- now-playing panel
class MediaWidget:
    """Small player above the keyboard: cover, title, artist, buttons and a progress bar you can click."""
    BG = (0.05, 0.05, 0.07)

    def __init__(self, overlay):
        self.ov = overlay
        self.state = None
        self.prompt = None
        self.cover, self.cover_key = None, None
        self.hover = None
        self.shown = False
        self.last_draw = 0.0
        self.fonts, self.dc, self.bmp = [], None, None
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self.wndproc)
        self.class_name = f"KeyboardSoundPlayer{id(self)}"
        wc = WNDCLASSW(0, self._wndproc, 0, 0, hinst, None, user32.LoadCursorW(None, ctypes.c_void_p(32649)),
                       None, None, self.class_name)                      # hand cursor
        user32.RegisterClassW(ctypes.byref(wc))
        ex = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000           # LAYERED|TOPMOST|TOOLWINDOW|NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, self.class_name, "Keyboard Sound player", 0x80000000,
                                           0, 0, 10, 10, None, None, hinst, None)
        self.screen_dc = user32.GetDC(self.hwnd)
        self.build()

    # ---------- layout
    def build(self):
        u = self.ov.u
        self.w, self.h = int(12 * u), int(2.5 * u)
        pad = self.pad = int(0.25 * u)
        self.art = self.h - 2 * pad
        tx = pad + self.art + int(0.35 * u)
        right = self.w - pad
        b = int(1.0 * u)
        cy = pad + int(0.55 * u)
        self.buttons = {name: (right - b // 2 - k * (b + int(0.12 * u)), cy, b // 2)
                        for k, name in enumerate(("next", "play", "prev"))}
        left_of_buttons = right - 3 * b - int(0.5 * u)
        self.title_rect = wintypes.RECT(tx, pad, left_of_buttons, pad + int(0.62 * u))
        self.artist_rect = wintypes.RECT(tx, pad + int(0.62 * u), left_of_buttons, pad + int(1.12 * u))
        self.time_rect = wintypes.RECT(left_of_buttons, pad + int(1.1 * u), right, pad + int(1.5 * u))
        self.bar = (tx, self.h - pad - int(0.3 * u), right - tx, max(4, int(0.2 * u)))
        self.prompt_rect = wintypes.RECT(tx, pad, right, self.h - pad)

        for f in self.fonts:
            gdi32.DeleteObject(f)
        mk = lambda px, weight, face: gdi32.CreateFontW(-max(7, int(px)), 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 4, 0, face)
        self.fonts = [mk(u * 0.42, 700, "Segoe UI"), mk(u * 0.34, 400, "Segoe UI"),
                      mk(u * 0.3, 600, "Segoe UI"), mk(u * 0.42, 400, "Segoe MDL2 Assets"),
                      mk(u * 0.8, 400, "Segoe UI Symbol")]
        if self.dc:
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.dc)
        self.dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self.bmp = gdi32.CreateCompatibleBitmap(self.screen_dc, self.w, self.h)
        gdi32.SelectObject(self.dc, self.bmp)
        gdi32.SelectObject(self.dc, gdi32.GetStockObject(18))
        gdi32.SelectObject(self.dc, gdi32.GetStockObject(19))
        gdi32.SetBkMode(self.dc, 1)
        self.plate = self.poly([(0, 0), (self.w - 1, 0), (self.w - 1, self.h - 1), (0, self.h - 1)], 0.35 * u)
        user32.SetLayeredWindowAttributes(self.hwnd, TRANSPARENT_KEY, int(self.ov.cfg["opacity"] * 255), 3)
        self.follow()
        self.refresh()

    def poly(self, pts, r):
        flat = rounded_points(pts, [r] * len(pts), 4)
        arr = (wintypes.POINT * (len(flat) // 2))(*[wintypes.POINT(int(flat[i]), int(flat[i + 1]))
                                                    for i in range(0, len(flat), 2)])
        return arr, len(arr)

    def follow(self):
        """Sit right above the keyboard (or below it when the keyboard is at the top of the screen)."""
        r, area = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(self.ov.hwnd, ctypes.byref(r))
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)
        gap = int(0.25 * self.ov.u)
        y = r.top - self.h - gap
        if y < area.top:
            y = r.bottom + gap
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), r.left, y, self.w, self.h, 0x10)

    def keep_on_top(self):
        if self.shown:
            user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

    # ---------- state
    def set_state(self, state):
        if state and state.get("cover_key") != self.cover_key:
            self.cover_key = state.get("cover_key")
            if self.cover:
                self.cover.close()
                self.cover = None
            if state.get("cover"):
                try:
                    self.cover = Cover(state["cover"])
                except OSError:
                    self.cover = None
        if state and self.state and time.monotonic() < getattr(self, "hold_until", 0):
            state = {**state, "pos": self.position(), "t": time.monotonic(), "playing": self.state["playing"]}
        self.state = state
        self.refresh()

    def position(self):
        st = self.state
        if not st:
            return 0.0
        pos = st["pos"] + (time.monotonic() - st["t"] if st["playing"] else 0.0)
        return min(pos, st["end"]) if st["end"] > 0 else pos

    def nudge(self, delta):
        """Move the bar right away; the player confirms a moment later."""
        if self.state and self.state["can_seek"]:
            self.state = {**self.state, "pos": max(0.0, self.position() + delta), "t": time.monotonic()}
            self.hold_until = time.monotonic() + 0.8
            self.refresh()

    def toggle_playing(self):
        if self.state:
            self.state = {**self.state, "pos": self.position(), "t": time.monotonic(),
                          "playing": not self.state["playing"]}
            self.hold_until = time.monotonic() + 0.8
            self.refresh()

    def refresh(self):
        cfg = self.ov.cfg
        want = bool(MEDIA_OK and cfg["visible"] and cfg["media_widget"] and (self.state or self.prompt))
        if self.prompt and not (cfg["visible"] and cfg["media_widget"]):
            want = True                                    # always show the "press a shortcut" message
        if want != self.shown:
            self.shown = want
            if want:
                self.follow()
            user32.ShowWindow(self.hwnd, 4 if want else 0)
        if want:
            self.draw()

    def update(self):
        if self.shown and self.state and self.state["playing"] and time.monotonic() - self.last_draw > 0.25:
            self.draw()

    # ---------- drawing
    def draw(self):
        self.last_draw = time.monotonic()
        dc, u, ov = self.dc, self.ov.u, self.ov
        now = time.monotonic()
        accent = ov.glow_color(0.15, now)
        gdi32.SetDCBrushColor(dc, TRANSPARENT_KEY)
        gdi32.SetDCPenColor(dc, TRANSPARENT_KEY)
        gdi32.Polygon(dc, (wintypes.POINT * 4)((0, 0), (self.w + 1, 0), (self.w + 1, self.h + 1), (0, self.h + 1)), 4)
        gdi32.SetDCBrushColor(dc, cref(self.BG))
        gdi32.SetDCPenColor(dc, cref(mix(self.BG, accent, 0.55)))
        gdi32.Polygon(dc, *self.plate)

        pad, art = self.pad, self.art
        if self.cover:
            self.cover.draw(dc, pad, pad, art)
        else:
            gdi32.SetDCBrushColor(dc, cref((0.12, 0.12, 0.16)))
            gdi32.SetDCPenColor(dc, cref((0.12, 0.12, 0.16)))
            gdi32.Polygon(dc, *self.poly([(pad, pad), (pad + art, pad), (pad + art, pad + art), (pad, pad + art)],
                                         0.2 * u))
            gdi32.SelectObject(dc, self.fonts[4])
            gdi32.SetTextColor(dc, cref(accent))
            user32.DrawTextW(dc, "♪", -1, ctypes.byref(wintypes.RECT(pad, pad, pad + art, pad + art)), 0x1 | 0x4 | 0x20)

        if self.prompt:
            gdi32.SelectObject(dc, self.fonts[0])
            gdi32.SetTextColor(dc, 0xFFFFFF)
            user32.DrawTextW(dc, self.prompt, -1, ctypes.byref(self.prompt_rect), 0x10 | 0x4 | 0x800 | 0x20 * 0)
            self.present()
            return
        st = self.state
        if not st:
            self.present()
            return
        ellipsis = 0x20 | 0x4 | 0x800 | 0x8000                    # single line, vcenter, no prefix, "..."
        gdi32.SelectObject(dc, self.fonts[0])
        gdi32.SetTextColor(dc, 0xFFFFFF)
        user32.DrawTextW(dc, st["title"] or T("nothing_playing"), -1, ctypes.byref(self.title_rect), ellipsis)
        gdi32.SelectObject(dc, self.fonts[1])
        gdi32.SetTextColor(dc, cref((0.66, 0.66, 0.74)))
        user32.DrawTextW(dc, st["artist"], -1, ctypes.byref(self.artist_rect), ellipsis)

        pos, end = self.position(), st["end"]
        fmt = lambda v: f"{int(v) // 60}:{int(v) % 60:02d}"
        gdi32.SelectObject(dc, self.fonts[2])
        gdi32.SetTextColor(dc, cref((0.66, 0.66, 0.74)))
        time_text = f"{fmt(pos)} / {fmt(end)}" if end > 0 else ""
        user32.DrawTextW(dc, time_text, -1, ctypes.byref(self.time_rect), 0x2 | 0x20 | 0x4 | 0x800)

        for name, (cx, cy, r) in self.buttons.items():          # ⏮ ⏯ ⏭
            hot = self.hover == name
            fill = mix(self.BG, accent, 0.85) if name == "play" else (mix(self.BG, WHITE, 0.14) if hot else self.BG)
            if name == "play" and hot:
                fill = mix(accent, WHITE, 0.25)
            gdi32.SetDCBrushColor(dc, cref(fill))
            gdi32.SetDCPenColor(dc, cref(fill))
            gdi32.Ellipse(dc, cx - r, cy - r, cx + r, cy + r)
            glyph = {"prev": "\ue892", "next": "\ue893", "play": "\ue769" if st["playing"] else "\ue768"}[name]
            gdi32.SelectObject(dc, self.fonts[3])
            gdi32.SetTextColor(dc, cref(INK if name == "play" else (0.92, 0.92, 0.96)))
            user32.DrawTextW(dc, glyph, -1, ctypes.byref(wintypes.RECT(cx - r, cy - r, cx + r, cy + r)), 0x1 | 0x4 | 0x20)

        x, y, w, h = self.bar                                    # progress bar
        gdi32.SetDCBrushColor(dc, cref((0.17, 0.17, 0.22)))
        gdi32.SetDCPenColor(dc, cref((0.17, 0.17, 0.22)))
        gdi32.Polygon(dc, *self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], h / 2))
        frac = min(1.0, pos / end) if end > 0 else 0.0
        filled = int(w * frac)
        if filled > 2:
            dim = 1.0 if st["can_seek"] else 0.55
            for i in range(0, filled, 3):                        # rainbow / effect colored fill
                c = ov.glow_color(0.05 + 0.9 * i / max(1, w), now)
                gdi32.SetDCBrushColor(dc, cref((c[0] * dim, c[1] * dim, c[2] * dim)))
                gdi32.SetDCPenColor(dc, cref((c[0] * dim, c[1] * dim, c[2] * dim)))
                gdi32.Rectangle(dc, x + i, y + 1, x + min(i + 3, filled), y + h - 1)
            if self.hover == "bar" and st["can_seek"]:
                gdi32.SetDCBrushColor(dc, 0xFFFFFF)
                gdi32.SetDCPenColor(dc, 0xFFFFFF)
                gdi32.Ellipse(dc, x + filled - h, y - h // 2, x + filled + h, y + h + h // 2)
        self.present()

    def present(self):
        gdi32.BitBlt(self.screen_dc, 0, 0, self.w, self.h, self.dc, 0, 0, SRCCOPY)

    # ---------- mouse
    def hit(self, mx, my):
        for name, (cx, cy, r) in self.buttons.items():
            if (mx - cx) ** 2 + (my - cy) ** 2 <= r * r:
                return name
        x, y, w, h = self.bar
        if x - h <= mx <= x + w + h and y - int(0.35 * self.ov.u) <= my <= y + h + int(0.35 * self.ov.u):
            return "bar"
        return None

    def wndproc(self, hwnd, msg, wparam, lparam):
        try:
            mx, my = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
            if msg == 0x0201 and self.state and not self.prompt:           # left click
                target = self.hit(mx, my)
                if target in ("prev", "next", "play"):
                    self.ov.media_action(target)
                elif target == "bar" and self.state["can_seek"]:
                    x, _, w, _ = self.bar
                    seconds = max(0.0, min(1.0, (mx - x) / w)) * self.state["end"]
                    media_commands.put(("seek_to", seconds))
                    self.nudge(seconds - self.position())
                return 0
            if msg == 0x0200:                                              # mouse move
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x2, hwnd, 0)
                ctypes.windll.user32.TrackMouseEvent(ctypes.byref(tme))
                target = self.hit(mx, my)
                if target != self.hover:
                    self.hover = target
                    self.draw()
                return 0
            if msg == 0x02A3:                                              # mouse left the player
                self.hover = None
                self.draw()
                return 0
            if msg == WM_PAINT:
                ps = ctypes.create_string_buffer(72)
                hdc = user32.BeginPaint(hwnd, ps)
                gdi32.BitBlt(hdc, 0, 0, self.w, self.h, self.dc, 0, 0, SRCCOPY)
                user32.EndPaint(hwnd, ps)
                return 0
        except Exception as e:
            print("player error:", e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def close(self):
        if self.cover:
            self.cover.close()
        user32.ReleaseDC(self.hwnd, self.screen_dc)
        user32.DestroyWindow(self.hwnd)
        ctypes.windll.user32.UnregisterClassW(ctypes.c_wchar_p(self.class_name),
                                              ctypes.c_void_p(kernel32.GetModuleHandleW(None)))


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


def show_help():
    user32.MessageBoxW(None, T("help_text"), T("help_title"), 0x40)


def main():
    global overlay_hwnd, sound_enabled
    mutex = kernel32.CreateMutexW(None, False, "KeyboardSound_SingleInstance")
    if ctypes.get_last_error() == 183:                   # ERROR_ALREADY_EXISTS
        user32.MessageBoxW(None, T("already"), APP_NAME, 0x40)
        return
    if not os.path.isdir(SOUNDS_DIR):
        error_box(f"Sound files not found:\n{SOUNDS_DIR}")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # crisp overlay on scaled displays
    except Exception:
        pass
    sys.setswitchinterval(0.001)                         # react to keys faster
    ctypes.windll.ole32.CoInitializeEx(None, 2)          # single-threaded COM for the file dialog

    cfg = load_settings()
    sound_enabled = cfg["sound"]
    try:
        audio = start(audio_worker, sound_path(cfg), cfg["volume"])
    except Exception:                                    # e.g. own sound deleted -> use the default one
        cfg["sound_choice"] = SOUNDS[0][0]
        audio = start(audio_worker, sound_path(cfg), cfg["volume"])
    overlay = Overlay(cfg)
    overlay_hwnd = overlay.hwnd
    start(input_loop, True)
    start(media_worker)

    signal.signal(signal.SIGINT, lambda *_: user32.PostQuitMessage(0))
    print("Keyboard sounds + RGB overlay ON.")
    print("  Ctrl+Alt+F11  move / resize / options      Ctrl+Alt+F12 (or Ctrl+C here)  quit")
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        media_commands.put(None)
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
