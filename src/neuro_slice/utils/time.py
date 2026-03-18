from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Simple inclusive-exclusive time range in seconds."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def pad(self, before: float = 0.0, after: float = 0.0, minimum_start: float = 0.0) -> "TimeRange":
        return TimeRange(
            start=max(minimum_start, self.start - before),
            end=max(minimum_start, self.end + after),
        )

    def clamp(self, minimum: float = 0.0, maximum: float | None = None) -> "TimeRange":
        start = max(minimum, self.start)
        end = max(minimum, self.end)
        if maximum is not None:
            start = min(start, maximum)
            end = min(end, maximum)
        if end < start:
            end = start
        return TimeRange(start=start, end=end)

    def overlaps_or_touches(self, other: "TimeRange", gap_tolerance: float = 0.0) -> bool:
        return other.start <= self.end + gap_tolerance and self.start <= other.end + gap_tolerance

    def merge(self, other: "TimeRange") -> "TimeRange":
        return TimeRange(start=min(self.start, other.start), end=max(self.end, other.end))


def clamp_seconds(value: float, minimum: float = 0.0, maximum: float | None = None) -> float:
    """Clamp seconds to a valid range."""
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def sec_to_timestamp(seconds: float) -> str:
    """Convert seconds to ffmpeg-friendly HH:MM:SS.mmm timestamp."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def seconds_to_filename_stamp(seconds: float) -> str:
    """Convert seconds to a filename-safe HH-MM-SS string."""
    seconds = int(max(0, round(seconds)))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def format_duration(seconds: float) -> str:
    """Human-readable duration string like 03:41 or 01:03:41."""
    total_seconds = int(max(0, round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timestamp(value: str) -> float:
    """
    Parse a timestamp into seconds.

    Supported formats:
    - SS
    - MM:SS
    - HH:MM:SS
    - HH:MM:SS.mmm
    - MM:SS.mmm
    """
    text = value.strip()
    if not text:
        raise ValueError("Timestamp cannot be empty.")

    parts = text.split(":")
    if len(parts) == 1:
        return float(parts[0])

    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds

    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds

    raise ValueError(f"Unsupported timestamp format: {value!r}")


def merge_ranges(ranges: list[TimeRange], gap_tolerance: float = 0.0) -> list[TimeRange]:
    """Merge overlapping or near-adjacent ranges."""
    if not ranges:
        return []

    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    merged: list[TimeRange] = [ordered[0]]

    for current in ordered[1:]:
        previous = merged[-1]
        if previous.overlaps_or_touches(current, gap_tolerance=gap_tolerance):
            merged[-1] = previous.merge(current)
        else:
            merged.append(current)

    return merged