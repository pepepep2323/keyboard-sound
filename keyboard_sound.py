"""
Keyboard Sound - click sounds + RGB widgets for Windows.

Plays a click every time you press a key and shows movable widgets on screen:
an RGB (or pastel / black) keyboard and mouse that light up, a music player (Spotify, YouTube, ...),
stats (CPS / APM), a clicking cat, a session timer, Rocket League and Geometry Dash widgets,
your own texts and images.

Run:    python keyboard_sound.py   (or KeyboardSound.exe)
Games:  tray icon -> Games: Rocket League borderless fullscreen + live data (official Stats API, TCP 49123)
Tray:   right-click the tray icon for everything
Move:   Ctrl+Alt+F11 -> drag widgets, mouse wheel = size, right-click = options, Ctrl+Alt+F11 again = done
Stop:   Ctrl+Alt+F12 anywhere, tray icon -> Quit, or Ctrl+C in the console
Music:  Ctrl+Alt+Down play/pause, Ctrl+Alt+Left/Right previous/next, Ctrl+Alt+Shift+Left/Right seek,
        Ctrl+Alt+Shift+Up/Down music volume (needs the "winrt-Windows.Media.Control" and
        "winrt-Windows.Storage.Streams" packages when run from source)

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
import socket
import codecs
import sys
import threading
import time
import wave
from collections import deque
import importlib.util
from datetime import datetime, timezone
import winreg
from ctypes import wintypes

# ---------------------------------------------------------------- settings
APP_NAME = "Keyboard Sound"
WIDGET_TYPES = ["keyboard", "mouse", "player", "stats", "cat", "session", "clock", "drink", "rocket", "scoreboard",
                "boost", "dash", "fnmatch", "chests", "text", "image"]
RL_STYLE_KINDS = ("scoreboard", "boost")      # these share look, font and opacity
RL_STYLE_KEYS = ("mode", "font", "bold", "opacity")
FROZEN = getattr(sys, "frozen", False)               # True when running as KeyboardSound.exe
HERE = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", HERE)             # where bundled files are inside the .exe
SOUNDS_DIR = os.path.join(RES_DIR, "sounds")        # built-in click sounds
ICON_FILE = os.path.join(RES_DIR, "icon.ico")
DATA_DIR = os.path.join(os.environ.get("APPDATA", HERE), "KeyboardSound") if FROZEN else HERE
SETTINGS_FILE = os.path.join(DATA_DIR, "overlay_settings.json")

VOICES = 8             # how many clicks can overlap when typing fast
PITCH_VARIATION = 0.04 # small random pitch change per click, sounds natural (0 = off)

# defaults (saved to overlay_settings.json; most of it is changed from the tray menu / Ctrl+Alt+F11)
DEFAULTS = {
    "visible": True,             # all widgets shown (tray icon -> Show widgets)
    "corner": "bottom-left",     # where the widgets start: "top-left", "top-right", "bottom-left", "bottom-right"
    "opacity": 0.9,
    "sound": True,               # click sound on (tray icon -> Sound)
    "sound_choice": "creamy-1",  # one of SOUNDS below, or "custom"
    "custom_sound": None,        # path of your own sound (copied into the settings folder)
    "custom_name": None,         # original file name of your own sound (shown in the menu)
    "volume": 1.0,               # click volume 0.1 .. 2.0 (above 1.0 = louder than the sound file)
    "mouse_sound": False,        # also click on mouse buttons (tray icon -> Sound)
    "effect": "wave",            # wave, static, breathe, ripple, reactive
    "color": "rainbow",          # rainbow or one of COLORS below
    "seek_step": 10,             # seconds to jump with the seek shortcuts
    "music_step": 5,             # % of music volume per shortcut / mouse wheel step
    "mute_ads": True,            # mute Spotify while an ad plays
    "binds": {                   # [modifiers, virtual key]; modifiers: Alt=1, Ctrl=2, Shift=4, Win=8
        "play": [3, 0x28],       # Ctrl+Alt+Down
        "next": [3, 0x27],       # Ctrl+Alt+Right
        "prev": [3, 0x25],       # Ctrl+Alt+Left
        "fwd": [7, 0x27],        # Ctrl+Alt+Shift+Right
        "back": [7, 0x25],       # Ctrl+Alt+Shift+Left
        "vol_up": [7, 0x26],     # Ctrl+Alt+Shift+Up
        "vol_down": [7, 0x28],   # Ctrl+Alt+Shift+Down
    },
    "widgets": None,             # every widget's position / size / options (made on first start)
    "stats": {"date": "", "keys": 0, "clicks": 0, "best_cps": 0, "cat": 0, "rl_w": 0, "rl_l": 0, "gd_jumps": 0,
              "gd_total": 0, "fn_match": 0, "fn_today": 0, "fn_total": 0, "fn_best": 0,
              "fnm_count": 0, "fnm_time": 0, "fnm_best": 0, "drinks": 0},
    "welcomed": False,           # first-start tip already shown
    "obs": False,                # widgets for OBS (local web page on 127.0.0.1, tray icon -> OBS)
    "theme": "classic",          # look of all widgets: classic, pastel, black, neon, glass, minimal, custom
    "font": "Segoe UI",          # font of all widgets (each widget can have its own)
    "custom_theme": {            # your own theme (main menu -> Theme)
        "bg": "#12131c", "bg_a": 0.85, "border": "#7d9bff", "text": "#ecedf5", "muted": "#9a9cb2",
        "nums": "effect", "radius": 0.45,
    },
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
MEDIA_ACTIONS = ["play", "next", "prev", "fwd", "back", "vol_up", "vol_down"]
MEDIA_OK = importlib.util.find_spec("winrt.windows.media.control") is not None
OCR_OK = importlib.util.find_spec("winrt.windows.media.ocr") is not None      # Windows text recognition

CLICK_VOLUMES = [2.0, 1.75, 1.5, 1.25, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
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

WIDGETY (klávesnice, myš, přehrávač, statistiky, kočka, čas hraní,
         Rocket League, Geometry Dash, text, obrázek)
  Ctrl+Alt+F11 = úpravy (nebo menu > Přesunout / změnit velikost)
  - každý widget táhni myší zvlášť
  - kolečko nad widgetem = jeho velikost
  - pravý klik na widget = jeho možnosti (skrýt, barva, text...)
  - znovu Ctrl+Alt+F11 = hotovo (uloží se)
  Zapnout / vypnout widgety: menu > Widgety
  Přidat text nebo obrázek: menu > Widgety > Přidat...
  Vzhled klávesnice (RGB, pastelová, normální černá):
  menu > Vzhled klávesnice, nebo pravý klik na klávesnici.

ZVUK KLÁVES
  Menu > Zvuk: zapnout, Creamy 1 / 2 / 3, vlastní zvuk
  (wav, mp3, m4a, ogg, flac, wma), hlasitost kliků,
  cvakání i při klikání myší

HUDBA (SPOTIFY, YOUTUBE...)
  Ctrl+Alt+Šipka dolů               přehrát / pauza
  Ctrl+Alt+Šipka vpravo / vlevo     další / předchozí skladba
  Ctrl+Alt+Shift+Šipka vpravo/vlevo posun o 10 s
  Ctrl+Alt+Shift+Šipka nahoru/dolů  hlasitost hudby
  Kolečko nad přehrávačem = hlasitost hudby,
  klik na reproduktor = ztlumit.
  Reklamy ve Spotify se samy ztlumí (menu > Hudba).
  Zkratky změníš v menu > Hudba > Změnit zkratky.

STATISTIKY
  CPS = kliky za sekundu, APM = akce (klávesy + kliky) za minutu.

VYPNOUT
  Ctrl+Alt+F12, nebo menu > Ukončit

HRY
  Widgety jsou vidět jen v režimu "okno bez okraje"
  (borderless), ne v exkluzivní celé obrazovce.
  Rocket League přepneš jedním klikem: menu > Hry.
  Skóre Rocket League = počítadlo skóre a času nahoře uprostřed, stejně
  velké jako to ve hře. 3 vzhledy (vylepšený, pastelový RGB, černý RGB)
  a libovolné písmo: Ctrl+Alt+F11 a pravý klik na něj.
  Widget Rocket League ukazuje skóre, čas, tvůj boost a výhry
  (zapni menu > Hry > živá data, pak restartuj hru).
  Widget Geometry Dash počítá skoky a čas hraní.

Nastavení se ukládá do %APPDATA%\\KeyboardSound."""

HELP_EN = """KEYBOARD SOUND - HELP

MENU
  Right-click the keyboard icon near the clock (bottom right).
  (If you can't see it, click the ^ arrow next to the clock.)

WIDGETS (keyboard, mouse, player, stats, cat, session timer,
         Rocket League, Geometry Dash, text, image)
  Ctrl+Alt+F11 = edit mode (or menu > Move / resize)
  - drag every widget on its own
  - mouse wheel over a widget = its size
  - right-click a widget = its options (hide, color, text...)
  - Ctrl+Alt+F11 again = done (it's saved)
  Turn widgets on / off: menu > Widgets
  Add a text or an image: menu > Widgets > Add...
  Keyboard look (RGB, pastel, normal black):
  menu > Keyboard look, or right-click the keyboard.

KEY SOUND
  Menu > Sound: on/off, Creamy 1 / 2 / 3, your own sound
  (wav, mp3, m4a, ogg, flac, wma), click volume,
  click sound on mouse buttons too

MUSIC (SPOTIFY, YOUTUBE...)
  Ctrl+Alt+Down arrow                play / pause
  Ctrl+Alt+Right / Left arrow        next / previous track
  Ctrl+Alt+Shift+Right / Left arrow  seek 10 s
  Ctrl+Alt+Shift+Up / Down arrow     music volume
  Mouse wheel over the player = music volume,
  click the speaker = mute.
  Spotify ads are muted automatically (menu > Music).
  Change the shortcuts in menu > Music > Change shortcuts.

STATS
  CPS = clicks per second, APM = actions (keys + clicks) per minute.

QUIT
  Ctrl+Alt+F12, or menu > Quit

GAMES
  Widgets are only visible in borderless / windowed
  fullscreen, not in exclusive fullscreen.
  Rocket League can be switched with one click: menu > Games.
  Rocket League score = a score and time counter at the top center, the
  same size as the game's own. 3 looks (enhanced, pastel RGB, black RGB)
  and any font: Ctrl+Alt+F11, then right-click it.
  The Rocket League widget shows the score, time, your boost and wins
  (turn on menu > Games > live data, then restart the game).
  The Geometry Dash widget counts your jumps and play time.

Settings are saved in %APPDATA%\\KeyboardSound."""

TEXTS = {
    "cs": {
        "move": "Přesunout / změnit velikost\tCtrl+Alt+F11", "show": "Zobrazit widgety",
        "sound": "Zvuk", "sound_on": "Zvuk zapnutý", "own": "Vlastní: {}", "upload": "Nahrát vlastní zvuk...",
        "volume": "Hlasitost", "effect": "Efekt", "color": "Barva", "autostart": "Spouštět s Windows",
        "help": "Nápověda", "quit": "Ukončit\tCtrl+Alt+F12",
        "fx_wave": "Duhová vlna", "fx_static": "Statické světlo", "fx_breathe": "Dýchání",
        "fx_ripple": "Vlna od klávesy", "fx_reactive": "Jen stisknuté klávesy",
        "c_rainbow": "Duha", "c_red": "Červená", "c_orange": "Oranžová", "c_yellow": "Žlutá",
        "c_green": "Zelená", "c_cyan": "Tyrkysová", "c_blue": "Modrá", "c_purple": "Fialová",
        "c_pink": "Růžová", "c_white": "Bílá",
        "running_title": "Keyboard Sound běží",
        "running_text": "Klik na tuhle ikonu = hlavní menu, pravý klik = rychlé menu.\nCtrl+Alt+F11 = přesunout widgety, Ctrl+Alt+F12 = vypnout.",
        "already": "Keyboard Sound už běží.\nNajdeš ho jako ikonu klávesnice vpravo dole u hodin.",
        "sound_error": "Tenhle zvuk nejde přehrát:\n{}\n\nZkus jiný soubor (wav, mp3, m4a, ogg, flac, wma).",
        "pick_title": "Vyber zvuk kláves", "pick_filter": "Zvuky",
        "edit_hint": "Táhni = přesun  •  Kolečko = velikost  •  Pravý klik = možnosti  •  Ctrl+Alt+F11 = hotovo",
        "numpad_hide": "Skrýt numpad", "numpad_show": "Zobrazit numpad",
        "bg_hide": "Skrýt pozadí", "bg_show": "Zobrazit pozadí",
        "bigger": "Větší", "smaller": "Menší", "opacity": "Průhlednost",
        "top-left": "vlevo nahoře", "top-right": "vpravo nahoře",
        "bottom-left": "vlevo dole", "bottom-right": "vpravo dole",
        "done": "Hotovo (zamknout pozice)", "quit_plain": "Ukončit", "help_title": "Keyboard Sound - nápověda",
        "help_text": HELP_CS,
        "media": "Hudba (Spotify, YouTube...)",
        "ma_play": "Přehrát / pauza", "ma_next": "Další skladba", "ma_prev": "Předchozí skladba",
        "ma_fwd": "Posunout vpřed", "ma_back": "Posunout zpět",
        "ma_vol_up": "Hlasitost hudby +", "ma_vol_down": "Hlasitost hudby -",
        "change_binds": "Změnit zkratky", "reset_binds": "Obnovit výchozí zkratky", "seek_step": "Krok posunu",
        "music_step": "Krok hlasitosti", "mute_ads": "Ztlumit reklamy (Spotify)",
        "bind_prompt": "Stiskni novou zkratku pro: {}\n(s Ctrl / Alt / Shift, nebo F-klávesu.  Esc = zrušit)",
        "bind_busy": "Zkratku {} už používá jiný program.\nZkus jinou.", "bind_taken": "obsazeno",
        "media_missing": "Ovládání hudby není k dispozici", "space": "Mezerník",
        "nothing_playing": "Nic nehraje", "ad_muted": "Reklama - ztlumeno", "ad_playing": "Reklama",
        "click_volume": "Hlasitost kliků\tCtrl+Alt+PgUp / PgDn",
        "widgets": "Widgety", "w_keyboard": "Klávesnice", "w_mouse": "Myš", "w_player": "Přehrávač",
        "w_stats": "Statistiky", "w_cat": "Kočka", "w_text": "Text", "w_image": "Obrázek", "w_hint": "",
        "add_text": "Přidat text...", "add_image": "Přidat obrázek...", "reset_layout": "Rozmístit",
        "hide_widget": "Skrýt tento widget", "remove_widget": "Odstranit widget",
        "edit_text": "Upravit text...", "text_color": "Barva", "tc_effect": "podle efektu", "bold": "Tučně",
        "text_bg": "Pozadí", "change_image": "Změnit obrázek...", "image_title": "Vyber obrázek",
        "image_filter": "Obrázky", "image_missing": "Obrázek nenalezen",
        "text_prompt": "Napiš text (Enter = nový řádek):", "default_text": "Ahoj!", "ok": "OK", "cancel": "Zrušit",
        "reset_today": "Vynulovat dnešek", "reset_best": "Vynulovat rekord", "reset_cat": "Vynulovat počítadlo",
        "fur": "Kočka", "fur_orange": "zrzavá", "fur_gray": "šedá", "fur_black": "černá", "fur_white": "bílá",
        "fur_rgb": "RGB", "stats_best": "rekord {}", "stats_apm_sub": "akcí za minutu",
        "stats_today": "Dnes: {} kláves · {} kliků",
        "mouse_sound": "Cvakat i při klikání myší",
        "style": "Vzhled klávesnice", "st_rgb": "RGB", "st_pastel": "Pastelová", "st_black": "Normální černá",
        "w_session": "Čas hraní", "w_rocket": "Rocket League", "w_dash": "Geometry Dash",
        "session_reset": "Vynulovat čas", "session_pause": "Pozastavit", "session_now": "teď {}",
        "session_playing": "{} · {}", "session_label": "SESSION",
        "games": "Hry",
        "rl_waiting": "Čekám na zápas...", "rl_closed": "Spusť Rocket League",
        "rl_today": "Dnes: {} výher · {} proher", "rl_ball": "Míč {} km/h", "rl_ot": "PRODL.",
        "rl_goal": "GÓL!", "rl_stats": "Góly {}  ·  Střely {}  ·  Asist. {}  ·  Zákroky {}",
        "rl_live": "Rocket League: živá data pro widget", "rl_live_rate": " (teď {}× za s)",
        "rl_borderless": "Rocket League: celá obrazovka bez okraje (widgety vidět)",
        "rl_close_first": "Nejdřív úplně zavři Rocket League a pak to zkus znovu.\n(Hra si nastavení při zavření přepíše.)",
        "rl_no_config": "Nastavení Rocket League jsem nenašel.\nSpusť hru aspoň jednou a zkus to znovu.",
        "rl_live_done": "Hotovo. Při příštím spuštění Rocket League pošle widgetu skóre, čas a boost.",
        "rl_border_done": "Hotovo. Rocket League se příště spustí v celé obrazovce bez okraje.\n"
                          "Vypadá to stejně jako normální celá obrazovka, ale widgety budou vidět.\n"
                          "(Vrátit to jde ve hře: Nastavení > Video > Režim okna.)",
        "rl_fullscreen_back": "Rocket League: zpět na exkluzivní celou obrazovku",
        "rl_reset": "Vynulovat výhry / prohry",
        "gd_jumps": "skoků dnes", "gd_total": "celkem {}", "gd_closed": "Spusť Geometry Dash",
        "rl_win": "VÝHRA!", "rl_loss": "PROHRA", "rl_enable_hint": "Zapni živá data: menu > Hry",
        "w_scoreboard": "Skóre Rocket League", "sb_mode": "Vzhled", "sb_enhanced": "Vylepšený",
        "sb_pastel": "Pastelový RGB", "sb_black": "Černý RGB", "sb_font": "Písmo", "sb_font_other": "Jiné písmo...",
        "sb_bold": "Tučné písmo", "sb_top": "Zpět nahoru doprostřed", "sb_handwriting": "rukopis",
        "w_boost": "Boost Rocket League",
        "home_widget": "Vrátit na původní místo", "home_all": "Vrátit všechny na původní místo",
        "fn_windowed": "Fortnite: celá obrazovka v okně (widgety vidět)",
        "fn_windowed_hint": "",
        "fn_close_first": "Nejdřív úplně zavři Fortnite a pak to zkus znovu.\n(Hra si nastavení při zavření přepíše.)",
        "fn_no_config": "Nastavení Fortnite jsem nenašel.\nSpusť hru aspoň jednou a zkus to znovu.",
        "fn_windowed_done": "Hotovo. Fortnite se příště spustí v režimu „Celá obrazovka v okně“.\n"
                            "Vypadá to stejně jako celá obrazovka, ale widgety budou vidět.\n"
                            "(Vrátit to jde ve hře: Nastavení > Video > Režim okna.)",
        "fs_text_fn": "Fortnite běží v exkluzivní celé obrazovce.\n"
                      "Oprava jedním klikem: zavři hru, menu > Hry > Fortnite: celá obrazovka v okně.",
        "sb_names": "Zobrazit názvy týmů",
        "w_clock": "Hodiny", "clk_analog": "Ručičkové hodiny", "clk_seconds": "Zobrazit sekundy",
        "clk_timer": "Minutka: {} min", "clk_cancel": "Zrušit minutku", "clk_left": "zbývá {}",
        "clk_done": "Čas vypršel!", "clk_done_text": "Minutka {} min doběhla.",
        "days": "pondělí,úterý,středa,čtvrtek,pátek,sobota,neděle",
        "w_chests": "Truhly Fortnite", "fn_match": "truhel v zápase", "fn_line": "dnes {}  ·  celkem {}  ·  rekord {}",
        "fn_closed": "Spusť Fortnite", "fn_key": "Klávesa pro truhlu: {}", "fn_change": "Změnit klávesu pro truhlu...",
        "fn_plus": "+1 truhla", "fn_minus": "-1 truhla (oprava)", "fn_reset_match": "Vynulovat tenhle zápas",
        "fn_reset_all": "Vynulovat všechno", "fn_prompt": "Zmáčkni klávesu nebo tlačítko myši (Esc = zrušit)",
        "fn_hint": "{} = +1 truhla", "mouse_m": "kolečko myši (klik)", "mouse_x1": "myš - boční tlačítko 4",
        "mouse_x2": "myš - boční tlačítko 5",
        "fn_auto": "Poznávat truhly samo (čte nápis SEARCH u zaměřovače)",
        "fn_hint_auto": "otevři truhlu = počítá se samo",
        "w_fnmatch": "Čas kola (Fortnite)", "fm_lobby": "LOBBY", "fm_warmup": "OSTROV", "fm_bus": "AUTOBUS",
        "fm_game": "ZÁPAS", "fm_end": "KONEC ZÁPASU", "fm_last": "poslední zápas", "fm_line": "dnes {} zápasů · průměr {} · nejdelší {}",
        "fm_reset": "Vynulovat dnešní zápasy",
        "w_drink": "Připomínka pití", "dr_in": "do dalšího pití", "dr_now": "NAPIJ SE!", "dr_today": "dnes {}× voda",
        "dr_title": "Čas na vodu 💧", "dr_text": "Napij se, ať vydržíš hrát. Další připomínka za {} min.",
        "dr_interval": "Připomínat každých {} min", "dr_games": "Jen když hraju hru", "dr_done": "Napil jsem se",
        "dr_reset": "Vynulovat dnešek", "dr_paused": "pauza (žádná hra)",
        "theme": "Téma", "th_classic": "Klasické", "th_pastel": "Pastelové", "th_black": "Černé RGB", "th_neon": "Neon",
        "th_glass": "Sklo", "th_minimal": "Bez pozadí", "th_custom": "Vlastní", "th_global": "Podle hlavního tématu",
        "st_theme": "Podle tématu",
        "main_menu": "Hlavní menu (nastavení všeho)...", "sound_saved": "Vlastní zvuk nahrán: {}",
        "obs": "OBS (stream)", "obs_on": "Widgety pro OBS (zdroj Prohlížeč)", "obs_copy": "Zkopírovat adresu pro OBS",
        "obs_open": "Otevřít návod pro OBS", "obs_copied": "Adresa je ve schránce:\n{}\n\nV OBS: Přidat zdroj > Prohlížeč, "
        "vlož adresu, šířka {} a výška {}.", "obs_fail": "Server pro OBS se nepodařilo spustit (port {} je obsazený).",
        "bm_home": "Zpět na místo boostu (vpravo dole)",
        "sb_same": "Vzhled, písmo a průhlednost má skóre i boost stejné",
        "gd_time": "Hraješ {}", "gd_reset": "Vynulovat dnešní skoky",
        "fs_title": "Widgety nebyly ve hře vidět",
        "fs_text": "{} běží v exkluzivní celé obrazovce, přes ni Windows nic nezobrazí.\n"
                   "Přepni ve hře režim okna na „Bez okraje“ (Borderless).",
        "fs_text_rl": "Rocket League běží v exkluzivní celé obrazovce.\n"
                      "Oprava jedním klikem: menu > Hry > celá obrazovka bez okraje.",
    },
    "en": {
        "move": "Move / resize\tCtrl+Alt+F11", "show": "Show widgets",
        "sound": "Sound", "sound_on": "Sound on", "own": "Own: {}", "upload": "Load your own sound...",
        "volume": "Volume", "effect": "Effect", "color": "Color", "autostart": "Start with Windows",
        "help": "Help", "quit": "Quit\tCtrl+Alt+F12",
        "fx_wave": "Rainbow wave", "fx_static": "Static light", "fx_breathe": "Breathing",
        "fx_ripple": "Ripple from key", "fx_reactive": "Only pressed keys",
        "c_rainbow": "Rainbow", "c_red": "Red", "c_orange": "Orange", "c_yellow": "Yellow",
        "c_green": "Green", "c_cyan": "Cyan", "c_blue": "Blue", "c_purple": "Purple",
        "c_pink": "Pink", "c_white": "White",
        "running_title": "Keyboard Sound is running",
        "running_text": "Click this icon for the main menu, right-click for the quick menu.\nCtrl+Alt+F11 = move widgets, Ctrl+Alt+F12 = quit.",
        "already": "Keyboard Sound is already running.\nLook for the keyboard icon near the clock (bottom right).",
        "sound_error": "This sound can't be played:\n{}\n\nTry another file (wav, mp3, m4a, ogg, flac, wma).",
        "pick_title": "Choose a key sound", "pick_filter": "Sounds",
        "edit_hint": "Drag = move  •  Wheel = size  •  Right-click = options  •  Ctrl+Alt+F11 = done",
        "numpad_hide": "Hide numpad", "numpad_show": "Show numpad",
        "bg_hide": "Hide background", "bg_show": "Show background",
        "bigger": "Bigger", "smaller": "Smaller", "opacity": "Opacity",
        "top-left": "top left", "top-right": "top right", "bottom-left": "bottom left", "bottom-right": "bottom right",
        "done": "Done (lock positions)", "quit_plain": "Quit", "help_title": "Keyboard Sound - help",
        "help_text": HELP_EN,
        "media": "Music (Spotify, YouTube...)",
        "ma_play": "Play / pause", "ma_next": "Next track", "ma_prev": "Previous track",
        "ma_fwd": "Seek forward", "ma_back": "Seek back",
        "ma_vol_up": "Music volume +", "ma_vol_down": "Music volume -",
        "change_binds": "Change shortcuts", "reset_binds": "Reset shortcuts", "seek_step": "Seek step",
        "music_step": "Volume step", "mute_ads": "Mute ads (Spotify)",
        "bind_prompt": "Press a new shortcut for: {}\n(with Ctrl / Alt / Shift, or an F-key.  Esc = cancel)",
        "bind_busy": "The shortcut {} is already used by another program.\nTry a different one.",
        "bind_taken": "taken", "media_missing": "Music control is not available", "space": "Space",
        "nothing_playing": "Nothing is playing", "ad_muted": "Ad - muted", "ad_playing": "Ad",
        "click_volume": "Click volume\tCtrl+Alt+PgUp / PgDn",
        "widgets": "Widgets", "w_keyboard": "Keyboard", "w_mouse": "Mouse", "w_player": "Player",
        "w_stats": "Stats", "w_cat": "Cat", "w_text": "Text", "w_image": "Image", "w_hint": "",
        "add_text": "Add text...", "add_image": "Add image...", "reset_layout": "Arrange",
        "hide_widget": "Hide this widget", "remove_widget": "Remove widget",
        "edit_text": "Edit text...", "text_color": "Color", "tc_effect": "follow the effect", "bold": "Bold",
        "text_bg": "Background", "change_image": "Change image...", "image_title": "Choose an image",
        "image_filter": "Images", "image_missing": "Image not found",
        "text_prompt": "Type your text (Enter = new line):", "default_text": "Hello!", "ok": "OK", "cancel": "Cancel",
        "reset_today": "Reset today", "reset_best": "Reset record", "reset_cat": "Reset counter",
        "fur": "Cat", "fur_orange": "ginger", "fur_gray": "gray", "fur_black": "black", "fur_white": "white",
        "fur_rgb": "RGB", "stats_best": "record {}", "stats_apm_sub": "actions per minute",
        "stats_today": "Today: {} keys · {} clicks",
        "mouse_sound": "Click sound on mouse buttons too",
        "style": "Keyboard look", "st_rgb": "RGB", "st_pastel": "Pastel", "st_black": "Normal black",
        "w_session": "Session timer", "w_rocket": "Rocket League", "w_dash": "Geometry Dash",
        "session_reset": "Reset timer", "session_pause": "Pause", "session_now": "now {}",
        "session_playing": "{} · {}", "session_label": "SESSION",
        "games": "Games",
        "rl_waiting": "Waiting for a match...", "rl_closed": "Start Rocket League",
        "rl_today": "Today: {} wins · {} losses", "rl_ball": "Ball {} km/h", "rl_ot": "OT",
        "rl_goal": "GOAL!", "rl_stats": "Goals {}  ·  Shots {}  ·  Assists {}  ·  Saves {}",
        "rl_live": "Rocket League: live data for the widget", "rl_live_rate": " (now {}× per s)",
        "rl_borderless": "Rocket League: borderless fullscreen (widgets visible)",
        "rl_close_first": "Close Rocket League completely first, then try again.\n(The game overwrites its settings when it closes.)",
        "rl_no_config": "Rocket League settings not found.\nStart the game at least once and try again.",
        "rl_live_done": "Done. Next time Rocket League starts it sends the score, time and boost to the widget.",
        "rl_border_done": "Done. Rocket League will start in borderless fullscreen next time.\n"
                          "It looks the same as normal fullscreen, but the widgets will be visible.\n"
                          "(You can change it back in the game: Settings > Video > Window mode.)",
        "rl_fullscreen_back": "Rocket League: back to exclusive fullscreen",
        "rl_reset": "Reset wins / losses",
        "gd_jumps": "jumps today", "gd_total": "total {}", "gd_closed": "Start Geometry Dash",
        "rl_win": "WIN!", "rl_loss": "LOSS", "rl_enable_hint": "Turn on live data: menu > Games",
        "w_scoreboard": "Rocket League score", "sb_mode": "Look", "sb_enhanced": "Enhanced",
        "sb_pastel": "Pastel RGB", "sb_black": "Black RGB", "sb_font": "Font", "sb_font_other": "Other font...",
        "sb_bold": "Bold font", "sb_top": "Back to top center", "sb_handwriting": "handwriting",
        "w_boost": "Rocket League boost",
        "home_widget": "Back to its original place", "home_all": "Put all back in their original places",
        "fn_windowed": "Fortnite: windowed fullscreen (widgets visible)",
        "fn_windowed_hint": "",
        "fn_close_first": "Close Fortnite completely first, then try again.\n(The game overwrites its settings when it closes.)",
        "fn_no_config": "Fortnite settings not found.\nStart the game at least once and try again.",
        "fn_windowed_done": "Done. Fortnite will start in \"Windowed Fullscreen\" next time.\n"
                            "It looks the same as fullscreen, but the widgets will be visible.\n"
                            "(You can change it back in the game: Settings > Video > Window Mode.)",
        "fs_text_fn": "Fortnite runs in exclusive fullscreen.\n"
                      "One-click fix: close the game, menu > Games > Fortnite: windowed fullscreen.",
        "sb_names": "Show team names",
        "w_clock": "Clock", "clk_analog": "Analog clock", "clk_seconds": "Show seconds",
        "clk_timer": "Timer: {} min", "clk_cancel": "Cancel the timer", "clk_left": "{} left",
        "clk_done": "Time's up!", "clk_done_text": "Your {} min timer is done.",
        "days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "w_chests": "Fortnite chests", "fn_match": "chests this match", "fn_line": "today {}  ·  total {}  ·  record {}",
        "fn_closed": "Start Fortnite", "fn_key": "Chest key: {}", "fn_change": "Change the chest key...",
        "fn_plus": "+1 chest", "fn_minus": "-1 chest (undo)", "fn_reset_match": "Reset this match",
        "fn_reset_all": "Reset everything", "fn_prompt": "Press a key or mouse button (Esc = cancel)",
        "fn_hint": "{} = +1 chest", "mouse_m": "mouse wheel (click)", "mouse_x1": "mouse side button 4",
        "mouse_x2": "mouse side button 5",
        "fn_auto": "Recognize chests by itself (reads SEARCH by the crosshair)",
        "fn_hint_auto": "open a chest = counted by itself",
        "w_fnmatch": "Match time (Fortnite)", "fm_lobby": "LOBBY", "fm_warmup": "ISLAND", "fm_bus": "BATTLE BUS",
        "fm_game": "MATCH", "fm_end": "MATCH OVER", "fm_last": "last match", "fm_line": "today {} matches · avg {} · longest {}",
        "fm_reset": "Reset today's matches",
        "w_drink": "Drink reminder", "dr_in": "until the next drink", "dr_now": "DRINK!", "dr_today": "today {}× water",
        "dr_title": "Time for water 💧", "dr_text": "Have a drink to keep playing well. Next reminder in {} min.",
        "dr_interval": "Remind every {} min", "dr_games": "Only while I play a game", "dr_done": "I had a drink",
        "dr_reset": "Reset today", "dr_paused": "paused (no game)",
        "theme": "Theme", "th_classic": "Classic", "th_pastel": "Pastel", "th_black": "Black RGB", "th_neon": "Neon",
        "th_glass": "Glass", "th_minimal": "No background", "th_custom": "Custom", "th_global": "Main theme",
        "st_theme": "Follow the theme",
        "main_menu": "Main menu (all settings)...", "sound_saved": "Your sound is loaded: {}",
        "obs": "OBS (stream)", "obs_on": "Widgets for OBS (Browser source)", "obs_copy": "Copy the address for OBS",
        "obs_open": "Open the OBS how-to", "obs_copied": "The address is in your clipboard:\n{}\n\nIn OBS: Add source > Browser, "
        "paste the address, width {} and height {}.", "obs_fail": "The OBS server couldn't start (port {} is taken).",
        "bm_home": "Back to the boost meter's place (bottom right)",
        "sb_same": "Score and boost share the look, font and opacity",
        "gd_time": "Playing {}", "gd_reset": "Reset today's jumps",
        "fs_title": "Widgets weren't visible in the game",
        "fs_text": "{} runs in exclusive fullscreen, Windows can't show anything over it.\n"
                   "Set the game's window mode to \"Borderless\".",
        "fs_text_rl": "Rocket League runs in exclusive fullscreen.\n"
                      "One-click fix: menu > Games > borderless fullscreen.",
    },
}
LANG = ui_language()


def T(key):
    return TEXTS[LANG].get(key) or TEXTS["en"].get(key, key)


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
user32.MessageBeep.argtypes = [wintypes.UINT]
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
                v = (samples[j * ch + c] * (1 - f) + samples[j2 * ch + c] * f) * g
                if v > 24000 or v < -24000:            # louder than 100 %: round off the peaks instead of crackling
                    a = abs(v)
                    v = math.copysign(24000 + 8700 * math.tanh((a - 24000) / 8700), v)
                out[i * ch + c] = max(-32768, min(32767, int(v)))
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
mouse_sound_enabled = False
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
            if mouse_sound_enabled and sound_enabled:
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


def mouse_layout(ox, y0=0.55):
    """A mouse drawn in key units: left/right/middle button, wheel up/down, two side buttons."""
    h = 5.7
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
KEY_STYLES = ["rgb", "pastel", "black", "theme"]


def style_colors(style, key, theme=None, accent=None):
    """(fill, outline, text, pressed fill, pressed outline, pressed text) of one key in the pastel / black / theme look."""
    body = key.get("body")
    if style == "theme" and theme is not None:
        bg = theme.bg if theme.bg is not None and theme.bg_a >= 0.3 else (0.08, 0.08, 0.1)
        acc = accent or ACCENT
        if body:
            return mix(bg, theme.text_rgb, 0.04), mix(bg, acc, 0.45), theme.text_rgb, None, None, None
        return (mix(bg, theme.text_rgb, 0.07), mix(bg, acc, 0.55), theme.text_rgb,
                acc, mix(acc, WHITE, 0.4), bg if sum(acc) / 3 > 0.55 else WHITE)
    if style == "pastel":
        if body:
            return (0.96, 0.95, 0.99), (0.78, 0.76, 0.87), INK, None, None, None
        hue = (key["xfrac"] * 0.85 + 0.55) % 1.0
        fill = colorsys.hsv_to_rgb(hue, 0.30, 1.0)
        return (fill, colorsys.hsv_to_rgb(hue, 0.42, 0.86), (0.30, 0.27, 0.42),
                colorsys.hsv_to_rgb(hue, 0.62, 1.0), colorsys.hsv_to_rgb(hue, 0.75, 0.78), WHITE)
    if body:
        return (0.10, 0.10, 0.115), (0.25, 0.25, 0.28), WHITE, None, None, None
    return ((0.075, 0.075, 0.085), (0.24, 0.24, 0.27), (0.88, 0.88, 0.9),
            (0.42, 0.42, 0.47), (0.7, 0.7, 0.76), WHITE)


def default_widgets(old):
    """The first set of widgets (also takes over settings from versions 1.0 - 1.2)."""
    size = old.get("key_size") or 26
    return {
        "keyboard": {"type": "keyboard", "x": old.get("x"), "y": old.get("y"), "size": size, "visible": True,
                     "numpad": old.get("numpad", True), "background": old.get("background", False)},
        "mouse": {"type": "mouse", "x": None, "y": None, "size": size, "visible": old.get("mouse", True)},
        "player": {"type": "player", "x": None, "y": None, "size": size, "visible": old.get("media_widget", True)},
        "stats": {"type": "stats", "x": None, "y": None, "size": size, "visible": True},
        "cat": {"type": "cat", "x": None, "y": None, "size": size, "visible": True, "fur": "orange"},
    }


NEW_WIDGETS = {          # built-in widgets added in later versions (added to old settings too)
    "session": {"type": "session", "visible": True},
    "rocket": {"type": "rocket", "visible": True},
    "scoreboard": {"type": "scoreboard", "visible": True, "mode": "enhanced", "font": "Bahnschrift", "bold": True},
    "boost": {"type": "boost", "visible": True},
    "clock": {"type": "clock", "visible": True},
    "drink": {"type": "drink", "visible": True, "interval": 30},
    "fnmatch": {"type": "fnmatch", "visible": True},
    "chests": {"type": "chests", "visible": True, "key": ["m", "X2"]},
    "dash": {"type": "dash", "visible": True},
}


def scoreboard_default_size():
    h = ctypes.windll.user32.GetSystemMetrics(1) or 1080
    dpi = (ctypes.windll.user32.GetDpiForSystem() or 96) / 96.0
    return max(16, int(round(40 * h / 1080 / dpi)))


def load_settings():
    cfg = copy.deepcopy(DEFAULTS)
    raw = {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8-sig") as f:
            raw = json.load(f)
        cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if not isinstance(cfg.get("widgets"), dict) or not cfg["widgets"]:
        cfg["widgets"] = default_widgets(raw)
        if raw.get("visible") is False:
            cfg["visible"] = False
    widgets = {}
    for wid, w in cfg["widgets"].items():
        if isinstance(w, dict) and w.get("type") in WIDGET_TYPES:
            w.setdefault("x", None)
            w.setdefault("y", None)
            w.setdefault("size", 26)
            w.setdefault("visible", True)
            widgets[wid] = w
    for wid, w in NEW_WIDGETS.items():
        if not any(x["type"] == w["type"] for x in widgets.values()):
            size = (widgets.get("keyboard") or {}).get("size", 26)
            if w["type"] in ("scoreboard", "boost"):      # about as big as the game's own scoreboard / boost
                size = scoreboard_default_size()
            widgets[wid] = {**w, "x": None, "y": None, "size": size}
    cfg["widgets"] = widgets
    cfg["stats"] = {**DEFAULTS["stats"], **(cfg.get("stats") or {})}
    cfg["binds"] = {**DEFAULTS["binds"], **(cfg.get("binds") or {})}
    cfg["volume"] = max(0.1, min(2.0, float(cfg.get("volume") or 1.0)))
    return cfg


def save_settings(cfg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------- music control (Spotify, YouTube, ... via Windows)
media_commands = queue.Queue()    # ("play" | "next" | "prev" | "seek_by" | "seek_to" | "vol_by" | "mute_toggle", value)
media_options = {"mute_ads": True}

# Windows volume mixer (Core Audio): change the volume of just the music app
CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = GUID("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = GUID("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
IID_ISimpleAudioVolume = GUID("87CE5498-68D6-44E5-9215-6DA47EF883D8")
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def process_name(pid, cache={}):
    if pid in cache:
        return cache[pid]
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value).lower()
        kernel32.CloseHandle(h)
    if len(cache) > 500:
        cache.clear()
    cache[pid] = name
    return name


def program_names(app_id):
    """Which .exe plays the sound for a media session (Spotify.exe, chrome.exe, ...)."""
    a = (app_id or "").lower()
    if "spotify" in a:
        return {"spotify.exe"}
    for key, exe in (("chrome", "chrome.exe"), ("msedge", "msedge.exe"), ("edge", "msedge.exe"),
                     ("firefox", "firefox.exe"), ("opera", "opera.exe"), ("brave", "brave.exe"),
                     ("vivaldi", "vivaldi.exe")):
        if key in a:
            return {exe}
    if a.endswith(".exe"):
        return {os.path.basename(a)}
    return {a.split("!")[0].split("_")[0] + ".exe"}


class AppVolume:
    """Volume / mute of one program in the Windows mixer."""

    def __init__(self):
        self.enum = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, 0x17,
                                                  ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(self.enum))
        if hr < 0:
            raise OSError("no audio device")

    def _volumes(self, names):
        vp, ref = ctypes.c_void_p, ctypes.byref
        device, manager, sessions = vp(), vp(), vp()
        found = []
        try:
            com(self.enum, 4, 0, 1, ref(device), argtypes=(wintypes.DWORD, wintypes.DWORD, vp))    # speakers
            com(device, 3, ref(IID_IAudioSessionManager2), 0x17, None, ref(manager), argtypes=(vp, wintypes.DWORD, vp, vp))
            com(manager, 5, ref(sessions), argtypes=(vp,))
            count = ctypes.c_int()
            com(sessions, 3, ref(count), argtypes=(vp,))
            for i in range(count.value):
                ctl, ctl2 = vp(), vp()
                try:
                    com(sessions, 4, i, ref(ctl), argtypes=(ctypes.c_int, vp))
                    com(ctl, 0, ref(IID_IAudioSessionControl2), ref(ctl2), argtypes=(vp, vp))
                    pid = wintypes.DWORD()
                    com(ctl2, 14, ref(pid), argtypes=(vp,))
                    if pid.value and process_name(pid.value) in names:
                        vol = vp()
                        com(ctl, 0, ref(IID_ISimpleAudioVolume), ref(vol), argtypes=(vp, vp))
                        found.append(vol)
                except OSError:
                    pass
                finally:
                    for obj in (ctl2, ctl):
                        if obj.value:
                            com(obj, 2, restype=ctypes.c_ulong)
        except OSError:
            pass
        finally:
            for obj in (sessions, manager, device):
                if obj.value:
                    com(obj, 2, restype=ctypes.c_ulong)
        return found

    def get(self, names):
        vols = self._volumes(names)
        level, muted = None, False
        if vols:
            lv, m = ctypes.c_float(), wintypes.BOOL()
            com(vols[0], 4, ctypes.byref(lv), argtypes=(ctypes.c_void_p,))
            com(vols[0], 6, ctypes.byref(m), argtypes=(ctypes.c_void_p,))
            level, muted = lv.value, bool(m.value)
        for v in vols:
            com(v, 2, restype=ctypes.c_ulong)
        return level, muted

    def set(self, names, level=None, mute=None):
        for v in self._volumes(names):
            if level is not None:
                com(v, 3, ctypes.c_float(max(0.0, min(1.0, level))), None, argtypes=(ctypes.c_float, ctypes.c_void_p))
            if mute is not None:
                com(v, 5, bool(mute), None, argtypes=(wintypes.BOOL, ctypes.c_void_p))
            com(v, 2, restype=ctypes.c_ulong)


def is_spotify_ad(app_id, title, artist, playing):
    """Spotify Free shows ads as a 'song' with no artist (or called 'Advertisement' / 'Spotify')."""
    if "spotify" not in (app_id or "").lower() or not playing:
        return False
    t = (title or "").strip().lower()
    return not (artist or "").strip() or t in ("advertisement", "spotify", "spotify free", "reklama")


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


async def run_media_command(session, cmd, value, mixer):
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
    elif cmd in ("vol_by", "mute_toggle") and mixer:
        names = program_names(session.source_app_user_model_id)
        level, muted = mixer.get(names)
        if level is None:
            return
        if cmd == "vol_by":
            mixer.set(names, level=level + value, mute=False if muted else None)
        else:
            mixer.set(names, mute=not muted)


async def media_main():
    import asyncio
    from winrt.windows.media.control import (GlobalSystemMediaTransportControlsSessionManager as Manager,
                                             GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status)
    manager = await Manager.request_async()
    try:
        mixer = AppVolume()
    except OSError:
        mixer = None
    cover_key, cover = None, None
    ad_muted = None                                      # program names we muted for an ad
    try:
        while True:
            session = pick_session(manager)
            while True:                                   # do what the shortcuts / buttons asked for
                try:
                    cmd = media_commands.get_nowait()
                except queue.Empty:
                    break
                if cmd is None:
                    return
                if session:
                    try:
                        await run_media_command(session, *cmd, mixer)
                    except Exception:
                        pass
            state = None
            if session:
                try:
                    props = await session.try_get_media_properties_async()
                    info = session.get_playback_info()
                    playing = info.playback_status == Status.PLAYING
                    pos, end = timeline_now(session.get_timeline_properties(), playing)
                    app_id = session.source_app_user_model_id or ""
                    key = (app_id, props.title, props.artist)
                    if key != cover_key:
                        cover_key, cover = key, await read_cover(props)
                    names = program_names(app_id)
                    ad = is_spotify_ad(app_id, props.title, props.artist, playing)
                    if mixer:                             # mute Spotify ads, un-mute when the music is back
                        if ad and media_options["mute_ads"] and not ad_muted:
                            mixer.set(names, mute=True)
                            ad_muted = names
                        elif ad_muted and (not ad or not media_options["mute_ads"]):
                            mixer.set(ad_muted, mute=False)
                            ad_muted = None
                    level, muted = mixer.get(names) if mixer else (None, False)
                    state = {"app": app_id, "title": props.title or "",
                             "artist": props.artist or props.album_artist or "", "playing": playing,
                             "pos": pos, "end": end, "t": time.monotonic(), "cover": cover, "cover_key": key,
                             "can_seek": bool(info.controls.is_playback_position_enabled) and end > 0,
                             "volume": level, "muted": muted, "ad": ad, "ad_muted": bool(ad_muted)}
                except Exception:
                    state = None
            elif ad_muted and mixer:                      # Spotify closed during an ad
                mixer.set(ad_muted, mute=False)
                ad_muted = None
            ui_events.put(("media", state))
            wake_overlay()
            for _ in range(10):                           # check again in 0.5 s, or sooner for a command
                if not media_commands.empty():
                    break
                await asyncio.sleep(0.05)
    finally:
        if ad_muted and mixer:                            # never leave Spotify muted when we quit
            mixer.set(ad_muted, mute=False)


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



# ---------------------------------------------------------------- games (running programs, Rocket League, Geometry Dash)
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

KNOWN_GAMES = {
    "rocketleague.exe": "Rocket League", "geometrydash.exe": "Geometry Dash",
    "fortniteclient-win64-shipping.exe": "Fortnite", "cs2.exe": "Counter-Strike 2",
    "valorant-win64-shipping.exe": "Valorant", "robloxplayerbeta.exe": "Roblox",
    "minecraft.windows.exe": "Minecraft", "league of legends.exe": "League of Legends",
    "gta5.exe": "GTA V", "gta5_enhanced.exe": "GTA V", "r5apex.exe": "Apex Legends",
    "overwatch.exe": "Overwatch 2", "osu!.exe": "osu!", "brawlhalla.exe": "Brawlhalla",
    "fallguys_client_game.exe": "Fall Guys", "rainbowsix.exe": "Rainbow Six Siege",
    "eafc25.exe": "EA FC", "eafc26.exe": "EA FC", "cod.exe": "Call of Duty",
}


def running_programs():
    """Lower-case exe names of all running programs (cheap: no process is opened)."""
    names = set()
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return names
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        names.add(entry.szExeFile.lower())
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    return names


def foreground_program():
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return (process_name(pid.value) or "").lower() if pid.value else ""


def is_geometry_dash(exe):
    return "geometrydash" in exe.replace(" ", "")


def documents_dir():
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:      # CSIDL_PERSONAL
        return buf.value
    return os.path.join(os.path.expanduser("~"), "Documents")


def rl_config_dir():
    return os.path.join(documents_dir(), "My Games", "Rocket League", "TAGame", "Config")


def read_ini(path, section):
    values, current = {}, None
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1]
                elif current == section and "=" in line:
                    k, v = line.split("=", 1)
                    values.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    return values


def write_ini(path, section, changes):
    """Change keys in one [section] of an .ini file, keep everything else exactly as it was."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    todo, out, current = dict(changes), [], None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current == section and todo:                   # leaving our section: add what's missing
                out.extend(f"{k}={v}" for k, v in todo.items())
                todo = {}
            current = stripped[1:-1]
        elif current == section and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in changes:
                if key not in todo:
                    continue                                    # duplicate key: drop it
                line = f"{key}={todo.pop(key)}"
        out.append(line)
    if todo:
        if current != section:
            out += ([""] if out and out[-1].strip() else []) + [f"[{section}]"]
        out.extend(f"{k}={v}" for k, v in todo.items())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".ks-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)


RL_STATS_INI = "TAStatsAPI.ini"
RL_STATS_SECTION = "TAGame.MatchStatsExporter_TA"
RL_VIDEO_INI = "TASystemSettings.ini"
rl_wanted = threading.Event()      # set while the Rocket League widget wants data


def rl_port():
    try:
        return int(read_ini(os.path.join(rl_config_dir(), RL_STATS_INI), RL_STATS_SECTION).get("Port", 49123))
    except ValueError:
        return 49123


def rl_worker(ready):
    """Reads Rocket League's official Stats API (a local TCP socket that streams JSON messages)."""
    ready.append(None)
    decoder = json.JSONDecoder()
    while True:
        rl_wanted.wait()
        try:
            sock = socket.create_connection(("127.0.0.1", rl_port()), timeout=1.5)
        except OSError:
            time.sleep(3)
            continue
        ui_events.put(("rl", ("Connected", None)))
        wake_overlay()
        text, utf8 = "", codecs.getincrementaldecoder("utf-8")(errors="replace")
        sock.settimeout(5)
        try:
            while rl_wanted.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                text += utf8.decode(chunk)
                while text:
                    start = text.find("{")
                    if start < 0:
                        text = ""
                        break
                    try:
                        msg, end = decoder.raw_decode(text, start)
                    except ValueError:
                        if len(text) > 4_000_000:                # broken stream: start over
                            text = ""
                        elif text.find("{", start + 1) > 0 and len(text) - start > 200_000:
                            text = text[start + 1:]
                        break
                    text = text[end:]
                    if isinstance(msg, dict):
                        data = msg.get("Data")
                        if isinstance(data, str):
                            try:
                                data = json.loads(data)
                            except ValueError:
                                pass
                        ui_events.put(("rl", (msg.get("Event"), data)))
                        wake_overlay()
        except OSError:
            pass
        finally:
            sock.close()
        ui_events.put(("rl", ("Disconnected", None)))
        wake_overlay()
        time.sleep(2)


FORTNITE_EXE = "fortniteclient-win64-shipping.exe"
FN_SETTINGS_SECTION = "/Script/FortniteGame.FortGameUserSettings"


def fn_settings_path():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "FortniteGame", "Saved", "Config", "WindowsClient",
                        "GameUserSettings.ini")


def fn_windowed_on():
    """0 = exclusive fullscreen (nothing can be drawn over it), 1 = windowed fullscreen, 2 = window."""
    return read_ini(fn_settings_path(), FN_SETTINGS_SECTION).get("FullscreenMode", "0").strip() != "0"

fn_wanted = threading.Event()      # set while a Fortnite widget wants to know about matches


def fortnite_worker(ready):
    """Follows Fortnite's own log file: match start (island), battle bus, match, end, back in the lobby."""
    ready.append(None)
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FortniteGame", "Saved", "Logs", "FortniteGame.log")
    f, ident, rest = None, None, b""
    while True:
        fn_wanted.wait()
        try:
            st = os.stat(path)
            if f is None or (st.st_ino, st.st_ctime) != ident or st.st_size < f.tell():
                if f:
                    f.close()
                fresh = f is not None              # the game restarted: read the new log from the start
                f = open(path, "rb")
                ident = (st.st_ino, st.st_ctime)
                if not fresh:
                    f.seek(0, 2)                   # first look: only what happens from now on
                rest = b""
            data = f.read(1 << 20)
            if data:
                lines = (rest + data).split(b"\n")
                rest = lines.pop()
                for line in lines:
                    event = None
                    if b"HandleGamePhaseChanged" in line and b"NewPhase = EAthenaGamePhase::" in line:
                        event = ("phase", line.split(b"NewPhase = EAthenaGamePhase::")[1].split()[0].decode(errors="replace"))
                    elif b"LogLoad: LoadMap: /Game/Maps/Frontend" in line:
                        event = ("lobby", "")
                    if event:
                        ui_events.put(("fn", event))
                        wake_overlay()
                continue
        except OSError:
            f = None
        time.sleep(1.0)


def key_label(kid):
    if not kid:
        return "-"
    kid = tuple(kid)
    if kid[0] == "m":
        return T({"M": "mouse_m", "X1": "mouse_x1", "X2": "mouse_x2"}.get(kid[1], "mouse_m"))
    return bind_text(0, vk_from_key_id(kid))

# Fortnite's prompt on a chest is just "[E] SEARCH" (OCR sometimes reads SEARCN / SEARCR / SEARcu);
# "Cannot Do That Now" right after it means the chest is already opening.
SEARCH_RE = __import__("re").compile(r"s[e3][a4]r[c(]|prohled|durchsuch|fouiller|buscar")
fn_ocr_requests = queue.Queue(maxsize=3)      # (time, kind, w, h, BGRA pixels) next to the crosshair
OCR_THREAD_STARTED = False


def grab_screen(x, y, w, h):
    """Pixels of a part of the screen, as the game shows it (our own widgets are not included)."""
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bmp)
    gdi32.BitBlt(dc, 0, 0, w, h, screen, x, y, SRCCOPY)              # no CAPTUREBLT: layered widgets stay out
    data = ctypes.string_at(bits, w * h * 4)
    gdi32.SelectObject(dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return data


def prompt_area():
    """Where Fortnite shows "SEARCH" prompts: right of and below the crosshair."""
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return int(sw * 0.47), int(sh * 0.44), int(sw * 0.28), int(sh * 0.22)


def fn_ocr_worker(ready):
    """Reads the prompt next to the crosshair with Windows' text recognition (nothing touches the game)."""
    ready.append(None)
    import asyncio
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.storage.streams import DataWriter
    engine = OcrEngine.try_create_from_user_profile_languages()
    loop = asyncio.new_event_loop()

    async def read(data, w, h):
        dw = DataWriter()
        dw.write_bytes(data)
        bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h,
                                                     BitmapAlphaMode.PREMULTIPLIED)
        result = await engine.recognize_async(bmp)
        return " | ".join(line.text for line in result.lines)

    while True:
        t, kind, w, h, data = fn_ocr_requests.get()
        try:
            text = loop.run_until_complete(read(data, w, h))
        except Exception:
            text = ""
        ui_events.put(("fn_ocr", (t, kind, bool(SEARCH_RE.search(text.lower())))))
        wake_overlay()


# ---------------------------------------------------------------- OBS: widgets as a local web page (Browser source)
OBS_PORT = 47123                 # first port tried for the local web server (main menu + OBS)
WEB_TOKEN = __import__("secrets").token_urlsafe(18)     # only the main menu window knows it
obs_enabled = False
web_port = OBS_PORT
obs_lock = threading.Lock()
obs_frames = {}          # widget id -> (pixel format, w, h, raw pixels, version)
obs_wanted = {}          # widget id -> time a page last asked for it (only those get copied)
obs_layout = {"screen": [1920, 1080], "items": []}
obs_server = None
PNG_CLSID = GUID("557CF406-1A04-11D3-9A73-0000F81EF32E")
FMT_PARGB, FMT_RGB = 0xE200B, 0x22009
ole32 = ctypes.WinDLL("ole32")
ole32.CreateStreamOnHGlobal.argtypes = [wintypes.HGLOBAL, wintypes.BOOL, ctypes.POINTER(ctypes.c_void_p)]
ole32.GetHGlobalFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.HGLOBAL)]
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
EMPTY_PNG = None


def encode_png(fmt, w, h, data, key=True):
    """Raw widget pixels -> PNG with transparency (GDI+ does the work; the see-through color key becomes alpha)."""
    ensure_gdiplus()
    buf = ctypes.create_string_buffer(data, len(data))
    src = ctypes.c_void_p()
    gdiplus.GdipCreateBitmapFromScan0(w, h, w * 4, fmt, buf, ctypes.byref(src))
    img, extra = src, []
    if fmt == FMT_RGB and key:                     # plain GDI widget: make the color key see-through
        dst, g, attr = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        gdiplus.GdipCreateBitmapFromScan0(w, h, 0, FMT_PARGB, None, ctypes.byref(dst))
        gdiplus.GdipGetImageGraphicsContext(dst, ctypes.byref(g))
        gdiplus.GdipCreateImageAttributes(ctypes.byref(attr))
        key = 0xFF000000 | 0x010203                # TRANSPARENT_KEY (0x00030201 as COLORREF) in ARGB order
        gdiplus.GdipSetImageAttributesColorKeys(attr, 0, 1, key, key)
        gdiplus.GdipDrawImageRectRectI(g, src, 0, 0, w, h, 0, 0, w, h, 2, attr, None, None)
        gdiplus.GdipDisposeImageAttributes(attr)
        gdiplus.GdipDeleteGraphics(g)
        img, extra = dst, [src]
    stream = ctypes.c_void_p()
    ole32.CreateStreamOnHGlobal(None, True, ctypes.byref(stream))
    gdiplus.GdipSaveImageToStream(img, stream, ctypes.byref(PNG_CLSID), None)
    size = ctypes.c_ulonglong()
    com(stream, 5, ctypes.c_longlong(0), 2, ctypes.byref(size),          # IStream::Seek(0, END) = its size
        argtypes=(ctypes.c_longlong, wintypes.DWORD, ctypes.POINTER(ctypes.c_ulonglong)))
    hg = wintypes.HGLOBAL()
    ole32.GetHGlobalFromStream(stream, ctypes.byref(hg))
    ptr = kernel32.GlobalLock(hg)
    png = ctypes.string_at(ptr, size.value) if ptr else b""
    kernel32.GlobalUnlock(hg)
    com(stream, 2, restype=wintypes.ULONG)                                  # Release
    gdiplus.GdipDisposeImage(img)
    for x in extra:
        gdiplus.GdipDisposeImage(x)
    return png


OBS_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Keyboard Sound - OBS</title>
<style>html,body{margin:0;background:transparent;overflow:hidden}img{position:absolute;image-rendering:auto}</style>
</head><body><script>
const ONE = %s, imgs = {};
function pump(id, img) {
  if (!img.isConnected || img.dataset.on !== '1') { img.dataset.busy = ''; return; }
  img.dataset.busy = '1';
  const next = new Image();
  next.onload = () => { img.src = next.src; setTimeout(() => pump(id, img), 60); };
  next.onerror = () => setTimeout(() => pump(id, img), 500);
  next.src = '/frame/' + encodeURIComponent(id) + '.png?' + Date.now();
}
async function layout() {
  try {
    const L = await (await fetch('/layout.json?' + Date.now())).json();
    const seen = new Set();
    for (const it of L.items) {
      if (ONE && it.id !== ONE) continue;
      seen.add(it.id);
      let img = imgs[it.id];
      if (!img) { img = imgs[it.id] = document.createElement('img'); document.body.appendChild(img); }
      img.style.left = (ONE ? 0 : it.x) + 'px'; img.style.top = (ONE ? 0 : it.y) + 'px';
      img.style.width = it.w + 'px'; img.style.height = it.h + 'px';
      img.style.display = it.shown ? 'block' : 'none';
      img.dataset.on = it.shown ? '1' : '';
      if (it.shown && !img.dataset.busy) pump(it.id, img);
    }
    for (const id in imgs) if (!seen.has(id)) { imgs[id].remove(); delete imgs[id]; }
  } catch (e) {}
  setTimeout(layout, 1000);
}
layout();
</script></body></html>"""


def obs_index_html():
    sw, sh = obs_layout["screen"]
    rows = "".join(f'<li><a href="/w/{it["id"]}">{it["label"]}</a> - {it["w"]}x{it["h"]}</li>'
                   for it in obs_layout["items"])
    base = f"http://127.0.0.1:{web_port}"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Keyboard Sound - OBS</title>
<style>body{{font:16px Segoe UI,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;background:#14141c;color:#e8e8f0}}
code{{background:#2a2a3a;padding:2px 6px;border-radius:6px}}a{{color:#9db4ff}}</style></head><body>
<h1>Keyboard Sound → OBS</h1>
<p><b>{"Všechny widgety najednou" if LANG == "cs" else "All widgets at once"}:</b> <code>{base}/all</code>
({"šířka" if LANG == "cs" else "width"} {sw}, {"výška" if LANG == "cs" else "height"} {sh})</p>
<ol><li>{"OBS → Zdroje → + → Prohlížeč" if LANG == "cs" else "OBS → Sources → + → Browser"}</li>
<li>URL: <code>{base}/all</code></li>
<li>{"Šířka" if LANG == "cs" else "Width"} {sw}, {"výška" if LANG == "cs" else "height"} {sh}</li></ol>
<p>{"Widgety jsou vidět i na streamu, když hra běží v exkluzivní celé obrazovce." if LANG == "cs"
    else "The widgets show on stream even when the game runs in exclusive fullscreen."}</p>
<p>{"Jeden widget zvlášť" if LANG == "cs" else "One widget on its own"}:</p><ul>{rows}</ul></body></html>"""


class ObsHandler(__import__("http.server").server.BaseHTTPRequestHandler):
    cache = {}

    def log_message(self, *args):
        pass

    def send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """Main menu actions: only with the window's secret token (no other page can change settings)."""
        if self.path != "/api/do" or self.headers.get("X-Token") != WEB_TOKEN:
            self.send(b"forbidden", "text/plain", 403)
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size > 40_000_000:
                raise ValueError("too big")
            data = json.loads(self.rfile.read(size) or b"{}")
            result = ask_app(str(data.pop("a", "state")), data)
            self.send(json.dumps(result).encode("utf-8"), "application/json")
        except (ValueError, ConnectionError, OSError) as e:
            try:
                self.send(json.dumps({"msg": str(e)}).encode("utf-8"), "application/json", 400)
            except OSError:
                pass

    def do_GET(self):
        global EMPTY_PNG
        path = self.path.split("?")[0]
        query = __import__("urllib.parse").parse.parse_qs(self.path.partition("?")[2])
        if path == "/menu":
            if query.get("t", [""])[0] != WEB_TOKEN:
                self.send(b"forbidden", "text/plain", 403)
                return
            page = MENU_PAGE.replace("%%TOKEN%%", WEB_TOKEN).replace("%%LANG%%", LANG)
            self.send(page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if not obs_enabled:                        # the OBS pages only while "Widgets for OBS" is on
            self.send(b"OBS is off in Keyboard Sound", "text/plain", 404)
            return
        try:
            if path == "/":
                self.send(obs_index_html().encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/all":
                self.send((OBS_PAGE % "null").encode("utf-8"), "text/html; charset=utf-8")
            elif path.startswith("/w/"):
                wid = __import__("urllib.parse").parse.unquote(path[3:])
                self.send((OBS_PAGE % json.dumps(wid)).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/layout.json":
                with obs_lock:
                    body = json.dumps(obs_layout).encode("utf-8")
                self.send(body, "application/json")
            elif path.startswith("/frame/") and path.endswith(".png"):
                wid = __import__("urllib.parse").parse.unquote(path[7:-4])
                with obs_lock:
                    obs_wanted[wid] = time.monotonic()
                    frame = obs_frames.get(wid)
                if not frame:
                    if EMPTY_PNG is None:
                        EMPTY_PNG = encode_png(FMT_PARGB, 1, 1, b"\\0\\0\\0\\0")
                    self.send(EMPTY_PNG, "image/png")
                    return
                fmt, w, h, data, version = frame
                cached = self.cache.get(wid)
                if not cached or cached[0] != version:
                    cached = self.cache[wid] = (version, encode_png(fmt, w, h, data))
                self.send(cached[1], "image/png")
            else:
                self.send(b"not found", "text/plain", 404)
        except (ConnectionError, OSError):
            pass


def web_start():
    """The local web server (only this PC can reach it): the main menu window and the OBS pages."""
    global obs_server, web_port
    if obs_server:
        return True
    import http.server
    class Server(http.server.ThreadingHTTPServer):
        allow_reuse_address = False               # never share the port with another program

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    for port in range(OBS_PORT, OBS_PORT + 10):
        try:
            obs_server = Server(("127.0.0.1", port), ObsHandler)
        except OSError:
            continue
        web_port = port
        obs_server.daemon_threads = True
        threading.Thread(target=obs_server.serve_forever, daemon=True).start()
        return True
    return False


def ask_app(action, data, timeout=120):
    """Runs a main-menu action on the app's own thread and waits for the answer."""
    reply = queue.Queue()
    ui_events.put(("web", (action, data, reply)))
    wake_overlay()
    try:
        return reply.get(timeout=timeout)
    except queue.Empty:
        return {"msg": "timeout"}


MENU_PAGE = r'''<!doctype html>
<html lang="%%LANG%%"><head><meta charset="utf-8"><title>Keyboard Sound</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0e0f15;--side:#13141c;--card:#191a24;--line:#2a2b3a;--text:#ecedf5;--muted:#9a9cb2;--acc:#7d9bff;--acc2:#b07dff;--ok:#3ddc84;--bad:#ff5c6c}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:15px/1.45 "Segoe UI",system-ui,sans-serif}
body{display:flex;overflow:hidden}
aside{width:220px;flex:none;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:18px 12px;gap:4px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px;margin:0 8px 16px}
.brand i{width:30px;height:30px;border-radius:9px;background:conic-gradient(from 0deg,#ff4d6d,#ffb84d,#f9f871,#4dff9a,#4dc3ff,#9b6bff,#ff4d6d)}
nav button{all:unset;display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:10px;color:var(--muted);cursor:pointer;font-weight:600}
nav button:hover{background:#1d1f2b;color:var(--text)}nav button.on{background:linear-gradient(90deg,#2a2f55,#20223a);color:#fff}
nav button span{width:22px;text-align:center}
.side-bottom{margin-top:auto;display:flex;flex-direction:column;gap:8px}
main{flex:1;overflow:auto;padding:26px 30px 60px}
h1{margin:0 0 4px;font-size:24px}.sub{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.card h3{margin:0 0 12px;font-size:15px;display:flex;align-items:center;gap:8px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:7px 0;min-height:38px}
.row+.row{border-top:1px solid #23243200}
.row label{color:var(--text)}.row .hint{color:var(--muted);font-size:13px}
.col{display:flex;flex-direction:column;gap:2px}
.sw{position:relative;width:44px;height:24px;flex:none}.sw input{opacity:0;width:0;height:0}
.sw b{position:absolute;inset:0;border-radius:24px;background:#353748;transition:.15s;cursor:pointer}
.sw b:before{content:"";position:absolute;width:18px;height:18px;left:3px;top:3px;border-radius:50%;background:#fff;transition:.15s}
.sw input:checked+b{background:var(--acc)}.sw input:checked+b:before{transform:translateX(20px)}
.seg{display:flex;flex-wrap:wrap;gap:6px}.seg button{all:unset;padding:6px 12px;border-radius:9px;background:#232534;color:var(--muted);cursor:pointer;font-weight:600;font-size:14px}
.seg button:hover{color:var(--text)}.seg button.on{background:var(--acc);color:#0c0d14}
input[type=range]{width:180px;accent-color:var(--acc)}
select,textarea,input[type=text]{background:#232534;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:7px 10px;font:inherit;max-width:230px}
textarea{width:100%;max-width:none;min-height:64px;resize:vertical}
.btn{all:unset;padding:8px 14px;border-radius:10px;background:#272a3d;color:var(--text);cursor:pointer;font-weight:600;font-size:14px;text-align:center}
.btn:hover{background:#30344c}.btn.pri{background:linear-gradient(90deg,var(--acc),var(--acc2));color:#0b0c12}.btn.bad{background:#3a2028;color:#ffb3bd}
.btns{display:flex;flex-wrap:wrap;gap:8px}
.sw-list{display:flex;flex-wrap:wrap;gap:6px}.dot{width:26px;height:26px;border-radius:50%;border:2px solid transparent;cursor:pointer}
.dot.on{border-color:#fff;box-shadow:0 0 0 2px var(--acc)}
.wcard{display:flex;flex-direction:column;gap:4px}
.wtitle{display:flex;align-items:center;justify-content:space-between;gap:10px}
.wtitle b{font-size:15px}.tag{font-size:12px;color:var(--muted);background:#232534;border-radius:6px;padding:1px 7px}
details summary{cursor:pointer;color:var(--acc);font-weight:600;margin-top:6px}
.kbd{background:#232534;border:1px solid var(--line);border-radius:7px;padding:3px 9px;font-weight:600;font-size:13px;white-space:nowrap}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(20px);opacity:0;background:#262a45;border:1px solid #3b4272;color:#fff;padding:12px 18px;border-radius:12px;max-width:560px;white-space:pre-line;transition:.25s;box-shadow:0 10px 30px #0008;z-index:9}
#toast.show{opacity:1;transform:translateX(-50%)}
.fontpv{font-size:22px;padding:6px 0;color:#fff;min-height:40px}
.muted{color:var(--muted)}
@media (max-width:760px){aside{width:64px;padding:14px 6px}nav button{justify-content:center}nav button em,.brand em,.side-bottom .btn em{display:none}main{padding:18px}}
</style></head><body>
<aside>
  <div class="brand"><i></i><em>Keyboard Sound</em></div>
  <nav id="nav"></nav>
  <div class="side-bottom">
    <button class="btn" id="editBtn"></button>
    <button class="btn bad" id="quitBtn"></button>
  </div>
</aside>
<main id="main"></main>
<div id="toast"></div>
<script>
const TOKEN = "%%TOKEN%%", LANG = "%%LANG%%";
const T = {
 cs: {sound:"Zvuk", look:"Vzhled", widgets:"Widgety", rl:"Rocket League", music:"Hudba", games:"Hry", obs:"OBS (stream)", system:"Systém",
  edit_on:"✓ Hotovo (uložit pozice)", edit_off:"✥ Přesunout widgety", quit:"Ukončit aplikaci", quit_q:"Opravdu ukončit Keyboard Sound?",
  sound_sub:"Zvuk při každém stisku klávesy.", sound_on:"Zvuk kláves", which:"Zvuk", custom:"Vlastní", upload:"Nahrát vlastní zvuk…", test:"▶ Vyzkoušet",
  volume:"Hlasitost kliků", mouse:"Cvakat i při klikání myší", vol_hint:"Ctrl+Alt+PgUp / PgDn",
  look_sub:"Světelné efekty a barvy klávesnice a ostatních widgetů.", effect:"Efekt", color:"Barva", kstyle:"Vzhled klávesnice", opacity:"Průhlednost widgetů", show:"Zobrazit widgety",
  w_sub:"Každý widget zvlášť: zapnout, velikost, možnosti. Přesouvá se tlačítkem vlevo dole (nebo Ctrl+Alt+F11).", size:"Velikost", home:"Na původní místo", home_all:"Všechny na původní místo",
  arrange:"Rozmístit do rohu", add_text:"+ Text", add_image:"+ Obrázek", remove:"Odstranit", options:"Možnosti", numpad:"Numpad", bg:"Pozadí", fur:"Barva kočky",
  analog:"Ručičkové", seconds:"Sekundy", timer:"Minutka", cancel:"Zrušit", text:"Text", save:"Uložit", tcolor:"Barva textu", bold:"Tučně", change_img:"Změnit obrázek…",
  reset_today:"Vynulovat dnešek", reset_best:"Vynulovat rekord", reset_count:"Vynulovat počítadlo", reset:"Vynulovat", pause:"Pozastavit / pokračovat", reset_wins:"Vynulovat výhry", reset_jumps:"Vynulovat skoky",
  rl_sub:"Počítadlo skóre a boost přes ty herní. Vzhled, písmo a průhlednost mají společné.", rl_look:"Vzhled", font:"Písmo", font_rec:"Doporučená", font_hand:"Rukopis", font_all:"Všechna písma",
  names:"Názvy týmů", rl_opacity:"Průhlednost skóre a boostu", rl_game:"Nastavení hry", live:"Živá data pro widgety", border:"Celá obrazovka bez okraje (widgety vidět)",
  live_hint:"Zavři hru, zapni, pak hru spusť.", now_rate:"teď {}× za s",
  music_sub:"Spotify, YouTube a další přehrávače.", shortcuts:"Zkratky", change:"Změnit", press_now:"Stiskni novou zkratku… (Esc = zrušit)", reset_binds:"Obnovit výchozí",
  seek:"Krok posunu", vstep:"Krok hlasitosti", ads:"Ztlumit reklamy ve Spotify", no_media:"Ovládání hudby není k dispozici.",
  games_sub:"Aby byly widgety ve hře vidět, hra musí běžet v okně bez okraje.", fn_win:"Fortnite: celá obrazovka v okně",
  obs_sub:"Widgety jako zdroj Prohlížeč v OBS – uvidí je i diváci.", obs_on:"Widgety pro OBS", obs_url:"Adresa", copy:"Kopírovat", obs_how:"V OBS: Zdroje → + → Prohlížeč, vlož adresu, šířka {} a výška {}.",
  system_sub:"Spouštění a nápověda.", autostart:"Spouštět s Windows", stats:"Dnešní statistiky", keys:"kláves", clicks:"kliků", best:"rekord CPS", cat:"kočka",
  help:"Nápověda", help_text:"Ctrl+Alt+F11 – přesunout widgety (táhni, kolečko = velikost, pravý klik = možnosti)\nCtrl+Alt+F12 – vypnout\nCtrl+Alt+PgUp / PgDn – hlasitost kliků\nCtrl+Alt+šipky – hudba",
  copied:"Zkopírováno", saved:"Uloženo", hidden:"skrytý", only_game:"jen ve hře",
  theme:"Téma", theme_sub:"Vzhled a písmo všech widgetů. Každý widget může mít i vlastní (Widgety → Možnosti).", gfont:"Písmo všech widgetů",
  custom_t:"Vlastní téma", c_bg:"Pozadí", c_bga:"Průhlednost pozadí", c_border:"Okraj", c_text:"Text", c_muted:"Popisky", c_nums:"Čísla",
  c_radius:"Zaoblení", b_color:"Barva", b_glow:"Podle efektu", b_rainbow:"Duha", b_pastel:"Pastel", b_neon:"Neon", b_none:"Žádný",
  n_color:"Barva", n_effect:"Podle efektu", n_rainbow:"Duha", n_pastel:"Pastel", wtheme:"Téma widgetu", wfont:"Písmo widgetu",
  same_as_main:"Podle hlavního", fortnite:"Fortnite", fn_sub:"Čas kola a počítadlo truhel. Fortnite neposílá data, proto se čte jeho log a obrazovka.",
  fm_title:"Čas kola", chests:"Truhly", fn_auto:"Poznávat truhly samo (čte nápis SEARCH u zaměřovače)", fn_key:"Ruční +1 tlačítko",
  plus:"+1", minus:"−1", reset_match:"Vynulovat zápas", reset_all:"Vynulovat vše", reset_fm:"Vynulovat dnešní zápasy",
  interval:"Připomínat každých", drink_games:"Jen když hraju hru", drank:"Napil jsem se", fn_win:"Fortnite: celá obrazovka v okně"},
 en: {sound:"Sound", look:"Look", widgets:"Widgets", rl:"Rocket League", music:"Music", games:"Games", obs:"OBS (stream)", system:"System",
  edit_on:"✓ Done (save positions)", edit_off:"✥ Move widgets", quit:"Quit the app", quit_q:"Really quit Keyboard Sound?",
  sound_sub:"A click on every key press.", sound_on:"Key sound", which:"Sound", custom:"Your own", upload:"Load your own sound…", test:"▶ Try it",
  volume:"Click volume", mouse:"Click sound on mouse buttons too", vol_hint:"Ctrl+Alt+PgUp / PgDn",
  look_sub:"Light effects and colors of the keyboard and the other widgets.", effect:"Effect", color:"Color", kstyle:"Keyboard look", opacity:"Widget opacity", show:"Show widgets",
  w_sub:"Every widget on its own: on/off, size, options. Move them with the button bottom left (or Ctrl+Alt+F11).", size:"Size", home:"Back to its place", home_all:"All back to their places",
  arrange:"Arrange in a corner", add_text:"+ Text", add_image:"+ Image", remove:"Remove", options:"Options", numpad:"Numpad", bg:"Background", fur:"Cat color",
  analog:"Analog", seconds:"Seconds", timer:"Timer", cancel:"Cancel", text:"Text", save:"Save", tcolor:"Text color", bold:"Bold", change_img:"Change image…",
  reset_today:"Reset today", reset_best:"Reset record", reset_count:"Reset counter", reset:"Reset", pause:"Pause / resume", reset_wins:"Reset wins", reset_jumps:"Reset jumps",
  rl_sub:"Score counter and boost meter over the game's own. They share look, font and opacity.", rl_look:"Look", font:"Font", font_rec:"Recommended", font_hand:"Handwriting", font_all:"All fonts",
  names:"Team names", rl_opacity:"Score and boost opacity", rl_game:"Game settings", live:"Live data for the widgets", border:"Borderless fullscreen (widgets visible)",
  live_hint:"Close the game, turn on, then start the game.", now_rate:"now {}× per s",
  music_sub:"Spotify, YouTube and other players.", shortcuts:"Shortcuts", change:"Change", press_now:"Press the new shortcut… (Esc = cancel)", reset_binds:"Reset to default",
  seek:"Seek step", vstep:"Volume step", ads:"Mute Spotify ads", no_media:"Music control is not available.",
  games_sub:"Widgets are only visible over a game running in borderless / windowed fullscreen.", fn_win:"Fortnite: windowed fullscreen",
  obs_sub:"Widgets as a Browser source in OBS – your viewers see them too.", obs_on:"Widgets for OBS", obs_url:"Address", copy:"Copy", obs_how:"In OBS: Sources → + → Browser, paste the address, width {} and height {}.",
  system_sub:"Start-up and help.", autostart:"Start with Windows", stats:"Today's stats", keys:"keys", clicks:"clicks", best:"CPS record", cat:"cat",
  help:"Help", help_text:"Ctrl+Alt+F11 – move widgets (drag, wheel = size, right-click = options)\nCtrl+Alt+F12 – quit\nCtrl+Alt+PgUp / PgDn – click volume\nCtrl+Alt+arrows – music",
  copied:"Copied", saved:"Saved", hidden:"hidden", only_game:"only in the game",
  theme:"Theme", theme_sub:"Look and font of all widgets. Every widget can also have its own (Widgets → Options).", gfont:"Font of all widgets",
  custom_t:"Custom theme", c_bg:"Background", c_bga:"Background opacity", c_border:"Border", c_text:"Text", c_muted:"Labels", c_nums:"Numbers",
  c_radius:"Roundness", b_color:"Color", b_glow:"Follow the effect", b_rainbow:"Rainbow", b_pastel:"Pastel", b_neon:"Neon", b_none:"None",
  n_color:"Color", n_effect:"Follow the effect", n_rainbow:"Rainbow", n_pastel:"Pastel", wtheme:"Widget theme", wfont:"Widget font",
  same_as_main:"Main one", fortnite:"Fortnite", fn_sub:"Match time and chest counter. Fortnite sends no data, so its log and the screen are read.",
  fm_title:"Match time", chests:"Chests", fn_auto:"Recognize chests by itself (reads SEARCH by the crosshair)", fn_key:"Manual +1 button",
  plus:"+1", minus:"−1", reset_match:"Reset match", reset_all:"Reset all", reset_fm:"Reset today's matches",
  interval:"Remind every", drink_games:"Only while I play a game", drank:"I had a drink", fn_win:"Fortnite: windowed fullscreen"}
}[LANG];
const SECTIONS = [["sound","🔊"],["look","🎨"],["theme","🖌️"],["widgets","🧩"],["rl","🏆"],["fortnite","🪂"],["music","🎵"],["games","🎮"],["obs","📡"],["system","⚙️"]];
let S = null, cur = location.hash.slice(1) || "sound", busy = false;
const openDetails = new Set();          // "Options" sections stay open while you change things

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "cls") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}
function toast(msg) { const t = document.getElementById("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), msg.length > 60 ? 6000 : 2200); }
async function api(a, data) {
  busy = true;
  try {
    const r = await fetch("/api/do", {method: "POST", headers: {"Content-Type": "application/json", "X-Token": TOKEN}, body: JSON.stringify({a, ...(data || {})})});
    const j = await r.json();
    if (j.msg) toast(j.msg);
    if (j.state) { S = j.state; render(); }
    return j;
  } catch (e) { toast("⚠ " + e); } finally { busy = false; }
}
const row = (label, ctrl, hint) => h("div", {cls: "row"}, h("div", {cls: "col"}, h("label", {}, label), hint ? h("span", {cls: "hint"}, hint) : null), ctrl);
const toggle = (on, fn) => h("label", {cls: "sw"}, h("input", {type: "checkbox", checked: on, onchange: e => fn(e.target.checked)}), h("b"));
const seg = (opts, val, fn) => h("div", {cls: "seg"}, opts.map(([v, t]) => h("button", {cls: v === val ? "on" : "", onclick: () => fn(v)}, t)));
function slider(min, max, step, val, fmt, fn) {
  const out = h("span", {cls: "hint", style: "min-width:48px;text-align:right"}, fmt(val));
  return h("div", {style: "display:flex;align-items:center;gap:10px"},
    h("input", {type: "range", min, max, step, value: val, oninput: e => out.textContent = fmt(+e.target.value), onchange: e => fn(+e.target.value)}), out);
}
const sel = (opts, val, fn, style) => h("select", {onchange: e => fn(e.target.value), style}, opts.map(o => Array.isArray(o) ? h("option", {value: o[0], selected: o[0] === val}, o[1]) : o));
const btn = (t, fn, cls) => h("button", {cls: "btn " + (cls || ""), onclick: fn}, t);
const card = (title, ...kids) => h("div", {cls: "card"}, title ? h("h3", {}, title) : null, ...kids);
function fontSelect(value, fn, withMain) {
  const F = S.fonts, opt = x => h("option", {value: x, selected: x === value, style: "font-family:'" + x + "'"}, x);
  return h("select", {onchange: e => fn(e.target.value), style: "max-width:240px"},
    withMain ? h("option", {value: "", selected: !value}, T.same_as_main) : null,
    h("optgroup", {label: T.font_rec}, F.rec.map(opt)), h("optgroup", {label: T.font_hand}, F.hand.map(opt)),
    h("optgroup", {label: T.font_all}, F.all.filter(x => !F.rec.includes(x) && !F.hand.includes(x)).map(opt)));
}
function swatch(t) {
  const bg = t.bg ? `rgba(${t.bg.map(v => Math.round(v * 255)).join(",")},${Math.max(t.bg_a, .25)})` : "transparent";
  const bd = t.border_css || "transparent";
  return h("div", {style: `height:54px;border-radius:${8 + t.radius * 14}px;background:${bg};border:2px solid ${bd};display:flex;align-items:center;justify-content:center;gap:6px;font-weight:800;font-size:22px`},
    h("span", {style: `background:${t.nums_css};-webkit-background-clip:text;color:transparent`}, "12:34"),
    h("span", {style: `color:${t.text_css};font-size:12px;font-weight:600`}, "abc"));
}
function pickFile(accept, fn) {
  const i = h("input", {type: "file", accept, style: "display:none"});
  i.onchange = () => { const f = i.files[0]; if (!f) return; const r = new FileReader();
    r.onload = () => fn(f.name, r.result.split(",")[1]); r.readAsDataURL(f); };
  document.body.append(i); i.click(); setTimeout(() => i.remove(), 60000);
}
const pct = v => Math.round(v * 100) + " %";

const SEC = {
 sound() {
  const s = S.sound;
  const choices = s.sounds.map(x => [x.id, x.name]); if (s.custom_name) choices.push(["custom", T.custom + ": " + s.custom_name]);
  return [h("h1", {}, T.sound), h("p", {cls: "sub"}, T.sound_sub), h("div", {cls: "grid"},
   card(null,
    row(T.sound_on, toggle(s.on, v => api("set", {k: "sound", v}))),
    row(T.which, seg(choices, s.choice, v => api("sound_choice", {v}))),
    h("div", {cls: "btns", style: "margin-top:8px"}, btn(T.test, () => api("test_sound")),
      btn(T.upload, () => pickFile(".wav,.mp3,.m4a,.ogg,.flac,.wma,audio/*", (name, data) => api("upload_sound", {name, data}))))),
   card(null,
    row(T.volume, slider(0.1, 2, 0.05, s.volume, pct, v => api("set", {k: "volume", v})), T.vol_hint),
    row(T.mouse, toggle(s.mouse, v => api("set", {k: "mouse_sound", v})))))];
 },
 look() {
  const l = S.look;
  return [h("h1", {}, T.look), h("p", {cls: "sub"}, T.look_sub), h("div", {cls: "grid"},
   card(null,
    row(T.show, toggle(S.visible, v => api("set", {k: "visible", v}))),
    row(T.effect, sel(l.effects.map(e => [e.id, e.name]), l.effect, v => api("set", {k: "effect", v}))),
    h("div", {cls: "row"}, h("label", {}, T.color), h("div", {cls: "sw-list"}, l.colors.map(c => h("div", {cls: "dot" + (c.id === l.color ? " on" : ""), title: c.name,
       style: "background:" + (c.rgb || "conic-gradient(#ff4d6d,#ffb84d,#f9f871,#4dff9a,#4dc3ff,#9b6bff,#ff4d6d)"), onclick: () => api("set", {k: "color", v: c.id})})))),
    row(T.opacity, slider(0.3, 1, 0.05, l.opacity, pct, v => api("set", {k: "opacity", v})))),
   card(T.kstyle, seg(l.styles.map(x => [x.id, x.name]), l.key_style, v => api("key_style", {v}))))];
 },
 theme() {
  const th = S.theme, ct = th.custom;
  const presets = h("div", {cls: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(150px,1fr))"},
    th.list.map(t => h("div", {cls: "card", style: "padding:10px;cursor:pointer;" + (t.id === th.current ? "outline:2px solid var(--acc)" : ""),
      onclick: () => api("theme", {v: t.id})}, swatch(t), h("div", {style: "text-align:center;margin-top:6px;font-weight:600"}, t.name))));
  const color = (k, val) => h("input", {type: "color", value: val, onchange: e => api("custom", {k, v: e.target.value})});
  const isHex = v => /^#/.test(v || "");
  return [h("h1", {}, T.theme), h("p", {cls: "sub"}, T.theme_sub), presets, h("div", {cls: "grid", style: "margin-top:14px"},
   card(T.gfont, row(T.font, fontSelect(th.font, v => api("font", {v}))),
     h("div", {cls: "fontpv", style: "font-family:'" + th.font + "'"}, "Keyboard Sound 12:34")),
   card(T.custom_t,
     row(T.c_bg, color("bg", ct.bg)),
     row(T.c_bga, slider(0, 1, 0.05, ct.bg_a, pct, v => api("custom", {k: "bg_a", v}))),
     row(T.c_border, h("div", {style: "display:flex;gap:8px;align-items:center"},
       sel([["color", T.b_color], ["glow", T.b_glow], ["rainbow", T.b_rainbow], ["pastel", T.b_pastel], ["neon", T.b_neon], ["none", T.b_none]],
         isHex(ct.border) ? "color" : (ct.border || "none"), v => api("custom", {k: "border", v: v === "color" ? "#7d9bff" : v})),
       isHex(ct.border) ? color("border", ct.border) : null)),
     row(T.c_text, color("text", ct.text)), row(T.c_muted, color("muted", ct.muted)),
     row(T.c_nums, h("div", {style: "display:flex;gap:8px;align-items:center"},
       sel([["color", T.n_color], ["effect", T.n_effect], ["rainbow", T.n_rainbow], ["pastel", T.n_pastel]],
         isHex(ct.nums) ? "color" : ct.nums, v => api("custom", {k: "nums", v: v === "color" ? "#ffd166" : v})),
       isHex(ct.nums) ? color("nums", ct.nums) : null)),
     row(T.c_radius, slider(0, 1.2, 0.05, ct.radius, v => Math.round(v * 100) + " %", v => api("custom", {k: "radius", v})))))];
 },
 fortnite() {
  const fm = S.widgets.find(w => w.kind === "fnmatch"), ch = S.widgets.find(w => w.kind === "chests"), dr = S.widgets.find(w => w.kind === "drink");
  const cmd = (w, c) => api("wcmd", {id: w.id, cmd: c});
  return [h("h1", {}, T.fortnite), h("p", {cls: "sub"}, T.fn_sub), h("div", {cls: "grid"},
   fm ? card(T.fm_title, row(T.show, toggle(fm.visible, v => api("w", {id: fm.id, k: "visible", v}))),
     h("div", {cls: "btns"}, btn(T.reset_fm, () => cmd(fm, 890)))) : null,
   ch ? card(T.chests, row(T.show, toggle(ch.visible, v => api("w", {id: ch.id, k: "visible", v}))),
     ch.ocr ? row(T.fn_auto, toggle(ch.auto, v => api("w", {id: ch.id, k: "auto", v}))) : null,
     row(T.fn_key, h("div", {style: "display:flex;gap:8px;align-items:center"}, h("span", {cls: "kbd"}, ch.prompt ? T.press_now : ch.key), btn(T.change, () => cmd(ch, 852)))),
     h("div", {cls: "btns", style: "margin-top:8px"}, btn(T.plus, () => cmd(ch, 850)), btn(T.minus, () => cmd(ch, 851)), btn(T.reset_match, () => cmd(ch, 853)), btn(T.reset_all, () => cmd(ch, 854), "bad"))) : null,
   card("Fortnite", row(T.fn_win, toggle(S.games.fn_windowed, () => api("fn_window"))))) ];
 },
 widgets() {
  const cards = S.widgets.map(w => {
   const set = (k, v) => api("w", {id: w.id, k, v}), cmd = c => api("wcmd", {id: w.id, cmd: c});
   const opts = [];
   if (w.kind === "keyboard" || w.kind === "mouse") opts.push(row(T.kstyle, seg(S.look.styles.map(x => [x.id, x.name]), w.style, v => set("style", v))));
   if (w.kind === "keyboard") { opts.push(row(T.numpad, toggle(w.numpad, v => set("numpad", v)))); opts.push(row(T.bg, toggle(w.background, v => set("background", v)))); }
   if (w.kind === "cat") { opts.push(row(T.fur, seg(S.furs.map(f => [f.id, f.name]), w.fur, v => set("fur", v)))); opts.push(h("div", {cls: "btns"}, btn(T.reset_count, () => cmd(729)))); }
   if (w.kind === "stats") opts.push(h("div", {cls: "btns"}, btn(T.reset_today, () => cmd(710)), btn(T.reset_best, () => cmd(711))));
   if (w.kind === "session") opts.push(h("div", {cls: "btns"}, btn(T.reset, () => cmd(770)), btn(T.pause, () => cmd(771))));
   if (w.kind === "rocket") opts.push(h("div", {cls: "btns"}, btn(T.reset_wins, () => cmd(762))));
   if (w.kind === "dash") opts.push(h("div", {cls: "btns"}, btn(T.reset_jumps, () => cmd(780))));
   if (w.kind === "clock") {
    opts.push(row(T.analog, toggle(w.analog, v => set("analog", v)))); opts.push(row(T.seconds, toggle(w.seconds, v => set("seconds", v))));
    opts.push(row(T.timer, h("div", {cls: "seg"}, [5, 10, 15, 20, 30, 45, 60, 90].map(m => h("button", {cls: w.timer === m ? "on" : "", onclick: () => set("timer", m)}, m + "")),
      w.timer ? h("button", {onclick: () => set("timer", 0)}, T.cancel) : null)));
   }
   if (w.kind === "text") {
    const ta = h("textarea", {}, w.text || "");
    opts.push(ta, h("div", {cls: "btns"}, btn(T.save, () => set("text", ta.value), "pri")));
    opts.push(row(T.tcolor, sel(w.colors.map(c => [c.id, c.name]), w.color, v => set("color", v))));
    opts.push(row(T.bold, toggle(w.bold, v => set("bold", v)))); opts.push(row(T.bg, toggle(w.background, v => set("background", v))));
   }
   if (w.kind === "image") opts.push(h("div", {cls: "btns"}, btn(T.change_img, () => pickFile("image/*", (name, data) => api("image", {id: w.id, name, data})))));
   if (w.kind === "drink") {
    opts.push(row(T.interval, seg([15, 20, 30, 45, 60, 90].map(m => [m, m + " min"]), w.interval, v => set("interval", v))));
    opts.push(row(T.drink_games, toggle(w.only_games, v => set("only_games", v))));
    opts.push(h("div", {cls: "btns"}, btn(T.drank, () => cmd(880), "pri"), btn(T.reset_today, () => cmd(888))));
   }
   if (w.kind === "fnmatch") opts.push(h("div", {cls: "btns"}, btn(T.reset_fm, () => cmd(890))));
   if (w.kind === "chests") opts.push(h("div", {cls: "btns"}, btn(T.plus, () => cmd(850)), btn(T.minus, () => cmd(851)), btn(T.reset_match, () => cmd(853))));
   if (w.themeable) {
    opts.push(row(T.wtheme, sel([["", T.same_as_main], ...S.theme.list.map(t => [t.id, t.name])], w.theme || "", v => set("theme", v))));
    opts.push(row(T.wfont, fontSelect(w.wfont || "", v => set("wfont", v), true)));
   }
   return card(null, h("div", {cls: "wcard"},
    h("div", {cls: "wtitle"}, h("b", {}, w.label), h("span", {style: "display:flex;gap:8px;align-items:center"}, w.game ? h("span", {cls: "tag"}, T.only_game) : null, toggle(w.visible, v => set("visible", v)))),
    row(T.size, slider(10, 120, 2, w.size, v => v, v => set("size", v))),
    opts.length ? h("details", {"data-id": w.id, open: openDetails.has(w.id), ontoggle: e => e.target.open ? openDetails.add(w.id) : openDetails.delete(w.id)}, h("summary", {}, T.options), ...opts) : null,
    h("div", {cls: "btns", style: "margin-top:8px"}, btn(T.home, () => api("home", {id: w.id})), w.removable ? btn(T.remove, () => api("remove", {id: w.id}), "bad") : null)));
  });
  const corners = S.corners.map(c => [c.id, c.name]);
  return [h("h1", {}, T.widgets), h("p", {cls: "sub"}, T.w_sub),
   h("div", {cls: "btns", style: "margin-bottom:14px"}, btn(T.add_text, () => { const t = prompt(T.text, "GG!"); if (t) api("add_text", {text: t}); }, "pri"),
     btn(T.add_image, () => pickFile("image/*", (name, data) => api("add_image", {name, data})), "pri"), btn(T.home_all, () => api("home_all")),
     sel([[ "", T.arrange + "…"], ...corners], "", v => v && api("arrange", {v}))),
   h("div", {cls: "grid"}, cards)];
 },
 rl() {
  const r = S.rl, f = r.font;
  const fontSel = fontSelect(f, v => api("rl", {k: "font", v}));
  return [h("h1", {}, T.rl), h("p", {cls: "sub"}, T.rl_sub), h("div", {cls: "grid"},
   card(null,
    row(T.rl_look, seg(r.modes.map(m => [m.id, m.name]), r.mode, v => api("rl", {k: "mode", v}))),
    row(T.font, fontSel), h("div", {cls: "fontpv", style: "font-family:'" + f + "';font-weight:" + (r.bold ? 700 : 400)}, "2 : 1   4:37   67"),
    row(T.bold, toggle(r.bold, v => api("rl", {k: "bold", v}))),
    row(T.names, toggle(r.names, v => api("rl", {k: "names", v}))),
    row(T.rl_opacity, slider(0.3, 1, 0.05, r.opacity, pct, v => api("rl", {k: "opacity", v})))),
   card(T.rl_game,
    row(T.live, toggle(r.live_on, () => api("rl_live")), r.rate > 0 && r.rate < 10 ? T.now_rate.replace("{}", r.rate) : T.live_hint),
    row(T.border, toggle(r.borderless, () => api("rl_border")))))];
 },
 music() {
  const m = S.music;
  if (!m.available) return [h("h1", {}, T.music), h("p", {cls: "sub"}, T.no_media)];
  return [h("h1", {}, T.music), h("p", {cls: "sub"}, T.music_sub), h("div", {cls: "grid"},
   card(T.shortcuts, m.binds.map(b => row(b.label, h("div", {style: "display:flex;gap:8px;align-items:center"},
      m.capturing === b.act ? h("span", {cls: "hint"}, T.press_now) : h("span", {cls: "kbd"}, b.text), btn(T.change, () => api("bind", {act: b.act}))))),
     h("div", {cls: "btns", style: "margin-top:8px"}, btn(T.reset_binds, () => api("bind_reset")))),
   card(null,
    row(T.ads, toggle(m.mute_ads, v => api("set", {k: "mute_ads", v}))),
    row(T.seek, seg([[5, "5 s"], [10, "10 s"], [15, "15 s"], [30, "30 s"]], m.seek_step, v => api("set", {k: "seek_step", v}))),
    row(T.vstep, seg([[2, "2 %"], [5, "5 %"], [10, "10 %"]], m.music_step, v => api("set", {k: "music_step", v})))))];
 },
 games() {
  const g = S.games;
  return [h("h1", {}, T.games), h("p", {cls: "sub"}, T.games_sub), h("div", {cls: "grid"},
   card("Rocket League", row(T.border, toggle(S.rl.borderless, () => api("rl_border"))), row(T.live, toggle(S.rl.live_on, () => api("rl_live")), T.live_hint)),
   card("Fortnite", row(T.fn_win, toggle(g.fn_windowed, () => api("fn_window")))))];
 },
 obs() {
  const o = S.obs;
  return [h("h1", {}, T.obs), h("p", {cls: "sub"}, T.obs_sub), h("div", {cls: "grid"},
   card(null, row(T.obs_on, toggle(o.on, v => api("obs", {v}))),
    o.on ? h("div", {}, row(T.obs_url, h("div", {style: "display:flex;gap:8px"}, h("span", {cls: "kbd"}, o.url),
      btn(T.copy, () => { navigator.clipboard.writeText(o.url).then(() => toast(T.copied)); }))),
      h("p", {cls: "hint"}, T.obs_how.replace("{}", o.w).replace("{}", o.h))) : null))];
 },
 system() {
  const st = S.stats;
  return [h("h1", {}, T.system), h("p", {cls: "sub"}, T.system_sub), h("div", {cls: "grid"},
   card(null, S.frozen ? row(T.autostart, toggle(S.autostart, v => api("autostart", {v}))) : null,
     h("p", {style: "white-space:pre-line", cls: "muted"}, T.help_text)),
   card(T.stats, h("p", {}, `${st.keys} ${T.keys} · ${st.clicks} ${T.clicks} · ${T.best} ${st.best_cps} · ${T.cat} ${st.cat}`)))];
 }
};
function render() {
  if (!S) return;
  const nav = document.getElementById("nav"); nav.innerHTML = "";
  for (const [id, icon] of SECTIONS) nav.append(h("button", {cls: id === cur ? "on" : "", onclick: () => { cur = id; location.hash = id; render(); }}, h("span", {}, icon), h("em", {}, T[id])));
  const main = document.getElementById("main"); const y = main.scrollTop; main.innerHTML = "";
  main.append(...SEC[cur]());
  main.scrollTop = y;
  const eb = document.getElementById("editBtn"); eb.textContent = S.editing ? T.edit_on : T.edit_off; eb.className = "btn " + (S.editing ? "pri" : "");
  document.getElementById("quitBtn").textContent = T.quit;
}
document.getElementById("editBtn").onclick = () => api("edit");
document.getElementById("quitBtn").onclick = () => { if (confirm(T.quit_q)) api("quit").then(() => window.close()); };
api("state");
setInterval(() => {                     // keep in sync with changes made elsewhere (tray menu, hotkeys)
  const a = document.activeElement;
  if (!busy && !(a && /INPUT|TEXTAREA|SELECT/.test(a.tagName))) api("state");
}, 2500);
</script></body></html>
'''


def browser_for_app_window():
    """Edge (on every Windows 10/11) or Chrome, to show the main menu as its own app window."""
    candidates = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for exe in ("msedge.exe", "chrome.exe"):
            try:
                with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    candidates.append(winreg.QueryValue(k, None))
            except OSError:
                pass
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                       os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def open_main_menu():
    if not web_start():
        user32.MessageBoxW(None, T("obs_fail").format(OBS_PORT), APP_NAME, 0x30)
        return
    url = f"http://127.0.0.1:{web_port}/menu?t={WEB_TOKEN}"
    exe = browser_for_app_window()
    try:
        if exe:
            import subprocess
            subprocess.Popen([exe, f"--app={url}", "--window-size=1100,800", "--new-window"],
                             creationflags=0x08000000)
            return
    except OSError:
        pass
    os.startfile(url)


_fonts_cache = []


def installed_fonts():
    """Names of the fonts installed on this PC (for the font list in the main menu)."""
    if _fonts_cache:
        return _fonts_cache
    names = set()
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LOGFONTW), ctypes.c_void_p, wintypes.DWORD,
                                   wintypes.LPARAM)

    def found(lf, tm, kind, lparam):
        name = lf.contents.lfFaceName
        if name and not name.startswith("@") and kind & 4:            # TrueType / OpenType only (GDI+ can draw them)
            names.add(name)
        return 1
    cb = proc_type(found)
    lf = LOGFONTW()
    lf.lfCharSet = 1
    dc = user32.GetDC(None)
    gdi32.EnumFontFamiliesExW(dc, ctypes.byref(lf), cb, 0, 0)
    user32.ReleaseDC(None, dc)
    _fonts_cache.extend(sorted(names, key=str.lower))
    return _fonts_cache


def set_clipboard(text):
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    h = kernel32.GlobalAlloc(0x2, size)
    ptr = kernel32.GlobalLock(h)
    ctypes.memmove(ptr, data, size)
    kernel32.GlobalUnlock(h)
    if user32.OpenClipboard(None):
        user32.EmptyClipboard()
        user32.SetClipboardData(13, h)                 # CF_UNICODETEXT
        user32.CloseClipboard()


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


class LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long), ("lfEscapement", ctypes.c_long),
                ("lfOrientation", ctypes.c_long), ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_ubyte),
                ("lfUnderline", ctypes.c_ubyte), ("lfStrikeOut", ctypes.c_ubyte), ("lfCharSet", ctypes.c_ubyte),
                ("lfOutPrecision", ctypes.c_ubyte), ("lfClipPrecision", ctypes.c_ubyte), ("lfQuality", ctypes.c_ubyte),
                ("lfPitchAndFamily", ctypes.c_ubyte), ("lfFaceName", ctypes.c_wchar * 32)]


class CHOOSEFONTW(ctypes.Structure):
    _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hDC", wintypes.HDC),
                ("lpLogFont", ctypes.POINTER(LOGFONTW)), ("iPointSize", ctypes.c_int), ("Flags", wintypes.DWORD),
                ("rgbColors", wintypes.DWORD), ("lCustData", wintypes.LPARAM), ("lpfnHook", ctypes.c_void_p),
                ("lpTemplateName", wintypes.LPCWSTR), ("hInstance", wintypes.HINSTANCE), ("lpszStyle", wintypes.LPWSTR),
                ("nFontType", wintypes.WORD), ("_align", wintypes.WORD), ("nSizeMin", ctypes.c_int),
                ("nSizeMax", ctypes.c_int)]


def pick_font(current, bold):
    """Windows' own font dialog; returns (font name, bold) or None."""
    lf = LOGFONTW()
    lf.lfFaceName = (current or "Segoe UI")[:31]
    lf.lfWeight = 700 if bold else 400
    lf.lfHeight = -24
    cf = CHOOSEFONTW()
    cf.lStructSize = ctypes.sizeof(CHOOSEFONTW)
    cf.lpLogFont = ctypes.pointer(lf)
    cf.Flags = 0x1 | 0x40 | 0x40000 | 0x1000000 | 0x800000     # screen fonts, start at current, TrueType, no vertical, no size
    if not ctypes.windll.comdlg32.ChooseFontW(ctypes.byref(cf)):
        return None
    return lf.lfFaceName, lf.lfWeight >= 600


def pick_sound_file():
    return pick_file(T("pick_title"), T("pick_filter"), "*.wav;*.mp3;*.m4a;*.ogg;*.flac;*.wma;*.aac")


def pick_file(title, filter_name, patterns):
    """Standard Windows 'Open' dialog. Returns the chosen path or None."""
    filt = f"{filter_name}\0{patterns}\0\0"
    filt_buf = (ctypes.c_wchar * len(filt))(*filt)
    file_buf = ctypes.create_unicode_buffer(1024)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFilter = ctypes.cast(filt_buf, wintypes.LPCWSTR)
    ofn.lpstrFile = ctypes.cast(file_buf, wintypes.LPWSTR)
    ofn.nMaxFile = 1024
    ofn.lpstrTitle = title
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


# ---------------------------------------------------------------- GDI+ canvas (smooth shapes and text with real transparency)
REAL = ctypes.c_float
_vp = ctypes.c_void_p
for _name, _args in (
    ("GdiplusStartup", [_vp, _vp, _vp]),
    ("GdipCreateBitmapFromScan0", [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, _vp, _vp]),
    ("GdipGetImageGraphicsContext", [_vp, _vp]),
    ("GdipDeleteGraphics", [_vp]),
    ("GdipDisposeImage", [_vp]),
    ("GdipGraphicsClear", [_vp, wintypes.DWORD]),
    ("GdipSetSmoothingMode", [_vp, ctypes.c_int]),
    ("GdipSetTextRenderingHint", [_vp, ctypes.c_int]),
    ("GdipSetInterpolationMode", [_vp, ctypes.c_int]),
    ("GdipSetPixelOffsetMode", [_vp, ctypes.c_int]),
    ("GdipCreateSolidFill", [wintypes.DWORD, _vp]),
    ("GdipSetSolidFillColor", [_vp, wintypes.DWORD]),
    ("GdipDeleteBrush", [_vp]),
    ("GdipCreatePen1", [wintypes.DWORD, REAL, ctypes.c_int, _vp]),
    ("GdipCreatePen2", [_vp, REAL, ctypes.c_int, _vp]),
    ("GdipSetClipPath", [_vp, _vp, ctypes.c_int]),
    ("GdipResetClip", [_vp]),
    ("GdipDeletePen", [_vp]),
    ("GdipSetPenLineJoin", [_vp, ctypes.c_int]),
    ("GdipSetPenStartCap", [_vp, ctypes.c_int]),
    ("GdipSetPenEndCap", [_vp, ctypes.c_int]),
    ("GdipCreatePath", [ctypes.c_int, _vp]),
    ("GdipDeletePath", [_vp]),
    ("GdipResetPath", [_vp]),
    ("GdipAddPathArc", [_vp, REAL, REAL, REAL, REAL, REAL, REAL]),
    ("GdipAddPathPolygon", [_vp, _vp, ctypes.c_int]),
    ("GdipClosePathFigure", [_vp]),
    ("GdipSetPathFillMode", [_vp, ctypes.c_int]),
    ("GdipFillPath", [_vp, _vp, _vp]),
    ("GdipDrawPath", [_vp, _vp, _vp]),
    ("GdipFillEllipse", [_vp, _vp, REAL, REAL, REAL, REAL]),
    ("GdipDrawEllipse", [_vp, _vp, REAL, REAL, REAL, REAL]),
    ("GdipDrawLine", [_vp, _vp, REAL, REAL, REAL, REAL]),
    ("GdipDrawArc", [_vp, _vp, REAL, REAL, REAL, REAL, REAL, REAL]),
    ("GdipCreateFontFamilyFromName", [wintypes.LPCWSTR, _vp, _vp]),
    ("GdipDeleteFontFamily", [_vp]),
    ("GdipCreateStringFormat", [ctypes.c_int, wintypes.WORD, _vp]),
    ("GdipDeleteStringFormat", [_vp]),
    ("GdipSetStringFormatAlign", [_vp, ctypes.c_int]),
    ("GdipSetStringFormatLineAlign", [_vp, ctypes.c_int]),
    ("GdipAddPathString", [_vp, wintypes.LPCWSTR, ctypes.c_int, _vp, ctypes.c_int, REAL, _vp, _vp]),
    ("GdipGetPathWorldBounds", [_vp, _vp, _vp, _vp]),
    ("GdipCreateLineBrush", [_vp, _vp, wintypes.DWORD, wintypes.DWORD, ctypes.c_int, _vp]),
    ("GdipSetLinePresetBlend", [_vp, _vp, _vp, ctypes.c_int]),
    ("GdipLoadImageFromFile", [wintypes.LPCWSTR, _vp]),
    ("GdipGetImageWidth", [_vp, _vp]),
    ("GdipGetImageHeight", [_vp, _vp]),
    ("GdipDrawImageRectI", [_vp, _vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
    ("GdipImageGetFrameCount", [_vp, _vp, _vp]),
    ("GdipImageSelectActiveFrame", [_vp, _vp, ctypes.c_uint]),
    ("GdipGetPropertyItemSize", [_vp, ctypes.c_uint, _vp]),
    ("GdipGetPropertyItem", [_vp, ctypes.c_uint, ctypes.c_uint, _vp]),
    ("GdipSaveImageToStream", [_vp, _vp, _vp, _vp]),
    ("GdipCreateImageAttributes", [_vp]),
    ("GdipSetImageAttributesColorKeys", [_vp, ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD]),
    ("GdipDisposeImageAttributes", [_vp]),
    ("GdipDrawImageRectRectI", [_vp, _vp] + [ctypes.c_int] * 8 + [ctypes.c_int, _vp, _vp, _vp]),
):
    getattr(gdiplus, _name).argtypes = _args


class RectF(ctypes.Structure):
    _fields_ = [("X", REAL), ("Y", REAL), ("Width", REAL), ("Height", REAL)]


class PointF(ctypes.Structure):
    _fields_ = [("X", REAL), ("Y", REAL)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class PropertyItem(ctypes.Structure):
    _fields_ = [("id", ctypes.c_ulong), ("length", ctypes.c_ulong), ("type", ctypes.c_ushort), ("value", ctypes.c_void_p)]


gdi32.CreateDIBSection.argtypes = [HDC, _vp, wintypes.UINT, _vp, wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, HDC, _vp, _vp, HDC, _vp, wintypes.COLORREF, _vp, wintypes.DWORD]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
FRAME_DIMENSION_TIME = GUID("6aedbd6d-3fb5-418a-83a6-7f45229dc872")


def ensure_gdiplus():
    if not _gdiplus_token.value:
        gdiplus.GdiplusStartup(ctypes.byref(_gdiplus_token), ctypes.byref(GdiplusStartupInput(1, None, 0, 0)), None)


def argb(rgb, a=1.0):
    r, g, b = (max(0, min(255, int(c * 255))) for c in rgb)
    return (max(0, min(255, int(a * 255))) << 24) | (r << 16) | (g << 8) | b


class Canvas:
    """A 32-bit see-through bitmap you draw on with GDI+ and put on screen with per-pixel transparency."""

    def __init__(self, w, h):
        ensure_gdiplus()
        self.w, self.h = w, h
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        screen = user32.GetDC(None)
        self.dc = gdi32.CreateCompatibleDC(screen)
        self.hbmp = gdi32.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
        user32.ReleaseDC(None, screen)
        gdi32.SelectObject(self.dc, self.hbmp)
        self.bmp, self.g = ctypes.c_void_p(), ctypes.c_void_p()
        gdiplus.GdipCreateBitmapFromScan0(w, h, w * 4, 0xE200B, self.bits, ctypes.byref(self.bmp))   # 32bpp PARGB
        gdiplus.GdipGetImageGraphicsContext(self.bmp, ctypes.byref(self.g))
        gdiplus.GdipSetSmoothingMode(self.g, 4)            # anti-aliased shapes
        gdiplus.GdipSetTextRenderingHint(self.g, 4)        # anti-aliased text
        gdiplus.GdipSetInterpolationMode(self.g, 7)
        gdiplus.GdipSetPixelOffsetMode(self.g, 2)
        self.brush, self.path, self.fmt = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(0, ctypes.byref(self.brush))
        gdiplus.GdipCreatePath(0, ctypes.byref(self.path))
        gdiplus.GdipCreateStringFormat(0, 0, ctypes.byref(self.fmt))
        self.families = {}
        self.font = "Segoe UI"                 # font used when a text call names none (the widget's theme font)
        self._theme = None

    @property
    def theme(self):
        return self._theme or default_theme()

    @theme.setter
    def theme(self, value):
        self._theme = value

    def family(self, name):
        if name not in self.families:
            fam = ctypes.c_void_p()
            if gdiplus.GdipCreateFontFamilyFromName(name, None, ctypes.byref(fam)):
                gdiplus.GdipCreateFontFamilyFromName("Segoe UI", None, ctypes.byref(fam))
            self.families[name] = fam
        return self.families[name]

    def clear(self, color=0):
        gdiplus.GdipGraphicsClear(self.g, color)

    def _fill(self, color):
        gdiplus.GdipSetSolidFillColor(self.brush, color)
        return self.brush

    def _pen(self, color, width):
        pen = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(color, width, 0, ctypes.byref(pen))
        gdiplus.GdipSetPenLineJoin(pen, 2)
        gdiplus.GdipSetPenStartCap(pen, 2)
        gdiplus.GdipSetPenEndCap(pen, 2)
        return pen

    def _round_path(self, x, y, w, h, r):
        p = self.path
        gdiplus.GdipResetPath(p)
        r = max(0.5, min(r, w / 2, h / 2))
        d = 2 * r
        gdiplus.GdipAddPathArc(p, x, y, d, d, 180, 90)
        gdiplus.GdipAddPathArc(p, x + w - d, y, d, d, 270, 90)
        gdiplus.GdipAddPathArc(p, x + w - d, y + h - d, d, d, 0, 90)
        gdiplus.GdipAddPathArc(p, x, y + h - d, d, d, 90, 90)
        gdiplus.GdipClosePathFigure(p)

    def round_rect(self, x, y, w, h, r, fill=None, stroke=None, width=1.0):
        self._round_path(x, y, w, h, r)
        if fill is not None:
            gdiplus.GdipFillPath(self.g, self._fill(fill), self.path)
        if stroke is not None:
            pen = self._pen(stroke, width)
            gdiplus.GdipDrawPath(self.g, pen, self.path)
            gdiplus.GdipDeletePen(pen)

    def ellipse(self, x, y, w, h, fill=None, stroke=None, width=1.0):
        if fill is not None:
            gdiplus.GdipFillEllipse(self.g, self._fill(fill), x, y, w, h)
        if stroke is not None:
            pen = self._pen(stroke, width)
            gdiplus.GdipDrawEllipse(self.g, pen, x, y, w, h)
            gdiplus.GdipDeletePen(pen)

    def polygon(self, pts, fill=None, stroke=None, width=1.0):
        arr = (PointF * len(pts))(*[PointF(px, py) for px, py in pts])
        gdiplus.GdipResetPath(self.path)
        gdiplus.GdipAddPathPolygon(self.path, arr, len(pts))
        if fill is not None:
            gdiplus.GdipFillPath(self.g, self._fill(fill), self.path)
        if stroke is not None:
            pen = self._pen(stroke, width)
            gdiplus.GdipDrawPath(self.g, pen, self.path)
            gdiplus.GdipDeletePen(pen)

    def line(self, x1, y1, x2, y2, color, width):
        pen = self._pen(color, width)
        gdiplus.GdipDrawLine(self.g, pen, x1, y1, x2, y2)
        gdiplus.GdipDeletePen(pen)

    def arc(self, x, y, w, h, start, sweep, color, width):
        """color = one ARGB color or a gradient list (left -> right across the circle)."""
        if isinstance(color, list):
            brush = self.line_brush(x, y, x + w, y + h, color)
            pen = ctypes.c_void_p()
            gdiplus.GdipCreatePen2(brush, width, 0, ctypes.byref(pen))
            gdiplus.GdipSetPenStartCap(pen, 2)
            gdiplus.GdipSetPenEndCap(pen, 2)
            gdiplus.GdipDrawArc(self.g, pen, x, y, w, h, start, sweep)
            gdiplus.GdipDeletePen(pen)
            gdiplus.GdipDeleteBrush(brush)
            return
        pen = self._pen(color, width)
        gdiplus.GdipDrawArc(self.g, pen, x, y, w, h, start, sweep)
        gdiplus.GdipDeletePen(pen)

    def gradient(self, x1, x2, colors):
        """Horizontal multi-color brush (caller deletes it)."""
        brush = ctypes.c_void_p()
        p1, p2 = PointF(x1, 0), PointF(max(x2, x1 + 1), 0)
        gdiplus.GdipCreateLineBrush(ctypes.byref(p1), ctypes.byref(p2), colors[0], colors[-1], 0, ctypes.byref(brush))
        n = len(colors)
        gdiplus.GdipSetLinePresetBlend(brush, (wintypes.DWORD * n)(*colors),
                                       (REAL * n)(*[i / (n - 1) for i in range(n)]), n)
        return brush

    def line_brush(self, x1, y1, x2, y2, colors):
        """Gradient brush from (x1, y1) to (x2, y2) through all colors (caller deletes it)."""
        brush = ctypes.c_void_p()
        if abs(x2 - x1) < 1 and abs(y2 - y1) < 1:
            x2 = x1 + 1
        p1, p2 = PointF(x1, y1), PointF(x2, y2)
        gdiplus.GdipCreateLineBrush(ctypes.byref(p1), ctypes.byref(p2), colors[0], colors[-1], 0, ctypes.byref(brush))
        n = len(colors)
        if n > 2:
            gdiplus.GdipSetLinePresetBlend(brush, (wintypes.DWORD * n)(*colors),
                                           (REAL * n)(*[i / (n - 1) for i in range(n)]), n)
        return brush

    def shape(self, flat, fill=None, stroke=None, width=1.0, vertical=True, stroke_vertical=False):
        """Polygon from a flat [x0, y0, x1, y1, ...] list; fill / stroke = one ARGB color or a gradient list."""
        n = len(flat) // 2
        arr = (PointF * n)(*[PointF(flat[2 * i], flat[2 * i + 1]) for i in range(n)])
        xs, ys = flat[0::2], flat[1::2]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        gdiplus.GdipResetPath(self.path)
        gdiplus.GdipAddPathPolygon(self.path, arr, n)
        for paint, is_fill, vert in ((fill, True, vertical), (stroke, False, stroke_vertical)):
            if paint is None:
                continue
            brush = None
            if isinstance(paint, list):
                brush = self.line_brush(x1, y1, x1 if vert else x2, y2 if vert else y1, paint)
            if is_fill:
                gdiplus.GdipFillPath(self.g, brush or self._fill(paint), self.path)
            else:
                pen = ctypes.c_void_p()
                if brush:
                    gdiplus.GdipCreatePen2(brush, width, 0, ctypes.byref(pen))
                    gdiplus.GdipSetPenLineJoin(pen, 2)
                else:
                    pen = self._pen(paint, width)
                gdiplus.GdipDrawPath(self.g, pen, self.path)
                gdiplus.GdipDeletePen(pen)
            if brush:
                gdiplus.GdipDeleteBrush(brush)

    def clip(self, flat):
        """Only draw inside this polygon until unclip()."""
        n = len(flat) // 2
        arr = (PointF * n)(*[PointF(flat[2 * i], flat[2 * i + 1]) for i in range(n)])
        gdiplus.GdipResetPath(self.path)
        gdiplus.GdipAddPathPolygon(self.path, arr, n)
        gdiplus.GdipSetClipPath(self.g, self.path, 0)

    def unclip(self):
        gdiplus.GdipResetClip(self.g)

    def gradient_rect(self, x, y, w, h, r, fill=None, stroke=None, width=1.0, vertical=True):
        """Rounded rectangle filled and / or outlined with gradients (lists of ARGB colors)."""
        self._round_path(x, y, w, h, r)
        x2, y2 = (x, y + h) if vertical else (x + w, y)
        if fill:
            brush = self.line_brush(x, y, x2, y2, fill)
            gdiplus.GdipFillPath(self.g, brush, self.path)
            gdiplus.GdipDeleteBrush(brush)
        if stroke:
            brush = self.line_brush(x, y, x + w, y, stroke)
            pen = ctypes.c_void_p()
            gdiplus.GdipCreatePen2(brush, width, 0, ctypes.byref(pen))
            gdiplus.GdipDrawPath(self.g, pen, self.path)
            gdiplus.GdipDeletePen(pen)
            gdiplus.GdipDeleteBrush(brush)

    def _text_path(self, text, x, y, w, h, size, bold, align, valign, font):
        gdiplus.GdipResetPath(self.path)
        gdiplus.GdipSetStringFormatAlign(self.fmt, align)          # 0 near, 1 center, 2 far
        gdiplus.GdipSetStringFormatLineAlign(self.fmt, valign)
        rect = RectF(x, y, w, h)
        gdiplus.GdipSetPathFillMode(self.path, 1)                  # winding: no holes where letter parts overlap
        if gdiplus.GdipAddPathString(self.path, text, -1, self.family(font), 1 if bold else 0, size,
                                     ctypes.byref(rect), self.fmt) and bold:     # font has no bold: use regular
            gdiplus.GdipResetPath(self.path)
            gdiplus.GdipSetPathFillMode(self.path, 1)
            gdiplus.GdipAddPathString(self.path, text, -1, self.family(font), 0, size, ctypes.byref(rect), self.fmt)

    def text(self, text, x, y, w, h, size, fill, bold=True, align=1, valign=1, outline=None, outline_w=0.0,
             font=None):
        """fill = one ARGB color or a list of ARGB colors (horizontal gradient across the box)."""
        if not text:
            return
        font = font or self.font
        self._text_path(text, x, y, w, h, size, bold, align, valign, font)
        if outline is not None and outline_w > 0:
            pen = self._pen(outline, outline_w)
            gdiplus.GdipDrawPath(self.g, pen, self.path)
            gdiplus.GdipDeletePen(pen)
        if isinstance(fill, list):
            brush = self.gradient(x, x + w, fill)
            gdiplus.GdipFillPath(self.g, brush, self.path)
            gdiplus.GdipDeleteBrush(brush)
        else:
            gdiplus.GdipFillPath(self.g, self._fill(fill), self.path)

    def measure(self, text, size, bold=True, font=None):
        self._text_path(text or " ", 0, 0, 100000, 100000, size, bold, 0, 0, font or self.font)
        r = RectF()
        gdiplus.GdipGetPathWorldBounds(self.path, ctypes.byref(r), None, None)
        return r.X, r.Y, r.Width, r.Height

    def image(self, img, x, y, w, h):
        gdiplus.GdipDrawImageRectI(self.g, img, int(x), int(y), int(w), int(h))

    def present(self, hwnd, opacity):
        size, src = wintypes.SIZE(self.w, self.h), wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, max(1, min(255, int(opacity * 255))), 1)
        user32.UpdateLayeredWindow(hwnd, None, None, ctypes.byref(size), self.dc, ctypes.byref(src), 0,
                                   ctypes.byref(blend), 2)

    def close(self):
        for fam in self.families.values():
            gdiplus.GdipDeleteFontFamily(fam)
        gdiplus.GdipDeleteStringFormat(self.fmt)
        gdiplus.GdipDeletePath(self.path)
        gdiplus.GdipDeleteBrush(self.brush)
        gdiplus.GdipDeleteGraphics(self.g)
        gdiplus.GdipDisposeImage(self.bmp)
        gdi32.DeleteObject(self.hbmp)
        gdi32.DeleteDC(self.dc)


# ---------------------------------------------------------------- small dialogs
class TextDialog:
    """A tiny window with a text box, OK and Cancel (Windows has no ready-made one)."""
    _proc = None

    @classmethod
    def ask(cls, title, prompt, initial=""):
        self = cls()
        return self.run(title, prompt, initial)

    def run(self, title, prompt, initial):
        hinst = kernel32.GetModuleHandleW(None)
        self.done, self.value = False, None
        self._wndproc = WNDPROC(self.wndproc)
        self.class_name = f"KeyboardSoundText{id(self)}"
        wc = WNDCLASSW(0, self._wndproc, 0, 0, hinst, None, user32.LoadCursorW(None, ctypes.c_void_p(32512)),
                       wintypes.HBRUSH(16), None, self.class_name)              # COLOR_BTNFACE + 1
        user32.RegisterClassW(ctypes.byref(wc))
        dpi = user32.GetDpiForSystem() / 96.0
        w, h = int(440 * dpi), int(230 * dpi)
        area = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)
        x, y = (area.left + area.right - w) // 2, (area.top + area.bottom - h) // 2
        style = 0x80000000 | 0x00C00000 | 0x00080000 | 0x10000000             # POPUP | CAPTION | SYSMENU | VISIBLE
        cw = user32.CreateWindowExW
        cw.restype = wintypes.HWND
        self.hwnd = cw(0x8 | 0x1, self.class_name, title, style, x, y, w, h, None, None, hinst, None)
        font = gdi32.CreateFontW(-int(15 * dpi), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        child = 0x40000000 | 0x10000000                                          # CHILD | VISIBLE
        m = int(14 * dpi)
        inner_w = w - 2 * m - int(16 * dpi)
        label = cw(0, "STATIC", prompt, child, m, m, inner_w, int(22 * dpi), self.hwnd, None, hinst, None)
        self.edit = cw(0x200, "EDIT", initial, child | 0x00800000 | 0x00010000 | 0x4 | 0x40 | 0x1000 | 0x00200000,
                       m, m + int(26 * dpi), inner_w, int(90 * dpi), self.hwnd, wintypes.HMENU(100), hinst, None)
        bw, bh = int(100 * dpi), int(30 * dpi)
        by = m + int(126 * dpi)
        ok = cw(0, "BUTTON", T("ok"), child | 0x00010000 | 0x1, inner_w + m - 2 * bw - int(8 * dpi), by, bw, bh,
                self.hwnd, wintypes.HMENU(1), hinst, None)
        cancel = cw(0, "BUTTON", T("cancel"), child | 0x00010000, inner_w + m - bw, by, bw, bh,
                    self.hwnd, wintypes.HMENU(2), hinst, None)
        for ctl in (label, self.edit, ok, cancel):
            user32.SendMessageW(ctl, 0x0030, font, 1)                           # WM_SETFONT
        user32.SetForegroundWindow(self.hwnd)
        user32.SetFocus(self.edit)
        user32.SendMessageW(self.edit, 0x00B1, 0, -1)                           # select all
        msg = wintypes.MSG()
        while not self.done:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:   # app is quitting: pass it on
                user32.PostQuitMessage(0)
                user32.DestroyWindow(self.hwnd)
                break
            if not user32.IsDialogMessageW(self.hwnd, ctypes.byref(msg)):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        gdi32.DeleteObject(font)
        ctypes.windll.user32.UnregisterClassW(ctypes.c_wchar_p(self.class_name), ctypes.c_void_p(hinst))
        return self.value

    def wndproc(self, hwnd, msg, wparam, lparam):
        if msg == 0x0111:                                                       # WM_COMMAND
            cid = wparam & 0xFFFF
            if cid in (1, 2):
                if cid == 1:
                    n = user32.GetWindowTextLengthW(self.edit)
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(self.edit, buf, n + 1)
                    self.value = buf.value.replace("\r\n", "\n")
                self.done = True
                user32.DestroyWindow(hwnd)
                return 0
        if msg == 0x0010:                                                       # WM_CLOSE
            self.done = True
            user32.DestroyWindow(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


for _name, _args, _res in (
    ("SendMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], LRESULT),
    ("SetFocus", [wintypes.HWND], wintypes.HWND),
    ("IsDialogMessageW", [wintypes.HWND, ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
    ("GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
    ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
):
    getattr(user32, _name).argtypes, getattr(user32, _name).restype = _args, _res


# ---------------------------------------------------------------- widgets
EX_BASE = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000      # LAYERED | TOPMOST | TOOLWINDOW | NOACTIVATE
ACCENT = (0.49, 0.61, 1.0)


class Widget:
    """One movable thing on screen, in its own see-through always-on-top window."""
    kind = ""
    interactive = False          # True = reacts to mouse clicks (the player); others let clicks go through
    argb = False                 # True = drawn with GDI+ (per-pixel transparency)

    def __init__(self, app, wid):
        self.app, self.id = app, wid
        self.w = self.h = 1
        self.shown = False
        self.editing = False
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self.wndproc)
        self.class_name = f"KeyboardSound{type(self).__name__}{id(self)}"
        cursor = 32649 if self.interactive else 32646                            # hand / move
        wc = WNDCLASSW(0, self._wndproc, 0, 0, hinst, None, user32.LoadCursorW(None, ctypes.c_void_p(cursor)),
                       None, None, self.class_name)
        user32.RegisterClassW(ctypes.byref(wc))
        ex = EX_BASE | (0 if self.interactive else 0x20)                        # 0x20 = clicks go through
        self.hwnd = user32.CreateWindowExW(ex, self.class_name, f"Keyboard Sound {self.kind}", 0x80000000,
                                           0, 0, 1, 1, None, None, hinst, None)
        self.screen_dc = user32.GetDC(self.hwnd)

    # ---------- config / geometry
    @property
    def wcfg(self):
        return self.app.cfg["widgets"][self.id]

    @property
    def u(self):
        return self.wcfg["size"] * self.app.dpi

    def label(self):
        return T("w_" + self.kind)

    def place(self):
        x, y = self.wcfg.get("x"), self.wcfg.get("y")
        if x is None or y is None:
            x, y = self.app.auto_place(self)
            self.wcfg["x"], self.wcfg["y"] = int(x), int(y)
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), int(x), int(y), self.w, self.h, 0x10)

    def rect(self):
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r

    def remember_position(self):
        r = self.rect()
        self.wcfg["x"], self.wcfg["y"] = r.left, r.top

    # ---------- visibility / editing
    def has_content(self):
        return True

    def refresh(self):
        want = bool(self.app.cfg["visible"] and self.wcfg.get("visible", True)
                    and (self.has_content() or self.app.editing))
        if want != self.shown:
            self.shown = want
            user32.ShowWindow(self.hwnd, 4 if want else 0)
        if want:
            self.redraw()

    def set_editing(self, on):
        self.editing = on
        style = user32.GetWindowLongPtrW(self.hwnd, -20)
        through = not on and not self.interactive
        user32.SetWindowLongPtrW(self.hwnd, -20, style | 0x20 if through else style & ~0x20)
        self.refresh()

    def keep_on_top(self):
        if self.shown:
            user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

    # ---------- hooks for subclasses
    def build(self):
        pass

    def redraw(self):
        pass

    def tick(self, now):
        pass

    def on_input(self, kind, val):
        pass

    def on_mouse(self, msg, x, y):
        return False

    def menu_items(self, add, checked):
        """Extra right-click menu items (ids 700+)."""

    def menu_command(self, cmd):
        return False

    def paint(self, hdc):
        pass

    # ---------- window messages
    def wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST and self.editing:
                return 2                                       # HTCAPTION: Windows drags the widget for us
            if msg == WM_NCRBUTTONUP and self.editing:
                self.app.widget_menu(self)
                return 0
            if msg == WM_EXITSIZEMOVE:
                self.remember_position()
                save_settings(self.app.cfg)
                return 0
            if msg == WM_PAINT:
                ps = ctypes.create_string_buffer(72)
                hdc = user32.BeginPaint(hwnd, ps)
                self.paint(hdc)
                user32.EndPaint(hwnd, ps)
                return 0
            if msg in (0x0200, 0x0201, 0x02A3) and not self.editing:
                x = ctypes.c_short(lparam & 0xFFFF).value
                y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                if self.on_mouse(msg, x, y):
                    return 0
        except Exception as e:
            print(f"{self.kind} widget error:", e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def close(self):
        user32.ReleaseDC(self.hwnd, self.screen_dc)
        user32.DestroyWindow(self.hwnd)
        ctypes.windll.user32.UnregisterClassW(ctypes.c_wchar_p(self.class_name),
                                              ctypes.c_void_p(kernel32.GetModuleHandleW(None)))


class GdiWidget(Widget):
    """Widget drawn with plain GDI into a back buffer; one exact color (TRANSPARENT_KEY) is see-through."""

    def make_buffer(self):
        if getattr(self, "buf_dc", None):
            gdi32.DeleteObject(self.buf_bmp)
            gdi32.DeleteDC(self.buf_dc)
        self.buf_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self.buf_bmp = gdi32.CreateCompatibleBitmap(self.screen_dc, self.w, self.h)
        gdi32.SelectObject(self.buf_dc, self.buf_bmp)
        gdi32.SelectObject(self.buf_dc, gdi32.GetStockObject(18))
        gdi32.SelectObject(self.buf_dc, gdi32.GetStockObject(19))
        gdi32.SetBkMode(self.buf_dc, 1)
        user32.SetLayeredWindowAttributes(self.hwnd, TRANSPARENT_KEY, int(self.app.cfg["opacity"] * 255), 3)
        if not getattr(self, "label_font", None):
            self.label_font = gdi32.CreateFontW(-int(13 * self.app.dpi), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0,
                                                "Segoe UI")

    def present(self):
        gdi32.BitBlt(self.screen_dc, 0, 0, self.w, self.h, self.buf_dc, 0, 0, SRCCOPY)

    def paint(self, hdc):
        gdi32.BitBlt(hdc, 0, 0, self.w, self.h, self.buf_dc, 0, 0, SRCCOPY)

    def draw_edit_frame(self, dc):
        """Blue frame + name while the widgets are being moved."""
        gdi32.SelectObject(dc, gdi32.GetStockObject(5))                          # hollow brush
        gdi32.SetDCPenColor(dc, cref(ACCENT))
        for i in range(2):
            gdi32.Rectangle(dc, i, i, self.w - i, self.h - i)
        gdi32.SelectObject(dc, gdi32.GetStockObject(18))
        gdi32.SelectObject(dc, self.label_font)
        text = self.label()
        lw = int((len(text) * 8 + 16) * self.app.dpi)
        lh = int(20 * self.app.dpi)
        gdi32.SetDCBrushColor(dc, cref(ACCENT))
        gdi32.Rectangle(dc, 0, 0, lw, lh)
        gdi32.SetTextColor(dc, 0x101010)
        user32.DrawTextW(dc, text, -1, ctypes.byref(wintypes.RECT(0, 0, lw, lh)), DT_FLAGS)

    def close(self):
        if getattr(self, "buf_dc", None):
            gdi32.DeleteObject(self.buf_bmp)
            gdi32.DeleteDC(self.buf_dc)
        super().close()


class ArgbWidget(Widget):
    """Widget drawn with GDI+ (soft edges, real transparency)."""
    argb = True

    def make_canvas(self, w, h):
        if getattr(self, "canvas", None):
            self.canvas.close()
        self.w, self.h = max(1, int(w)), max(1, int(h))
        self.canvas = Canvas(self.w, self.h)

    def redraw(self):
        c = self.canvas
        th = self.app.theme_for(self)
        c.theme, c.font = th, th.font
        c.clear()
        if self.editing:
            c.round_rect(1, 1, self.w - 2, self.h - 2, 8 * self.app.dpi, fill=argb((0.05, 0.05, 0.1), 0.45))
        self.draw(c, time.monotonic())
        if self.editing:
            d = self.app.dpi
            c.round_rect(1, 1, self.w - 2, self.h - 2, 8 * d, stroke=argb(ACCENT), width=2 * d)
            text = self.label()
            _, _, tw, _ = c.measure(text, 12 * d)
            c.round_rect(2, 2, tw + 14 * d, 20 * d, 5 * d, fill=argb(ACCENT))
            c.text(text, 2, 2, tw + 14 * d, 20 * d, 12 * d, argb((0.06, 0.06, 0.06)))
        c.present(self.hwnd, self.opacity())

    def draw(self, c, now):
        pass

    def opacity(self):
        return self.app.cfg["opacity"]

    def close(self):
        if getattr(self, "canvas", None):
            self.canvas.close()
        super().close()


# ---------------------------------------------------------------- keyboard and mouse widgets
class KeysWidget(GdiWidget):
    """The RGB keyboard or the mouse: keys light up when pressed."""

    def __init__(self, app, wid, kind):
        self.kind = kind
        super().__init__(app, wid)
        self.held, self.released_at, self.active = set(), {}, set()
        self.ripples = []
        self.fonts, self.bitmaps = [], []
        self.hkl = None
        self.dirty = True
        self.last_rgb = 0.0
        self.ticks = 0
        self.build()

    def build(self):
        u = self.u
        pad = 0.35 * u
        if self.kind == "keyboard":
            keys, width_u = keyboard_layout(0, self.wcfg.get("numpad", True))
            height_u = 6.25
        else:
            keys, width_u, height_u = mouse_layout(0, 0.0), MOUSE_W, 5.7
        self.keys = keys
        self.w, self.h = int(width_u * u + 2 * pad), int(height_u * u + 2 * pad)

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
        face = self.app.theme_for(self).font
        face = "Segoe UI Semibold" if face == "Segoe UI" else face
        mk = lambda px: gdi32.CreateFontW(-max(7, int(px)), 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 4, 0, face)
        self.fonts = [mk(u * 0.42), mk(u * 0.27)]
        for dc, bmp in self.bitmaps:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(dc)
        self.bitmaps = []
        for _ in range(5):
            dc = gdi32.CreateCompatibleDC(self.screen_dc)
            bmp = gdi32.CreateCompatibleBitmap(self.screen_dc, self.w, self.h)
            gdi32.SelectObject(dc, bmp)
            gdi32.SelectObject(dc, gdi32.GetStockObject(18))     # DC_BRUSH
            gdi32.SelectObject(dc, gdi32.GetStockObject(19))     # DC_PEN
            gdi32.SetBkMode(dc, 1)
            self.bitmaps.append((dc, bmp))
        self.base_dc, self.static_dc, self.text_dc, self.outline_dc, self.tmp_dc = [dc for dc, _ in self.bitmaps]
        self.tri = (TRIVERTEX * (2 * GRADIENT_STEPS))()
        self.mesh = (GRADIENT_RECT * GRADIENT_STEPS)(*[(2 * i, 2 * i + 1) for i in range(GRADIENT_STEPS)])
        self.make_buffer()
        self.place()
        self.hkl = None
        self.refresh_labels()
        self.build_layers()
        self.active = {i for kid in self.held for i in self.by_id.get(kid, ())}
        self.dirty = True
        self.refresh()

    @property
    def style(self):
        st = self.wcfg.get("style", "rgb")
        return st if st in KEY_STYLES else "rgb"

    def menu_items(self, add, checked):
        for i, st in enumerate(KEY_STYLES):
            add(checked(self.style == st), 702 + i, f"{T('style')}: {T('st_' + st)}")
        if self.kind == "keyboard":
            add(0, 700, T("numpad_hide" if self.wcfg.get("numpad", True) else "numpad_show"))
            add(0, 701, T("bg_hide" if self.wcfg.get("background") else "bg_show"))

    def menu_command(self, cmd):
        if 702 <= cmd < 702 + len(KEY_STYLES):
            self.wcfg["style"] = KEY_STYLES[cmd - 702]
        elif cmd == 700:
            self.wcfg["numpad"] = not self.wcfg.get("numpad", True)
        elif cmd == 701:
            self.wcfg["background"] = not self.wcfg.get("background")
        else:
            return False
        self.build()
        return True

    # ---------- drawing (pre-drawn masks tinted with the effect colors; see build_layers)
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

    def key_colors(self, key, now, glow):
        app = self.app
        if self.style != "rgb":
            fill, outline, text, p_fill, p_outline, p_text = key["style"]
            return (cref(mix(fill, p_fill, glow)), cref(mix(outline, p_outline, glow)), cref(mix(text, p_text, glow)))
        color = app.glow_color(key["xfrac"], now)
        idle = mix(DARK, color, IDLE_BRIGHTNESS * app.idle_level(now))
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
        dc = self.static_dc
        self.fill_all(dc, TRANSPARENT_KEY)
        if self.style != "rgb":                   # pastel / black: one still picture, no color masks needed
            if self.wcfg.get("background"):
                plate = ((0.97, 0.96, 1.0), (0.84, 0.82, 0.92)) if self.style == "pastel" else \
                        ((0.035, 0.035, 0.04), (0.17, 0.17, 0.2))
                gdi32.SetDCBrushColor(dc, cref(plate[0]))
                gdi32.SetDCPenColor(dc, cref(plate[1]))
                gdi32.Polygon(dc, *self.plate)
            th = self.app.theme_for(self)
            acc = th.accent(self.app, 0.0)
            for key in self.keys:
                key["style"] = style_colors(self.style, key, th, acc)
                fill, outline, text = key["style"][:3]
                if key.get("body"):
                    gdi32.SetDCBrushColor(dc, cref(fill))
                    gdi32.SetDCPenColor(dc, cref(outline))
                    gdi32.Polygon(dc, *key["poly"])
                else:
                    self.draw_key(dc, key, (cref(fill), cref(outline), cref(text)))
            return
        if self.wcfg.get("background"):
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
            r, g, b = mix(self.app.base_color(i / n, now), WHITE, lift)
            col = (int(r * brightness * 0xFF00), int(g * brightness * 0xFF00), int(b * brightness * 0xFF00))
            x = int(self.w * i / n)
            for j, y in ((2 * i - 1, self.h), (2 * i, 0)):
                if 0 <= j < 2 * n:
                    v = self.tri[j]
                    v.x, v.y, v.Red, v.Green, v.Blue = x, y, *col
        msimg32.GradientFill(dc, self.tri, 2 * n, self.mesh, n, 0)

    def render_base(self, now):
        w, h = self.w, self.h
        gdi32.BitBlt(self.base_dc, 0, 0, w, h, self.static_dc, 0, 0, SRCCOPY)
        if self.style != "rgb":
            return
        level = self.app.idle_level(now)
        for mask, lift, brightness in ((self.outline_dc, 0.0, IDLE_BRIGHTNESS * level),
                                       (self.text_dc, 0.3, max(level, 0.35))):
            self.rainbow(self.tmp_dc, now, lift, brightness)
            gdi32.BitBlt(self.tmp_dc, 0, 0, w, h, mask, 0, 0, SRCAND)
            gdi32.BitBlt(self.base_dc, 0, 0, w, h, self.tmp_dc, 0, 0, SRCPAINT)

    def render(self, force_base=False):
        if not self.shown:
            return
        now = time.monotonic()
        base_changed = False
        if force_base or (self.style == "rgb" and self.app.animated() and now - self.last_rgb >= 1.0 / RGB_FPS):
            self.render_base(now)
            self.last_rgb = now
            base_changed = True
        if not (base_changed or self.active or self.ripples or self.dirty):
            return
        self.dirty = False
        dc = self.buf_dc
        gdi32.BitBlt(dc, 0, 0, self.w, self.h, self.base_dc, 0, 0, SRCCOPY)
        glows = {}
        for i in list(self.active):
            kid = self.keys[i]["id"]
            glow = 1.0 if kid in self.held else max(0.0, 1.0 - (now - self.released_at.get(kid, -99.0)) / GLOW_TIME)
            if glow <= 0:
                self.active.discard(i)
                self.dirty = True
                continue
            glows[i] = glow
        if self.ripples:
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
            self.draw_edit_frame(dc)
        self.present()

    def redraw(self):
        self.dirty = True
        self.render(force_base=True)

    def tick(self, now):
        self.render()
        if self.kind == "keyboard":
            self.ticks += 1
            if self.ticks % FPS == 0 and self.refresh_labels():
                self.build_layers()
                self.render(force_base=True)

    def on_input(self, kind, kid):
        if kind == "down":
            self.held.add(kid)
        elif kind in ("up", "tap"):
            self.held.discard(kid)
            self.released_at[kid] = time.monotonic()
        else:
            return
        idx = self.by_id.get(kid)
        if idx:
            self.active.update(idx)
            if kind in ("down", "tap") and self.app.cfg["effect"] == "ripple":
                key = self.keys[idx[0]]
                self.ripples = self.ripples[-11:] + [(key["cpx"], key["cpy"], time.monotonic())]

    def close(self):
        for f in self.fonts:
            gdi32.DeleteObject(f)
        for dc, bmp in self.bitmaps:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(dc)
        super().close()


# ---------------------------------------------------------------- music player widget
class PlayerWidget(GdiWidget):
    """Cover, title, artist, buttons, volume and a progress bar you can click."""
    kind = "player"
    interactive = True
    BG = (0.05, 0.05, 0.07)

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.state = None
        self.prompt = None
        self.cover, self.cover_key = None, None
        self.hover = None
        self.last_draw = 0.0
        self.hold_until = 0.0
        self.fonts = []
        self.build()

    def has_content(self):
        return bool(MEDIA_OK and (self.state or self.prompt))

    def refresh(self):
        want = bool(self.prompt) or bool(self.app.cfg["visible"] and self.wcfg.get("visible", True)
                                         and (self.has_content() or self.app.editing))
        if want != self.shown:                          # the "press a shortcut" message always shows
            self.shown = want
            user32.ShowWindow(self.hwnd, 4 if want else 0)
        if want:
            self.redraw()

    def build(self):
        u = self.u
        self.w, self.h = int(13 * u), int(2.5 * u)
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
        self.artist_rect = wintypes.RECT(tx, pad + int(0.66 * u), left_of_buttons, pad + int(1.2 * u))
        info = (pad + int(1.12 * u), pad + int(1.58 * u))          # under the buttons, above the bar
        self.time_rect = wintypes.RECT(right - int(2.7 * u), info[0], right, info[1])
        self.vol_rect = wintypes.RECT(right - int(4.7 * u), info[0], right - int(2.8 * u), info[1])
        self.bar = (tx, self.h - pad - int(0.3 * u), right - tx, max(4, int(0.2 * u)))
        self.prompt_rect = wintypes.RECT(tx, pad, right, self.h - pad)
        for f in self.fonts:
            gdi32.DeleteObject(f)
        mk = lambda px, weight, face: gdi32.CreateFontW(-max(7, int(px)), 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 4, 0, face)
        th = self.app.theme_for(self)
        face = th.font
        self.BG = th.bg if th.bg is not None and th.bg_a >= 0.3 else PlayerWidget.BG
        dark_bg = sum(self.BG) / 3 < 0.5
        self.FG = th.text_rgb if dark_bg == th.dark else ((0.95, 0.95, 0.98) if dark_bg else (0.12, 0.12, 0.18))
        self.MUTED = th.muted_rgb if dark_bg == th.dark else mix(self.FG, self.BG, 0.35)
        self.fonts = [mk(u * 0.42, 700, face), mk(u * 0.34, 400, face),
                      mk(u * 0.3, 600, face), mk(u * 0.42, 400, "Segoe MDL2 Assets"),
                      mk(u * 0.8, 400, "Segoe UI Symbol"), mk(u * 0.34, 400, "Segoe MDL2 Assets")]
        self.make_buffer()
        self.plate = self.poly([(0, 0), (self.w - 1, 0), (self.w - 1, self.h - 1), (0, self.h - 1)], 0.35 * u)
        self.place()
        self.refresh()

    def poly(self, pts, r):
        flat = rounded_points(pts, [r] * len(pts), 4)
        arr = (wintypes.POINT * (len(flat) // 2))(*[wintypes.POINT(int(flat[i]), int(flat[i + 1]))
                                                    for i in range(0, len(flat), 2)])
        return arr, len(arr)

    # ---------- state from the music thread
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
        if state and self.state and time.monotonic() < self.hold_until:   # keep our instant change a moment
            state = {**state, "pos": self.position(), "t": time.monotonic(), "playing": self.state["playing"],
                     "volume": self.state.get("volume"), "muted": self.state.get("muted")}
        self.state = state
        self.refresh()

    def position(self):
        st = self.state
        if not st:
            return 0.0
        pos = st["pos"] + (time.monotonic() - st["t"] if st["playing"] else 0.0)
        return min(pos, st["end"]) if st["end"] > 0 else pos

    def _optimistic(self, **changes):
        if self.state:
            self.state = {**self.state, "pos": self.position(), "t": time.monotonic(), **changes}
            self.hold_until = time.monotonic() + 0.8
            self.refresh()

    def nudge(self, delta):
        if self.state and self.state["can_seek"]:
            self._optimistic(pos=max(0.0, self.position() + delta))

    def toggle_playing(self):
        if self.state:
            self._optimistic(playing=not self.state["playing"])

    def change_volume(self, delta):
        if self.state and self.state.get("volume") is not None:
            self._optimistic(volume=max(0.0, min(1.0, self.state["volume"] + delta)), muted=False)

    def tick(self, now):
        if self.shown and self.state and self.state["playing"] and now - self.last_draw > 0.25:
            self.redraw()

    # ---------- drawing
    def redraw(self):
        self.last_draw = time.monotonic()
        dc, u, app = self.buf_dc, self.u, self.app
        now = time.monotonic()
        accent = app.glow_color(0.15, now)
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
            gdi32.SetDCBrushColor(dc, cref(mix(self.BG, self.FG, 0.07)))
            gdi32.SetDCPenColor(dc, cref(mix(self.BG, self.FG, 0.07)))
            gdi32.Polygon(dc, *self.poly([(pad, pad), (pad + art, pad), (pad + art, pad + art), (pad, pad + art)],
                                         0.2 * u))
            gdi32.SelectObject(dc, self.fonts[4])
            gdi32.SetTextColor(dc, cref(accent))
            user32.DrawTextW(dc, "♪", -1, ctypes.byref(wintypes.RECT(pad, pad, pad + art, pad + art)), 0x1 | 0x4 | 0x20)
        st = self.state
        if self.prompt or not st:
            if self.prompt:
                gdi32.SelectObject(dc, self.fonts[0])
                gdi32.SetTextColor(dc, cref(self.FG))
                user32.DrawTextW(dc, self.prompt, -1, ctypes.byref(self.prompt_rect), 0x10 | 0x800)
            if self.editing:
                self.draw_edit_frame(dc)
            self.present()
            return
        ellipsis = 0x20 | 0x4 | 0x800 | 0x8000
        muted_text = cref(self.MUTED)
        gdi32.SelectObject(dc, self.fonts[0])
        gdi32.SetTextColor(dc, cref(self.FG))
        user32.DrawTextW(dc, st["title"] or T("nothing_playing"), -1, ctypes.byref(self.title_rect), ellipsis)
        gdi32.SelectObject(dc, self.fonts[1])
        if st.get("ad"):
            gdi32.SetTextColor(dc, cref(accent))
            user32.DrawTextW(dc, T("ad_muted") if st.get("ad_muted") else T("ad_playing"), -1,
                             ctypes.byref(self.artist_rect), ellipsis)
        else:
            gdi32.SetTextColor(dc, muted_text)
            user32.DrawTextW(dc, st["artist"], -1, ctypes.byref(self.artist_rect), ellipsis)

        pos, end = self.position(), st["end"]
        fmt = lambda v: f"{int(v) // 60}:{int(v) % 60:02d}"
        gdi32.SelectObject(dc, self.fonts[2])
        gdi32.SetTextColor(dc, muted_text)
        user32.DrawTextW(dc, f"{fmt(pos)} / {fmt(end)}" if end > 0 else "", -1, ctypes.byref(self.time_rect),
                         0x2 | 0x20 | 0x4 | 0x800)
        vol = st.get("volume")
        if vol is not None:                                     # speaker + percent (click = mute)
            muted = st.get("muted")
            hot = self.hover == "vol"
            gdi32.SelectObject(dc, self.fonts[5])
            gdi32.SetTextColor(dc, cref(WHITE if hot else (accent if not muted else (0.9, 0.35, 0.35))))
            r = self.vol_rect
            glyph = "" if muted or vol <= 0.001 else ("" if vol < 0.34 else ("" if vol < 0.67 else ""))
            user32.DrawTextW(dc, glyph, -1, ctypes.byref(wintypes.RECT(r.left, r.top, r.left + int(0.55 * u), r.bottom)),
                             0x1 | 0x4 | 0x20)
            gdi32.SelectObject(dc, self.fonts[2])
            gdi32.SetTextColor(dc, cref(WHITE) if hot else muted_text)
            user32.DrawTextW(dc, f"{round(vol * 100)} %", -1,
                             ctypes.byref(wintypes.RECT(r.left + int(0.6 * u), r.top, r.right, r.bottom)), 0x20 | 0x4)
        for name, (cx, cy, r) in self.buttons.items():
            hot = self.hover == name
            fill = mix(self.BG, accent, 0.85) if name == "play" else (mix(self.BG, WHITE, 0.14) if hot else self.BG)
            if name == "play" and hot:
                fill = mix(accent, WHITE, 0.25)
            gdi32.SetDCBrushColor(dc, cref(fill))
            gdi32.SetDCPenColor(dc, cref(fill))
            gdi32.Ellipse(dc, cx - r, cy - r, cx + r, cy + r)
            glyph = {"prev": "", "next": "", "play": "" if st["playing"] else ""}[name]
            gdi32.SelectObject(dc, self.fonts[3])
            gdi32.SetTextColor(dc, cref(INK if name == "play" else self.FG))
            user32.DrawTextW(dc, glyph, -1, ctypes.byref(wintypes.RECT(cx - r, cy - r, cx + r, cy + r)), 0x1 | 0x4 | 0x20)
        x, y, w, h = self.bar
        gdi32.SetDCBrushColor(dc, cref(mix(self.BG, self.FG, 0.12)))
        gdi32.SetDCPenColor(dc, cref(mix(self.BG, self.FG, 0.12)))
        gdi32.Polygon(dc, *self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], h / 2))
        frac = min(1.0, pos / end) if end > 0 else 0.0
        filled = int(w * frac)
        if filled > 2:
            dim = 1.0 if st["can_seek"] else 0.55
            for i in range(0, filled, 3):
                c = app.glow_color(0.05 + 0.9 * i / max(1, w), now)
                col = cref((c[0] * dim, c[1] * dim, c[2] * dim))
                gdi32.SetDCBrushColor(dc, col)
                gdi32.SetDCPenColor(dc, col)
                gdi32.Rectangle(dc, x + i, y + 1, x + min(i + 3, filled), y + h - 1)
            if self.hover == "bar" and st["can_seek"]:
                gdi32.SetDCBrushColor(dc, 0xFFFFFF)
                gdi32.SetDCPenColor(dc, 0xFFFFFF)
                gdi32.Ellipse(dc, x + filled - h, y - h // 2, x + filled + h, y + h + h // 2)
        if self.editing:
            self.draw_edit_frame(dc)
        self.present()

    # ---------- mouse
    def hit(self, mx, my):
        for name, (cx, cy, r) in self.buttons.items():
            if (mx - cx) ** 2 + (my - cy) ** 2 <= r * r:
                return name
        r = self.vol_rect
        if self.state and self.state.get("volume") is not None and r.left <= mx <= r.right and r.top <= my <= r.bottom:
            return "vol"
        x, y, w, h = self.bar
        if x - h <= mx <= x + w + h and y - int(0.35 * self.u) <= my <= y + h + int(0.35 * self.u):
            return "bar"
        return None

    def on_mouse(self, msg, mx, my):
        if msg == 0x0201:                                       # left click
            if not self.state or self.prompt:
                return True
            target = self.hit(mx, my)
            if target in ("prev", "next", "play"):
                self.app.media_action(target)
            elif target == "vol":
                media_commands.put(("mute_toggle", None))
                self._optimistic(muted=not self.state.get("muted"))
            elif target == "bar" and self.state["can_seek"]:
                x, _, w, _ = self.bar
                seconds = max(0.0, min(1.0, (mx - x) / w)) * self.state["end"]
                media_commands.put(("seek_to", seconds))
                self.nudge(seconds - self.position())
            return True
        if msg == 0x0200:                                       # mouse move
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x2, self.hwnd, 0)
            ctypes.windll.user32.TrackMouseEvent(ctypes.byref(tme))
            target = self.hit(mx, my)
            if target != self.hover:
                self.hover = target
                self.redraw()
            return True
        if msg == 0x02A3:                                       # mouse left the player
            self.hover = None
            self.redraw()
            return True
        return False

    def close(self):
        if self.cover:
            self.cover.close()
        for f in self.fonts:
            gdi32.DeleteObject(f)
        super().close()


# ---------------------------------------------------------------- stats widget
class Stats:
    """Clicks per second, actions per minute, today's totals (kept in the settings)."""

    def __init__(self, cfg):
        self.data = cfg["stats"]
        self.clicks, self.actions = deque(), deque()
        self.started = time.monotonic()
        self.check_day()

    def check_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.data.get("date") != today:
            self.data.update(date=today, keys=0, clicks=0, rl_w=0, rl_l=0, gd_jumps=0, fn_today=0,
                             fnm_count=0, fnm_time=0, fnm_best=0, drinks=0)

    def add(self, kid):
        now = time.monotonic()
        self.check_day()
        if kid[0] == "m":
            if kid[1] in ("WU", "WD"):
                return
            self.clicks.append(now)
            self.data["clicks"] += 1
        else:
            self.data["keys"] += 1
        self.actions.append(now)
        self.data["cat"] = self.data.get("cat", 0) + 1
        cps = self.cps(now)
        if cps > self.data.get("best_cps", 0):
            self.data["best_cps"] = cps

    def cps(self, now):
        while self.clicks and now - self.clicks[0] > 1.0:
            self.clicks.popleft()
        return len(self.clicks)

    def apm(self, now):
        while self.actions and now - self.actions[0] > 60.0:
            self.actions.popleft()
        window = max(10.0, min(60.0, now - self.started))
        return int(round(len(self.actions) * 60.0 / window))


class StatsWidget(ArgbWidget):
    kind = "stats"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.last = None
        self.last_draw = 0.0
        self.volume_until, self.volume_shown = 0.0, 1.0
        self.build()

    def flash_volume(self, volume):
        self.volume_until, self.volume_shown = time.monotonic() + 1.5, volume
        if self.shown:
            self.redraw()

    def build(self):
        u = self.u
        self.make_canvas(7.8 * u, 3.3 * u)
        self.place()
        self.refresh()

    def draw(self, c, now):
        u, app, st = self.u, self.app, self.app.stats
        cps, apm = st.cps(now), st.apm(now)
        self.last = (cps, apm, st.data["keys"], st.data["clicks"], st.data.get("best_cps", 0))
        panel(c, self.w, self.h, u, app, now)
        colors = c.theme.nums(app, now)
        half = self.w / 2
        muted = c.theme.muted
        for i, (label, value, sub) in enumerate((("CPS", cps, T("stats_best").format(st.data.get("best_cps", 0))),
                                                 ("APM", apm, T("stats_apm_sub")))):
            x = i * half
            c.text(label, x, 0.25 * u, half, 0.5 * u, 0.36 * u, muted)
            c.text(str(value), x, 0.62 * u, half, 1.25 * u, 1.05 * u, colors, outline=argb((0, 0, 0), 0.6),
                   outline_w=u / 10)
            c.text(sub, x, 1.85 * u, half, 0.45 * u, 0.3 * u, muted, bold=False)
        c.line(0.5 * u, 2.45 * u, self.w - 0.5 * u, 2.45 * u, c.theme.line, max(1, u / 25))
        if now < self.volume_until:               # just changed the click volume with Ctrl+Alt+PgUp / PgDn
            c.text(f"{T('click_volume').split(chr(9))[0]}: {int(round(self.volume_shown * 100))} %", 0, 2.5 * u,
                   self.w, 0.65 * u, 0.33 * u, argb(app.glow_color(0.3, now)), bold=True)
            return
        c.text(T("stats_today").format(f"{st.data['keys']:,}".replace(",", " "),
                                       f"{st.data['clicks']:,}".replace(",", " ")),
               0, 2.5 * u, self.w, 0.65 * u, 0.33 * u, c.theme.text, bold=False)

    def tick(self, now):
        if self.shown and now - self.last_draw > 0.25:
            if now < self.volume_until + 0.3:
                self.last_draw = now
                self.redraw()
                return
            st = self.app.stats
            key = (st.cps(now), st.apm(now), st.data["keys"], st.data["clicks"], st.data.get("best_cps", 0))
            if key != self.last or self.app.animated():
                self.last_draw = now
                self.redraw()

    def menu_items(self, add, checked):
        add(0, 710, T("reset_today"))
        add(0, 711, T("reset_best"))

    def menu_command(self, cmd):
        if cmd == 710:
            self.app.stats.data.update(keys=0, clicks=0)
        elif cmd == 711:
            self.app.stats.data["best_cps"] = 0
        else:
            return False
        self.redraw()
        return True


# ---------------------------------------------------------------- the clicking cat (an original drawing)
FURS = {
    "orange": ((0.96, 0.6, 0.28), (1.0, 0.9, 0.78)),
    "gray": ((0.58, 0.6, 0.66), (0.9, 0.9, 0.93)),
    "black": ((0.17, 0.17, 0.2), (0.42, 0.42, 0.47)),
    "white": ((0.97, 0.97, 0.98), (1.0, 0.82, 0.86)),
    "rgb": None,
}


class CatWidget(ArgbWidget):
    """A cat behind a tiny RGB keyboard: left paw taps for keys, right paw for mouse clicks."""
    kind = "cat"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.paw_until = {"left": 0.0, "right": 0.0}
        self.paw_held = {"left": set(), "right": set()}
        self.drawn_state = None
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(6.2 * u, 5.6 * u)
        self.place()
        self.refresh()

    def paw_down(self, side, now):
        return bool(self.paw_held[side]) or now < self.paw_until[side]

    def on_input(self, kind, kid):
        if kid[0] == "m" and kid[1] in ("WU", "WD"):
            return
        side = "right" if kid[0] == "m" else "left"
        now = time.monotonic()
        if kind == "down":
            self.paw_held[side].add(kid)
            self.paw_until[side] = now + 0.09
            self.redraw_if_shown()
        elif kind == "up":
            self.paw_held[side].discard(kid)

    def redraw_if_shown(self):
        if self.shown:
            self.redraw()

    def tick(self, now):
        state = (self.paw_down("left", now), self.paw_down("right", now))
        rgb = self.wcfg.get("fur") == "rgb" and self.app.animated()
        if self.shown and (state != self.drawn_state or (rgb and now - getattr(self, "last_draw", 0) > 1 / RGB_FPS)):
            self.redraw()

    def menu_items(self, add, checked):
        fur = self.wcfg.get("fur", "orange")
        for i, name in enumerate(FURS):
            add(checked(fur == name), 720 + i, f"{T('fur')}: {T('fur_' + name)}")
        add(0x800, 0, None)
        add(0, 729, T("reset_cat"))

    def menu_command(self, cmd):
        if 720 <= cmd < 720 + len(FURS):
            self.wcfg["fur"] = list(FURS)[cmd - 720]
        elif cmd == 729:
            self.app.stats.data["cat"] = 0
        else:
            return False
        self.redraw()
        return True

    def draw(self, c, now):
        self.last_draw = now
        u, app = self.u, self.app
        left, right = self.paw_down("left", now), self.paw_down("right", now)
        self.drawn_state = (left, right)
        fur_name = self.wcfg.get("fur", "orange")
        if FURS.get(fur_name):
            fur, light = FURS[fur_name]
        else:
            fur = mix(app.glow_color(0.5, now), WHITE, 0.25)
            light = mix(fur, WHITE, 0.6)
        ink = argb((0.1, 0.08, 0.1))
        line = max(1.2, u * 0.07)
        cx = self.w / 2

        # counter
        count = f"{app.stats.data.get('cat', 0):,}".replace(",", " ")
        colors = c.theme.nums(app, now)
        c.text(count, 0, 0, self.w, 1.05 * u, 0.8 * u, colors, outline=argb((0, 0, 0), 0.75), outline_w=u / 8)

        # body and head
        hy = 2.55 * u
        c.ellipse(cx - 1.9 * u, 3.0 * u, 3.8 * u, 2.6 * u, fill=argb(fur), stroke=ink, width=line)
        for sgn in (-1, 1):                                      # ears
            ear = [(cx + sgn * 1.32 * u, hy - 0.45 * u), (cx + sgn * 1.12 * u, hy - 1.72 * u),
                   (cx + sgn * 0.3 * u, hy - 1.2 * u)]
            c.polygon(ear, fill=argb(fur), stroke=ink, width=line)
            inner = [(cx + sgn * 1.15 * u, hy - 0.65 * u), (cx + sgn * 1.07 * u, hy - 1.42 * u),
                     (cx + sgn * 0.55 * u, hy - 1.12 * u)]
            c.polygon(inner, fill=argb((1.0, 0.68, 0.74)))
        c.ellipse(cx - 1.5 * u, hy - 1.35 * u, 3.0 * u, 2.6 * u, fill=argb(fur), stroke=ink, width=line)
        c.ellipse(cx - 0.95 * u, hy - 0.05 * u, 1.9 * u, 1.15 * u, fill=argb(light))        # muzzle
        tapping = left or right
        for sgn in (-1, 1):                                      # eyes: happy ^ ^ while tapping
            ex = cx + sgn * 0.6 * u
            if tapping:
                c.arc(ex - 0.26 * u, hy - 0.35 * u, 0.52 * u, 0.5 * u, 200, 140, ink, line * 1.3)
            else:
                c.ellipse(ex - 0.2 * u, hy - 0.42 * u, 0.4 * u, 0.52 * u, fill=ink)
                c.ellipse(ex - 0.08 * u, hy - 0.35 * u, 0.14 * u, 0.16 * u, fill=argb(WHITE))
            c.ellipse(ex - 0.32 * u + sgn * 0.12 * u, hy + 0.12 * u, 0.42 * u, 0.24 * u,
                      fill=argb((1.0, 0.55, 0.6), 0.55))                                       # blush
        c.polygon([(cx - 0.16 * u, hy + 0.18 * u), (cx + 0.16 * u, hy + 0.18 * u), (cx, hy + 0.36 * u)],
                  fill=argb((0.95, 0.45, 0.55)), stroke=ink, width=line * 0.6)                  # nose
        c.arc(cx - 0.3 * u, hy + 0.22 * u, 0.3 * u, 0.32 * u, 0, 180, ink, line * 0.8)          # mouth "w"
        c.arc(cx, hy + 0.22 * u, 0.3 * u, 0.32 * u, 0, 180, ink, line * 0.8)
        for sgn in (-1, 1):                                      # whiskers
            for k, dy in enumerate((-0.05, 0.12, 0.29)):
                x1 = cx + sgn * 0.85 * u
                c.line(x1, hy + dy * u + 0.2 * u, x1 + sgn * 0.75 * u, hy + (dy + (k - 1) * 0.12) * u + 0.2 * u,
                       argb((0.1, 0.08, 0.1), 0.7), line * 0.55)

        # desk with a tiny RGB keyboard and mouse
        desk_y = 4.35 * u
        c.round_rect(0.15 * u, desk_y, self.w - 0.3 * u, self.h - desk_y - 0.1 * u, 0.3 * u,
                     fill=argb((0.13, 0.13, 0.17)), stroke=ink, width=line)
        kx, ky, kw, kh = 0.45 * u, desk_y + 0.22 * u, 3.0 * u, 0.62 * u
        c.round_rect(kx, ky, kw, kh, 0.12 * u, fill=argb((0.06, 0.06, 0.08)), stroke=ink, width=line * 0.6)
        cols = 8
        for row in range(2):
            for col in range(cols):
                kc = app.glow_color(col / cols, now)
                lit = left and (row * cols + col) % 3 == int(now * 20) % 3
                c.round_rect(kx + 0.08 * u + col * (kw - 0.16 * u) / cols, ky + 0.07 * u + row * 0.27 * u,
                             (kw - 0.16 * u) / cols - 0.05 * u, 0.22 * u, 0.04 * u,
                             fill=argb(mix(kc, WHITE, 0.6) if lit else mix(DARK, kc, 0.75)))
        mx_, my_ = self.w - 1.55 * u, desk_y + 0.16 * u
        mc = app.glow_color(0.9, now)
        c.ellipse(mx_, my_, 0.75 * u, 0.85 * u, fill=argb(mix(DARK, mc, 0.9 if right else 0.35)),
                  stroke=ink, width=line * 0.6)
        c.line(mx_ + 0.375 * u, my_ + 0.05 * u, mx_ + 0.375 * u, my_ + 0.35 * u, ink, line * 0.5)

        # arms + paws (down = touching the keyboard / mouse)
        paws = {"left": ((cx - 1.0 * u, 3.55 * u), (kx + 1.6 * u, ky + 0.25 * u), left),
                "right": ((cx + 1.0 * u, 3.55 * u), (mx_ + 0.38 * u, my_ + 0.25 * u), right)}
        for side, (up, down, is_down) in paws.items():
            px, py = down if is_down else up
            sx = cx + (-0.9 if side == "left" else 0.9) * u
            sy = 3.35 * u
            c.line(sx, sy, px, py, ink, 0.78 * u + 2 * line)
            c.line(sx, sy, px, py, argb(fur), 0.78 * u)
            c.ellipse(px - 0.45 * u, py - 0.35 * u, 0.9 * u, 0.7 * u, fill=argb(light), stroke=ink, width=line)
            for k in (-1, 0, 1):                                 # little toes
                c.line(px + k * 0.2 * u, py - 0.05 * u, px + k * 0.2 * u, py + 0.15 * u,
                       argb((0.1, 0.08, 0.1), 0.45), line * 0.5)
            if is_down:                                          # tap lines
                for k in (-1, 1):
                    c.line(px + k * 0.55 * u, py - 0.45 * u, px + k * 0.75 * u, py - 0.7 * u,
                           argb(WHITE, 0.8), line * 0.8)


# ---------------------------------------------------------------- session timer and game widgets
def clock_text(seconds, hours=True):
    seconds = max(0, int(seconds))
    h, m, sec = seconds // 3600, seconds // 60 % 60, seconds % 60
    if hours or h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"



# ---------------------------------------------------------------- themes (look + font of all widgets)
def hex_to_rgb(text, default=(1.0, 1.0, 1.0)):
    try:
        v = int(str(text).strip().lstrip("#")[:6], 16)
        return ((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255
    except (TypeError, ValueError):
        return default


THEMES = {
    # bg / bg_a: card color and opacity (None = no card); border: color, "glow" (follows the effect), "rainbow",
    # "pastel", "neon" or None; nums: big numbers - "effect", "rainbow", "pastel" or a list of colors
    "classic": {"bg": (0.05, 0.05, 0.07), "bg_a": 0.82, "border": "glow", "text": (0.85, 0.85, 0.9),
                "muted": (0.62, 0.62, 0.7), "nums": "effect", "radius": 0.45},
    "pastel": {"bg": (0.99, 0.97, 1.0), "bg_a": 0.93, "border": "pastel", "text": (0.22, 0.2, 0.33),
               "muted": (0.47, 0.44, 0.58), "nums": "pastel", "radius": 0.6},
    "black": {"bg": (0.0, 0.0, 0.0), "bg_a": 0.95, "border": "rainbow", "text": (0.9, 0.9, 0.93),
              "muted": (0.6, 0.6, 0.66), "nums": "rainbow", "radius": 0.35},
    "neon": {"bg": (0.03, 0.02, 0.09), "bg_a": 0.9, "border": "neon", "text": (0.86, 0.95, 1.0),
             "muted": (0.56, 0.62, 0.9), "nums": [(0.2, 1.0, 1.0), (1.0, 0.25, 0.9)], "radius": 0.25},
    "glass": {"bg": (1.0, 1.0, 1.0), "bg_a": 0.14, "border": (1.0, 1.0, 1.0), "text": (1.0, 1.0, 1.0),
              "muted": (0.86, 0.86, 0.92), "nums": [(1.0, 1.0, 1.0), (0.85, 0.9, 1.0)], "radius": 0.55},
    "minimal": {"bg": None, "bg_a": 0.0, "border": None, "text": (1.0, 1.0, 1.0), "muted": (0.88, 0.88, 0.92),
                "nums": "effect", "radius": 0.0},
}
THEME_IDS = list(THEMES) + ["custom"]


class Theme:
    """Colors, card and font one widget is drawn with."""

    def __init__(self, spec, font):
        self.spec, self.font = spec, font
        self.bg, self.bg_a = spec.get("bg"), float(spec.get("bg_a", 0.85))
        self.border = spec.get("border")
        self.text_rgb, self.muted_rgb = spec["text"], spec["muted"]
        self.text, self.muted = argb(spec["text"]), argb(spec["muted"])
        self.radius = float(spec.get("radius", 0.45))
        light_text = sum(spec["text"]) / 3 > 0.55
        self.outline = argb((0, 0, 0), 0.85 if self.bg is None else 0.6) if light_text else argb(WHITE, 0.8)
        self.line = argb(spec["text"], 0.1)

    @property
    def dark(self):
        """True when the card is dark (text is light)."""
        return sum(self.text_rgb) / 3 > 0.55

    def accent(self, app, now):
        b = self.border
        if isinstance(b, tuple):
            return b
        n = self.spec.get("nums")
        if isinstance(n, list):
            return n[0]
        return app.glow_color(0.1, now)

    def nums(self, app, now):
        n = self.spec.get("nums", "effect")
        if n == "rainbow":
            return [argb(colorsys.hsv_to_rgb((i / 4 * 0.6 - now * 0.12) % 1.0, 0.9, 1.0)) for i in range(5)]
        if n == "pastel":
            return [argb(colorsys.hsv_to_rgb((i / 4 * 0.6 - now * 0.05) % 1.0, 0.55, 0.85)) for i in range(5)]
        if isinstance(n, list):
            return [argb(col) for col in n] if len(n) > 1 else argb(n[0])
        if isinstance(n, tuple):
            return argb(n)
        return [argb(app.glow_color(i / 4, now)) for i in range(5)]

    def panel(self, c, w, h, u, app, now):
        if self.bg is None:
            return
        r = self.radius * u
        c.round_rect(0.5, 0.5, w - 1, h - 1, r, fill=argb(self.bg, self.bg_a))
        b, width = self.border, max(1.0, u / 20)
        if b is None:
            return
        if b == "glow":
            c.round_rect(0.5, 0.5, w - 1, h - 1, r, stroke=argb(mix(self.bg, app.glow_color(0.1, now), 0.6)), width=width)
        elif b in ("rainbow", "pastel", "neon"):
            if b == "neon":
                cols = [argb((0.2, 1.0, 1.0)), argb((1.0, 0.25, 0.9))]
                for k in range(3):
                    c.gradient_rect(0.5 + k, 0.5 + k, w - 1 - 2 * k, h - 1 - 2 * k, max(0.5, r - k),
                                    stroke=[argb((0.2, 1.0, 1.0), 0.25 / (k + 1)), argb((1.0, 0.25, 0.9), 0.25 / (k + 1))],
                                    width=width * (3 - k))
            else:
                sat = 0.4 if b == "pastel" else 1.0
                cols = [argb(colorsys.hsv_to_rgb((i / 6 - now * 0.08) % 1.0, sat, 1.0)) for i in range(7)]
            c.gradient_rect(0.5, 0.5, w - 1, h - 1, r, stroke=cols, width=width * (1.6 if b != "glow" else 1))
        else:
            c.round_rect(0.5, 0.5, w - 1, h - 1, r, stroke=argb(b, 0.45 if self.bg_a < 0.3 else 0.9), width=width)


def theme_spec(name, cfg):
    if name == "custom":
        ct = {**DEFAULTS["custom_theme"], **(cfg.get("custom_theme") or {})}
        border = ct.get("border")
        border = border if border in ("glow", "rainbow", "pastel", "neon") else (
            None if border in (None, "", "none") else hex_to_rgb(border, ACCENT))
        nums = ct.get("nums")
        nums = nums if nums in ("effect", "rainbow", "pastel") else [hex_to_rgb(nums, ACCENT)]
        return {"bg": hex_to_rgb(ct.get("bg"), (0.07, 0.07, 0.1)), "bg_a": max(0.0, min(1.0, float(ct.get("bg_a", 0.85)))),
                "border": border, "text": hex_to_rgb(ct.get("text"), WHITE), "muted": hex_to_rgb(ct.get("muted"), (0.6, 0.6, 0.7)),
                "nums": nums, "radius": max(0.0, min(1.2, float(ct.get("radius", 0.45))))}
    return THEMES.get(name, THEMES["classic"])


_default_theme = []


def default_theme():
    if not _default_theme:
        _default_theme.append(Theme(THEMES["classic"], "Segoe UI"))
    return _default_theme[0]


def panel(c, w, h, u, app, now):
    """The card most widgets sit on, in the widget's theme."""
    c.theme.panel(c, w, h, u, app, now)


class SessionWidget(ArgbWidget):
    """How long you've been playing: since Keyboard Sound started, plus the game that's running."""
    kind = "session"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.last_draw = 0.0
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(7.4 * u, 3.0 * u)
        self.place()
        self.refresh()

    def draw(self, c, now):
        self.last_draw = now
        u, app = self.u, self.app
        panel(c, self.w, self.h, u, app, now)
        muted = c.theme.muted
        c.text(T("session_label"), 0.45 * u, 0.2 * u, 3 * u, 0.5 * u, 0.3 * u, muted, align=0)
        c.text(T("session_now").format(datetime.now().strftime("%H:%M")), 0, 0.2 * u, self.w - 0.45 * u, 0.5 * u,
               0.3 * u, muted, bold=False, align=2)
        paused = app.session_paused_at is not None
        colors = c.theme.nums(app, now)
        c.text(clock_text(app.session_seconds(now)), 0, 0.6 * u, self.w, 1.4 * u, 1.05 * u,
               argb((0.6, 0.6, 0.65)) if paused else colors, outline=c.theme.outline, outline_w=u / 10)
        game = app.current_game()
        if game:
            name, since = game
            line = T("session_playing").format(name, clock_text(now - since, hours=False))
        else:
            st = app.stats.data
            line = T("stats_today").format(f"{st['keys']:,}".replace(",", " "), f"{st['clicks']:,}".replace(",", " "))
        c.line(0.5 * u, 2.1 * u, self.w - 0.5 * u, 2.1 * u, c.theme.line, max(1, u / 25))
        c.text(line, 0, 2.15 * u, self.w, 0.7 * u, 0.33 * u, c.theme.text, bold=False)

    def tick(self, now):
        if self.shown and (now - self.last_draw >= 1.0 or (self.app.animated() and now - self.last_draw > 1 / RGB_FPS)):
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 770, T("session_reset"))
        add(checked(self.app.session_paused_at is not None), 771, T("session_pause"))

    def menu_command(self, cmd):
        app, now = self.app, time.monotonic()
        if cmd == 770:
            app.session_start = now
            app.session_paused_at = now if app.session_paused_at is not None else None
        elif cmd == 771:
            if app.session_paused_at is None:
                app.session_paused_at = now
            else:
                app.session_start += now - app.session_paused_at
                app.session_paused_at = None
        else:
            return False
        self.redraw()
        return True


RL_BLUE, RL_ORANGE = (0.16, 0.5, 1.0), (1.0, 0.52, 0.08)


class RocketWidget(ArgbWidget):
    """Live Rocket League scoreboard from the game's official Stats API: score, time, your boost, wins today."""
    kind = "rocket"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.connected = False
        self.in_match = False
        self.game, self.me = {}, None
        self.my_team, self.my_name = None, None
        self.flash = None                       # (until, text, color)
        self.kmh = None                         # None = speeds in Unreal units per second (the docs), True = km/h
        self.last_end = 0.0
        self.dirty, self.last_draw = True, 0.0
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(9.0 * u, 4.15 * u)
        self.place()
        self.refresh()

    def has_content(self):
        return self.connected or "rocketleague.exe" in self.app.running

    # ---------- data from the game
    def find_me(self, players, target):
        if target:
            for pl in players:
                if pl.get("Name") == target.get("Name") and pl.get("Shortcut") == target.get("Shortcut"):
                    return pl
        if self.my_name:
            for pl in players:
                if pl.get("Name") == self.my_name:
                    return pl
        return None

    def on_rl(self, event, data):
        now = time.monotonic()
        data = data if isinstance(data, dict) else {}
        if event in ("Connected", "Disconnected"):
            self.connected = event == "Connected"
            self.in_match, self.game, self.me = False, {}, None
            self.refresh()
            return
        if event == "UpdateState":
            game, players = data.get("Game") or {}, data.get("Players") or []
            self.game = game
            self.in_match = bool(game.get("Teams") or players)
            target = game.get("Target") if game.get("bHasTarget") and isinstance(game.get("Target"), dict) else None
            me = self.find_me(players, target)
            if me:
                self.me = me
                if not game.get("bReplay") and me.get("TeamNum") in (0, 1):
                    self.my_team, self.my_name = me.get("TeamNum"), me.get("Name")
            for pl in players:                    # find out the speed unit once from a supersonic car
                if self.kmh is None and pl.get("bSupersonic") and isinstance(pl.get("Speed"), (int, float)):
                    self.kmh = pl["Speed"] < 400
        elif event == "GoalScored":
            scorer = data.get("Scorer") or {}
            team = scorer.get("TeamNum")
            self.flash = (now + 2.5, f"{T('rl_goal')} {scorer.get('Name', '')}".strip(),
                          RL_BLUE if team == 0 else RL_ORANGE)
        elif event == "MatchEnded":
            winner = data.get("WinnerTeamNum")
            if self.my_team in (0, 1) and winner in (0, 1) and now - self.last_end > 15:
                self.last_end = now
                won = winner == self.my_team
                st = self.app.stats
                st.check_day()
                st.data["rl_w" if won else "rl_l"] = st.data.get("rl_w" if won else "rl_l", 0) + 1
                self.flash = (now + 6, T("rl_win" if won else "rl_loss"), (0.2, 0.85, 0.4) if won else (0.9, 0.25, 0.3))
                save_settings(self.app.cfg)
        elif event in ("MatchDestroyed", "MatchCreated"):
            self.in_match, self.game, self.me = False, {}, None
            if event == "MatchCreated":
                self.in_match = True
        self.dirty = True

    def kmh_of(self, speed):
        if not isinstance(speed, (int, float)):
            return None
        return int(round(speed if self.kmh else speed * 0.036))

    # ---------- drawing
    def draw(self, c, now):
        self.last_draw, self.dirty = now, False
        u, app = self.u, self.app
        panel(c, self.w, self.h, u, app, now)
        muted, light = c.theme.muted, c.theme.text
        colors = c.theme.nums(app, now)
        st = app.stats.data
        today = T("rl_today").format(st.get("rl_w", 0), st.get("rl_l", 0))
        if not self.in_match:
            c.text("ROCKET LEAGUE", 0, 0.35 * u, self.w, 1.2 * u, 0.75 * u, colors, outline=argb((0, 0, 0), 0.6),
                   outline_w=u / 10)
            if self.connected:
                status = T("rl_waiting")
            elif "rocketleague.exe" in app.running:
                status = T("rl_enable_hint") if not app.rl_live_on() else T("rl_waiting")
            else:
                status = T("rl_closed")
            c.text(status, 0, 1.65 * u, self.w, 0.8 * u, 0.4 * u, light, bold=False)
            c.line(0.5 * u, 2.75 * u, self.w - 0.5 * u, 2.75 * u, c.theme.line, max(1, u / 25))
            c.text(today, 0, 2.85 * u, self.w, 0.9 * u, 0.4 * u, light, bold=False)
            self.draw_flash(c, now)
            return

        game = self.game
        teams = {t.get("TeamNum"): t for t in game.get("Teams") or [] if isinstance(t, dict)}
        # score boxes + clock
        box_w, box_h, top = 2.3 * u, 1.35 * u, 0.3 * u
        for team, x, col in ((0, 0.3 * u, RL_BLUE), (1, self.w - 0.3 * u - box_w, RL_ORANGE)):
            mine = team == self.my_team
            c.round_rect(x, top, box_w, box_h, 0.3 * u, fill=argb(mix(col, (0, 0, 0), 0.25)),
                         stroke=argb(WHITE, 0.9) if mine else None, width=max(1.5, u / 12))
            c.text(str((teams.get(team) or {}).get("Score", 0)), x, top, box_w, box_h, 0.95 * u, argb(WHITE),
                   outline=argb((0, 0, 0), 0.4), outline_w=u / 14)
        secs = game.get("TimeSeconds", 0) or 0
        ot = game.get("bOvertime")
        clock = ("+" if ot else "") + clock_text(secs, hours=False)
        c.text(clock, 0, top, self.w, box_h, 0.8 * u, colors if not ot else argb((1.0, 0.85, 0.3)),
               outline=c.theme.outline, outline_w=u / 12)
        if ot:
            c.text(T("rl_ot"), 0, top + box_h - 0.2 * u, self.w, 0.4 * u, 0.26 * u, argb((1.0, 0.85, 0.3)))

        # boost bar
        me = self.me or {}
        boost = me.get("Boost")
        by, bh = 1.95 * u, 0.5 * u
        c.text("BOOST", 0.3 * u, by, 1.5 * u, bh, 0.3 * u, muted, align=0)
        bx, bw = 1.6 * u, self.w - 1.6 * u - 1.25 * u
        c.round_rect(bx, by + 0.08 * u, bw, bh - 0.16 * u, 0.17 * u, fill=argb((1, 1, 1), 0.08))
        if isinstance(boost, (int, float)):
            frac = max(0.0, min(1.0, boost / 100))
            if frac > 0.01:
                fill_col = (1.0, 0.85, 0.2) if me.get("bBoosting") else (1.0, 0.6, 0.12)
                c.round_rect(bx, by + 0.08 * u, max(bh - 0.16 * u, bw * frac), bh - 0.16 * u, 0.17 * u,
                             fill=argb(fill_col))
            c.text(str(int(boost)), self.w - 1.2 * u, by, 0.9 * u, bh, 0.36 * u, argb(WHITE), align=2)
        else:
            c.text("-", self.w - 1.2 * u, by, 0.9 * u, bh, 0.36 * u, muted, align=2)

        # your numbers, ball speed, wins today
        c.text(T("rl_stats").format(me.get("Goals", 0), me.get("Shots", 0), me.get("Assists", 0), me.get("Saves", 0)),
               0, 2.6 * u, self.w, 0.55 * u, 0.3 * u, light, bold=False)
        c.line(0.5 * u, 3.2 * u, self.w - 0.5 * u, 3.2 * u, c.theme.line, max(1, u / 25))
        ball = self.kmh_of((game.get("Ball") or {}).get("Speed"))
        if ball is not None:
            c.text(T("rl_ball").format(ball), 0.4 * u, 3.25 * u, 4 * u, 0.75 * u, 0.3 * u, muted, bold=False, align=0)
        c.text(today, 0, 3.25 * u, self.w - 0.4 * u, 0.75 * u, 0.3 * u, light, bold=False, align=2)
        self.draw_flash(c, now)

    def draw_flash(self, c, now):
        if self.flash and now < self.flash[0]:
            u = self.u
            _, text, col = self.flash
            c.round_rect(0.3 * u, 1.85 * u, self.w - 0.6 * u, 1.2 * u, 0.35 * u, fill=argb(mix(col, (0, 0, 0), 0.15), 0.95))
            c.text(text, 0.3 * u, 1.85 * u, self.w - 0.6 * u, 1.2 * u, 0.6 * u, argb(WHITE),
                   outline=argb((0, 0, 0), 0.4), outline_w=u / 14)

    def tick(self, now):
        if not self.shown:
            return
        flashing = self.flash is not None and now < self.flash[0] + 0.1
        if (self.dirty and now - self.last_draw > 1 / 15) or now - self.last_draw >= 1.0 or \
                (flashing and now - self.last_draw > 0.2) or (self.app.animated() and now - self.last_draw > 1 / RGB_FPS):
            if self.flash and now > self.flash[0]:
                self.flash = None
            self.redraw()

    def menu_items(self, add, checked):
        add(checked(self.app.rl_live_on()), 760, T("rl_live"))
        add(checked(self.app.rl_borderless_on()), 761, T("rl_borderless"))
        add(0, 762, T("rl_reset"))

    def menu_command(self, cmd):
        if cmd in (760, 761):
            self.app.games_command(cmd - 200)
        elif cmd == 762:
            self.app.stats.data.update(rl_w=0, rl_l=0)
        else:
            return False
        self.redraw()
        return True



SB_MODES = ["enhanced", "pastel", "black"]
SB_FONTS = ["Bahnschrift", "Segoe UI", "Arial Black", "Impact", "Verdana", "Trebuchet MS", "Consolas",
            "Georgia", "Courier New",
            "Ink Free", "Segoe Script", "Segoe Print", "Comic Sans MS", "Gabriola"]     # the last ones: handwriting
SB_HANDWRITING = {"Ink Free", "Segoe Script", "Segoe Print", "Comic Sans MS", "Gabriola"}


def font_installed(name, cache={}):
    if name not in cache:
        ensure_gdiplus()
        fam = ctypes.c_void_p()
        cache[name] = not gdiplus.GdipCreateFontFamilyFromName(name, None, ctypes.byref(fam))
        if cache[name]:
            gdiplus.GdipDeleteFontFamily(fam)
    return cache[name]


def hex_rgb(text, default):
    try:
        v = int(str(text).strip().lstrip("#")[:6], 16)
        return ((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255
    except (TypeError, ValueError):
        return default


def vivid(rgb, min_v=0.45):
    """Team colors can be very dark (club colors): keep them bright enough to read."""
    h, sat, v = colorsys.rgb_to_hsv(*rgb)
    return colorsys.hsv_to_rgb(h, sat, max(v, min_v))


class ScoreboardWidget(ArgbWidget):
    """Rocket League score counter: your team's score, match time, the other team's score.
    Covers the game's own scoreboard: one solid board, real team colors, animated."""
    kind = "scoreboard"
    HOME_TEXT = "sb_top"
    PULSE = 3.0
    LAYOUT = 2              # bump when the default size / place changes (old settings get the new default)
    CONFETTI = 2.6          # seconds confetti falls after a goal

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.in_match = False
        self.scores, self.secs, self.ot = [0, 0], 300, False
        self.colors = {0: RL_BLUE, 1: RL_ORANGE}
        self.names = {0: "", 1: ""}
        self.my_team, self.my_name = None, None
        self.goal = None                          # (team, time)
        self.pops = {}                            # team -> time its number changed
        self.waves = []                           # (team, time): shock rings after a goal
        self.bits = []                            # confetti: [x, y, vx, vy, angle, spin, size, rgb, born]
        self.last_draw = 0.0
        if self.wcfg.get("layout") != self.LAYOUT:      # older, smaller scoreboard -> new size, back to its place
            self.wcfg.update(layout=self.LAYOUT, size=scoreboard_default_size(), x=None, y=None)
        mate = next((v for k, v in app.cfg["widgets"].items()       # a new one takes the look of its partner
                     if v.get("type") in RL_STYLE_KINDS and k != wid and "mode" in v), None)
        if mate and "mode" not in self.wcfg:
            self.wcfg.update({k: mate[k] for k in RL_STYLE_KEYS if k in mate})
        self.build()

    own_opacity = True                            # its own opacity (100 % = the game's scoreboard never shows)

    def opacity(self):
        return max(0.2, min(1.0, float(self.wcfg.get("opacity", 1.0))))

    def sync_style(self):
        """Score and boost always look the same: copy look / font / opacity to the partner and redraw both."""
        for w in self.app.widgets.values():
            if w.kind in RL_STYLE_KINDS and w is not self:
                w.wcfg.update({k: self.wcfg[k] for k in RL_STYLE_KEYS if k in self.wcfg})
                w.redraw()

    def build(self):
        u = self.u
        self.H = 2.5 * u                          # time tab height (the game's board is ~2.1 u plus its glow)
        self.Hb = 2.3 * u                         # score tabs
        self.slant = 0.38 * u
        self.clock_w, self.box_w, self.gap = 5.7 * u, 2.4 * u, 0.12 * u
        self.side = 0.9 * u                       # room for the glow and goal rings at the sides
        w = self.clock_w + 2 * (self.gap + self.box_w) + 2 * self.side + self.slant
        self.make_canvas(w, self.H + 2.2 * u)     # extra room below for the confetti
        self.shapes, self.backing = self.make_shapes()
        self.place()
        self.refresh()

    def make_shapes(self):
        """[left score, time, right score]: (rounded outline points, text box), plus one board behind them all."""
        u, H, Hb, sl = self.u, self.H, self.Hb, self.slant
        cx = self.w / 2
        r_top, r_bot = 0.04 * u, 0.42 * u
        cl, cr = cx - self.clock_w / 2, cx + self.clock_w / 2
        clock = [(cl, 0), (cr, 0), (cr - sl, H), (cl + sl, H)]
        k = sl * Hb / H                            # same slope on the score tabs
        bt_r = cl - self.gap
        left = [(bt_r - self.box_w, 0), (bt_r, 0), (bt_r + k, Hb), (bt_r - self.box_w + k, Hb)]
        ot_l = cr + self.gap
        right = [(ot_l, 0), (ot_l + self.box_w, 0), (ot_l + self.box_w - k, Hb), (ot_l - k, Hb)]
        shapes = []
        for pts, h in ((left, Hb), (clock, H), (right, Hb)):
            flat = rounded_points(pts, [r_top, r_top, r_bot, r_bot], 8)
            xs = [x for x, _ in pts]
            shapes.append((flat, (min(xs) + sl / 2, 0, max(xs) - min(xs) - sl, h)))
        # the board behind: fills the gaps between the tabs so nothing of the game shows through them
        back = [left[0], right[1], (right[2][0], Hb - 0.15 * u), (left[3][0], Hb - 0.15 * u)]
        return shapes, rounded_points(back, [r_top, r_top, r_bot, r_bot], 8)

    def has_content(self):
        return self.in_match

    @property
    def mode(self):
        m = self.wcfg.get("mode", "enhanced")
        return m if m in SB_MODES else "enhanced"

    def order(self):
        """Teams left -> right: your team first (blue first while it's not known yet)."""
        mine = self.my_team if self.my_team in (0, 1) else 0
        return mine, 1 - mine

    # ---------- data from the game
    def on_rl(self, event, data):
        data = data if isinstance(data, dict) else {}
        now = time.monotonic()
        was = self.in_match
        if event in ("Connected", "Disconnected", "MatchDestroyed"):
            self.in_match, self.scores, self.secs, self.ot, self.goal = False, [0, 0], 300, False, None
            self.colors = {0: RL_BLUE, 1: RL_ORANGE}
            self.my_team, self.pops, self.waves, self.bits = None, {}, [], []
        elif event == "UpdateState":
            game = data.get("Game") or {}
            players = [pl for pl in data.get("Players") or [] if isinstance(pl, dict)]
            teams = [t for t in game.get("Teams") or [] if isinstance(t, dict)]
            self.in_match = bool(teams or players)
            for t in teams:
                team = t.get("TeamNum")
                if team in (0, 1):
                    score = int(t.get("Score") or 0)
                    if score != self.scores[team] and was:
                        self.pops[team] = now                      # the number pops when it changes
                    self.scores[team] = score
                    self.colors[team] = vivid(hex_rgb(t.get("ColorPrimary"), RL_BLUE if team == 0 else RL_ORANGE))
                    self.names[team] = str(t.get("Name") or "").strip()
            if isinstance(game.get("TimeSeconds"), (int, float)):
                self.secs = game["TimeSeconds"]
            self.ot = bool(game.get("bOvertime"))
            target = game.get("Target") if game.get("bHasTarget") and isinstance(game.get("Target"), dict) else None
            if target and not game.get("bReplay") and target.get("TeamNum") in (0, 1):
                me = next((pl for pl in players if pl.get("Name") == target.get("Name")
                           and pl.get("Shortcut") == target.get("Shortcut")), None)
                if me is not None and "Boost" in me:          # boost is only sent for your own team
                    self.my_team, self.my_name = target["TeamNum"], me.get("Name")
        elif event == "ClockUpdatedSeconds":
            if isinstance(data.get("TimeSeconds"), (int, float)):
                self.secs = data["TimeSeconds"]
            self.ot = bool(data.get("bOvertime", self.ot))
        elif event == "GoalScored":
            team = (data.get("Scorer") or {}).get("TeamNum")
            if team in (0, 1):
                self.goal = (team, now)
                self.waves = self.waves[-3:] + [(team, now), (team, now + 0.18)]
                self.burst(team, now)
        elif event in ("MatchCreated", "MatchInitialized", "RoundStarted"):
            self.in_match = True
        if self.in_match != was:
            self.refresh()

    def burst(self, team, now):
        """Confetti in the scoring team's color (and white) flies out of its tab."""
        slot = 0 if self.order()[0] == team else 2
        _, (x, y, w, h) = self.shapes[slot]
        u, col = self.u, self.colors[team]
        palette = [col, mix(col, WHITE, 0.5), WHITE, mix(col, (1.0, 0.85, 0.3), 0.5)]
        for _ in range(50):                       # from the bottom edge, outwards and down: never over the numbers
            px = x + random.random() * w
            out = (px - (x + w / 2)) / (w / 2)
            self.bits.append([px, y + h - 0.1 * u, (out * 3 + (random.random() - 0.5) * 3) * u,
                              (-0.8 + random.random() * 2.6) * u, random.random() * 6.28, (random.random() - 0.5) * 14,
                              (0.1 + random.random() * 0.12) * u, random.choice(palette), now])

    # ---------- drawing
    def font(self):
        name = self.wcfg.get("font") or "Bahnschrift"
        return name if font_installed(name) else "Segoe UI"

    def metrics(self, c, font, bold, cache={}):
        """(size factor, digit top, digit height) so every font's numbers are equally tall and truly centered
        (script fonts like Gabriola draw small digits in a tall line)."""
        key = (font, bold)
        if key not in cache:                      # "8" is full height in every font (also old-style digits)
            _, _, _, ref_h = c.measure("8", 100, bold, "Segoe UI")
            _, top, _, h = c.measure("8", 100, bold, font)
            factor = max(0.6, min(2.5, ref_h / h)) if h > 1 else 1.0
            cache[key] = (factor, top, h)         # top / height of the "8" at size 100
        return cache[key]

    def number(self, c, label, x, y, w, h, size, fill, bold, font, outline, outline_w):
        """Draws the score / time centered on the digits themselves, the same height in every font."""
        factor, top, height = self.metrics(c, font, bold)
        size *= factor
        cy = y + h * 0.45
        y0 = cy - (top + height / 2) * size / 100
        c.text(label, x - w, y0, 3 * w, h * 2.5, size, fill, bold=bold, valign=0, outline=outline,
               outline_w=outline_w, font=font)

    @staticmethod
    def rainbow(now, n=7, s=1.0, v=1.0, offset=0.0, speed=0.15, span=1.0, a=1.0):
        return [argb(colorsys.hsv_to_rgb((offset + i / (n - 1) * span - now * speed) % 1.0, s, v), a)
                for i in range(n)]

    @staticmethod
    def shifted(flat, dx=0.0, dy=0.0):
        return [v + (dx if i % 2 == 0 else dy) for i, v in enumerate(flat)]

    @staticmethod
    def grown(flat, box, amount):
        """The outline pushed outwards from its middle by `amount` pixels."""
        x, y, w, h = box
        cx, cy = x + w / 2, y + h / 2
        out = []
        for i in range(0, len(flat), 2):
            dx, dy = flat[i] - cx, flat[i + 1] - cy
            d = math.hypot(dx, dy) or 1
            out += [flat[i] + dx / d * amount, flat[i + 1] + dy / d * amount]
        return out

    def sheen(self, c, flat, box, now, delay):
        """A light stripe sweeps over the tab every few seconds."""
        t = (now + delay) % 4.5
        if t > 0.9:
            return
        u = self.u
        x, y, w, h = box
        xc = x - 1.5 * u + (w + 3 * u) * (t / 0.9)
        c.clip(flat)
        band = [xc - 0.55 * u, y, xc + 0.15 * u, y, xc - 0.35 * u, y + h + u, xc - 1.05 * u, y + h + u]
        c.shape(band, fill=[argb(WHITE, 0.0), argb(WHITE, 0.38), argb(WHITE, 0.0)], vertical=False)
        c.unclip()

    def pop_scale(self, team, now):
        """1.0 normally; the number jumps up and settles with a little bounce when the score changes."""
        t0 = self.pops.get(team)
        if t0 is None or now - t0 > 0.9:
            return 1.0, 0.0
        t = (now - t0) / 0.9
        return 1.0 + 0.55 * math.exp(-5 * t) * math.cos(t * 9), max(0.0, 1.0 - t * 2.2)

    def draw(self, c, now):
        dt = min(0.1, now - self.last_draw) if self.last_draw else 0.0
        self.last_draw = now
        u, mode, font = self.u, self.mode, self.font()
        bold = self.wcfg.get("bold", True)
        text_size = 1.12 * u                       # score and time: same text size, like in the game
        pulse = 0.0
        if self.goal and now - self.goal[1] < self.PULSE:
            age = now - self.goal[1]
            pulse = (0.5 + 0.5 * math.cos(age * 2 * math.pi * 1.6)) * (1.0 - age / self.PULSE)
        clock = ("+" if self.ot else "") + clock_text(self.secs, hours=False)
        hurry = not self.ot and 0 < self.secs <= 10       # last 10 seconds: the time blinks
        late = not self.ot and 0 < self.secs <= 30        # last 30 seconds: the time tab's edge pulses
        blink = 0.5 + 0.5 * math.cos(now * 2 * math.pi) if hurry else 0.0
        gold = (1.0, 0.84, 0.3)
        left, right = self.order()
        slots = [left, None, right]

        # soft shadow, then one solid board behind the tabs (the game never shows through the gaps)
        for k in range(3):
            c.shape(self.shifted(self.backing, 0, (k + 1) * 0.07 * u), fill=argb((0, 0, 0), 0.22 - k * 0.06))
            c.shape(self.shifted(self.shapes[1][0], 0, (k + 1) * 0.07 * u), fill=argb((0, 0, 0), 0.22 - k * 0.06))
        back = {"enhanced": [argb((0.1, 0.1, 0.15)), argb((0.03, 0.03, 0.05))],
                "pastel": [argb((1.0, 0.98, 1.0)), argb((0.9, 0.88, 0.97))],
                "black": [argb((0.04, 0.04, 0.05)), argb((0.0, 0.0, 0.0))]}[mode]
        c.shape(self.backing, fill=back)

        # shock rings after a goal
        live = []
        for team, t0 in self.waves:
            age = now - t0
            if age < 0:
                live.append((team, t0))
                continue
            if age < 0.7:
                live.append((team, t0))
                slot = 0 if team == left else 2
                flat, box = self.shapes[slot]
                ring = self.grown(flat, box, age * 1.1 * u)
                col = self.colors[team] if mode == "enhanced" else colorsys.hsv_to_rgb((now * 0.3) % 1, 0.6, 1)
                c.shape(ring, stroke=argb(mix(col, WHITE, 0.3), 0.8 * (1 - age / 0.7)), width=max(1.5, u / 9))
        self.waves = live

        for i, (flat, box) in enumerate(self.shapes):
            team = slots[i]
            x, y, bw, bh = box
            team_col = None if team is None else self.colors[team]
            glowing = team is not None and self.goal is not None and self.goal[0] == team and pulse > 0.01
            scale, flash = (1.0, 0.0) if team is None else self.pop_scale(team, now)
            label = clock if team is None else str(self.scores[team])
            size = text_size * scale

            if mode == "enhanced":
                if glowing:
                    for k in range(4):
                        c.shape(flat, stroke=argb(mix(team_col, WHITE, 0.3), 0.35 * pulse / (k + 1)),
                                width=(k + 1) * 0.16 * u)
                if team is None:
                    lc, rc = self.colors[left], self.colors[right]
                    c.shape(flat, fill=[argb((0.2, 0.21, 0.29)), argb((0.09, 0.09, 0.14)), argb((0.03, 0.03, 0.06))])
                    if late:
                        c.shape(flat, stroke=argb((1.0, 0.3, 0.3), 0.35 + 0.45 * (0.5 + 0.5 * math.cos(now * 2 * math.pi))),
                                width=max(2.0, u / 9))
                    elif self.ot:
                        c.shape(flat, stroke=[argb(gold), argb(WHITE), argb(gold)], width=max(2.0, u / 10))
                    else:
                        c.shape(flat, stroke=[argb(lc, 0.95), argb(WHITE, 0.25), argb(rc, 0.95)], width=max(1.5, u / 14))
                    bar_y, bar_h = bh - 0.36 * u, 0.09 * u     # team bar under the time with a running light
                    half = bw / 2 - 0.45 * u
                    c.round_rect(x + 0.45 * u, bar_y, half, bar_h, bar_h / 2, fill=argb(lc))
                    c.round_rect(x + bw / 2, bar_y, half, bar_h, bar_h / 2, fill=argb(rc))
                    dot = x + 0.45 * u + (2 * half) * (0.5 + 0.5 * math.sin(now * 1.3))
                    c.ellipse(dot - 0.35 * u, bar_y - 0.1 * u, 0.7 * u, bar_h + 0.2 * u, fill=argb(WHITE, 0.45))
                    fill = argb(gold) if self.ot else argb(mix(WHITE, (1.0, 0.25, 0.25), blink))
                else:
                    breathe = 0.06 * math.sin(now * 1.7 + i)            # the color slowly breathes
                    lift = 0.55 * pulse if glowing else 0.0
                    lift = max(lift, 0.6 * flash)
                    top = mix(team_col, WHITE, 0.4 + breathe + lift)
                    mid, bot = mix(team_col, WHITE, lift), mix(team_col, (0, 0, 0), 0.35)
                    c.shape(flat, fill=[argb(top), argb(mid), argb(bot)])
                    c.shape(flat, stroke=[argb(WHITE, 0.7), argb(WHITE, 0.15)], width=max(1.5, u / 14),
                            stroke_vertical=True)
                    self.sheen(c, flat, box, now, i * 0.25)
                    fill = argb(WHITE)
                self.number(c, label, x, y, bw, bh, size, fill, bold, font, argb((0, 0, 0), 0.6), max(2.0, u / 8))

            elif mode == "pastel":
                if glowing:
                    for k in range(4):
                        c.shape(flat, stroke=self.rainbow(now, s=0.45, offset=i * 0.2, a=0.5 / (k + 1)),
                                width=(k + 1) * 0.15 * u * pulse)
                if team is None:                      # the time: pastel rainbow
                    base = now * 0.05 + i * 0.18
                    cols = [argb(colorsys.hsv_to_rgb((base + j * 0.09) % 1.0, 0.26, 1.0)) for j in range(4)]
                else:                                 # a score: the team's own color, pastel, gently shimmering
                    h0, s0, _ = colorsys.rgb_to_hsv(*team_col)
                    sat = max(0.38, s0 * 0.55) if s0 > 0.15 else 0.0        # white / gray team stays gray
                    cols = [argb(mix(colorsys.hsv_to_rgb((h0 + 0.035 * math.sin(now * 1.4 + j)) % 1.0, sat,
                                                         1.0 - (0.1 if not sat else 0) + 0.03 * math.sin(now * 1.4 + j)),
                                     WHITE, 0.5 * flash)) for j in range(4)]
                c.shape(flat, fill=cols, vertical=False)
                c.shape(flat, fill=[argb(WHITE, 0.45), argb(WHITE, 0.0)])           # soft top light
                edge = self.rainbow(now, s=0.4, offset=i * 0.2, speed=0.08)
                if late:
                    edge = argb((1.0, 0.55, 0.6), 0.5 + 0.5 * (0.5 + 0.5 * math.cos(now * 2 * math.pi)))
                c.shape(flat, stroke=edge, width=max(2.0, u / 10))
                if team is not None:                  # a strong bar in the team's color = which team
                    c.round_rect(x + 0.4 * u, bh - 0.36 * u, bw - 0.8 * u, 0.17 * u, 0.085 * u, fill=argb(team_col))
                    self.sheen(c, flat, box, now, i * 0.25)
                if team is not None:                  # the number in a deep shade of the team's color
                    hh, ss, _ = colorsys.rgb_to_hsv(*team_col)
                    ink = colorsys.hsv_to_rgb(hh, min(1.0, ss * 0.9 + 0.1), 0.42)
                else:
                    ink = (0.62, 0.42, 0.02) if self.ot else mix((0.2, 0.17, 0.33), (0.85, 0.1, 0.2), blink)
                self.number(c, label, x, y, bw, bh, size, argb(ink), bold, font, argb(WHITE, 0.85), max(2.0, u / 9))

            else:                                            # black RGB
                ring = self.rainbow(now, offset=i * 0.22)
                boost = 1 + 0.6 * pulse if glowing else 1.0
                for k in range(4):                           # soft neon glow around the tab
                    c.shape(flat, stroke=self.rainbow(now, offset=i * 0.22, a=min(1.0, 0.16 + 0.4 * pulse * glowing) / (k + 1)),
                            width=(k + 1) * 0.12 * u * boost)
                if team is None:
                    c.shape(flat, fill=[argb((0.06, 0.06, 0.08)), argb((0.01, 0.01, 0.015))])
                else:                                 # a dark tint of the team's color from below
                    c.shape(flat, fill=[argb(mix((0.03, 0.03, 0.04), WHITE, 0.35 * flash)),
                                        argb(mix((0.02, 0.02, 0.03), team_col, 0.32 + 0.3 * flash))])
                c.shape(flat, stroke=argb((1.0, 0.3, 0.3)) if (late and team is None and int(now * 2) % 2) else ring,
                        width=max(2.0, u / 9))
                if team is not None:                  # a glowing bar in the team's color
                    bar = (x + 0.4 * u, bh - 0.38 * u, bw - 0.8 * u, 0.16 * u)
                    c.round_rect(bar[0] - 0.08 * u, bar[1] - 0.08 * u, bar[2] + 0.16 * u, bar[3] + 0.16 * u,
                                 0.16 * u, fill=argb(team_col, 0.35))
                    c.round_rect(*bar, 0.08 * u, fill=argb(mix(team_col, WHITE, 0.15)))
                self.sheen(c, flat, box, now, i * 0.25)
                if team is None and self.ot:
                    fill = argb(gold)
                elif hurry and team is None:
                    fill = argb(mix((1.0, 0.2, 0.25), WHITE, blink))
                elif team is None:
                    fill = self.rainbow(now, n=5, offset=i * 0.22 + 0.1, span=0.5)
                else:                                 # the number in the team's color (light to full)
                    fill = [argb(mix(team_col, WHITE, 0.55)), argb(mix(team_col, WHITE, 0.15)),
                            argb(mix(team_col, WHITE, 0.55))]
                self.number(c, label, x, y, bw, bh, size, fill, bold, font, argb((0, 0, 0), 0.9), max(2.0, u / 8))

            if team is None and self.ot:
                c.text(T("rl_ot"), x, y + 0.08 * u, bw, 0.38 * u, 0.26 * u,
                       argb(gold) if mode != "pastel" else argb((0.62, 0.42, 0.02)), font=font)

        if self.wcfg.get("names", True):
            self.draw_names(c, now, mode, font, left, right)

        # confetti
        if self.bits:
            g = 5.5 * u
            alive = []
            for b in self.bits:
                age = now - b[8]
                if age > self.CONFETTI or b[1] > self.h + u:
                    continue
                b[0] += b[2] * dt
                b[1] += b[3] * dt
                b[3] += g * dt
                b[2] *= 0.985
                b[4] += b[5] * dt
                alive.append(b)
                sz, ca, sa = b[6], math.cos(b[4]), math.sin(b[4])
                pts = []
                for px, py in ((-sz, -sz * 0.5), (sz, -sz * 0.5), (sz, sz * 0.5), (-sz, sz * 0.5)):
                    pts += [b[0] + px * ca - py * sa, b[1] + px * sa + py * ca]
                c.shape(pts, fill=argb(b[7], max(0.0, 1.0 - max(0.0, age - self.CONFETTI + 0.8) / 0.8)))
            self.bits = alive

    def draw_names(self, c, now, mode, font, left, right):
        """Team names in small pills under the score tabs."""
        u = self.u
        for slot, team in ((0, left), (2, right)):
            name = self.names.get(team, "")
            if not name:
                continue
            if len(name) > 14:
                name = name[:13] + "…"
            _, (x, y, w, h) = self.shapes[slot]
            col = self.colors[team]
            _, _, tw, _ = c.measure(name, 0.34 * u, True, font)
            pw = min(w + 1.2 * u, tw + 0.6 * u)
            px, py, ph = x + w / 2 - pw / 2 + (0.18 * u if slot == 0 else -0.18 * u), self.Hb + 0.12 * u, 0.56 * u
            if mode == "enhanced":
                c.round_rect(px, py, pw, ph, ph / 2, fill=argb((0.05, 0.05, 0.08), 0.92), stroke=argb(col), width=max(1.2, u / 18))
                ink = argb(mix(col, WHITE, 0.55))
            elif mode == "pastel":
                c.round_rect(px, py, pw, ph, ph / 2, fill=argb(mix(col, WHITE, 0.7)), stroke=argb(col), width=max(1.2, u / 18))
                hh, ss, _ = colorsys.rgb_to_hsv(*col)
                ink = argb(colorsys.hsv_to_rgb(hh, min(1.0, ss * 0.9 + 0.1), 0.38))
            else:
                c.round_rect(px, py, pw, ph, ph / 2, fill=argb((0.01, 0.01, 0.02), 0.95))
                c.gradient_rect(px, py, pw, ph, ph / 2, stroke=self.rainbow(now, offset=slot * 0.2), width=max(1.2, u / 16))
                ink = argb(mix(col, WHITE, 0.4))
            c.text(name, px, py, pw, ph, 0.34 * u, ink, bold=True, font=font)

    def busy(self, now):
        return bool(self.bits or self.waves or any(now - t < 0.9 for t in self.pops.values())
                    or (self.goal and now - self.goal[1] < self.PULSE))

    def tick(self, now):
        if self.shown and now - self.last_draw > (1 / 30 if self.busy(now) else 1 / 20):
            self.redraw()                         # always animated; 30 fps only while a goal / pop plays

    # ---------- right-click options
    def menu_items(self, add, checked):
        for i, m in enumerate(SB_MODES):
            add(checked(self.mode == m), 790 + i, f"{T('sb_mode')}: {T('sb_' + m)}")
        add(0x800, 0, None)
        current = self.wcfg.get("font") or "Bahnschrift"
        fonts = [f for f in SB_FONTS if font_installed(f)]
        for i, f in enumerate(fonts):
            hand = f" ({T('sb_handwriting')})" if f in SB_HANDWRITING else ""
            add(checked(current == f), 800 + i, f"{T('sb_font')}: {f}{hand}")
        if current not in fonts:
            add(0x8, 831, f"{T('sb_font')}: {current}")
        add(0, 830, T("sb_font_other"))
        add(checked(self.wcfg.get("bold", True)), 793, T("sb_bold"))
        if self.kind == "scoreboard":
            add(checked(self.wcfg.get("names", True)), 795, T("sb_names"))
        add(0x1, 0, T("sb_same"))

    def menu_command(self, cmd):
        fonts = [f for f in SB_FONTS if font_installed(f)]
        if 790 <= cmd < 790 + len(SB_MODES):
            self.wcfg["mode"] = SB_MODES[cmd - 790]
        elif 800 <= cmd < 800 + len(fonts):
            self.wcfg["font"] = fonts[cmd - 800]
        elif cmd == 830:
            picked = pick_font(self.wcfg.get("font"), self.wcfg.get("bold", True))
            if not picked:
                return True
            self.wcfg["font"], self.wcfg["bold"] = picked
        elif cmd == 793:
            self.wcfg["bold"] = not self.wcfg.get("bold", True)
        elif cmd == 795:
            self.wcfg["names"] = not self.wcfg.get("names", True)
        elif cmd == 794:
            self.wcfg["x"] = self.wcfg["y"] = None
            self.place()
        elif cmd == 831:
            return True
        else:
            return False
        self.redraw()
        self.sync_style()
        return True


class BoostWidget(ScoreboardWidget):
    """Rocket League boost meter: a ring that fills up with your boost, the number in the middle.
    Same looks, team color, font and opacity as the score counter."""
    kind = "boost"
    HOME_TEXT = "bm_home"
    LAYOUT = 1

    def __init__(self, app, wid):
        self.boost, self.shown_boost, self.boosting = None, 0.0, False
        self.jump_at, self.sparks, self.prev_boost = None, [], None
        super().__init__(app, wid)

    def build(self):
        u = self.u
        self.D = 6.0 * u                          # ring diameter (~240 px at 1080p like the game's)
        self.margin = 0.9 * u
        size = self.D + 2 * self.margin
        self.make_canvas(size, size)
        self.shapes, self.backing = [], []
        self.place()
        self.refresh()

    def has_content(self):
        return self.in_match and self.boost is not None

    def on_rl(self, event, data):
        super().on_rl(event, data)
        data = data if isinstance(data, dict) else {}
        if event in ("Connected", "Disconnected", "MatchDestroyed"):
            self.boost, self.boosting, self.prev_boost = None, False, None
        elif event == "UpdateState":
            game = data.get("Game") or {}
            target = game.get("Target") if game.get("bHasTarget") and isinstance(game.get("Target"), dict) else None
            car = None
            if target:
                car = next((pl for pl in data.get("Players") or [] if isinstance(pl, dict)
                            and pl.get("Name") == target.get("Name") and pl.get("Shortcut") == target.get("Shortcut")), None)
            was_known = self.boost is not None
            if car is not None and isinstance(car.get("Boost"), (int, float)):
                self.boost = max(0, min(100, car["Boost"]))
                self.boosting = bool(car.get("bBoosting"))
                if self.prev_boost is not None and self.boost - self.prev_boost >= 10:
                    self.jump_at = time.monotonic()            # picked up a boost pad
                self.prev_boost = self.boost
            elif car is not None and car.get("bDemolished"):
                self.boost, self.boosting = 0, False         # demolished: empty tank (the game sends no number)
            # no car / no number for a moment (replays, kickoff): keep the last value instead of vanishing
            if (self.boost is not None) != was_known:
                self.refresh()

    def burst(self, team, now):
        pass                                      # no confetti on the boost meter

    def draw(self, c, now):
        dt = min(0.1, now - self.last_draw) if self.last_draw else 0.0
        self.last_draw = now
        u, mode, font = self.u, self.mode, self.font()
        bold = self.wcfg.get("bold", True)
        target = self.boost if self.boost is not None else 67          # sample value while moving widgets
        self.shown_boost += (target - self.shown_boost) * min(1.0, dt * 12) if dt else (target - self.shown_boost)
        val = self.shown_boost
        frac = max(0.0, min(1.0, val / 100))
        mine = self.colors[self.order()[0]]
        cx = cy = self.w / 2
        R = self.D / 2
        ring_w = 0.62 * u
        rr = R - ring_w / 2 - 0.25 * u                    # radius of the ring's middle line
        start, sweep = 135.0, 270.0
        low = val < 20.5
        lowp = 0.5 + 0.5 * math.cos(now * 2 * math.pi * 1.5) if low else 0.0
        jump = 0.0
        if self.jump_at and now - self.jump_at < 0.6:
            jump = 1.0 - (now - self.jump_at) / 0.6
        boostp = (0.5 + 0.5 * math.sin(now * 2 * math.pi * 4)) if self.boosting else 0.0
        gold = (1.0, 0.84, 0.3)

        # shadow + disc
        for k in range(3):
            c.ellipse(cx - R + 0.04 * u, cy - R + (k + 1) * 0.08 * u, 2 * R, 2 * R, fill=argb((0, 0, 0), 0.2 - k * 0.05))
        if mode == "enhanced":
            c.ellipse(cx - R, cy - R, 2 * R, 2 * R, fill=argb((0.06, 0.06, 0.1)))
            c.gradient_rect(cx - R * 0.92, cy - R * 0.92, 1.84 * R, 1.84 * R, R * 0.92,
                            fill=[argb((0.2, 0.21, 0.29)), argb((0.07, 0.07, 0.11)), argb((0.02, 0.02, 0.04))])
            c.ellipse(cx - R, cy - R, 2 * R, 2 * R, stroke=[argb(mine), argb(WHITE, 0.3), argb(mine)][0],
                      width=max(1.5, u / 14))
        elif mode == "pastel":
            pastel = mix(mine, WHITE, 0.62)
            c.ellipse(cx - R, cy - R, 2 * R, 2 * R, fill=argb(pastel))
            c.gradient_rect(cx - R, cy - R, 2 * R, 2 * R, R, fill=[argb(WHITE, 0.55), argb(WHITE, 0.0)])
            c.gradient_rect(cx - R, cy - R, 2 * R, 2 * R, R, stroke=self.rainbow(now, s=0.4, speed=0.08),
                            width=max(2.0, u / 10))
        else:
            for k in range(4):                    # soft rainbow neon around the disc
                c.gradient_rect(cx - R, cy - R, 2 * R, 2 * R, R, stroke=self.rainbow(now, a=0.16 / (k + 1)),
                                width=(k + 1) * 0.12 * u)
            c.ellipse(cx - R, cy - R, 2 * R, 2 * R, fill=argb((0.02, 0.02, 0.03)))
            c.gradient_rect(cx - R * 0.94, cy - R * 0.94, 1.88 * R, 1.88 * R, R * 0.94,
                            fill=[argb((0.02, 0.02, 0.03)), argb(mix((0.02, 0.02, 0.03), mine, 0.28))])
            c.gradient_rect(cx - R, cy - R, 2 * R, 2 * R, R, stroke=self.rainbow(now), width=max(2.0, u / 9))

        # ring: track, ticks, value (team color), glow
        box = (cx - rr, cy - rr, 2 * rr, 2 * rr)
        track = {"enhanced": argb(WHITE, 0.1), "pastel": argb(WHITE, 0.75), "black": argb(WHITE, 0.08)}[mode]
        c.arc(*box, start, sweep, track, ring_w)
        if low:                                    # low / empty: the whole track glows red in time with the pulse
            c.arc(*box, start, sweep, argb((1.0, 0.25, 0.25), 0.08 + 0.14 * lowp), ring_w)
        for k in range(11):                        # little marks every 10 %
            a = math.radians(start + sweep * k / 10)
            r1, r2 = rr - ring_w / 2 - 0.12 * u, rr - ring_w / 2 - (0.32 if k % 5 == 0 else 0.22) * u
            mark = argb((0.25, 0.22, 0.35), 0.5) if mode == "pastel" else argb(WHITE, 0.35)
            c.line(cx + r1 * math.cos(a), cy + r1 * math.sin(a), cx + r2 * math.cos(a), cy + r2 * math.sin(a),
                   mark, max(1.0, u / 18))
        if low:
            arc_col = [argb(mix((1.0, 0.25, 0.25), WHITE, 0.3 * lowp)), argb((1.0, 0.35, 0.3))]
        elif mode == "pastel":
            arc_col = [argb(mix(mine, WHITE, 0.25)), argb(mine)]
        elif mode == "black" and frac >= 0.999:
            arc_col = self.rainbow(now)
        else:
            arc_col = [argb(mix(mine, WHITE, 0.45)), argb(mine), argb(mix(mine, WHITE, 0.2))]
        if frac > 0.005:
            glow_a = 0.18 + 0.25 * boostp + 0.35 * jump
            for k in range(3):
                c.arc(*box, start, sweep * frac, argb(mine if not low else (1.0, 0.3, 0.3), glow_a / (k + 1)),
                      ring_w + (k + 1) * 0.28 * u)
            c.arc(*box, start, sweep * frac, arc_col, ring_w)
            c.arc(cx - rr + ring_w * 0.2, cy - rr + ring_w * 0.2, 2 * rr - ring_w * 0.4, 2 * rr - ring_w * 0.4,
                  start, sweep * frac, argb(WHITE, 0.25 + 0.3 * jump), ring_w * 0.18)        # shiny inner line
            # the tip: a bright dot (sparks fly while boosting)
            a = math.radians(start + sweep * frac)
            tx, ty = cx + rr * math.cos(a), cy + rr * math.sin(a)
            c.ellipse(tx - ring_w * 0.42, ty - ring_w * 0.42, ring_w * 0.84, ring_w * 0.84,
                      fill=argb(WHITE, 0.55 + 0.4 * boostp))
            if self.boosting and dt:
                for _ in range(2):
                    ang = a + math.pi / 2 + (random.random() - 0.5) * 1.2
                    sp = (1.5 + random.random() * 2.5) * u
                    self.sparks.append([tx, ty, math.cos(ang) * sp, math.sin(ang) * sp, now,
                                        mix(mine, gold, random.random())])
        if frac <= 0.005:                          # empty: a red light pulses where the ring starts
            a = math.radians(start)
            tx, ty = cx + rr * math.cos(a), cy + rr * math.sin(a)
            for k in range(3):
                r = ring_w * (0.5 + 0.35 * k) * (1 + 0.25 * lowp)
                c.ellipse(tx - r, ty - r, 2 * r, 2 * r, fill=argb((1.0, 0.25, 0.25), (0.55 if k == 0 else 0.18 / k) * (0.5 + 0.5 * lowp)))
            c.ellipse(tx - ring_w * 0.38, ty - ring_w * 0.38, ring_w * 0.76, ring_w * 0.76,
                      fill=argb(mix((1.0, 0.3, 0.3), WHITE, 0.4 * lowp)))
        if frac >= 0.999 and not self.boosting:     # full tank: a light runs around the ring
            a = math.radians(start + sweep * ((now * 0.5) % 1.0))
            c.ellipse(cx + rr * math.cos(a) - ring_w * 0.5, cy + rr * math.sin(a) - ring_w * 0.5, ring_w, ring_w,
                      fill=argb(WHITE, 0.7))
        alive = []
        for sp in self.sparks:
            age = now - sp[4]
            if age > 0.45:
                continue
            sp[0] += sp[2] * dt
            sp[1] += sp[3] * dt
            alive.append(sp)
            r = 0.09 * u * (1 - age / 0.45) + 0.02 * u
            c.ellipse(sp[0] - r, sp[1] - r, 2 * r, 2 * r, fill=argb(sp[5], 1 - age / 0.45))
        self.sparks = alive[-80:]

        # the number + "BOOST"
        scale = 1.0 + 0.35 * jump * jump
        size = 1.75 * u * scale
        label = str(int(round(val)))
        if mode == "enhanced":
            fill = argb(mix(WHITE, (1.0, 0.3, 0.3), lowp)) if low else argb(WHITE)
            outline, ow = argb((0, 0, 0), 0.6), max(2.0, u / 8)
            small = argb((0.75, 0.75, 0.82))
        elif mode == "pastel":
            hh, ss, _ = colorsys.rgb_to_hsv(*mine)
            ink = colorsys.hsv_to_rgb(hh, min(1.0, ss * 0.9 + 0.1), 0.42)
            fill = argb(mix(ink, (0.85, 0.1, 0.2), lowp)) if low else argb(ink)
            outline, ow = argb(WHITE, 0.85), max(2.0, u / 9)
            small = argb(mix(ink, WHITE, 0.25))
        else:
            fill = argb(mix((1.0, 0.25, 0.25), WHITE, lowp)) if low else \
                [argb(mix(mine, WHITE, 0.55)), argb(mix(mine, WHITE, 0.15)), argb(mix(mine, WHITE, 0.55))]
            outline, ow = argb((0, 0, 0), 0.9), max(2.0, u / 8)
            small = argb(mix(mine, WHITE, 0.4))
        bw = 2.6 * u
        self.number(c, label, cx - bw / 2, cy - 1.05 * u, bw, 1.9 * u, size, fill, bold, font, outline, ow)
        c.text("BOOST", cx - 2 * u, cy + 0.75 * u, 4 * u, 0.6 * u, 0.36 * u, small, bold=True, font=font)

    def busy(self, now):
        return bool(self.sparks or self.boosting or (self.jump_at and now - self.jump_at < 0.7)
                    or abs((self.boost or 0) - self.shown_boost) > 0.3)


class ClockWidget(ArgbWidget):
    """Clock (digital or analog) with the date, and a timer that tells you when time is up."""
    kind = "clock"
    TIMERS = (5, 10, 15, 20, 30, 45, 60, 90)

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.last_draw = 0.0
        self.done_at = None
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(7.0 * u, 3.0 * u)
        self.place()
        self.refresh()

    def timer_left(self):
        end = self.wcfg.get("timer_end")
        return None if not end else end - time.time()

    def draw(self, c, now):
        self.last_draw = now
        u, app = self.u, self.app
        t = datetime.now()
        panel(c, self.w, self.h, u, app, now)
        colors = c.theme.nums(app, now)
        muted, light = c.theme.muted, c.theme.text
        days = T("days").split(",")
        date = f"{days[t.weekday()]} {t.day}. {t.month}." if LANG == "cs" else f"{days[t.weekday()]} {t.day}/{t.month}"
        left = self.timer_left()
        flashing = self.done_at is not None and now - self.done_at < 10
        if self.wcfg.get("analog"):
            r = 1.2 * u
            cx, cy = 0.35 * u + r, self.h / 2
            c.ellipse(cx - r, cy - r, 2 * r, 2 * r, fill=argb((0.03, 0.03, 0.05)), stroke=colors[1], width=max(1.5, u / 12))
            for k in range(12):
                a = math.radians(k * 30 - 90)
                r1 = r * (0.78 if k % 3 == 0 else 0.86)
                c.line(cx + r1 * math.cos(a), cy + r1 * math.sin(a), cx + r * 0.93 * math.cos(a), cy + r * 0.93 * math.sin(a),
                       argb(WHITE, 0.8 if k % 3 == 0 else 0.4), max(1.0, u / (14 if k % 3 == 0 else 22)))
            sec = t.second + t.microsecond / 1e6
            hands = ((((t.hour % 12) + t.minute / 60) * 30, 0.5, u / 7, argb(WHITE)),
                     ((t.minute + sec / 60) * 6, 0.76, u / 10, argb(WHITE)))
            for deg, length, width, col in hands:
                a = math.radians(deg - 90)
                c.line(cx, cy, cx + r * length * math.cos(a), cy + r * length * math.sin(a), col, width)
            if self.wcfg.get("seconds", True):
                a = math.radians(sec * 6 - 90)
                c.line(cx, cy, cx + r * 0.84 * math.cos(a), cy + r * 0.84 * math.sin(a), colors[3], max(1.0, u / 20))
            c.ellipse(cx - 0.09 * u, cy - 0.09 * u, 0.18 * u, 0.18 * u, fill=colors[2])
            tx, tw = cx + r + 0.3 * u, self.w - (cx + r + 0.3 * u) - 0.3 * u
            c.text(t.strftime("%H:%M"), tx, 0.3 * u, tw, 1.1 * u, 0.85 * u, colors, outline=argb((0, 0, 0), 0.6),
                   outline_w=u / 12)
            c.text(date, tx, 1.35 * u, tw, 0.5 * u, 0.3 * u, light, bold=False)
            line_y, x0, x1 = 1.95 * u, tx, tx + tw
        else:
            secs = self.wcfg.get("seconds", True)
            main = t.strftime("%H:%M")
            c.text(main, 0, 0.05 * u, self.w * (0.82 if secs else 1.0), 1.55 * u, 1.25 * u, colors,
                   outline=c.theme.outline, outline_w=u / 10)
            if secs:
                c.text(f"{t.second:02d}", self.w * 0.74, 0.45 * u, self.w * 0.2, 1.0 * u, 0.55 * u, muted)
            c.text(date, 0, 1.55 * u, self.w, 0.5 * u, 0.32 * u, light, bold=False)
            line_y, x0, x1 = 2.15 * u, 0.5 * u, self.w - 0.5 * u
        c.line(x0, line_y, x1, line_y, c.theme.line, max(1, u / 25))
        if flashing:
            blink = 0.5 + 0.5 * math.cos((now - self.done_at) * 2 * math.pi * 2)
            c.round_rect(x0 - 0.2 * u, line_y + 0.08 * u, x1 - x0 + 0.4 * u, self.h - line_y - 0.25 * u, 0.2 * u,
                         fill=argb((0.9, 0.2, 0.25), 0.35 + 0.5 * blink))
            c.text(T("clk_done"), x0, line_y, x1 - x0, self.h - line_y - 0.1 * u, 0.34 * u, argb(WHITE))
        elif left is not None:
            total = self.wcfg.get("timer_len", 1) * 60
            frac = max(0.0, min(1.0, left / total))
            bar_x, bar_w = x0, x1 - x0
            by = line_y + 0.2 * u
            c.round_rect(bar_x, by, bar_w, 0.12 * u, 0.06 * u, fill=argb(WHITE, 0.12))
            c.round_rect(bar_x, by, max(0.12 * u, bar_w * frac), 0.12 * u, 0.06 * u, fill=colors[1])
            mm, ss = divmod(int(max(0, left)) + (1 if left % 1 else 0), 60)
            c.text(T("clk_left").format(f"{mm}:{ss:02d}"), x0, by + 0.12 * u, x1 - x0, self.h - by - 0.2 * u, 0.3 * u,
                   light, bold=False)
        else:
            c.text(t.strftime("%Y"), x0, line_y, x1 - x0, self.h - line_y - 0.05 * u, 0.28 * u, muted, bold=False)

    def tick(self, now):
        left = self.timer_left()
        if left is not None and left <= 0:              # time's up
            minutes = self.wcfg.get("timer_len", 0)
            self.wcfg["timer_end"] = None
            self.done_at = now
            self.app.balloon(T("clk_done"), T("clk_done_text").format(minutes))
            for k in range(3):
                user32.MessageBeep(0x30)
            save_settings(self.app.cfg)
        if not self.shown:
            return
        busy = self.done_at is not None and now - self.done_at < 10
        rate = 1 / 15 if (busy or self.wcfg.get("analog") or self.app.animated()) else 0.5
        if now - self.last_draw >= rate:
            self.redraw()

    def menu_items(self, add, checked):
        add(checked(self.wcfg.get("analog")), 860, T("clk_analog"))
        add(checked(self.wcfg.get("seconds", True)), 861, T("clk_seconds"))
        add(0x800, 0, None)
        for i, m in enumerate(self.TIMERS):
            add(checked(self.timer_left() is not None and self.wcfg.get("timer_len") == m), 862 + i, T("clk_timer").format(m))
        if self.timer_left() is not None:
            add(0, 875, T("clk_cancel"))

    def menu_command(self, cmd):
        if cmd == 860:
            self.wcfg["analog"] = not self.wcfg.get("analog")
        elif cmd == 861:
            self.wcfg["seconds"] = not self.wcfg.get("seconds", True)
        elif 862 <= cmd < 862 + len(self.TIMERS):
            m = self.TIMERS[cmd - 862]
            self.wcfg.update(timer_len=m, timer_end=time.time() + m * 60)
            self.done_at = None
        elif cmd == 875:
            self.wcfg["timer_end"] = None
        else:
            return False
        self.redraw()
        return True



class ChestWidget(ArgbWidget):
    """Fortnite chest counter. Fortnite sends no data about chests, so it watches the "SEARCH" prompt next to the
    crosshair (screen reading): E pressed while it shows, then it disappears = a chest opened. A key / mouse button
    also counts +1 by hand. Resets when a new match starts; keeps today, total and the record per match."""
    kind = "chests"
    OPEN_TIME = 1.1

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.opened_at = -9.0
        self.prompt = False
        self.capture_what = "key"
        self.seen_at = -99.0              # last time "SEARCH" was on screen
        self.armed_at = None              # E pressed on a chest: waiting for the prompt to go away
        self.last_draw = 0.0
        self.sparkles = []
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(7.6 * u, 3.5 * u)
        self.place()
        self.refresh()

    def has_content(self):
        return FORTNITE_EXE in self.app.running or self.prompt

    @property
    def key(self):
        k = self.wcfg.get("key") or ["m", "X2"]
        return tuple(k)

    @property
    def interact(self):
        return tuple(self.wcfg.get("interact") or ["sc", 0x12, 0])          # E

    @property
    def auto(self):
        return OCR_OK and self.wcfg.get("auto", True)

    def pressed_interact(self, now):
        if now - self.seen_at < 0.9:                     # the prompt was just on screen
            self.armed_at = now

    def ocr_result(self, t, kind, search):
        """search = the "SEARCH" prompt is on screen in this picture."""
        if search:
            self.seen_at = max(self.seen_at, t)
            if kind == "press":
                self.armed_at = t
            return
        if self.armed_at is not None and t > self.armed_at + 0.2:
            if t - self.armed_at < 4.0:                  # the prompt went away after E: the chest opened
                self.add(1)
            self.armed_at = None

    def add(self, n):
        st = self.app.stats
        st.check_day()
        d = st.data
        d["fn_match"] = max(0, d.get("fn_match", 0) + n)
        d["fn_today"] = max(0, d.get("fn_today", 0) + n)
        d["fn_total"] = max(0, d.get("fn_total", 0) + n)
        d["fn_best"] = max(d.get("fn_best", 0), d["fn_match"])
        if n > 0:
            now = time.monotonic()
            self.opened_at = now
            u = self.u
            for _ in range(18):                    # golden sparkles out of the chest
                a = -math.pi / 2 + (random.random() - 0.5) * 2.2
                sp = (1.2 + random.random() * 2.2) * u
                self.sparkles.append([1.5 * u, 1.2 * u, math.cos(a) * sp, math.sin(a) * sp, now])
        if self.shown:
            self.redraw()

    def match_started(self):
        self.app.stats.data["fn_match"] = 0
        if self.shown:
            self.redraw()

    def draw(self, c, now):
        dt = min(0.1, now - self.last_draw) if self.last_draw else 0.0
        self.last_draw = now
        u, app = self.u, self.app
        d = app.stats.data
        panel(c, self.w, self.h, u, app, now)
        t = (now - self.opened_at) / self.OPEN_TIME
        opening = 0 <= t < 1
        lid = math.sin(math.pi * min(1.0, t * 1.6)) if opening else 0.0      # lid opens and closes again
        gold, gold_dark = (1.0, 0.8, 0.25), (0.75, 0.5, 0.1)
        wood, wood_dark = (0.55, 0.32, 0.16), (0.36, 0.2, 0.1)
        ink = argb((0.08, 0.05, 0.03))
        line = max(1.2, u * 0.07)
        cx, base = 1.5 * u, 2.05 * u                      # the chest (an original little drawing)
        bw, bh = 2.0 * u, 0.85 * u
        if opening:                                      # golden light rays from inside
            for k in range(9):
                a = -math.pi / 2 + (k - 4) * 0.28 + math.sin(now * 3 + k) * 0.05
                L = (1.1 + 0.3 * math.sin(now * 7 + k)) * u * lid
                c.polygon([(cx - 0.12 * u, base - bh), (cx + 0.12 * u, base - bh),
                           (cx + math.cos(a + 0.08) * L, base - bh + math.sin(a + 0.08) * L),
                           (cx + math.cos(a - 0.08) * L, base - bh + math.sin(a - 0.08) * L)],
                          fill=argb(gold, 0.35 * lid))
        c.round_rect(cx - bw / 2, base - bh, bw, bh, 0.12 * u, fill=argb(wood), stroke=ink, width=line)
        c.round_rect(cx - bw / 2 + 0.1 * u, base - bh + 0.1 * u, bw - 0.2 * u, bh * 0.35, 0.06 * u,
                     fill=argb(mix(wood, WHITE, 0.12)))
        for bx in (cx - bw / 2 + 0.28 * u, cx + bw / 2 - 0.5 * u):                # metal bands
            c.round_rect(bx, base - bh, 0.22 * u, bh, 0.04 * u, fill=argb(gold_dark), stroke=ink, width=line * 0.6)
        lift = lid * 0.45 * u                                                    # the lid
        lid_h = 0.6 * u
        ly = base - bh - lid_h - lift
        if opening and lid > 0.05:
            c.ellipse(cx - bw / 2 + 0.2 * u, base - bh - 0.25 * u, bw - 0.4 * u, 0.5 * u, fill=argb(gold, 0.85 * lid))
        c.round_rect(cx - bw / 2 - 0.05 * u, ly, bw + 0.1 * u, lid_h, 0.4 * u, fill=argb(wood_dark), stroke=ink,
                     width=line)
        for bx in (cx - bw / 2 + 0.28 * u, cx + bw / 2 - 0.5 * u):
            c.round_rect(bx, ly, 0.22 * u, lid_h, 0.04 * u, fill=argb(gold_dark), stroke=ink, width=line * 0.6)
        c.round_rect(cx - 0.22 * u, base - bh - 0.15 * u - lift * 0.3, 0.44 * u, 0.5 * u, 0.08 * u,
                     fill=argb(gold), stroke=ink, width=line * 0.7)                   # lock
        alive = []
        for sp in self.sparkles:
            age = now - sp[4]
            if age > 0.9:
                continue
            sp[0] += sp[2] * dt
            sp[1] += sp[3] * dt
            sp[3] += 3.5 * u * dt
            alive.append(sp)
            r = 0.1 * u * (1 - age / 0.9) + 0.02 * u
            c.ellipse(sp[0] - r, sp[1] - r, 2 * r, 2 * r, fill=argb(mix(gold, WHITE, 0.4), 1 - age / 0.9))
        self.sparkles = alive

        tx = 3.1 * u
        tw = self.w - tx - 0.3 * u
        if self.prompt:
            c.text(T("fn_prompt"), tx, 0.3 * u, tw, 2.6 * u, 0.34 * u, argb((1.0, 0.85, 0.4)), bold=True)
            return
        pop = 1.0 + (0.4 * math.exp(-6 * t) * math.cos(t * 10) if opening else 0.0)
        colors = [argb(mix(gold, WHITE, 0.3)), argb(gold), argb((1.0, 0.6, 0.15))]
        c.text(str(d.get("fn_match", 0)), tx - tw, 0.1 * u, 3 * tw, 1.5 * u, 1.15 * u * pop, colors,
               outline=c.theme.outline, outline_w=u / 10)
        c.text(T("fn_match"), tx, 1.5 * u, tw, 0.5 * u, 0.32 * u, c.theme.muted)
        running = FORTNITE_EXE in app.running
        c.line(0.5 * u, 2.25 * u, self.w - 0.5 * u, 2.25 * u, c.theme.line, max(1, u / 25))
        c.text(T("fn_line").format(d.get("fn_today", 0), d.get("fn_total", 0), d.get("fn_best", 0)), 0, 2.3 * u,
               self.w, 0.55 * u, 0.3 * u, c.theme.text, bold=False)
        if not running:
            hint = T("fn_closed")
        elif self.auto:
            hint = T("fn_hint_auto")
        else:
            hint = T("fn_hint").format(key_label(self.key))
        c.text(hint, 0, 2.8 * u, self.w, 0.5 * u, 0.26 * u, c.theme.muted, bold=False)

    def tick(self, now):
        if not self.shown:
            return
        busy = now - self.opened_at < self.OPEN_TIME + 0.1 or self.sparkles
        if (busy and now - self.last_draw > 1 / FPS) or now - self.last_draw >= 1.0:
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 850, T("fn_plus"))
        add(0, 851, T("fn_minus"))
        add(0x800, 0, None)
        if OCR_OK:
            add(checked(self.auto), 855, T("fn_auto"))
        add(0x1, 0, T("fn_key").format(key_label(self.key)))
        add(0, 852, T("fn_change"))
        add(0x800, 0, None)
        add(0, 853, T("fn_reset_match"))
        add(0, 854, T("fn_reset_all"))

    def menu_command(self, cmd):
        d = self.app.stats.data
        if cmd == 850:
            self.add(1)
        elif cmd == 851:
            self.add(-1)
        elif cmd == 852:
            self.prompt = True
            self.app.chest_capture = self
            self.refresh()
        elif cmd == 855:
            self.wcfg["auto"] = not self.auto
        elif cmd == 853:
            d["fn_match"] = 0
        elif cmd == 854:
            d.update(fn_match=0, fn_today=0, fn_total=0, fn_best=0)
        else:
            return False
        self.redraw()
        return True

    def set_key(self, kid):
        self.prompt = False
        self.app.chest_capture = None
        if kid is not None:
            self.wcfg[self.capture_what] = list(kid)
            save_settings(self.app.cfg)
        self.refresh()

class FnMatchWidget(ArgbWidget):
    """Fortnite match time from the game's own log: island, battle bus, match, end; today's matches."""
    kind = "fnmatch"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.phase, self.since, self.bus_at = "lobby", time.time(), None
        self.last_len = None
        self.last_draw = 0.0
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(7.4 * u, 3.1 * u)
        self.place()
        self.refresh()

    def has_content(self):
        return FORTNITE_EXE in self.app.running

    def finish(self, now):
        if self.bus_at is None:
            return
        length = int(now - self.bus_at)
        self.bus_at = None
        if length < 20:
            return
        self.last_len = length
        st = self.app.stats
        st.check_day()
        d = st.data
        d["fnm_count"] = d.get("fnm_count", 0) + 1
        d["fnm_time"] = d.get("fnm_time", 0) + length
        d["fnm_best"] = max(d.get("fnm_best", 0), length)
        save_settings(self.app.cfg)

    def on_fn(self, event, value):
        now = time.time()
        if event == "phase":
            if value == "Warmup":
                self.finish(now)
                self.phase, self.since = "warmup", now
            elif value == "Aircraft":
                self.phase, self.since, self.bus_at = "bus", now, now
            elif value == "SafeZones":
                self.phase = "game"
                if self.bus_at is None:
                    self.bus_at = now
            elif value == "EndGame":
                self.finish(now)
                self.phase, self.since = "end", now
        elif event == "lobby":
            self.finish(now)
            self.phase, self.since = "lobby", now
        if self.shown:
            self.redraw()

    def draw(self, c, now):
        self.last_draw = now
        u, app, th = self.u, self.app, c.theme
        panel(c, self.w, self.h, u, app, now)
        t = time.time()
        d = app.stats.data
        label = T({"lobby": "fm_lobby", "warmup": "fm_warmup", "bus": "fm_bus", "game": "fm_game",
                   "end": "fm_end"}[self.phase])
        accent = argb(th.accent(app, now))
        c.text(label, 0.45 * u, 0.18 * u, self.w - 0.9 * u, 0.5 * u, 0.32 * u, accent, align=0)
        if self.phase in ("bus", "game") and self.bus_at:
            big, sub = clock_text(t - self.bus_at, hours=False), ""
        elif self.phase == "warmup":
            big, sub = clock_text(t - self.since, hours=False), ""
        else:
            big = clock_text(self.last_len, hours=False) if self.last_len else "--:--"
            sub = T("fm_last")
        c.text(sub, 0, 0.18 * u, self.w - 0.45 * u, 0.5 * u, 0.28 * u, th.muted, bold=False, align=2)
        c.text(big, 0, 0.55 * u, self.w, 1.45 * u, 1.1 * u, th.nums(app, now), outline=th.outline, outline_w=u / 10)
        n = d.get("fnm_count", 0)
        avg = clock_text(d.get("fnm_time", 0) / n, hours=False) if n else "-"
        best = clock_text(d.get("fnm_best", 0), hours=False) if n else "-"
        c.line(0.5 * u, 2.15 * u, self.w - 0.5 * u, 2.15 * u, th.line, max(1, u / 25))
        c.text(T("fm_line").format(n, avg, best), 0, 2.2 * u, self.w, 0.7 * u, 0.3 * u, th.text, bold=False)

    def tick(self, now):
        if self.shown and now - self.last_draw >= (1 / 15 if self.app.animated() else 0.5):
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 890, T("fm_reset"))

    def menu_command(self, cmd):
        if cmd == 890:
            self.app.stats.data.update(fnm_count=0, fnm_time=0, fnm_best=0)
            self.redraw()
            return True
        return False


class DrinkWidget(ArgbWidget):
    """Reminds you to drink: the glass empties over the interval; when it's empty you get a reminder."""
    kind = "drink"
    INTERVALS = (15, 20, 30, 45, 60, 90)

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.left = self.interval * 60.0            # seconds until the next reminder
        self.due_at = None
        self.last_tick, self.last_draw = time.monotonic(), 0.0
        self.build()

    @property
    def interval(self):
        v = self.wcfg.get("interval", 30)
        return v if v in self.INTERVALS else 30

    def build(self):
        u = self.u
        self.make_canvas(6.6 * u, 2.9 * u)
        self.place()
        self.refresh()

    def paused(self):
        return self.wcfg.get("only_games") and not self.app.game_since

    def drank(self):
        st = self.app.stats
        st.check_day()
        st.data["drinks"] = st.data.get("drinks", 0) + 1
        self.left, self.due_at = self.interval * 60.0, None
        save_settings(self.app.cfg)
        if self.shown:
            self.redraw()

    def draw(self, c, now):
        self.last_draw = now
        u, app, th = self.u, self.app, c.theme
        panel(c, self.w, self.h, u, app, now)
        due = self.due_at is not None
        frac = 0.0 if due else max(0.0, min(1.0, self.left / (self.interval * 60.0)))
        blink = 0.5 + 0.5 * math.cos(now * 2 * math.pi * 1.5) if due else 0.0
        # the glass (an original little drawing)
        gx, gy, gw, gh = 0.55 * u, 0.4 * u, 1.5 * u, 2.1 * u
        inset = 0.22 * u
        glass = [gx, gy, gx + gw, gy, gx + gw - inset, gy + gh, gx + inset, gy + gh]
        water = (0.25, 0.65, 1.0)
        if frac > 0.01:
            level = gy + gh - (gh - 0.15 * u) * frac
            k = (level - gy) / gh * inset
            wave = math.sin(now * 2.5) * 0.06 * u
            pts = [gx + k + 0.05 * u, level + wave, gx + gw - k - 0.05 * u, level - wave,
                   gx + gw - inset - 0.04 * u, gy + gh - 0.05 * u, gx + inset + 0.04 * u, gy + gh - 0.05 * u]
            c.shape(pts, fill=[argb(mix(water, WHITE, 0.35), 0.9), argb(water, 0.95)])
        c.shape(glass, fill=argb(WHITE, 0.08), stroke=argb(th.text_rgb, 0.85), width=max(1.5, u / 12))
        c.line(gx + 0.25 * u, gy + 0.3 * u, gx + 0.38 * u, gy + gh - 0.4 * u, argb(WHITE, 0.35), max(1.0, u / 16))
        tx = gx + gw + 0.45 * u
        tw = self.w - tx - 0.3 * u
        if due:
            c.round_rect(tx - 0.15 * u, 0.45 * u, tw + 0.3 * u, 1.3 * u, 0.3 * u,
                         fill=argb(mix(water, WHITE, 0.2), 0.35 + 0.5 * blink))
            c.text(T("dr_now"), tx, 0.45 * u, tw, 1.3 * u, 0.62 * u, argb(WHITE), outline=argb((0, 0, 0), 0.5),
                   outline_w=u / 12)
        else:
            mins = int(self.left // 60) + 1
            c.text(f"{mins} min", tx, 0.35 * u, tw, 1.1 * u, 0.85 * u, th.nums(app, now), outline=th.outline,
                   outline_w=u / 10)
            sub = T("dr_paused") if self.paused() else T("dr_in")
            c.text(sub, tx, 1.4 * u, tw, 0.5 * u, 0.3 * u, th.muted, bold=False)
        c.text(T("dr_today").format(app.stats.data.get("drinks", 0)), tx, 2.0 * u, tw, 0.5 * u, 0.3 * u, th.text,
               bold=False)

    def tick(self, now):
        dt, self.last_tick = now - self.last_tick, now
        if self.due_at is None and not self.paused():
            self.left -= dt
            if self.left <= 0:                       # time to drink
                self.due_at = now
                self.app.balloon(T("dr_title"), T("dr_text").format(self.interval))
                user32.MessageBeep(0x40)
        elif self.due_at is not None and now - self.due_at > 60:
            self.drank()                             # the reminder showed for a minute: next round
        if self.shown and (now - self.last_draw >= (1 / 20 if self.due_at else 0.5)):
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 880, T("dr_done"))
        add(0x800, 0, None)
        for i, m in enumerate(self.INTERVALS):
            add(checked(self.interval == m), 881 + i, T("dr_interval").format(m))
        add(checked(bool(self.wcfg.get("only_games"))), 887, T("dr_games"))
        add(0, 888, T("dr_reset"))

    def menu_command(self, cmd):
        if cmd == 880:
            self.drank()
            return True
        if 881 <= cmd < 881 + len(self.INTERVALS):
            self.wcfg["interval"] = self.INTERVALS[cmd - 881]
            self.left = min(self.left, self.interval * 60.0)
        elif cmd == 887:
            self.wcfg["only_games"] = not self.wcfg.get("only_games")
        elif cmd == 888:
            self.app.stats.data["drinks"] = 0
        else:
            return False
        self.redraw()
        return True


class DashWidget(ArgbWidget):
    """Geometry Dash: a little cube that jumps with you and counts your jumps (clicks, Space, Up, W in the game)."""
    kind = "dash"
    JUMP_TIME = 0.38

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.jumped_at = -9.0
        self.spin = 0
        self.last_draw = 0.0
        self.build()

    def build(self):
        u = self.u
        self.make_canvas(7.0 * u, 3.4 * u)
        self.place()
        self.refresh()

    def has_content(self):
        return any(is_geometry_dash(x) for x in self.app.running)

    def jump(self):
        now = time.monotonic()
        if now - self.jumped_at >= self.JUMP_TIME:
            self.spin = (self.spin + 1) % 4
        self.jumped_at = now
        if self.shown:
            self.redraw()

    def draw(self, c, now):
        self.last_draw = now
        u, app = self.u, self.app
        panel(c, self.w, self.h, u, app, now)
        st = app.stats.data
        # the cube (an original little character)
        t = min(1.0, (now - self.jumped_at) / self.JUMP_TIME)
        size = 1.5 * u
        ground = self.h - 0.55 * u
        lift = math.sin(math.pi * t) * 0.85 * u if t < 1 else 0.0
        angle = math.radians(90 * ((self.spin - 1) + (t if t < 1 else 1)))
        cx, cy = 0.45 * u + size / 2 + 0.1 * u, ground - size / 2 - lift
        c.line(0.35 * u, ground + 0.06 * u, 2.6 * u, ground + 0.06 * u, argb((1, 1, 1), 0.25), max(1, u / 18))

        def pt(dx, dy):
            ca, sa = math.cos(angle), math.sin(angle)
            return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)

        half = size / 2
        main = app.glow_color(0.15, now)
        c.polygon([pt(-half, -half), pt(half, -half), pt(half, half), pt(-half, half)],
                  fill=argb(main), stroke=argb((0.03, 0.03, 0.05)), width=max(1.5, u / 10))
        inner = half * 0.72
        c.polygon([pt(-inner, -inner), pt(inner, -inner), pt(inner, inner), pt(-inner, inner)],
                  fill=argb(mix(main, WHITE, 0.55)), stroke=argb((0.03, 0.03, 0.05)), width=max(1, u / 16))
        dark = argb((0.03, 0.03, 0.05))
        for ox in (-0.13 * size, 0.13 * size):                    # eyes
            e = 0.055 * size
            c.polygon([pt(ox - e, -0.1 * size - e), pt(ox + e, -0.1 * size - e), pt(ox + e, -0.1 * size + e),
                       pt(ox - e, -0.1 * size + e)], fill=dark)
        c.polygon([pt(-0.14 * size, 0.08 * size), pt(0.14 * size, 0.08 * size), pt(0.14 * size, 0.14 * size),
                   pt(-0.14 * size, 0.14 * size)], fill=dark)                                      # smile

        # numbers
        tx = 2.8 * u
        colors = c.theme.nums(app, now)
        c.text(f"{st.get('gd_jumps', 0):,}".replace(",", " "), tx, 0.2 * u, self.w - tx - 0.3 * u, 1.3 * u, 1.0 * u,
               colors, outline=c.theme.outline, outline_w=u / 10)
        c.text(T("gd_jumps"), tx, 1.45 * u, self.w - tx - 0.3 * u, 0.5 * u, 0.32 * u, c.theme.muted)
        played = app.gd_seconds
        line = T("gd_time").format(clock_text(played, hours=False)) if self.has_content() else T("gd_closed")
        c.text(line, tx, 2.0 * u, self.w - tx - 0.3 * u, 0.5 * u, 0.3 * u, c.theme.text, bold=False)
        c.text(T("gd_total").format(f"{st.get('gd_total', 0):,}".replace(",", " ")), tx, 2.5 * u,
               self.w - tx - 0.3 * u, 0.5 * u, 0.28 * u, c.theme.muted, bold=False)

    def tick(self, now):
        if not self.shown:
            return
        moving = now - self.jumped_at < self.JUMP_TIME + 0.05
        if (moving and now - self.last_draw > 1 / FPS) or now - self.last_draw >= 1.0 or \
                (self.app.animated() and now - self.last_draw > 1 / RGB_FPS):
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 780, T("gd_reset"))

    def menu_command(self, cmd):
        if cmd == 780:
            self.app.stats.data["gd_jumps"] = 0
            self.redraw()
            return True
        return False


# ---------------------------------------------------------------- text and image widgets
class TextWidget(ArgbWidget):
    kind = "text"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.last_draw = 0.0
        self.build()

    def label(self):
        return f"{T('w_text')}: {self.wcfg.get('text', '')[:18]}"

    def build(self):
        u = self.u
        probe = Canvas(1, 1)
        x, y, w, h = probe.measure(self.wcfg.get("text") or " ", 1.1 * u, self.wcfg.get("bold", True))
        probe.close()
        self.pad = 0.35 * u
        self.text_box = (x, y, w, h)
        self.make_canvas(w + 2 * self.pad + 2, h + 2 * self.pad + 2)
        self.place()
        self.refresh()

    def colors(self, now):
        mode = self.wcfg.get("color", "effect")
        if mode == "effect":
            return self.app.theme_for(self).nums(self.app, now)
        if mode == "white":
            return argb(WHITE)
        return argb(COLORS.get(mode) or WHITE)

    def draw(self, c, now):
        self.last_draw = now
        u = self.u
        if self.wcfg.get("background"):
            c.round_rect(0.5, 0.5, self.w - 1, self.h - 1, 0.35 * u, fill=argb((0.05, 0.05, 0.07), 0.8))
        x, y, w, h = self.text_box
        c.text(self.wcfg.get("text", ""), self.pad - x, self.pad - y, w + 2 * x + 4, h + 2 * y + 4, 1.1 * u,
               self.colors(now), bold=self.wcfg.get("bold", True), align=0, valign=0,
               outline=argb((0, 0, 0), 0.75), outline_w=max(2.0, u / 7))

    def tick(self, now):
        if (self.shown and self.wcfg.get("color", "effect") == "effect" and self.app.animated()
                and now - self.last_draw > 1 / RGB_FPS):
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 730, T("edit_text"))
        mode = self.wcfg.get("color", "effect")
        add(checked(mode == "effect"), 731, f"{T('text_color')}: {T('tc_effect')}")
        add(checked(mode == "white"), 732, f"{T('text_color')}: {T('c_white')}")
        for i, name in enumerate(n for n in COLORS if n not in ("rainbow", "white")):
            add(checked(mode == name), 740 + i, f"{T('text_color')}: {T('c_' + name)}")
        add(checked(self.wcfg.get("bold", True)), 733, T("bold"))
        add(checked(self.wcfg.get("background")), 734, T("text_bg"))

    def menu_command(self, cmd):
        names = [n for n in COLORS if n not in ("rainbow", "white")]
        if cmd == 730:
            text = TextDialog.ask(T("w_text"), T("text_prompt"), self.wcfg.get("text", ""))
            if text is None or not text.strip():
                return True
            self.wcfg["text"] = text
        elif cmd == 731:
            self.wcfg["color"] = "effect"
        elif cmd == 732:
            self.wcfg["color"] = "white"
        elif 740 <= cmd < 740 + len(names):
            self.wcfg["color"] = names[cmd - 740]
        elif cmd == 733:
            self.wcfg["bold"] = not self.wcfg.get("bold", True)
        elif cmd == 734:
            self.wcfg["background"] = not self.wcfg.get("background")
        else:
            return False
        self.build()
        return True


class ImageWidget(ArgbWidget):
    kind = "image"

    def __init__(self, app, wid):
        super().__init__(app, wid)
        self.img = None
        self.frames, self.delays, self.frame = 1, [0.1], 0
        self.next_frame = 0.0
        self.load()
        self.build()

    def label(self):
        return f"{T('w_image')}: {self.wcfg.get('name', '')[:18]}"

    def load(self):
        if self.img:
            gdiplus.GdipDisposeImage(self.img)
            self.img = None
        ensure_gdiplus()
        path = self.wcfg.get("path")
        img = ctypes.c_void_p()
        if not path or not os.path.exists(path) or gdiplus.GdipLoadImageFromFile(path, ctypes.byref(img)):
            self.iw, self.ih = 4, 3
            return
        self.img = img
        w, h = ctypes.c_uint(), ctypes.c_uint()
        gdiplus.GdipGetImageWidth(img, ctypes.byref(w))
        gdiplus.GdipGetImageHeight(img, ctypes.byref(h))
        self.iw, self.ih = max(1, w.value), max(1, h.value)
        count = ctypes.c_uint(1)
        gdiplus.GdipImageGetFrameCount(img, ctypes.byref(FRAME_DIMENSION_TIME), ctypes.byref(count))
        self.frames = max(1, count.value)
        self.delays = [0.1] * self.frames
        if self.frames > 1:                                      # animated GIF: read the frame delays
            size = ctypes.c_uint()
            if not gdiplus.GdipGetPropertyItemSize(img, 0x5100, ctypes.byref(size)) and size.value:
                buf = ctypes.create_string_buffer(size.value)
                if not gdiplus.GdipGetPropertyItem(img, 0x5100, size.value, buf):
                    item = ctypes.cast(buf, ctypes.POINTER(PropertyItem)).contents
                    n = min(self.frames, item.length // 4)
                    raw = (ctypes.c_long * n).from_address(item.value)
                    self.delays = [max(0.02, raw[i] / 100.0) for i in range(n)] + [0.1] * (self.frames - n)

    def build(self):
        u = self.u
        h = 5 * u
        w = h * self.iw / self.ih
        self.make_canvas(w, h)
        self.place()
        self.refresh()

    def draw(self, c, now):
        if self.img:
            c.image(self.img, 0, 0, self.w, self.h)
        else:
            c.round_rect(0.5, 0.5, self.w - 1, self.h - 1, 8, fill=argb((0.1, 0.1, 0.12), 0.8))
            c.text(T("image_missing"), 0, 0, self.w, self.h, 0.4 * self.u, argb(WHITE))

    def tick(self, now):
        if self.shown and self.img and self.frames > 1 and now >= self.next_frame:
            self.frame = (self.frame + 1) % self.frames
            gdiplus.GdipImageSelectActiveFrame(self.img, ctypes.byref(FRAME_DIMENSION_TIME), self.frame)
            self.next_frame = now + self.delays[self.frame]
            self.redraw()

    def menu_items(self, add, checked):
        add(0, 750, T("change_image"))

    def menu_command(self, cmd):
        if cmd == 750:
            if self.app.choose_image(self.wcfg):
                self.load()
                self.build()
            return True
        return False

    def close(self):
        if self.img:
            gdiplus.GdipDisposeImage(self.img)
        super().close()


class HintWidget(ArgbWidget):
    """Banner at the top of the screen while widgets are being moved."""
    kind = "hint"

    def __init__(self, app):
        super().__init__(app, None)
        d = app.dpi
        probe = Canvas(1, 1)
        _, _, w, _ = probe.measure(T("edit_hint"), 15 * d)
        probe.close()
        self.make_canvas(w + 40 * d, 44 * d)
        area = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), (area.left + area.right - self.w) // 2,
                            area.top + int(12 * d), self.w, self.h, 0x10)

    @property
    def wcfg(self):
        return {"visible": True, "size": 26}

    def has_content(self):
        return self.app.editing

    def refresh(self):
        want = self.app.editing
        if want != self.shown:
            self.shown = want
            user32.ShowWindow(self.hwnd, 4 if want else 0)
        if want:
            self.redraw()

    def redraw(self):
        c, d = self.canvas, self.app.dpi
        c.clear()
        c.round_rect(1, 1, self.w - 2, self.h - 2, 12 * d, fill=argb((0.04, 0.04, 0.07), 0.92),
                     stroke=argb(ACCENT), width=2 * d)
        c.text(T("edit_hint"), 0, 0, self.w, self.h, 15 * d, argb(WHITE))
        c.present(self.hwnd, 1.0)


WIDGET_CLASSES = {"player": PlayerWidget, "stats": StatsWidget, "cat": CatWidget, "text": TextWidget,
                  "image": ImageWidget, "session": SessionWidget, "rocket": RocketWidget, "dash": DashWidget,
                  "scoreboard": ScoreboardWidget, "boost": BoostWidget,
                  "clock": ClockWidget, "chests": ChestWidget, "fnmatch": FnMatchWidget, "drink": DrinkWidget}


GD_JUMP_KEYS = {M("L"), S(0x39), E(0x48), S(0x11)}         # click, Space, Up arrow, W


# ---------------------------------------------------------------- the app: tray icon, menus, shortcuts, editing
class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dpi = user32.GetDpiForSystem() / 96.0
        self.editing = False
        self.capturing = None
        self.obs_dibs = {}
        self.obs_last = 0.0
        self.web_messages = None
        self.chest_capture = None
        self.fn_checks, self.fn_scan_at = [], 0.0
        self.bind_ok = {}
        self.held = set()
        self.ticks = 0
        self.last_save = time.monotonic()
        self.stats = Stats(cfg)
        media_options["mute_ads"] = cfg["mute_ads"]
        self.session_start = time.monotonic()
        self.session_paused_at = None
        self.running = running_programs()
        self.game_since = {}
        self.gd_seconds = 0.0
        self.fs_game, self.fs_warned = None, set()
        self.last_scan = time.monotonic()
        self.note_games(self.last_scan)

        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self.wndproc)
        self.class_name = f"KeyboardSoundApp{id(self)}"
        wc = WNDCLASSW(0, self._wndproc, 0, 0, hinst, None, None, None, None, self.class_name)
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0x80, self.class_name, "Keyboard Sound", 0x80000000, 0, 0, 0, 0,
                                           None, None, hinst, None)
        self.widgets = {}
        order = sorted(cfg["widgets"], key=lambda k: WIDGET_TYPES.index(cfg["widgets"][k]["type"]))
        for wid in order:
            self.create_widget(wid)
        self.hint = HintWidget(self)
        self.register_binds()
        user32.RegisterHotKey(self.hwnd, 1100, 3, 0x21)          # Ctrl+Alt+PageUp   = clicks louder
        user32.RegisterHotKey(self.hwnd, 1101, 3, 0x22)          # Ctrl+Alt+PageDown = clicks quieter
        user32.SetTimer(self.hwnd, 1, int(1000 / FPS), None)
        self.add_tray_icon()
        if not cfg["welcomed"]:
            self.balloon(T("running_title"), T("running_text"))
            cfg["welcomed"] = True
            save_settings(cfg)

    # ---------- widgets
    def create_widget(self, wid):
        kind = self.cfg["widgets"][wid]["type"]
        if kind in ("keyboard", "mouse"):
            widget = KeysWidget(self, wid, kind)
        else:
            widget = WIDGET_CLASSES[kind](self, wid)
        self.widgets[wid] = widget
        if self.editing:
            widget.set_editing(True)
        return widget

    def theme_for(self, widget):
        """The widget's own theme / font if it has one, otherwise the main ones."""
        c = widget.wcfg if widget.id else {}
        name = c.get("theme") if c.get("theme") in THEME_IDS else self.cfg.get("theme", "classic")
        font = c.get("wfont") or self.cfg.get("font") or "Segoe UI"
        if not font_installed(font):
            font = "Segoe UI"
        key = (name, font, json.dumps(self.cfg.get("custom_theme"), sort_keys=True) if name == "custom" else "")
        cache = self.__dict__.setdefault("_themes", {})
        if key not in cache:
            cache[key] = Theme(theme_spec(name, self.cfg), font)
        return cache[key]

    def widgets_of(self, kind):
        return [w for w in self.widgets.values() if w.kind == kind]

    @property
    def player(self):
        return next((w for w in self.widgets.values() if w.kind == "player"), None)

    def auto_place(self, widget):
        """Where a widget goes the first time: next to the others, near the corner chosen in settings."""
        area = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)
        gap = int(10 * self.dpi)
        others = {w.kind: w.rect() for w in self.widgets.values() if w is not widget}
        kb = others.get("keyboard")
        kind = widget.kind
        if kind == "scoreboard":                  # top center, over the game's own scoreboard
            screen_w = user32.GetSystemMetrics(0)
            return (screen_w - widget.w) // 2, 0
        if kind == "boost":                       # bottom right, over the game's boost meter
            sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            return int(sw * 0.909 - widget.w / 2), int(sh * 0.852 - widget.h / 2)
        if kind == "keyboard" or kb is None:
            corner = self.cfg.get("corner", "bottom-left")
            m = int(20 * self.dpi)
            x = area.left + m if "left" in corner else area.right - widget.w - m
            y = area.top + m if "top" in corner else area.bottom - widget.h - m
            if kind != "keyboard":
                x, y = (area.left + area.right - widget.w) // 2, (area.top + area.bottom - widget.h) // 2
            return x, y
        above = kb.top - widget.h - gap >= area.top
        if kind == "mouse":
            x = kb.right + gap if kb.right + gap + widget.w <= area.right else kb.left - gap - widget.w
            return x, kb.bottom - widget.h
        if kind == "player":
            return kb.left, (kb.top - widget.h - gap) if above else kb.bottom + gap
        if kind == "stats":
            pl = others.get("player")
            if pl:
                return pl.right + gap, pl.bottom - widget.h
            return kb.left, kb.top - widget.h - gap
        if kind == "cat":
            ms = others.get("mouse", kb)
            if ms.right + gap + widget.w <= area.right:
                return ms.right + gap, kb.bottom - widget.h
            return ms.left, ms.top - widget.h - gap
        return self.free_spot(widget, area, gap)

    def free_spot(self, widget, area, gap):
        """First place (from the bottom left, row by row upwards) that doesn't cover another widget."""
        taken = []
        for w in self.widgets.values():
            if w is widget or not w.wcfg.get("visible", True) or w.wcfg.get("x") is None:
                continue
            r = w.rect()
            taken.append((r.left - gap, r.top - gap, r.right + gap, r.bottom + gap))
        m, step = int(20 * self.dpi), max(8, int(16 * self.dpi))
        y = area.bottom - widget.h - m
        while y >= area.top + m:
            x = area.left + m
            while x + widget.w <= area.right - m:
                hit = next((t for t in taken if x < t[2] and x + widget.w > t[0] and y < t[3] and y + widget.h > t[1]),
                           None)
                if hit is None:
                    return x, y
                x = max(x + step, hit[2])
            y -= step
        return (area.left + area.right - widget.w) // 2, (area.top + area.bottom - widget.h) // 3

    def refresh_all(self):
        for w in self.widgets.values():
            w.refresh()

    def reset_layout(self, corner=None):
        if corner:
            self.cfg["corner"] = corner
        for wid in self.cfg["widgets"]:
            self.cfg["widgets"][wid]["x"] = self.cfg["widgets"][wid]["y"] = None
        order = sorted(self.widgets, key=lambda k: WIDGET_TYPES.index(self.widgets[k].kind))
        for wid in order:                     # one after another: each goes next to the ones placed before
            self.widgets[wid].place()
        save_settings(self.cfg)
        self.refresh_all()

    def add_widget(self, kind):
        n = 1
        while f"{kind}{n}" in self.cfg["widgets"]:
            n += 1
        wid = f"{kind}{n}"
        wcfg = {"type": kind, "x": None, "y": None, "size": 26, "visible": True}
        if kind == "text":
            text = TextDialog.ask(T("w_text"), T("text_prompt"), T("default_text"))
            if not text or not text.strip():
                return
            wcfg.update(text=text, color="effect", bold=True, background=False)
        elif kind == "image":
            wcfg["size"] = 30
            if not self.choose_image(wcfg, wid):
                return
        self.cfg["widgets"][wid] = wcfg
        self.cfg["visible"] = True
        self.create_widget(wid)
        save_settings(self.cfg)
        if not self.editing:                  # straight into moving mode, so the new widget can be dragged
            self.toggle_edit()

    def choose_image(self, wcfg, wid=None):
        path = pick_file(T("image_title"), T("image_filter"), "*.png;*.jpg;*.jpeg;*.gif;*.bmp")
        if not path:
            return False
        try:
            folder = os.path.join(DATA_DIR, "images")
            os.makedirs(folder, exist_ok=True)
            wid = wid or next(k for k, v in self.cfg["widgets"].items() if v is wcfg)
            ext = os.path.splitext(path)[1].lower()
            target = os.path.join(folder, f"{wid}{ext}")
            old = wcfg.get("path")
            if old and os.path.exists(old) and os.path.abspath(old) != os.path.abspath(target):
                os.remove(old)
            if os.path.abspath(path) != os.path.abspath(target):
                shutil.copyfile(path, target)
        except OSError as e:
            user32.MessageBoxW(None, str(e), APP_NAME, 0x30)
            return False
        wcfg["path"], wcfg["name"] = target, os.path.basename(path)
        return True

    def remove_widget(self, widget):
        widget.close()
        self.widgets.pop(widget.id, None)
        path = self.cfg["widgets"].pop(widget.id, {}).get("path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        save_settings(self.cfg)


    # ---------- main menu (web page in its own window)
    def web_call(self, action, data):
        self.web_messages = []
        try:
            self.web_action(action, data or {})
            result = {"state": self.web_state()}
        except Exception as e:
            result = {"state": self.web_state(), "msg": f"⚠ {e}"}
        finally:
            msgs, self.web_messages = self.web_messages, None
        if msgs and "msg" not in result:
            result["msg"] = "\n\n".join(msgs)
        return result

    def web_state(self):
        cfg = self.cfg
        widgets = []
        for wid, w in self.widgets.items():
            c = w.wcfg
            item = {"id": wid, "kind": w.kind, "label": w.label(), "visible": bool(c.get("visible", True)),
                    "size": c.get("size", 26), "removable": w.kind in ("text", "image"),
                    "game": w.kind in ("rocket", "dash", "scoreboard", "boost")}
            if isinstance(w, KeysWidget):
                item.update(style=w.style, numpad=c.get("numpad", True), background=bool(c.get("background")))
            elif w.kind == "cat":
                item["fur"] = c.get("fur", "orange")
            elif w.kind == "clock":
                left = w.timer_left()
                item.update(analog=bool(c.get("analog")), seconds=c.get("seconds", True),
                            timer=c.get("timer_len") if left is not None else 0)
            elif w.kind == "text":
                names = [n for n in COLORS if n not in ("rainbow", "white")]
                item.update(text=c.get("text", ""), color=c.get("color", "effect"), bold=c.get("bold", True),
                            background=bool(c.get("background")),
                            colors=[{"id": "effect", "name": T("tc_effect")}, {"id": "white", "name": T("c_white")}]
                            + [{"id": n, "name": T("c_" + n)} for n in names])
            widgets.append(item)
        widgets.sort(key=lambda x: WIDGET_TYPES.index(x["kind"]))
        for item in widgets:                             # every widget can have its own theme and font
            w = self.widgets[item["id"]]
            item.update(themeable=w.kind not in ("scoreboard", "boost", "image"), theme=w.wcfg.get("theme") or "",
                        wfont=w.wcfg.get("wfont") or "")
            if w.kind == "drink":
                item.update(interval=w.interval, only_games=bool(w.wcfg.get("only_games")))
            elif w.kind == "chests":
                item.update(auto=w.auto, ocr=OCR_OK, key=key_label(w.key), prompt=w.prompt)
        sb = next((w for w in self.widgets.values() if w.kind == "scoreboard"), None)
        sbc = sb.wcfg if sb else {}
        styles = {w.style for w in self.widgets.values() if isinstance(w, KeysWidget)}
        binds = self.binds()
        fonts = installed_fonts()
        st = self.stats.data
        def css(rgb, a=1.0):
            return f"rgba({int(rgb[0] * 255)},{int(rgb[1] * 255)},{int(rgb[2] * 255)},{a})"

        def theme_card(tid):
            sp = theme_spec(tid, cfg)
            b, n = sp.get("border"), sp.get("nums")
            border = {"glow": css(self.glow_color(0.1, 0)), "rainbow": "#ff4dc4", "pastel": "#c9b6ff", "neon": "#33ffff"}.get(b) \
                if isinstance(b, str) else (css(b) if b else None)
            if isinstance(n, list):
                nums = f"linear-gradient(90deg,{css(n[0])},{css(n[-1])})"
            elif n == "pastel":
                nums = "linear-gradient(90deg,#d88fd0,#8fb8e8,#8fd8a8)"
            elif n == "rainbow":
                nums = "linear-gradient(90deg,#ff4d4d,#ffd24d,#4dff88,#4dc3ff,#b04dff)"
            else:
                nums = "linear-gradient(90deg," + ",".join(css(self.glow_color(i / 4, 0)) for i in range(5)) + ")"
            return {"id": tid, "name": T("th_" + tid), "bg": list(sp["bg"]) if sp.get("bg") else None,
                    "bg_a": sp.get("bg_a", 0), "border_css": border, "text_css": css(sp["text"]), "nums_css": nums,
                    "radius": sp.get("radius", 0.45)}
        return {
            "theme": {"current": cfg.get("theme", "classic"), "font": cfg.get("font", "Segoe UI"),
                      "list": [theme_card(t) for t in THEME_IDS],
                      "custom": {**DEFAULTS["custom_theme"], **(cfg.get("custom_theme") or {})}},
            "fonts": {"rec": [f for f in SB_FONTS if f not in SB_HANDWRITING and font_installed(f)],
                      "hand": [f for f in SB_FONTS if f in SB_HANDWRITING and font_installed(f)], "all": fonts},
            "lang": LANG, "visible": cfg["visible"], "editing": self.editing, "frozen": FROZEN,
            "autostart": autostart_enabled() if FROZEN else False,
            "sound": {"on": cfg["sound"], "choice": cfg["sound_choice"], "volume": cfg["volume"],
                      "mouse": cfg["mouse_sound"], "sounds": [{"id": k, "name": n} for k, n in SOUNDS],
                      "custom_name": (os.path.basename(cfg.get("custom_name") or cfg.get("custom_sound") or "")
                                      if cfg.get("custom_sound") and os.path.exists(cfg["custom_sound"]) else "")},
            "look": {"effect": cfg["effect"], "effects": [{"id": e, "name": T("fx_" + e)} for e in EFFECTS],
                     "color": cfg["color"], "opacity": cfg["opacity"],
                     "colors": [{"id": n, "name": T("c_" + n), "rgb": hex_color(rgb) if rgb else None}
                                for n, rgb in COLORS.items()],
                     "key_style": styles.pop() if len(styles) == 1 else "",
                     "styles": [{"id": k, "name": T("st_" + k)} for k in KEY_STYLES]},

            "furs": [{"id": f, "name": T("fur_" + f)} for f in FURS],
            "corners": [{"id": c, "name": T(c)} for c in ("bottom-left", "bottom-right", "top-left", "top-right")],
            "widgets": widgets,
            "rl": {"mode": sbc.get("mode", "enhanced"), "modes": [{"id": m, "name": T("sb_" + m)} for m in SB_MODES],
                   "font": sbc.get("font") or "Bahnschrift", "bold": sbc.get("bold", True),
                   "names": sbc.get("names", True), "opacity": float(sbc.get("opacity", 1.0)),
                   "fonts_rec": [f for f in SB_FONTS if f not in SB_HANDWRITING and font_installed(f)],
                   "fonts_hand": [f for f in SB_FONTS if f in SB_HANDWRITING and font_installed(f)],
                   "fonts_all": fonts, "rate": self.rl_live_rate(), "live_on": self.rl_live_on(),
                   "borderless": self.rl_borderless_on()},
            "games": {"fn_windowed": fn_windowed_on()},
            "music": {"available": MEDIA_OK, "mute_ads": cfg["mute_ads"], "seek_step": cfg["seek_step"],
                      "music_step": cfg["music_step"], "capturing": self.capturing,
                      "binds": [{"act": a, "label": T("ma_" + a), "text": bind_text(*binds[a])} for a in MEDIA_ACTIONS]},
            "obs": {"on": obs_enabled, "url": f"http://127.0.0.1:{web_port}/all",
                    "w": user32.GetSystemMetrics(0), "h": user32.GetSystemMetrics(1)},
            "stats": {"keys": st.get("keys", 0), "clicks": st.get("clicks", 0), "best_cps": st.get("best_cps", 0),
                      "cat": st.get("cat", 0)},
        }

    def web_action(self, a, d):
        global sound_enabled, mouse_sound_enabled
        cfg = self.cfg
        v = d.get("v")
        if a == "state":
            return
        if a == "set":
            k = d.get("k")
            if k == "sound":
                cfg["sound"] = sound_enabled = bool(v)
            elif k == "mouse_sound":
                cfg["mouse_sound"] = mouse_sound_enabled = bool(v)
            elif k == "volume":
                cfg["volume"] = max(0.1, min(2.0, float(v)))
                self.set_sound(cfg["sound_choice"])
            elif k == "effect" and v in EFFECTS:
                cfg["effect"] = v
                self.effect_changed()
            elif k == "color" and v in COLORS:
                cfg["color"] = v
                self.effect_changed()
            elif k == "opacity":
                cfg["opacity"] = max(0.3, min(1.0, float(v)))
                for x in self.widgets.values():
                    if not x.argb:
                        user32.SetLayeredWindowAttributes(x.hwnd, TRANSPARENT_KEY, int(cfg["opacity"] * 255), 3)
                    x.refresh()
            elif k == "visible":
                cfg["visible"] = bool(v)
                self.refresh_all()
            elif k == "mute_ads":
                cfg["mute_ads"] = media_options["mute_ads"] = bool(v)
            elif k == "seek_step" and int(v) in (5, 10, 15, 30):
                cfg["seek_step"] = int(v)
            elif k == "music_step" and int(v) in (2, 5, 10):
                cfg["music_step"] = int(v)
            save_settings(cfg)
        elif a == "sound_choice":
            if v in dict(SOUNDS) or (v == "custom" and cfg.get("custom_sound") and os.path.exists(cfg["custom_sound"])):
                self.set_sound(v)
        elif a == "test_sound":
            play_requests.put(1)
        elif a == "upload_sound":
            name = os.path.basename(str(d.get("name") or "sound.wav"))
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".wma", ".aac"):
                self.say(T("sound_error").format(name))
                return
            os.makedirs(DATA_DIR, exist_ok=True)
            target = os.path.join(DATA_DIR, "custom_sound" + ext)
            for old in os.listdir(DATA_DIR):
                if old.startswith("custom_sound") and os.path.join(DATA_DIR, old) != target:
                    os.remove(os.path.join(DATA_DIR, old))
            with open(target, "wb") as f:
                f.write(__import__("base64").b64decode(d.get("data") or ""))
            cfg["custom_sound"], cfg["custom_name"] = target, name
            self.set_sound("custom")
            self.say(T("sound_saved").format(name), 0x40)
        elif a == "theme" and v in THEME_IDS:
            cfg["theme"] = v
            self.theme_changed()
        elif a == "font" and v:
            cfg["font"] = str(v)[:64]
            self.theme_changed()
        elif a == "custom":
            k = d.get("k")
            ct = {**DEFAULTS["custom_theme"], **(cfg.get("custom_theme") or {})}
            if k in ("bg", "text", "muted") and str(v).startswith("#"):
                ct[k] = str(v)[:7]
            elif k == "border" and (str(v).startswith("#") or v in ("glow", "rainbow", "pastel", "neon", "none")):
                ct[k] = str(v)[:7] if str(v).startswith("#") else v
            elif k == "nums" and (str(v).startswith("#") or v in ("effect", "rainbow", "pastel")):
                ct[k] = str(v)[:7] if str(v).startswith("#") else v
            elif k == "bg_a":
                ct[k] = max(0.0, min(1.0, float(v)))
            elif k == "radius":
                ct[k] = max(0.0, min(1.2, float(v)))
            cfg["custom_theme"] = ct
            cfg["theme"] = "custom"                       # editing it shows it
            self.theme_changed()
        elif a == "key_style" and v in KEY_STYLES:
            for w in self.widgets.values():
                if isinstance(w, KeysWidget):
                    w.wcfg["style"] = v
                    w.build()
            save_settings(cfg)
        elif a == "edit":
            self.toggle_edit()
        elif a in ("w", "wcmd", "home", "remove", "image"):
            w = self.widgets.get(d.get("id"))
            if not w:
                return
            if a == "w":
                self.web_widget(w, d.get("k"), v)
            elif a == "wcmd" and d.get("cmd") in (710, 711, 729, 762, 770, 771, 780, 850, 851, 852, 853, 854,
                                                  880, 888, 890):
                w.menu_command(int(d["cmd"]))
            elif a == "home":
                w.wcfg["x"] = w.wcfg["y"] = None
                w.place()
                w.refresh()
            elif a == "remove" and w.kind in ("text", "image"):
                self.remove_widget(w)
            elif a == "image" and w.kind == "image":
                if self.save_image(w.wcfg, w.id, d.get("name"), d.get("data")):
                    w.load()
                    w.build()
            save_settings(cfg)
        elif a == "home_all":
            self.reset_layout()
        elif a == "arrange" and v in ("bottom-left", "bottom-right", "top-left", "top-right"):
            self.reset_layout(v)
        elif a == "add_text":
            text = str(d.get("text") or "").strip()
            if text:
                self.new_widget("text", {"text": text, "color": "effect", "bold": True, "background": False})
        elif a == "add_image":
            wid = self.next_id("image")
            wcfg = {"type": "image", "x": None, "y": None, "size": 30, "visible": True}
            if self.save_image(wcfg, wid, d.get("name"), d.get("data")):
                self.new_widget("image", wcfg, wid)
        elif a == "rl":
            sb = next((w for w in self.widgets.values() if w.kind == "scoreboard"), None)
            k = d.get("k")
            if sb:
                if k == "mode" and v in SB_MODES:
                    sb.wcfg["mode"] = v
                elif k == "font" and v:
                    sb.wcfg["font"] = str(v)[:64]
                elif k in ("bold", "names"):
                    sb.wcfg[k] = bool(v)
                elif k == "opacity":
                    sb.wcfg["opacity"] = max(0.3, min(1.0, float(v)))
                sb.redraw()
                sb.sync_style()
                save_settings(cfg)
        elif a == "rl_live":
            self.games_command(560)
        elif a == "rl_border":
            self.games_command(561)
        elif a == "fn_window":
            self.fortnite_window_command()
        elif a == "bind" and d.get("act") in MEDIA_ACTIONS:
            self.start_capture(d["act"])
        elif a == "bind_reset":
            cfg["binds"] = copy.deepcopy(DEFAULTS["binds"])
            save_settings(cfg)
            self.register_binds()
        elif a == "obs":
            self.obs_toggle(bool(v))
        elif a == "autostart" and FROZEN:
            set_autostart(bool(v))
        elif a == "quit":
            user32.PostQuitMessage(0)

    def web_widget(self, w, k, v):
        c = w.wcfg
        if k == "visible":
            c["visible"] = bool(v)
            if v:
                self.cfg["visible"] = True
            self.refresh_all()
            return
        if k == "size":
            w.remember_position()
            c["size"] = max(10, min(120, int(v)))
        elif k == "style" and isinstance(w, KeysWidget) and v in KEY_STYLES:
            c["style"] = v
        elif k in ("numpad", "background", "analog", "seconds", "bold") and isinstance(v, bool):
            c[k] = v
        elif k == "fur" and v in FURS:
            c["fur"] = v
        elif k == "theme":
            c["theme"] = v if v in THEME_IDS else None
            self.__dict__.pop("_themes", None)
        elif k == "wfont":
            c["wfont"] = str(v)[:64] if v else None
            self.__dict__.pop("_themes", None)
        elif k == "interval" and w.kind == "drink" and int(v) in DrinkWidget.INTERVALS:
            c["interval"] = int(v)
            w.left = min(w.left, int(v) * 60.0)
        elif k == "only_games" and w.kind == "drink":
            c["only_games"] = bool(v)
        elif k == "auto" and w.kind == "chests":
            c["auto"] = bool(v)
        elif k == "timer" and w.kind == "clock":
            minutes = int(v or 0)
            if minutes:
                c.update(timer_len=minutes, timer_end=time.time() + minutes * 60)
                w.done_at = None
            else:
                c["timer_end"] = None
        elif k == "text" and w.kind == "text" and str(v).strip():
            c["text"] = str(v)
        elif k == "color" and w.kind == "text":
            c["color"] = str(v)
        else:
            return
        if w.kind in ("cat", "clock"):
            w.redraw()
        else:
            w.build()

    def next_id(self, kind):
        n = 1
        while f"{kind}{n}" in self.cfg["widgets"]:
            n += 1
        return f"{kind}{n}"

    def new_widget(self, kind, extra, wid=None):
        wid = wid or self.next_id(kind)
        wcfg = {"type": kind, "x": None, "y": None, "size": 26, "visible": True, **extra}
        self.cfg["widgets"][wid] = wcfg
        self.cfg["visible"] = True
        self.create_widget(wid)
        save_settings(self.cfg)

    def save_image(self, wcfg, wid, name, data):
        name = os.path.basename(str(name or "image.png"))
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".bmp") or not data:
            self.say(T("image_missing"))
            return False
        folder = os.path.join(DATA_DIR, "images")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, f"{wid}{ext}")
        old = wcfg.get("path")
        if old and os.path.exists(old) and os.path.abspath(old) != os.path.abspath(target):
            os.remove(old)
        with open(target, "wb") as f:
            f.write(__import__("base64").b64decode(data))
        wcfg["path"], wcfg["name"] = target, name
        return True

    # ---------- Fortnite chests (screen reading)
    def fn_read_prompt(self, kind):
        x, y, w, h = prompt_area()
        try:
            fn_ocr_requests.put_nowait((time.monotonic(), kind, w, h, grab_screen(x, y, w, h)))
        except queue.Full:
            pass

    def fn_screen(self, now):
        """While Fortnite is in front: look at the prompt 2.5x a second, and right after E is pressed / let go."""
        chest = self.widgets_of("chests")
        if not (chest and chest[0].auto and OCR_THREAD_STARTED and chest[0].wcfg.get("visible", True)):
            return
        if self.fn_checks and now >= self.fn_checks[0]:
            self.fn_checks.pop(0)
            if foreground_program() == FORTNITE_EXE:
                self.fn_read_prompt("check")
        elif now >= self.fn_scan_at:
            self.fn_scan_at = now + 0.4
            if FORTNITE_EXE in self.running and foreground_program() == FORTNITE_EXE and fn_ocr_requests.empty():
                self.fn_read_prompt("scan")

    # ---------- OBS
    def obs_capture(self, now):
        """Hand the current pictures of the widgets an OBS page is showing to the web server."""
        items = []
        for wid, w in self.widgets.items():
            r = w.rect()
            items.append({"id": wid, "label": w.label(), "kind": w.kind, "x": r.left, "y": r.top, "w": w.w, "h": w.h,
                          "shown": bool(w.shown)})
        with obs_lock:
            obs_layout["screen"] = [user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)]
            obs_layout["items"] = items
            wanted = [wid for wid, t in obs_wanted.items() if now - t < 3.0]
        for wid in wanted:
            w = self.widgets.get(wid)
            if not w or not w.shown:
                continue
            if w.argb:
                fmt, data = FMT_PARGB, ctypes.string_at(w.canvas.bits, w.w * w.h * 4)
            else:
                dib = self.obs_dibs.get(wid)
                if not dib or dib[0] != (w.w, w.h):
                    if dib:
                        gdi32.DeleteObject(dib[2])
                        gdi32.DeleteDC(dib[1])
                    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w.w, -w.h, 1, 32, 0, 0, 0, 0, 0, 0)
                    bits = ctypes.c_void_p()
                    dc = gdi32.CreateCompatibleDC(w.screen_dc)
                    bmp = gdi32.CreateDIBSection(w.screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
                    gdi32.SelectObject(dc, bmp)
                    dib = self.obs_dibs[wid] = ((w.w, w.h), dc, bmp, bits)
                gdi32.BitBlt(dib[1], 0, 0, w.w, w.h, w.buf_dc, 0, 0, SRCCOPY)
                fmt, data = FMT_RGB, ctypes.string_at(dib[3], w.w * w.h * 4)
            with obs_lock:
                old = obs_frames.get(wid)
                if not old or old[3] != data:
                    obs_frames[wid] = (fmt, w.w, w.h, data, (old[4] + 1) if old else 1)

    def obs_toggle(self, on=None):
        global obs_enabled
        on = (not obs_enabled) if on is None else bool(on)
        if on and not web_start():
            self.say(T("obs_fail").format(OBS_PORT))
            on = False
        obs_enabled = self.cfg["obs"] = on
        save_settings(self.cfg)

    # ---------- games
    def session_seconds(self, now):
        return (self.session_paused_at or now) - self.session_start

    def note_games(self, now):
        for exe in KNOWN_GAMES:
            if exe in self.running:
                self.game_since.setdefault(exe, now)
            else:
                self.game_since.pop(exe, None)
        for exe in self.running:
            if is_geometry_dash(exe):
                self.game_since.setdefault(exe, now)
        for exe in list(self.game_since):
            if exe not in self.running:
                del self.game_since[exe]

    def current_game(self):
        if not self.game_since:
            return None
        fg = foreground_program()
        exe = fg if fg in self.game_since else min(self.game_since, key=self.game_since.get)
        name = KNOWN_GAMES.get(exe) or ("Geometry Dash" if is_geometry_dash(exe) else exe)
        return name, self.game_since[exe]

    def scan_games(self, now):
        """Every 2 seconds: which games run, Rocket League data on/off, exclusive fullscreen check."""
        elapsed, self.last_scan = now - self.last_scan, now
        self.running = running_programs()
        if any(is_geometry_dash(x) for x in self.running):
            self.gd_seconds += elapsed
        self.note_games(now)
        rocket = next((w for w in self.widgets.values() if w.kind == "rocket"), None)
        rl_widgets = [w for w in self.widgets.values() if w.kind in ("rocket",) + RL_STYLE_KINDS
                      and w.wcfg.get("visible", True)]
        want = bool(rl_widgets and self.cfg["visible"]
                    and ("rocketleague.exe" in self.running or (rocket and rocket.connected)))
        if want:
            rl_wanted.set()
        else:
            rl_wanted.clear()
        fn_widgets = [w for w in self.widgets.values() if w.kind in ("chests", "fnmatch") and w.wcfg.get("visible", True)]
        if fn_widgets and FORTNITE_EXE in self.running:
            fn_wanted.set()
        else:
            fn_wanted.clear()
        for w in self.widgets.values():
            if w.kind in ("rocket", "dash", "chests", "fnmatch"):
                w.refresh()
        state = ctypes.c_int(0)
        try:
            shell32.SHQueryUserNotificationState(ctypes.byref(state))
        except Exception:
            return
        if state.value == 3:                     # QUNS_RUNNING_D3D_FULL_SCREEN: nothing can be drawn over it
            fg = foreground_program()
            if fg:
                self.fs_game = fg
        elif self.fs_game:
            exe, self.fs_game = self.fs_game, None
            if exe not in self.fs_warned and self.cfg["visible"]:
                self.fs_warned.add(exe)
                if exe == "rocketleague.exe":
                    self.balloon(T("fs_title"), T("fs_text_rl"))
                elif exe == FORTNITE_EXE:
                    self.balloon(T("fs_title"), T("fs_text_fn"))
                else:
                    name = KNOWN_GAMES.get(exe) or os.path.splitext(exe)[0]
                    self.balloon(T("fs_title"), T("fs_text").format(name))

    def rl_live_rate(self):
        try:
            return int(float(read_ini(os.path.join(rl_config_dir(), RL_STATS_INI), RL_STATS_SECTION)
                             .get("PacketSendRate", 0)))
        except ValueError:
            return 0

    def rl_live_on(self):
        return self.rl_live_rate() >= 10

    def rl_borderless_on(self):
        v = read_ini(os.path.join(rl_config_dir(), RL_VIDEO_INI), "SystemSettings")
        return v.get("Borderless", "").lower() == "true" or v.get("Fullscreen", "").lower() == "false"

    def say(self, text, icon=0x30):
        """Message for the user: shown in the main menu when it asked, otherwise as a Windows message box."""
        if self.web_messages is not None:
            self.web_messages.append(text)
        else:
            user32.MessageBoxW(None, text, APP_NAME, icon)

    def fortnite_window_command(self):
        """Fortnite: exclusive fullscreen <-> windowed fullscreen (only while the game is closed)."""
        path = fn_settings_path()
        if not os.path.exists(path):
            self.say(T("fn_no_config"))
            return
        if FORTNITE_EXE in running_programs():
            self.say(T("fn_close_first"))
            return
        mode = "0" if fn_windowed_on() else "1"
        try:
            write_ini(path, FN_SETTINGS_SECTION, {"FullscreenMode": mode, "LastConfirmedFullscreenMode": mode,
                                                  "PreferredFullscreenMode": mode})
        except OSError as e:
            self.say(str(e))
            return
        if mode == "1":
            self.say(T("fn_windowed_done"), 0x40)

    def games_command(self, cmd):
        """560 = Rocket League live data on/off, 561 = borderless / exclusive fullscreen."""
        folder = rl_config_dir()
        if not os.path.isdir(folder):
            self.say(T("rl_no_config"))
            return
        if "rocketleague.exe" in running_programs():
            self.say(T("rl_close_first"))
            return
        try:
            if cmd == 560:
                on = not self.rl_live_on()
                path = os.path.join(folder, RL_STATS_INI)
                changes = {"PacketSendRate": 30 if on else 0}
                if not os.path.exists(path):
                    changes = {"Port": 49123, **changes}
                write_ini(path, RL_STATS_SECTION, changes)
                if on:
                    self.say(T("rl_live_done"), 0x40)
            elif cmd == 561:
                path = os.path.join(folder, RL_VIDEO_INI)
                if not os.path.exists(path):
                    self.say(T("rl_no_config"))
                    return
                on = not self.rl_borderless_on()
                write_ini(path, "SystemSettings", {"Fullscreen": "False" if on else "True",
                                                   "Borderless": "True" if on else "False"})
                if on:
                    self.say(T("rl_border_done"), 0x40)
        except OSError as e:
            self.say(str(e))

    # ---------- colors shared by all widgets
    def glow_color(self, xfrac, now):
        c = COLORS.get(self.cfg["color"])
        if c is None:
            moving = self.cfg["effect"] == "wave"
            return colorsys.hsv_to_rgb((xfrac * RGB_SPREAD - (now * RGB_SPEED if moving else 0)) % 1.0, 1.0, 1.0)
        return c

    def base_color(self, xfrac, now):
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
        return eff == "breathe" or (eff == "wave" and bool(RGB_SPEED))

    # ---------- main loop pieces
    def wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg in (WM_TIMER, WM_APP):
                self.tick(msg == WM_TIMER)
                return 0
            if msg == WM_HOTKEY and wparam in (1100, 1101):
                self.change_click_volume(1 if wparam == 1100 else -1)
                return 0
            if msg == WM_HOTKEY and 1000 <= wparam < 1000 + len(MEDIA_ACTIONS):
                self.media_action(MEDIA_ACTIONS[wparam - 1000])
                return 0
            if msg == WM_TRAY:
                if lparam & 0xFFFF == 0x0202:                    # click: the main menu window
                    open_main_menu()
                elif lparam & 0xFFFF == 0x0205:                  # right-click: the quick menu
                    self.show_tray_menu()
                return 0
            if msg == WM_TASKBARCREATED:
                self.add_tray_icon()
                return 0
        except Exception as e:
            print("app error:", e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def tick(self, from_timer):
        try:
            while True:
                kind, val = ui_events.get_nowait()
                if kind in ("down", "up", "tap"):
                    self.on_input(kind, val)
                elif kind == "media":
                    pl = self.player
                    if pl:
                        pl.set_state(val)
                elif kind == "fn":
                    for w in self.widgets.values():
                        if w.kind == "chests" and val[0] == "phase" and val[1] == "Warmup":
                            w.match_started()
                        elif w.kind == "fnmatch":
                            w.on_fn(*val)
                elif kind == "fn_ocr":
                    for w in self.widgets_of("chests"):
                        w.ocr_result(*val)
                elif kind == "web":
                    action, data, reply = val
                    reply.put(self.web_call(action, data))
                elif kind == "rl":
                    for w in list(self.widgets.values()):
                        if w.kind in ("rocket",) + RL_STYLE_KINDS:
                            w.on_rl(*val)
                elif kind == "sound_error":
                    self.sound_failed(val)
                elif kind == "wheel":
                    self.on_wheel(val)
                elif kind == "edit":
                    self.toggle_edit()
                elif kind == "quit":
                    user32.PostQuitMessage(0)
                    return
        except queue.Empty:
            pass
        now = time.monotonic()
        if self.cfg["visible"]:
            for w in list(self.widgets.values()):
                if w.shown:
                    w.tick(now)
        if from_timer:
            self.ticks += 1
            if self.ticks % FPS == 0:                            # once a second
                for w in self.widgets.values():
                    w.keep_on_top()
            if self.ticks % (2 * FPS) == 0:
                self.scan_games(now)
                if now - self.last_save > 30:                    # keep today's stats safe
                    self.last_save = now
                    save_settings(self.cfg)
        self.fn_screen(now)
        if obs_enabled and now - self.obs_last >= 1 / 15:
            self.obs_last = now
            self.obs_capture(now)

    def on_input(self, kind, kid):
        if kind == "down" and self.chest_capture:                 # choosing the chest key
            if kid == S(0x01):                                   # Esc = cancel
                self.chest_capture.set_key(None)
            elif kid not in (M("L"), M("R"), M("WU"), M("WD")) and kid not in MODIFIER_IDS:
                self.chest_capture.set_key(kid)
            return
        chest = self.widgets_of("chests")
        if chest and kind in ("down", "up") and kid in (chest[0].key, chest[0].interact) \
                and foreground_program() == FORTNITE_EXE:
            now = time.monotonic()
            if kind == "down" and kid == chest[0].key:
                chest[0].add(1)
            elif kid == chest[0].interact and chest[0].auto and OCR_THREAD_STARTED:
                if kind == "down":
                    chest[0].pressed_interact(now)
                    self.fn_read_prompt("press")
                    self.fn_checks = [now + 1.6]
                else:                                             # let go: look again while it opens
                    self.fn_checks = [now + 0.3, now + 0.8, now + 1.5, now + 2.5]
        if kind == "down":
            if self.capturing and kid not in MODIFIER_IDS and kid[0] != "m":
                self.finish_capture(kid)
            self.held.add(kid)
            self.stats.add(kid)
            if kid in GD_JUMP_KEYS and is_geometry_dash(foreground_program()):
                self.stats.data["gd_jumps"] = self.stats.data.get("gd_jumps", 0) + 1
                self.stats.data["gd_total"] = self.stats.data.get("gd_total", 0) + 1
                for w in self.widgets.values():
                    if w.kind == "dash":
                        w.jump()
        elif kind == "up":
            self.held.discard(kid)
        for w in self.widgets.values():
            w.on_input(kind, kid)

    def widget_under_cursor(self):
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        best = None
        for w in self.widgets.values():
            if not w.shown:
                continue
            r = w.rect()
            if r.left <= p.x < r.right and r.top <= p.y < r.bottom:
                area = (r.right - r.left) * (r.bottom - r.top)
                if best is None or area < best[0]:
                    best = (area, w)
        return best[1] if best else None

    def on_wheel(self, delta):
        w = self.widget_under_cursor()
        if not w:
            return
        if self.editing:
            self.resize_widget(w, 1 if delta > 0 else -1)
        elif w.kind == "player":                                 # scroll over the player = music volume
            step = self.cfg["music_step"] / 100 * (1 if delta > 0 else -1)
            media_commands.put(("vol_by", step))
            w.change_volume(step)

    def resize_widget(self, w, step):
        w.remember_position()
        w.wcfg["size"] = max(10, min(120, w.wcfg["size"] + 2 * step))
        w.build()
        save_settings(self.cfg)

    def toggle_edit(self):
        if not self.cfg["visible"]:
            self.cfg["visible"] = True
        self.editing = not self.editing
        for w in self.widgets.values():
            w.set_editing(self.editing)
        self.hint.refresh()
        if not self.editing:
            for w in self.widgets.values():
                w.remember_position()
            save_settings(self.cfg)

    # ---------- tray icon
    def tray_data(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID = self.hwnd, 1
        return nid

    def add_tray_icon(self):
        size = user32.GetSystemMetrics(49)
        self.icon = user32.LoadImageW(None, ICON_FILE, 1, size, size, 0x10) if os.path.exists(ICON_FILE) else None
        if not self.icon:
            self.icon = user32.LoadIconW(None, ctypes.c_void_p(32512))
        nid = self.tray_data()
        nid.uFlags = 0x1 | 0x2 | 0x4
        nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, self.icon, APP_NAME
        shell32.Shell_NotifyIconW(0, ctypes.byref(nid))

    def balloon(self, title, text):
        nid = self.tray_data()
        nid.uFlags, nid.szInfoTitle, nid.szInfo, nid.dwInfoFlags = 0x10, title, text, 0x1
        shell32.Shell_NotifyIconW(1, ctypes.byref(nid))

    def popup(self, menu):
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, 0x100 | 0x2, p.x, p.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        return cmd

    def show_tray_menu(self):
        global sound_enabled, mouse_sound_enabled
        cfg, add = self.cfg, user32.AppendMenuW
        menu, snd, vol, fx, col, wmenu, look, games, obs = (user32.CreatePopupMenu() for _ in range(9))
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
        volumes = CLICK_VOLUMES
        for i, v in enumerate(volumes):
            add(vol, checked(abs(cfg["volume"] - v) < 0.01), 220 + i, f"{int(v * 100)} %")
        add(snd, 0x10, vol, T("click_volume"))
        add(snd, checked(cfg["mouse_sound"]), 212, T("mouse_sound"))
        keys_widgets = [w for w in self.widgets.values() if isinstance(w, KeysWidget)]
        styles = {w.style for w in keys_widgets}
        for i, st in enumerate(KEY_STYLES):
            add(look, checked(styles == {st}), 450 + i, T("st_" + st))
        rate = self.rl_live_rate()
        add(games, checked(rate >= 10), 560, T("rl_live") + (T("rl_live_rate").format(rate) if 0 < rate < 10 else ""))
        add(games, checked(self.rl_borderless_on()), 561, T("rl_borderless"))
        add(games, 0x800, 0, None)
        add(games, checked(fn_windowed_on()), 563, T("fn_windowed"))
        for i, name in enumerate(EFFECTS):
            add(fx, checked(cfg["effect"] == name), 300 + i, T("fx_" + name))
        color_names = list(COLORS)
        for i, name in enumerate(color_names):
            add(col, checked(cfg["color"] == name), 400 + i, T("c_" + name))

        music, keys_menu, steps, vsteps = (user32.CreatePopupMenu() for _ in range(4))
        binds = self.binds()
        if MEDIA_OK:
            for i, act in enumerate(MEDIA_ACTIONS):
                label = T("ma_" + act)
                if act in ("fwd", "back"):
                    label += f" ({'+' if act == 'fwd' else '-'}{cfg['seek_step']} s)"
                if act in ("vol_up", "vol_down"):
                    label += f" ({'+' if act == 'vol_up' else '-'}{cfg['music_step']} %)"
                shortcut = bind_text(*binds[act]) + ("" if self.bind_ok.get(act, True) else f" ({T('bind_taken')})")
                add(music, 0, 510 + i, f"{label}\t{shortcut}")
                add(keys_menu, 0, 520 + i, f"{T('ma_' + act)}:  {bind_text(*binds[act])}...")
            add(keys_menu, 0x800, 0, None)
            add(keys_menu, 0, 530, T("reset_binds"))
            add(music, 0x800, 0, None)
            add(music, checked(cfg["mute_ads"]), 535, T("mute_ads"))
            add(music, 0x10, keys_menu, T("change_binds"))
            for i, v in enumerate((5, 10, 15, 30)):
                add(steps, checked(cfg["seek_step"] == v), 540 + i, f"{v} s")
            add(music, 0x10, steps, T("seek_step"))
            for i, v in enumerate((2, 5, 10)):
                add(vsteps, checked(cfg["music_step"] == v), 545 + i, f"{v} %")
            add(music, 0x10, vsteps, T("music_step"))
        else:
            add(music, 0x1, 0, T("media_missing"))

        wlist = list(self.widgets.values())
        for i, w in enumerate(wlist[:40]):
            add(wmenu, checked(w.wcfg.get("visible", True)), 600 + i, w.label())
        add(wmenu, 0x800, 0, None)
        add(wmenu, 0, 650, T("add_text"))
        add(wmenu, 0, 651, T("add_image"))
        add(wmenu, 0x800, 0, None)
        add(wmenu, 0, 664, T("home_all"))
        corners = ("bottom-left", "bottom-right", "top-left", "top-right")
        for i, c in enumerate(corners):
            add(wmenu, 0, 660 + i, f"{T('reset_layout')}: {T(c)}")

        add(menu, 0, 90, T("main_menu"))
        user32.SetMenuDefaultItem(menu, 90, 0)
        add(menu, 0x800, 0, None)
        add(menu, checked(self.editing), 101, T("move"))
        add(menu, checked(cfg["visible"]), 102, T("show"))
        add(menu, 0x10, wmenu, T("widgets"))
        add(menu, 0x800, 0, None)
        add(menu, 0x10, snd, T("sound"))
        add(menu, 0x10, fx, T("effect"))
        add(menu, 0x10, col, T("color"))
        add(menu, 0x10, look, T("style"))
        add(menu, 0x10, music, T("media"))
        add(menu, 0x10, games, T("games"))
        add(obs, checked(obs_enabled), 570, T("obs_on"))
        add(obs, 0 if obs_enabled else 0x1, 571, T("obs_copy"))
        add(obs, 0 if obs_enabled else 0x1, 572, T("obs_open"))
        add(menu, 0x10, obs, T("obs"))
        add(menu, 0x800, 0, None)
        if FROZEN:
            add(menu, checked(autostart_enabled()), 104, T("autostart"))
        add(menu, 0, 106, T("help"))
        add(menu, 0, 105, T("quit"))
        cmd = self.popup(menu)

        if cmd == 90:
            open_main_menu()
        elif cmd == 101:
            self.toggle_edit()
        elif cmd == 102:
            cfg["visible"] = not cfg["visible"]
            save_settings(cfg)
            self.refresh_all()
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
        elif cmd == 212:
            cfg["mouse_sound"] = mouse_sound_enabled = not cfg["mouse_sound"]
            save_settings(cfg)
        elif 450 <= cmd < 450 + len(KEY_STYLES):
            for w in keys_widgets:
                w.wcfg["style"] = KEY_STYLES[cmd - 450]
                w.build()
            save_settings(cfg)
        elif cmd in (560, 561):
            self.games_command(cmd)
            self.refresh_all()
        elif cmd == 563:
            self.fortnite_window_command()
        elif cmd == 570:
            self.obs_toggle()
        elif cmd == 571:
            url = f"http://127.0.0.1:{web_port}/all"
            set_clipboard(url)
            user32.MessageBoxW(None, T("obs_copied").format(url, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)),
                               APP_NAME, 0x40)
        elif cmd == 572:
            os.startfile(f"http://127.0.0.1:{web_port}/")
        elif 220 <= cmd < 220 + len(volumes):
            cfg["volume"] = volumes[cmd - 220]
            self.set_sound(cfg["sound_choice"])
        elif 300 <= cmd < 300 + len(EFFECTS):
            cfg["effect"] = EFFECTS[cmd - 300]
            self.effect_changed()
        elif 400 <= cmd < 400 + len(color_names):
            cfg["color"] = color_names[cmd - 400]
            self.effect_changed()
        elif 510 <= cmd < 510 + len(MEDIA_ACTIONS):
            self.media_action(MEDIA_ACTIONS[cmd - 510])
        elif 520 <= cmd < 520 + len(MEDIA_ACTIONS):
            self.start_capture(MEDIA_ACTIONS[cmd - 520])
        elif cmd == 530:
            cfg["binds"] = copy.deepcopy(DEFAULTS["binds"])
            save_settings(cfg)
            self.register_binds()
        elif cmd == 535:
            cfg["mute_ads"] = media_options["mute_ads"] = not cfg["mute_ads"]
            save_settings(cfg)
        elif 540 <= cmd < 544:
            cfg["seek_step"] = (5, 10, 15, 30)[cmd - 540]
            save_settings(cfg)
        elif 545 <= cmd < 548:
            cfg["music_step"] = (2, 5, 10)[cmd - 545]
            save_settings(cfg)
        elif 600 <= cmd < 600 + len(wlist):
            w = wlist[cmd - 600]
            w.wcfg["visible"] = not w.wcfg.get("visible", True)
            if w.wcfg["visible"]:
                cfg["visible"] = True
            save_settings(cfg)
            self.refresh_all()
        elif cmd == 650:
            self.add_widget("text")
        elif cmd == 651:
            self.add_widget("image")
        elif 660 <= cmd < 664:
            self.reset_layout(corners[cmd - 660])
        elif cmd == 664:
            self.reset_layout()

    def widget_menu(self, w):
        """Right-click on a widget while moving them."""
        cfg, add_raw = self.cfg, user32.AppendMenuW
        menu, opacity = user32.CreatePopupMenu(), user32.CreatePopupMenu()
        checked = lambda on: 0x8 if on else 0
        add = lambda flags, cid, text: add_raw(menu, flags, cid, text)
        w.menu_items(add, checked)
        add(0x800, 0, None)
        add(0, 1, T("bigger"))
        add(0, 2, T("smaller"))
        levels = (1.0, 0.9, 0.75, 0.6, 0.45)
        own = getattr(w, "own_opacity", False)
        current = w.opacity() if own else cfg["opacity"]
        for i, v in enumerate(levels):
            add_raw(opacity, checked(abs(current - v) < 0.01), 10 + i, f"{int(v * 100)} %")
        add(0x10, opacity, T("opacity"))
        add(0, 5, T("home_widget"))
        add(0, 3, T("hide_widget"))
        if w.kind in ("text", "image"):
            add(0, 4, T("remove_widget"))
        add(0x800, 0, None)
        add(0, 30, T("done"))
        add(0, 32, T("help"))
        add(0, 31, T("quit_plain"))
        cmd = self.popup(menu)
        w.remember_position()
        if cmd >= 700:
            if w.menu_command(cmd):
                save_settings(cfg)
        elif cmd in (1, 2):
            self.resize_widget(w, 1 if cmd == 1 else -1)
        elif 10 <= cmd < 10 + len(levels) and own:
            w.wcfg["opacity"] = levels[cmd - 10]
            w.redraw()
            w.sync_style()
            save_settings(cfg)
        elif 10 <= cmd < 10 + len(levels):
            cfg["opacity"] = levels[cmd - 10]
            for x in self.widgets.values():
                if not x.argb:
                    user32.SetLayeredWindowAttributes(x.hwnd, TRANSPARENT_KEY, int(cfg["opacity"] * 255), 3)
                x.refresh()
            save_settings(cfg)
        elif cmd == 5:                             # back to where it was when it was new
            w.wcfg["x"] = w.wcfg["y"] = None
            w.place()
            save_settings(cfg)
            w.refresh()
        elif cmd == 3:
            w.wcfg["visible"] = False
            save_settings(cfg)
            w.refresh()
        elif cmd == 4:
            self.remove_widget(w)
        elif cmd == 30:
            self.toggle_edit()
        elif cmd == 32:
            show_help()
        elif cmd == 31:
            user32.PostQuitMessage(0)

    # ---------- sound
    def set_sound(self, choice):
        global sound_enabled
        self.cfg["sound_choice"] = choice
        self.cfg["sound"] = sound_enabled = True
        save_settings(self.cfg)
        play_requests.put(("load", sound_path(self.cfg), self.cfg["volume"], True))

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

    def theme_changed(self):
        self.__dict__.pop("_themes", None)
        for w in self.widgets.values():
            w.build()
        save_settings(self.cfg)

    def effect_changed(self):
        for w in self.widgets.values():
            if isinstance(w, KeysWidget):
                w.ripples.clear()
            w.refresh()
        save_settings(self.cfg)

    # ---------- music shortcuts
    def binds(self):
        return {**DEFAULTS["binds"], **(self.cfg.get("binds") or {})}

    def change_click_volume(self, step):
        """Ctrl+Alt+PageUp / PageDown: click volume +- 10 % (up to 200 %), you hear the new volume at once."""
        levels = sorted(CLICK_VOLUMES)
        cur = self.cfg["volume"]
        if step > 0:
            new = next((v for v in levels if v > cur + 0.001), levels[-1])
        else:
            new = next((v for v in reversed(levels) if v < cur - 0.001), levels[0])
        self.cfg["volume"] = new
        self.set_sound(self.cfg["sound_choice"])
        for w in self.widgets.values():
            if w.kind == "stats":
                w.flash_volume(new)

    def register_binds(self):
        for i in range(len(MEDIA_ACTIONS)):
            user32.UnregisterHotKey(self.hwnd, 1000 + i)
        if not MEDIA_OK or self.capturing:
            return
        binds = self.binds()
        for i, act in enumerate(MEDIA_ACTIONS):
            mods, vk = binds[act]
            repeat_ok = act in ("fwd", "back", "vol_up", "vol_down")            # holding keeps going
            self.bind_ok[act] = bool(user32.RegisterHotKey(self.hwnd, 1000 + i, mods | (0 if repeat_ok else 0x4000), vk))

    def media_action(self, act):
        pl = self.player
        if act in ("fwd", "back"):
            delta = self.cfg["seek_step"] * (1 if act == "fwd" else -1)
            media_commands.put(("seek_by", delta))
            if pl:
                pl.nudge(delta)
        elif act in ("vol_up", "vol_down"):
            step = self.cfg["music_step"] / 100 * (1 if act == "vol_up" else -1)
            media_commands.put(("vol_by", step))
            if pl:
                pl.change_volume(step)
        else:
            media_commands.put((act, None))
            if act == "play" and pl:
                pl.toggle_playing()

    def start_capture(self, act):
        self.capturing = act
        self.register_binds()
        pl = self.player
        if pl:
            pl.prompt = T("bind_prompt").format(T("ma_" + act))
            pl.refresh()

    def finish_capture(self, kid):
        vk = vk_from_key_id(kid)
        mods = 0
        for held in self.held:
            mods |= MODIFIER_IDS.get(held, 0)
        if not mods and vk != 0x1B and not (0x70 <= vk <= 0x87 or 0x60 <= vk <= 0x6F or 0xA6 <= vk <= 0xB7
                                            or vk in (0x13, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x91)):
            return                     # a plain letter would block typing -> wait for a combo with Ctrl/Alt/...
        act, self.capturing = self.capturing, None
        pl = self.player
        if pl:
            pl.prompt = None
        if vk and not (vk == 0x1B and not mods):
            old = self.cfg.get("binds") or {}
            self.cfg["binds"] = {**self.binds(), act: [mods, vk]}
            self.register_binds()
            if not self.bind_ok.get(act):
                user32.MessageBoxW(None, T("bind_busy").format(bind_text(mods, vk)), APP_NAME, 0x30)
                self.cfg["binds"] = old
            save_settings(self.cfg)
        self.register_binds()
        if pl:
            pl.refresh()

    def close(self):
        if self.editing:
            self.toggle_edit()
        save_settings(self.cfg)
        for i in range(len(MEDIA_ACTIONS)):
            user32.UnregisterHotKey(self.hwnd, 1000 + i)
        user32.UnregisterHotKey(self.hwnd, 1100)
        user32.UnregisterHotKey(self.hwnd, 1101)
        for w in list(self.widgets.values()):
            w.close()
        self.hint.close()
        shell32.Shell_NotifyIconW(2, ctypes.byref(self.tray_data()))
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


def remove_old_files():
    """Files older versions left behind that nothing uses any more (the Fortnite chest counter's log)."""
    for name in ("fortnite_chests_log.txt", "fortnite_chests_log.txt.old"):                # test version leftovers
        try:
            os.remove(os.path.join(DATA_DIR, name))
        except OSError:
            pass
    shutil.rmtree(os.path.join(DATA_DIR, "fortnite_prompts"), ignore_errors=True)


def error_box(text):
    user32.MessageBoxW(None, text, APP_NAME, 0x10)


def show_help():
    user32.MessageBoxW(None, T("help_text"), T("help_title"), 0x40)


def main():
    global overlay_hwnd, sound_enabled, mouse_sound_enabled, obs_enabled, OCR_THREAD_STARTED
    mutex = kernel32.CreateMutexW(None, False, "KeyboardSound_SingleInstance")
    if ctypes.get_last_error() == 183:                   # ERROR_ALREADY_EXISTS
        user32.MessageBoxW(None, T("already"), APP_NAME, 0x40)
        return
    if not os.path.isdir(SOUNDS_DIR):
        error_box(f"Sound files not found:\n{SOUNDS_DIR}")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # crisp drawing on scaled displays
    except Exception:
        pass
    sys.setswitchinterval(0.001)                         # react to keys faster
    ctypes.windll.ole32.CoInitializeEx(None, 2)          # single-threaded COM for the dialogs

    cfg = load_settings()
    sound_enabled = cfg["sound"]
    mouse_sound_enabled = cfg["mouse_sound"]
    try:
        audio = start(audio_worker, sound_path(cfg), cfg["volume"])
    except Exception:                                    # e.g. own sound deleted -> use the default one
        cfg["sound_choice"] = SOUNDS[0][0]
        audio = start(audio_worker, sound_path(cfg), cfg["volume"])
    app = App(cfg)
    overlay_hwnd = app.hwnd
    start(input_loop, True)
    start(media_worker)
    start(rl_worker)
    remove_old_files()
    start(fortnite_worker)
    if OCR_OK:
        try:
            start(fn_ocr_worker)
            OCR_THREAD_STARTED = True
        except Exception:
            pass
    web_start()
    obs_enabled = bool(cfg.get("obs"))

    signal.signal(signal.SIGINT, lambda *_: user32.PostQuitMessage(0))
    print("Keyboard Sound is running.  Ctrl+Alt+F11 = move widgets, Ctrl+Alt+F12 (or Ctrl+C here) = quit")
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        media_commands.put(None)
        time.sleep(0.3)                                  # let the music thread un-mute an ad it muted
        app.close()
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
