@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

python -m PyInstaller --name M3U8SnifferTV --onedir --windowed --clean --collect-all PySide6 --collect-all playwright run.py

echo.
echo Build complete. EXE folder: dist\M3U8SnifferTV
pause
