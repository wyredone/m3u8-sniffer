from __future__ import annotations

from typing import Any

import requests

from .hls_utils import content_type_is_hls, looks_like_hls_text, playlist_kind, extract_resolution, extract_bandwidth


def test_hls_record(record: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    url = record.get("m3u8_url", "")
    headers_needed = record.get("headers_needed", {}) or {}

    headers = {}
    user_agent = headers_needed.get("User-Agent") or record.get("user_agent")
    referer = headers_needed.get("Referer") or record.get("referer") or record.get("source_page")
    origin = headers_needed.get("Origin") or record.get("origin")

    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin

    result = {
        "ok": False,
        "status_code": None,
        "content_type": "",
        "playlist_type": "",
        "resolution": "",
        "bandwidth": "",
        "message": "",
        "sample": "",
    }

    if not url:
        result["message"] = "No URL selected."
        return result

    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        content_type = response.headers.get("content-type", "")
        text = response.text[:5000]

        result.update(
            {
                "status_code": response.status_code,
                "content_type": content_type,
                "playlist_type": playlist_kind(text, url),
                "resolution": extract_resolution(text),
                "bandwidth": extract_bandwidth(text),
                "sample": text[:1000],
            }
        )

        if response.ok and (looks_like_hls_text(text) or content_type_is_hls(content_type)):
            result["ok"] = True
            result["message"] = "Valid HLS playlist response."
        elif response.ok:
            result["message"] = "URL responded, but the response did not look like an HLS playlist. It may require cookies, timing, or a different referer."
        else:
            result["message"] = f"HTTP {response.status_code}. The URL may be expired, blocked, or require browser session cookies."
    except Exception as exc:
        result["message"] = f"Test failed: {exc}"

    return result
