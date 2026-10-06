from __future__ import annotations

import asyncio
import os
import queue
import re
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Request, Response

from .hls_utils import (
    content_type_is_hls,
    is_probable_m3u8_url,
    looks_like_hls_text,
    make_capture_record,
    origin_from_url,
)

AD_DOMAIN_KEYWORDS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adnxs.com",
    "amazon-adsystem.com",
    "adservice",
    "analytics",
    "telemetry",
    "scorecardresearch.com",
    "quantserve.com",
    "/ads/",
    "ad-delivery",
    "dai.google.com",
)


def is_ad_domain(url: str) -> bool:
    url_lower = url.lower()
    return any(keyword in url_lower for keyword in AD_DOMAIN_KEYWORDS)


def is_probable_media_stream_url(url: str) -> bool:
    """Checks if the URL matches standard stream formats (HLS, DASH, TS/m4s segments, blob)."""
    if not url:
        return False
    if is_probable_m3u8_url(url):
        return True
    clean_url = url.split("?")[0].split("#")[0].lower()
    return (
        clean_url.endswith((".m3u8", ".mpd", ".ts", ".m4s"))
        or "m3u8" in clean_url
        or "manifest" in clean_url
        or "playlist" in clean_url
        or clean_url.startswith("blob:")
    )


def content_type_is_media_stream(content_type: str) -> bool:
    """Checks if the Content-Type header indicates HLS, DASH, or media segment content."""
    if not content_type:
        return False
    if content_type_is_hls(content_type):
        return True
    ct = content_type.lower()
    return any(
        media_type in ct
        for media_type in (
            "application/x-mpegurl",
            "application/vnd.apple.mpegurl",
            "application/dash+xml",
            "video/mp2t",
            "video/iso.segment",
            "audio/mp4",
            "video/mp4",
        )
    )


def detect_stream_format(url: str, content_type: str = "") -> str:
    """Identifies stream media classification (HLS, DASH, TS Segment, fMP4, Blob)."""
    clean_url = url.split("?")[0].split("#")[0].lower()
    ct = content_type.lower()
    if clean_url.endswith(".mpd") or "dash+xml" in ct:
        return "DASH"
    if clean_url.endswith(".ts") or "video/mp2t" in ct:
        return "TS Segment"
    if clean_url.endswith(".m4s") or "iso.segment" in ct:
        return "fMP4 Segment"
    if url.startswith("blob:"):
        return "Blob Stream"
    return "HLS"


def parse_m3u8_variants(playlist_text: str) -> list[dict[str, Any]]:
    """Extracts stream qualities, bandwidths, resolution, codecs, and URLs from Master Playlists."""
    variants: list[dict[str, Any]] = []
    if not playlist_text or "#EXT-X-STREAM-INF" not in playlist_text:
        return variants

    lines = playlist_text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            info: dict[str, Any] = {}

            res_match = re.search(r"RESOLUTION=(\d+x\d+)", line)
            if res_match:
                info["resolution"] = res_match.group(1)
                try:
                    _, h = map(int, res_match.group(1).split("x"))
                    info["quality_label"] = f"{h}p"
                except Exception:
                    pass

            bw_match = re.search(r"BANDWIDTH=(\d+)", line)
            if bw_match:
                info["bandwidth"] = int(bw_match.group(1))

            codec_match = re.search(r'CODECS="([^"]+)"', line)
            if codec_match:
                info["codecs"] = codec_match.group(1)

            fps_match = re.search(r"FRAME-RATE=([\d.]+)", line)
            if fps_match:
                info["fps"] = float(fps_match.group(1))

            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("#"):
                    info["url"] = next_line

            variants.append(info)
    return variants


