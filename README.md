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
- **在线视频保留 MP4** — 在线链接默认优先下载并保存视频文件，字幕、原视频和 ChatGPT 分析包可以在同一输出目录中使用
- **下载策略可配置** — 支持「保存 MP4 视频」「仅转写不保留视频」「仅音频转写」三种模式，并可限制最高质量
- **自动多格式输出** — 任务完成后会自动保存 SRT、VTT、TXT，并生成 `manifest.json` 输出元数据
- **历史记录增强** — 历史窗口可打开输出目录、加回任务列表、重新生成 ChatGPT 包、复制/打开来源
- **一键环境检查** — 设置页可检查 Python、依赖、yt-dlp、ffmpeg、模型目录、输出目录、端口和服务健康状态
- **统一模型目录** — 客户端模型安装目录 `models/` 同时供内置服务和本地 fallback 使用，下载一次，两边共用
- **隐藏后台窗口** — Windows 下开始处理时，转写 helper、yt-dlp 等后台子进程不会再弹出空黑窗口
- **可见的服务状态** — 客户端底部会显示本地服务启动结果，启动日志写入 `.cache/whisper-service.log`
- **客户端模型安装** — 设置窗口可选择模型大小、模型目录，并一键安装/检查模型
- **本地优先流程** — 本地视频和在线链接都优先走内置本地服务；服务不可用时，本地视频会 fallback 到客户端直转
- **模型位置可配置** — 本地转录默认使用项目内 `models/` 作为模型目录，也支持指定自定义模型目录
- **本地 & API 转录** — 可直接使用 `faster-whisper` 本地转录，或连接自定义 Whisper Server
- **多格式导出** — 导出 SRT、VTT、TXT 字幕格式
- **ChatGPT 分析包** — 右侧字幕预览区和右键菜单都可生成含代理视频、关键帧和字幕的上传包
- **双语字幕** — 支持原文+翻译同时显示
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

---

## 安装 / Installation

```bash
pip install -r requirements.txt
```

如果你只处理本地视频，安装完成即可使用。如果要处理 YouTube/Bilibili 等在线链接，请确保 `yt-dlp` 和 `ffmpeg` 可用。打开设置后也可以点击「一键检查环境」查看当前依赖和服务状态。

---

## 使用 / Usage

### 推荐首次流程 / First-run Workflow

1. 启动客户端：`python app.py` 或双击 `start.bat`。
2. 如果右上角显示「⚠ 需要安装模型」，点击「⚙」打开设置。
3. 点击「一键检查环境」，确认依赖、端口、模型目录和输出目录状态。
4. 在「Whisper 模型」里选择模型大小，通常先用 `base`、`small` 或速度更快的 `large-v3-turbo`。
5. 按需要设置「在线视频下载模式」和「下载质量」，默认「保存 MP4 视频（推荐）」。
6. 点击「安装/检查模型」，等待状态显示「模型已就绪」。
7. 添加本地视频或在线视频链接并点击「开始处理」。

> 现在项目已经内置 `whisper-server/`。客户端安装的模型会通过 `WHISPER_MODEL_DIR` 传给内置服务，因此在线链接和本地文件可以共用同一份模型缓存。

### 启动 / Launch

```bash
python app.py
```

Windows 下也可双击：

- `start.bat` — 生产模式启动，会自动尝试拉起 `whisper-server/main.py`，后台窗口默认隐藏
- `start_debug.bat` — 调试模式启动（显示控制台），方便查看本地服务日志

### Whisper 服务与模型目录 / Whisper Service and Model Paths

项目现在自带轻量本地服务：

- `whisper-server/main.py`：内置 FastAPI sidecar，提供 `/health`、`/transcribe`、`/upload`、`/status/{task_id}`。
- `models/`：默认的 `faster-whisper` 模型缓存/存放目录。客户端本地 fallback 和内置服务都会使用这里。

内置本地服务默认监听：

```text
http://127.0.0.1:8765
```

