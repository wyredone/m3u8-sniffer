from __future__ import annotations
from typing import Any, Callable

class DownloadCancelled(Exception):
    pass

def session_headers(record: dict[str, Any]) -> dict[str, str]:
    allowed = {"user-agent", "referer", "origin", "cookie", "authorization", "accept"}
    headers = {k: v for k, v in record.get("request_headers", {}).items() if k.lower() in allowed and v}
    present = {k.lower() for k in headers}
    for k, v in record.get("headers_needed", {}).items():
        if v and k.lower() not in present:
            headers[k] = v
    return headers

def download_record(record: dict[str, Any], output_path: str, progress: Callable[[str], None], cancelled: Callable[[], bool], metrics: Callable[[dict], None] | None = None) -> None:
    import yt_dlp
    import shutil
    import sys
    from pathlib import Path
    ffmpeg_dir = Path(sys.executable).parent
    if not shutil.which("ffmpeg") and not (ffmpeg_dir / "ffmpeg.exe").exists():
        raise RuntimeError("FFmpeg is required for MP4 output. Install FFmpeg and add it to PATH, or place ffmpeg.exe beside the app executable.")
    def hook(info):
        if metrics:
            metrics({k: info.get(k) for k in ("status", "downloaded_bytes", "total_bytes", "total_bytes_estimate", "speed", "eta")})
        if cancelled():
            raise DownloadCancelled("Download cancelled.")
        if info.get("status") == "downloading":
            progress(" | ".join(str(info.get(k, "")).strip() for k in ("_percent_str", "_speed_str", "_eta_str")))
        elif info.get("status") == "finished":
            progress("Download finished; merging/remuxing...")
    url = record.get("m3u8_url", "")
    if not url.startswith(("http://", "https://")):
        raise ValueError("A downloadable HTTP or HTTPS URL is required.")
    height = record.get("max_height")
    quality_filter = f"[height<=?{int(height)}]" if height else ""
    import http.cookiejar
    from urllib.parse import urlparse
    request_headers = session_headers(record)
    cookie_value = next((v for k, v in request_headers.items() if k.lower() == "cookie"), "")
    request_headers = {k: v for k, v in request_headers.items() if k.lower() != "cookie"}
    options = {"outtmpl": output_path, "http_headers": request_headers,
               "format": f"bestvideo{quality_filter}+bestaudio/best{quality_filter}", "merge_output_format": "mp4",
               "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
               "progress_hooks": [hook], "socket_timeout": 15, "retries": 3,
               "quiet": True, "noprogress": True, "concurrent_fragment_downloads": 5}
    if record.get("audio_only"):
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    if (ffmpeg_dir / "ffmpeg.exe").exists():
        options["ffmpeg_location"] = str(ffmpeg_dir)
    if cancelled():
        raise DownloadCancelled("Download cancelled.")
    with yt_dlp.YoutubeDL(options) as downloader:
        host = urlparse(url).hostname or ""
        for item in cookie_value.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator:
                downloader.cookiejar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, host, False, False, "/", True, url.startswith("https:"), None, True, None, None, {}))
        downloader.download([url])
