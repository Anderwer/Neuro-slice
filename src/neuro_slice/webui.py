from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any

try:
    import gradio as gr
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "本地 WebUI 需要 Gradio。请先安装 `uv sync --extra web`，"
        "然后再启动 WebUI。"
    ) from exc

from neuro_slice.config import AppConfig, load_config
from neuro_slice.export.cutter import export_from_manifest
from neuro_slice.pipeline import run_pipeline


_UI_LOCK = threading.Lock()
_STOP_REQUESTED = threading.Event()


def _request_stop() -> tuple[str, str]:
    _STOP_REQUESTED.set()
    return "已请求停止，等待当前步骤结束...", "已收到停止请求。当前正在运行的步骤会尽快结束，后续片段不会继续处理。"


class _LiveLogBuffer(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self._status = "等待开始"
        self._partial_line = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            written = super().write(text)
            combined = self._partial_line + text
            lines = combined.splitlines(keepends=True)
            self._partial_line = ""

            for line in lines:
                if line.endswith("\n") or line.endswith("\r"):
                    self._update_status_from_line(line.strip())
                else:
                    self._partial_line = line

            return written

    def set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def get_status(self) -> str:
        with self._lock:
            return self._status

    def _update_status_from_line(self, line: str) -> None:
        if not line:
            return

        if line.startswith("Input video:"):
            self._status = "已读取输入视频"
        elif line.startswith("Video duration:"):
            self._status = "已获取视频时长"
        elif "Detected " in line and "candidate segments" in line:
            self._status = f"分段检测完成：{line}"
        elif line.startswith("[") and "]" in line and "confidence=" in line:
            self._status = f"正在处理片段：{line}"
        elif line.startswith("Loading faster-whisper model:"):
            self._status = f"正在加载转录模型：{line}"
        elif line.startswith("Prepended bundled CUDA runtime directories to PATH"):
            self._status = "已配置 CUDA 运行库路径"
        elif line.startswith("Registered CUDA runtime DLL directory:"):
            self._status = "已注册 CUDA 运行库目录"
        elif "Falling back to CPU/int8" in line:
            self._status = "GPU 不可用，已自动回退到 CPU/int8"
        elif line.startswith("Retrying transcription on CPU/int8."):
            self._status = "正在使用 CPU/int8 重试转录"
        elif line.startswith("Manifest written to:"):
            self._status = "已写入 manifest 文件"
        elif line.startswith("Recognition enabled for segment "):
            self._status = "已启用识曲，正在准备识别歌曲信息"
        elif line.startswith("Calling shared title recognition client."):
            self._status = "正在调用 LLM 识曲"
        elif line.startswith("Title recognition attempt "):
            self._status = line
        elif line.startswith("Recognition success for segment "):
            self._status = f"识曲成功：{line}"
        elif line.startswith("Recognition did not return a confident title for segment "):
            self._status = "识曲未返回足够确定的结果，正在使用默认标题"
        elif line.startswith("Shared title recognition client returned:"):
            self._status = f"识曲返回结果：{line}"
        elif line.startswith("Shared title recognition client returned no confident result."):
            self._status = "识曲没有返回足够确定的结果"
        elif line.startswith("Skipping title recognition because the cleaned transcript is too short."):
            self._status = "跳过识曲：歌词文本太短"
        elif line.startswith("Skipping title recognition because recognition is disabled."):
            self._status = "识曲当前处于关闭状态"
        elif line.startswith("Skipping title recognition because no API key is configured."):
            self._status = "识曲已开启，但未填写 API Key"
        elif line.startswith("Skipping title recognition because mode '"):
            self._status = f"跳过识曲：{line}"
        elif line.startswith("Title recognition failed:"):
            self._status = f"识曲失败：{line}"
        elif line.startswith("Recognition disabled for segment "):
            self._status = "当前片段未启用识曲，使用默认标题"


class _StatusBridge:
    def __init__(self, buffer: _LiveLogBuffer) -> None:
        self._buffer = buffer

    def __call__(self, message: str) -> None:
        self._buffer.set_status(message)
        self._buffer.write(message + "\n")

    def should_stop(self) -> bool:
        return _STOP_REQUESTED.is_set()


def _native_pick_file(
    *,
    title: str,
    filetypes: list[tuple[str, str]],
) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return ""

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(
            title=title,
            filetypes=filetypes,
        )
    finally:
        root.destroy()

    return selected or ""


def _native_pick_directory(*, title: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return ""

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title=title)
    finally:
        root.destroy()

    return selected or ""


def _browse_video() -> str:
    return _native_pick_file(
        title="选择视频文件",
        filetypes=[
            ("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v"),
            ("All files", "*.*"),
        ],
    )


def _browse_manifest() -> str:
    return _native_pick_file(
        title="选择 manifest JSON 文件",
        filetypes=[
            ("JSON files", "*.json"),
            ("All files", "*.*"),
        ],
    )


def _browse_config() -> str:
    return _native_pick_file(
        title="选择配置 TOML 文件",
        filetypes=[
            ("TOML files", "*.toml"),
            ("All files", "*.*"),
        ],
    )


def _browse_output_dir() -> str:
    return _native_pick_directory(title="选择导出目录")


def _load_config_or_default(config_path: str) -> AppConfig:
    text = (config_path or "").strip()
    return load_config(text) if text else AppConfig()


def _format_manifest_summary(manifest: dict[str, Any]) -> str:
    segments = manifest.get("segments", [])
    lines = [
        f"source_video: {manifest.get('source_video', '')}",
        f"output_dir: {manifest.get('output_dir', '')}",
        f"total_duration: {manifest.get('total_duration', '')}",
        f"count: {manifest.get('count', len(segments))}",
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


def _format_exports(exports: list[str]) -> str:
    if not exports:
        return "(no exports)"
    return "\n".join(exports)


def _safe_output_dir(output_dir: str, cfg: AppConfig) -> Path:
    target = (output_dir or "").strip() or cfg.project.output_dir
    path = Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _advanced_settings_path() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "webui_advanced_settings.json"


def _apply_saved_advanced_settings(cfg: AppConfig) -> AppConfig:
    path = _advanced_settings_path()
    if not path.exists():
        return cfg

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return cfg

    asr_payload = payload.get("asr", {}) if isinstance(payload, dict) else {}
    recognition_payload = payload.get("recognition", {}) if isinstance(payload, dict) else {}
    export_payload = payload.get("export", {}) if isinstance(payload, dict) else {}
    metadata_payload = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    segment_payload = payload.get("segment", {}) if isinstance(payload, dict) else {}

    if isinstance(asr_payload, dict):
        cfg.asr.model_size = str(asr_payload.get("model_size", cfg.asr.model_size) or cfg.asr.model_size)
        cfg.asr.device = str(asr_payload.get("device", cfg.asr.device) or cfg.asr.device)
        cfg.asr.compute_type = str(asr_payload.get("compute_type", cfg.asr.compute_type) or cfg.asr.compute_type)

    if isinstance(segment_payload, dict):
        with contextlib.suppress(Exception):
            cfg.segment.padding_before = float(segment_payload.get("padding_before", cfg.segment.padding_before))
        with contextlib.suppress(Exception):
            cfg.segment.padding_after = float(segment_payload.get("padding_after", cfg.segment.padding_after))

    if isinstance(export_payload, dict):
        cfg.export.export_video = bool(export_payload.get("export_video", cfg.export.export_video))
        cfg.export.export_audio = bool(export_payload.get("export_audio", cfg.export.export_audio))

    if isinstance(metadata_payload, dict):
        cfg.metadata.singer = str(metadata_payload.get("singer", cfg.metadata.singer) or cfg.metadata.singer)
        cfg.metadata.date = str(metadata_payload.get("date", cfg.metadata.date) or cfg.metadata.date)

    if isinstance(recognition_payload, dict):
        cfg.recognition.enabled = bool(recognition_payload.get("enabled", cfg.recognition.enabled))
        cfg.recognition.mode = str(recognition_payload.get("mode", cfg.recognition.mode) or cfg.recognition.mode)
        cfg.recognition.model = str(recognition_payload.get("model", cfg.recognition.model) or cfg.recognition.model)
        cfg.recognition.base_url = str(recognition_payload.get("base_url", cfg.recognition.base_url) or cfg.recognition.base_url)
        cfg.recognition.api_key = str(recognition_payload.get("api_key", cfg.recognition.api_key) or cfg.recognition.api_key)

    return cfg


def _save_advanced_settings(
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
) -> tuple[str, str]:
    path = _advanced_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "asr": {
            "model_size": (asr_model_size or "").strip(),
            "device": (asr_device or "").strip(),
            "compute_type": (asr_compute_type or "").strip(),
        },
        "segment": {
            "padding_before": float(padding_before),
            "padding_after": float(padding_after),
        },
        "export": {
            "export_video": bool(export_video),
            "export_audio": bool(export_audio),
        },
        "metadata": {
            "singer": (singer or "").strip(),
            "date": (date_text or "").strip(),
        },
        "recognition": {
            "enabled": bool(recognition_enabled),
            "mode": (recognition_mode or "").strip(),
            "api_key": (recognition_api_key or "").strip(),
            "base_url": (recognition_base_url or "").strip(),
            "model": (recognition_model or "").strip(),
        },
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return "已保存高级设置", f"已保存当前高级设置到：{path}"


def _advanced_settings_export_toml_path() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "webui_advanced_settings.toml"


def _to_toml_string(
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
) -> str:
    lines = [
        "[segment]",
        f"padding_before = {float(padding_before):.1f}",
        f"padding_after = {float(padding_after):.1f}",
        "",
        "[export]",
        f"export_video = {'true' if export_video else 'false'}",
        f"export_audio = {'true' if export_audio else 'false'}",
        "",
        "[metadata]",
        f'date = "{(date_text or "").strip()}"',
        f'singer = "{(singer or "").strip()}"',
        "",
        "[asr]",
        f'model_size = "{(asr_model_size or "").strip()}"',
        f'device = "{(asr_device or "").strip()}"',
        f'compute_type = "{(asr_compute_type or "").strip()}"',
        "",
        "[recognition]",
        f"enabled = {'true' if recognition_enabled else 'false'}",
        f'mode = "{(recognition_mode or "").strip()}"',
        f'api_key = "{(recognition_api_key or "").strip()}"',
        f'base_url = "{(recognition_base_url or "").strip()}"',
        f'model = "{(recognition_model or "").strip()}"',
        "",
    ]
    return "\n".join(lines)


def _export_advanced_settings_toml(
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
) -> tuple[str, str]:
    path = _advanced_settings_export_toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _to_toml_string(
            asr_model_size,
            asr_device,
            asr_compute_type,
            padding_before,
            padding_after,
            export_video,
            export_audio,
            singer,
            date_text,
            recognition_enabled,
            recognition_mode,
            recognition_api_key,
            recognition_base_url,
            recognition_model,
        ),
        encoding="utf-8",
    )
    return "已导出当前配置", f"已导出当前高级设置为 TOML：{path}"


def _reset_advanced_settings() -> tuple[str, str, str, float, float, bool, bool, str, str, bool, str, str, str, str, str]:
    path = _advanced_settings_path()
    with contextlib.suppress(Exception):
        path.unlink(missing_ok=True)

    cfg = AppConfig()
    return (
        cfg.asr.model_size,
        cfg.asr.device,
        cfg.asr.compute_type,
        float(cfg.segment.padding_before),
        float(cfg.segment.padding_after),
        bool(cfg.export.export_video),
        bool(cfg.export.export_audio),
        cfg.metadata.singer,
        cfg.metadata.date,
        bool(cfg.recognition.enabled),
        cfg.recognition.mode,
        cfg.recognition.api_key,
        cfg.recognition.base_url,
        cfg.recognition.model,
        "已重置高级设置（并清除已保存的本地高级设置）",
    )


def _apply_ui_overrides(
    *,
    cfg: AppConfig,
    video_path: str,
    output_dir: str,
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
) -> AppConfig:
    cfg.project.output_dir = str(_safe_output_dir(output_dir, cfg))
    cfg.input.video_path = video_path

    cfg.asr.model_size = (asr_model_size or cfg.asr.model_size).strip()
    cfg.asr.device = (asr_device or cfg.asr.device).strip()
    cfg.asr.compute_type = (asr_compute_type or "").strip()

    cfg.segment.padding_before = float(padding_before)
    cfg.segment.padding_after = float(padding_after)

    cfg.export.export_video = bool(export_video)
    cfg.export.export_audio = bool(export_audio)

    cfg.metadata.singer = (singer or cfg.metadata.singer).strip() or cfg.metadata.singer
    cfg.metadata.date = (date_text or cfg.metadata.date).strip() or cfg.metadata.date

    cfg.recognition.enabled = bool(recognition_enabled)
    cfg.recognition.mode = (recognition_mode or cfg.recognition.mode).strip()
    cfg.recognition.api_key = (recognition_api_key or "").strip()
    cfg.recognition.base_url = (recognition_base_url or "").strip()
    cfg.recognition.model = (recognition_model or cfg.recognition.model).strip()

    return cfg


def _run_analyze(
    *,
    video_path: str,
    output_dir: str,
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
    output_buffer: io.StringIO | None = None,
) -> tuple[str, str, str]:
    if not video_path.strip():
        raise ValueError("请先选择要处理的视频文件。")

    cfg = AppConfig()
    cfg = _apply_ui_overrides(
        cfg=cfg,
        video_path=video_path,
        output_dir=output_dir,
        asr_model_size=asr_model_size,
        asr_device=asr_device,
        asr_compute_type=asr_compute_type,
        padding_before=padding_before,
        padding_after=padding_after,
        export_video=False,
        export_audio=False,
        singer=singer,
        date_text=date_text,
        recognition_enabled=recognition_enabled,
        recognition_mode=recognition_mode,
        recognition_api_key=recognition_api_key,
        recognition_base_url=recognition_base_url,
        recognition_model=recognition_model,
    )

    log_buffer = output_buffer or io.StringIO()
    status_bridge = _StatusBridge(log_buffer) if isinstance(log_buffer, _LiveLogBuffer) else None
    with contextlib.redirect_stdout(log_buffer), contextlib.redirect_stderr(log_buffer):
        manifest, _ = run_pipeline(
            config=cfg,
            video_path=video_path,
            status_callback=status_bridge,
        )

    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    summary = _format_manifest_summary(manifest)
    logs = log_buffer.getvalue().strip()

    return summary, manifest_text, logs


def _run_analyze_and_export(
    *,
    video_path: str,
    output_dir: str,
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
    output_buffer: io.StringIO | None = None,
) -> tuple[str, str, str, str]:
    if not video_path.strip():
        raise ValueError("请先选择要处理的视频文件。")

    cfg = AppConfig()
    cfg = _apply_ui_overrides(
        cfg=cfg,
        video_path=video_path,
        output_dir=output_dir,
        asr_model_size=asr_model_size,
        asr_device=asr_device,
        asr_compute_type=asr_compute_type,
        padding_before=padding_before,
        padding_after=padding_after,
        export_video=export_video,
        export_audio=export_audio,
        singer=singer,
        date_text=date_text,
        recognition_enabled=recognition_enabled,
        recognition_mode=recognition_mode,
        recognition_api_key=recognition_api_key,
        recognition_base_url=recognition_base_url,
        recognition_model=recognition_model,
    )

    log_buffer = output_buffer or io.StringIO()
    status_bridge = _StatusBridge(log_buffer) if isinstance(log_buffer, _LiveLogBuffer) else None
    with contextlib.redirect_stdout(log_buffer), contextlib.redirect_stderr(log_buffer):
        manifest, exports = run_pipeline(
            config=cfg,
            video_path=video_path,
            status_callback=status_bridge,
        )

    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    summary = _format_manifest_summary(manifest)
    export_text = _format_exports(exports)
    logs = log_buffer.getvalue().strip()

    return summary, manifest_text, export_text, logs


def _run_export_only(
    *,
    video_path: str,
    manifest_path: str,
    output_dir: str,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    output_buffer: io.StringIO | None = None,
) -> tuple[str, str]:
    if not video_path.strip():
        raise ValueError("请先选择要处理的视频文件。")
    if not manifest_path.strip():
        raise ValueError("请先选择 manifest JSON 文件。")

    cfg = AppConfig()
    cfg.project.output_dir = str(_safe_output_dir(output_dir, cfg))
    cfg.input.video_path = video_path
    cfg.export.export_video = bool(export_video)
    cfg.export.export_audio = bool(export_audio)
    cfg.metadata.singer = (singer or cfg.metadata.singer).strip() or cfg.metadata.singer
    cfg.metadata.date = (date_text or cfg.metadata.date).strip() or cfg.metadata.date

    log_buffer = output_buffer or io.StringIO()
    with contextlib.redirect_stdout(log_buffer), contextlib.redirect_stderr(log_buffer):
        exported = export_from_manifest(
            video_path=video_path,
            manifest_path=manifest_path,
            output_dir=cfg.project.output_dir,
            config=cfg,
        )

    export_text = _format_exports([str(path) for path in exported])
    logs = log_buffer.getvalue().strip()

    return export_text, logs


def _stream_analyze(
    video_path: str,
    output_dir: str,
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
):
    with _UI_LOCK:
        _STOP_REQUESTED.clear()
        buffer = _LiveLogBuffer()
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                buffer.set_status("准备开始分析")
                summary, manifest_text, _ = _run_analyze(
                    video_path=video_path,
                    output_dir=output_dir,
                    asr_model_size=asr_model_size,
                    asr_device=asr_device,
                    asr_compute_type=asr_compute_type,
                    padding_before=padding_before,
                    padding_after=padding_after,
                    singer=singer,
                    date_text=date_text,
                    recognition_enabled=recognition_enabled,
                    recognition_mode=recognition_mode,
                    recognition_api_key=recognition_api_key,
                    recognition_base_url=recognition_base_url,
                    recognition_model=recognition_model,
                    output_buffer=buffer,
                )
                result["summary"] = summary
                result["manifest_text"] = manifest_text
                if _STOP_REQUESTED.is_set():
                    buffer.set_status("分析已停止")
                else:
                    buffer.set_status("分析完成")
            except Exception:
                result["error"] = traceback.format_exc()
                buffer.set_status("分析失败")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive():
            if _STOP_REQUESTED.is_set():
                yield "已请求停止，等待当前步骤结束...", "", "", buffer.getvalue().strip()
            else:
                yield buffer.get_status(), "", "", buffer.getvalue().strip()
            time.sleep(0.5)

        thread.join()

        if "error" in result:
            logs = buffer.getvalue().strip()
            if logs:
                logs += "\n\n"
            logs += result["error"]
            yield buffer.get_status(), "", "", logs
            return

        final_status = "分析已停止" if _STOP_REQUESTED.is_set() else buffer.get_status()
        yield (
            final_status,
            result.get("summary", ""),
            result.get("manifest_text", ""),
            buffer.getvalue().strip(),
        )
        _STOP_REQUESTED.clear()


def _stream_analyze_and_export(
    video_path: str,
    output_dir: str,
    asr_model_size: str,
    asr_device: str,
    asr_compute_type: str,
    padding_before: float,
    padding_after: float,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
    recognition_enabled: bool,
    recognition_mode: str,
    recognition_api_key: str,
    recognition_base_url: str,
    recognition_model: str,
):
    with _UI_LOCK:
        _STOP_REQUESTED.clear()
        buffer = _LiveLogBuffer()
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                buffer.set_status("准备开始分析并导出")
                summary, manifest_text, export_text, _ = _run_analyze_and_export(
                    video_path=video_path,
                    output_dir=output_dir,
                    asr_model_size=asr_model_size,
                    asr_device=asr_device,
                    asr_compute_type=asr_compute_type,
                    padding_before=padding_before,
                    padding_after=padding_after,
                    export_video=export_video,
                    export_audio=export_audio,
                    singer=singer,
                    date_text=date_text,
                    recognition_enabled=recognition_enabled,
                    recognition_mode=recognition_mode,
                    recognition_api_key=recognition_api_key,
                    recognition_base_url=recognition_base_url,
                    recognition_model=recognition_model,
                    output_buffer=buffer,
                )
                result["summary"] = summary
                result["manifest_text"] = manifest_text
                result["export_text"] = export_text
                if _STOP_REQUESTED.is_set():
                    buffer.set_status("分析并导出已停止")
                else:
                    buffer.set_status("分析并导出完成")
            except Exception:
                result["error"] = traceback.format_exc()
                buffer.set_status("分析并导出失败")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive():
            if _STOP_REQUESTED.is_set():
                yield "已请求停止，等待当前步骤结束...", "", "", "", buffer.getvalue().strip()
            else:
                yield buffer.get_status(), "", "", "", buffer.getvalue().strip()
            time.sleep(0.5)

        thread.join()

        if "error" in result:
            logs = buffer.getvalue().strip()
            if logs:
                logs += "\n\n"
            logs += result["error"]
            yield buffer.get_status(), "", "", "", logs
            return

        final_status = "分析并导出已停止" if _STOP_REQUESTED.is_set() else buffer.get_status()
        yield (
            final_status,
            result.get("summary", ""),
            result.get("manifest_text", ""),
            result.get("export_text", ""),
            buffer.getvalue().strip(),
        )
        _STOP_REQUESTED.clear()


def _stream_export_only(
    video_path: str,
    manifest_path: str,
    output_dir: str,
    export_video: bool,
    export_audio: bool,
    singer: str,
    date_text: str,
):
    with _UI_LOCK:
        _STOP_REQUESTED.clear()
        buffer = _LiveLogBuffer()
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                buffer.set_status("准备开始导出")
                export_text, _ = _run_export_only(
                    video_path=video_path,
                    manifest_path=manifest_path,
                    output_dir=output_dir,
                    export_video=export_video,
                    export_audio=export_audio,
                    singer=singer,
                    date_text=date_text,
                    output_buffer=buffer,
                )
                result["export_text"] = export_text
                if _STOP_REQUESTED.is_set():
                    buffer.set_status("导出已停止")
                else:
                    buffer.set_status("导出完成")
            except Exception:
                result["error"] = traceback.format_exc()
                buffer.set_status("导出失败")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive():
            if _STOP_REQUESTED.is_set():
                yield "已请求停止，等待当前步骤结束...", "", buffer.getvalue().strip()
            else:
                yield buffer.get_status(), "", buffer.getvalue().strip()
            time.sleep(0.5)

        thread.join()

        if "error" in result:
            logs = buffer.getvalue().strip()
            if logs:
                logs += "\n\n"
            logs += result["error"]
            yield buffer.get_status(), "", logs
            return

        final_status = "导出已停止" if _STOP_REQUESTED.is_set() else buffer.get_status()
        yield (
            final_status,
            result.get("export_text", ""),
            buffer.getvalue().strip(),
        )
        _STOP_REQUESTED.clear()


def create_app(initial_config: AppConfig | None = None) -> gr.Blocks:
    cfg = _apply_saved_advanced_settings(initial_config or AppConfig())

    with gr.Blocks(title="Neuro-slice 本地界面") as app:
        gr.Markdown(
            """
# Neuro-slice 本地界面

这个界面分为两层：

## 基础模式
适合直接使用：
- 选择要处理的视频文件
- 选择导出目录
- 按需加载 TOML 配置文件
- 直接开始分析或导出

## 高级模式
适合需要调参时使用：
- ASR 模型 / 设备 / 精度
- 留白秒数
- 导出视频 / 音频
- 歌手 / 日期
- 识曲相关配置

这个界面适用于 **本机桌面使用**。
            """.strip()
        )

        with gr.Row():
            with gr.Column(scale=3):
                video_path = gr.Textbox(
                    label="视频文件",
                    placeholder="请选择本地视频文件...",
                    value=cfg.input.video_path,
                )
            with gr.Column(scale=1, min_width=120):
                browse_video = gr.Button("选择视频")

        with gr.Row():
            with gr.Column(scale=3):
                output_dir = gr.Textbox(
                    label="导出目录",
                    placeholder="请选择导出目录...",
                    value=cfg.project.output_dir,
                )
            with gr.Column(scale=1, min_width=120):
                browse_output = gr.Button("选择目录")

        with gr.Row():
            with gr.Column(scale=3):
                manifest_path = gr.Textbox(
                    label="Manifest 文件（仅导出模式）",
                    placeholder="可选的 manifest JSON 路径...",
                )
            with gr.Column(scale=1, min_width=120):
                browse_manifest = gr.Button("选择 Manifest")

        gr.Markdown(
            """
> **Manifest 是什么？**
>
> - `分析` / `分析并导出` 会先生成一个 manifest（通常是 `songs.json`）。
> - 这个文件记录了每首歌的时间范围、标题、转录、导出相关信息。
> - 如果你后面想微调时间范围或只重新导出，不需要重新分析整条视频，只要导入这个 manifest 即可。
> - `导出已有 Manifest` 按钮就是读取已经生成好的 manifest，再重新切片导出。
            """.strip()
        )

        with gr.Accordion("基础设置", open=True):
            with gr.Row():
                padding_before = gr.Slider(
                    label="前置留白（秒）",
                    minimum=0.0,
                    maximum=15.0,
                    step=0.5,
                    value=cfg.segment.padding_before,
                )
                padding_after = gr.Slider(
                    label="后置留白（秒）",
                    minimum=0.0,
                    maximum=15.0,
                    step=0.5,
                    value=cfg.segment.padding_after,
                )

            with gr.Row():
                export_video = gr.Checkbox(
                    label="导出视频",
                    value=cfg.export.export_video,
                )
                export_audio = gr.Checkbox(
                    label="导出音频",
                    value=cfg.export.export_audio,
                )

            with gr.Row():
                singer = gr.Textbox(
                    label="歌手 / 主播标记",
                    value=cfg.metadata.singer,
                )
                date_text = gr.Textbox(
                    label="日期",
                    value=cfg.metadata.date,
                )

        with gr.Accordion("高级设置（ASR / 识曲 / 导出细项）", open=False):
            gr.Markdown(
                """
> **识曲说明**
>
> - 默认建议关闭识曲，只做切段和导出。
> - 只有在你想让系统根据歌词自动猜歌名时，再开启“启用识曲”。
> - 开启后请至少填写 `API Key`。
> - 如果你使用的是 OpenAI 兼容接口，再填写 `Base URL`。
> - 识曲请求遇到临时网络波动时，会自动重试最多 5 次。
                """.strip()
            )

            with gr.Row():
                asr_model_size = gr.Dropdown(
                    label="Whisper 模型",
                    choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                    value=cfg.asr.model_size,
                    allow_custom_value=True,
                )
                asr_device = gr.Dropdown(
                    label="ASR 设备",
                    choices=["auto", "cuda", "cpu"],
                    value=cfg.asr.device,
                )
                asr_compute_type = gr.Dropdown(
                    label="计算精度",
                    choices=["", "float16", "int8", "int8_float16", "float32"],
                    value=cfg.asr.compute_type,
                    allow_custom_value=True,
                )

            with gr.Row():
                recognition_enabled = gr.Checkbox(
                    label="启用识曲（默认关闭）",
                    value=cfg.recognition.enabled,
                )
                recognition_mode = gr.Dropdown(
                    label="识曲模式",
                    choices=["none", "openai"],
                    value=cfg.recognition.mode,
                )

            with gr.Row():
                recognition_model = gr.Textbox(
                    label="识曲模型",
                    value=cfg.recognition.model,
                    placeholder="例如：gpt-4o-mini",
                )
                recognition_base_url = gr.Textbox(
                    label="Base URL（兼容接口时填写）",
                    value=cfg.recognition.base_url,
                    placeholder="例如：https://api.openai.com/v1",
                )

            recognition_api_key = gr.Textbox(
                label="API Key（启用识曲时必填）",
                value=cfg.recognition.api_key,
                type="password",
                placeholder="请输入识曲服务的 API Key",
            )

            save_advanced_btn = gr.Button("保存当前高级设置", variant="secondary")

        with gr.Row():
            analyze_btn = gr.Button("仅分析", variant="secondary")
            analyze_export_btn = gr.Button("分析并导出", variant="primary")
            export_only_btn = gr.Button("导出已有 Manifest", variant="secondary")
            stop_btn = gr.Button("停止当前任务", variant="stop")

        with gr.Row():
            with gr.Column():
                status_output = gr.Textbox(
                    label="当前运行状态",
                    lines=2,
                    interactive=False,
                )
                summary_output = gr.Textbox(
                    label="摘要",
                    lines=12,
                    interactive=False,
                )
                exports_output = gr.Textbox(
                    label="已导出文件",
                    lines=8,
                    interactive=False,
                )
            with gr.Column():
                manifest_output = gr.Code(
                    label="Manifest JSON",
                    language="json",
                    interactive=False,
                )
                logs_output = gr.Textbox(
                    label="日志",
                    lines=20,
                    interactive=False,
                )

        browse_video.click(fn=_browse_video, outputs=video_path)
        browse_output.click(fn=_browse_output_dir, outputs=output_dir)
        browse_manifest.click(fn=_browse_manifest, outputs=manifest_path)
        save_advanced_btn.click(
            fn=_save_advanced_settings,
            inputs=[
                asr_model_size,
                asr_device,
                asr_compute_type,
                padding_before,
                padding_after,
                export_video,
                export_audio,
                singer,
                date_text,
                recognition_enabled,
                recognition_mode,
                recognition_api_key,
                recognition_base_url,
                recognition_model,
            ],
            outputs=[status_output, logs_output],
            queue=False,
        )
        stop_btn.click(
            fn=_request_stop,
            outputs=[status_output, logs_output],
            queue=False,
        )

        analyze_btn.click(
            fn=_stream_analyze,
            inputs=[
                video_path,
                output_dir,
                asr_model_size,
                asr_device,
                asr_compute_type,
                padding_before,
                padding_after,
                singer,
                date_text,
                recognition_enabled,
                recognition_mode,
                recognition_api_key,
                recognition_base_url,
                recognition_model,
            ],
            outputs=[status_output, summary_output, manifest_output, logs_output],
            show_progress="full",
        )

        analyze_export_btn.click(
            fn=_stream_analyze_and_export,
            inputs=[
                video_path,
                output_dir,
                asr_model_size,
                asr_device,
                asr_compute_type,
                padding_before,
                padding_after,
                export_video,
                export_audio,
                singer,
                date_text,
                recognition_enabled,
                recognition_mode,
                recognition_api_key,
                recognition_base_url,
                recognition_model,
            ],
            outputs=[status_output, summary_output, manifest_output, exports_output, logs_output],
            show_progress="full",
        )

        export_only_btn.click(
            fn=_stream_export_only,
            inputs=[
                video_path,
                manifest_path,
                output_dir,
                export_video,
                export_audio,
                singer,
                date_text,
            ],
            outputs=[status_output, exports_output, logs_output],
            show_progress="full",
        )

    return app


def launch_webui(
    *,
    config: AppConfig | None = None,
    host: str = "127.0.0.1",
    port: int = 7860,
    share: bool = False,
) -> None:
    app = create_app(initial_config=config)
    app.launch(
        server_name=host,
        server_port=port,
        share=share,
        inbrowser=True,
        show_api=False,
        prevent_thread_lock=True,
    )

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Keyboard interruption in main thread... closing server.")
    finally:
        with contextlib.suppress(Exception):
            app.close()


def main() -> None:
    launch_webui()


if __name__ == "__main__":
    main()