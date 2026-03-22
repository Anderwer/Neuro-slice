# Neuro-slice

一个面向歌回 / 音乐直播回放的本地桌面切片工具。

`Neuro-slice` 的当前目标是：

- 输入一条长视频回放
- 自动检测其中的歌曲片段
- 生成可复查的 `manifest`（通常是 `songs.json`）
- 导出每首歌对应的独立切片
- 提供本地桌面 GUI，方便直接操作
- 默认优先保留你原来准确率更高的 legacy 检测方案

当前项目以 **Windows 本地桌面工具** 为主，主入口是 **Qt 桌面 GUI**，不是浏览器页面。

---

## 当前架构（重要）

当前项目默认采用：

# **Windows 主环境 + WSL legacy detector**

也就是说：

### 1）主环境：Windows `.venv`
主环境负责：

- 桌面 GUI
- CLI
- `faster-whisper`
- 视频 / 音频导出
- manifest 生成与读取
- 可选 LLM 识曲
- 主流程编排

### 2）legacy detector：WSL Ubuntu
高准确率歌曲段检测器放在 **WSL** 中运行，负责：

- `inaSpeechSegmenter`
- `runtime/detect_segments_tf.py`
- `runtime/detect_segments_wsl.sh`

### 3）运行桥接：`runtime/local_envs.json`
主程序会通过 `runtime/local_envs.json` 自动找到：

- 主环境 Python
- WSL distro
- WSL detector Python
- WSL detector 脚本
- WSL wrapper 脚本

也就是说，正常情况下用户**不需要手动填写 detector 路径**。

---

## 为什么是这种架构

因为这个项目里有两类需求：

### 主程序更适合：
- Windows 本地桌面 GUI
- `faster-whisper`
- 导出
- 本地工具体验
- Windows 兼容
- GPU/CPU 自动回退

### legacy detector 更适合：
- 保留旧版更高准确率的检测方式
- 放到更适合它的运行环境里
- 不干扰主环境依赖

如果把所有东西强行塞进同一个环境里，通常会遇到：

- 依赖冲突
- 安装复杂
- 平台兼容性差
- 排查困难

所以当前默认设计就是：

- **主环境负责工具体验**
- **WSL 负责高准确率检测**

---

# 快速开始（先看这里）

如果你只是想尽快把项目跑起来，推荐直接按下面这几步走。

## 1）先克隆仓库

先把仓库克隆到本地：

- `git clone https://github.com/Anderwer/Neuro-slice.git`
- `cd Neuro-slice`

如果你已经把仓库下载到本地，可以直接进入项目目录即可。

---

## 2）一键安装（推荐）

Windows 下直接运行：

- `scripts\setup.bat`

或者：

- `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`

### 这一步会尽量自动准备：
- 主环境 `.venv`
- 桌面 GUI 依赖
- GPU 相关依赖
- WSL legacy detector 环境
- `runtime/local_envs.json`

### 注意
由于 WSL 属于系统级组件，安装脚本可能仍然会遇到这些情况：

- 需要管理员权限
- 需要重启
- 需要第一次启动 Ubuntu 完成用户初始化

如果脚本提示你先运行一次：

- `wsl -d Ubuntu`

那就先完成 Ubuntu 首次初始化，然后再回到项目里重新运行安装脚本。

---

## 3）检查当前环境状态

安装后先运行：

- `uv run neuro-slice doctor`

如果这里大部分项目都是 `OK`，说明：

- 主环境已经就绪
- WSL detector runtime 也已经基本接通

---

## 4）启动桌面 GUI

运行：

- `uv run neuro-slice webui`

然后在桌面 GUI 中：

- 选择视频文件
- 选择导出目录
- 直接点击“分析并导出”

---

## 5）如果你只是想先试命令行

可以先运行：

- `uv run neuro-slice analyze --config examples/sample_config.toml --output output`

---

# 系统要求

建议环境：

- Windows
- Python 3.11+
- `uv`
- `ffmpeg`
- `ffprobe`
- WSL2
- Ubuntu（默认用于 legacy detector）

如果你希望主环境中的 ASR 尽量使用 GPU，还需要：

- NVIDIA 显卡
- 主环境 GPU runtime 依赖

---

# 一键安装目前会做什么

默认安装脚本会尝试：

## 主环境
- 创建或复用 `.venv`
- 安装主项目依赖
- 安装开发依赖
- 安装音频依赖
- 安装 GPU 依赖
- 安装桌面 GUI 依赖

## WSL legacy detector 环境
- 检查 `wsl`
- 检查 Ubuntu 是否存在且已初始化
- 在 WSL 中准备 `/root/.neuro-slice-legacy`
- 安装：
  - `python3`
  - `python3-venv`
  - `python3-pip`
  - `ffmpeg`
  - `inaSpeechSegmenter`
- 把 detector 脚本复制到：
  - `/root/neuro-slice-runtime/detect_segments_tf.py`
  - `/root/neuro-slice-runtime/detect_segments_wsl.sh`

## 运行桥接
- 生成 `runtime/local_envs.json`

---

# 手动安装（如果你不想用一键脚本）

