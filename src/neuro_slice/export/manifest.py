"""Helpers for reading and writing song segment manifests."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def _json_default(value: Any) -> Any:
    """Convert common Python objects into JSON-serializable values."""
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _to_plain_data(value: Any) -> Any:
    """Recursively normalize arbitrary objects to plain Python data."""
    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump()
    elif is_dataclass(value):
        value = asdict(value)
    elif isinstance(value, Path):
        return str(value)
    elif isinstance(value, (datetime, date)):
        return value.isoformat()
    elif isinstance(value, Enum):
        return value.value

    if isinstance(value, Mapping):
        return {str(k): _to_plain_data(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_plain_data(item) for item in value]

    return value


def _coerce_segment(segment: Any, index: int | None = None) -> dict[str, Any]:
    """Convert a segment-like object into a normalized manifest record."""
    record = _to_plain_data(segment)

    if not isinstance(record, dict):
        raise TypeError(
            "Each segment must be a mapping, dataclass, or Pydantic model. "
            f"Got {type(segment).__name__}."
        )

    normalized: dict[str, Any] = dict(record)

    if index is not None and "index" not in normalized:
        normalized["index"] = index

    if "start" in normalized:
        normalized["start"] = float(normalized["start"])
    if "end" in normalized:
        normalized["end"] = float(normalized["end"])

    if "duration" not in normalized and "start" in normalized and "end" in normalized:
        normalized["duration"] = max(0.0, float(normalized["end"]) - float(normalized["start"]))

    if "title" not in normalized or normalized["title"] in (None, ""):
        seg_index = normalized.get("index", index)
        if seg_index is not None:
            normalized["title"] = f"song_{int(seg_index):02d}"
        else:
            normalized["title"] = "unknown_song"

    if "export" not in normalized:
        normalized["export"] = True

    return normalized


def build_manifest(
    segments: Sequence[Any],
    *,
    source_video: str | Path | None = None,
    generated_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a manifest object from a sequence of segment-like items."""
    manifest: dict[str, Any] = {
        "source_video": str(source_video) if source_video is not None else None,
        "generated_at": (generated_at or datetime.now()).isoformat(),
        "count": len(segments),
        "segments": [_coerce_segment(segment, index=i) for i, segment in enumerate(segments, start=1)],
    }

    if metadata:
        manifest["metadata"] = _to_plain_data(dict(metadata))

    return manifest


def write_manifest_json(
    path: str | Path,
    segments: Sequence[Any],
    *,
    source_video: str | Path | None = None,
    generated_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> Path:
    """Write a manifest JSON file and return its path."""
    manifest = build_manifest(
        segments,
        source_video=source_video,
        generated_at=generated_at,
        metadata=metadata,
    )

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=indent, ensure_ascii=ensure_ascii, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_manifest_json(path: str | Path) -> dict[str, Any]:
    """Load a manifest JSON file."""
    manifest_path = Path(path)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def iter_manifest_segments(manifest: Mapping[str, Any] | Sequence[Any]) -> Iterable[dict[str, Any]]:
    """Yield normalized segment records from a full manifest or raw segment list."""
    if isinstance(manifest, Mapping):
        raw_segments = manifest.get("segments", [])
    else:
        raw_segments = manifest

    for index, segment in enumerate(raw_segments, start=1):
        yield _coerce_segment(segment, index=index)


def write_manifest_csv(
    path: str | Path,
    manifest: Mapping[str, Any] | Sequence[Any],
    *,
    fieldnames: Sequence[str] | None = None,
) -> Path:
    """Write manifest segments to a CSV file."""
    rows = list(iter_manifest_segments(manifest))
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fieldnames is None:
        ordered_keys: list[str] = []
        seen: set[str] = set()
        preferred = [
            "index",
            "title",
            "start",
            "end",
            "duration",
            "confidence",
            "export",
            "artist",
            "source",
            "notes",
        ]
        for key in preferred:
            if any(key in row for row in rows):
                ordered_keys.append(key)
                seen.add(key)
        for row in rows:
            for key in row:
                if key not in seen:
                    ordered_keys.append(key)
                    seen.add(key)
        fieldnames = ordered_keys

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            flat_row = {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list, tuple))
                else value
                for key, value in row.items()
            }
            writer.writerow(flat_row)

    return output_path


def read_manifest_segments(path: str | Path) -> list[dict[str, Any]]:
    """Read only the normalized segment list from a manifest JSON file."""
    manifest = load_manifest_json(path)
    return list(iter_manifest_segments(manifest))