"""Bundled local Whisper sidecar service for Video2Subtitles.

This intentionally implements only the API surface used by the desktop client:
/health, /transcribe, /upload, /status/{task_id}, and /task/{task_id}.

The service shares the same faster-whisper model settings as the desktop local
fallback via WHISPER_MODEL_DIR / WHISPER_MODEL_PATH / MODEL_SIZE.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


SERVER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SERVER_DIR.parent
TEMP_DIR = SERVER_DIR / "temp"
CACHE_DIR = SERVER_DIR / "cache"
RAW_DIR = SERVER_DIR / "raw"
for directory in (TEMP_DIR, CACHE_DIR, RAW_DIR):
    directory.mkdir(parents=True, exist_ok=True)

API_AUTH_KEY = os.environ.get("API_AUTH_KEY", "")
MODEL_LOCK = threading.Lock()
MODEL = None
MODEL_KEY = None
TASKS: Dict[str, Dict[str, Any]] = {}
TASK_LOCK = threading.Lock()
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".wav", ".aac", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
SUPPORTED_DOWNLOAD_MODES = {"video", "transcribe_only", "audio"}
SUPPORTED_DOWNLOAD_QUALITIES = {"best", "720p", "480p"}


class TranscribeRequest(BaseModel):
    video_url: Optional[str] = None
    language: str = "auto"
    service: str = "local"
    api_key: Optional[str] = None
    target_lang: Optional[str] = None
    domain: str = "general"
    engine: str = "whisper"
    llm_correction: bool = False
    download_mode: str = "video"
    download_quality: str = "best"
    keep_video: bool = True


app = FastAPI(title="Video2Subtitles Local Whisper Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if API_AUTH_KEY and x_api_key != API_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key


def _hidden_subprocess_kwargs() -> Dict[str, Any]:
    """Hide console windows created by CLI helpers on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _now() -> float:
    return time.time()


def _clean_download_mode(mode: str) -> str:
    mode = (mode or "video").strip()
    return mode if mode in SUPPORTED_DOWNLOAD_MODES else "video"


def _clean_download_quality(quality: str) -> str:
    quality = (quality or "best").strip()
    return quality if quality in SUPPORTED_DOWNLOAD_QUALITIES else "best"


def _video_format_selector(quality: str) -> str:
    quality = _clean_download_quality(quality)
    if quality == "720p":
        return "bv*[height<=720]+ba/b[height<=720][ext=mp4]/best[height<=720]/best"
    if quality == "480p":
        return "bv*[height<=480]+ba/b[height<=480][ext=mp4]/best[height<=480]/best"
    return "bv*+ba/b[ext=mp4]/best"


def _task_id_from_url(url: str) -> str:
    patterns = [
        r"(?:v=|/videos/|embed/|youtu\.be/|/v/|/e/|watch\?v=|&v=)([^#&\n/?]+)",
        r"bilibili\.com/video/([^/?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return re.sub(r"[^A-Za-z0-9_.-]", "_", match.group(1))[:80]
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _client_video_id_from_url(url: str) -> str:
    """Match the desktop client's legacy output-copy ID fallback."""
    match = re.search(
        r"(?:v=|/videos/|embed/|youtu\.be/|/v/|/e/|watch\?v=|&v=)([^#&\n]*)",
        url,
    )
    if match:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", match.group(1))[:80]
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:11]


def _update_task(task_id: str, status: str, progress: int, message: str, **extra: Any) -> None:
    with TASK_LOCK:
        existing = TASKS.get(task_id, {})
        payload = {
            **existing,
            "task_id": task_id,
            "status": status,
            "progress": int(progress),
            "message": message,
            "updated_at": _now(),
        }
        payload.update(extra)
        TASKS[task_id] = payload


def _get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        return dict(task) if task else None


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or uuid.uuid4().hex


def _cookie_args() -> list[str]:
    cookie_file = SERVER_DIR / "cookies.txt"
    if cookie_file.exists() and cookie_file.stat().st_size > 0:
        return ["--cookies", str(cookie_file)]
    return []


def _existing_download(base_name: str, *, video_only: bool = False, audio_only: bool = False) -> Optional[Path]:
    candidates = [p for p in TEMP_DIR.glob(f"{base_name}.*") if p.is_file() and p.stat().st_size > 0]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not audio_only:
        for item in candidates:
            if item.suffix.lower() in VIDEO_EXTS:
                return item
    if video_only:
        return None
    for item in candidates:
        suffix = item.suffix.lower()
        if audio_only and suffix in AUDIO_EXTS:
            return item
        if not audio_only and suffix in MEDIA_EXTS:
            return item
    return None