## 1）主环境

在项目根目录执行：

- `uv venv`
- `uv sync --group dev --extra gpu --extra audio --extra web`

---

## 2）WSL legacy detector 环境

你需要保证：

### WSL 基础
- `wsl` 可用
- `Ubuntu` 已安装
- `Ubuntu` 已完成首次初始化

### WSL detector 目录
- `/root/.neuro-slice-legacy`
- `/root/neuro-slice-runtime`

### WSL detector 依赖
- `python3`
- `python3-venv`
- `python3-pip`
- `ffmpeg`
- `inaSpeechSegmenter`

### detector 脚本
- `/root/neuro-slice-runtime/detect_segments_tf.py`
- `/root/neuro-slice-runtime/detect_segments_wsl.sh`

---

# 如何启动桌面 GUI

启动命令：

- `uv run neuro-slice webui`

虽然命令名还是 `webui`，但当前实际启动的是：

- **Qt 桌面 GUI**

不是浏览器页面。

---

# GUI 的使用逻辑

## 基础模式
适合直接使用：

- 选择视频
- 选择导出目录
- 选择已有 manifest（仅导出模式）
- 调整前置 / 后置留白
- 设置导出视频 / 导出音频
- 设置歌手标记和日期
- 点击开始

## 高级模式
高级设置可展开，用于调：

- Whisper 模型
- ASR 设备
- 计算精度
- 是否启用识曲
- 识曲模式
- 识曲模型
- Base URL
- API Key
- 保存高级设置
- 重置高级设置
- 导出当前配置为 TOML

---

# 当前支持的主要操作

## 1）仅分析
用途：

- 只检测歌曲片段
- 生成 manifest
- 不导出切片

## 2）分析并导出
用途：

- 检测歌曲片段
- 生成 manifest
- 直接导出视频 / 音频切片

## 3）导出已有 Manifest
用途：

- 不重新分析视频
- 直接读取已有 `songs.json`
- 根据已有 manifest 重新导出

---

# 什么是 Manifest

Manifest 可以理解成：

- 这次切歌任务的中间结果文件
- 一份“歌曲切片时间表”
- 一份“导出说明书”

通常文件名是：

- `songs.json`

里面会记录：

- 每首歌的开始时间
- 结束时间
- 留白后的时间范围
- 时长
- 标题
- transcript
- 识曲结果
- 导出结果

## 为什么它很重要
因为它允许你把流程拆成两段：

### 第一段：分析
先检测歌曲片段并生成 `songs.json`

### 第二段：导出
之后只根据 `songs.json` 导出，不需要每次都重新分析整条视频

这对长视频非常重要。

---

# 当前检测策略

当前默认 detector 后端是：

- `legacy-wsl`

也就是：

- 主程序在 Windows 跑
- detector 在 WSL 中通过 wrapper 启动

当前主程序会优先通过：

- `/root/neuro-slice-runtime/detect_segments_wsl.sh`

去启动 WSL legacy detector。

---

# 当前导出策略

## 默认导出模式：NVIDIA 精确加速导出

为了避免关键帧切点问题，同时尽量提升速度，当前默认视频导出优先使用：

- `video_codec = "h264_nvenc"`
- `audio_codec = "aac"`

这意味着默认导出不是 `copy`，而是：

- 仍然进行精确重编码
- 但优先尝试使用 NVIDIA 编码器加速

### 优点
- 切点更准
- 不容易出现前几秒黑屏
- 比 `copy` 稳定
- 相比 `libx264` 通常更快

### 自动回退
如果当前机器不支持 `h264_nvenc`，程序会自动回退到：

- `libx264`
- `aac`

也就是说当前导出策略是：

1. **优先尝试 NVIDIA 精确加速**
2. **如果不可用，自动回退到 CPU 精确重编码**

---

# 当前识曲功能

当前项目支持可选的 LLM 识曲。

## 默认状态
- 关闭

## 开启后
- 会基于转录文本尝试猜歌名
- 默认会做最多 5 次重试
- 网络波动时不会第一次失败就直接放弃
- 如果无法给出足够确定的答案，会回退到默认标题

## 文件名策略
如果识曲返回类似：

- `Artist - Title`

最终导出文件名默认只保留：

- `Title`

例如：

- `[Evil] All the Small Things (2026-02-19).mp4`

而不是：

- `[Evil] Blink-182 - All the Small Things (2026-02-19).mp4`

---

# 当前进度反馈

GUI 中目前已经支持：

- 当前运行状态
- 实时日志
- 摘要
- 已导出文件
- Manifest JSON
- 停止当前任务

另外，legacy detector 运行时也会输出阶段状态，例如：

- 正在启动 legacy detector 子进程
- 正在初始化 legacy detector
- 正在提取 legacy detector 分析音频
- 正在运行 `inaSpeechSegmenter` 分段
- 当前阶段心跳状态
- 正在输出 `SEGMENTS_JSON`

---

# `doctor` 诊断命令

如果你怀疑双环境没配好，运行：

- `uv run neuro-slice doctor`

它会检查：

