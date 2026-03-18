from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Any


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class TranscriptResult:
    text: str
    language: str | None
    language_probability: float | None
    duration: float | None
    segments: list[TranscriptSegment] = field(default_factory=list)


@dataclass(slots=True)
class FasterWhisperTranscriberConfig:
    model_size: str = "large-v3"
    device: str = "auto"
    compute_type: str | None = None
    cpu_threads: int = 0
    num_workers: int = 1
    language: str | None = None
    beam_size: int = 5
    best_of: int = 5
    vad_filter: bool = False
    vad_parameters: dict[str, Any] | None = None
    condition_on_previous_text: bool = False
    initial_prompt: str | None = "Lyrics of the song."
    repetition_penalty: float = 1.1
    no_speech_threshold: float = 0.6
    log_progress: bool = True

    def resolved_device(self) -> str:
        if self.device != "auto":
            return self.device
        return "cuda" if which("nvidia-smi") else "cpu"

    def resolved_compute_type(self) -> str:
        if self.compute_type:
            return self.compute_type
        return "float16" if self.resolved_device() == "cuda" else "int8"


class FasterWhisperTranscriber:
    def __init__(self, config: FasterWhisperTranscriberConfig | None = None) -> None:
        self.config = config or FasterWhisperTranscriberConfig()
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Install the package before using transcription."
            ) from exc

        device = self.config.resolved_device()
        compute_type = self.config.resolved_compute_type()

        if self.config.log_progress:
            print(
                f"🎙️ Loading faster-whisper model "
                f"({self.config.model_size} / {device} / {compute_type})..."
            )

        kwargs: dict[str, Any] = {
            "device": device,
            "compute_type": compute_type,
            "num_workers": self.config.num_workers,
        }
        if self.config.cpu_threads > 0:
            kwargs["cpu_threads"] = self.config.cpu_threads

        self._model = WhisperModel(self.config.model_size, **kwargs)
        return self._model

    def transcribe(self, audio_path: str | Path) -> TranscriptResult:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        model = self._load_model()

        if self.config.log_progress:
            print(f"📝 Transcribing: {audio_path.name}")

        transcribe_kwargs: dict[str, Any] = {
            "beam_size": self.config.beam_size,
            "best_of": self.config.best_of,
            "vad_filter": self.config.vad_filter,
            "condition_on_previous_text": self.config.condition_on_previous_text,
            "repetition_penalty": self.config.repetition_penalty,
            "no_speech_threshold": self.config.no_speech_threshold,
        }

        if self.config.language:
            transcribe_kwargs["language"] = self.config.language
        if self.config.initial_prompt:
            transcribe_kwargs["initial_prompt"] = self.config.initial_prompt
        if self.config.vad_parameters is not None:
            transcribe_kwargs["vad_parameters"] = self.config.vad_parameters

        raw_segments, info = model.transcribe(str(audio_path), **transcribe_kwargs)

        segments: list[TranscriptSegment] = []
        text_parts: list[str] = []

        for segment in raw_segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=text,
                )
            )
            text_parts.append(text)

        text = " ".join(text_parts).strip()
        language = getattr(info, "language", None)
        language_probability = getattr(info, "language_probability", None)
        duration = getattr(info, "duration", None)

        if self.config.log_progress:
            lang_display = language or "unknown"
            print(
                f"✅ Transcription complete | language={lang_display} "
                f"| chars={len(text)} | segments={len(segments)}"
            )

        return TranscriptResult(
            text=text,
            language=language,
            language_probability=language_probability,
            duration=duration,
            segments=segments,
        )


def lyrics_only_text(
    result: TranscriptResult,
    forbidden_fragments: list[str] | None = None,
) -> str:
    forbidden_fragments = forbidden_fragments or [
        "这首歌曲",
        "准确转录",
        "不要包含",
        "旁白",
        "watching",
    ]

    kept: list[str] = []
    for segment in result.segments:
        if any(fragment in segment.text for fragment in forbidden_fragments):
            continue
        kept.append(segment.text)

    return " ".join(kept).strip()


__all__ = [
    "FasterWhisperTranscriber",
    "FasterWhisperTranscriberConfig",
    "TranscriptResult",
    "TranscriptSegment",
    "lyrics_only_text",
]