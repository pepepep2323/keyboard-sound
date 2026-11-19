@echo off
rem Builds dist\KeyboardSound.exe (a single file you can share).
cd /d "%~dp0"

if not exist .build-venv (
    python -m venv .build-venv || goto :error
    .build-venv\Scripts\python -m pip install --quiet pyinstaller || goto :error
)
if not exist icon.ico python make_icon.py

.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed ^
    --name KeyboardSound --icon icon.ico ^
    --add-data "click_loud.wav;." --add-data "icon.ico;." ^
    keyboard_sound.py || goto :error

echo.
echo Done: %~dp0dist\KeyboardSound.exe
exit /b 0

:error
echo Build failed.
exit /b 1
