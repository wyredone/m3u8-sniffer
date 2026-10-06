from __future__ import annotations
from typing import Any
import requests
from .hls_utils import looks_like_hls_text, playlist_kind, extract_resolution, extract_bandwidth
from .download_engine import session_headers

def test_hls_record(record: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    result = dict(ok=False, status_code=None, content_type="", playlist_type="", resolution="", bandwidth="", message="", sample="")
    url = record.get("m3u8_url", "")
    if not url.startswith(("http://", "https://")):
        result["message"] = "Select an HTTP or HTTPS stream URL."
        return result
    try:
        with requests.get(url, headers=session_headers(record), timeout=timeout, allow_redirects=True, stream=True) as response:
            content_type = response.headers.get("content-type", "")
            sample = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                sample.extend(chunk[:8192-len(sample)])
                if len(sample) >= 8192:
                    break
            text = sample.decode("utf-8", errors="replace")
            kind = playlist_kind(text, response.url, content_type)
            valid = looks_like_hls_text(text)
            if "dash+xml" in content_type or response.url.split("?")[0].endswith(".mpd"):
                import xml.etree.ElementTree as ET
                try:
                    valid = ET.fromstring(text).tag.split("}")[-1] == "MPD"
                except ET.ParseError:
                    valid = False
            if kind == "Progressive Video":
                valid = "ftyp" in text[:64] or bytes(sample[:4]) == b"\x1a\x45\xdf\xa3"
            result.update(ok=response.ok and valid, status_code=response.status_code, content_type=content_type,
                          playlist_type=kind, resolution=extract_resolution(text), bandwidth=extract_bandwidth(text), sample=text[:1000])
            result["message"] = f"Validated {kind} response." if result["ok"] else f"HTTP {response.status_code}: stream could not be validated; it may be expired or require session credentials."
    except Exception as exc:
        result["message"] = f"Test failed: {exc}"
    return result
