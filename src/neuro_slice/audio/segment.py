from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


@dataclass(slots=True)
class WindowScore:
    start: float
    end: float
    energy: float
    zcr: float
    flux: float
    score: float


@dataclass(slots=True)
class Segment:
    start: float
    end: float
    confidence: float
    source: tuple[str, ...] = ("audio",)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "confidence": round(self.confidence, 4),
            "source": list(self.source),
        }


@dataclass(slots=True)
class AudioSegmentDetectorConfig:
    window_seconds: float = 2.0
    hop_seconds: float = 1.0
    min_song_duration: float = 45.0
    max_song_duration: float = 420.0
    merge_gap_seconds: float = 10.0
    padding_before: float = 1.5
    padding_after: float = 2.0
    smoothing_windows: int = 5
    energy_threshold_quantile: float = 0.55
    score_threshold: float = 0.58
    min_active_ratio: float = 0.45

    def validate(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.hop_seconds <= 0:
            raise ValueError("hop_seconds must be > 0")
        if self.min_song_duration <= 0:
            raise ValueError("min_song_duration must be > 0")
        if self.max_song_duration < self.min_song_duration:
            raise ValueError("max_song_duration must be >= min_song_duration")
        if not 0 < self.energy_threshold_quantile < 1:
            raise ValueError("energy_threshold_quantile must be in (0, 1)")
        if not 0 < self.score_threshold < 1:
            raise ValueError("score_threshold must be in (0, 1)")
        if not 0 < self.min_active_ratio <= 1:
            raise ValueError("min_active_ratio must be in (0, 1]")


def detect_segments_from_waveform(
    samples: np.ndarray,
    sample_rate: int,
    config: AudioSegmentDetectorConfig | None = None,
) -> tuple[list[Segment], list[WindowScore]]:
    """
    Detect likely song segments from a mono waveform using lightweight heuristics.

    The detector is intentionally simple:
    1. Slice the waveform into overlapping windows.
    2. Compute per-window energy, zero-crossing rate, and spectral flux.
    3. Build a music/singing score using adaptive normalization.
    4. Smooth scores and merge adjacent active windows into segments.

    This is designed as a practical first-pass detector for karaoke/VOD archives.
    """
    if config is None:
        config = AudioSegmentDetectorConfig()
    config.validate()

    waveform = _prepare_waveform(samples)
    if waveform.size == 0:
        return [], []

    windows = _extract_window_features(waveform, sample_rate, config)
    if not windows:
        return [], []

    scores = _score_windows(windows, config)
    smoothed_scores = _smooth_scores(scores, config.smoothing_windows)
    active_mask = _build_active_mask(windows, smoothed_scores, config)

    segments = _windows_to_segments(
        windows=windows,
        scores=smoothed_scores,
        active_mask=active_mask,
        total_duration=waveform.size / sample_rate,
        config=config,
    )
    scored_windows = [
        WindowScore(
            start=w.start,
            end=w.end,
            energy=w.energy,
            zcr=w.zcr,
            flux=w.flux,
            score=float(smoothed_scores[i]),
        )
        for i, w in enumerate(windows)
    ]
    return segments, scored_windows


def segments_to_dicts(segments: Sequence[Segment]) -> list[dict]:
    return [segment.to_dict() for segment in segments]


@dataclass(slots=True)
class _FeatureWindow:
    start: float
    end: float
    energy: float
    zcr: float
    flux: float


def _prepare_waveform(samples: np.ndarray) -> np.ndarray:
    arr = np.asarray(samples, dtype=np.float32)

    if arr.ndim == 0:
        return np.array([], dtype=np.float32)

    if arr.ndim > 1:
        # Average channels to mono.
        arr = arr.mean(axis=-1)

    if arr.size == 0:
        return np.array([], dtype=np.float32)

    peak = float(np.max(np.abs(arr)))
    if peak > 0:
        arr = arr / peak

    # Remove NaN/Inf to avoid poisoning the heuristics.
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr.astype(np.float32, copy=False)


def _extract_window_features(
    waveform: np.ndarray,
    sample_rate: int,
    config: AudioSegmentDetectorConfig,
) -> list[_FeatureWindow]:
    win_size = max(1, int(config.window_seconds * sample_rate))
    hop_size = max(1, int(config.hop_seconds * sample_rate))

    if waveform.size < win_size:
        padded = np.pad(waveform, (0, win_size - waveform.size))
        waveform = padded

    windows: list[_FeatureWindow] = []
    previous_magnitude: np.ndarray | None = None

    for start_idx in range(0, max(1, waveform.size - win_size + 1), hop_size):
        chunk = waveform[start_idx : start_idx + win_size]
        if chunk.size < win_size:
            chunk = np.pad(chunk, (0, win_size - chunk.size))

        energy = _rms(chunk)
        zcr = _zero_crossing_rate(chunk)
        magnitude = np.abs(np.fft.rfft(chunk * np.hanning(chunk.size)))
        flux = 0.0 if previous_magnitude is None else _spectral_flux(previous_magnitude, magnitude)
        previous_magnitude = magnitude

        start = start_idx / sample_rate
        end = min((start_idx + win_size) / sample_rate, waveform.size / sample_rate)
        windows.append(_FeatureWindow(start=start, end=end, energy=energy, zcr=zcr, flux=flux))

    return windows


def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))


