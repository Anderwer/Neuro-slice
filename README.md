# Neuro-slice

一个面向歌回 / 音乐直播回放的本地桌面切片工具。

## 项目简介

`Neuro-slice` 的目标是：

- 输入一条长视频回放
- 自动检测其中的歌曲片段
- 生成可复查的 `manifest`（通常是 `songs.json`）
- 导出每首歌对应的独立切片
- 提供本地桌面 GUI，方便直接操作

这个项目最初来自 VTuber 歌回切片需求，但当前目标已经扩大为更广泛的歌回 / 音乐直播回放切片工具。

---

## 当前默认架构

项目当前默认采用：

### Windows 主环境 + WSL legacy detector

- **Windows 主环境 `.venv`**
  - 桌面 GUI
  - CLI
  - `faster-whisper`
  - 导出
  - manifest
  - 可选 LLM 识曲

- **WSL legacy detector**
  - 保留旧版高准确率检测方案
  - 通过 `inaSpeechSegmenter` 检测音乐段
  - 由主程序自动调用

- **运行桥接**
  - `runtime/local_envs.json`

正常情况下用户不需要手动填写 detector 路径。

---

## 快速开始

### 1. 克隆仓库

- `git clone https://github.com/Anderwer/Neuro-slice.git`
- `cd Neuro-slice`

### 2. 一键安装

Windows 下直接运行：

- `scripts\setup.bat`

或者：

- `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`

安装脚本会尽量准备：

- 主环境 `.venv`
- 桌面 GUI 依赖
- GPU 相关依赖
- WSL legacy detector 环境
- `runtime/local_envs.json`

### 3. 检查环境

- `uv run neuro-slice doctor`

### 4. 启动桌面 GUI

- `uv run neuro-slice webui`

---

## GUI 使用方式

### 基础模式
适合直接使用：

- 选择视频
- 选择导出目录
- 选择已有 manifest（仅导出模式）
- 调整前置 / 后置留白
- 点击开始

### 高级模式
按需展开，用于调整：

- Whisper 模型
- ASR 设备
- 计算精度
- 识曲开关
- 识曲模型 / Base URL / API Key
- 导出细项

---

## Manifest 是什么

`manifest` 可以理解成：

- 这次切歌任务的中间结果文件
- 一份“歌曲切片时间表”
- 一份“导出说明书”

通常文件名是：

- `songs.json`

它允许你把流程拆成两段：

1. 先分析视频，生成 manifest  
2. 后续只根据 manifest 导出，不需要每次重新分析整条视频

---

## 默认导出策略

当前默认导出优先使用：

- `video_codec = "h264_nvenc"`
- `audio_codec = "aac"`

如果当前机器不支持 `h264_nvenc`，程序会自动回退到：

- `libx264`
- `aac`

也就是说默认策略是：

1. 优先尝试 NVIDIA 精确加速导出
2. 不可用时自动回退到 CPU 精确重编码

这样可以避免 `copy` 模式常见的：

- 前几秒黑屏
- 关键帧切点不准
- 视频开头不可播放

---

## 识曲功能

项目支持可选的 LLM 识曲。

### 默认状态
- 关闭

### 开启后
- 根据转录文本尝试猜歌名
- 默认最多重试 5 次
- 网络波动时不会第一次失败就直接放弃
- 无法确定时回退默认标题

### 文件名策略
如果识曲返回：

- `Artist - Title`

最终导出文件名默认只保留：

- `Title`

例如：

- `[Evil] All the Small Things (2026-02-19).mp4`

---

## 常用命令

### 启动桌面 GUI
- `uv run neuro-slice webui`

### 环境诊断
- `uv run neuro-slice doctor`

### 仅分析
- `uv run neuro-slice analyze --config examples/sample_config.toml --output output`

### 分析并导出
- `uv run neuro-slice all --config examples/sample_config.toml`

### 从已有 manifest 导出
- `uv run neuro-slice export --manifest output/songs.json --config examples/sample_config.toml`

---

## 常见问题

### 1. 安装脚本为什么可能要重新运行一次？
如果是第一次安装 Ubuntu，系统可能会要求你先完成：

- 用户名 / 密码创建
- 首次初始化

完成后退出 Ubuntu，再重新运行安装脚本即可。

### 2. 为什么 `doctor` 里有些项是 `WARN` / `MISSING`？
优先看当前默认后端对应的主路线是否正常：

- 如果默认是 `legacy-wsl`，重点看 WSL 相关项
- Windows fallback 项不一定是主问题

### 3. 为什么第一次运行 detector 可能会比较慢？
因为 detector 可能会在第一次真正运行时下载模型文件并初始化运行环境。

---

## 当前已知限制

- legacy detector 仍然依赖 WSL
- WSL 首次安装不一定完全零交互
- legacy detector 在不同机器上的 TensorFlow / GPU 组合可能存在兼容性差异
- 桌面 GUI 仍在持续迭代中

---

## License

MIT