- `detector.backend`
- `runtime/local_envs.json`
- `.venv_legacy`
- fallback detector Python
- detector 脚本
- WSL distro
- WSL detector Python
- WSL detector script
- WSL runtime 配置是否已写好

这是当前排查环境问题最重要的命令。

---

# 安装失败排查

如果你在安装或第一次运行时遇到问题，优先按下面顺序检查。

## 1）先运行 `doctor`

最先建议运行：

- `uv run neuro-slice doctor`

它会告诉你：

- 主环境是否存在
- `runtime/local_envs.json` 是否存在
- Windows fallback legacy 环境是否存在
- WSL distro 是否存在
- WSL legacy detector 的 Python / 脚本路径是否已经写好

如果这里已经有明显的 `MISSING` 或 `WARN`，先解决它们，再继续跑 GUI。

## 2）安装脚本卡在 WSL 阶段

如果 `setup.ps1` / `setup.bat` 卡在 WSL 相关步骤，常见原因包括：

- 没有管理员权限
- WSL 尚未安装
- Ubuntu 虽然已安装，但还没完成第一次启动初始化
- 系统要求重启后才能继续

这时建议：

- 先手动运行一次 `wsl -d Ubuntu`
- 完成 Ubuntu 首次用户初始化
- 然后重新运行安装脚本

## 3）GPU 没有生效

### 主环境 ASR
主环境中的 `faster-whisper` 是否走 GPU，取决于 Windows 主环境中的 GPU runtime 是否准备好。

如果没有准备好，日志里通常会看到类似：
- CUDA 运行库找不到
- 自动回退到 CPU/int8

### WSL legacy detector
WSL 中的 legacy detector 是否能真正走 GPU，取决于：
- WSL GPU 透传是否正常
- WSL 中 TensorFlow GPU 运行时是否完整
- WSL detector 启动时是否正确设置了 `LD_LIBRARY_PATH`

如果 legacy detector 仍然只看到 CPU，这通常不是 GUI 的问题，而是 WSL 内 TensorFlow 运行时问题。

## 4）Qt 桌面 GUI 启动失败

如果运行：

- `uv run neuro-slice webui`

报桌面 GUI 依赖缺失，通常说明主环境还没把桌面依赖装完整。

可以重新执行：

- `uv sync --group dev --extra gpu --extra audio --extra web`

然后再启动 GUI。

## 5）导出视频很慢

当前默认导出策略不是 `copy`，而是：

- `h264_nvenc`
- 如果不可用则自动回退到 `libx264`

这样做是为了避免：
- 开头黑屏
- 关键帧切点不准
- 视频前几秒不可播放

所以导出速度比 `copy` 慢是预期行为，不一定是 bug。

## 6）如果安装脚本提示成功，但运行仍然有问题

建议按这个顺序重新确认：

1. `uv run neuro-slice doctor`
2. `uv run neuro-slice webui`
3. 看 GUI 日志中的：
   - 当前 detector backend
   - WSL legacy detector 是否真正启动
   - 是否自动回退 CPU
   - 是否有 detector / wrapper 路径错误

---

# 当前已知限制

## 1）legacy detector 仍然依赖 WSL
这是当前默认架构的一部分，不是 bug。

## 2）WSL 首次安装不一定完全零交互
某些机器上可能需要：
- 管理员权限
- 重启
- 手动首次进入 Ubuntu 完成初始化

## 3）WSL detector 是否真正用上 GPU，仍取决于 WSL 内 TensorFlow 的运行时是否完全接通
这也是当前最值得继续验证和优化的点之一。

## 4）主环境中的 ASR 是否走 GPU，取决于 Windows 主环境中的 GPU runtime 是否准备好
如果主环境里没有对应 runtime，Whisper 会自动回退 CPU。

## 5）导出默认用精确重编码，速度会比 `copy` 慢
这是为了避免黑屏和关键帧切点问题。

## 6）桌面 GUI 仍在持续迭代
当前版本优先保证：
- 可用
- 稳定
- 结构清楚

视觉和交互还在继续优化中。

---

# 当前建议的使用顺序

推荐顺序：

## 第一步
运行安装脚本准备环境：

- `scripts\setup.bat`

或：

- `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`

## 第二步
运行：

- `uv run neuro-slice doctor`

确认环境状态。

## 第三步
启动桌面 GUI：

- `uv run neuro-slice webui`

## 第四步
在 GUI 中：
- 选视频
- 选导出目录
- 开始分析或分析并导出

---

# 常用命令

## 启动桌面 GUI
- `uv run neuro-slice webui`

## 运行诊断
- `uv run neuro-slice doctor`

## 仅分析
- `uv run neuro-slice analyze --config examples/sample_config.toml`

## 分析并导出
- `uv run neuro-slice all --config examples/sample_config.toml`

## 从已有 manifest 导出
- `uv run neuro-slice export --manifest output/songs.json --config examples/sample_config.toml`

---

## License

MIT

---

## 当前建议

如果你准备继续长期维护这个项目，建议后面继续补这些文件：

- `CHANGELOG.md`
- `docs/architecture.md`
- `CONTRIBUTING.md`
- `.env.example`

这样仓库会更适合长期维护和公开协作。