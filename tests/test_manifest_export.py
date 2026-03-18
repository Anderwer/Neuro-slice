from __future__ import annotations

import json
from pathlib import Path

from neuro_slice.export.cutter import build_output_stem, sanitize_filename
from neuro_slice.export.manifest import (
    build_manifest,
    read_manifest_segments,
    write_manifest_csv,
    write_manifest_json,
)


def test_build_manifest_populates_defaults() -> None:
    manifest = build_manifest(
        [
            {"start": 12.5, "end": 42.0, "confidence": 0.91},
            {"start": 60.0, "end": 120.0, "title": "Existing Title", "export": False},
        ],
        source_video="vod.mp4",
        metadata={"date": "2025-08-21", "singer": "Evil"},
    )

    assert manifest["source_video"] == "vod.mp4"
    assert manifest["count"] == 2
    assert "generated_at" in manifest

    first = manifest["segments"][0]
    second = manifest["segments"][1]

    assert first["index"] == 1
    assert first["duration"] == 29.5
    assert first["title"] == "song_01"
    assert first["export"] is True

    assert second["index"] == 2
    assert second["duration"] == 60.0
    assert second["title"] == "Existing Title"
    assert second["export"] is False

    assert manifest["metadata"]["date"] == "2025-08-21"
    assert manifest["metadata"]["singer"] == "Evil"


def test_write_manifest_json_and_read_segments_roundtrip(tmp_path: Path) -> None:
    manifest_path = tmp_path / "songs.json"

    write_manifest_json(
        manifest_path,
        [
            {"start": 1.0, "end": 11.25, "title": "song_01"},
            {"start": 20.0, "end": 55.5, "confidence": 0.8},
        ],
        source_video="archive.mp4",
    )

    assert manifest_path.exists()

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["source_video"] == "archive.mp4"
    assert payload["count"] == 2

    segments = read_manifest_segments(manifest_path)
    assert len(segments) == 2

    assert segments[0]["index"] == 1
    assert segments[0]["title"] == "song_01"
    assert segments[0]["duration"] == 10.25
    assert segments[0]["export"] is True

    assert segments[1]["index"] == 2
    assert segments[1]["title"] == "song_02"
    assert segments[1]["duration"] == 35.5
    assert segments[1]["export"] is True


def test_write_manifest_csv_contains_expected_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "songs.csv"

    manifest = build_manifest(
        [
            {"start": 5.0, "end": 25.0, "title": "Alpha", "confidence": 0.77},
            {"start": 30.0, "end": 70.0, "artist": "Neuro-sama"},
        ],
        source_video="vod.mp4",
    )

    write_manifest_csv(csv_path, manifest)

    text = csv_path.read_text(encoding="utf-8")
    assert "index,title,start,end,duration,confidence,export,artist" in text
    assert "Alpha" in text
    assert "Neuro-sama" in text


def test_build_output_stem_uses_manifest_fields_and_sanitizes() -> None:
    segment = {
        "index": 3,
        "start": 125.2,
        "end": 402.8,
        "title": 'Bad:Apple?/Test',
    }

    stem = build_output_stem(
        segment=segment,
        index=3,
        filename_pattern="[{index:02d}] {title} {start}_{end}",
        date="2025-08-21",
        singer="Evil",
    )

    assert stem == "[03] Bad_Apple__Test 00-02-05_00-06-42"


def test_sanitize_filename_returns_fallback_for_empty_values() -> None:
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename('  .<>:"/\\|?*  ') == "untitled"