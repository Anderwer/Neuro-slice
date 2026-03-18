# Neuro-slice

Automatically detect and slice song segments from VTuber karaoke/VOD archives.

`Neuro-slice` is a Python CLI project for turning long stream recordings into individual song clips.  
It is designed around a practical workflow:

1. analyze a VOD
2. detect likely singing segments
3. optionally transcribe lyrics
4. optionally guess song titles with an OpenAI-compatible API
5. export clips and a reviewable manifest

Current `v0.1` workflow:

1. `analyze` a VOD and write `songs.json`
2. manually review or edit the manifest if needed
3. `export` clips from the manifest
4. or use `all` to run detection + export in one step

The project started from a Neuro-sama karaoke clipping use case, but the architecture is intentionally general enough to support other VTuber singing streams as well.

---

## Goals

- Work from a local video file
- Avoid fragile multi-environment setups
- Keep the core pipeline modular
- Support manual review instead of forcing "full auto"
- Be easy to run, tweak, and publish on GitHub

---

## Current direction

This repository is being organized into a proper project instead of a single script.

Compared with the original prototype, the new version aims to:

- remove the TensorFlow + PyTorch dual-stack requirement
- avoid needing multiple CUDA setups
- make CPU-only execution possible
- separate detection, ASR, recognition, and export into modules
- output a manifest for manual correction
- support cleaner CLI usage

---

## Planned pipeline

### 1. Input
- local VOD video file
- optional config file

### 2. Audio analysis
- extract mono analysis audio with `ffmpeg`
- compute simple windows over the audio timeline
- score windows as likely singing / not singing
- merge nearby windows into candidate song segments

### 3. ASR refinement
- use `faster-whisper` to transcribe candidate segments
- use transcription for:
  - quality checking
  - title guessing
  - future boundary refinement

### 4. Song recognition
Optional:
- send lyrics snippets to an OpenAI-compatible model
- ask for `artist - title`
- fall back to generic names if uncertain

### 5. Export
- write `songs.json` manifest
- cut video and/or audio clips with `ffmpeg`
- allow manual editing and re-export

---

## Repository structure

```text
Neuro-slice/
├─ README.md
├─ pyproject.toml
├─ LICENSE
├─ .gitignore
├─ src/
│  └─ neuro_slice/
│     ├─ __init__.py
│     ├─ cli.py
│     ├─ config.py
│     ├─ models.py
│     ├─ pipeline.py
│     ├─ audio/
│     ├─ asr/
│     ├─ recognition/
│     ├─ export/
│     └─ utils/
├─ examples/
│  └─ sample_config.toml
├─ tests/
├─ docs/
└─ output/
```

---

## Installation

### Requirements

- Python 3.11+
- `uv`
- `ffmpeg`
- `ffprobe`

Make sure `ffmpeg` and `ffprobe` are available in your `PATH`.

### One-click Windows setup

For Windows users, the repository is intended to provide a single setup flow:

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

The default setup goal is to install the **full default environment stack** in one run:

- check whether `uv` is installed
- check whether `ffmpeg` / `ffprobe` are available
- create the main project environment: `.venv`
- install the main project dependencies
- install the default extras used by the current project workflow:
  - `dev`
  - `audio`
  - `gpu`
  - `web`
- automatically prepare the legacy detector environment: `.venv_legacy`
- write the local runtime wiring file used by the application to find the legacy detector
- print the next commands to run

This means the intended out-of-the-box experience is:

- **main environment**: WebUI, ASR, exporting, manifest handling
- **legacy environment**: original high-accuracy music detector
- **runtime bridge**: the main app automatically calls the legacy detector subprocess

A convenient companion launcher can also be provided:

```bash
.\scripts\setup.bat
```

This keeps first-time setup simple for users who just want to clone the repo and get started, while still preserving the original high-accuracy detector workflow.

### Recommended setup with `uv`

Create or reuse the main project virtual environment:

```bash
uv venv
```

## Default workflow: legacy dual environment mode

The project now assumes the **legacy dual environment workflow** by default because the original detector remains the most accurate approach for the current project goal.

### Environment roles

#### Main environment: `.venv`
Used for:
- CLI
- WebUI
- `faster-whisper`
- exporting clips
- manifest generation
- optional recognition APIs

#### Legacy detector environment: `.venv_legacy`
Used for:
- the original high-accuracy music detection pipeline
- `inaSpeechSegmenter`-style detector logic
- the legacy detector subprocess script under `runtime/`

### What the default setup should do

A full default setup is expected to prepare both environments.

If you use the one-click setup script, it is intended to do the full default installation for you, including the main environment extras and the legacy detector environment:

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

If you prefer to prepare the main environment manually first, you can still do:

```bash
uv sync --group dev --extra gpu --extra audio --extra web
```

After setup completes, the application should be able to:

1. run from the main environment
2. resolve the legacy detector Python automatically
3. resolve the legacy detector script automatically
4. call the legacy detector subprocess without requiring the user to type any detector paths manually

