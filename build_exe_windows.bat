@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
set PLAYWRIGHT_BROWSERS_PATH=0
python -m playwright install chromium
if errorlevel 1 exit /b 1

python -m PyInstaller --name M3U8SnifferTV --onedir --windowed --clean --collect-all PySide6 --collect-all playwright --collect-all yt_dlp --collect-all requests --collect-all m3u8 run.py

if errorlevel 1 exit /b 1
echo.
echo Build complete. EXE folder: dist\M3U8SnifferTV
pause