def _ensure_client_video_alias(media_path: Path, video_url: str, task_id: str) -> Path:
    if media_path.suffix.lower() not in VIDEO_EXTS:
        return media_path
    aliases = {
        _safe_name(task_id),
        _safe_name(_client_video_id_from_url(video_url)),
    }
    for alias in aliases:
        alias_path = TEMP_DIR / f"{alias}{media_path.suffix}"
        if alias_path.resolve() == media_path.resolve():
            continue
        try:
            if not alias_path.exists() or alias_path.stat().st_size != media_path.stat().st_size:
                shutil.copy2(str(media_path), str(alias_path))
        except Exception:
            pass
    return media_path


def _run_ytdlp(cmd: list[str]) -> None:
    run_kwargs = {
        "cwd": str(SERVER_DIR),
        "capture_output": True,
        "text": True,
        "check": True,
        "timeout": 1800,
    }
    run_kwargs.update(_hidden_subprocess_kwargs())
    subprocess.run(cmd, **run_kwargs)


def _download_audio(video_url: str, task_id: str) -> Path:
    _update_task(task_id, "downloading", 8, "正在下载音频...")
    base_name = _safe_name(task_id)
    output_template = str(TEMP_DIR / f"{base_name}.%(ext)s")

    existing = _existing_download(base_name, audio_only=True)
    if existing:
        return existing

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "128K",
        "-o",
        output_template,
    ]
    cmd += _cookie_args()
    cmd.append(video_url)

    try:
        _run_ytdlp(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 yt-dlp，请运行 pip install -r requirements.txt") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-800:]
        raise RuntimeError(f"yt-dlp 下载音频失败: {detail}") from exc

    media_path = _existing_download(base_name, audio_only=True)
    if not media_path:
        raise RuntimeError("下载完成但未找到音频文件")
    return media_path


def _download_video(video_url: str, task_id: str, quality: str = "best") -> Path:
    _update_task(task_id, "downloading", 8, "正在下载视频...")
    base_name = _safe_name(task_id)
    output_template = str(TEMP_DIR / f"{base_name}.%(ext)s")

    existing = _existing_download(base_name, video_only=True)
    if existing:
        return _ensure_client_video_alias(existing, video_url, task_id)

    common = ["yt-dlp", "--no-playlist", "-o", output_template]
    common += _cookie_args()

    cmd = [
        *common,
        "-f",
        _video_format_selector(quality),
        "--merge-output-format",
        "mp4",
        video_url,
    ]

    try:
        _run_ytdlp(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 yt-dlp，请运行 pip install -r requirements.txt") from exc
    except subprocess.CalledProcessError as first_exc:
        fallback_cmd = [*common, "-f", "b[ext=mp4]/best", video_url]
        try:
            _run_ytdlp(fallback_cmd)
        except subprocess.CalledProcessError as second_exc:
            detail = (second_exc.stderr or second_exc.stdout or first_exc.stderr or first_exc.stdout or str(second_exc))[-800:]
            raise RuntimeError(f"yt-dlp 下载视频失败: {detail}") from second_exc

    media_path = _existing_download(base_name, video_only=False)
    if not media_path:
        raise RuntimeError("下载完成但未找到音视频文件")
    if media_path.suffix.lower() not in VIDEO_EXTS:
        raise RuntimeError(f"下载完成但未得到视频文件: {media_path.name}")
    return _ensure_client_video_alias(media_path, video_url, task_id)


def _download_media(video_url: str, task_id: str, mode: str = "video", quality: str = "best") -> Path:
    mode = _clean_download_mode(mode)
    if mode == "audio":
        return _download_audio(video_url, task_id)
    return _download_video(video_url, task_id, quality)


def _cleanup_transcribe_only_media(media_path: Path, video_url: Optional[str], task_id: str, mode: str, keep_video: bool) -> None:
    if _clean_download_mode(mode) != "transcribe_only" or keep_video:
        return
    if media_path.suffix.lower() not in VIDEO_EXTS:
        return
    names = {
        media_path.name,
        f"{_safe_name(task_id)}{media_path.suffix}",
    }
    if video_url:
        names.add(f"{_safe_name(_client_video_id_from_url(video_url))}{media_path.suffix}")
    for name in names:
        try:
            (TEMP_DIR / name).unlink(missing_ok=True)
        except Exception:
            pass


def _model_config() -> tuple[str, str, str, str]:
    model_path = os.environ.get("WHISPER_MODEL_PATH", "").strip()
    model_size = os.environ.get("MODEL_SIZE", "base").strip() or "base"
    model_id = model_path or model_size
    model_dir = os.environ.get("WHISPER_MODEL_DIR", "").strip() or str(PROJECT_DIR / "models")
    device = os.environ.get("DEVICE", "cpu")
    compute_type = os.environ.get("COMPUTE_TYPE", "int8")
    return model_id, model_dir, device, compute_type


def _get_model():
    global MODEL, MODEL_KEY
    from faster_whisper import WhisperModel

    model_id, model_dir, device, compute_type = _model_config()
    key = (model_id, model_dir, device, compute_type)
    with MODEL_LOCK:
        if MODEL is not None and MODEL_KEY == key:
            return MODEL
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        kwargs = {"device": device, "compute_type": compute_type, "download_root": model_dir}
        MODEL = WhisperModel(model_id, **kwargs)
        MODEL_KEY = key
        return MODEL


def _split_text(text: str, max_len: int = 32) -> list[str]:
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"([。！？；.!?;，,、])", text)
    chunks: list[str] = []
    current = ""
    for i in range(0, len(parts), 2):
        piece = parts[i]
        if i + 1 < len(parts):
            piece += parts[i + 1]
        if len(current) + len(piece) > max_len and current:
            chunks.append(current.strip())
            current = piece
        else:
            current += piece
    if current.strip():
        chunks.append(current.strip())
    final: list[str] = []
    for chunk in chunks or [text]:
        while len(chunk) > max_len:
            final.append(chunk[:max_len].strip())
            chunk = chunk[max_len:]
        if chunk.strip():
            final.append(chunk.strip())
    return final or [text]


def _transcribe_file(path: Path, task_id: str, language: str = "auto") -> tuple[list[dict[str, Any]], str]:
    _update_task(task_id, "transcribing", 20, "正在加载 Whisper 模型...")
    model = _get_model()
    _update_task(task_id, "transcribing", 30, "正在本地识别...")

    segments, info = model.transcribe(
        str(path),
        language=None if not language or language == "auto" else language,
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 1000},
        initial_prompt="以下是普通话的句子，请用简体中文。",
    )

    detected_lang = getattr(info, "language", None) or language or "unknown"
    duration = float(getattr(info, "duration", 0) or 0)
    subtitles: list[dict[str, Any]] = []

    for segment in segments:
        text = (getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(segment, "start", 0) or 0)
        end = float(getattr(segment, "end", start) or start)
        split_parts = _split_text(text)
        if len(split_parts) <= 1:
            subtitles.append({"start": round(start, 2), "end": round(end + 0.1, 2), "text": text})
        else:
            total_chars = sum(len(p) for p in split_parts) or 1
            cursor = start
            seg_duration = max(0.1, end - start)
            for part in split_parts:
                part_duration = seg_duration * (len(part) / total_chars)
                part_end = cursor + part_duration
                subtitles.append({"start": round(cursor, 2), "end": round(part_end + 0.1, 2), "text": part})
                cursor = part_end
        if duration > 0:
            progress = min(95, 30 + int((end / duration) * 65))
            if progress % 5 == 0:
                _update_task(task_id, "transcribing", progress, f"正在识别 {int(end)}s / {int(duration)}s...", subtitles=subtitles, detected_language=detected_lang)

    return subtitles, detected_lang


