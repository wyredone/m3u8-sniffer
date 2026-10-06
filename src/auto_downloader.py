from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse, urljoin, urlunparse
import m3u8
import requests
from playwright.sync_api import sync_playwright
import yt_dlp

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def preserve_query_token(base_url: str, target_url: str) -> str:
    """Appends query token parameters from base_url to target_url if missing."""
    parsed_base = urlparse(base_url)
    parsed_target = urlparse(target_url)

    if parsed_base.query and not parsed_target.query:
        return urlunparse((
            parsed_target.scheme,
            parsed_target.netloc,
            parsed_target.path,
            parsed_target.params,
            parsed_base.query,
            parsed_target.fragment
        ))
    return target_url


def resolve_best_stream(master_url: str, referer: str, user_agent: str) -> str:
    """Fetches an M3U8 manifest and resolves the highest bandwidth/resolution variant URL while preserving query tokens."""
    headers = {
        "User-Agent": user_agent,
        "Referer": referer,
    }
    try:
        resp = requests.get(master_url, headers=headers, timeout=10)
        if not resp.ok:
            return master_url

        parsed = m3u8.loads(resp.text, uri=master_url)
        if parsed.is_variant and parsed.playlists:
            sorted_playlists = sorted(
                parsed.playlists,
                key=lambda p: (
                    p.stream_info.resolution[0] if p.stream_info.resolution else 0,
                    p.stream_info.bandwidth or 0,
                ),
                reverse=True,
            )
            best_variant = sorted_playlists[0]
            resolved_url = preserve_query_token(master_url, best_variant.absolute_uri)
            print(
                f"[+] Resolved highest quality variant: "
                f"{best_variant.stream_info.resolution or 'Unknown Res'} "
                f"({best_variant.stream_info.bandwidth} bps)"
            )
            return resolved_url
    except Exception as exc:
        print(f"[!] Manifest parsing warning: {exc}. Falling back to captured URL.")

    return master_url


def capture_stream_url(target_page_url: str, timeout_seconds: int = 15) -> tuple[str, str] | None:
    """Launches Playwright, monitors network traffic, and returns (m3u8_url, referer)."""
    detected_m3u8: str | None = None

    print(f"[*] Navigating to page: {target_page_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()

        def handle_response(response):
            nonlocal detected_m3u8
            url = response.url
            if ".m3u8" in url.lower() and not detected_m3u8:
                if not any(ext in url.lower() for ext in (".ts", ".m4s")):
                    detected_m3u8 = url
                    print(f"[+] Sniffed M3U8 manifest from network traffic: {url}")

        page.on("response", handle_response)

        try:
            page.goto(target_page_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"[!] Page load note: {exc}")
        finally:
            browser.close()

    if detected_m3u8:
        return detected_m3u8, target_page_url
    return None


def download_stream(stream_url: str, referer: str, user_agent: str, output_path: str = "%(title)s.%(ext)s") -> None:
    """Downloads the stream directly via yt-dlp Python API with proper headers."""
    print(f"[*] Starting download for stream...")

    ydl_opts = {
        "outtmpl": output_path,
        "http_headers": {
            "Referer": referer,
            "User-Agent": user_agent,
        },
        "concurrent_fragment_downloads": 5,
        "nocheckcertificate": True,
        "quiet": False,
        "no_warnings": False,
        "hls_use_mpegts": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([stream_url])


def auto_detect_and_download(page_url: str) -> None:
    """Main workflow automation."""
    capture_result = capture_stream_url(page_url)
    if not capture_result:
        print("[-] Error: No valid .m3u8 stream was detected during page navigation.")
        sys.exit(1)

    raw_m3u8_url, referer = capture_result

    best_stream_url = resolve_best_stream(raw_m3u8_url, referer=referer, user_agent=DEFAULT_USER_AGENT)

    download_stream(best_stream_url, referer=referer, user_agent=DEFAULT_USER_AGENT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated M3U8 stream detector and downloader.")
    parser.add_argument("url", nargs="?", help="Video page URL")
    args = parser.parse_args()

    url = args.url or input("Enter video page URL: ").strip()
    if not url:
        print("No URL provided.")
        sys.exit(1)

    auto_detect_and_download(url)


if __name__ == "__main__":
    main()