### Minimal manual setup

If you prefer not to use the one-click setup script, the intended manual setup is still:

#### Main environment
```bash
uv sync
uv sync --group dev --extra gpu --extra audio --extra web
```

#### Legacy detector environment
The project setup flow is expected to create and populate `.venv_legacy` automatically. If you are setting things up manually, you should provision the legacy environment before using the default detector mode.

### CPU / GPU behavior

The detector itself is still the legacy subprocess detector by default.

For ASR in the main environment:

- CPU-only machines can still run the project
- NVIDIA GPU machines can install the `gpu` extra
- if GPU runtime loading fails, ASR falls back to CPU automatically

That means the dual-environment design is:

- **accuracy-first** for detection
- **compatibility-first** for ASR/runtime behavior

### Desktop GUI mode

Use this if:
- you want a native desktop interface instead of a browser page
- you want users to select the input video themselves
- you want users to choose the export directory themselves
- you still want all processing to run on the local machine

Install the desktop GUI dependencies:

```bash
uv sync --extra web
```

Install desktop GUI + GPU + audio + development dependencies:

```bash
uv sync --group dev --extra gpu --extra audio --extra web
```

For more details about cross-machine GPU compatibility, Windows DLL loading, and fallback behavior, see:

```text
docs/gpu.md
```

### Run commands with `uv`

Use `uv run` for the CLI, tests, the desktop GUI, and legacy runtime diagnostics:

```bash
uv run neuro-slice --help
uv run pytest tests/test_manifest_export.py
uv run neuro-slice webui
uv run neuro-slice doctor
```

The desktop GUI mode is intended for local use on the same computer as the media files.
A typical local workflow is:

1. start the GUI with `uv run neuro-slice webui`
2. wait for the desktop window to open
3. choose the source video from the machine
4. choose the output directory
5. run analyze or analyze-and-export from the application

### Optional `pip` fallback

If you prefer not to use `uv`, the project still works with `pip`:

```bash
python -m venv .venv
python -m pip install -U pip
python -m pip install -e .[dev]
```

---

## Quick start

### 1. Copy and edit the sample config

```bash
cp examples/sample_config.toml my_config.toml
```

On Windows PowerShell, you can use:

```bash
Copy-Item examples/sample_config.toml my_config.toml
```

Edit fields such as:

- input video path
- output directory
- segmentation thresholds
- metadata date/singer
- ASR model size
- optional API settings

### 2. Run the pipeline

Actual `v0.1` CLI commands:

```bash
uv run neuro-slice analyze path/to/vod.mp4 --config my_config.toml --output output/
uv run neuro-slice export path/to/vod.mp4 --manifest output/songs.json --config my_config.toml --output output/
uv run neuro-slice all path/to/vod.mp4 --config my_config.toml --output output/
uv run neuro-slice webui
```

What each command does:

- `analyze`: run the default legacy subprocess detector, optionally transcribe / guess titles, and write `songs.json`
- `export`: read an existing manifest and cut clips again without re-running detection
- `all`: run detection and export in one command
- `webui`: launch the local Qt desktop GUI for selecting the source video and output directory interactively
- `doctor`: inspect whether the legacy dual-environment runtime is wired correctly, including `.venv_legacy`, `runtime/local_envs.json`, the legacy detector Python interpreter, and the legacy detector script

Unless you intentionally change the detector backend, the project is intended to use the legacy subprocess detector by default.

By default, video export now uses **precise re-encoding** instead of stream copy:
- video codec default: `libx264`
- audio codec default: `aac`

This is slower than `copy`, but it avoids the common fast-cut problem where the beginning of a clip shows several seconds of black frames or delayed video because the cut lands away from a keyframe.

### 3. Review manifest

The tool generates a manifest file like:

```json
{
  "source_video": "vod.mp4",
  "output_dir": "output",
  "total_duration": 10842.37,
  "segments": [
    {
      "index": 1,
      "start": 125.0,
      "end": 402.0,
      "padded_start": 123.5,
      "padded_end": 404.0,
      "raw_duration": 277.0,
      "export_duration": 280.5,
      "confidence": 0.91,
      "transcript": "sample lyrics text",
      "language": "en",
      "guessed_title": "Artist - Title",
      "final_title": "Artist - Title",
      "skipped_reason": null,
      "video_output": null,
      "audio_output": null
    }
  ]
}
```

Important manifest fields:

- `start` / `end`: detected core segment
- `padded_start` / `padded_end`: actual export range after padding
- `transcript`: optional `faster-whisper` transcription text
- `guessed_title`: optional OpenAI-compatible recognition result
- `final_title`: final filename title used for export fallback
- `video_output` / `audio_output`: populated after export

You can manually edit the manifest and rerun `export` later.

---

## Why not fully automatic from day one?

Because the most useful version is usually:

- good enough at finding candidate segments
- easy to review
- easy to correct
- reliable to export