def _cache_path(task_id: str) -> Path:
    return CACHE_DIR / f"{_safe_name(task_id)}.json"


def _load_cache(task_id: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(task_id: str, payload: Dict[str, Any]) -> None:
    _cache_path(task_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _process_task(
    task_id: str,
    source_path: Optional[Path],
    video_url: Optional[str],
    language: str,
    download_mode: str = "video",
    download_quality: str = "best",
    keep_video: bool = True,
) -> None:
    try:
        download_mode = _clean_download_mode(download_mode)
        download_quality = _clean_download_quality(download_quality)
        cached = _load_cache(task_id)
        if cached and cached.get("subtitles"):
            media_path = None
            if video_url:
                if download_mode == "audio":
                    media_path = _existing_download(_safe_name(task_id), audio_only=True)
                else:
                    media_path = _existing_download(_safe_name(task_id), video_only=True)
                if not media_path:
                    media_path = _download_media(video_url, task_id, download_mode, download_quality)
                if media_path and media_path.suffix.lower() in VIDEO_EXTS:
                    _ensure_client_video_alias(media_path, video_url, task_id)
            _update_task(
                task_id,
                "completed",
                100,
                "从缓存加载",
                subtitles=cached.get("subtitles", []),
                detected_language=cached.get("detected_language", cached.get("language", "unknown")),
                media_file=str(media_path) if media_path else cached.get("media_file", ""),
                download_mode=download_mode,
                download_quality=download_quality,
            )
            return

        media_path = source_path
        if video_url:
            media_path = _download_media(video_url, task_id, download_mode, download_quality)
        if not media_path or not media_path.exists():
            raise RuntimeError("找不到可转写的音视频文件")

        subtitles, detected_lang = _transcribe_file(media_path, task_id, language)
        if not subtitles:
            raise RuntimeError("未识别到有效语音内容")

        result = {
            "task_id": task_id,
            "status": "completed",
            "progress": 100,
            "message": "完成",
            "subtitles": subtitles,
            "detected_language": detected_lang,
            "language": detected_lang,
            "media_file": str(media_path),
            "download_mode": download_mode,
            "download_quality": download_quality,
            "updated_at": _now(),
        }
        _save_cache(task_id, result)
        _update_task(
            task_id,
            "completed",
            100,
            "完成",
            subtitles=subtitles,
            detected_language=detected_lang,
            language=detected_lang,
            media_file=str(media_path),
            download_mode=download_mode,
            download_quality=download_quality,
        )
        if video_url:
            _cleanup_transcribe_only_media(media_path, video_url, task_id, download_mode, keep_video)
    except Exception as exc:
        _update_task(task_id, "error", 0, str(exc)[:1000], subtitles=[])


def _start_background(
    task_id: str,
    source_path: Optional[Path],
    video_url: Optional[str],
    language: str,
    download_mode: str = "video",
    download_quality: str = "best",
    keep_video: bool = True,
) -> None:
    thread = threading.Thread(
        target=_process_task,
        args=(task_id, source_path, video_url, language, download_mode, download_quality, keep_video),
        daemon=True,
    )
    thread.start()


@app.get("/")
@app.get("/health")
def health():
    model_id, model_dir, device, compute_type = _model_config()
    return {
        "status": "ok",
        "service": "video2subtitles-local-whisper",
        "local_whisper": True,
        "model": model_id,
        "model_dir": model_dir,
        "device": device,
        "compute_type": compute_type,
        "download_modes": sorted(SUPPORTED_DOWNLOAD_MODES),
    }


@app.post("/transcribe")
def transcribe(request: TranscribeRequest, auth: str = Depends(verify_api_key)):
    if not request.video_url:
        raise HTTPException(status_code=400, detail="video_url is required")
    task_id = _task_id_from_url(request.video_url)
    existing = _get_task(task_id)
    if existing and existing.get("status") in {"pending", "downloading", "transcribing", "completed"}:
        return existing
    download_mode = _clean_download_mode(request.download_mode)
    download_quality = _clean_download_quality(request.download_quality)
    keep_video = bool(request.keep_video) or download_mode == "video"
    _update_task(
        task_id,
        "pending",
        0,
        "等待处理...",
        video_url=request.video_url,
        subtitles=[],
        download_mode=download_mode,
        download_quality=download_quality,
    )
    _start_background(task_id, None, request.video_url, request.language, download_mode, download_quality, keep_video)
    return {"task_id": task_id, "status": "pending"}


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    service: str = Form("local"),
    language: str = Form("auto"),
    domain: str = Form("general"),
    api_key: Optional[str] = Form(None),
    target_lang: Optional[str] = Form(None),
    auth: str = Depends(verify_api_key),
):
    task_id = uuid.uuid4().hex
    filename = _safe_name(file.filename or f"upload_{task_id}")
    suffix = Path(filename).suffix or ".bin"
    upload_path = TEMP_DIR / f"upload_{task_id}{suffix}"
    with upload_path.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    _update_task(task_id, "pending", 0, "文件已上传，等待处理...", local_file=str(upload_path), subtitles=[])
    _start_background(task_id, upload_path, None, language)
    return {"task_id": task_id, "status": "pending"}


@app.get("/status/{task_id}")
@app.get("/task/{task_id}")
def get_task_status(task_id: str):
    task = _get_task(task_id)
    if task:
        return task
    cached = _load_cache(task_id)
    if cached:
        return cached
    return {"task_id": task_id, "status": "not_found", "message": "No existing data for this task", "progress": 0}


@app.delete("/cache/{task_id}")
def delete_cache(task_id: str, auth: str = Depends(verify_api_key)):
    removed = []
    with TASK_LOCK:
        if task_id in TASKS:
            del TASKS[task_id]
            removed.append("memory")
    path = _cache_path(task_id)
    if path.exists():
        path.unlink()
        removed.append("cache")
    return {"task_id": task_id, "deleted_items": removed, "success": bool(removed)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
