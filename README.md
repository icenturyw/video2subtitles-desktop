# 🎬 Video2Subtitles

视频字幕生成桌面工具 — 支持本地视频和在线视频链接，一键生成字幕并导出多种格式。

> A desktop GUI tool for generating subtitles from video files and online video links (YouTube, Bilibili, etc.). Built with PyQt5.

---

## 截图 / Screenshots

| 主界面 / Main Window | 字幕预览 / Subtitle Preview |
|---|---|
| ![example](example.png) | ![example2.png](example2.png) |
| **运行截图 / Processing** | |
| ![example3.png](example3.png) | |

---

## 功能特色 / Features

- **本地文件 & 在线视频** — 支持 mp4/avi/mov/mkv 等常见格式，以及 YouTube、Bilibili 等平台链接
- **已内置 Whisper 服务** — 项目自带 `whisper-server/main.py`，启动客户端时会自动拉起本机 sidecar 服务
- **GPU 自动加速** — 默认自动检测 NVIDIA GPU；检测到 RTX/4070 等显卡时优先使用 `cuda + float16`，否则回退 `cpu + int8`
- **在线视频保留 MP4** — 在线链接默认优先下载并保存视频文件，字幕、原视频和 ChatGPT 分析包可以在同一输出目录中使用
- **下载策略可配置** — 支持「保存 MP4 视频」「仅转写不保留视频」「仅音频转写」三种模式，并可限制最高质量
- **YouTube 播放列表选择** — 粘贴带 `list=` 的 YouTube 链接时，可选择添加整个播放列表，或只添加当前粘贴的视频
- **自动多格式输出** — 任务完成后会自动保存 SRT、VTT、TXT，并生成 `manifest.json` 输出元数据
- **字幕工具模块化** — 字幕时间格式、SRT/VTT/TXT 读写、SRT 解析和文件名清洗集中到 `subtitle_utils.py`，降低重复实现
- **基础验证脚本** — 新增 `tools/check_project.py`，可执行源码语法检查和基础单元测试
- **历史记录增强** — 历史窗口可打开输出目录、加回任务列表、重新生成 ChatGPT 包、复制/打开来源
- **错误日志增强** — 生成失败时会突出显示错误、弹出可复制详情，并在右侧预览区保留完整错误日志和服务日志尾部
- **标题获取增强** — 添加 YouTube 链接时会过滤 yt-dlp 的警告输出，避免把 `No supported JavaScript runtime...` 当成视频标题显示
- **一键环境检查** — 设置页可检查 Python、依赖、GPU、yt-dlp、ffmpeg、模型目录、输出目录、端口和服务健康状态
- **统一模型目录** — 客户端模型安装目录 `models/` 同时供内置服务和本地 fallback 使用，下载一次，两边共用
- **隐藏后台窗口** — Windows 下开始处理时，转写 helper、yt-dlp 等后台子进程不会再弹出空黑窗口
- **可见的服务状态** — 客户端底部会显示本地服务启动结果，启动日志写入 `.cache/whisper-service.log`
- **客户端模型安装** — 设置窗口可选择模型大小、模型目录，并一键安装/检查模型
- **本地优先流程** — 本地视频和在线链接都优先走内置本地服务；服务不可用时，本地视频会 fallback 到客户端直转
- **模型位置可配置** — 本地转录默认使用项目内 `models/` 作为模型目录，也支持指定自定义模型目录
- **本地 & API 转录** — 可直接使用 `faster-whisper` 本地转录，或连接自定义 Whisper Server
- **多格式导出** — 导出 SRT、VTT、TXT 字幕格式
- **ChatGPT 分析包** — 右侧字幕预览区和右键菜单都可生成含代理视频、关键帧和字幕的上传包；生成过程中会显示持续进度浮层，完成/失败后弹窗提醒，并支持打开包目录或复制路径/错误信息
- **字幕翻译** — 集成本地化引擎，支持 OpenAI 兼容 API 批量翻译字幕，术语表注入，断点续翻
- **翻译配置持久化** — 本地化设置中的 API 地址、模型和 Key 会保存到 `.cache/settings.json`，点击 OK 后同步刷新当前进程和本地化引擎运行时配置
- **双语+样式字幕** — 支持原文、译文、双语 ASS/SSA 字幕，可自定义字体、大小、轮廓、阴影和边距
- **硬字幕烧录** — 通过 FFmpeg 将字幕直接烧录到视频画面，支持质量预设（快速/平衡/高质量）
- **配音音色与试听** — 配音模式支持 Edge-TTS 与本地 Qwen3-TTS，目标语言变化时自动刷新音色列表，并可生成试听音频
- **深色主题** — 现代化暗色 UI 界面

