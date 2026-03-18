# Neuro-slice

一个面向歌回 / 音乐直播回放的本地桌面切片工具。

`Neuro-slice` 的目标是：

- 输入一条长视频回放
- 自动检测其中的歌曲片段
- 生成可复查的 `manifest`（通常是 `songs.json`）
- 导出每首歌对应的独立切片
- 提供桌面 GUI，方便本地直接操作
- 默认保留你原来准确率更高的 legacy 检测方案

当前项目以 **桌面工具** 为主，不再把浏览器式界面作为主入口。

---

## 这个项目现在的定位

这个项目最初来自 VTuber 歌回切片需求，但现在的目标已经扩大为：

- 不局限于 VTuber
- 尽量适配更广泛的歌回 / 音乐直播回放
- 核心仍然是“自动切歌”
- 当前默认优先保留 **旧版高准确率检测器**

换句话说：

- UI 和主流程是新的
- 歌曲检测的核心优先沿用旧模式
- 桌面 GUI、导出、识曲、配置和诊断则在新项目里统一管理

---

## 当前默认架构：双环境方案

为了同时满足：

- 旧检测器的准确率
- 新项目的可维护性
- 本地桌面工具体验

项目现在默认采用 **双环境架构**。

### 1）主环境：`.venv`

主环境负责：

- 桌面 GUI
- CLI
- `faster-whisper`
- 视频 / 音频导出
- manifest 生成与读取
- 可选 LLM 识曲
- 主流程编排

### 2）legacy 检测环境：`.venv_legacy`

legacy 环境负责：

- 原高准确率歌曲段检测
- `inaSpeechSegmenter`
- `runtime/detect_segments_tf.py`

### 3）运行桥接：`runtime/local_envs.json`

主程序会通过 `runtime/local_envs.json` 自动找到：

- 主环境 Python
- legacy 环境 Python
- legacy detector 脚本

也就是说，正常情况下用户**不需要手动填写 detector 路径**。

---

## 为什么需要两个环境

因为当前项目里有两类完全不同的依赖需求：

### 主程序更适合：

- 桌面 GUI
- `faster-whisper`
- 导出
- 本地工具体验
- Windows 兼容
- 可选 GPU/CPU 自动回退

### legacy detector 更适合：

- 保留你旧版更高准确率的检测方式
- 独立运行
- 不干扰主环境

如果把它们强行塞进同一个环境里，通常会遇到：

- 依赖冲突
- 安装复杂
- 平台兼容性更差
- 排查问题更困难

所以当前默认设计就是：

- 主环境负责工具体验
- legacy 环境负责高准确率检测

---

## 当前主界面入口

桌面 GUI 启动命令：

- `uv run neuro-slice webui`

虽然命令名还是 `webui`，但当前实际启动的是：

- **Qt 桌面 GUI**

不是浏览器页面。

---

## 系统要求

建议环境：

- Python 3.11+
- Windows
- `uv`
- `ffmpeg`
- `ffprobe`

如果要在主环境中使用 GPU 跑 ASR，还需要：

- NVIDIA 显卡
- 对应的主环境 GPU 运行时依赖

---

## 一键安装（推荐）

Windows 下推荐直接运行：

- `scripts\setup.bat`

或者：

- `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`

### 当前一键安装默认会尝试准备：

- 主环境 `.venv`
- 主环境依赖
- 开发依赖
- 音频依赖
- GPU 依赖
- 桌面 GUI 依赖
- legacy 检测环境 `.venv_legacy`
- `runtime/local_envs.json`

也就是说，目标是让用户在首次安装后尽量直接用，而不需要自己手动配置双环境路径。

---

## 手动安装（如果你不想用一键脚本）

### 1）主环境

在项目根目录执行：

- `uv venv`
- `uv sync`
- `uv sync --group dev --extra gpu --extra audio --extra web`

### 2）legacy 环境

项目默认希望由安装脚本自动创建 `.venv_legacy`。  
如果你手动配置，需要确保：

- `.venv_legacy` 已创建
- `inaSpeechSegmenter` 已安装
- `runtime/detect_segments_tf.py` 存在
- `runtime/local_envs.json` 正常生成

---

## 如何启动桌面 GUI

安装完成后，启动 GUI：

- `uv run neuro-slice webui`

启动后你会看到一个本地桌面窗口，可以直接：

- 选择视频文件
- 选择导出目录
- 选择已有 manifest（仅导出模式）
- 点击开始处理

---

## GUI 的使用逻辑

### 基础模式

适合直接使用：

- 选择视频
- 选择导出目录
- 调整前置 / 后置留白
- 设置导出视频 / 导出音频
- 设置歌手标记和日期
- 点击开始

### 高级模式

高级设置是可折叠的，适合需要调参时再展开：

- Whisper 模型
- ASR 设备
- 计算精度
- 是否启用识曲
- 识曲模式
- 识曲模型
- Base URL
- API Key
- 保存 / 重置高级设置
- 导出当前配置为 TOML

---

## 当前支持的主要操作

### 1）仅分析

用途：

- 只检测歌曲片段
- 生成 manifest
- 不导出切片

### 2）分析并导出

用途：

- 检测歌曲片段
- 生成 manifest
- 直接导出视频 / 音频切片

### 3）导出已有 Manifest

用途：

- 不重新分析视频
- 直接读取已有 `songs.json`
- 根据已有 manifest 重新导出

---

## 什么是 Manifest

Manifest 可以理解为：

- 这次切歌任务的中间结果文件
- 一份“歌曲切片时间表”
- 一份“导出说明书”

通常文件名是：

- `songs.json`

它里面会记录：

- 每首歌的开始时间
- 结束时间
- 留白后的时间范围
- 时长
- 标题
- transcript
- 识曲结果
- 导出结果

