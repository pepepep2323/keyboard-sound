# Keyboard Sound

Click sound on every key press + an RGB keyboard & mouse overlay that lights up the keys you press.
Works on Windows 10/11, in games too (borderless / windowed fullscreen).

![icon](icon_preview.png)

## Download

**[⬇ Download KeyboardSound.zip](../../releases/latest)**. Unzip it and run `KeyboardSound.exe`.

If Windows says *"Windows protected your PC"*, click **More info → Run anyway** (the app is not code-signed).

## Controls

| Shortcut | What it does |
|---|---|
| `Ctrl+Alt+F11` | Move / resize the overlay: drag it, scroll to resize, right-click for options, press again to finish |
| `Ctrl+Alt+F12` | Quit |
| Tray icon (near the clock) | Right-click: sound on/off, hide overlay, start with Windows, quit |

Settings are saved in `%APPDATA%\KeyboardSound`.

## Česky

Zvuk cvaknutí při každém stisku klávesy + RGB klávesnice a myš na obrazovce, které svítí podle toho, co mačkáš.

1. Stáhni **[KeyboardSound.zip](../../releases/latest)**, rozbal ho a spusť `KeyboardSound.exe`.
2. Když Windows napíše *„Systém Windows ochránil váš počítač“*, klikni na **Další informace → Přesto spustit**.
3. `Ctrl+Alt+F11` přesune nebo zvětší overlay, `Ctrl+Alt+F12` aplikaci ukončí. Další možnosti najdeš pod pravým klikem na ikonu u hodin.

## Run from source

Needs only Python 3 (no extra packages):

```
python keyboard_sound.py
```

Build the `.exe` yourself with `build.bat` (uses PyInstaller).