---

## 环境要求 / Prerequisites

- Python 3.10+
- PyQt5 `>=5.15`
- `requests`
- `faster-whisper`
- `fastapi` / `uvicorn` / `python-multipart` — 内置本地服务
- `yt-dlp` — 在线视频下载
- **可选依赖：**
  - `ffmpeg` — `yt-dlp` 合并在线视频音视频流，以及 ChatGPT 分析包的视频处理
  - NVIDIA 驱动 / CUDA 运行环境 — 使用 RTX 4070 等 NVIDIA GPU 推理时需要

---

## 安装 / Installation

```bash
pip install -r requirements.txt
```

如果你只处理本地视频，安装完成即可使用。如果要处理 YouTube/Bilibili 等在线链接，请确保 `yt-dlp` 和 `ffmpeg` 可用。打开设置后也可以点击「一键检查环境」查看当前依赖、GPU 和服务状态。

---

## 开发与验证 / Development Checks

轻量验证不会下载模型，也不会启动 Whisper 服务，适合每次改动后快速检查：

```bash
python tools/check_project.py
python -m unittest discover -s tests
```

`tools/check_project.py` 会执行项目 Python 源码语法编译和 `tests/` 基础单元测试。完整桌面端验证仍建议在 Windows 图形环境中手动启动 `python app.py` 或 `start_debug.bat` 检查。

---

## 维护记录 / Maintenance Notes

### 2026-06 — 本地化、翻译 API 与 Qwen3-TTS 增强

- **TTS 音色选择与试听**：本地化设置的配音模式新增音色下拉、刷新和试听按钮；Edge-TTS 会按目标语言过滤音色，Qwen3-TTS 会优先读取本地 sidecar `/voices`，服务未启动时回退到预设音色。
- **Qwen3-TTS sidecar**：新增 `qwen3-tts-engine/` 和 `ui/qwen_tts_setup_dialog.py`，支持本地 Qwen3-TTS 服务状态检测、模型加载、语音合成能力探测，以及与本地化引擎的 TTS provider 对接。
- **Qwen3-TTS 服务生命周期修复**：此前 Qwen3-TTS sidecar 仅能通过手动对话框启动且 auto-start 函数从未被调用（死代码），导致 TTS 阶段服务未运行时报笼统 `No TTS audio was generated`。本次修复包括：
  - `localization-engine/engine/pipeline.py`：TTS 合成前增加 `/health` 预检，服务不可用时立即以 `TTS_SERVICE_DOWN` 终止任务并给出明确操作指引（"请在设置→Qwen3-TTS 管理中启动服务"），替代逐段失败后笼统的 `TTS_NO_OUTPUT`。
  - `main_window.py`：发起配音任务前探测 Qwen3-TTS 健康状态，不可用时尝试自动启动；仍失败时弹框阻止任务发出。
  - `app.py main()`：当保存设置 `tts_provider == qwen3‑tts` 且 `localization_mode == dub` 时，后台 daemon 线程自动 `ensure_qwen3_tts_engine()`（仅起 HTTP 进程，不预加载模型，首请求按 `qwen_mode` 自动按需加载）。
  - `tests/test_localization_engine.py`：新增 `test_qwen3_tts_fails_with_service_down` 回归测试，mock `/health` 返回连接拒绝，断言 pipeline 以 `TTS_SERVICE_DOWN` 失败；同时为既有稳定 seed 测试补上健康检查 mock。
