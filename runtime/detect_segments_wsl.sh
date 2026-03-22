#!/usr/bin/env bash
set -euo pipefail

emit_status() {
    printf 'LEGACY_STATUS=%s\n' "$1"
}

usage() {
    echo "Usage: detect_segments_wsl.sh <legacy_python> <detector_script> <video_path> [min_music_duration] [chunk_seconds] [chunk_overlap_seconds]" >&2
}

if [ "$#" -lt 3 ]; then
    usage
    exit 1
fi

LEGACY_PYTHON="$1"
DETECTOR_SCRIPT="$2"
VIDEO_PATH="$3"
MIN_MUSIC_DURATION="${4:-20}"
CHUNK_SECONDS="${5:-300}"
CHUNK_OVERLAP_SECONDS="${6:-10}"

if [ ! -x "$LEGACY_PYTHON" ]; then
    echo "LEGACY_DETECTOR_ERROR=Legacy Python is not executable: $LEGACY_PYTHON" >&2
    exit 2
fi

if [ ! -f "$DETECTOR_SCRIPT" ]; then
    echo "LEGACY_DETECTOR_ERROR=Legacy detector script was not found: $DETECTOR_SCRIPT" >&2
    exit 2
fi

if [ ! -f "$VIDEO_PATH" ]; then
    echo "LEGACY_DETECTOR_ERROR=Input video was not found: $VIDEO_PATH" >&2
    exit 2
fi

LEGACY_ROOT="$(dirname "$(dirname "$LEGACY_PYTHON")")"

if [ ! -d "$LEGACY_ROOT" ]; then
    echo "LEGACY_DETECTOR_ERROR=Legacy environment root was not found: $LEGACY_ROOT" >&2
    exit 2
fi

emit_status "正在准备 WSL legacy detector 运行环境"

NVIDIA_LIB_DIRS=""
shopt -s nullglob
for dir in "$LEGACY_ROOT"/lib/python*/site-packages/nvidia/*/lib; do
    if [ -d "$dir" ]; then
        if [ -n "$NVIDIA_LIB_DIRS" ]; then
            NVIDIA_LIB_DIRS="${NVIDIA_LIB_DIRS}:$dir"
        else
            NVIDIA_LIB_DIRS="$dir"
        fi
    fi
done
shopt -u nullglob

if [ -z "$NVIDIA_LIB_DIRS" ]; then
    emit_status "未找到 WSL 内置的 NVIDIA 运行库目录，将仅使用 /usr/lib/wsl/lib"
else
    emit_status "已解析 WSL legacy detector 的 NVIDIA 运行库目录"
fi

export LD_LIBRARY_PATH="/usr/lib/wsl/lib${NVIDIA_LIB_DIRS:+:${NVIDIA_LIB_DIRS}}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"
export LEGACY_CHUNK_SECONDS="$CHUNK_SECONDS"
export LEGACY_CHUNK_OVERLAP_SECONDS="$CHUNK_OVERLAP_SECONDS"

emit_status "正在启动 WSL legacy detector Python"
exec "$LEGACY_PYTHON" -u "$DETECTOR_SCRIPT" "$VIDEO_PATH" "$MIN_MUSIC_DURATION" "$CHUNK_SECONDS" "$CHUNK_OVERLAP_SECONDS"