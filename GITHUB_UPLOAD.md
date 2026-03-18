# GitHub 上传清单与发布说明

这个文档用于在把 `Neuro-slice` 上传到 GitHub 前，快速检查当前项目是否已经整理到适合公开发布的状态。

---

## 项目定位

`Neuro-slice` 是一个本地桌面工具，目标是：

- 输入歌回 / 音乐直播回放视频
- 自动检测歌曲片段
- 生成 manifest
- 导出歌曲切片
- 默认使用 **legacy 高准确率检测器**
- 主程序与 legacy 检测器采用 **双环境方案**

---

## 当前默认架构

### 主环境
主环境通常位于：

- `.venv`

负责：

- 桌面 GUI
- ASR / `faster-whisper`
- 导出视频 / 音频
- manifest 生成与读取
- 可选识曲
- 主流程编排

### Legacy 检测环境
legacy 环境通常位于：

- `.venv_legacy`

负责：

- 原高准确率音乐段检测
- `inaSpeechSegmenter`
- `runtime/detect_segments_tf.py`

### 运行桥接
主程序通过：

- `runtime/local_envs.json`

自动找到 legacy 检测环境和 detector 脚本，并以子进程方式调用。

---

## 上传前检查清单

### 1. 必要文件是否存在
确认这些文件已经存在：

- `README.md`
- `LICENSE`
- `pyproject.toml`
- `.gitignore`
- `scripts/setup.ps1`
- `scripts/setup.bat`
- `runtime/detect_segments_tf.py`
- `examples/sample_config.toml`
- `docs/gpu.md`

### 2. 不应该上传的内容
确认这些内容不要进 GitHub：

- `.venv/`
- `.venv_legacy/`
- `output/`
- `test_video/`
- 大体积视频文件
- 切好的成品视频 / 音频
- 临时日志
- 私密 API Key
- 本地调试缓存

### 3. 配置检查
确认示例配置没有写入你的私人信息：

- API Key 为空
- Base URL 不包含私人地址（如果不想公开）
- 路径不写死到私人磁盘目录
- 默认参数适合公开示例

### 4. 说明文档检查
确认 README 至少说清楚：

- 项目是做什么的
- 默认使用 legacy 双环境模式
- 如何一键安装
- 如何启动桌面 GUI
- 如何运行 `doctor`
- 如果 legacy 环境失败应该怎么办
- 当前导出默认使用精确重编码

### 5. GUI/主流程检查
在本地至少验证一次：

- 启动桌面 GUI
- 选择视频
- 选择输出目录
- 运行一次分析
- 运行一次分析并导出
- 验证 manifest 能生成
- 验证导出文件名符合预期

### 6. Doctor 检查
上传前建议本地执行一次：

- `uv run neuro-slice doctor`

确认双环境桥接状态正常。

---

## 推荐上传前本地命令

如果你要在本地完成 Git 初始化和首个提交，可以按下面顺序执行。

### 初始化仓库
```bash
git init
```

### 查看当前状态
```bash
git status
```

### 添加文件
```bash
git add .
```

### 首次提交
```bash
git commit -m "Initial release: legacy dual-environment desktop edition"
```

---

## 推荐的 `.gitignore` 关注点

应确保忽略以下内容：

### Python 环境
- `.venv/`
- `.venv_legacy/`
- `__pycache__/`

### 输出目录
- `output/`
- 任何导出的视频 / 音频

### 本地测试资源
- `test_video/`
- 私人回放文件

### 临时日志
- `*.log`
- 调试输出文件

### 私密配置
- `.env`
- 包含密钥的本地配置文件

---

## 推荐发布说明（Release Notes）

下面是一份适合作为首版发布说明的模板。

---

# Neuro-slice v0.1.0

## Highlights

- 默认采用 **legacy subprocess detector**，优先保留旧版高准确率检测方案
- 使用 **双环境架构**
  - 主环境：桌面 GUI、ASR、导出、识曲
  - legacy 环境：高准确率 detector
- 提供本地桌面 GUI
- 支持生成和导入 manifest
- 支持导出已有 manifest
- 默认视频导出采用 **精确重编码**
  - 避免常见的黑屏和关键帧切点问题
- 提供 `doctor` 命令检查双环境桥接状态

## Included

- 桌面 GUI
- 停止当前任务
- 高级设置保存 / 重置
- 导出当前配置为 TOML
- 识曲可选启用
- 识曲请求自动重试
- legacy detector 子进程进度日志
- 实时日志与结果更新

## Known Limitations

- `inaSpeechSegmenter` / TensorFlow 相关依赖在不同机器上的兼容性可能不同
- legacy detector 在某些 Windows 环境下仍可能需要额外排查
- ASR 是否走 GPU 取决于当前主环境中的 CUDA runtime 是否齐全
- 导出视频采用精确重编码，速度会比 `copy` 慢
- 桌面 GUI 仍在持续优化中，当前版本以稳定和可用为优先

---

## 推荐 GitHub 仓库描述

可以考虑使用下面这样的仓库描述：

> Automatically detect and export song clips from karaoke / stream VODs, using a high-accuracy legacy detector with a local desktop GUI workflow.

---

## 推荐 GitHub Topics

建议添加一些主题标签，方便别人找到：

- `python`
- `qt`
- `pyside6`
- `ffmpeg`
- `whisper`
- `karaoke`
- `vod`
- `audio-processing`
- `video-processing`
- `vtuber`
- `desktop-tool`

---

## 发布前最后自查

在真正 push 到 GitHub 前，再确认一遍：

- [ ] 没有把 `.venv` 上传
- [ ] 没有把 `.venv_legacy` 上传
- [ ] 没有把测试视频上传
- [ ] 没有把输出成品上传
- [ ] 没有泄露 API Key
- [ ] README 内容完整
- [ ] 安装脚本可运行
- [ ] `doctor` 命令可运行
- [ ] GUI 能正常启动
- [ ] manifest 流程可用

---

## 说明

如果你后面准备继续迭代，建议下一步新增：

- `CHANGELOG.md`
- `docs/architecture.md`
- `CONTRIBUTING.md`
- `.env.example`

这样仓库会更适合长期维护。