- **翻译设置保存修复**：`LocalizationDialog` 点击 OK 后会保存 `translation_base_url`、`translation_model`、`translation_api_key`，并调用 `apply_settings_to_env(..., overwrite=True)` 覆盖旧 `V2S_TRANSLATION_*` 运行时变量，避免旧环境变量反向覆盖新设置。
- **翻译 Key 热更新**：本地化引擎新增 `/config/translation-api-key`，新任务也会把当前 Key 传给引擎但不会写入任务记录，避免重启前后继续使用旧 Key。
- **OpenAI 兼容响应增强**：翻译客户端兼容 NewAPI 风格的 `data.choices` 响应；401/403/404/400 会保留服务端 JSON 错误详情，不可恢复错误不再重复重试，日志会继续脱敏 API Key。
- **YouTube cookies 与下载错误提示**：Whisper 服务会检查 `whisper-server/cookies.txt` 是否包含真实 YouTube/Google cookies，并在登录、过期、格式不可用时给出更明确的中文提示。
- **FFmpeg 配音合成修复**：配音合成统一使用 `localization_workspace/rendered/` 输出目录，自动创建父目录，并在 FFmpeg 失败时返回 stderr 尾部，便于复制错误日志定位问题。
- **验证覆盖**：新增/更新 `tests/test_qwen3_tts.py`、`tests/test_translation_parser.py`、`tests/test_localization_language_codes.py`、`tests/test_youtube_captions.py` 和 `tests/test_localization_engine.py`，覆盖音色、翻译响应解析、YouTube cookies 和音频合成错误路径。

### 2026-06 — Phase 3：字幕翻译与烧录 MVP

- **本地化引擎 sidecar**：在 `localization-engine/` 中新增独立的 FastAPI 服务（端口 8766），提供 `/health`、`/jobs`、`/cancel`、`/retry`、`/logs` 端点，由 `app.py` 自动启动。
- **Pipeline 编排**：新增 `PipelineRunner`，按 `prepare → normalize → translate → subtitle_export → render → finalize` 顺序执行，支持 checkpoint 断点续跑。
- **字幕标准化**：`localization-engine/subtitles/normalize.py` 支持读取 SRT/ASS/VTT 并转为 `SubtitleSegment`；`subtitle_ass.py` 支持生成带样式的 ASS 字幕（原文/译文/双语）。
- **翻译引擎**：`localization-engine/translation/` 提供 OpenAI 兼容 API 翻译提供者、分段批处理、术语表注入（JSON/CSV）、响应解析和 API Key 脱敏日志。
- **FFmpeg 渲染**：`services/ffmpeg_service.py` 支持硬字幕烧录（atomic rename 防止损坏文件）和软字幕封装；`localization-engine/rendering/` 提供字幕滤镜构造和编码预设（快速/平衡/高质量）。
- **客户端集成**：新增「🌐 本地化」工具栏按钮，打开 `LocalizationDialog` 设置翻译参数；ASR 完成后自动将源字幕提交到本地化引擎，翻译完成后的字幕自动显示在预览区。
- **详情配置**：`localization_dialog.py` 支持模式选择（快速字幕/翻译字幕成片）、源语言/目标语言、翻译服务（OpenAI 兼容）、API 地址/模型/Key、字幕模式（双语/仅译文/仅原文）、硬字幕烧录和软字幕封装。
- **新增文件**：`localization_client.py`、`localization-engine/`、`services/ffmpeg_service.py`、`services/sidecar_manager.py`、`ui/localization_dialog.py`、`ui/subtitle_style_dialog.py`、`subtitle_ass.py`。

### 2026-06 — 任务状态模型与取消/重试优化

- **新增任务状态模型**：在 `core/task_state.py` 中定义 `TaskStatus` 枚举（PENDING、QUEUED、DOWNLOADING、PROCESSING、SAVING、COMPLETED、ERROR、CANCELLED）和 `TaskInfo` 数据类，提供 `status_to_ui_text()` 和 `normalize_status()` 安全转换函数，兼容现有字符串状态。
- **停止按钮行为改进**：点击停止后，WorkerThread 立即设置 `_cancel_event`，`wait_for_result()` 循环中检测到取消标记后立即返回 `{"status": "cancelled"}`，不再无休止等待服务结果。UI 中被取消的任务显示「已取消」，进度保持当前值。已完成任务不受影响。停止后所有按钮状态正确恢复。
- **失败任务重试整理**：「重试失败」按钮只处理 `error` 状态任务，不处理 `cancelled` 任务。右键菜单对 `cancelled` 任务提供「重新处理」选项。重试前清理旧错误信息。已完成任务不会被重试。
- **`api_client.py` 兼容性**：`wait_for_result()` 新增可选参数 `cancel_checker=None`，旧调用方式完全兼容。

---

## 使用 / Usage

