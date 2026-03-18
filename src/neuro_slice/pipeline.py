from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import site
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency at runtime
    OpenAI = None  # type: ignore[assignment]


class PipelineError(RuntimeError):
    """Raised when the processing pipeline cannot continue."""


@dataclass(slots=True)
class SongSegment:
    index: int
    start: float
    end: float
    padded_start: float
    padded_end: float
    raw_duration: float
    export_duration: float
    confidence: float
    transcript: str = ""
    language: str | None = None
    guessed_title: str | None = None
    final_title: str | None = None
    skipped_reason: str | None = None
    video_output: str | None = None
    audio_output: str | None = None


@dataclass(slots=True)
class PipelineRunResult:
    source_video: str
    output_dir: str
    total_duration: float
    segments: list[SongSegment] = field(default_factory=list)
    manifest_path: str | None = None


class ProcessingPipeline:
    """
    End-to-end processing pipeline for detecting, reviewing, and exporting song clips.

    Design goals:
    - avoid multi-framework CUDA requirements
    - keep runtime dependencies minimal
    - use ffmpeg for media I/O
    - use a simple energy/spectrum based detector for the first pass
    - optionally use faster-whisper for transcript extraction
    - optionally use an OpenAI-compatible API for title guessing
    """

    def __init__(
        self,
        config: Any,
        console: Any | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.console = console
        self.status_callback = status_callback
        self._stop_requested = False
        self._whisper_model: Any | None = None
        self._openai_client: Any | None = None
        self._dll_directories: list[Any] = []

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def run(self, video_path: str | Path | None = None) -> PipelineRunResult:
        self._ensure_ffmpeg_available()

        input_video = Path(video_path or self._get("input.video_path", ""))
        if not input_video:
            raise PipelineError("No input video path was provided.")
        if not input_video.exists():
            raise PipelineError(f"Input video does not exist: {input_video}")

        output_dir = Path(self._get("project.output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        total_duration = self._probe_duration(input_video)
        self._log(f"Input video: {input_video}")
        self._log(f"Output directory: {output_dir}")
        self._log(f"Video duration: {total_duration:.2f}s")
        self._update_status("准备开始处理视频")

        with tempfile.TemporaryDirectory(prefix="neuro_slice_") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            analysis_wav = temp_dir / "analysis.wav"

            self._update_status("正在提取分析音频")
            self._extract_analysis_audio(input_video, analysis_wav)

            self._update_status("正在检测候选歌曲片段")
            candidates = self._detect_song_candidates(
                input_video=input_video,
                wav_path=analysis_wav,
                total_duration=total_duration,
            )

            if not candidates:
                self._log("No candidate song segments detected.")
                self._update_status("未检测到候选歌曲片段")
                result = PipelineRunResult(
                    source_video=str(input_video),
                    output_dir=str(output_dir),
                    total_duration=total_duration,
                    segments=[],
                    manifest_path=None,
                )
                self._emit_live_result_update(
                    input_video=input_video,
                    output_dir=output_dir,
                    processed_segments=[],
                )
                if self._get("review.write_manifest", True):
                    manifest_path = self._write_manifest(result, output_dir)
                    result.manifest_path = str(manifest_path)
                return result

            self._log(f"Detected {len(candidates)} candidate segments.")
            self._update_status(f"已检测到 {len(candidates)} 个候选片段")

            processed_segments: list[SongSegment] = []
            total_segments = len(candidates)

            for segment in candidates:
                if self._stop_is_requested():
                    self._stop_requested = True
                    self._log("Stop requested by user. No more segments will be processed.")
                    self._update_status("已收到停止请求，正在停止后续片段处理")
                    break

                self._log(
                    f"[{segment.index:02d}/{total_segments:02d}] "
                    f"{segment.start:.1f}s -> {segment.end:.1f}s "
                    f"(confidence={segment.confidence:.2f})"
                )
                self._update_status(
                    f"正在处理第 {segment.index}/{total_segments} 段 "
                    f"({segment.start:.1f}s -> {segment.end:.1f}s)"
                )

                if self._asr_enabled():
                    self._update_status(f"正在转录第 {segment.index}/{total_segments} 段")
                    transcript, language = self._transcribe_segment(
                        input_video=input_video,
                        segment=segment,
                        temp_dir=temp_dir,
                    )
                    segment.transcript = transcript
                    segment.language = language

                cleaned_transcript = self._clean_transcript_for_recognition(segment.transcript)

                if self._recognition_enabled():
                    self._update_status(f"正在识别第 {segment.index}/{total_segments} 段的歌曲信息")
                    self._log(
                        f"Recognition enabled for segment {segment.index:02d} | "
                        f"cleaned transcript chars={len(cleaned_transcript)}"
                    )
                    guessed_title = self._guess_title(cleaned_transcript)
                    segment.guessed_title = guessed_title
                    segment.final_title = (
                        self._title_only_from_recognition(guessed_title)
                        if guessed_title
                        else self._default_title(segment)
                    )

                    if guessed_title:
                        self._log(
                            f"Recognition success for segment {segment.index:02d}: {guessed_title}"
                        )
                    else:
                        self._log(
                            f"Recognition did not return a confident title for segment {segment.index:02d}."
                        )
                else:
                    self._log(
                        f"Recognition disabled for segment {segment.index:02d}; "
                        f"using fallback title {self._default_title(segment)}."
                    )
                    segment.final_title = self._default_title(segment)

                if self._get("export.export_video", True) or self._get("export.export_audio", False):
                    self._update_status(
                        f"正在导出第 {segment.index}/{total_segments} 段：{segment.final_title or self._default_title(segment)}"
                    )

                self._export_segment(input_video, segment, output_dir)
                processed_segments.append(segment)
                self._emit_live_result_update(
                    input_video=input_video,
                    output_dir=output_dir,
                    processed_segments=processed_segments,
                )

                api_sleep = float(self._get("recognition.api_sleep_seconds", 0.0))
                if api_sleep > 0:
                    time.sleep(api_sleep)

            result = PipelineRunResult(
                source_video=str(input_video),
                output_dir=str(output_dir),
                total_duration=total_duration,
                segments=processed_segments,
            )

            if self._get("review.write_manifest", True):
                self._update_status("正在写入 manifest")
                manifest_path = self._write_manifest(result, output_dir)
                result.manifest_path = str(manifest_path)

            self._emit_live_result_update(
                input_video=input_video,
                output_dir=output_dir,
                processed_segments=processed_segments,
            )

            if self._stop_requested:
                self._update_status("已停止当前任务")
            else:
                self._update_status("处理完成")
            return result

    # ---------------------------------------------------------------------
    # Config helpers
    # ---------------------------------------------------------------------

    def _get(self, dotted_key: str, default: Any = None) -> Any:
        current: Any = self.config
        for part in dotted_key.split("."):
            if current is None:
                return default

            if isinstance(current, dict):
                current = current.get(part, default if part == dotted_key.split(".")[-1] else None)
                continue

            if hasattr(current, part):
                current = getattr(current, part)
                continue

            if hasattr(current, "model_dump"):
                payload = current.model_dump()
                current = payload.get(part, default if part == dotted_key.split(".")[-1] else None)
                continue

            return default

        return default if current is None else current

    def _asr_enabled(self) -> bool:
        return bool(self._get("asr.enabled", True))

    def _recognition_enabled(self) -> bool:
        if not self._get("recognition.enabled", False):
            return False
        return str(self._get("recognition.mode", "none")).lower() != "none"

    # ---------------------------------------------------------------------
    # Logging
    # ---------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.console is not None and hasattr(self.console, "print"):
            self.console.print(message)
        else:
            print(message)

    def _update_status(self, message: str) -> None:
        callback = self.status_callback
        if callback is None:
            return
        try:
            callback(message)
        except Exception as exc:
            self._log(f"Status callback failed: {exc}")

    def _emit_live_result_update(
        self,
        *,
        input_video: Path,
        output_dir: Path,
        processed_segments: list[SongSegment],
    ) -> None:
        callback = self.status_callback
        if callback is None:
            return

        try:
            maybe_method = getattr(callback, "on_live_result_update")
        except Exception:
            return

        if not callable(maybe_method):
            return

        report = PipelineRunResult(
            source_video=str(input_video),
            output_dir=str(output_dir),
            total_duration=0.0,
            segments=processed_segments,
            manifest_path=None,
        )
        manifest = result_to_manifest(report)
        exports: list[str] = []
        for segment in processed_segments:
            if segment.video_output:
                exports.append(segment.video_output)
            if segment.audio_output:
                exports.append(segment.audio_output)

        try:
            maybe_method(
                {
                    "summary": _live_manifest_summary(manifest),
                    "manifest": manifest,
                    "exports": exports,
                }
            )
        except Exception as exc:
            self._log(f"Live result callback failed: {exc}")

    def _stop_is_requested(self) -> bool:
        stop_requested = self._stop_requested

        callback = self.status_callback
        if callback is not None and not stop_requested:
            with contextlib.suppress(Exception):
                maybe_method = getattr(callback, "should_stop")
                if callable(maybe_method):
                    stop_requested = bool(maybe_method())

            if not stop_requested:
                with contextlib.suppress(Exception):
                    maybe_flag = getattr(callback, "stop_requested")
                    if callable(maybe_flag):
                        stop_requested = bool(maybe_flag())
                    else:
                        stop_requested = bool(maybe_flag)

        if stop_requested:
            self._stop_requested = True
            active_process = getattr(self, "_active_subprocess", None)
            if active_process is not None:
                with contextlib.suppress(Exception):
                    if active_process.poll() is None:
                        active_process.terminate()
            return True

        return False

    # ---------------------------------------------------------------------
    # Media utilities
    # ---------------------------------------------------------------------

    def _ensure_ffmpeg_available(self) -> None:
        missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        if missing:
            joined = ", ".join(missing)
            raise PipelineError(f"Required executables not found in PATH: {joined}")

    def _run_subprocess(
        self,
        command: list[str],
        *,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        self._active_subprocess = process

        try:
            while True:
                if self._stop_is_requested():
                    with contextlib.suppress(Exception):
                        if process.poll() is None:
                            process.terminate()
                    with contextlib.suppress(Exception):
                        process.wait(timeout=2.0)
                    with contextlib.suppress(Exception):
                        if process.poll() is None:
                            process.kill()
                    raise PipelineError(
                        f"Processing was stopped by the user while running: {' '.join(command)}"
                    )

                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if getattr(self, "_active_subprocess", None) is process:
                self._active_subprocess = None

        completed = subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode or 0,
            stdout=stdout if capture else None,
            stderr=stderr if capture else None,
        )

        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                command,
                output=completed.stdout,
                stderr=completed.stderr,
            )

        return completed

    def _probe_duration(self, video_path: Path) -> float:
        result = self._run_subprocess(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture=True,
        )
        value = (result.stdout or "").strip()
        try:
            return float(value)
        except ValueError as exc:  # pragma: no cover - ffprobe edge case
            raise PipelineError(f"Could not parse video duration from ffprobe output: {value!r}") from exc

    def _extract_analysis_audio(self, input_video: Path, output_wav: Path) -> None:
        sample_rate = int(self._get("audio.sample_rate", 16000))
        channels = int(self._get("audio.channels", 1))
        preprocess_filter = str(self._get("audio.preprocess_filter", "") or "").strip()

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
        ]
        if preprocess_filter:
            command.extend(["-af", preprocess_filter])
        command.append(str(output_wav))

        self._run_subprocess(command)

    # ---------------------------------------------------------------------
    # Detection
    # ---------------------------------------------------------------------

    def _detect_song_candidates(
        self,
        input_video: Path,
        wav_path: Path,
        total_duration: float,
    ) -> list[SongSegment]:
        detector_backend = str(
            os.environ.get(
                "NEURO_SLICE_DETECTOR_BACKEND",
                self._get("detector.backend", "legacy-subprocess"),
            )
            or "legacy-subprocess"
        ).strip().lower()

        if detector_backend in {"legacy-subprocess", "legacy", "legacy-subprocess-default"}:
            self._log("Using legacy detector backend: subprocess")
            return self._detect_song_candidates_with_legacy_subprocess(
                input_video=input_video,
                total_duration=total_duration,
                suppress_errors=False,
            )

        if detector_backend == "auto":
            self._log("Using auto detector backend: trying legacy subprocess detector first")
            legacy_segments = self._detect_song_candidates_with_legacy_subprocess(
                input_video=input_video,
                total_duration=total_duration,
                suppress_errors=True,
            )
            if legacy_segments:
                return legacy_segments
            self._log("Legacy subprocess detector unavailable. Falling back to heuristic detector.")

        samples, sample_rate = self._read_wav_mono(wav_path)

        window_seconds = float(self._get("segment.window_seconds", 2.0))
        hop_seconds = float(self._get("segment.hop_seconds", 1.0))
        min_song_duration = float(self._get("segment.min_song_duration", 45.0))
        max_song_duration = float(self._get("segment.max_song_duration", 420.0))
        merge_gap_seconds = float(self._get("segment.merge_gap_seconds", 10.0))
        padding_before = float(self._get("segment.padding_before", 1.5))
        padding_after = float(self._get("segment.padding_after", 2.0))

        if samples.size == 0:
            return []

        features = self._compute_window_features(
            samples=samples,
            sample_rate=sample_rate,
            window_seconds=window_seconds,
            hop_seconds=hop_seconds,
        )
        if not features:
            return []

        scores = np.array([f["score"] for f in features], dtype=np.float32)

        threshold = max(
            float(np.percentile(scores, 72)) * 0.82,
            float(scores.mean() + 0.18 * scores.std()),
            0.16,
        )

        active = scores >= threshold
        active = self._close_boolean_gaps(active, max_gap_windows=max(1, round(merge_gap_seconds / hop_seconds / 2)))
        raw_ranges = self._boolean_runs(active)

        segments: list[SongSegment] = []
        next_index = 1

        for start_idx, end_idx in raw_ranges:
            start_time = features[start_idx]["start"]
            end_time = features[end_idx]["end"]
            duration = end_time - start_time

            if duration < min_song_duration:
                continue
            if duration > max_song_duration:
                continue

            confidence = float(np.clip(scores[start_idx : end_idx + 1].mean(), 0.0, 1.0))
            padded_start = max(0.0, start_time - padding_before)
            padded_end = min(total_duration, end_time + padding_after)

            segments.append(
                SongSegment(
                    index=next_index,
                    start=round(start_time, 3),
                    end=round(end_time, 3),
                    padded_start=round(padded_start, 3),
                    padded_end=round(padded_end, 3),
                    raw_duration=round(duration, 3),
                    export_duration=round(padded_end - padded_start, 3),
                    confidence=round(confidence, 4),
                )
            )
            next_index += 1

        merged: list[SongSegment] = []
        for segment in segments:
            if not merged:
                merged.append(segment)
                continue

            prev = merged[-1]
            gap = segment.start - prev.end
            if gap <= merge_gap_seconds:
                prev.end = segment.end
                prev.padded_end = segment.padded_end
                prev.raw_duration = round(prev.end - prev.start, 3)
                prev.export_duration = round(prev.padded_end - prev.padded_start, 3)
                prev.confidence = round(max(prev.confidence, segment.confidence), 4)
            else:
                merged.append(segment)

        for idx, segment in enumerate(merged, start=1):
            segment.index = idx

        return merged

    def _read_wav_mono(self, wav_path: Path) -> tuple[np.ndarray, int]:
        with wave.open(str(wav_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            raw = wav_file.readframes(frame_count)

        if sample_width != 2:
            raise PipelineError("Only 16-bit PCM WAV files are supported for analysis.")

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        return samples, sample_rate

    def _compute_window_features(
        self,
        *,
        samples: np.ndarray,
        sample_rate: int,
        window_seconds: float,
        hop_seconds: float,
    ) -> list[dict[str, float]]:
        window_size = max(1, int(sample_rate * window_seconds))
        hop_size = max(1, int(sample_rate * hop_seconds))

        if len(samples) < window_size:
            return []

        features: list[dict[str, float]] = []
        for start_sample in range(0, len(samples) - window_size + 1, hop_size):
            end_sample = start_sample + window_size
            frame = samples[start_sample:end_sample]

            rms = float(np.sqrt(np.mean(np.square(frame)) + 1e-12))
            zcr = float(np.mean(np.abs(np.diff(np.signbit(frame)).astype(np.float32))))

            spectrum = np.fft.rfft(frame * np.hanning(len(frame)))
            magnitude = np.abs(spectrum)
            freqs = np.fft.rfftfreq(len(frame), d=1.0 / sample_rate)
            total_mag = float(np.sum(magnitude) + 1e-12)

            centroid = float(np.sum(freqs * magnitude) / total_mag)
            band_mask = (freqs >= 180.0) & (freqs <= 5000.0)
            band_ratio = float(np.sum(magnitude[band_mask]) / total_mag)

            # Heuristic singing / accompaniment score.
            # The weights intentionally favor sustained, broad-spectrum audio over silence.
            rms_score = self._sigmoid((rms - 0.018) / 0.012)
            band_score = self._sigmoid((band_ratio - 0.60) / 0.12)
            centroid_score = self._sigmoid((centroid - 700.0) / 350.0)
            zcr_penalty = 1.0 - self._sigmoid((zcr - 0.20) / 0.08)

            score = (
                0.48 * rms_score
                + 0.24 * band_score
                + 0.18 * centroid_score
                + 0.10 * zcr_penalty
            )

            start_seconds = start_sample / sample_rate
            end_seconds = end_sample / sample_rate

            features.append(
                {
                    "start": float(start_seconds),
                    "end": float(end_seconds),
                    "rms": rms,
                    "zcr": zcr,
                    "centroid": centroid,
                    "band_ratio": band_ratio,
                    "score": float(np.clip(score, 0.0, 1.0)),
                }
            )

        return features

    def _close_boolean_gaps(self, active: np.ndarray, max_gap_windows: int) -> np.ndarray:
        if active.size == 0 or max_gap_windows <= 0:
            return active

        result = active.copy()
        false_runs = self._boolean_runs(~active)
        for start_idx, end_idx in false_runs:
            run_len = end_idx - start_idx + 1
            left_active = start_idx > 0 and result[start_idx - 1]
            right_active = end_idx + 1 < len(result) and result[end_idx + 1]
            if run_len <= max_gap_windows and left_active and right_active:
                result[start_idx : end_idx + 1] = True
        return result

    def _boolean_runs(self, values: np.ndarray) -> list[tuple[int, int]]:
        runs: list[tuple[int, int]] = []
        start: int | None = None

        for idx, value in enumerate(values.tolist()):
            if value and start is None:
                start = idx
            elif not value and start is not None:
                runs.append((start, idx - 1))
                start = None

        if start is not None:
            runs.append((start, len(values) - 1))

        return runs

    def _sigmoid(self, value: float) -> float:
        value = max(-60.0, min(60.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def _runtime_config_path(self) -> Path:
        return Path(__file__).resolve().parents[2] / "runtime" / "local_envs.json"

    def _load_runtime_local_envs(self) -> dict[str, Any]:
        path = self._runtime_config_path()
        if not path.exists():
            return {}

        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            self._log(f"Failed to read runtime config '{path}': {exc}")
            return {}

    def inspect_legacy_runtime(self) -> dict[str, Any]:
        runtime_config_path = self._runtime_config_path()
        runtime_config = self._load_runtime_local_envs()

        legacy_python = self._resolve_legacy_python_path()
        legacy_script = self._resolve_legacy_script_path()

        return {
            "backend": str(self._get("detector.backend", "legacy-subprocess")),
            "runtime_config_path": str(runtime_config_path),
            "runtime_config_exists": runtime_config_path.exists(),
            "runtime_config": runtime_config,
            "legacy_python_path": str(legacy_python),
            "legacy_python_exists": legacy_python.exists(),
            "legacy_script_path": str(legacy_script),
            "legacy_script_exists": legacy_script.exists(),
            "auto_setup_legacy_env": bool(self._get("detector.auto_setup_legacy_env", True)),
            "legacy_env_dir": str(self._get("detector.legacy_env_dir", ".venv_legacy")),
        }

    def _resolve_legacy_python_path(self) -> Path:
        configured = str(self._get("detector.legacy_python_path", "") or "").strip()
        runtime_config = self._load_runtime_local_envs()
        runtime_value = str(runtime_config.get("legacy_python", "") or "").strip()

        candidate = configured or runtime_value
        if not candidate:
            legacy_env_dir = str(self._get("detector.legacy_env_dir", ".venv_legacy") or ".venv_legacy").strip()
            candidate = str(Path(legacy_env_dir) / "Scripts" / "python.exe")

        path = Path(candidate)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path

        return path.resolve()

    def _resolve_legacy_script_path(self) -> Path:
        configured = str(self._get("detector.legacy_script_path", "runtime/detect_segments_tf.py") or "").strip()
        runtime_config = self._load_runtime_local_envs()
        runtime_value = str(runtime_config.get("legacy_detector_script", "") or "").strip()

        candidate = configured or runtime_value or "runtime/detect_segments_tf.py"
        path = Path(candidate)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path

        return path.resolve()

    def _validate_legacy_runtime(self) -> tuple[Path, Path]:
        runtime_config_path = self._runtime_config_path()
        runtime_config = self._load_runtime_local_envs()

        legacy_python = self._resolve_legacy_python_path()
        legacy_script = self._resolve_legacy_script_path()

        guidance_lines = [
            "Legacy detector runtime validation failed.",
            f"runtime config: {runtime_config_path}",
            f"legacy python: {legacy_python}",
            f"legacy detector script: {legacy_script}",
        ]

        if not runtime_config:
            guidance_lines.append(
                "runtime/local_envs.json is missing or empty. Run the setup script again so the project can wire the main environment to .venv_legacy automatically."
            )

        if not legacy_python.exists():
            guidance_lines.append(
                "The legacy detector Python interpreter does not exist. Make sure .venv_legacy has been created successfully during setup."
            )

        if not legacy_script.exists():
            guidance_lines.append(
                "The legacy detector script does not exist. Make sure runtime/detect_segments_tf.py is present in the project."
            )

        if not legacy_python.exists() or not legacy_script.exists():
            raise PipelineError("\n".join(guidance_lines))

        return legacy_python, legacy_script

    def _detect_song_candidates_with_legacy_subprocess(
        self,
        *,
        input_video: Path,
        total_duration: float,
        suppress_errors: bool = False,
    ) -> list[SongSegment]:
        min_music_duration = int(self._get("detector.min_music_duration", 20))
        max_song_duration = float(self._get("segment.max_song_duration", 420.0))
        padding_before = float(self._get("segment.padding_before", 1.5))
        padding_after = float(self._get("segment.padding_after", 2.0))

        try:
            legacy_python, legacy_script = self._validate_legacy_runtime()
        except PipelineError as exc:
            if suppress_errors:
                self._log(str(exc))
                return []
            raise

        self._log(f"Launching legacy detector subprocess: {legacy_python} {legacy_script}")
        self._update_status("正在启动 legacy detector 子进程")

        command = [str(legacy_python), "-u", str(legacy_script), str(input_video), str(min_music_duration)]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        self._active_subprocess = process

        stdout_lines: list[str] = []
        json_line: str | None = None
        try:
            while True:
                if self._stop_is_requested():
                    with contextlib.suppress(Exception):
                        if process.poll() is None:
                            process.terminate()
                    with contextlib.suppress(Exception):
                        process.wait(timeout=2.0)
                    with contextlib.suppress(Exception):
                        if process.poll() is None:
                            process.kill()
                    raise PipelineError("Processing was stopped by the user while running the legacy detector subprocess.")

                stdout_line = process.stdout.readline() if process.stdout is not None else ""
                if stdout_line:
                    line = stdout_line.rstrip("\r\n")
                    stdout_lines.append(line)
                    if line.startswith("LEGACY_STATUS="):
                        status_message = line.split("=", 1)[1].strip()
                        self._log(f"[legacy] {status_message}")
                        self._update_status(status_message)
                    elif line.startswith("SEGMENTS_JSON="):
                        json_line = line.split("=", 1)[1]
                    elif line:
                        self._log(f"[legacy] {line}")

                if process.poll() is not None:
                    remaining_stdout = process.stdout.read() if process.stdout is not None else ""

                    if remaining_stdout:
                        for extra_line in remaining_stdout.splitlines():
                            stdout_lines.append(extra_line)
                            if extra_line.startswith("LEGACY_STATUS="):
                                status_message = extra_line.split("=", 1)[1].strip()
                                self._log(f"[legacy] {status_message}")
                                self._update_status(status_message)
                            elif extra_line.startswith("SEGMENTS_JSON="):
                                json_line = extra_line.split("=", 1)[1]
                            elif extra_line:
                                self._log(f"[legacy] {extra_line}")

                    break
        finally:
            if getattr(self, "_active_subprocess", None) is process:
                self._active_subprocess = None

        completed = subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode or 0,
            stdout="\n".join(stdout_lines),
            stderr="",
        )

        if completed.returncode != 0:
            message = (
                "Legacy detector subprocess failed.\n"
                f"python: {legacy_python}\n"
                f"script: {legacy_script}\n"
                f"exit code: {completed.returncode}\n"
                "This usually means the legacy environment is missing dependencies "
                "(for example inaSpeechSegmenter / TensorFlow stack) or the detector "
                "script failed inside .venv_legacy.\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
            if suppress_errors:
                self._log(message)
                return []
            raise PipelineError(message)

        if json_line is None:
            message = (
                "Legacy detector subprocess did not return SEGMENTS_JSON.\n"
                f"python: {legacy_python}\n"
                f"script: {legacy_script}\n"
                "The detector process completed, but it did not print the expected "
                "SEGMENTS_JSON=... line.\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
            if suppress_errors:
                self._log(message)
                return []
            raise PipelineError(message)

        try:
            raw_segments = json.loads(json_line)
        except Exception as exc:
            message = f"Failed to parse SEGMENTS_JSON from legacy detector: {exc}"
            if suppress_errors:
                self._log(message)
                return []
            raise PipelineError(message) from exc

        segments: list[SongSegment] = []
        next_index = 1

        for item in raw_segments:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue

            start_time = float(item[0])
            end_time = float(item[1])
            duration = end_time - start_time

            if duration <= 0:
                continue

            if duration > max_song_duration:
                self._log(
                    f"Skipping legacy detector segment {next_index:02d}: "
                    f"duration {duration:.1f}s exceeds max_song_duration {max_song_duration:.1f}s"
                )
                continue

            padded_start = max(0.0, start_time - padding_before)
            padded_end = min(total_duration, end_time + padding_after)
            segments.append(
                SongSegment(
                    index=next_index,
                    start=round(start_time, 3),
                    end=round(end_time, 3),
                    padded_start=round(padded_start, 3),
                    padded_end=round(padded_end, 3),
                    raw_duration=round(duration, 3),
                    export_duration=round(padded_end - padded_start, 3),
                    confidence=1.0,
                )
            )
            next_index += 1

        if segments:
            self._log(f"Legacy detector subprocess found {len(segments)} candidate segment(s).")

        return segments

    def _load_windows_cuda_runtime_dirs(self) -> None:
        if os.name != "nt":
            return
        if not hasattr(os, "add_dll_directory"):
            return
        if self._dll_directories:
            self._log("CUDA runtime DLL directories are already registered.")
            return

        root_candidates: list[Path] = []

        for entry in sys.path:
            text = str(entry or "").strip()
            if not text:
                continue
            try:
                root = Path(text)
            except Exception:
                continue
            if root.exists():
                root_candidates.append(root)

        for getter in (site.getsitepackages, site.getusersitepackages):
            try:
                value = getter()
            except Exception:
                continue

            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item or "").strip()
                if not text:
                    continue
                try:
                    root = Path(text)
                except Exception:
                    continue
                if root.exists():
                    root_candidates.append(root)

        executable = Path(sys.executable).resolve()
        env_root = executable.parent.parent
        env_site_packages = env_root / "Lib" / "site-packages"
        if env_site_packages.exists():
            root_candidates.append(env_site_packages)

        project_root = Path(__file__).resolve().parents[2]
        project_venv_site_packages = project_root / ".venv" / "Lib" / "site-packages"
        if project_venv_site_packages.exists():
            root_candidates.append(project_venv_site_packages)

        seen_roots: set[str] = set()
        normalized_roots: list[Path] = []
        for root in root_candidates:
            root_str = str(root.resolve())
            if root_str in seen_roots:
                continue
            seen_roots.add(root_str)
            normalized_roots.append(root)

        self._log("Scanning potential site-packages directories for bundled CUDA runtimes:")
        for root in normalized_roots:
            self._log(f" - {root}")

        candidate_dirs: list[Path] = []
        for root in normalized_roots:
            for base in (root, root / "Lib" / "site-packages"):
                if not base.exists():
                    continue

                for relative in (
                    Path("nvidia") / "cuda_runtime" / "bin",
                    Path("nvidia") / "cublas" / "bin",
                    Path("nvidia") / "cudnn" / "bin",
                ):
                    dll_dir = base / relative
                    if dll_dir.exists():
                        candidate_dirs.append(dll_dir)

        if not candidate_dirs:
            self._log("No bundled CUDA runtime DLL directories were found in the discovered Python environments.")
            return

        seen: set[str] = set()
        resolved_dirs: list[str] = []
        for dll_dir in candidate_dirs:
            dll_dir_str = str(dll_dir.resolve())
            if dll_dir_str in seen:
                continue
            seen.add(dll_dir_str)
            resolved_dirs.append(dll_dir_str)

        current_path = os.environ.get("PATH", "")
        path_parts = current_path.split(os.pathsep) if current_path else []
        for dll_dir_str in reversed(resolved_dirs):
            if dll_dir_str not in path_parts:
                path_parts.insert(0, dll_dir_str)
        os.environ["PATH"] = os.pathsep.join(path_parts)

        self._log("Prepended bundled CUDA runtime directories to PATH for the current process.")

        for dll_dir_str in resolved_dirs:
            self._dll_directories.append(os.add_dll_directory(dll_dir_str))
            self._log(f"Registered CUDA runtime DLL directory: {dll_dir_str}")

    # ---------------------------------------------------------------------
    # ASR
    # ---------------------------------------------------------------------

    def _import_whisper_model(self) -> Any | None:
        try:
            from faster_whisper import WhisperModel as ImportedWhisperModel
        except Exception:
            return None
        return ImportedWhisperModel

    def _get_whisper_model(self, force_cpu: bool = False) -> Any | None:
        if not self._asr_enabled():
            return None

        model_size = str(self._get("asr.model_size", "large-v3"))
        config_device = str(self._get("asr.device", "auto") or "auto").strip().lower()
        config_compute_type = str(self._get("asr.compute_type", "") or "").strip().lower()

        requested_device = str(
            os.environ.get("NEURO_SLICE_ASR_DEVICE", config_device) or config_device
        ).strip().lower()
        requested_compute_type = str(
            os.environ.get("NEURO_SLICE_ASR_COMPUTE_TYPE", config_compute_type) or config_compute_type
        ).strip().lower()

        if requested_device not in {"auto", "cuda", "cpu"}:
            self._log(
                f"Unknown ASR device '{requested_device}', falling back to config/default '{config_device}'."
            )
            requested_device = config_device if config_device in {"auto", "cuda", "cpu"} else "auto"

        if force_cpu:
            device = "cpu"
            compute_type = "int8"
        else:
            device = requested_device
            if requested_compute_type:
                compute_type = requested_compute_type
            elif device == "cuda":
                compute_type = "float16"
            else:
                compute_type = "int8"

        if not force_cpu and device in {"auto", "cuda"}:
            self._load_windows_cuda_runtime_dirs()

        WhisperModel = self._import_whisper_model()
        if WhisperModel is None:
            self._log("ASR is enabled, but faster-whisper is not available. Skipping transcription.")
            return None

        self._log(
            "ASR runtime selection | "
            f"model={model_size} | config_device={config_device} | "
            f"config_compute_type={config_compute_type or 'default'} | "
            f"requested_device={requested_device} | "
            f"requested_compute_type={requested_compute_type or 'default'} | "
            f"effective_device={device} | effective_compute_type={compute_type} | "
            f"force_cpu={force_cpu}"
        )

        if self._whisper_model is None:
            self._update_status(
                f"正在加载 ASR 模型：{model_size}（device={device}, compute_type={compute_type}）"
            )
            self._log(f"Loading faster-whisper model: {model_size} ({device}, {compute_type})")
            self._whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)

        return self._whisper_model

    def _should_retry_whisper_on_cpu(self, exc: Exception) -> bool:
        message = str(exc).lower()
        retry_markers = (
            "cublas64_12.dll",
            "cudnn",
            "cuda",
            "cublas",
            "cannot be loaded",
            "failed to load library",
        )
        return any(marker in message for marker in retry_markers)

    def _transcribe_segment(
        self,
        *,
        input_video: Path,
        segment: SongSegment,
        temp_dir: Path,
    ) -> tuple[str, str | None]:
        model = self._get_whisper_model()
        if model is None:
            return "", None

        audio_path = temp_dir / f"segment_{segment.index:03d}.wav"
        preprocess_filter = str(self._get("audio.preprocess_filter", "") or "").strip()
        sample_rate = int(self._get("audio.sample_rate", 16000))

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-ss",
            self._format_timestamp(segment.padded_start),
            "-to",
            self._format_timestamp(segment.padded_end),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
        ]
        if preprocess_filter:
            command.extend(["-af", preprocess_filter])
        command.append(str(audio_path))
        self._run_subprocess(command)

        language = str(self._get("asr.language", "") or "").strip() or None
        beam_size = int(self._get("asr.beam_size", 5))
        best_of = int(self._get("asr.best_of", 5))
        vad_filter = bool(self._get("asr.vad_filter", False))
        condition_on_previous_text = bool(self._get("asr.condition_on_previous_text", False))
        initial_prompt = str(self._get("asr.initial_prompt", "") or "").strip() or None

        try:
            segments, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=beam_size,
                best_of=best_of,
                vad_filter=vad_filter,
                condition_on_previous_text=condition_on_previous_text,
                initial_prompt=initial_prompt,
            )
        except RuntimeError as exc:
            if not self._should_retry_whisper_on_cpu(exc):
                raise

            self._log(
                "faster-whisper CUDA runtime is unavailable. "
                f"Original error: {exc}. Falling back to CPU/int8 for transcription."
            )
            self._update_status("CUDA 不可用，正在回退到 CPU/int8 继续转录")
            self._whisper_model = None
            model = self._get_whisper_model(force_cpu=True)
            if model is None:
                raise

            self._log("Retrying transcription on CPU/int8.")
            self._update_status("正在使用 CPU/int8 重试转录")
            segments, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=beam_size,
                best_of=best_of,
                vad_filter=vad_filter,
                condition_on_previous_text=condition_on_previous_text,
                initial_prompt=initial_prompt,
            )

        parts: list[str] = []
        for item in segments:
            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            parts.append(text)

        transcript = " ".join(parts).strip()
        detected_language = getattr(info, "language", None)
        return transcript, detected_language

    def _clean_transcript_for_recognition(self, transcript: str) -> str:
        text = (transcript or "").strip()
        if not text:
            return ""

        forbidden_fragments = [
            "这首歌曲",
            "准确转录",
            "不要包含",
            "旁白",
            "watching",
        ]
        kept_parts: list[str] = []
        for part in text.split():
            if any(fragment in part for fragment in forbidden_fragments):
                continue
            kept_parts.append(part)

        return " ".join(kept_parts).strip()

    # ---------------------------------------------------------------------
    # Optional title recognition
    # ---------------------------------------------------------------------

    def _get_openai_client(self) -> Any | None:
        if not self._recognition_enabled():
            return None
        if str(self._get("recognition.mode", "none")).lower() != "openai":
            return None
        if OpenAI is None:
            self._log("Title recognition is enabled, but the OpenAI SDK is not available.")
            return None

        if self._openai_client is None:
            api_key = str(self._get("recognition.api_key", "") or "").strip()
            base_url = str(self._get("recognition.base_url", "") or "").strip() or None
            if not api_key:
                self._log("Title recognition is enabled, but no API key is configured.")
                return None
            self._openai_client = OpenAI(api_key=api_key, base_url=base_url)

        return self._openai_client

    def _guess_title(self, transcript: str) -> str | None:
        transcript = (transcript or "").strip()
        if len(transcript) < 30:
            self._log("Skipping title recognition because the cleaned transcript is too short.")
            return None

        if not self._recognition_enabled():
            self._log("Skipping title recognition because recognition is disabled.")
            return None

        recognition_mode = str(self._get("recognition.mode", "none") or "none").strip().lower()
        if recognition_mode != "openai":
            self._log(f"Skipping title recognition because mode '{recognition_mode}' is not supported.")
            return None

        api_key = str(self._get("recognition.api_key", "") or "").strip()
        if not api_key:
            self._log("Skipping title recognition because no API key is configured.")
            return None

        try:
            from neuro_slice.recognition.title_guess import guess_song_title
        except Exception as exc:
            self._log(f"Title recognition client is unavailable: {exc}")
            return None

        self._log("Calling shared title recognition client.")
        try:
            guessed = guess_song_title(
                transcript,
                {
                    "enabled": True,
                    "mode": recognition_mode,
                    "api_key": api_key,
                    "base_url": str(self._get("recognition.base_url", "") or "").strip(),
                    "model": str(self._get("recognition.model", "gpt-4o-mini")),
                    "request_timeout_seconds": float(self._get("recognition.request_timeout_seconds", 30.0)),
                    "max_lyrics_chars": int(self._get("recognition.max_lyrics_chars", 2500)),
                    "api_sleep_seconds": float(self._get("recognition.api_sleep_seconds", 0.0)),
                    "log_callback": self._log,
                },
            )
        except Exception as exc:  # pragma: no cover - network / API failure
            self._log(f"Title recognition failed: {exc}")
            return None

        if guessed:
            self._log(f"Shared title recognition client returned: {guessed}")
        else:
            self._log("Shared title recognition client returned no confident result.")

        return guessed

    def _title_only_from_recognition(self, guessed_title: str | None) -> str | None:
        text = (guessed_title or "").strip()
        if not text:
            return None

        if " - " in text:
            _, title = text.split(" - ", 1)
            title = title.strip()
            if title:
                return title

        return text

    # ---------------------------------------------------------------------
    # Export
    # ---------------------------------------------------------------------

    def _export_segment(self, input_video: Path, segment: SongSegment, output_dir: Path) -> None:
        title = self._sanitize_filename(segment.final_title or self._default_title(segment))
        singer = self._sanitize_filename(str(self._get("metadata.singer", "Unknown")).strip() or "Unknown")
        date = self._sanitize_filename(str(self._get("metadata.date", "")).strip())
        pattern = str(self._get("export.filename_pattern", "[{singer}] {title} ({date})"))

        base_name = pattern.format(
            index=f"{segment.index:02d}",
            title=title,
            date=date,
            singer=singer,
            start=self._format_compact_time(segment.padded_start),
            end=self._format_compact_time(segment.padded_end),
        ).strip()

        if not base_name:
            base_name = f"{segment.index:02d}_{title}"

        if self._get("export.export_video", True):
            container = str(self._get("export.video_container", "mp4"))
            video_codec = str(self._get("export.video_codec", "copy"))
            audio_codec = str(self._get("export.audio_codec", "copy"))
            video_path = output_dir / f"{base_name}.{container}"

            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_video),
                "-ss",
                self._format_timestamp(segment.padded_start),
                "-to",
                self._format_timestamp(segment.padded_end),
            ]

            if video_codec == "copy":
                command.extend(["-c:v", "copy"])
            else:
                command.extend(["-c:v", video_codec])

            if audio_codec == "copy":
                command.extend(["-c:a", "copy"])
            else:
                command.extend(["-c:a", audio_codec])

            command.append(str(video_path))
            self._run_subprocess(command)
            segment.video_output = str(video_path)

        if self._get("export.export_audio", False):
            audio_format = str(self._get("export.audio_format", "mp3"))
            audio_path = output_dir / f"{base_name}.{audio_format}"

            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_video),
                "-ss",
                self._format_timestamp(segment.padded_start),
                "-to",
                self._format_timestamp(segment.padded_end),
                "-vn",
            ]

            if audio_format.lower() == "mp3":
                command.extend(["-q:a", "0"])
            else:
                command.extend(["-c:a", "copy"])

            command.append(str(audio_path))
            self._run_subprocess(command)
            segment.audio_output = str(audio_path)

    def _write_manifest(self, result: PipelineRunResult, output_dir: Path) -> Path:
        manifest_name = str(self._get("review.manifest_name", "songs.json"))
        manifest_path = output_dir / manifest_name

        payload = {
            "version": "1",
            "project": "neuro-slice",
            "generated_at": datetime.now().isoformat(),
            "source_video": result.source_video,
            "output_dir": result.output_dir,
            "total_duration": result.total_duration,
            "count": len(result.segments),
            "metadata": {
                "date": str(self._get("metadata.date", "") or ""),
                "singer": str(self._get("metadata.singer", "") or ""),
            },
            "segments": [asdict(item) for item in result.segments],
        }

        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"Manifest written to: {manifest_path}")
        return manifest_path

    # ---------------------------------------------------------------------
    # Formatting
    # ---------------------------------------------------------------------

    def _default_title(self, segment: SongSegment) -> str:
        return f"song_{segment.index:02d}"

    def _sanitize_filename(self, value: str) -> str:
        forbidden = '\\/:*?"<>|'
        cleaned = "".join("_" if ch in forbidden else ch for ch in value)
        cleaned = " ".join(cleaned.split()).strip(" ._")
        return cleaned or "untitled"

    def _format_timestamp(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

    def _format_compact_time(self, seconds: float) -> str:
        seconds = int(max(0.0, seconds))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def result_to_manifest(result: PipelineRunResult) -> dict[str, Any]:
    return {
        "version": "1",
        "project": "neuro-slice",
        "source_video": result.source_video,
        "output_dir": result.output_dir,
        "total_duration": result.total_duration,
        "count": len(result.segments),
        "segments": [asdict(item) for item in result.segments],
    }


def _live_manifest_summary(manifest: dict[str, Any]) -> str:
    segments = manifest.get("segments", [])
    lines = [
        f"source_video: {manifest.get('source_video', '')}",
        f"output_dir: {manifest.get('output_dir', '')}",
        f"total_duration: {manifest.get('total_duration', '')}",
        f"count: {manifest.get('count', len(segments) if isinstance(segments, list) else 0)}",
        "",
        "segments:",
    ]

    if not isinstance(segments, list) or not segments:
        lines.append("  (none)")
        return "\n".join(lines)

    for idx, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue

        title = (
            segment.get("final_title")
            or segment.get("title")
            or segment.get("guessed_title")
            or f"song_{idx:02d}"
        )
        start = segment.get("start", "")
        end = segment.get("end", "")
        confidence = segment.get("confidence", "")
        lines.append(
            f"  [{idx:02d}] {start} -> {end} | confidence={confidence} | title={title}"
        )

    return "\n".join(lines)


def inspect_legacy_runtime(config: Any) -> dict[str, Any]:
    pipeline = ProcessingPipeline(config=config)
    return pipeline.inspect_legacy_runtime()


def analyze_video(
    video_path: str | Path,
    config: Any,
    output_dir: str | Path | None = None,
    console: Any | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if output_dir is not None and hasattr(config, "project") and hasattr(config.project, "output_dir"):
        config.project.output_dir = str(output_dir)

    pipeline = ProcessingPipeline(config=config, console=console, status_callback=status_callback)
    result = pipeline.run(video_path=video_path)
    return result_to_manifest(result)


def run_pipeline(
    config: Any,
    video_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    console: Any | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if output_dir is not None and hasattr(config, "project") and hasattr(config.project, "output_dir"):
        config.project.output_dir = str(output_dir)

    pipeline = ProcessingPipeline(config=config, console=console, status_callback=status_callback)
    result = pipeline.run(video_path=video_path)

    manifest = result_to_manifest(result)
    exports: list[str] = []

    for segment in result.segments:
        if segment.video_output:
            exports.append(segment.video_output)
        if segment.audio_output:
            exports.append(segment.audio_output)

    return manifest, exports