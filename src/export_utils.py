from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

EXPORT_COLUMNS = [
    "captured_at",
    "score",
    "playlist_type",
    "status_code",
    "resolution",
    "bandwidth",
    "host",
    "m3u8_url",
    "source_page",
    "referer",
    "origin",
    "user_agent",
    "content_type",
    "event_source",
]


def export_json(records: list[dict[str, Any]], path: str | Path) -> None:
    Path(path).write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def export_csv(records: list[dict[str, Any]], path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)