桌面端自动拉起本地服务时会把 `WHISPER_MODEL_DIR`、`WHISPER_MODEL_PATH`、`MODEL_SIZE`、`DEVICE`、`COMPUTE_TYPE`、`V2S_DOWNLOAD_MODE`、`V2S_DOWNLOAD_QUALITY` 传入服务进程。也就是说，在设置窗口里下载/选择的模型和下载策略，对在线链接和本地文件都有效。

在线链接处理时，内置服务会优先使用 `yt-dlp` 下载视频并合并为 MP4；桌面端随后会把下载得到的视频、字幕、历史记录和 `manifest.json` 保存到同一个输出子目录。右键已完成任务，或在右侧字幕预览区点击「📦 生成 ChatGPT 包」，都可以生成 ChatGPT 分析包；分析包会使用该视频生成 480p 代理视频、关键帧和上传 zip。

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
5. 点击「安装/检查模型」提前下载或验证模型。
6. 点击「一键检查环境」可检查依赖、服务、端口和目录权限。

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
| `DEVICE` | 可选。推理设备，默认 `cpu` |
| `COMPUTE_TYPE` | 可选。计算类型，默认 `int8` |
| `V2S_DOWNLOAD_MODE` | 可选。在线视频下载模式：`video`、`transcribe_only`、`audio`；默认 `video` |
| `V2S_DOWNLOAD_QUALITY` | 可选。下载质量：`best`、`720p`、`480p`；默认 `best` |
| `V2S_KEEP_DOWNLOADED_VIDEO` | 可选。是否保留下载视频：`true` / `false`；默认 `true` |

Windows 示例：

```bat
set WHISPER_MODEL_DIR=D:\AI\whisper-models
set MODEL_SIZE=small
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

需要直接看到服务窗口时，请使用 `start_debug.bat`。

### 基本流程 / Workflow

1. **添加视频** — 点击「添加视频」选择本地文件，或粘贴在线视频链接
2. **开始处理** — 点击「开始处理」下载/保存视频并进行字幕转录
3. **预览字幕** — 点击已完成的任务查看字幕内容
4. **导出/打包** — 右键导出 SRT/VTT/TXT，或在右侧字幕预览区点击「生成 ChatGPT 包」
5. **历史管理** — 点击「历史记录」重新打开输出、加回任务列表或重新生成 ChatGPT 包

---

## 项目结构 / Project Structure

```
video_2_subtitles/
├── app.py              # 入口文件 / Entry point
├── main_window.py      # 主窗口界面 / Main GUI window
├── api_client.py       # Whisper API 客户端 / API client
├── local_whisper.py    # 本地 Whisper 转录 / Local transcriber
├── whisper_config.py   # Whisper 路径和模型配置 / Whisper path config
├── client_settings.py  # 客户端持久化设置 / Client settings
├── diagnostics.py      # 环境检查 / Runtime diagnostics
├── output_manifest.py  # 输出元数据 / Output metadata
├── output_patch.py     # 输出流程和历史记录增强 / Output workflow patch
├── settings_patch.py   # 设置窗口和启动流程扩展 / Setup flow extension
├── history.py          # 历史记录管理 / History manager
├── requirements.txt    # Python 依赖 / Dependencies
├── whisper-server/     # 内置 Whisper 服务 / Bundled local service
│   ├── main.py         # FastAPI sidecar / Local API server
│   ├── requirements.txt
│   ├── cache/          # 服务端字幕缓存 / Service cache
│   └── temp/           # 下载和上传临时文件 / Temporary files
├── models/             # 默认模型目录 / Default model directory
├── .cache/             # 本地设置、服务日志和辅助脚本缓存 / Local cache
├── start.bat           # 生产启动（Win） / Production launcher
├── start_debug.bat     # 调试启动（Win） / Debug launcher
├── example.png         # 界面截图 / Screenshot
├── example2.png        # 界面截图 / Screenshot
├── example3.png        # 运行截图 / Processing screenshot
└── output/             # 字幕输出目录 / Output directory
```

---

## 许可 / License

MIT
