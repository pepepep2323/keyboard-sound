# Keyboard Sound

Click sound on every key press + an RGB keyboard & mouse overlay that lights up the keys you press.
Works on Windows 10/11, in games too (borderless / windowed fullscreen).

**🌐 Website with live demo: https://pepepep2323.github.io/keyboard-sound**

![overlay](docs/preview.png)

## Download

**[⬇ Download KeyboardSound.zip](../../releases/latest)**. Unzip it and run `KeyboardSound.exe`.

If Windows says *"Windows protected your PC"*, click **More info → Run anyway** (the app is not code-signed).

## Features

- 🔊 **3 click sounds** (Creamy 1/2/3) or **your own** (wav, mp3, m4a, ogg, flac, wma), volume control
- 🌈 **5 light effects**: rainbow wave, static light, breathing, ripple from key, only pressed keys
- 🎨 **10 colors**: rainbow or a single color
- 🖱️ Mouse buttons, wheel and side buttons
- ⌨️ Key labels follow your keyboard layout (e.g. Czech ě š č ř ž)
- 🎮 Doesn't slow games down, clicks go through the overlay
- 🇨🇿 / 🇬🇧 Menus in Czech or English (follows your Windows language)

## Help

Full help: **[NAVOD.txt](NAVOD.txt)** (also inside the ZIP and in the app: menu → Help).

| What | How |
|---|---|
| Open the menu | Right-click the keyboard icon near the clock (bottom right) |
| Move / resize | `Ctrl+Alt+F11`, drag it, scroll to resize, right-click for options, `Ctrl+Alt+F11` again to finish |
| Change sound / effect / color | Menu → Sound / Effect / Color |
| Your own sound | Menu → Sound → Load your own sound… |
| Hide the keyboard | Menu → Show keyboard |
| Quit | `Ctrl+Alt+F12` or menu → Quit |

Settings are saved in `%APPDATA%\KeyboardSound`.

## Česky

Zvuk cvaknutí při každém stisku klávesy + RGB klávesnice a myš na obrazovce, které svítí podle toho, co mačkáš.
3 zvuky nebo tvůj vlastní, 5 světelných efektů, 10 barev.

1. Stáhni **[KeyboardSound.zip](../../releases/latest)**, rozbal ho a spusť `KeyboardSound.exe`.
2. Když Windows napíše *„Systém Windows ochránil váš počítač“*, klikni na **Další informace → Přesto spustit**.
3. Menu otevřeš pravým klikem na ikonu u hodin. `Ctrl+Alt+F11` přesune klávesnici, `Ctrl+Alt+F12` aplikaci vypne.
   Celý návod je v **[NAVOD.txt](NAVOD.txt)**.

## Run from source

Needs only Python 3 (no extra packages):

```
python keyboard_sound.py
```

- `build.bat` builds the `.exe` (uses PyInstaller)
- `make_icon.py` draws the app icon
- `make_preview.py` renders the overlay pictures for the website