### 推荐首次流程 / First-run Workflow

1. 启动客户端：`python app.py` 或双击 `start.bat`。
2. 如果右上角显示「⚠ 需要安装模型」，点击「⚙」打开设置。
3. 点击「一键检查环境」，确认依赖、GPU、端口、模型目录和输出目录状态。
4. 在「Whisper 模型」里选择模型大小，通常先用 `base`、`small` 或速度更快的 `large-v3-turbo`。
5. 在「GPU / 推理设备」里保持默认「自动」，有 NVIDIA GPU 时会优先使用 CUDA。
6. 按需要设置「在线视频下载模式」和「下载质量」，默认「保存 MP4 视频（推荐）」。
7. 点击「安装/检查模型」，等待状态显示「模型已就绪」。
8. 添加本地视频或在线视频链接并点击「开始处理」。

> 现在项目已经内置 `whisper-server/`。客户端安装的模型会通过 `WHISPER_MODEL_DIR` 传给内置服务，因此在线链接和本地文件可以共用同一份模型缓存。

### 启动 / Launch

```bash
python app.py
```

Windows 下也可双击：

- `start.bat` — 生产模式启动，会自动尝试拉起 `whisper-server/main.py`，后台窗口默认隐藏
- `start_debug.bat` — 调试模式启动（显示控制台），方便查看本地服务日志

### GPU / RTX 4070 加速

默认设置为：

```text
推理设备: auto
计算类型: auto
```

自动模式会按下面规则解析：

| 检测结果 | 实际使用 |
|---|---|
| 检测到 NVIDIA GPU，例如 RTX 4070 | `cuda + float16` |
| 未检测到 NVIDIA GPU | `cpu + int8` |

确认是否用上 4070：

1. 打开「⚙ 设置」。
2. 查看「GPU / 推理设备」区域，应该显示类似：`自动模式将使用 cuda/float16`。
3. 点击「一键检查环境」，查看「GPU 推理」一项。
4. 处理视频时也可以打开任务管理器或运行 `nvidia-smi` 观察显存和 GPU 占用。

如果之前已经启动过旧版内置服务，它可能仍然以 CPU 模式常驻。修改 GPU 设置后，请关闭客户端并结束旧的 Python/Whisper 服务进程，或者直接重启电脑后再启动客户端，确保新的 `DEVICE=cuda` / `COMPUTE_TYPE=float16` 生效。

如果自动模式没有识别 4070，请检查：

- NVIDIA 驱动是否正常安装
- 命令行运行 `nvidia-smi` 是否能看到 4070
- faster-whisper / CTranslate2 是否具备 CUDA 支持
- 设置页是否被手动改成了 `CPU`

### Whisper 服务与模型目录 / Whisper Service and Model Paths

项目现在自带轻量本地服务：

- `whisper-server/main.py`：内置 FastAPI sidecar，提供 `/health`、`/transcribe`、`/upload`、`/status/{task_id}`。
- `models/`：默认的 `faster-whisper` 模型缓存/存放目录。客户端本地 fallback 和内置服务都会使用这里。

内置本地服务默认监听：

```text
http://127.0.0.1:8765
```

桌面端自动拉起本地服务时会把 `WHISPER_MODEL_DIR`、`WHISPER_MODEL_PATH`、`MODEL_SIZE`、`DEVICE`、`COMPUTE_TYPE`、`V2S_DOWNLOAD_MODE`、`V2S_DOWNLOAD_QUALITY` 传入服务进程。也就是说，在设置窗口里下载/选择的模型、GPU 设备和下载策略，对在线链接和本地文件都有效。

在线链接处理时，内置服务会优先使用 `yt-dlp` 下载视频并合并为 MP4；桌面端随后会把下载得到的视频、字幕、历史记录和 `manifest.json` 保存到同一个输出子目录。右键已完成任务，或在右侧字幕预览区点击「📦 生成 ChatGPT 包」，都可以生成 ChatGPT 分析包；分析包会使用该视频生成 480p 代理视频、关键帧和上传 zip。

生成 ChatGPT 包时，主窗口会弹出持续显示的进度浮层，展示当前阶段和百分比，例如压缩代理视频、抽取关键帧、写入清单、生成轻量/完整上传包。生成完成后会弹出提醒，可直接打开包目录或复制路径；生成失败时可一键复制错误信息。

