# Video2Subtitles

A desktop GUI tool for generating subtitles from video files and online video links (YouTube, Bilibili, etc.). Built with PyQt5.

## Features

- **Local files & online videos** — Supports local video files and links from YouTube, Bilibili, Douyin, and more
- **Local & API transcription** — Works with a local Whisper server or cloud APIs (Groq, OpenAI)
- **Subtitle export** — Export to SRT, VTT, or TXT format
- **ChatGPT analysis pack** — Generate a ready-to-upload zip with proxy video, key frames, and subtitles for ChatGPT analysis
- **Dark theme** — Modern dark UI with a polished look
- **Bilingual subtitles** — Supports translation alongside original text

## Prerequisites

- Python 3.10+
- PyQt5 `>=5.15`
- `requests`
- [Whisper Server](https://github.com/icenturyw/youtube-live-subtitles) (optional, for local transcription and URL support)
- `ffmpeg` (optional, for ChatGPT analysis pack feature)
- `yt-dlp` (optional, for online video title fetching)

## Installation

```bash
pip install PyQt5 requests
```

## Usage

### Start the application

```bash
python app.py
```

Or use the provided batch files on Windows:

- `start.bat` — Launch in production mode
- `start_debug.bat` — Launch with a visible console window

### Environment variables

| Variable | Description |
|---|---|
| `WHISPER_SERVER_DIR` | Path to the [whisper-server](https://github.com/icenturyw/youtube-live-subtitles) directory |

### Basic workflow

1. **Add videos** — Click "添加视频" to select local files, or paste an online video URL
2. **Start processing** — Click "开始处理" to transcribe
3. **View subtitles** — Select a completed item to preview subtitles
4. **Export** — Right-click to export as SRT/VTT/TXT or generate a ChatGPT analysis pack

## Project structure

```
video_2_subtitles/
├── app.py              # Application entry point
├── main_window.py      # Main GUI window
├── api_client.py       # Whisper API client
├── local_whisper.py    # Local Whisper transcriber
├── history.py          # History manager
├── requirements.txt    # Python dependencies
├── start.bat           # Production launcher (Windows)
├── start_debug.bat     # Debug launcher (Windows)
└── output/             # Generated subtitles & exports
```

## License

MIT
