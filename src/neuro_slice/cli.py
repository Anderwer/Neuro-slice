from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from neuro_slice import __version__
from neuro_slice.config import AppConfig, load_config
from neuro_slice.export.cutter import export_from_manifest
from neuro_slice.pipeline import run_pipeline

app = typer.Typer(
    help="Automatically detect and slice song segments from VTuber karaoke/VOD archives.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()


def _resolve_video_path(video: Path | None, config: AppConfig) -> Path:
    if video is not None:
        return video
    if config.input.video_path:
        return Path(config.input.video_path)
    raise typer.BadParameter(
        "You must provide a video path or set [input].video_path in the config file."
    )


def _resolve_output_dir(output: Path | None, config: AppConfig) -> Path:
    return output or Path(config.project.output_dir)


def _load_config_or_default(config_path: Path | None) -> AppConfig:
    if config_path is None:
        return AppConfig()
    return load_config(config_path)


def _print_banner() -> None:
    console.print("[bold cyan]Neuro-slice[/bold cyan]")
    console.print(f"[dim]Version {__version__}[/dim]")


def _runtime_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "local_envs.json"


def _load_runtime_config() -> dict[str, object]:
    path = _runtime_config_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _resolve_legacy_paths(config: AppConfig) -> tuple[Path, Path]:
    runtime_config = _load_runtime_config()

    configured_python = (config.detector.legacy_python_path or "").strip()
    configured_script = (config.detector.legacy_script_path or "").strip()

    runtime_python = str(runtime_config.get("legacy_python", "") or "").strip()
    runtime_script = str(runtime_config.get("legacy_detector_script", "") or "").strip()

    legacy_python_candidate = configured_python or runtime_python
    if not legacy_python_candidate:
        legacy_python_candidate = str(Path(config.detector.legacy_env_dir) / "Scripts" / "python.exe")

    legacy_script_candidate = configured_script or runtime_script or "runtime/detect_segments_tf.py"

    legacy_python_path = Path(legacy_python_candidate)
    if not legacy_python_path.is_absolute():
        legacy_python_path = Path.cwd() / legacy_python_path

    legacy_script_path = Path(legacy_script_candidate)
    if not legacy_script_path.is_absolute():
        legacy_script_path = Path.cwd() / legacy_script_path

    return legacy_python_path.resolve(), legacy_script_path.resolve()


def _resolve_legacy_wsl_runtime(config: AppConfig) -> tuple[str, str, str]:
    runtime_config = _load_runtime_config()

    configured_distro = (config.detector.wsl_distro or "").strip()
    configured_python = (config.detector.wsl_legacy_python_path or "").strip()
    configured_script = (config.detector.wsl_legacy_script_path or "").strip()

    runtime_distro = str(runtime_config.get("wsl_distro", "") or "").strip()
    runtime_python = str(runtime_config.get("legacy_wsl_python", "") or "").strip()
    runtime_script = str(runtime_config.get("legacy_wsl_detector_script", "") or "").strip()

    distro = configured_distro or runtime_distro
    python_path = configured_python or runtime_python
    script_path = configured_script or runtime_script

    return distro, python_path, script_path


def _print_segments_summary(manifest: Mapping[str, object]) -> None:
    raw_segments = manifest.get("segments", [])
    segments: Sequence[object] = raw_segments if isinstance(raw_segments, Sequence) else []
    table = Table(title="Detected Song Segments")
    table.add_column("#", justify="right")
    table.add_column("Start")
    table.add_column("End")
    table.add_column("Duration", justify="right")
    table.add_column("Title")
    table.add_column("Confidence", justify="right")

    for index, raw_segment in enumerate(segments, start=1):
        if not isinstance(raw_segment, Mapping):
            continue

        table.add_row(
            str(index),
            str(raw_segment.get("start", "")),
            str(raw_segment.get("end", "")),
            str(raw_segment.get("duration", "")),
            str(raw_segment.get("title", "song")),
            str(raw_segment.get("confidence", "")),
        )

    console.print(table)
    console.print(f"[green]Total segments:[/green] {len(segments)}")


@app.callback()
def main() -> None:
    """Neuro-slice command line interface."""


@app.command("version")
def version() -> None:
    """Show the installed version."""
    console.print(__version__)


@app.command("analyze")
def analyze(
    video: Path | None = typer.Argument(
        None,
        exists=False,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the input VOD/video file.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a TOML config file.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        file_okay=False,
        dir_okay=True,
        help="Override output directory.",
    ),
    write_manifest: bool = typer.Option(
        True,
        "--write-manifest/--no-write-manifest",
        help="Write detected segments to the manifest JSON file.",
    ),
) -> None:
    """Analyze a video and generate a song segment manifest."""
    _print_banner()
    cfg = _load_config_or_default(config)
    cfg.review.write_manifest = write_manifest

    video_path = _resolve_video_path(video, cfg)
    output_dir = _resolve_output_dir(output, cfg)
    cfg.project.output_dir = str(output_dir)

    console.print(f"[bold]Video:[/bold] {video_path}")
    console.print(f"[bold]Output:[/bold] {output_dir}")

    cfg.export.export_video = False
    cfg.export.export_audio = False

    manifest, _ = run_pipeline(config=cfg, video_path=video_path, console=console)
    _print_segments_summary(manifest)

    if write_manifest:
        manifest_path = output_dir / cfg.review.manifest_name
        console.print(f"[green]Manifest written to:[/green] {manifest_path}")


@app.command("export")
def export(
    video: Path | None = typer.Argument(
        None,
        exists=False,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the input VOD/video file.",
    ),
    manifest: Path = typer.Option(
        ...,
        "--manifest",
        "-m",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to the manifest JSON file.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a TOML config file.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        file_okay=False,
        dir_okay=True,
        help="Override output directory.",
    ),
) -> None:
    """Export clips using an existing manifest."""
    _print_banner()
    cfg = _load_config_or_default(config)

    video_path = _resolve_video_path(video, cfg)
    output_dir = _resolve_output_dir(output, cfg)
    cfg.project.output_dir = str(output_dir)

    console.print(f"[bold]Video:[/bold] {video_path}")
    console.print(f"[bold]Manifest:[/bold] {manifest}")
    console.print(f"[bold]Output:[/bold] {output_dir}")

    results = export_from_manifest(
        video_path=video_path,
        manifest_path=manifest,
        output_dir=output_dir,
        config=cfg,
    )

    console.print(f"[green]Exported {len(results)} clip(s).[/green]")
    for path in results:
        console.print(f" - {path}")


@app.command("all")
def all_in_one(
    video: Path | None = typer.Argument(
        None,
        exists=False,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the input VOD/video file.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a TOML config file.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        file_okay=False,
        dir_okay=True,
        help="Override output directory.",
    ),
) -> None:
    """Run the full pipeline: analyze, write manifest, and export clips."""
    _print_banner()
    cfg = _load_config_or_default(config)

    video_path = _resolve_video_path(video, cfg)
    output_dir = _resolve_output_dir(output, cfg)
    cfg.project.output_dir = str(output_dir)

    console.print(f"[bold]Video:[/bold] {video_path}")
    console.print(f"[bold]Output:[/bold] {output_dir}")

    manifest, exports = run_pipeline(config=cfg, video_path=video_path, console=console)

    _print_segments_summary(manifest)
    console.print(f"[green]Exported {len(exports)} clip(s).[/green]")
    for path in exports:
        console.print(f" - {path}")


@app.command("inspect-config")
def inspect_config(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a TOML config file.",
    )
) -> None:
    """Print the resolved configuration."""
    cfg = _load_config_or_default(config)
    console.print_json(data=cfg.model_dump(mode="json"))