### 输出目录内容 / Output Folder

每个任务完成后会生成独立输出子目录，通常包含：

```text
视频文件.mp4
字幕.srt
字幕.vtt
字幕.txt
manifest.json
chatgpt_package/      # 生成 ChatGPT 包后出现
```

`manifest.json` 会记录来源链接/路径、标题、语言、字幕数量、视频文件、SRT/VTT/TXT 文件、下载模式、下载质量和 ChatGPT 包路径。即使历史记录文件损坏，输出目录本身也保留了任务元数据。

### 历史记录 / History

点击「历史记录」可以查看已处理任务。历史窗口支持：

- 打开输出目录
- 加回任务列表
- 重新生成 ChatGPT 包
- 复制来源路径/链接
- 打开来源文件夹或浏览器链接

### 在线标题获取 / Online Title Fetching

添加在线视频链接后，客户端会异步调用 `yt-dlp` 预取标题。标题预取只用于列表展示，不影响后续下载、转写和输出保存。

为避免 YouTube 近期的 JavaScript runtime / 签名警告污染标题显示，标题预取现在会：

- 分离 `yt-dlp` 的 stdout 和 stderr，只从 stdout 读取标题。
- 使用 `--no-warnings` 并过滤 `WARNING:`、`ERROR:`、`No supported JavaScript runtime...` 等非标题行。
- 复用设置中的代理 `V2S_PROXY`，并自动使用 `whisper-server/cookies.txt`。
- 如果标题仍然获取失败，则保留原始链接显示，不再把报错文本当标题。

### YouTube 播放列表 / YouTube Playlists

粘贴带 `list=` 参数的 YouTube 链接时，客户端会先弹出选择：

- **添加整个列表**：使用 `yt-dlp` 读取播放列表条目，并把列表内视频逐个添加为普通单视频任务。
- **只添加当前视频**：自动移除 `list`、`index`、`pp` 等播放列表参数，只保留当前粘贴视频本身。

播放列表展开只发生在添加链接阶段；后续下载、转写、历史记录、输出目录和 ChatGPT 分析包仍复用单视频任务流程。

### 错误日志 / Error Logs

生成过程中如果任务失败，客户端会：

- 在任务列表中使用更醒目的红色错误状态显示失败原因。
- 弹出「错误日志」窗口，包含任务来源、完整错误信息、`.cache/whisper-service.log` 路径和最近 80 行服务日志，可一键复制。
- 选中失败任务时，右侧预览区会显示完整错误日志，并提供「复制错误日志」按钮。
- 重新处理失败任务时，会清理该任务的旧错误日志，避免误复制旧内容。

### 在线视频下载策略 / Online Download Strategy

设置窗口支持三种下载模式：

| 模式 | 说明 | 适合场景 |
|---|---|---|
| 保存 MP4 视频（推荐） | 下载并保留 MP4，输出目录中会有原视频，ChatGPT 完整包可用 | 默认模式，推荐大多数用户使用 |
| 仅用于转写，不保留视频 | 下载视频用于识别，完成后清理下载的视频文件 | 只需要字幕、想节省磁盘空间 |
| 仅音频转写（节省空间） | 使用 yt-dlp 提取音频进行转写 | 只要字幕，不需要视频文件；完整视频分析包可能不可用 |

下载质量可选择：最高可用质量、最高 720p、最高 480p。

### 模型与路径配置 / Model and Path Settings

1. 点击右上角「⚙」打开设置。
2. 在「Whisper 模型」里选择「模型大小」，支持 `tiny`、`base`、`small`、`medium`、`large-v2`、`large-v3`、`large-v3-turbo`。
3. 设置「模型缓存目录」，用于保存或读取 `faster-whisper` 模型文件。
4. 如需使用某个已经转换好的模型，设置「具体模型目录」；留空时按「模型大小」从缓存目录加载或下载。
5. 在「GPU / 推理设备」中选择 `自动`、`CUDA` 或 `CPU`，并选择计算类型。
6. 点击「安装/检查模型」提前下载或验证模型。
7. 点击「一键检查环境」可检查依赖、GPU、服务、端口和目录权限。

设置会保存到 `.cache/settings.json`，下次启动自动生效。外部环境变量仍可覆盖客户端保存的值。

也可以通过环境变量覆盖默认路径：

