from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def win_quote(value: str) -> str:
    value = value or ""
    return "'" + value.replace("'", "''") + "'"



def safe_filename_from_url(url: str, default: str = "captured_stream.mp4") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", url or "").strip("_")
    if not cleaned:
        return default
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip("_")
    return f"{cleaned}.mp4"


def build_ytdlp_command(record: dict[str, Any]) -> str:
    url = record.get("m3u8_url", "")
    headers = record.get("headers_needed", {}) or {}
    args = ["yt-dlp"]

    referer = headers.get("Referer") or record.get("referer") or record.get("source_page")
    user_agent = headers.get("User-Agent") or record.get("user_agent")
    origin = headers.get("Origin") or record.get("origin")

    if referer:
        args.extend(["--add-header", win_quote(f"Referer:{referer}")])
    if origin:
        args.extend(["--add-header", win_quote(f"Origin:{origin}")])
    if user_agent:
        args.extend(["--user-agent", win_quote(user_agent)])

    args.append(win_quote(url))
    return " ".join(args)


def build_ffmpeg_command(record: dict[str, Any]) -> str:
    url = record.get("m3u8_url", "")
    headers = record.get("headers_needed", {}) or {}

    referer = headers.get("Referer") or record.get("referer") or record.get("source_page")
    user_agent = headers.get("User-Agent") or record.get("user_agent")
    origin = headers.get("Origin") or record.get("origin")

    header_lines = []
    if referer:
        header_lines.append(f"Referer: {referer}")
    if origin:
        header_lines.append(f"Origin: {origin}")

    output_file = safe_filename_from_url(record.get("host", "captured_stream"))
    args = ["ffmpeg", "-y"]
    if user_agent:
        args.extend(["-user_agent", win_quote(user_agent)])
    if header_lines:
        args.extend(["-headers", '(' + win_quote('\n'.join(header_lines) + '\n') + ' -replace "`n", "`r`n")'])
    args.extend(["-i", win_quote(url), "-c", "copy", win_quote(output_file)])
    return " ".join(args)


def build_vlc_command(record: dict[str, Any], vlc_path: str = "vlc") -> str:
    url = record.get("m3u8_url", "")
    headers = record.get("headers_needed", {}) or {}
    referer = headers.get("Referer") or record.get("referer") or record.get("source_page")
    user_agent = headers.get("User-Agent") or record.get("user_agent")

    args = ["&", win_quote(vlc_path)]
    if referer:
        args.append(f":http-referrer={win_quote(referer)}")
    if user_agent:
        args.append(f":http-user-agent={win_quote(user_agent)}")
    args.append(win_quote(url))
    return " ".join(args)