In practice, a "half-automatic but dependable" tool is much more useful than a "fully automatic but unstable" one.

So this project prioritizes:

1. reproducible cutting
2. clean manifests
3. modular detection logic
4. optional title recognition

---

## Differences from the old prototype

The earlier script already proved the concept:
- detect music segments
- transcribe lyrics with `faster-whisper`
- ask an LLM to guess the song title
- export MP3 clips

But it also had some practical issues:

### Pain points
- TensorFlow and PyTorch were mixed together
- GPU setup was fragile
- Linux-only workaround complexity
- segment boundaries were sometimes inaccurate
- all logic lived inside one script
- hard to extend and hard to publish cleanly

### Improvements in this project
- single-project structure
- cleaner configuration
- explicit data models
- manifest-first workflow
- better GitHub-readiness
- easier future testing

---

## Configuration overview

Example config sections:

- `[project]` project name and output dir
- `[input]` source video path
- `[segment]` window size, merge gap, min/max duration
- `[audio]` sample rate, channels, preprocess filters
- `[asr]` whisper options
- `[recognition]` optional LLM title recognition
- `[export]` video/audio export settings
- `[metadata]` date and singer labels
- `[review]` manifest output options

This makes it easier to tune behavior without editing code.

---

## Planned commands

### `analyze`
Analyze a VOD and write a manifest.

Current behavior in `v0.1`:
- detects candidate singing segments from extracted analysis audio
- optionally transcribes each segment with `faster-whisper`
- optionally guesses song titles with an OpenAI-compatible API
- writes `songs.json`
- does **not** export clips unless you use `all`

### `export`
Export clips from an existing manifest.

Useful when:
- you manually corrected `start` / `end` or `padded_start` / `padded_end`
- you changed titles
- you only want to rerun cutting
- you do not want to rerun detection / ASR / recognition

### `all`
Run the full pipeline:
- analyze
- optional transcription
- optional recognition
- export
- write updated manifest with output paths

### `inspect`
Potential future command:
- render debugging info
- show segment scores
- save timeline charts

---

## Roadmap

### v0.1.0
- [x] initialize Python package structure
- [x] add project metadata and config example
- [ ] build CLI skeleton
- [ ] implement `ffmpeg` helpers
- [ ] define manifest models
- [ ] implement basic segment detection
- [ ] export clips from manifest

### v0.2.0
- [ ] integrate `faster-whisper`
- [ ] attach lyrics excerpts to segments
- [ ] support OpenAI-compatible title guessing
- [ ] improve boundary refinement logic
- [ ] add JSON/CSV export

### v0.3.0
- [ ] better singing-vs-talking heuristics
- [ ] visualization / debug timeline output
- [ ] review-friendly workflow improvements
- [ ] better filename templating
- [ ] unit tests for merge and export logic

### v0.4.0
- [ ] optional lightweight web UI
- [ ] chapter import/export
- [ ] batched processing for multiple VODs
- [ ] plugin-style recognizers / detectors

---

## Known limitations

- song-title recognition is probabilistic
- boundary detection is still heuristic-driven
- some talking + BGM sections may be misclassified
- some songs may be merged or split incorrectly
- `ffmpeg -c copy` exports are fast but may cut less precisely than re-encoding

---

## Suggested development strategy

If you want to build this project incrementally, the best order is:

1. make manifest-driven export solid
2. add basic segment detection
3. add ASR enrichment
4. add optional title recognition
5. improve detection quality
6. add UI / visualization later

This avoids getting stuck on "perfect AI detection" too early.

---

## GitHub publishing notes

When publishing this repository, it is recommended to include:

- a clear README
- a permissive license
- sample config
- example manifest format
- screenshots or timing examples later
- no copyrighted VOD files
- no large model caches
- no secrets or API keys

### Do not commit
- source VOD files
- exported song clips
- `.env` files with real keys
- cached models
- temporary WAV files

---

## Copyright / usage note

This repository is intended as a processing tool.

Users should provide their own legally obtained media inputs and are responsible for complying with:
- platform rules
- local copyright law
- fair use / archival / personal use restrictions where applicable

This project does not include copyrighted stream archives or music assets.

---

## Inspiration

The original prototype was built to process Neuro-sama karaoke stream archives automatically:
- detect music segments
- cut clips
- identify songs when possible

This repository is the cleaned-up, extensible version of that idea.

---

## Status

Early-stage, but actively being turned into a real project.

If you are cloning this repository while development is in progress, expect the structure and CLI to stabilize before detection quality becomes the main optimization target.

---

## Future ideas

- better speech-vs-singing classifier
- optional subtitle-assisted boundary detection
- chapter file generation for editors
- confidence-based review queue
- support for multiple recognition providers
- automatic safe filename normalization
- benchmark dataset for karaoke VOD segmentation

---

## Contributing

Contributions are welcome once the base pipeline is in place.

Good first contribution areas:
- unit tests
- detection heuristics
- manifest validation
- export stability
- documentation improvements

---

## License

MIT