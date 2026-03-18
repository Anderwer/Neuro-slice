from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TimeRange(BaseModel):
    start: float = Field(..., ge=0.0, description="Start time in seconds.")
    end: float = Field(..., ge=0.0, description="End time in seconds.")

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @model_validator(mode="after")
    def validate_range(self) -> "TimeRange":
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class TranscriptSegment(TimeRange):
    text: str = Field(default="", description="Transcript text for the segment.")
    language: str | None = Field(default=None, description="Detected language, if any.")
    avg_logprob: float | None = Field(default=None)
    no_speech_prob: float | None = Field(default=None)


class RecognitionResult(BaseModel):
    title: str | None = Field(default=None, description="Detected or guessed song title.")
    artist: str | None = Field(default=None, description="Detected or guessed artist.")
    raw_response: str | None = Field(default=None, description="Raw provider response.")
    provider: str | None = Field(default=None, description="Recognition backend name.")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    matched: bool = Field(default=False)

    @property
    def display_name(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        if self.title:
            return self.title
        if self.raw_response:
            return self.raw_response
        return "Unknown Song"


class SongSegment(TimeRange):
    index: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: list[str] = Field(default_factory=list)
    title: str | None = Field(default=None)
    artist: str | None = Field(default=None)
    transcript: str | None = Field(default=None)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    recognition: RecognitionResult | None = Field(default=None)
    skipped: bool = Field(default=False)
    skip_reason: str | None = Field(default=None)
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def display_title(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        if self.title:
            return self.title
        return f"song_{(self.index or 0):02d}"

    def with_padding(self, before: float, after: float, max_end: float | None = None) -> "SongSegment":
        start = max(0.0, self.start - max(0.0, before))
        end = self.end + max(0.0, after)
        if max_end is not None:
            end = min(end, max_end)
        data = self.model_dump()
        data["start"] = start
        data["end"] = max(start, end)
        return SongSegment.model_validate(data)


class DetectionWindow(TimeRange):
    score: float = Field(..., ge=0.0, le=1.0)
    energy: float | None = Field(default=None)
    speech_ratio: float | None = Field(default=None)
    music_ratio: float | None = Field(default=None)


class MediaInfo(BaseModel):
    video_path: Path
    duration: float = Field(..., ge=0.0)
    has_video: bool = True
    has_audio: bool = True
    sample_rate: int | None = Field(default=None, ge=1)
    channels: int | None = Field(default=None, ge=1)

    @field_validator("video_path", mode="before")
    @classmethod
    def normalize_video_path(cls, value: str | Path) -> Path:
        return Path(value)


class ExportedArtifact(BaseModel):
    kind: Literal["video", "audio", "manifest", "transcript", "other"]
    path: Path
    segment_index: int | None = Field(default=None, ge=1)
    title: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value: str | Path) -> Path:
        return Path(value)


class ProcessingReport(BaseModel):
    input_file: Path
    output_dir: Path
    media: MediaInfo | None = None
    detected_segments: list[SongSegment] = Field(default_factory=list)
    exported_files: list[ExportedArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("input_file", "output_dir", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        return Path(value)

    @property
    def exported_count(self) -> int:
        return len(self.exported_files)

    @property
    def kept_segments(self) -> list[SongSegment]:
        return [segment for segment in self.detected_segments if not segment.skipped]


class Manifest(BaseModel):
    version: str = "1"
    project: str = "neuro-slice"
    input_file: str
    output_dir: str
    date: str | None = None
    singer: str | None = None
    segments: list[SongSegment] = Field(default_factory=list)

    @classmethod
    def from_report(
        cls,
        report: ProcessingReport,
        *,
        date: str | None = None,
        singer: str | None = None,
    ) -> "Manifest":
        return cls(
            input_file=str(report.input_file),
            output_dir=str(report.output_dir),
            date=date,
            singer=singer,
            segments=report.detected_segments,
        )