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
- **本地 & API 转录** — 可配合 [Whisper Server](https://github.com/icenturyw/youtube-live-subtitles) 本地转录，或使用 Groq/OpenAI 云端 API
- **多格式导出** — 导出 SRT、VTT、TXT 字幕格式
- **ChatGPT 分析包** — 一键生成含代理视频、关键帧和字幕的上传包，方便 ChatGPT 分析
- **双语字幕** — 支持原文+翻译同时显示
- **深色主题** — 现代化暗色 UI 界面

---

## 环境要求 / Prerequisites

- Python 3.10+
- PyQt5 `>=5.15`
- `requests`
- **可选依赖：**
  - [Whisper Server](https://github.com/icenturyw/youtube-live-subtitles) — 本地转录和在线视频下载
  - `ffmpeg` — ChatGPT 分析包的视频压缩和关键帧抽取
  - `yt-dlp` — 在线视频标题获取

---

## 安装 / Installation

```bash
pip install PyQt5 requests
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

### 环境变量 / Environment Variables

| 变量 | 说明 |
|---|---|
| `WHISPER_SERVER_DIR` | [Whisper Server](https://github.com/icenturyw/youtube-live-subtitles) 目录路径 |

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
├── history.py          # 历史记录管理 / History manager
├── requirements.txt    # Python 依赖 / Dependencies
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
