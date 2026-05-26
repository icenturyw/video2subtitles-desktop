# 🎬 Video2Subtitles

视频字幕生成桌面工具 — 支持本地视频和在线视频链接，一键生成字幕并导出多种格式。

> A desktop GUI tool for generating subtitles from video files and online video links (YouTube, Bilibili, etc.). Built with PyQt5.

---

## 截图 / Screenshots

| 主界面 / Main Window | 字幕预览 / Subtitle Preview |
|---|---|
| ![example](example.png) | ![example2](example2.png) |
| **运行截图 / Processing** | |
| ![example3](example3.png) | |

---

## 功能特色 / Features

- **本地文件 & 在线视频** — 支持 mp4/avi/mov/mkv 等常见格式，以及 YouTube、Bilibili 等平台链接
- **内置本地 Whisper 服务** — 默认从项目内 `whisper-server/` 自动拉起服务，不再要求用户手动启动外部 server
- **兼容 youtube-live-subtitles 服务** — 支持 `whisper-server/main.py` 入口，同时兼容旧版 `server.py`
- **可见的服务状态** — 客户端底部会显示本地服务启动结果，启动日志写入 `.cache/whisper-service.log`
- **客户端模型安装** — 设置窗口可选择模型大小、模型目录，并一键安装/检查模型
- **本地优先流程** — 本地视频不再依赖服务器；在线链接由内置本地服务负责下载、分段、转录和缓存
- **模型位置可配置** — 本地转录默认使用项目内 `models/` 作为模型目录，也支持指定自定义模型目录
- **本地 & API 转录** — 可直接使用 `faster-whisper` 本地转录，或配合 Whisper Server / Groq / OpenAI 云端 API
- **多格式导出** — 导出 SRT、VTT、TXT 字幕格式
- **ChatGPT 分析包** — 一键生成含代理视频、关键帧和字幕的上传包，方便 ChatGPT 分析
- **双语字幕** — 支持原文+翻译同时显示
- **深色主题** — 现代化暗色 UI 界面

---

## 环境要求 / Prerequisites

- Python 3.10+
- PyQt5 `>=5.15`
- `requests`
- `faster-whisper` — 本地视频转录
- **可选依赖：**
  - 项目内 `whisper-server/` 或自定义 Whisper Server — 在线视频下载和服务模式转录
  - `ffmpeg` — ChatGPT 分析包的视频压缩、关键帧抽取，以及服务端音频处理
  - `yt-dlp` — 在线视频标题获取和服务端在线链接下载

---

## 安装 / Installation

```bash
pip install -r requirements.txt
```

如需在线链接下载/转录，把 `youtube-live-subtitles` 仓库中的 `whisper-server/` 目录放到本项目根目录，或通过 `WHISPER_SERVER_DIR` 指向该目录。桌面端会优先启动其中的 `main.py`。

---

## 使用 / Usage

### 推荐首次流程 / First-run Workflow

1. 启动客户端：`python app.py` 或双击 `start.bat`。
2. 如果右上角显示「⚠ 需要安装模型」，点击「⚙」打开设置。
3. 在「Whisper 模型」里选择模型大小，通常先用 `base`、`small` 或速度更快的 `large-v3-turbo`。
4. 点击「安装/检查模型」，等待状态显示「模型已就绪」。
5. 添加本地视频并点击「开始处理」。

> 本地视频可以直接转录，不需要 Whisper Server。在线链接会优先使用项目内置/指定的本地 Whisper 服务；服务缺失时，界面会保持「本地模式就绪」，并只限制在线链接处理。

### 启动 / Launch

```bash
python app.py
```

Windows 下也可双击：

- `start.bat` — 生产模式启动，会自动尝试拉起 `whisper-server/main.py` 或 `server.py`
- `start_debug.bat` — 调试模式启动（显示控制台），方便查看本地服务日志

### Whisper 服务与模型目录 / Whisper Service and Model Paths

项目现在优先使用项目内目录，减少外部路径歧义：

- `whisper-server/`：可选的内置 Whisper 服务目录。存在 `main.py`（`youtube-live-subtitles`）或 `server.py` 且具备 `venv/` 时，应用会自动尝试启动它；在线链接下载和服务模式转录使用该服务。
- `models/`：默认的 `faster-whisper` 模型缓存/存放目录。本地视频转录会优先使用这里，缺少模型时按 `faster-whisper` 逻辑下载到该目录。

内置本地服务默认监听 `http://127.0.0.1:8765`。桌面端自动拉起本地服务时会把 `API_AUTH_KEY` 置空，避免本机调用被默认密钥拦截；如果你连接的是远程或手动启动的服务，可以通过环境变量 `API_AUTH_KEY` 设置客户端请求头 `x-api-key`。

客户端也可以直接设置模型位置：

1. 点击右上角「⚙」打开设置。
2. 在「Whisper 模型」里选择「模型大小」，支持 `tiny`、`base`、`small`、`medium`、`large-v2`、`large-v3`、`large-v3-turbo`。
3. 设置「模型缓存目录」，用于保存或读取 `faster-whisper` 模型文件。
4. 如需使用某个已经转换好的模型，设置「具体模型目录」；留空时按「模型大小」从缓存目录加载或下载。
5. 点击「安装/检查模型」提前下载或验证模型。

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

使用自定义服务目录：

```bat
set WHISPER_SERVER_DIR=D:\Projects\youtube-live-subtitles\whisper-server
python app.py
```

连接已有远程服务：

```bat
set WHISPER_SERVER_URL=http://192.168.1.10:8765
set API_AUTH_KEY=your-secret-key
python app.py
```

### 本地服务启动状态排查 / Local Service Troubleshooting

`start.bat` 和 `app.py` 都会尝试启动本地 Whisper 服务。为了避免打扰用户，生产模式下服务窗口默认隐藏；如果启动失败，客户端底部状态栏会显示原因。

常见状态：

- `本地+在线就绪`：本地模型可用，在线链接服务也已连接。
- `在线服务已连接`：`127.0.0.1:8765` 可用，但本地 `faster-whisper` 未检测到。
- `本地模式就绪`：本地视频可转录，但在线链接服务未启动或不可用。
- `需要安装模型/服务`：既没有可用服务，也没有可用本地模型。

启动日志位置：

```text
.cache/whisper-service.log
```

如果客户端没有显示在线服务已连接，请优先检查：

1. `whisper-server/` 目录是否存在，或 `WHISPER_SERVER_DIR` 是否指向正确目录。
2. 目录下是否有 `main.py`（来自 `youtube-live-subtitles/whisper-server`）或 `server.py`。
3. `whisper-server/venv/` 是否存在，并已安装服务端依赖。
4. 端口 `8765` 是否被其他程序占用。
5. 打开 `.cache/whisper-service.log` 查看 Python 报错。

需要直接看到服务窗口时，请使用 `start_debug.bat`。

### 基本流程 / Workflow

1. **添加视频** — 点击「添加视频」选择本地文件，或粘贴在线视频链接
2. **开始处理** — 点击「开始处理」进行字幕转录
3. **预览字幕** — 点击已完成的任务查看字幕内容
4. **导出/打包** — 右键导出 SRT/VTT/TXT 或生成 ChatGPT 分析包

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
├── settings_patch.py   # 设置窗口和启动流程扩展 / Setup flow extension
├── history.py          # 历史记录管理 / History manager
├── requirements.txt    # Python 依赖 / Dependencies
├── whisper-server/     # 可选内置 Whisper 服务 / Optional bundled service
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