| 变量 | 说明 |
|---|---|
| `WHISPER_SERVER_DIR` | 可选。自定义 Whisper 服务目录；未设置时默认使用项目内 `whisper-server/` |
| `WHISPER_SERVER_URL` | 可选。自定义服务地址；默认 `http://127.0.0.1:8765` |
| `API_AUTH_KEY` | 可选。远程或手动启动服务的 API Key；客户端会作为 `x-api-key` 发送 |
| `WHISPER_MODEL_DIR` | 可选。自定义模型缓存/存放目录；未设置时默认使用项目内 `models/` |
| `WHISPER_MODEL_PATH` | 可选。指定某个已转换好的 `faster-whisper` / CTranslate2 模型目录；设置后优先于 `MODEL_SIZE` |
| `MODEL_SIZE` | 可选。模型名称，如 `tiny`、`base`、`small`、`medium`、`large-v3`、`large-v3-turbo`；默认 `base` |
| `DEVICE` | 可选。推理设备：`auto`、`cuda`、`cpu`；默认 `auto` |
| `COMPUTE_TYPE` | 可选。计算类型：`auto`、`float16`、`int8_float16`、`int8`、`float32`；默认 `auto` |
| `V2S_DOWNLOAD_MODE` | 可选。在线视频下载模式：`video`、`transcribe_only`、`audio`；默认 `video` |
| `V2S_DOWNLOAD_QUALITY` | 可选。下载质量：`best`、`720p`、`480p`；默认 `best` |
| `V2S_KEEP_DOWNLOADED_VIDEO` | 可选。是否保留下载视频：`true` / `false`；默认 `true` |
| `V2S_PROXY` | 可选。在线视频标题预取和 yt-dlp 下载使用的代理地址；留空表示直连 |

Windows 示例：

```bat
set WHISPER_MODEL_DIR=D:\AI\whisper-models
set MODEL_SIZE=small
python app.py
```

强制使用 4070 / CUDA：

```bat
set DEVICE=cuda
set COMPUTE_TYPE=float16
python app.py
```

指定单个本地模型目录：

```bat
set WHISPER_MODEL_PATH=D:\AI\whisper-models\models--Systran--faster-whisper-small\snapshots\xxxx
python app.py
```

连接已有远程服务：

```bat
set WHISPER_SERVER_URL=http://192.168.1.10:8765
set API_AUTH_KEY=your-secret-key
python app.py
```

### 本地服务启动状态排查 / Local Service Troubleshooting

`start.bat` 和 `app.py` 都会尝试启动本地 Whisper 服务。为了避免打扰用户，生产模式下服务窗口默认隐藏；点击「开始处理」时启动的转写 helper、`yt-dlp` 等后台子进程也会隐藏控制台窗口。如果启动失败，客户端底部状态栏会显示原因。

常见状态：

- `本地+在线就绪`：本地模型可用，在线链接服务也已连接。
- `在线服务已连接`：`127.0.0.1:8765` 可用，但本地 `faster-whisper` 未检测到。
- `本地模式就绪`：本地视频可转录，在线视频链接会自动尝试启动内置服务。
- `需要安装模型/服务`：既没有可用服务，也没有可用本地模型。

启动日志位置：

```text
.cache/whisper-service.log
```

如果客户端没有显示在线服务已连接，请优先检查：

1. 是否已经运行 `pip install -r requirements.txt`。
2. 端口 `8765` 是否被其他程序占用。
3. `yt-dlp` 和 `ffmpeg` 是否可用，尤其是在线链接下载失败时。
4. 设置页点击「一键检查环境」。
5. 打开 `.cache/whisper-service.log` 查看 Python 报错。
6. YouTube 标题或下载异常时，优先更新 `yt-dlp`，必要时安装 Node.js/Deno 等 JavaScript runtime 或使用 cookies。

需要直接看到服务窗口时，请使用 `start_debug.bat`。

### 基本流程 / Workflow