### 为什么 Manifest 很重要

因为它允许你把流程拆成两段：

#### 第一段：分析
先检测歌曲片段并生成 `songs.json`

#### 第二段：导出
之后只根据 `songs.json` 导出，不需要每次都重新分析整条视频

这对长视频非常重要。

---

## 当前导出策略

### 默认导出模式：精确重编码

为了避免常见的关键帧切点问题，当前默认视频导出使用：

- `video_codec = "libx264"`
- `audio_codec = "aac"`

这样做的优点是：

- 切点更准
- 不容易出现前几秒黑屏
- 更适合真正发布用的切片

缺点是：

- 比 `copy` 慢
- CPU 占用更高

但目前默认优先保证稳定性和可用性。

---

## 当前识曲功能

当前项目支持可选的 LLM 识曲。

### 默认状态
- 关闭

### 开启后
- 会基于转录文本尝试猜歌名
- 默认会做最多 5 次重试
- 网络波动时不会第一次失败就直接放弃
- 如果无法给出足够有把握的答案，会回退到默认标题

### 文件名策略
如果识曲返回类似：

- `Artist - Title`

最终导出文件名默认只保留：

- `Title`

例如：

- `[Evil] All the Small Things (2026-02-19).mp4`

而不是：

- `[Evil] Blink-182 - All the Small Things (2026-02-19).mp4`

---

## 当前进度反馈

GUI 中目前已经支持：

- 当前运行状态
- 实时日志
- 摘要
- 已导出文件
- Manifest JSON

另外，legacy detector 运行时也会实时输出阶段状态，例如：

- 正在启动 legacy detector 子进程
- 正在初始化 legacy detector
- 正在提取 legacy detector 分析音频
- 正在运行 `inaSpeechSegmenter` 分段
- 每隔 5 秒一次的心跳状态
- 正在输出 `SEGMENTS_JSON`

所以当前不会再像之前那样“卡住却没有任何反馈”。

---

## 停止任务

当前 GUI 支持：

- `停止当前任务`

当前实现不是简单“标记停止”，而是会尽量尝试打断：

- 当前 `ffmpeg`
- 当前 legacy detector 子进程

不过如果当前某一步是库内部阻塞调用，停止仍然可能不是瞬间完成。

---

## 诊断命令

如果你怀疑双环境没配好，直接运行：

- `uv run neuro-slice doctor`

它会检查：

- `detector.backend`
- `runtime/local_envs.json`
- `.venv_legacy`
- legacy Python
- legacy detector script
- runtime 配置是否正常读取

这是当前排查双环境问题最重要的命令。

---

## 当前默认工作流建议

推荐顺序：

### 第一步
先运行安装脚本准备环境

### 第二步
运行：

- `uv run neuro-slice doctor`

确认双环境是否正常

### 第三步
启动桌面 GUI：

- `uv run neuro-slice webui`

### 第四步
在 GUI 里：
- 选视频
- 选导出目录
- 先跑一次分析或分析并导出

---

## 推荐仓库结构概念

当前项目核心结构大致如下：

- `src/neuro_slice/`
  - 主程序代码
- `runtime/`
  - legacy detector 脚本
  - 本地环境桥接配置
- `scripts/`
  - Windows 安装脚本
- `examples/`
  - 示例配置
- `docs/`
  - 额外文档
- `.venv/`
  - 主环境（本地）
- `.venv_legacy/`
  - legacy 检测环境（本地）

---

## 当前已知限制

### 1）legacy detector 仍然依赖独立环境
这是当前默认架构的一部分，不是 bug。

### 2）legacy detector 本质上仍然是黑盒步骤
虽然现在已经有心跳日志，但它不是细粒度百分比进度。

### 3）ASR 是否走 GPU 取决于主环境 GPU runtime 是否准备好
如果主环境里找不到对应 CUDA runtime，Whisper 会自动回退 CPU。

### 4）导出默认用精确重编码，速度会比 `copy` 慢
这是为了避免黑屏和关键帧切点问题。

### 5）桌面 GUI 目前仍在持续迭代
当前版本优先的是：
- 可用
- 稳定
- 结构清楚

UI 还在持续美化中。

---

## 适合谁用

当前版本最适合：

- 你自己本地长期使用
- 想保留旧 detector 高准确率的人
- 希望用桌面 GUI 操作而不是命令行的人
- 需要处理本地长视频回放的人

---

## 当前不建议的理解方式

不要把这个项目理解成：

- 单环境、单模型、单命令一把梭就永远稳定的自动神工具

更准确地说，它当前是：

> 一个以高准确率 legacy 检测器为核心，  
> 由桌面 GUI 统一管理主流程的本地切歌工具。

---

## 后续方向

当前后续最重要的方向包括：

- 继续优化桌面 GUI 的产品感
- 让进度反馈更细
- 强化双环境自动安装体验
- 让导出、识曲、摘要、manifest 的实时反馈更完整
- 在保留旧 detector 准确率的前提下继续提升可用性

---

## 常用命令

### 启动桌面 GUI
- `uv run neuro-slice webui`

### 运行诊断
- `uv run neuro-slice doctor`

### 仅分析
- `uv run neuro-slice analyze --config examples/sample_config.toml`

### 分析并导出
- `uv run neuro-slice all --config examples/sample_config.toml`

### 从已有 manifest 导出
- `uv run neuro-slice export --manifest output/songs.json --config examples/sample_config.toml`

---

## License

MIT

---

## 当前建议

如果你准备继续长期维护这个项目，建议后面继续补这些文档：

- `CHANGELOG.md`
- `docs/architecture.md`
- `CONTRIBUTING.md`
- `.env.example`

这样仓库会更适合长期维护和公开协作。