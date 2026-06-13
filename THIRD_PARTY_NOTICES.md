# Third-Party Open Source Notices

This file lists third-party open source software used in Video2Subtitles.

---

## VideoLingo

- **Source**: https://github.com/Huanshere/VideoLingo
- **License**: Apache-2.0
- **Usage**: Referenced for subtitle segmentation, translation pipeline design,
  and subtitle alignment algorithms. Modified/adapted code (if any) is clearly
  marked and subject to Apache-2.0.
- **License file**: `licenses/VideoLingo-Apache-2.0.txt`
- **Note**: This project does not claim official endorsement by VideoLingo.

---

## Qwen3-TTS

- **Source**: https://github.com/QwenLM/Qwen3-TTS
- **License**: Apache-2.0
- **Usage**: Integrated as an optional local TTS sidecar for dubbing. The
  `qwen-tts` Python package is installed in an isolated virtual environment,
  not bundled into the main application. Model weights are downloaded on demand
  and are not included in this repository.
- **License file**: `licenses/Qwen3-TTS-Apache-2.0.txt`

---

## faster-whisper

- **Source**: https://github.com/SYSTRAN/faster-whisper
- **License**: MIT
- **Usage**: Used as the primary speech recognition engine.

---

## PyQt5

- **Source**: https://www.riverbankcomputing.com/software/pyqt/
- **License**: GPL v3 / Commercial
- **Usage**: Desktop GUI framework.

---

## FastAPI / Uvicorn

- **Source**: https://github.com/tiangolo/fastapi / https://github.com/encode/uvicorn
- **License**: MIT / BSD-3-Clause
- **Usage**: Local sidecar HTTP services (Whisper server, Localization engine).

---

## yt-dlp

- **Source**: https://github.com/yt-dlp/yt-dlp
- **License**: Unlicense (public domain)
- **Usage**: Online video downloading.

---

## FFmpeg

- **Source**: https://ffmpeg.org/
- **License**: LGPL-2.1 / GPL-2.0
- **Usage**: Video/audio processing, subtitle burning, and media muxing.

---

## Edge-TTS

- **Source**: https://github.com/rany2/edge-tts
- **License**: GPL-3.0
- **Usage**: Lightweight TTS fallback when Qwen3-TTS is not available.

---

## Other Dependencies

See `requirements.txt`, `whisper-server/requirements.txt`,
`localization-engine/requirements-base.txt`, and
`qwen3-tts-engine/requirements.txt` for the complete list of Python
dependencies and their respective licenses.