1. **添加视频** — 点击「添加视频」选择本地文件，或粘贴在线视频链接；若 YouTube 链接包含播放列表，可选择添加整个列表或只添加当前视频
2. **开始处理** — 点击「开始处理」下载/保存视频并进行字幕转录
3. **预览字幕** — 点击已完成的任务查看字幕内容
4. **翻译字幕（可选）** — 点击「🌐 本地化」打开翻译设置，选择目标语言和翻译服务（OpenAI 兼容 API），点击确定后再次处理时会自动翻译字幕并将翻译烧录到视频
   - 配音模式可选择 `edge-tts` 或 `qwen3-tts`，选择目标语言后会刷新可用音色，点击「试听」可快速验证当前音色。
   - OpenAI 兼容 API 地址请使用真实接口根路径，例如 `https://example.com/v1`；`/chat/completions` 会由程序自动拼接。
5. **失败排查** — 如果任务失败，选中失败任务查看完整错误日志，或点击「复制错误日志」发给开发者定位
6. **导出/打包** — 右键导出 SRT/VTT/TXT，或在右侧字幕预览区点击「生成 ChatGPT 包」；打包期间会显示进度浮层，完成后可打开包目录或复制路径
7. **历史管理** — 点击「历史记录」重新打开输出、加回任务列表或重新生成 ChatGPT 包

---

## 常见问题修复 / Troubleshooting

### 中文标题乱码 / Chinese Title Garbled

如果 YouTube/Bilibili 视频的中文标题显示为乱码（如 `???????? S1 Ep4??...`），是由于 Windows 系统编码（CP936/GBK）无法正确输出 Unicode 字符。

**修复**：已在以下位置设置 `PYTHONIOENCODING=utf-8` 环境变量：
- `title_fetch_patch.py` — 标题预取时 QProcess 子进程
- `whisper-server/main.py` — yt-dlp 下载子进程

### yt-dlp 未找到 / yt-dlp Not Found

如果提示 `未找到 yt-dlp`，但已通过 pip 安装，说明 `yt-dlp.exe` 不在系统 PATH 中。

**自动降级**：代码已内置自动降级逻辑，依次尝试：
1. `yt-dlp` 命令（PATH 中查找）
2. 用户 Python Scripts 目录下的 `yt-dlp.exe`
3. `python -m yt_dlp`（模块方式运行）

### 翻译 API 认证或模型不可用 / Translation API Auth or Model Errors

本地化引擎使用 OpenAI 兼容的 `/chat/completions` 接口。若翻译失败，请优先检查：

1. API 地址应指向真实接口根路径，例如 `https://example.com/v1`，不要填写文档站地址或网页控制台地址。
2. 点击本地化设置 OK 后，API 地址、模型和 Key 会保存并热更新到本地化引擎；如果引擎进程很旧，重启应用可强制加载最新代码。
3. `401/403` 通常表示 Key 无效、过期、额度不足或分组权限不匹配。
4. `404 当前 API 不支持所选模型` 表示 Key 可以访问服务，但该网关的 chat 通道不支持当前模型；即使 `/models` 能列出模型，也不代表 `/chat/completions` 一定可调用。
5. `5xx` 或空响应通常是上游通道暂时不可用，建议在网关后台切换可用渠道或更换模型后重试。

### YouTube 需要登录 / YouTube Login Required

如果 YouTube 下载提示需要登录，请在浏览器中登录 YouTube 后导出 Netscape 格式 cookies，并放到：

```text
whisper-server/cookies.txt
```

`whisper-server/cookie_backups/` 和 cookies 文件不会提交到仓库。替换 cookies 后重新处理任务即可。

### TTS 配音失败 / TTS Dubbing Failed

如果生成任务提示 `TTS_EMPTY_INPUT`、`TTS_NO_AUDIO_OUTPUT`、`TTS_ZERO_BYTE_AUDIO` 或 `TTS_GENERATION_FAILED`，表示转写/翻译已经完成，但 TTS 阶段没有生成有效音频。

常见原因：

1. **TTS_EMPTY_INPUT**：字幕或翻译文本全为空，TTS 没有可朗读的内容。
2. **TTS_NO_AUDIO_OUTPUT**：TTS 执行完成但输出目录无任何音频文件，可能是 TTS 模型未正确安装或引擎异常。
3. **TTS_ZERO_BYTE_AUDIO**：TTS 生成了文件但全部为 0 字节，通常是引擎写文件失败或磁盘空间/权限问题。
4. **TTS_GENERATION_FAILED**：引擎调用异常（如 Qwen3-TTS 服务未运行、voice 配置不存在）。
5. **TTS 参数配置**：确保已选择有效的 voice、目标语言与 voice 匹配。

排查命令：

