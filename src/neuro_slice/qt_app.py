from __future__ import annotations

import contextlib
import copy
import json
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QSizePolicy,
    QStyle,
)

from neuro_slice.config import AppConfig
from neuro_slice.export.cutter import export_from_manifest
from neuro_slice.pipeline import run_pipeline


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_dir() -> Path:
    path = _project_root() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _advanced_settings_path() -> Path:
    return _runtime_dir() / "qt_advanced_settings.json"


def _exported_config_path() -> Path:
    return _runtime_dir() / "qt_exported_config.toml"


def _safe_output_dir(output_dir: str, default_dir: str) -> Path:
    target = (output_dir or "").strip() or default_dir
    path = Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clone_config(cfg: AppConfig) -> AppConfig:
    return AppConfig.model_validate(cfg.model_dump(mode="python"))


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


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _apply_saved_advanced_settings(cfg: AppConfig) -> AppConfig:
    payload = _load_json(_advanced_settings_path())
    if not payload:
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
        cfg.recognition.base_url = str(
            recognition_payload.get("base_url", cfg.recognition.base_url) or cfg.recognition.base_url
        )
        cfg.recognition.api_key = str(
            recognition_payload.get("api_key", cfg.recognition.api_key) or cfg.recognition.api_key
        )

    return cfg


