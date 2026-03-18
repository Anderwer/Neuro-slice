from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe command fails."""


def _stringify_command(command: Sequence[str]) -> str:
    return " ".join(str(part) for part in command)


def _run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise FFmpegError(
            f"Command failed with exit code {result.returncode}: {_stringify_command(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def ensure_ffmpeg_available() -> None:
    """Ensure both ffmpeg and ffprobe are available on PATH."""
    missing: list[str] = []
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            missing.append(binary)

    if missing:
        joined = ", ".join(missing)
        raise FFmpegError(f"Missing required binaries: {joined}. Please install FFmpeg and add it to PATH.")


def probe_duration(input_path: str | Path) -> float:
    """Return media duration in seconds."""
    ensure_ffmpeg_available()
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ]
    )
    output = result.stdout.strip()
    if not output:
        raise FFmpegError(f"Could not read duration for: {input_path}")
    try:
        return float(output)
    except ValueError as exc:
        raise FFmpegError(f"Invalid duration returned by ffprobe for {input_path!s}: {output!r}") from exc


def probe_has_audio_stream(input_path: str | Path) -> bool:
    """Return True if the media file contains at least one audio stream."""
    ensure_ffmpeg_available()
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(input_path),
        ],
        check=False,
    )
    return bool(result.stdout.strip())


def seconds_to_timestamp(seconds: float) -> str:
    """Convert seconds to an ffmpeg-compatible HH:MM:SS.mmm timestamp."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def extract_audio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    start: float | None = None,
    end: float | None = None,
    sample_rate: int = 16000,
    channels: int = 1,
    audio_filter: str | None = None,
    overwrite: bool = True,
) -> Path:
    """Extract a mono/stereo audio track to a WAV file for analysis."""
    ensure_ffmpeg_available()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = ["ffmpeg"]
    command.append("-y" if overwrite else "-n")
    command.extend(["-hide_banner", "-loglevel", "error", "-i", str(input_path)])

    if start is not None:
        command.extend(["-ss", seconds_to_timestamp(start)])
    if end is not None:
        command.extend(["-to", seconds_to_timestamp(end)])

    command.extend(["-vn"])

    if audio_filter:
        command.extend(["-af", audio_filter])

    command.extend(
        [
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            str(output_path),
        ]
    )

    _run(command)
    return output_path


def cut_clip(
    input_path: str | Path,
    output_path: str | Path,
    *,
    start: float,
    end: float,
    video_codec: str | None = "copy",
    audio_codec: str | None = "copy",
    overwrite: bool = True,
    extra_args: Sequence[str] | None = None,
) -> Path:
    """Cut a media clip between start and end."""
    ensure_ffmpeg_available()

    if end <= start:
        raise ValueError(f"Clip end must be greater than start. Got start={start}, end={end}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = ["ffmpeg"]
    command.append("-y" if overwrite else "-n")
    command.extend(
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            seconds_to_timestamp(start),
            "-to",
            seconds_to_timestamp(end),
            "-i",
            str(input_path),
        ]
    )

    if video_codec is not None:
        command.extend(["-c:v", video_codec])
    if audio_codec is not None:
        command.extend(["-c:a", audio_codec])
    if extra_args:
        command.extend(str(arg) for arg in extra_args)

    command.append(str(output_path))
    _run(command)
    return output_path


def export_audio_clip(
    input_path: str | Path,
    output_path: str | Path,
    *,
    start: float,
    end: float,
    audio_codec: str = "libmp3lame",
    quality: str | None = "0",
    overwrite: bool = True,
    extra_args: Sequence[str] | None = None,
) -> Path:
    """Export an audio-only clip such as MP3."""
    ensure_ffmpeg_available()

    if end <= start:
        raise ValueError(f"Clip end must be greater than start. Got start={start}, end={end}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = ["ffmpeg"]
    command.append("-y" if overwrite else "-n")
    command.extend(
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            seconds_to_timestamp(start),
            "-to",
            seconds_to_timestamp(end),
            "-i",
            str(input_path),
            "-vn",
            "-map",
            "a",
            "-c:a",
            audio_codec,
        ]
    )

    if quality is not None:
        command.extend(["-q:a", str(quality)])
    if extra_args:
        command.extend(str(arg) for arg in extra_args)

    command.append(str(output_path))
    _run(command)
    return output_path


def sanitize_filename(value: str, replacement: str = "_") -> str:
    """Remove characters that are invalid on Windows/macOS/Linux file systems."""
    invalid = set('\\/:*?"<>|')
    cleaned = "".join(replacement if ch in invalid else ch for ch in value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or "untitled"