@app.command("doctor")
def doctor(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional TOML config file used to resolve the legacy detector paths.",
    )
) -> None:
    """Diagnose the legacy dual-environment detector setup."""
    _print_banner()
    cfg = _load_config_or_default(config)

    runtime_config_path = _runtime_config_path()
    runtime_config = _load_runtime_config()
    legacy_python_path, legacy_script_path = _resolve_legacy_paths(cfg)
    legacy_env_dir = Path(cfg.detector.legacy_env_dir)
    if not legacy_env_dir.is_absolute():
        legacy_env_dir = Path.cwd() / legacy_env_dir
    legacy_env_dir = legacy_env_dir.resolve()

    wsl_distro, wsl_python_path, wsl_script_path = _resolve_legacy_wsl_runtime(cfg)

    table = Table(title="Legacy Dual Environment Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")

    active_backend = cfg.detector.backend
    backend_ok = active_backend in {"legacy-subprocess", "legacy-wsl"}
    using_wsl = active_backend == "legacy-wsl"
    using_windows_legacy = active_backend == "legacy-subprocess"

    wsl_runtime_ok = bool(wsl_distro and wsl_python_path and wsl_script_path)
    windows_legacy_ok = bool(legacy_env_dir.exists() and legacy_python_path.exists() and legacy_script_path.exists())

    checks = [
        ("detector.backend", "OK" if backend_ok else "WARN", active_backend),
        ("runtime/local_envs.json", "OK" if runtime_config_path.exists() else "MISSING", str(runtime_config_path)),
        (
            "active detector runtime",
            "OK" if (wsl_runtime_ok if using_wsl else windows_legacy_ok if using_windows_legacy else False) else "WARN",
            "WSL legacy runtime" if using_wsl else "Windows legacy subprocess runtime" if using_windows_legacy else "unknown backend",
        ),
        (
            ".venv_legacy",
            "OK" if legacy_env_dir.exists() else "WARN" if using_wsl else "MISSING",
            str(legacy_env_dir) if legacy_env_dir.exists() else f"{legacy_env_dir} (WSL mode下仅作 Windows fallback，可选)",
        ),
        (
            "legacy python",
            "OK" if legacy_python_path.exists() else "WARN" if using_wsl else "MISSING",
            str(legacy_python_path) if legacy_python_path.exists() else f"{legacy_python_path} (WSL mode下仅作 Windows fallback，可选)",
        ),
        (
            "legacy detector script",
            "OK" if legacy_script_path.exists() else "MISSING",
            str(legacy_script_path),
        ),
        (
            "wsl distro",
            "OK" if wsl_distro else "MISSING" if using_wsl else "WARN",
            wsl_distro or ("missing" if using_wsl else "not used by the active backend"),
        ),
        (
            "wsl legacy python",
            "OK" if wsl_python_path else "MISSING" if using_wsl else "WARN",
            wsl_python_path or ("missing" if using_wsl else "not used by the active backend"),
        ),
        (
            "wsl legacy detector script",
            "OK" if wsl_script_path else "MISSING" if using_wsl else "WARN",
            wsl_script_path or ("missing" if using_wsl else "not used by the active backend"),
        ),
        (
            "wsl runtime payload",
            "OK" if wsl_runtime_ok else "MISSING" if using_wsl else "WARN",
            "loaded" if wsl_runtime_ok else "missing or incomplete" if using_wsl else "not used by the active backend",
        ),
        (
            "runtime config payload",
            "OK" if runtime_config else "WARN",
            "loaded" if runtime_config else "missing or unreadable",
        ),
    ]

    for name, status, details in checks:
        style = {
            "OK": "[green]OK[/green]",
            "WARN": "[yellow]WARN[/yellow]",
            "MISSING": "[red]MISSING[/red]",
        }.get(status, status)
        table.add_row(name, style, details)

    console.print(table)
    console.print_json(
        data={
            "detector_backend": active_backend,
            "runtime_config_path": str(runtime_config_path),
            "runtime_config_exists": runtime_config_path.exists(),
            "runtime_config": runtime_config,
            "legacy_env_dir": str(legacy_env_dir),
            "legacy_env_dir_exists": legacy_env_dir.exists(),
            "legacy_python_path": str(legacy_python_path),
            "legacy_python_exists": legacy_python_path.exists(),
            "legacy_script_path": str(legacy_script_path),
            "legacy_script_exists": legacy_script_path.exists(),
            "wsl_distro": wsl_distro,
            "wsl_distro_configured": bool(wsl_distro),
            "legacy_wsl_python_path": wsl_python_path,
            "legacy_wsl_python_configured": bool(wsl_python_path),
            "legacy_wsl_detector_script_path": wsl_script_path,
            "legacy_wsl_detector_script_configured": bool(wsl_script_path),
        }
    )


@app.command("webui")
def webui(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional TOML config file used as the initial desktop UI configuration.",
    ),
) -> None:
    """Launch the Qt desktop GUI."""
    cfg = _load_config_or_default(config)
    console.print("[bold]Launching Desktop UI[/bold]")
    try:
        from neuro_slice.qt_app import launch_desktop_app
    except ImportError as exc:
        raise typer.BadParameter(
            "Desktop UI dependencies are not installed. Install the project with the Qt desktop UI extra first."
        ) from exc

    launch_desktop_app(config=cfg)


if __name__ == "__main__":
    app()