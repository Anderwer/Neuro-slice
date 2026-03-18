# GPU Compatibility Guide

This document explains how `Neuro-slice` handles GPU acceleration across different Windows machines, how to install the required runtime pieces, and how to troubleshoot fallback-to-CPU situations.

---

## Design Goal

`Neuro-slice` is designed to work in three progressively better modes:

1. **GPU mode**
   - preferred on Windows machines with an NVIDIA GPU
   - uses `faster-whisper` with CUDA

2. **Automatic fallback mode**
   - if CUDA runtime loading fails, the app falls back to CPU automatically
   - the full workflow can still complete, just slower

3. **CPU-only mode**
   - works on machines without an NVIDIA GPU
   - no CUDA runtime required

This means the project is intended to be portable across multiple machines without requiring every user to manually install a full system-wide CUDA Toolkit.

---

## How GPU Compatibility Works

On Windows, `faster-whisper` and its backend need several CUDA-related runtime DLLs at runtime.

Instead of requiring a global CUDA installation, this project supports a **project-local GPU runtime setup**:

- GPU runtime packages are installed into the project's virtual environment
- the application automatically discovers those runtime directories
- those directories are added to the Windows DLL search path
- those directories are also prepended to the process `PATH`
- if GPU initialization still fails, the app falls back to CPU automatically

This approach is much easier to reproduce on other computers.

---

## Supported Runtime Strategy

The current GPU strategy uses these Python-distributed CUDA runtime packages:

- `nvidia-cuda-runtime-cu12`
- `nvidia-cublas-cu12`
- `nvidia-cudnn-cu12`

These provide DLLs such as:

- `cublas64_12.dll`
- `cudnn64_9.dll`

When `Neuro-slice` starts ASR in GPU mode, it attempts to register runtime directories similar to:

- `.venv/Lib/site-packages/nvidia/cuda_runtime/bin`
- `.venv/Lib/site-packages/nvidia/cublas/bin`
- `.venv/Lib/site-packages/nvidia/cudnn/bin`

---

## Recommended Installation Modes

### CPU Mode

Use CPU mode when:

- the machine has no NVIDIA GPU
- the machine has an NVIDIA GPU but you do not want to debug CUDA
- you want the simplest and most portable setup

Typical install:

```bash
uv sync
uv sync --group dev --extra audio
```

### GPU Mode

Use GPU mode when:

- the machine has an NVIDIA GPU
- you want `faster-whisper` to use CUDA
- you still want automatic CPU fallback if GPU loading fails

Typical install:

```bash
uv sync --extra gpu
uv sync --group dev --extra gpu --extra audio
```

---

## Windows One-Click Setup

The Windows setup script is intended to make first-time setup easier.

Default behavior should install:

- base project dependencies
- optional development dependencies
- optional audio dependencies
- optional GPU runtime dependencies

For a lighter install, users can skip pieces such as GPU support.

