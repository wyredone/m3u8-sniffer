from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

HLS_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
    "application/mpegurl",
    "application/octet-stream",
}

M3U8_URL_RE = re.compile(r"\.m3u8(?:$|[?#])", re.IGNORECASE)
RESOLUTION_RE = re.compile(r"RESOLUTION=(\d+x\d+)", re.IGNORECASE)
BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)", re.IGNORECASE)


def now_stamp() -> str:
    return datetime.now().strftime("%m-%d-%y, %I:%M %p")


def normalize_url(url: str) -> str:
    return (url or "").strip()


def url_fingerprint(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def is_probable_m3u8_url(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return bool(M3U8_URL_RE.search(lowered)) or "m3u8" in lowered


def content_type_is_hls(content_type: str | None) -> bool:
    if not content_type:
        return False
    main_type = content_type.split(";", 1)[0].strip().lower()
    if main_type in HLS_CONTENT_TYPES:
        return True
    return "mpegurl" in main_type or "m3u8" in main_type


def looks_like_hls_text(text: str | bytes | None) -> bool:
    if text is None:
        return False
    if isinstance(text, bytes):
        text = text[:4096].decode("utf-8", errors="ignore")
    sample = text[:4096].lstrip("\ufeff\r\n\t ")
    return sample.startswith("#EXTM3U")


def playlist_kind(text: str | None, url: str = "") -> str:
    if text and "#EXT-X-STREAM-INF" in text:
        return "Master"
    if text and "#EXTINF" in text:
        return "Media"
    if "master" in (url or "").lower():
        return "Likely Master"
    if "playlist" in (url or "").lower():
        return "Playlist"
    return "URL Match"


def extract_resolution(text: str | None) -> str:
    if not text:
        return ""
    resolutions = RESOLUTION_RE.findall(text)
    if not resolutions:
        return ""

    def area(res: str) -> int:
        try:
            w, h = res.lower().split("x", 1)
            return int(w) * int(h)
        except Exception:
            return 0

    unique = sorted(set(resolutions), key=area, reverse=True)
    if len(unique) == 1:
        return unique[0]
    return f"{unique[0]} +{len(unique) - 1}"


def extract_bandwidth(text: str | None) -> str:
    if not text:
        return ""
    values = []
    for item in BANDWIDTH_RE.findall(text):
        try:
            values.append(int(item))
        except ValueError:
            pass
    if not values:
        return ""
    top = max(values)
    mbps = top / 1_000_000
    return f"{mbps:.2f} Mbps"


def host_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def origin_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return ""


def rank_capture(record: dict[str, Any]) -> int:
    score = 0
    url = (record.get("m3u8_url") or "").lower()
    kind = (record.get("playlist_type") or "").lower()
    status_code = int(record.get("status_code") or 0)

    if status_code == 200:
        score += 25
    elif 200 <= status_code < 400:
        score += 15

    if "master" in kind or "master" in url:
        score += 35
    if "#EXT-X-STREAM-INF" in (record.get("playlist_sample") or ""):
        score += 35
    if record.get("resolution"):
        score += 20
    if record.get("bandwidth"):
        score += 10
    if "playlist" in url:
        score += 5
    if "chunk" in url or "segment" in url:
        score -= 15
    return max(score, 0)


def make_capture_record(
    *,
    m3u8_url: str,
    source_page: str = "",
    event_source: str = "",
    method: str = "GET",
    status_code: int | None = None,
    content_type: str = "",
    referer: str = "",
    origin: str = "",
    user_agent: str = "",
    resource_type: str = "",
    request_headers: dict[str, str] | None = None,
    response_headers: dict[str, str] | None = None,
    playlist_text: str | None = None,
) -> dict[str, Any]:
    playlist_sample = ""
    if playlist_text:
        playlist_sample = playlist_text[:3000]

    record: dict[str, Any] = {
        "id": url_fingerprint(m3u8_url),
        "captured_at": now_stamp(),
        "m3u8_url": normalize_url(m3u8_url),
        "host": host_from_url(m3u8_url),
        "source_page": source_page or "",
        "event_source": event_source or "",
        "request_method": method or "GET",
        "resource_type": resource_type or "",
        "status_code": status_code if status_code is not None else "",
        "content_type": content_type or "",
        "referer": referer or "",
        "origin": origin or origin_from_url(source_page or referer or ""),
        "user_agent": user_agent or "",
        "playlist_type": playlist_kind(playlist_text, m3u8_url),
        "resolution": extract_resolution(playlist_text),
        "bandwidth": extract_bandwidth(playlist_text),
        "headers_needed": {
            "User-Agent": user_agent or "",
            "Referer": referer or source_page or "",
            "Origin": origin or origin_from_url(source_page or referer or ""),
        },
        "request_headers": request_headers or {},
        "response_headers": response_headers or {},
        "playlist_sample": playlist_sample,
        "notes": "",
    }
    record["score"] = rank_capture(record)
    return record