def _advanced_settings_payload_from_values(
    *,
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
) -> dict[str, Any]:
    return {
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


def _write_toml_from_config(cfg: AppConfig, path: Path) -> None:
    lines = [
        "[project]",
        f"name = {_toml_quote(cfg.project.name)}",
        f"output_dir = {_toml_quote(cfg.project.output_dir)}",
        "",
        "[input]",
        f"video_path = {_toml_quote(cfg.input.video_path)}",
        "",
        "[detector]",
        f"backend = {_toml_quote(cfg.detector.backend)}",
        f"auto_setup_legacy_env = {'true' if cfg.detector.auto_setup_legacy_env else 'false'}",
        f"legacy_env_dir = {_toml_quote(cfg.detector.legacy_env_dir)}",
        f"legacy_python_path = {_toml_quote(cfg.detector.legacy_python_path)}",
        f"legacy_script_path = {_toml_quote(cfg.detector.legacy_script_path)}",
        f"min_music_duration = {cfg.detector.min_music_duration}",
        f"merge_gap_seconds = {cfg.detector.merge_gap_seconds}",
        "",
        "[segment]",
        f"window_seconds = {cfg.segment.window_seconds}",
        f"hop_seconds = {cfg.segment.hop_seconds}",
        f"min_song_duration = {cfg.segment.min_song_duration}",
        f"max_song_duration = {cfg.segment.max_song_duration}",
        f"merge_gap_seconds = {cfg.segment.merge_gap_seconds}",
        f"padding_before = {cfg.segment.padding_before}",
        f"padding_after = {cfg.segment.padding_after}",
        "",
        "[audio]",
        f"sample_rate = {cfg.audio.sample_rate}",
        f"channels = {cfg.audio.channels}",
        f"preprocess_filter = {_toml_quote(cfg.audio.preprocess_filter)}",
        "",
        "[asr]",
        f"enabled = {'true' if cfg.asr.enabled else 'false'}",
        f"provider = {_toml_quote(cfg.asr.provider)}",
        f"model_size = {_toml_quote(cfg.asr.model_size)}",
        f"device = {_toml_quote(cfg.asr.device)}",
        f"compute_type = {_toml_quote(cfg.asr.compute_type)}",
        f"language = {_toml_quote(cfg.asr.language)}",
        f"beam_size = {cfg.asr.beam_size}",
        f"best_of = {cfg.asr.best_of}",
        f"vad_filter = {'true' if cfg.asr.vad_filter else 'false'}",
        f"condition_on_previous_text = {'true' if cfg.asr.condition_on_previous_text else 'false'}",
        f"initial_prompt = {_toml_quote(cfg.asr.initial_prompt)}",
        "",
        "[recognition]",
        f"enabled = {'true' if cfg.recognition.enabled else 'false'}",
        f"mode = {_toml_quote(cfg.recognition.mode)}",
        f"api_key = {_toml_quote(cfg.recognition.api_key)}",
        f"base_url = {_toml_quote(cfg.recognition.base_url)}",
        f"model = {_toml_quote(cfg.recognition.model)}",
        f"request_timeout_seconds = {cfg.recognition.request_timeout_seconds}",
        f"max_lyrics_chars = {cfg.recognition.max_lyrics_chars}",
        f"api_sleep_seconds = {cfg.recognition.api_sleep_seconds}",
        "",
        "[export]",
        f"export_video = {'true' if cfg.export.export_video else 'false'}",
        f"export_audio = {'true' if cfg.export.export_audio else 'false'}",
        f"video_container = {_toml_quote(cfg.export.video_container)}",
        f"audio_format = {_toml_quote(cfg.export.audio_format)}",
        f"video_codec = {_toml_quote(cfg.export.video_codec)}",
        f"audio_codec = {_toml_quote(cfg.export.audio_codec)}",
        f"filename_pattern = {_toml_quote(cfg.export.filename_pattern)}",
        "",
        "[metadata]",
        f"date = {_toml_quote(cfg.metadata.date)}",
        f"singer = {_toml_quote(cfg.metadata.singer)}",
        "",
        "[review]",
        f"write_manifest = {'true' if cfg.review.write_manifest else 'false'}",
        f"manifest_name = {_toml_quote(cfg.review.manifest_name)}",
        "",
        "[logging]",
        f"level = {_toml_quote(cfg.logging.level)}",
        f"rich = {'true' if cfg.logging.rich else 'false'}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass(slots=True)
class TaskConfig:
    mode: str
    config: AppConfig
    video_path: str
    manifest_path: str
    output_dir: str


class QtPipelineBridge:
    def __init__(self, worker: "TaskWorker") -> None:
        self._worker = worker
        self._source_video = worker.task.video_path
        self._output_dir = worker.task.output_dir
        self._total_count: int | None = None
        self._segments: dict[int, dict[str, Any]] = {}
        self._exports: list[str] = []

    def __call__(self, message: str) -> None:
        text = str(message)
        self._worker.status_changed.emit(text)
        self._worker.log_message.emit(text)
        self._handle_message(text)

    def print(self, message: str) -> None:
        text = str(message)
        self._worker.log_message.emit(text)
        self._handle_message(text)

    def should_stop(self) -> bool:
        return self._worker.stop_requested.is_set()

    def _handle_message(self, message: str) -> None:
        line = (message or "").strip()
        if not line:
            return

        changed = False

        if line.startswith("Input video:"):
            self._source_video = line.split(":", 1)[1].strip()
            changed = True

        elif line.startswith("Output directory:"):
            self._output_dir = line.split(":", 1)[1].strip()
            changed = True

        elif line.startswith("Detected ") and "candidate segments" in line:
            parts = line.split()
            if len(parts) >= 2:
                with contextlib.suppress(Exception):
                    self._total_count = int(parts[1])
                    changed = True

        elif line.startswith("[") and "]" in line and "->" in line and "confidence=" in line:
            try:
                index_text = line.split("]", 1)[0].lstrip("[")
                current_index_text, total_index_text = index_text.split("/", 1)
                segment_index = int(current_index_text)
                self._total_count = int(total_index_text)

                remainder = line.split("]", 1)[1].strip()
                time_part, confidence_part = remainder.split("(confidence=", 1)
                start_text, end_text = [item.strip() for item in time_part.split("->", 1)]
                start_value = float(start_text.rstrip("s"))
                end_value = float(end_text.rstrip("s"))
                confidence_value = float(confidence_part.rstrip(")"))

                self._segments[segment_index] = {
                    "index": segment_index,
                    "start": round(start_value, 3),
                    "end": round(end_value, 3),
                    "duration": round(end_value - start_value, 3),
                    "confidence": round(confidence_value, 4),
                    "title": self._segments.get(segment_index, {}).get("title", f"song_{segment_index:02d}"),
                }
                changed = True
            except Exception:
                pass

        elif line.startswith("Recognition success for segment "):
            try:
                prefix, title = line.split(":", 1)
                segment_index = int(prefix.split("segment", 1)[1].strip())
                segment = self._segments.setdefault(segment_index, {"index": segment_index})
                segment["title"] = title.strip()
                changed = True
            except Exception:
                pass

        elif line.startswith("Recognition disabled for segment ") and "using fallback title " in line:
            try:
                prefix, title = line.split("using fallback title ", 1)
                segment_index = int(prefix.split("segment", 1)[1].split(";", 1)[0].strip())
                clean_title = title.strip().rstrip(".")
                segment = self._segments.setdefault(segment_index, {"index": segment_index})
                segment["title"] = clean_title
                changed = True
            except Exception:
                pass

        elif line.startswith("Output #0, ") and "to '" in line and "':" in line:
            try:
                path_text = line.split("to '", 1)[1].rsplit("':", 1)[0]
                lowered = path_text.lower()
                if not lowered.endswith(".wav") and path_text not in self._exports:
                    self._exports.append(path_text)
                    changed = True
            except Exception:
                pass

        if changed:
            self._emit_live_views()

    def _emit_live_views(self) -> None:
        ordered_segments = [
            self._segments[index]
            for index in sorted(self._segments)
        ]
        manifest = {
            "source_video": self._source_video,
            "output_dir": self._output_dir,
            "count": self._total_count or len(ordered_segments),
            "segments": ordered_segments,
        }
        self._worker.summary_ready.emit(_format_manifest_summary(manifest))
        self._worker.manifest_ready.emit(json.dumps(manifest, ensure_ascii=False, indent=2))
        self._worker.exports_ready.emit(_format_exports(self._exports))


class TaskWorker(QObject):
    status_changed = Signal(str)
    log_message = Signal(str)
    summary_ready = Signal(str)
    manifest_ready = Signal(str)
    exports_ready = Signal(str)
    error_raised = Signal(str)
    finished = Signal()

    def __init__(self, task: TaskConfig) -> None:
        super().__init__()
        self.task = task
        self.stop_requested = threading.Event()

    def request_stop(self) -> None:
        self.stop_requested.set()
        self.status_changed.emit("已请求停止，等待当前步骤结束...")
        self.log_message.emit("已收到停止请求。当前正在运行的步骤会尽快结束，后续片段不会继续处理。")

    def run(self) -> None:
        bridge = QtPipelineBridge(self)
        try:
            cfg = self.task.config
            cfg.project.output_dir = str(_safe_output_dir(self.task.output_dir, cfg.project.output_dir))
            cfg.input.video_path = self.task.video_path

            if self.task.mode == "analyze":
                self.status_changed.emit("准备开始分析")
                analyze_cfg = _clone_config(cfg)
                analyze_cfg.export.export_video = False
                analyze_cfg.export.export_audio = False
                manifest, _ = run_pipeline(
                    config=analyze_cfg,
                    video_path=self.task.video_path,
                    status_callback=bridge,
                )
                self.summary_ready.emit(_format_manifest_summary(manifest))
                self.manifest_ready.emit(json.dumps(manifest, ensure_ascii=False, indent=2))
                self.status_changed.emit("分析完成" if not self.stop_requested.is_set() else "分析已停止")

            elif self.task.mode == "analyze_export":
                self.status_changed.emit("准备开始分析并导出")
                manifest, exports = run_pipeline(
                    config=cfg,
                    video_path=self.task.video_path,
                    status_callback=bridge,
                )
                self.summary_ready.emit(_format_manifest_summary(manifest))
                self.manifest_ready.emit(json.dumps(manifest, ensure_ascii=False, indent=2))
                self.exports_ready.emit(_format_exports(exports))
                self.status_changed.emit("分析并导出完成" if not self.stop_requested.is_set() else "分析并导出已停止")

            elif self.task.mode == "export_only":
                self.status_changed.emit("准备开始导出")
                exported = export_from_manifest(
                    video_path=self.task.video_path,
                    manifest_path=self.task.manifest_path,
                    output_dir=cfg.project.output_dir,
                    config=cfg,
                )
                self.exports_ready.emit(_format_exports([str(path) for path in exported]))
                self.summary_ready.emit(
                    "已从已有 Manifest 完成导出。\n\n"
                    f"source_video: {self.task.video_path}\n"
                    f"output_dir: {cfg.project.output_dir}\n"
                    f"count: {len(exported)}"
                )
                self.status_changed.emit("导出完成" if not self.stop_requested.is_set() else "导出已停止")

            else:
                raise RuntimeError(f"Unsupported task mode: {self.task.mode}")
        except Exception:
            self.error_raised.emit(traceback.format_exc())
            self.status_changed.emit("任务失败")
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self, initial_config: AppConfig | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Neuro-slice 桌面版")
        self.resize(1480, 940)

        self._base_config = _apply_saved_advanced_settings(initial_config or AppConfig())
        self._thread: QThread | None = None
        self._worker: TaskWorker | None = None

        self._build_ui()
        self._load_widgets_from_config(self._base_config)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("root")
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        workspace_splitter.setChildrenCollapsible(False)
        workspace_splitter.setHandleWidth(10)
        main_layout.addWidget(workspace_splitter, 1)

        top_workspace = QWidget()
        top_workspace.setObjectName("topWorkspace")
        top_workspace_layout = QHBoxLayout(top_workspace)
        top_workspace_layout.setContentsMargins(0, 0, 0, 0)
        top_workspace_layout.setSpacing(14)

        header_card = QFrame()
        header_card.setObjectName("heroCard")
        header_card.setMinimumHeight(176)
        header_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(22, 20, 22, 20)
        header_layout.setSpacing(10)

        header = QLabel(
            "Neuro-slice\n\n"
            "高准确率歌回切片工具\n"
            "默认使用 legacy 高精度检测模式。选择视频、导出目录，然后直接开始。"
        )
        header.setObjectName("heroTitle")
        header.setWordWrap(True)
        header_layout.addWidget(header)
        header_layout.addStretch(1)

        task_card = QGroupBox("本次任务")
        task_card.setObjectName("taskCard")
        task_layout = QGridLayout(task_card)
        task_layout.setHorizontalSpacing(10)
        task_layout.setVerticalSpacing(10)
        task_layout.setColumnStretch(1, 1)

        self.video_edit = QLineEdit()
        self.video_edit.setPlaceholderText("请选择本地视频文件...")
        btn_video = QPushButton("选择视频")
        btn_video.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        btn_video.clicked.connect(self._choose_video)
        btn_video.setMinimumWidth(110)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("请选择导出目录...")
        btn_output = QPushButton("选择目录")
        btn_output.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        btn_output.clicked.connect(self._choose_output_dir)
        btn_output.setMinimumWidth(110)

        self.manifest_edit = QLineEdit()
        self.manifest_edit.setPlaceholderText("仅导出模式时选择已有 manifest JSON ...")
        btn_manifest = QPushButton("选择 Manifest")
        btn_manifest.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        btn_manifest.clicked.connect(self._choose_manifest)
        btn_manifest.setMinimumWidth(110)

        task_layout.addWidget(QLabel("视频文件"), 0, 0)
        task_layout.addWidget(self.video_edit, 0, 1)
        task_layout.addWidget(btn_video, 0, 2)

        task_layout.addWidget(QLabel("导出目录"), 1, 0)
        task_layout.addWidget(self.output_edit, 1, 1)
        task_layout.addWidget(btn_output, 1, 2)

        task_layout.addWidget(QLabel("Manifest 文件"), 2, 0)
        task_layout.addWidget(self.manifest_edit, 2, 1)
        task_layout.addWidget(btn_manifest, 2, 2)

        action_card = QGroupBox("操作")
        action_card.setObjectName("actionCard")
        action_grid = QGridLayout(action_card)
        action_grid.setHorizontalSpacing(10)
        action_grid.setVerticalSpacing(10)

        self.analyze_btn = QPushButton("仅分析")
        self.analyze_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.analyze_btn.clicked.connect(self._start_analyze)
        self.analyze_export_btn = QPushButton("分析并导出")
        self.analyze_export_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        self.analyze_export_btn.clicked.connect(self._start_analyze_and_export)
        self.export_only_btn = QPushButton("导出已有 Manifest")
        self.export_only_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_only_btn.clicked.connect(self._start_export_only)
        self.stop_btn = QPushButton("停止当前任务")
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
        self.stop_btn.clicked.connect(self._request_stop)
        self.stop_btn.setEnabled(False)

        action_grid.addWidget(self.analyze_export_btn, 0, 0, 1, 2)
        action_grid.addWidget(self.analyze_btn, 1, 0)
        action_grid.addWidget(self.export_only_btn, 1, 1)
        action_grid.addWidget(self.stop_btn, 2, 0, 1, 2)

        top_workspace_layout.addWidget(header_card, 5)
        top_workspace_layout.addWidget(task_card, 8)
        top_workspace_layout.addWidget(action_card, 5)

        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        bottom_splitter.setChildrenCollapsible(False)
        bottom_splitter.setHandleWidth(10)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setMinimumWidth(520)
        settings_scroll.setMaximumWidth(680)

        settings_panel = QWidget()
        settings_panel.setObjectName("leftPanel")
        settings_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(12)

        manifest_help = QLabel(
            "Manifest 是本次分析生成的歌曲清单（通常是 songs.json）。\n"
            "如果你后面只想微调边界或重新导出，不需要重新分析整条视频，直接读取这个文件即可。"
        )
        manifest_help.setWordWrap(True)
        manifest_help.setFrameShape(QFrame.Shape.StyledPanel)
        manifest_help.setObjectName("manifestHelp")
        settings_layout.addWidget(manifest_help)

        basic_box = QGroupBox("基础设置")
        basic_form = QFormLayout(basic_box)
        basic_form.setSpacing(10)
        basic_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.padding_before_spin = self._new_double_spin(0.0, 30.0, 0.5)
        self.padding_after_spin = self._new_double_spin(0.0, 30.0, 0.5)

        self.export_video_check = QCheckBox("导出视频")
        self.export_audio_check = QCheckBox("导出音频")

        export_row = QWidget()
        export_row_layout = QHBoxLayout(export_row)
        export_row_layout.setContentsMargins(0, 0, 0, 0)
        export_row_layout.setSpacing(14)
        export_row_layout.addWidget(self.export_video_check)
        export_row_layout.addWidget(self.export_audio_check)
        export_row_layout.addStretch(1)

        self.singer_edit = QLineEdit()
        self.date_edit = QLineEdit()

        basic_form.addRow("前置留白（秒）", self.padding_before_spin)
        basic_form.addRow("后置留白（秒）", self.padding_after_spin)
        basic_form.addRow("导出选项", export_row)
        basic_form.addRow("歌手 / 主播标记", self.singer_edit)
        basic_form.addRow("日期", self.date_edit)

        settings_layout.addWidget(basic_box)

        self.advanced_toggle_btn = QPushButton("显示高级设置")
        self.advanced_toggle_btn.setCheckable(True)
        self.advanced_toggle_btn.setChecked(False)
        settings_layout.addWidget(self.advanced_toggle_btn)

        self.advanced_box = QGroupBox("高级设置")
        self.advanced_box.setVisible(False)
        advanced_container = QVBoxLayout(self.advanced_box)
        advanced_container.setSpacing(12)

        recognition_hint = QLabel(
            "高级模式用于调整 ASR、识曲和导出细节。\n"
            "普通情况下只需要基础输入和基础设置即可开始处理。"
        )
        recognition_hint.setWordWrap(True)
        recognition_hint.setObjectName("hintLabel")
        advanced_container.addWidget(recognition_hint)

        self.advanced_toggle_btn.toggled.connect(self.advanced_box.setVisible)
        self.advanced_toggle_btn.toggled.connect(
            lambda checked: self.advanced_toggle_btn.setText("收起高级设置" if checked else "显示高级设置")
        )   

        advanced_form = QFormLayout()
        advanced_form.setSpacing(10)
        advanced_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.asr_model_combo = self._new_combo(
            ["tiny", "base", "small", "medium", "large-v2", "large-v3"],
            editable=True,
        )
        self.asr_device_combo = self._new_combo(["auto", "cuda", "cpu"], editable=False)
        self.asr_compute_combo = self._new_combo(
            ["", "float16", "int8", "int8_float16", "float32"],
            editable=True,
        )

        self.recognition_enabled_check = QCheckBox("启用识曲（默认关闭）")
        self.recognition_mode_combo = self._new_combo(["none", "openai"], editable=False)
        self.recognition_model_edit = QLineEdit()
        self.recognition_model_edit.setPlaceholderText("例如：gpt-4o-mini")
        self.recognition_base_url_edit = QLineEdit()
        self.recognition_base_url_edit.setPlaceholderText("例如：https://api.openai.com/v1")
        self.recognition_api_key_edit = QLineEdit()
        self.recognition_api_key_edit.setPlaceholderText("请输入识曲服务的 API Key")
        self.recognition_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        advanced_form.addRow("Whisper 模型", self.asr_model_combo)
        advanced_form.addRow("ASR 设备", self.asr_device_combo)
        advanced_form.addRow("计算精度", self.asr_compute_combo)
        advanced_form.addRow("", self.recognition_enabled_check)
        advanced_form.addRow("识曲模式", self.recognition_mode_combo)
        advanced_form.addRow("识曲模型", self.recognition_model_edit)
        advanced_form.addRow("Base URL（兼容接口时填写）", self.recognition_base_url_edit)
        advanced_form.addRow("API Key（启用识曲时必填）", self.recognition_api_key_edit)

        advanced_form_widget = QWidget()
        advanced_form_widget.setLayout(advanced_form)
        advanced_container.addWidget(advanced_form_widget)

        advanced_buttons_grid = QGridLayout()
        advanced_buttons_grid.setHorizontalSpacing(10)
        advanced_buttons_grid.setVerticalSpacing(10)

        self.save_advanced_btn = QPushButton("保存高级设置")
        self.save_advanced_btn.clicked.connect(self._save_advanced_settings)
        self.reset_advanced_btn = QPushButton("重置高级设置")
        self.reset_advanced_btn.clicked.connect(self._reset_advanced_settings)
        self.export_toml_btn = QPushButton("导出当前配置为 TOML")
        self.export_toml_btn.clicked.connect(self._export_current_config_toml)

        advanced_buttons_grid.addWidget(self.save_advanced_btn, 0, 0)
        advanced_buttons_grid.addWidget(self.reset_advanced_btn, 0, 1)
        advanced_buttons_grid.addWidget(self.export_toml_btn, 1, 0, 1, 2)
        advanced_container.addLayout(advanced_buttons_grid)

        self.settings_path_label = QLabel(f"当前高级设置保存路径：{_advanced_settings_path()}")
        self.settings_path_label.setWordWrap(True)
        self.settings_path_label.setObjectName("pathLabel")
        advanced_container.addWidget(self.settings_path_label)

        settings_layout.addWidget(self.advanced_box)
        settings_layout.addStretch(1)

        settings_scroll.setWidget(settings_panel)
        bottom_splitter.addWidget(settings_scroll)

        right = QWidget()
        right.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        status_box = QGroupBox("运行状态")
        status_layout = QVBoxLayout(status_box)
        status_layout.setSpacing(10)

        status_meta_row = QWidget()
        status_meta_row.setObjectName("statusMetaRow")
        status_meta_layout = QHBoxLayout(status_meta_row)
        status_meta_layout.setContentsMargins(0, 0, 0, 0)
        status_meta_layout.setSpacing(10)

        self.mode_chip = QLabel("模式：空闲")
        self.mode_chip.setObjectName("statusChip")
        self.detector_chip = QLabel(f"检测后端：{self._base_config.detector.backend}")
        self.detector_chip.setObjectName("statusChip")
        self.asr_chip = QLabel(f"ASR：{self._base_config.asr.model_size} / {self._base_config.asr.device}")
        self.asr_chip.setObjectName("statusChip")

        status_meta_layout.addWidget(self.mode_chip)
        status_meta_layout.addWidget(self.detector_chip)
        status_meta_layout.addWidget(self.asr_chip)
        status_meta_layout.addStretch(1)

        self.status_label = QLineEdit()
        self.status_label.setObjectName("statusDisplay")
        self.status_label.setReadOnly(True)
        self.status_label.setPlaceholderText("等待开始")
        status_layout.addWidget(status_meta_row)
        status_layout.addWidget(self.status_label)

        right_layout.addWidget(status_box)

        results_box = QGroupBox("处理结果")
        results_layout = QVBoxLayout(results_box)
        results_layout.setContentsMargins(12, 12, 12, 12)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self.logs_text = QPlainTextEdit()
        self.logs_text.setReadOnly(True)
        tabs.addTab(self.logs_text, "日志")

        self.summary_text = QPlainTextEdit()
        self.summary_text.setReadOnly(True)
        tabs.addTab(self.summary_text, "摘要")

        self.exports_text = QPlainTextEdit()
        self.exports_text.setReadOnly(True)
        tabs.addTab(self.exports_text, "已导出文件")

        self.manifest_text = QPlainTextEdit()
        self.manifest_text.setReadOnly(True)
        tabs.addTab(self.manifest_text, "Manifest JSON")

        results_layout.addWidget(tabs, 1)
        right_layout.addWidget(results_box, 1)

        bottom_splitter.addWidget(right)
        bottom_splitter.setStretchFactor(0, 0)
        bottom_splitter.setStretchFactor(1, 1)
        bottom_splitter.setSizes([580, 980])

        workspace_splitter.addWidget(top_workspace)
        workspace_splitter.addWidget(bottom_splitter)
        workspace_splitter.setStretchFactor(0, 0)
        workspace_splitter.setStretchFactor(1, 1)
        workspace_splitter.setSizes([260, 680])

    # ------------------------------------------------------------------
    # Widget factories
    # ------------------------------------------------------------------

    def _new_combo(self, items: list[str], *, editable: bool) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setEditable(editable)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        return combo

    def _new_double_spin(self, minimum: float, maximum: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(1)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        return spin

    # ------------------------------------------------------------------
    # Config / state helpers
    # ------------------------------------------------------------------

    def _load_widgets_from_config(self, cfg: AppConfig) -> None:
        self.video_edit.setText(cfg.input.video_path)
        self.output_edit.setText(cfg.project.output_dir)
        self.manifest_edit.setText("")

        self.padding_before_spin.setValue(cfg.segment.padding_before)
        self.padding_after_spin.setValue(cfg.segment.padding_after)

        self.export_video_check.setChecked(cfg.export.export_video)
        self.export_audio_check.setChecked(cfg.export.export_audio)

        self.singer_edit.setText(cfg.metadata.singer)
        self.date_edit.setText(cfg.metadata.date)

        self._set_combo_value(self.asr_model_combo, cfg.asr.model_size)
        self._set_combo_value(self.asr_device_combo, cfg.asr.device)
        self._set_combo_value(self.asr_compute_combo, cfg.asr.compute_type)

        self.recognition_enabled_check.setChecked(cfg.recognition.enabled)
        self._set_combo_value(self.recognition_mode_combo, cfg.recognition.mode)
        self.recognition_model_edit.setText(cfg.recognition.model)
        self.recognition_base_url_edit.setText(cfg.recognition.base_url)
        self.recognition_api_key_edit.setText(cfg.recognition.api_key)

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        text = value or ""
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(text)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)

    def _build_config_from_ui(self) -> AppConfig:
        cfg = _clone_config(self._base_config)
        cfg.input.video_path = self.video_edit.text().strip()
        cfg.project.output_dir = self.output_edit.text().strip() or cfg.project.output_dir

        cfg.segment.padding_before = float(self.padding_before_spin.value())
        cfg.segment.padding_after = float(self.padding_after_spin.value())

        cfg.export.export_video = self.export_video_check.isChecked()
        cfg.export.export_audio = self.export_audio_check.isChecked()

        cfg.metadata.singer = self.singer_edit.text().strip() or cfg.metadata.singer
        cfg.metadata.date = self.date_edit.text().strip() or cfg.metadata.date

        cfg.asr.model_size = self.asr_model_combo.currentText().strip() or cfg.asr.model_size
        cfg.asr.device = self.asr_device_combo.currentText().strip() or cfg.asr.device
        cfg.asr.compute_type = self.asr_compute_combo.currentText().strip()

        cfg.recognition.enabled = self.recognition_enabled_check.isChecked()
        cfg.recognition.mode = self.recognition_mode_combo.currentText().strip() or cfg.recognition.mode
        cfg.recognition.model = self.recognition_model_edit.text().strip() or cfg.recognition.model
        cfg.recognition.base_url = self.recognition_base_url_edit.text().strip()
        cfg.recognition.api_key = self.recognition_api_key_edit.text().strip()

        return cfg

    def _advanced_settings_values(self) -> dict[str, Any]:
        return _advanced_settings_payload_from_values(
            asr_model_size=self.asr_model_combo.currentText(),
            asr_device=self.asr_device_combo.currentText(),
            asr_compute_type=self.asr_compute_combo.currentText(),
            padding_before=self.padding_before_spin.value(),
            padding_after=self.padding_after_spin.value(),
            export_video=self.export_video_check.isChecked(),
            export_audio=self.export_audio_check.isChecked(),
            singer=self.singer_edit.text(),
            date_text=self.date_edit.text(),
            recognition_enabled=self.recognition_enabled_check.isChecked(),
            recognition_mode=self.recognition_mode_combo.currentText(),
            recognition_api_key=self.recognition_api_key_edit.text(),
            recognition_base_url=self.recognition_base_url_edit.text(),
            recognition_model=self.recognition_model_edit.text(),
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _save_advanced_settings(self) -> None:
        payload = self._advanced_settings_values()
        path = _advanced_settings_path()
        _save_json(path, payload)
        self.status_label.setText("已保存高级设置")
        self._append_log(f"已保存当前高级设置到：{path}")

    def _reset_advanced_settings(self) -> None:
        path = _advanced_settings_path()
        with contextlib.suppress(Exception):
            path.unlink(missing_ok=True)
        self._base_config = AppConfig()
        self._load_widgets_from_config(_apply_saved_advanced_settings(_clone_config(self._base_config)))
        self.status_label.setText("已重置高级设置")
        self._append_log("已重置高级设置，并恢复到默认值。")

    def _export_current_config_toml(self) -> None:
        cfg = self._build_config_from_ui()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出当前配置为 TOML",
            str(_exported_config_path()),
            "TOML files (*.toml);;All files (*.*)",
        )
        if not path:
            self.status_label.setText("已取消导出 TOML")
            return

        out_path = Path(path)
        _write_toml_from_config(cfg, out_path)
        self.status_label.setText("已导出当前配置为 TOML")
        self._append_log(f"已导出当前配置到：{out_path}")

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------

    def _choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            self.video_edit.text().strip() or str(_project_root()),
            "Video files (*.mp4 *.mkv *.webm *.mov *.avi *.m4v);;All files (*.*)",
        )
        if path:
            self.video_edit.setText(path)

    def _choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择导出目录",
            self.output_edit.text().strip() or str(_project_root()),
        )
        if path:
            self.output_edit.setText(path)

    def _choose_manifest(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Manifest JSON 文件",
            self.manifest_edit.text().strip() or str(_project_root()),
            "JSON files (*.json);;All files (*.*)",
        )
        if path:
            self.manifest_edit.setText(path)

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def _clear_outputs(self) -> None:
        self.summary_text.clear()
        self.exports_text.clear()
        self.manifest_text.clear()
        self.logs_text.clear()

    def _set_busy(self, busy: bool) -> None:
        self.analyze_btn.setEnabled(not busy)
        self.analyze_export_btn.setEnabled(not busy)
        self.export_only_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _start_task(self, mode: str) -> None:
        if self._thread is not None:
            QMessageBox.warning(self, "任务正在运行", "当前已经有任务在运行，请先等待完成或点击停止当前任务。")
            return

        video_path = self.video_edit.text().strip()
        if not video_path:
            QMessageBox.warning(self, "缺少视频", "请先选择要处理的视频文件。")
            return

        mode_map = {
            "analyze": "模式：仅分析",
            "analyze_export": "模式：分析并导出",
            "export_only": "模式：导出已有 Manifest",
        }
        self.mode_chip.setText(mode_map.get(mode, "模式：空闲"))
        self.detector_chip.setText(f"检测后端：{self._base_config.detector.backend}")
        self.asr_chip.setText(f"ASR：{self.asr_model_combo.currentText()} / {self.asr_device_combo.currentText()}")

        if mode == "export_only":
            manifest_path = self.manifest_edit.text().strip()
            if not manifest_path:
                QMessageBox.warning(self, "缺少 Manifest", "导出已有 Manifest 时，请先选择 manifest JSON 文件。")
                return
        else:
            manifest_path = self.manifest_edit.text().strip()

        cfg = self._build_config_from_ui()
        task = TaskConfig(
            mode=mode,
            config=cfg,
            video_path=video_path,
            manifest_path=manifest_path,
            output_dir=self.output_edit.text().strip() or cfg.project.output_dir,
        )

        self._clear_outputs()
        self.status_label.setText("准备开始任务...")
        self._append_log(f"启动任务模式：{mode}")

        self._thread = QThread(self)
        self._worker = TaskWorker(task)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.log_message.connect(self._append_log)
        self._worker.summary_ready.connect(self.summary_text.setPlainText)
        self._worker.manifest_ready.connect(self.manifest_text.setPlainText)
        self._worker.exports_ready.connect(self.exports_text.setPlainText)
        self._worker.error_raised.connect(self._on_error_raised)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._set_busy(True)
        self._thread.start()

    def _start_analyze(self) -> None:
        self._start_task("analyze")

    def _start_analyze_and_export(self) -> None:
        self._start_task("analyze_export")

    def _start_export_only(self) -> None:
        self._start_task("export_only")

    def _request_stop(self) -> None:
        if self._worker is None:
            self.status_label.setText("当前没有正在运行的任务")
            return

        self._worker.request_stop()
        self.status_label.setText("已请求停止，等待当前步骤结束...")

    def _on_status_changed(self, message: str) -> None:
        self.status_label.setText(message)

    def _append_log(self, message: str) -> None:
        text = str(message).rstrip()
        if not text:
            return
        self.logs_text.appendPlainText(text)

    def _on_error_raised(self, error_text: str) -> None:
        self._append_log(error_text)
        QMessageBox.critical(self, "任务失败", "当前任务执行失败，请查看日志。")

    def _on_worker_finished(self) -> None:
        self._set_busy(False)
        self.mode_chip.setText("模式：空闲")
        self._worker = None
        self._thread = None

    # ------------------------------------------------------------------
    # Close handling
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._worker is not None:
            reply = QMessageBox.question(
                self,
                "任务仍在运行",
                "当前还有任务在运行。确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            self._worker.request_stop()

        super().closeEvent(event)


def _apply_fluentish_style(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QMainWindow {
            background-color: #0c0f14;
        }

        QWidget#root {
            background-color: #0c0f14;
            color: #f4f7ff;
        }

        QWidget#leftPanel, QWidget#rightPanel {
            background: transparent;
        }

        QToolBar {
            background: #131924;
            border: 1px solid #212b3e;
            border-radius: 16px;
            spacing: 8px;
            padding: 8px 10px;
        }

        QToolBar QToolButton {
            background: transparent;
            color: #dbe5ff;
            border: 1px solid transparent;
            border-radius: 12px;
            padding: 8px 12px;
        }

        QToolBar QToolButton:hover {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.10);
        }

        QLabel#heroTitle {
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(79, 110, 255, 0.34),
                stop:1 rgba(29, 188, 192, 0.18)
            );
            border: 1px solid rgba(255, 255, 255, 0.09);
            border-radius: 22px;
            padding: 22px 24px;
            font-size: 18px;
            font-weight: 700;
            line-height: 1.5;
            color: #f7f9ff;
        }

        QGroupBox {
            background: #141a25;
            border: 1px solid rgba(123, 150, 210, 0.14);
            border-radius: 18px;
            margin-top: 10px;
            padding: 18px 16px 14px 16px;
            font-weight: 700;
            color: #f7f9ff;
        }

        QGroupBox#taskCard, QGroupBox#actionCard {
            background: #141b27;
        }

        QFrame#heroCard {
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(72, 109, 255, 0.34),
                stop:1 rgba(12, 161, 164, 0.16)
            );
            border: 1px solid rgba(126, 153, 220, 0.18);
            border-radius: 22px;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            top: 0px;
            background: #0c0f14;
            color: #9fb8ff;
            padding: 0 8px;
            border-radius: 6px;
        }

        QLabel {
            color: #e9eefb;
        }

        QLabel#manifestHelp, QLabel#hintLabel, QLabel#pathLabel {
            background: rgba(19, 25, 38, 0.58);
            border: 1px solid rgba(124, 149, 202, 0.10);
            border-radius: 14px;
            padding: 12px 14px;
            color: #dfe7fb;
        }

        QWidget#statusMetaRow {
            background: transparent;
        }

        QLabel#statusChip {
            background: rgba(87, 115, 176, 0.18);
            border: 1px solid rgba(124, 149, 202, 0.18);
            border-radius: 10px;
            padding: 6px 10px;
            color: #dce7ff;
            font-weight: 600;
        }

        QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox {
            background: #0f1521;
            border: 1px solid #2b3850;
            border-radius: 12px;
            color: #f5f7fb;
        }

        QLineEdit, QComboBox, QDoubleSpinBox {
            min-height: 40px;
            padding: 6px 10px;
        }

        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
            border: 1px solid #5a8dff;
        }

        QPlainTextEdit {
            padding: 10px 12px;
            selection-background-color: #3e69ff;
        }

        QComboBox::drop-down {
            border: none;
            width: 28px;
        }

        QComboBox QAbstractItemView {
            background: #151b28;
            color: #f5f7fb;
            border: 1px solid #2b3850;
            selection-background-color: #335eff;
        }

        QCheckBox {
            spacing: 8px;
            color: #e7ebf7;
        }

        QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border-radius: 6px;
            border: 1px solid #4a5672;
            background: #0f1420;
        }

        QCheckBox::indicator:checked {
            background: #4d7cff;
            border: 1px solid #4d7cff;
        }

        QPushButton {
            background: #182133;
            border: 1px solid rgba(112, 139, 201, 0.20);
            border-radius: 12px;
            color: #f6f8ff;
            min-height: 42px;
            padding: 8px 14px;
            font-weight: 700;
        }

        QPushButton:hover {
            background: #22304a;
            border: 1px solid rgba(132, 161, 228, 0.34);
        }

        QPushButton:pressed {
            background: #141b2a;
        }

        QPushButton:disabled {
            background: #141822;
            color: #6f7a90;
            border: 1px solid #232b3b;
        }

        QPushButton[text="分析并导出"] {
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 #4d7cff,
                stop:1 #6b8dff
            );
            border: 1px solid #6d91ff;
            min-height: 48px;
            font-size: 15px;
        }

        QPushButton[text="分析并导出"]:hover {
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 #5c88ff,
                stop:1 #7a9bff
            );
            border: 1px solid #88a7ff;
        }

        QPushButton[text="停止当前任务"] {
            background: #6a2634;
            border: 1px solid #8c4252;
        }

        QPushButton[text="停止当前任务"]:hover {
            background: #873448;
            border: 1px solid #a55368;
        }

        QTabWidget::pane {
            background: #121926;
            border: 1px solid #27344d;
            border-radius: 18px;
            top: -1px;
            padding: 10px;
        }

        QTabBar::tab {
            background: #171e2c;
            color: #b9c4df;
            border: 1px solid #28334a;
            border-bottom: none;
            padding: 10px 16px;
            min-width: 120px;
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
            margin-right: 6px;
        }

        QTabBar::tab:selected {
            background: #232d41;
            color: #ffffff;
            border: 1px solid #3b4e71;
            border-bottom: none;
        }

        QSplitter::handle {
            background: #151b28;
            width: 10px;
            margin: 8px 0;
            border-radius: 5px;
        }

        QScrollArea {
            border: none;
            background: transparent;
        }

        QLineEdit#statusDisplay {
            background: #121a2a;
            border: 1px solid #344767;
            border-radius: 14px;
            min-height: 44px;
            padding: 8px 12px;
            color: #eff4ff;
            font-weight: 700;
        }

        QMessageBox {
            background: #0c0f14;
        }

        QMessageBox QLabel {
            color: #f5f7fb;
        }
        """
    )


def launch_desktop_app(config: AppConfig | None = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv)

    with contextlib.suppress(Exception):
        app.setStyle("Fusion")

    with contextlib.suppress(Exception):
        import qdarktheme  # type: ignore

        qdarktheme.setup_theme("dark")

    _apply_fluentish_style(app)

    window = MainWindow(initial_config=config)
    window.show()
    app.exec()


def launch_qt_app(config: AppConfig | None = None) -> None:
    launch_desktop_app(config=config)


def main() -> None:
    launch_desktop_app()


if __name__ == "__main__":
    main()