from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class FFmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe cannot be found in PATH."""


class MediaCommandError(RuntimeError):
    """Raised when an ffmpeg/ffprobe command fails."""


def ensure_ffmpeg_installed() -> None:
    """Ensure both ffmpeg and ffprobe are available in PATH."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        joined = ", ".join(missing)
        raise FFmpegNotFoundError(
            f"Required executable(s) not found in PATH: {joined}. Please install FFmpeg."
        )


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and raise a readable error on failure."""
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or "Unknown command failure."
        raise MediaCommandError(details) from exc


def sec_to_timestamp(seconds: float) -> str:
    """Convert seconds to an ffmpeg-friendly HH:MM:SS.mmm timestamp."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def get_media_duration(media_path: str | Path) -> float:
    """Return media duration in seconds using ffprobe."""
    ensure_ffmpeg_installed()
    path = str(Path(media_path))
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
    )
    value = result.stdout.strip()
    if not value:
        raise MediaCommandError(f"Could not read duration for media file: {path}")
    return float(value)


def extract_audio(
    video_path: str | Path,
    output_audio_path: str | Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    audio_filter: str | None = None,
    start: float | None = None,
    end: float | None = None,
    overwrite: bool = True,
) -> Path:
    """
    Extract audio from a media file into a WAV file suitable for ASR / analysis.

    Parameters
    ----------
    video_path:
        Input video or audio file.
    output_audio_path:
        Destination path for the extracted WAV file.
    sample_rate:
        Output sample rate.
    channels:
        Number of output channels.
    audio_filter:
        Optional ffmpeg audio filter chain.
    start:
        Optional segment start time in seconds.
    end:
        Optional segment end time in seconds.
    overwrite:
        Whether to overwrite the target file if it already exists.
    """
    ensure_ffmpeg_installed()

    src = str(Path(video_path))
    dst_path = Path(output_audio_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = ["ffmpeg"]
    command.append("-y" if overwrite else "-n")
    command.extend(["-i", src])

    if start is not None:
        command.extend(["-ss", sec_to_timestamp(start)])
    if end is not None:
        command.extend(["-to", sec_to_timestamp(end)])

    command.extend(["-vn"])

    if audio_filter:
        command.extend(["-af", audio_filter])

    command.extend(
        [
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            str(dst_path),
        ]
    )

    run_command(command)
    return dst_path


def extract_audio_segment(
    video_path: str | Path,
    output_audio_path: str | Path,
    start: float,
    end: float,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    audio_filter: str | None = None,
    overwrite: bool = True,
) -> Path:
    """Convenience wrapper for extracting a bounded audio segment."""
    if end <= start:
        raise ValueError(f"Invalid segment range: start={start}, end={end}")

    return extract_audio(
        video_path=video_path,
        output_audio_path=output_audio_path,
        sample_rate=sample_rate,
        channels=channels,
        audio_filter=audio_filter,
        start=start,
        end=end,
        overwrite=overwrite,
    )