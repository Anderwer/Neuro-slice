from __future__ import annotations

from pathlib import Path
from typing import Literal

import tomllib
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProjectConfig(BaseConfigModel):
    name: str = "Neuro-slice"
    output_dir: str = "output"


class InputConfig(BaseConfigModel):
    video_path: str = "vod.mp4"


class DetectorConfig(BaseConfigModel):
    backend: Literal["heuristic", "legacy-subprocess", "legacy-wsl"] = "legacy-wsl"
    auto_setup_legacy_env: bool = True
    legacy_env_dir: str = ".venv_legacy"
    legacy_python_path: str = ""
    legacy_script_path: str = "runtime/detect_segments_tf.py"
    wsl_distro: str = "Ubuntu"
    wsl_legacy_python_path: str = "/root/.neuro-slice-legacy/bin/python"
    wsl_legacy_script_path: str = "/root/neuro-slice-runtime/detect_segments_tf.py"
    wsl_wrapper_script_path: str = "/root/neuro-slice-runtime/detect_segments_wsl.sh"
    auto_setup_legacy_wsl_env: bool = True
    min_music_duration: int = Field(default=20, gt=0)
    merge_gap_seconds: float = Field(default=90.0, ge=0)
    chunk_enabled: bool = False
    chunk_seconds: float = Field(default=300.0, gt=0)
    chunk_overlap_seconds: float = Field(default=10.0, ge=0)


class SegmentConfig(BaseConfigModel):
    window_seconds: float = Field(default=2.0, gt=0)
    hop_seconds: float = Field(default=1.0, gt=0)
    min_song_duration: float = Field(default=45.0, gt=0)
    max_song_duration: float = Field(default=420.0, gt=0)
    merge_gap_seconds: float = Field(default=10.0, ge=0)
    padding_before: float = Field(default=7.0, ge=0)
    padding_after: float = Field(default=7.0, ge=0)

    @field_validator("max_song_duration")
    @classmethod
    def validate_max_duration(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("max_song_duration must be greater than 0")
        return value


class AudioConfig(BaseConfigModel):
    sample_rate: int = Field(default=16000, gt=0)
    channels: int = Field(default=1, gt=0)
    preprocess_filter: str = "highpass=f=120,lowpass=f=5000"


class ASRConfig(BaseConfigModel):
    enabled: bool = True
    provider: Literal["faster-whisper"] = "faster-whisper"
    model_size: str = "large-v3"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = ""
    language: str = ""
    beam_size: int = Field(default=5, gt=0)
    best_of: int = Field(default=5, gt=0)
    vad_filter: bool = False
    condition_on_previous_text: bool = False
    initial_prompt: str = "Lyrics of the song."


class RecognitionConfig(BaseConfigModel):
    enabled: bool = False
    mode: Literal["none", "openai"] = "none"
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-4o-mini"
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    max_lyrics_chars: int = Field(default=2500, gt=0)
    api_sleep_seconds: float = Field(default=1.5, ge=0)


class ExportConfig(BaseConfigModel):
    export_video: bool = True
    export_audio: bool = False
    video_container: str = "mp4"
    audio_format: str = "mp3"
    video_codec: str = "h264_nvenc"
    audio_codec: str = "aac"
    filename_pattern: str = "[{singer}] {title} ({date})"


class MetadataConfig(BaseConfigModel):
    date: str = "1970-01-01"
    singer: str = "Unknown"


class ReviewConfig(BaseConfigModel):
    write_manifest: bool = True
    manifest_name: str = "songs.json"


class LoggingConfig(BaseConfigModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    rich: bool = True


class AppConfig(BaseConfigModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    input: InputConfig = Field(default_factory=InputConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    segment: SegmentConfig = Field(default_factory=SegmentConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    recognition: RecognitionConfig = Field(default_factory=RecognitionConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    metadata: MetadataConfig = Field(default_factory=MetadataConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("segment")
    @classmethod
    def validate_segment(cls, value: SegmentConfig) -> SegmentConfig:
        if value.max_song_duration < value.min_song_duration:
            raise ValueError("segment.max_song_duration must be >= segment.min_song_duration")
        if value.hop_seconds > value.window_seconds:
            raise ValueError("segment.hop_seconds must be <= segment.window_seconds")
        return value

    @property
    def output_path(self) -> Path:
        return Path(self.project.output_dir)

    @property
    def input_path(self) -> Path:
        return Path(self.input.video_path)


def load_config(config_path: str | Path | None = None) -> AppConfig:
    if config_path is None:
        return AppConfig()

    path = Path(config_path)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return AppConfig.model_validate(data)


def save_example_dict() -> dict:
    return AppConfig().model_dump(mode="json", exclude_none=True)