from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")
with contextlib.suppress(Exception):
    sys.stderr.reconfigure(encoding="utf-8")


def _emit_status(message: str) -> None:
    print(f"LEGACY_STATUS={message}", flush=True)


class _Heartbeat:
    def __init__(self, interval_seconds: float = 5.0) -> None:
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            elapsed_seconds = int(time.monotonic() - self._started_at)
            _emit_status(f"正在运行 inaSpeechSegmenter 分段（已耗时 {elapsed_seconds} 秒）")


def run(cmd: list[str]) -> None:
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def probe_duration(video_file: str | Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_file),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return float((result.stdout or "").strip())


def extract_analysis_audio(
    video_file: str | Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    start: float | None = None,
    end: float | None = None,
    status_message: str = "正在提取 legacy detector 分析音频",
) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav = Path(tmp.name)
    tmp.close()

    command = [
        "ffmpeg",
        "-y",
    ]

    if start is not None:
        command.extend(["-ss", f"{start:.3f}"])

    command.extend(
        [
            "-i",
            str(video_file),
        ]
    )

    if end is not None:
        command.extend(["-to", f"{end:.3f}"])

    command.extend(
        [
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            str(tmp_wav),
        ]
    )

    _emit_status(status_message)
    run(command)
    return tmp_wav


def detect_music_segments(
    video_file: str | Path,
    min_music_duration: int = 20,
    merge_gap: float = 90.0,
) -> list[tuple[float, float]]:
    try:
        from inaSpeechSegmenter import Segmenter
    except Exception as exc:
        raise RuntimeError(
            "inaSpeechSegmenter is not installed in the legacy detector environment."
        ) from exc

    _emit_status("正在初始化 legacy detector")
    segmenter = Segmenter()
    tmp_wav = extract_analysis_audio(video_file)

    heartbeat = _Heartbeat(interval_seconds=5.0)
    try:
        _emit_status("正在运行 inaSpeechSegmenter 分段")
        heartbeat.start()
        segmentation = segmenter(str(tmp_wav))
    finally:
        heartbeat.stop()
        try:
            tmp_wav.unlink(missing_ok=True)
        except Exception:
            pass

    music = [
        (float(start), float(end))
        for label, start, end in segmentation
        if str(label) == "music" and (float(end) - float(start)) >= min_music_duration
    ]

    if not music:
        _emit_status("未检测到满足条件的音乐段")
        return []

    _emit_status(f"原始音乐段数量：{len(music)}，正在合并相邻区间")
    merged: list[tuple[float, float]] = []
    current_start, current_end = music[0]

    for start, end in music[1:]:
        if start - current_end < merge_gap:
            current_end = max(current_end, end)
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end

    merged.append((current_start, current_end))
    _emit_status(f"legacy detector 检测完成，共 {len(merged)} 个合并后区间")
    return merged


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: python detect_segments_tf.py <video_path> [min_music_duration]",
            file=sys.stderr,
        )
        return 1

    video = sys.argv[1]
    min_duration = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    try:
        _emit_status("legacy detector 子进程已启动")
        segments = detect_music_segments(video, min_music_duration=min_duration)
    except Exception as exc:
        print(f"LEGACY_DETECTOR_ERROR={exc}", file=sys.stderr)
        return 2

    payload = json.dumps(segments, ensure_ascii=False)
    _emit_status("正在输出 SEGMENTS_JSON")
    print("SEGMENTS_JSON=" + payload, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())