class BrowserThread(QThread):
    stream_detected = Signal(dict)
    status_changed = Signal(str)
    error_reported = Signal(str)
    page_changed = Signal(str)

    def __init__(self, start_url: str = "", profile_dir: str | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.start_url = start_url.strip()
        self.profile_dir = profile_dir or str(Path.cwd() / "browser_profile")
        self.state_file = Path.cwd() / "browser_state.json"
        self.command_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.capture_enabled = True
        self.block_heavy_resources = False
        self.stop_requested = False
        self._attached_pages: set[int] = set()
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._active_page: Page | None = None
        self._page_user_agents: dict[int, str] = {}
        self._seen_response_ids: set[str] = set()

    def open_url(self, url: str) -> None:
        self.command_queue.put(("open_url", url.strip()))

    def set_capture_enabled(self, enabled: bool) -> None:
        self.command_queue.put(("capture", bool(enabled)))

    def set_resource_blocking(self, enabled: bool) -> None:
        self.command_queue.put(("block_resources", bool(enabled)))

    def close_browser(self) -> None:
        self.command_queue.put(("close", None))

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.error_reported.emit(f"Browser thread crashed: {exc}\n{traceback.format_exc()}")

    def _find_system_browser_path(self) -> dict[str, str | None]:
        paths = {
            "chrome": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            ],
            "msedge": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
            ],
        }
        found = {}
        for browser_name, candidates in paths.items():
            found[browser_name] = next((p for p in candidates if p and os.path.exists(p)), None)
        return found

    async def _launch_browser(self, playwright: Any) -> tuple[Browser, BrowserContext]:
        found_paths = self._find_system_browser_path()
        targets = [
            {"channel": None, "executable_path": None, "label": "Bundled Chromium"},
            {"channel": "chrome", "executable_path": None, "label": "Installed Chrome (Channel)"},
            {"channel": None, "executable_path": found_paths.get("chrome"), "label": "Installed Chrome (Path)"},
            {"channel": "msedge", "executable_path": None, "label": "Installed Edge (Channel)"},
            {"channel": None, "executable_path": found_paths.get("msedge"), "label": "Installed Edge (Path)"},
        ]

        last_error = None
        for target in targets:
            label = target["label"]
            executable_path = target["executable_path"]
            channel = target["channel"]

            if executable_path is None and label.endswith("(Path)"):
                continue

            self.status_changed.emit(f"Attempting launch with {label}...")
            kwargs: dict[str, Any] = {
                "headless": False,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            }
            if channel:
                kwargs["channel"] = channel
            if executable_path:
                kwargs["executable_path"] = executable_path

            try:
                browser = await playwright.chromium.launch(**kwargs)
                context_kwargs: dict[str, Any] = {
                    "ignore_https_errors": True,
                    "viewport": {"width": 1280, "height": 720},
                }
                if self.state_file.exists():
                    try:
                        context_kwargs["storage_state"] = str(self.state_file)
                    except Exception:
                        pass

                context = await browser.new_context(**context_kwargs)
                self.status_changed.emit(f"Successfully launched {label}.")
                return browser, context
            except Exception as exc:
                last_error = exc
                continue

        raise RuntimeError(f"Could not launch any browser instance. Last error: {last_error}")

    async def _run_async(self) -> None:
        self.status_changed.emit("Starting Browser...")
        async with async_playwright() as playwright:
            try:
                self._browser, self._context = await self._launch_browser(playwright)
            except Exception as launch_exc:
                self.error_reported.emit(f"Failed to launch browser: {launch_exc}")
                self.status_changed.emit("Browser launch failed.")
                return

            if self._context is None:
                return

            self._context.on("page", lambda page: asyncio.create_task(self._attach_page(page)))

            pages = self._context.pages
            if pages:
                self._active_page = pages[0]
                for page in pages:
                    await self._attach_page(page)
            else:
                self._active_page = await self._context.new_page()
                await self._attach_page(self._active_page)

            self.status_changed.emit("Browser ready. Capture is ON.")

            if self.start_url:
                await self._navigate(self.start_url)

            while not self.stop_requested:
                await self._process_commands()
                await asyncio.sleep(0.10)

            self.status_changed.emit("Saving browser session state...")
            try:
                if self._context:
                    await self._context.storage_state(path=str(self.state_file))
            except Exception:
                pass

            self.status_changed.emit("Closing browser...")
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            self.status_changed.emit("Browser closed.")

    async def _process_commands(self) -> None:
        while True:
            try:
                command, value = self.command_queue.get_nowait()
            except queue.Empty:
                return

            if command == "open_url":
                await self._navigate(str(value or ""))
            elif command == "capture":
                self.capture_enabled = bool(value)
                self.status_changed.emit("Capture is ON." if self.capture_enabled else "Capture is PAUSED.")
            elif command == "block_resources":
                self.block_heavy_resources = bool(value)
            elif command == "close":
                self.stop_requested = True
                return

    async def _navigate(self, url: str) -> None:
        if not url:
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url

        try:
            page = self._active_page
            if page is None or page.is_closed():
                if self._context is None:
                    return
                page = await self._context.new_page()
                self._active_page = page
                await self._attach_page(page)

            self.status_changed.emit(f"Opening: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            self.page_changed.emit(page.url)
            self.status_changed.emit("Page loaded. Click through manually; detected streams will appear here.")
        except Exception as exc:
            self.error_reported.emit(f"Navigation failed: {exc}")

    async def _attach_page(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._attached_pages:
            return
        self._attached_pages.add(page_id)
        self._active_page = page

        try:
            if self.block_heavy_resources:
                await page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ["image", "font", "stylesheet"]
                    and not is_probable_media_stream_url(route.request.url)
                    else route.continue_(),
                )

            page.on("request", lambda request: asyncio.create_task(self._on_request(page, request)))
            page.on("response", lambda response: asyncio.create_task(self._on_response(page, response)))
            page.on("framenavigated", lambda frame: self.page_changed.emit(page.url) if frame == page.main_frame else None)
            page.on("close", lambda: self.status_changed.emit("A browser page was closed."))
            self.status_changed.emit("Attached network sniffer to browser page/popup.")
        except Exception as exc:
            self.error_reported.emit(f"Could not attach to page: {exc}")

    async def _get_page_user_agent(self, page: Page) -> str:
        page_id = id(page)
        if page_id in self._page_user_agents:
            return self._page_user_agents[page_id]
        try:
            user_agent = await page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = ""
        self._page_user_agents[page_id] = user_agent or ""
        return user_agent or ""

    async def _on_request(self, page: Page, request: Request) -> None:
        if not self.capture_enabled:
            return

        url = request.url or ""
        if not is_probable_media_stream_url(url) or is_ad_domain(url):
            return

        try:
            headers = dict(request.headers or {})
            referer = headers.get("referer", "")
            origin = headers.get("origin", "") or origin_from_url(referer or page.url)
            user_agent = headers.get("user-agent", "") or await self._get_page_user_agent(page)

            record = make_capture_record(
                m3u8_url=url,
                source_page=page.url,
                event_source="request",
                method=request.method,
                status_code=None,
                content_type="",
                referer=referer,
                origin=origin,
                user_agent=user_agent,
                resource_type=request.resource_type,
                request_headers=headers,
                response_headers={},
                playlist_text=None,
            )
            record["stream_format"] = detect_stream_format(url)
            self.stream_detected.emit(record)
        except Exception as exc:
            self.error_reported.emit(f"Request capture error: {exc}")

    async def _on_response(self, page: Page, response: Response) -> None:
        if not self.capture_enabled:
            return

        url = response.url or ""
        if is_ad_domain(url):
            return

        headers = dict(response.headers or {})
        content_type = headers.get("content-type", "")
        is_stream_candidate = is_probable_media_stream_url(url) or content_type_is_media_stream(content_type)
        if not is_stream_candidate:
            return

        response_key = f"{response.status}:{url}"
        if response_key in self._seen_response_ids:
            return
        self._seen_response_ids.add(response_key)

        playlist_text = None
        should_read_body = is_probable_media_stream_url(url) or content_type_is_media_stream(content_type)

        if should_read_body:
            try:
                content_length = headers.get("content-length", "")
                if not content_length or int(content_length) <= 2_000_000:
                    playlist_text = await response.text()
                    if (
                        not looks_like_hls_text(playlist_text)
                        and not is_probable_media_stream_url(url)
                        and not content_type_is_media_stream(content_type)
                    ):
                        return
            except Exception:
                playlist_text = None

        try:
            request = response.request
            request_headers = dict(request.headers or {})
            referer = request_headers.get("referer", "")
            origin = request_headers.get("origin", "") or origin_from_url(referer or page.url)
            user_agent = request_headers.get("user-agent", "") or await self._get_page_user_agent(page)

            record = make_capture_record(
                m3u8_url=url,
                source_page=page.url,
                event_source="response",
                method=request.method,
                status_code=response.status,
                content_type=content_type,
                referer=referer,
                origin=origin,
                user_agent=user_agent,
                resource_type=request.resource_type,
                request_headers=request_headers,
                response_headers=headers,
                playlist_text=playlist_text,
            )

            record["stream_format"] = detect_stream_format(url, content_type)

            if playlist_text and "#EXT-X-STREAM-INF" in playlist_text:
                variants = parse_m3u8_variants(playlist_text)
                if variants:
                    record["variants"] = variants

            self.stream_detected.emit(record)
        except Exception as exc:
            self.error_reported.emit(f"Response capture error: {exc}")