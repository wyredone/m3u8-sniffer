"""
M3U8 Sniffer TV - Windows launcher

This launcher auto-installs missing Python packages, then starts the GUI.
Run with: python run.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = APP_DIR / "requirements.txt"

REQUIRED_IMPORTS = {
    "PySide6": "PySide6",
    "playwright": "playwright",
    "requests": "requests",
    "m3u8": "m3u8",
}


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _install_requirements() -> None:
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError(f"Missing requirements.txt at {REQUIREMENTS_FILE}")
    print("Installing missing dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)])


def _ensure_dependencies() -> None:
    missing = [package for module, package in REQUIRED_IMPORTS.items() if not _has_module(module)]
    if missing:
        _install_requirements()


def _ensure_playwright_browser() -> None:
    marker = APP_DIR / ".playwright_browser_checked"
    if marker.exists():
        return
    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        marker.write_text("chromium installed or verified\n", encoding="utf-8")
    except subprocess.CalledProcessError:
        print("Playwright Chromium install failed. Run: python -m playwright install chromium")


def main() -> None:
    os.chdir(APP_DIR)
    _ensure_dependencies()
    _ensure_playwright_browser()

    from src.gui import run_gui

    run_gui()


if __name__ == "__main__":
    main()