```powershell
Select-String -Path "D:\software\video_2_subtitles\.cache\whisper-service.log" -Pattern "tts|TTS|Traceback|Exception|Error|audio|wav|mp3|voice|cuda|ffmpeg"
```

> 如果字幕已生成但 TTS 失败，程序会保留字幕结果，用户可以先导出字幕。

### pythonw 未找到 / pythonw Not Found

启动脚本 `start.bat` / `start_debug.bat` 现在会自动查找可用的 Python 解释器：
`pythonw` → `python` → `py`，找不到时给出明确提示。

---

## 项目结构 / Project Structure

```
video_2_subtitles/
├── app.py              # 入口文件 / Entry point
├── main_window.py      # 主窗口界面 / Main GUI window
├── api_client.py       # Whisper API 客户端 / API client
├── local_whisper.py    # 本地 Whisper 转录 / Local transcriber
├── gpu_config.py       # GPU 自动检测和设备解析 / GPU config helpers
├── gpu_patch.py        # GPU 设置界面补丁 / GPU settings patch
├── whisper_config.py   # Whisper 路径和模型配置 / Whisper path config
├── client_settings.py  # 客户端持久化设置 / Client settings
├── diagnostics.py      # 环境检查 / Runtime diagnostics
├── output_manifest.py  # 输出元数据 / Output metadata
├── subtitle_utils.py   # 字幕格式化、解析和文件名清洗 / Subtitle utilities
├── output_patch.py     # 输出流程和历史记录增强 / Output workflow patch
├── error_log_patch.py  # 错误日志展示和复制增强 / Error log UI patch
├── title_fetch_patch.py # 在线标题获取增强 / Online title fetch patch
├── playlist_patch.py   # YouTube 播放列表添加模式选择 / Playlist add-mode patch
├── settings_patch.py   # 设置窗口和启动流程扩展 / Setup flow extension
├── history.py          # 历史记录管理 / History manager
├── requirements.txt    # Python 依赖 / Dependencies
├── whisper-server/     # 内置 Whisper 服务 / Bundled local service
│   ├── main.py         # FastAPI sidecar / Local API server
│   ├── requirements.txt
│   ├── cache/          # 服务端字幕缓存 / Service cache
│   └── temp/           # 下载和上传临时文件 / Temporary files
├── localization-engine/ # 本地化引擎 / Localization engine sidecar
│   ├── main.py         # FastAPI sidecar (port 8766)
│   ├── engine/         # Pipeline 编排 / Pipeline orchestrator
│   ├── subtitles/      # 字幕读写、标准化、验证 / Subtitle I/O & validation
│   ├── translation/    # 翻译提供者、批处理、术语表 / Translation providers
│   └── rendering/      # FFmpeg 滤镜和编码预设 / FFmpeg filters & presets
├── qwen3-tts-engine/   # 本地 Qwen3-TTS sidecar / Local Qwen3-TTS sidecar
│   ├── main.py         # FastAPI sidecar (port 8767)
│   └── engine/         # 模型管理、音色、合成 / Model manager & synthesis
├── localization_client.py # 本地化引擎 HTTP 客户端 / Localization API client
├── services/
│   ├── ffmpeg_service.py  # FFmpeg 渲染（烧录/软字幕）/ Rendering service
│   └── sidecar_manager.py  # Sidecar 进程管理 / Sidecar process manager
├── ui/
│   ├── localization_dialog.py   # 本地化设置对话框 / Localization settings dialog
│   ├── qwen_tts_setup_dialog.py # Qwen3-TTS 管理对话框 / Qwen3-TTS setup dialog
│   └── subtitle_style_dialog.py # 字幕样式编辑器 / Subtitle style editor
├── models/             # 默认模型目录 / Default model directory
├── .cache/             # 本地设置、服务日志和辅助脚本缓存 / Local cache
├── start.bat           # 生产启动（Win） / Production launcher
├── start_debug.bat     # 调试启动（Win） / Debug launcher
├── tools/              # 开发验证脚本 / Development check scripts
├── tests/              # 基础单元测试 / Unit tests
├── example.png         # 界面截图 / Screenshot
├── example2.png        # 界面截图 / Screenshot
├── example3.png        # 运行截图 / Processing screenshot
└── output/             # 字幕输出目录 / Output directory
```

---

## 许可 / License

MIT