def _zero_crossing_rate(chunk: np.ndarray) -> float:
    if chunk.size < 2:
        return 0.0
    signs = np.signbit(chunk)
    changes = np.count_nonzero(signs[1:] != signs[:-1])
    return float(changes / (chunk.size - 1))


def _spectral_flux(previous: np.ndarray, current: np.ndarray) -> float:
    prev_norm = previous / (np.linalg.norm(previous) + 1e-8)
    curr_norm = current / (np.linalg.norm(current) + 1e-8)
    diff = curr_norm - prev_norm
    diff[diff < 0] = 0
    return float(np.sum(diff))


def _score_windows(windows: Sequence[_FeatureWindow], config: AudioSegmentDetectorConfig) -> np.ndarray:
    energies = np.array([w.energy for w in windows], dtype=np.float32)
    zcrs = np.array([w.zcr for w in windows], dtype=np.float32)
    fluxes = np.array([w.flux for w in windows], dtype=np.float32)

    energy_floor = np.quantile(energies, config.energy_threshold_quantile)
    energy_ceiling = max(float(np.quantile(energies, 0.95)), energy_floor + 1e-6)

    energy_score = _normalize(energies, low=energy_floor, high=energy_ceiling)
    zcr_score = 1.0 - _normalize(zcrs, low=float(np.quantile(zcrs, 0.10)), high=float(np.quantile(zcrs, 0.90)))
    flux_score = _normalize(fluxes, low=float(np.quantile(fluxes, 0.20)), high=float(np.quantile(fluxes, 0.90)))

    # A simple heuristic:
    # - stronger energy generally means active content
    # - lower ZCR usually looks less like pure speech/noise
    # - moderate/high flux helps identify structured musical movement
    combined = (0.50 * energy_score) + (0.25 * zcr_score) + (0.25 * flux_score)
    return np.clip(combined, 0.0, 1.0)


def _normalize(values: np.ndarray, low: float, high: float) -> np.ndarray:
    denom = max(high - low, 1e-8)
    return np.clip((values - low) / denom, 0.0, 1.0)


def _smooth_scores(scores: np.ndarray, width: int) -> np.ndarray:
    width = max(1, int(width))
    if width == 1 or scores.size == 0:
        return scores.astype(np.float32, copy=True)

    kernel = np.ones(width, dtype=np.float32) / width
    padded = np.pad(scores, (width // 2, width - 1 - width // 2), mode="edge")
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed.astype(np.float32, copy=False)


def _build_active_mask(
    windows: Sequence[_FeatureWindow],
    scores: np.ndarray,
    config: AudioSegmentDetectorConfig,
) -> np.ndarray:
    energies = np.array([w.energy for w in windows], dtype=np.float32)
    dynamic_threshold = max(
        config.score_threshold,
        float(np.quantile(scores, 0.70)) * 0.9,
    )
    energy_gate = energies >= float(np.quantile(energies, config.energy_threshold_quantile))
    active = (scores >= dynamic_threshold) & energy_gate

    # Bridge short inactive gaps to reduce over-splitting.
    bridged = active.copy()
    max_gap_windows = max(1, round(config.merge_gap_seconds / config.hop_seconds))
    false_runs = _find_runs(~active)
    for start, end in false_runs:
        run_len = end - start
        if (
            run_len <= max_gap_windows
            and start > 0
            and end < active.size
            and active[start - 1]
            and active[end]
        ):
            bridged[start:end] = True

    return bridged


def _windows_to_segments(
    windows: Sequence[_FeatureWindow],
    scores: np.ndarray,
    active_mask: np.ndarray,
    total_duration: float,
    config: AudioSegmentDetectorConfig,
) -> list[Segment]:
    if active_mask.size == 0:
        return []

    active_runs = _find_runs(active_mask)
    segments: list[Segment] = []

    for start_idx, end_idx in active_runs:
        start_window = windows[start_idx]
        end_window = windows[end_idx - 1]

        segment_start = max(0.0, start_window.start - config.padding_before)
        segment_end = min(total_duration, end_window.end + config.padding_after)
        duration = segment_end - segment_start

        if duration < config.min_song_duration:
            continue
        if duration > config.max_song_duration:
            continue

        confidence = float(np.mean(scores[start_idx:end_idx]))
        active_ratio = float(np.mean(active_mask[start_idx:end_idx]))
        if active_ratio < config.min_active_ratio:
            continue

        segments.append(
            Segment(
                start=segment_start,
                end=segment_end,
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    return _merge_segments(segments, config.merge_gap_seconds, total_duration)


def _merge_segments(
    segments: Sequence[Segment],
    max_gap_seconds: float,
    total_duration: float,
) -> list[Segment]:
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: s.start)
    merged: list[Segment] = [ordered[0]]

    for current in ordered[1:]:
        previous = merged[-1]
        if current.start - previous.end <= max_gap_seconds:
            merged[-1] = Segment(
                start=previous.start,
                end=min(total_duration, max(previous.end, current.end)),
                confidence=max(previous.confidence, current.confidence),
                source=previous.source,
            )
        else:
            merged.append(current)

    return merged


def _find_runs(mask: Iterable[bool]) -> list[tuple[int, int]]:
    arr = np.asarray(list(mask), dtype=bool)
    if arr.size == 0:
        return []

    padded = np.pad(arr.astype(np.int8), (1, 1), constant_values=0)
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return list(zip(starts.tolist(), ends.tolist()))