Examples:

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SkipGpu
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SkipAudio -SkipGpu
```

---

## Configuration

The ASR config supports explicit device control.

Example:

```toml
[asr]
enabled = true
provider = "faster-whisper"
model_size = "large-v2"
device = "cuda"
compute_type = "float16"
```

### Recommended values

#### NVIDIA GPU
```toml
device = "cuda"
compute_type = "float16"
```

#### CPU
```toml
device = "cpu"
compute_type = "int8"
```

#### Auto mode
```toml
device = "auto"
compute_type = ""
```

Auto mode can be useful during development, but explicit settings are better when you want reproducible behavior.

---

## Runtime Behavior

At runtime, the application logs which ASR path it is using.

Typical GPU startup logs look like:

```text
Registered CUDA runtime DLL directory: ...\nvidia\cuda_runtime\bin
Registered CUDA runtime DLL directory: ...\nvidia\cublas\bin
Registered CUDA runtime DLL directory: ...\nvidia\cudnn\bin
ASR runtime selection | model=large-v2 | config_device=cuda | config_compute_type=float16 | requested_device=cuda | requested_compute_type=float16 | effective_device=cuda | effective_compute_type=float16 | force_cpu=False
Loading faster-whisper model: large-v2 (cuda, float16)
```

If GPU loading fails, fallback logs look like:

```text
faster-whisper CUDA runtime is unavailable. Original error: ...
Falling back to CPU/int8 for transcription.
ASR runtime selection | ... effective_device=cpu | effective_compute_type=int8 | force_cpu=True
Loading faster-whisper model: large-v2 (cpu, int8)
Retrying transcription on CPU/int8.
```

---

## How to Tell Whether GPU Is Really Working

The best indicators are:

1. the runtime selection log shows:
   - `effective_device=cuda`
   - `effective_compute_type=float16`

2. the model load log shows:
   - `Loading faster-whisper model: ... (cuda, float16)`

3. the process continues into later segments without immediately falling back to CPU

If the first segment starts on CUDA and the workflow continues to segment 2, 3, and beyond without printing the fallback message, GPU is very likely working.

---

## Troubleshooting

### Problem: `Library cublas64_12.dll is not found or cannot be loaded`

This is the most common Windows GPU issue.

#### Meaning
Usually this means one of the following:

- the CUDA runtime DLL is missing
- the DLL exists but is not visible through the current DLL search path
- a dependency of `cublas64_12.dll` is missing
- the backend is loading before runtime paths are registered

#### What this project does to help
The app already tries to reduce this problem by:

- installing project-local GPU runtime packages
- delaying ASR backend import
- registering runtime DLL directories
- prepending those directories to the process `PATH`
- falling back to CPU automatically

#### If it still happens
Check these in order:

1. verify the machine has an NVIDIA GPU:
   ```bash
   nvidia-smi
   ```

2. verify the runtime packages are installed in the project environment:
   ```bash
   uv sync --extra gpu
   ```

3. verify logs show runtime directory registration

4. verify your config requests CUDA:
   ```toml
   [asr]
   device = "cuda"
   compute_type = "float16"
   ```

5. rerun the command and inspect whether the app falls back to CPU

---

### Problem: GPU logs appear, but performance still feels slow

Possible reasons:

- the video is very long
- there are many candidate segments
- some segments are several minutes long
- the app is extracting audio for every segment
- the run may still have fallen back to CPU after the first segment

What to check:

- whether the fallback message appears
- how long each candidate segment is
- whether you are processing the whole VOD instead of a smaller test sample

---

### Problem: Another computer has no GPU

That is expected to still work.

Use CPU mode:

```toml
[asr]
device = "cpu"
compute_type = "int8"
```

The workflow will be slower, but it should still complete.

---

### Problem: Another computer has an NVIDIA GPU but no system CUDA Toolkit

That is exactly the case this project is designed to support.

You should still try:

```bash
uv sync --extra gpu
```

The goal is to avoid requiring a machine-wide CUDA Toolkit install.

---

## Portable Deployment Recommendation

For other Windows computers, the most portable deployment pattern is:

1. clone the repository
2. run the setup script
3. install GPU runtime dependencies into the project environment
4. let the application manage DLL registration itself
5. rely on automatic CPU fallback if GPU initialization fails

This is more robust than telling every user to install and configure a full CUDA Toolkit manually.

---

## Suggested User Flows

### Flow A: Typical GPU-capable Windows PC

```bash
git clone ...
cd Neuro-slice
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
uv run neuro-slice analyze --config my_config.toml
```

### Flow B: CPU-only Windows PC

```bash
git clone ...
cd Neuro-slice
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SkipGpu
uv run neuro-slice analyze --config my_config.toml
```

### Flow C: Quick manual GPU install

```bash
uv sync --extra gpu
uv run neuro-slice analyze --config my_config.toml
```

---

## Best Practice Summary

For cross-computer compatibility, the recommended strategy is:

- **project-local GPU runtime packages**
- **automatic DLL registration**
- **automatic PATH prepending**
- **automatic CPU fallback**
- **explicit ASR config options**

This gives the best balance between:

- portability
- reproducibility
- ease of installation
- Windows compatibility
- graceful degradation when GPU is unavailable

---

## Future Improvements

Potential future improvements include:

- a dedicated `setup-gpu` script
- a lightweight GPU self-check command
- a `doctor` command that reports:
  - GPU visibility
  - runtime package presence
  - DLL registration status
  - expected ASR backend mode
- per-segment timing logs to better estimate GPU speedups

---

## Final Recommendation

If you want other computers to "just work", do **not** rely on users manually configuring CUDA by hand.

Prefer this model instead:

- install GPU runtime packages with the project
- register DLL paths automatically in code
- try CUDA first
- fall back to CPU automatically

That is the compatibility model `Neuro-slice` is designed around.