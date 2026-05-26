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
- **内置 Whisper 路径** — 默认从项目内 `whisper-server/` 启动服务，不再要求用户理解外部仓库目录
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
  - `ffmpeg` — ChatGPT 分析包的视频压缩和关键帧抽取
  - `yt-dlp` — 在线视频标题获取

---

## 安装 / Installation

```bash
pip install -r requirements.txt
```

---

## 使用 / Usage

### 启动 / Launch

```bash
python app.py
```

Windows 下也可双击：

- `start.bat` — 生产模式启动
- `start_debug.bat` — 调试模式启动（显示控制台）

### Whisper 与模型目录 / Whisper and Model Paths

项目现在优先使用项目内目录，减少外部路径歧义：

- `whisper-server/`：可选的内置 Whisper Server 目录。存在 `server.py` 和 `venv/` 时，应用会自动尝试启动它；在线链接下载仍需要该服务。
- `models/`：默认的 `faster-whisper` 模型缓存/存放目录。本地视频转录会优先使用这里，缺少模型时按 `faster-whisper` 逻辑下载到该目录。

也可以通过环境变量覆盖默认路径：

| 变量 | 说明 |
|---|---|
| `WHISPER_SERVER_DIR` | 可选。自定义 Whisper Server 目录；未设置时默认使用项目内 `whisper-server/` |
| `WHISPER_MODEL_DIR` | 可选。自定义模型缓存/存放目录；未设置时默认使用项目内 `models/` |
| `WHISPER_MODEL_PATH` | 可选。指定某个已转换好的 `faster-whisper` / CTranslate2 模型目录；设置后优先于 `MODEL_SIZE` |
| `MODEL_SIZE` | 可选。模型名称，如 `tiny`、`base`、`small`、`medium`、`large-v3`；默认 `base` |
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
├── history.py          # 历史记录管理 / History manager
├── requirements.txt    # Python 依赖 / Dependencies
├── whisper-server/     # 可选内置 Whisper Server / Optional bundled server
├── models/             # 默认模型目录 / Default model directory
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
