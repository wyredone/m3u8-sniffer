from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse, urlunparse
from playwright.sync_api import sync_playwright

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def preserve_query_token(base_url: str, target_url: str) -> str:
    """Appends query token parameters from base_url to target_url if missing."""
    parsed_base = urlparse(base_url)
    parsed_target = urlparse(target_url)

    if parsed_base.netloc == parsed_target.netloc and parsed_base.query and not parsed_target.query:
        return urlunparse((
            parsed_target.scheme,
            parsed_target.netloc,
            parsed_target.path,
            parsed_target.params,
            parsed_base.query,
            parsed_target.fragment
        ))
    return target_url


def capture_stream_url(target_page_url: str, timeout_seconds: int = 15) -> tuple[str, dict[str, str]] | None:
    """Launches Playwright, monitors network traffic, and returns (m3u8_url, request headers)."""
    detected_m3u8: str | None = None
    captured_headers: dict[str, str] = {}

    print(f"[*] Navigating to page: {target_page_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()

        def handle_response(response):
            nonlocal detected_m3u8, captured_headers
            url = response.url
            if ".m3u8" in url.lower() and response.ok and (not detected_m3u8 or "master" in url.lower()):
                if not any(ext in url.lower() for ext in (".ts", ".m4s")):
                    detected_m3u8 = url
                    captured_headers = response.request.all_headers()
                    print(f"[+] Sniffed M3U8 manifest from network traffic: {url}")

        page.on("response", handle_response)

        try:
            page.goto(target_page_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"[!] Page load note: {exc}")
        finally:
            if detected_m3u8:
                cookies = context.cookies([detected_m3u8])
                if cookies:
                    captured_headers["cookie"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            browser.close()

    if detected_m3u8:
        return detected_m3u8, captured_headers
    return None


def auto_detect_and_download(page_url: str) -> None:
    """Main workflow automation."""
    capture_result = capture_stream_url(page_url)
    if not capture_result:
        print("[-] Error: No valid .m3u8 stream was detected during page navigation.")
        sys.exit(1)

    raw_m3u8_url, captured_headers = capture_result

    from .download_engine import download_record
    download_record({"m3u8_url": raw_m3u8_url, "request_headers": captured_headers}, "%(title)s.%(ext)s", print, lambda: False)


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