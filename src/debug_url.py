from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlparse
import requests


EXCLUDE_EXTENSIONS = (
    ".js", ".css", ".json", ".html", ".htm", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def derive_m3u8_from_segment(url: str) -> str | None:
    ph_match = re.search(r'(.+/)(seg-\d+-(v\d+-a\d+)\.ts)(\?.*)?$', url, re.IGNORECASE)
    if ph_match:
        base_path = ph_match.group(1)
        v_tag = ph_match.group(3)
        query = ph_match.group(4) or ""
        return f"{base_path}index-{v_tag}.m3u8{query}"

    generic_match = re.search(r'(.+/)(seg(?:ment)?[-_]?\d+[^/]*\.(?:ts|m4s|cmfv|cmfa))(\?.*)?$', url, re.IGNORECASE)
    if generic_match:
        base_path = generic_match.group(1)
        query = generic_match.group(3) or ""
        return f"{base_path}index.m3u8{query}"

    return None


def extract_inline_m3u8_from_text(text: str) -> list[str]:
    if not text:
        return []
    normalized = text.replace(r"\/", "/")
    pattern = r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*'
    matches = re.findall(pattern, normalized)
    cleaned = []
    for m in matches:
        clean_url = m.replace("&amp;", "&")
        if clean_url not in cleaned:
            cleaned.append(clean_url)

    def quality_score(u: str) -> int:
        if "1080P" in u.upper(): return 1080
        if "720P" in u.upper(): return 720
        if "480P" in u.upper(): return 480
        if "240P" in u.upper(): return 240
        return 0

    return sorted(cleaned, key=quality_score, reverse=True)


def is_segment_chunk(url: str) -> bool:
    lowered = url.lower()
    return (
        "range=" in lowered
        or "/range/" in lowered
        or "bytes=" in lowered
        or lowered.endswith((".ts", ".m4s", ".cmfv", ".cmfa"))
    )


def analyze_url(url: str, referer: str = "", user_agent: str = "") -> None:
    ua = user_agent or DEFAULT_USER_AGENT
    headers = {"User-Agent": ua}
    
    # Default referer to target URL if inspecting a webpage
    if not referer and ("http://" in url or "https://" in url):
        referer = url
    if referer:
        headers["Referer"] = referer

    print("\n" + "=" * 70)
    print(" 🔍 MEDIA URL DIAGNOSTIC REPORT")
    print("=" * 70)
    print(f" Target URL : {url}")
    print(f" Referer    : {referer}")
    print("=" * 70)

    clean_path = url.split("?")[0].lower()
    is_segment = is_segment_chunk(url)
    derived_m3u8 = derive_m3u8_from_segment(url) if is_segment else None

    print("\n[1] URL CLASSIFICATION")
    if is_segment:
        print(" ⚠️  WARNING: URL points to an individual segment chunk, not a full playlist.")
        if derived_m3u8:
            print(f" ✅ RECONSTRUCTED PARENT PLAYLIST:\n    {derived_m3u8}")
    elif any(clean_path.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
        print(" ❌ NOTICE: Static web asset (frontend JS/CSS), not a video stream.")
    elif ".m3u8" in clean_path or "manifest" in clean_path:
        print(" ✅ DETECTED: Master/Media HLS Manifest URL.")
    elif clean_path.endswith((".mp4", ".webm")):
        print(" ℹ️ DETECTED: Progressive MP4/WebM video stream.")
    else:
        print(" ℹ️ TYPE: General Web Document or Dynamic Stream API.")

    print("\n[2] HTTP REQUEST TEST")
    req_headers = dict(headers)
    if any(clean_path.endswith(ext) for ext in (".mp4", ".webm", ".ts", ".m4s")):
        req_headers["Range"] = "bytes=0-2048"

    found_m3u8s = []
    try:
        resp = requests.get(url, headers=req_headers, timeout=10, allow_redirects=True)
        status = resp.status_code
        content_type = resp.headers.get("Content-Type", "Unknown")
        content_len = resp.headers.get("Content-Length", "Unknown")

        print(f" Status Code   : {status} {'OK' if resp.ok else 'ERROR'}")
        print(f" Content-Type  : {content_type}")
        print(f" Content-Length: {content_len} bytes")

        body_sample = resp.text[:2000]

        print("\n[3] BODY CONTENT INSPECTION")
        if body_sample.lstrip().startswith("#EXTM3U"):
            print(" ✅ CONFIRMED: Response is a valid HLS M3U8 Playlist.")
        elif "<MPD" in body_sample:
            print(" ✅ CONFIRMED: Response is an MPEG-DASH Manifest.")
        elif "html" in content_type.lower() or "javascript" in content_type.lower():
            print(" ℹ️ BODY: HTML/JS document. Scanning for embedded inline .m3u8 streams...")
            found_m3u8s = extract_inline_m3u8_from_text(resp.text)
            if found_m3u8s:
                print(f" 🎉 FOUND {len(found_m3u8s)} EMBEDDED PLAYLIST(S) (Highest quality first):")
                for link in found_m3u8s:
                    print(f"    👉 {link}")
            else:
                print("    No inline .m3u8 links found in body.")
        else:
            print(f" ℹ️ BODY SAMPLE:\n{body_sample[:300]}...")

    except Exception as exc:
        print(f" ❌ HTTP Request failed: {exc}")
        return

    # Select best URL for download recommendation
    target_dl_url = derived_m3u8 or (found_m3u8s[0] if found_m3u8s else url)

    print("\n[4] RECOMMENDED YT-DLP DOWNLOAD COMMAND")
    cmd_parts = ["yt-dlp", f'"{target_dl_url}"']
    if referer:
        cmd_parts.append(f'--add-header "Referer:{referer}"')
    cmd_parts.append(f'--add-header "User-Agent:{ua}"')
    cmd_parts.append('-o "%(title)s.%(ext)s"')
    print(" " + " ".join(cmd_parts))
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug and analyze media stream URLs.")
    parser.add_argument("url", nargs="?", help="Target URL to inspect")
    parser.add_argument("-r", "--referer", help="Referer header URL", default="")
    parser.add_argument("-u", "--user-agent", help="User-Agent string", default="")

    args = parser.parse_args()

    target_url = args.url
    if not target_url:
        target_url = input("Enter URL to debug: ").strip()

    if not target_url:
        print("No URL provided. Exiting.")
        sys.exit(1)

    analyze_url(target_url, referer=args.referer, user_agent=args.user_agent)


if __name__ == "__main__":
    main()