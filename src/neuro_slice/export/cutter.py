from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from neuro_slice.export.manifest import read_manifest_segments


INVALID_FILENAME_CHARS = r'\/:*?"<>|'
_FILENAME_TRANSLATION = str.maketrans({char: "_" for char in INVALID_FILENAME_CHARS})


@dataclass(slots=True)
class ClipExportResult:
    index: int
    title: str
    start: float
    end: float
    output_path: Path
    command: list[str]


def sanitize_filename(value: str, fallback: str = "untitled") -> str:
    text = (value or "").translate(_FILENAME_TRANSLATION)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    return text or fallback


def format_seconds(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def compact_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def _get_field(segment: Any, name: str, default: Any = None) -> Any:
    if isinstance(segment, dict):
        return segment.get(name, default)
    return getattr(segment, name, default)


def _segment_title(segment: Any, index: int) -> str:
    title = _get_field(segment, "title")
    guessed = _get_field(segment, "guessed_title")
    song_name = _get_field(segment, "song_name")
    chosen = title or guessed or song_name or f"song_{index:02d}"
    return str(chosen)


def build_output_stem(
    segment: Any,
    index: int,
    filename_pattern: str = "[{index:02d}] {title}",
    date: str = "",
    singer: str = "",
) -> str:
    title = sanitize_filename(_segment_title(segment, index))
    start = float(_get_field(segment, "start", 0.0) or 0.0)
    end = float(_get_field(segment, "end", 0.0) or 0.0)

    mapping = {
        "index": index,
        "title": title,
        "date": date,
        "singer": singer,
        "start": compact_timestamp(start),
        "end": compact_timestamp(end),
    }
    stem = filename_pattern.format(**mapping)
    return sanitize_filename(stem, fallback=f"song_{index:02d}")


def _run_ffmpeg(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or "ffmpeg failed without output"
        raise RuntimeError(details)


def _base_ffmpeg_command(
    input_path: Path,
    start: float,
    end: float,
) -> list[str]:
    if end <= start:
        raise ValueError(f"invalid segment range: start={start}, end={end}")

    return [
        "ffmpeg",
        "-y",
        "-ss",
        format_seconds(start),
        "-to",
        format_seconds(end),
        "-i",
        str(input_path),
    ]


def export_video_clip(
    input_path: str | Path,
    output_path: str | Path,
    start: float,
    end: float,
    video_codec: str = "copy",
    audio_codec: str = "copy",
    extra_args: Iterable[str] | None = None,
) -> Path:
    src = Path(input_path)
    dst = Path(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    command = _base_ffmpeg_command(src, start, end)

    if video_codec == "copy":
        command += ["-c:v", "copy"]
    else:
        command += ["-c:v", video_codec]

    if audio_codec == "copy":
        command += ["-c:a", "copy"]
    else:
        command += ["-c:a", audio_codec]

    if extra_args:
        command.extend(extra_args)

    command.append(str(dst))
    _run_ffmpeg(command)
    return dst


def export_audio_clip(
    input_path: str | Path,
    output_path: str | Path,
    start: float,
    end: float,
    audio_codec: str = "libmp3lame",
    bitrate: str = "320k",
    extra_args: Iterable[str] | None = None,
) -> Path:
    src = Path(input_path)
    dst = Path(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    command = _base_ffmpeg_command(src, start, end)
    command += ["-vn", "-c:a", audio_codec]

    if bitrate:
        command += ["-b:a", bitrate]

    if extra_args:
        command.extend(extra_args)

    command.append(str(dst))
    _run_ffmpeg(command)
    return dst


def export_segment(
    input_path: str | Path,
    output_dir: str | Path,
    segment: Any,
    index: int,
    *,
    export_video: bool = True,
    export_audio: bool = False,
    video_container: str = "mp4",
    audio_format: str = "mp3",
    video_codec: str = "copy",
    audio_codec: str = "copy",
    audio_bitrate: str = "320k",
    filename_pattern: str = "[{index:02d}] {title}",
    date: str = "",
    singer: str = "",
    video_extra_args: Iterable[str] | None = None,
    audio_extra_args: Iterable[str] | None = None,
) -> list[ClipExportResult]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = float(_get_field(segment, "start", 0.0) or 0.0)
    end = float(_get_field(segment, "end", 0.0) or 0.0)
    title = _segment_title(segment, index)
    stem = build_output_stem(
        segment=segment,
        index=index,
        filename_pattern=filename_pattern,
        date=date,
        singer=singer,
    )

    results: list[ClipExportResult] = []

    if export_video:
        video_path = out_dir / f"{stem}.{video_container.lstrip('.')}"
        command = _base_ffmpeg_command(Path(input_path), start, end)
        if video_codec == "copy":
            command += ["-c:v", "copy"]
        else:
            command += ["-c:v", video_codec]
        if audio_codec == "copy":
            command += ["-c:a", "copy"]
        else:
            command += ["-c:a", audio_codec]
        if video_extra_args:
            command.extend(video_extra_args)
        command.append(str(video_path))

        _run_ffmpeg(command)
        results.append(
            ClipExportResult(
                index=index,
                title=title,
                start=start,
                end=end,
                output_path=video_path,
                command=command,
            )
        )

    if export_audio:
        audio_path = out_dir / f"{stem}.{audio_format.lstrip('.')}"
        command = _base_ffmpeg_command(Path(input_path), start, end)
        command += ["-vn", "-c:a", "libmp3lame" if audio_format.lower() == "mp3" else audio_codec]
        if audio_bitrate:
            command += ["-b:a", audio_bitrate]
        if audio_extra_args:
            command.extend(audio_extra_args)
        command.append(str(audio_path))

        _run_ffmpeg(command)
        results.append(
            ClipExportResult(
                index=index,
                title=title,
                start=start,
                end=end,
                output_path=audio_path,
                command=command,
            )
        )

    return results


def export_segments(
    input_path: str | Path,
    output_dir: str | Path,
    segments: Iterable[Any],
    **kwargs: Any,
) -> list[ClipExportResult]:
    all_results: list[ClipExportResult] = []

    for index, segment in enumerate(segments, start=1):
        all_results.extend(
            export_segment(
                input_path=input_path,
                output_dir=output_dir,
                segment=segment,
                index=index,
                **kwargs,
            )
        )

    return all_results


def export_from_manifest(
    video_path: str | Path,
    manifest_path: str | Path,
    output_dir: str | Path,
    config: Any,
) -> list[Path]:
    segments = [
        segment
        for segment in read_manifest_segments(manifest_path)
        if bool(segment.get("export", True)) and not bool(segment.get("skipped", False))
    ]

    results = export_segments(
        input_path=video_path,
        output_dir=output_dir,
        segments=segments,
        export_video=bool(getattr(config.export, "export_video", True)),
        export_audio=bool(getattr(config.export, "export_audio", False)),
        video_container=str(getattr(config.export, "video_container", "mp4")),
        audio_format=str(getattr(config.export, "audio_format", "mp3")),
        video_codec=str(getattr(config.export, "video_codec", "copy")),
        audio_codec=str(getattr(config.export, "audio_codec", "copy")),
        filename_pattern=str(getattr(config.export, "filename_pattern", "[{index:02d}] {title}")),
        date=str(getattr(config.metadata, "date", "")),
        singer=str(getattr(config.metadata, "singer", "")),
    )

    return [item.output_path for item in results]