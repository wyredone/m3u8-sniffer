# M3U8 Sniffer TV - Windows Python App

A Windows desktop GUI app that opens a visible Chromium browser, lets you manually navigate/click through a video page, and captures HLS/M3U8 playlist URLs from live browser network traffic.

## What it does

- Paste a page/video URL.
- Open a visible Playwright Chromium browser.
- Manually click through the page as needed.
- Detect `.m3u8` requests and HLS playlist responses in real time.
- Rank likely best/master playlists.
- Capture useful headers: Referer, Origin, User-Agent.
- Copy the raw M3U8 URL.
- Copy ready-to-run `yt-dlp`, `ffmpeg`, or `vlc` commands.
- Test whether a selected playlist URL responds as HLS.
- Export captured results to JSON or CSV.

## Guardrail

Use this only for streams you own, control, or are authorized to inspect. This app does not bypass DRM, logins, paywalls, or access controls. It does not auto-click ads or evade site restrictions.

## Requirements

- Windows 10/11
- Python 3.11 or newer recommended
- Internet access for first-time dependency/browser installation

## Fast start

Double-click:

```bat
run_windows.bat
```

That script will:

1. Create `.venv` if missing.
2. Install Python packages.
3. Install Playwright Chromium.
4. Start the app.

## Manual start

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py
```

## How to use

1. Paste the video/page URL.
2. Click **Open Browser**.
3. In the Chromium window, manually navigate/click until the actual player loads.
4. Watch the app table for captured HLS/M3U8 URLs.
5. Select the best/highest-score row.
6. Use:
   - **Copy URL** for the raw URL.
   - **Copy yt-dlp** for a download command.
   - **Copy FFmpeg** for a remux command.
   - **Copy VLC** for a playback command.
   - **Test Playlist** to check whether the selected URL still responds.

## Build EXE

Double-click:

```bat
build_exe_windows.bat
```

Output folder:

```text
dist\M3U8SnifferTV
```

Note: Playwright browser packaging can be large. If the EXE opens but Chromium is missing, run this once from the app folder or bundled Python environment:

```bat
python -m playwright install chromium
```

## Project structure

```text
m3u8_sniffer_windows/
├── run.py
├── requirements.txt
├── run_windows.bat
├── setup_windows.bat
├── build_exe_windows.bat
├── README.md
└── src/
    ├── __init__.py
    ├── browser_worker.py
    ├── command_utils.py
    ├── export_utils.py
    ├── gui.py
    ├── hls_utils.py
    └── stream_tester.py
```

## Notes

- The app uses a local `browser_profile` folder so cookies/session data can persist between runs.
- Some captured URLs expire quickly.
- Some streams require the browser session cookies and will not work from a copied URL alone.
- If a stream fails testing but plays in the browser, it may require cookies, signed headers, or short-lived authorization tokens.
