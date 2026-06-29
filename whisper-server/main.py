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
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import getproxies

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


SERVER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SERVER_DIR.parent
TEMP_DIR = SERVER_DIR / "temp"
CACHE_DIR = SERVER_DIR / "cache"
RAW_DIR = SERVER_DIR / "raw"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
for directory in (TEMP_DIR, CACHE_DIR, RAW_DIR):
    directory.mkdir(parents=True, exist_ok=True)

from subtitle_utils import (  # noqa: E402
    find_repeated_subtitle_runs,
    normalize_subtitle_timeline,
)

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


class TaskCancelled(RuntimeError):
    """Raised inside worker threads when a task has been cancelled."""


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


def _clean_language(value: Optional[str]) -> str:
    text = str(value or "auto").strip()
    if not text:
        return "auto"
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    return text or "auto"


def _task_id_from_url(url: str, language: str = "auto") -> str:
    lang = _clean_language(language)
    suffix = "" if lang == "auto" else f"_{_safe_name(lang)}"
    patterns = [
        r"(?:v=|/videos/|embed/|youtu\.be/|/v/|/e/|watch\?v=|&v=)([^#&\n/?]+)",
        r"bilibili\.com/video/([^/?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            base = re.sub(r"[^A-Za-z0-9_.-]", "_", match.group(1))[:72]
            return f"{base}{suffix}"[:80]
    return f"{hashlib.md5(url.encode('utf-8')).hexdigest()[:16]}{suffix}"[:80]


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
        if existing.get("status") == "cancelled" and status not in {"cancelled", "pending"}:
            return
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


def _is_task_cancelled(task_id: str) -> bool:
    with TASK_LOCK:
        return TASKS.get(task_id, {}).get("status") == "cancelled"


def _raise_if_cancelled(task_id: str) -> None:
    if _is_task_cancelled(task_id):
        raise TaskCancelled(f"Task {task_id} cancelled")


def _mark_task_cancelled(task_id: str, message: str = "任务已取消") -> None:
    _update_task(task_id, "cancelled", 0, message)


def _get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        return dict(task) if task else None


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or uuid.uuid4().hex


def _detect_system_proxy() -> str:
    proxy = os.environ.get("V2S_PROXY", "").strip()
    if proxy:
        return proxy
    system_proxies = getproxies()
    http_proxy = system_proxies.get("http") or system_proxies.get("https") or ""
    return http_proxy


def _youtube_cookie_file() -> Path:
    configured = os.environ.get("V2S_YOUTUBE_COOKIES", "").strip()
    return Path(configured).expanduser() if configured else SERVER_DIR / "cookies.txt"


def _has_youtube_cookies(cookie_file: Optional[Path] = None) -> bool:
    path = cookie_file or _youtube_cookie_file()
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return bool(re.search(r"(^|\n)\.?(youtube|google)\.com\t", text, re.I))


def _classify_ytdlp_error(stderr: str) -> str:
    if "LOGIN_REQUIRED" in stderr or "Sign in to confirm" in stderr or "Sign in" in stderr:
        cookie_file = _youtube_cookie_file()
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            if _has_youtube_cookies(cookie_file):
                return (
                    "YouTube 拒绝了当前 cookies.txt（可能已过期、未包含可用登录态或账号触发风控）。"
                    "请重新从已登录 YouTube 的浏览器导出 Netscape 格式 cookies。"
                )
            return (
                "当前 cookies.txt 不包含 YouTube/Google 登录 cookies。"
                "请在浏览器中登录 YouTube 后重新导出 Netscape 格式 cookies。"
            )
        return "需要登录 YouTube。请在浏览器中登录 YouTube 后导出 Netscape 格式 cookies.txt"
    if "Video unavailable" in stderr:
        return "视频不可用（可能已删除、私密或地区限制）"
    if "Private video" in stderr:
        return "该视频是私密视频"
    if "This video is not available" in stderr:
        return "视频不可用（可能已删除、私密或地区限制）"
    if "connect timeout" in stderr or "timed out" in stderr:
        return "连接 YouTube 超时，请检查网络连接或代理设置"
    if "HTTP Error 403" in stderr:
        return "YouTube 返回 403 禁止访问，可能是 cookies 过期或被风控"
    if "HTTP Error 429" in stderr:
        return "请求过于频繁，被 YouTube 限流，请稍后再试"
    return ""


def _extra_ytdlp_args() -> list[str]:
    """Common yt-dlp arguments to handle YouTube anti-bot measures."""
    cookie_file = _youtube_cookie_file()
    has_cookies = _has_youtube_cookies(cookie_file)
    player_client = "default" if has_cookies else "android,tv"
    args = [
        "--extractor-args", f"youtube:player_client={player_client}",
        "--remote-components", "ejs:github",
        "--js-runtimes", "node",
        "--extractor-retries", "3",
    ]
    proxy = _detect_system_proxy()
    if proxy:
        args += ["--proxy", proxy]
    if has_cookies:
        args += ["--cookies", str(cookie_file)]
    return args


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


def _run_ytdlp_once(cmd: list[str], run_kwargs: Dict[str, Any], task_id: Optional[str]) -> None:
    started = time.monotonic()
    process = subprocess.Popen(cmd, **run_kwargs)
    while True:
        if task_id and _is_task_cancelled(task_id):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise TaskCancelled(f"Task {task_id} cancelled")

        try:
            stdout, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() - started > 1800:
                process.kill()
                process.wait(timeout=5)
                raise subprocess.TimeoutExpired(cmd, 1800)

    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, cmd, output=stdout, stderr=stderr,
        )


def _run_ytdlp(cmd: list[str], task_id: Optional[str] = None) -> None:
    run_kwargs = {
        "cwd": str(SERVER_DIR),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    run_kwargs.update(_hidden_subprocess_kwargs())
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    run_kwargs["env"] = env
    try:
        _run_ytdlp_once(cmd, run_kwargs, task_id)
    except FileNotFoundError:
        cmd = [sys.executable, "-m", "yt_dlp", *cmd[1:]]
        _run_ytdlp_once(cmd, run_kwargs, task_id)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-1600:].strip()
        sys.stderr.write(f"[yt-dlp] command failed: {' '.join(cmd[:8])}...\n")
        sys.stderr.write(f"[yt-dlp] stderr tail:\n{detail}\n")
        raise


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be|youtube-nocookie\.com)", url or "", re.I))


def _caption_language_candidates(language: str) -> list[str]:
    lang = _clean_language(language).lower()
    if lang in {"auto", ""}:
        return []
    if lang in {"zh", "zh-cn", "zh-hans", "chinese"}:
        return ["zh-Hans", "zh-CN", "zh", "zh-Hant"]
    if lang in {"zh-tw", "zh-hk", "zh-hant"}:
        return ["zh-Hant", "zh-TW", "zh", "zh-Hans"]
    base = lang.split("-", 1)[0]
    return [language, lang, base]


def _youtube_info_options() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
    }
    proxy = _detect_system_proxy()
    if proxy:
        opts["proxy"] = proxy
    cookie_file = _youtube_cookie_file()
    if _has_youtube_cookies(cookie_file):
        opts["cookiefile"] = str(cookie_file)
    return opts


def _select_caption_track(info: dict[str, Any], language: str) -> tuple[Optional[dict[str, Any]], str]:
    candidates = _caption_language_candidates(language)
    if not candidates:
        return None, ""
    for bucket_name in ("subtitles", "automatic_captions"):
        bucket = info.get(bucket_name) or {}
        for lang in candidates:
            tracks = bucket.get(lang)
            if not tracks:
                continue
            for ext in ("json3", "vtt"):
                for track in tracks:
                    if track.get("ext") == ext and track.get("url"):
                        return track, lang
            for track in tracks:
                if track.get("url"):
                    return track, lang
    return None, ""


def _parse_youtube_json3(payload: dict[str, Any]) -> list[dict[str, Any]]:
    subtitles: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        if "tStartMs" not in event:
            continue
        text = "".join(
            str(seg.get("utf8", ""))
            for seg in event.get("segs") or []
        ).replace("\n", " ").strip()
        if not text:
            continue
        start = float(event.get("tStartMs") or 0) / 1000.0
        duration = float(event.get("dDurationMs") or 0) / 1000.0
        end = start + max(duration, 0.1)
        subtitles.append({
            "start": round(start, 2),
            "end": round(end + 0.1, 2),
            "text": text,
        })
    return subtitles


def _parse_youtube_vtt(text: str) -> list[dict[str, Any]]:
    subtitles: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", text or "")
    time_re = re.compile(
        r"(?:(\d+):)?(\d+):(\d+)\.(\d+)\s+-->\s+(?:(\d+):)?(\d+):(\d+)\.(\d+)"
    )

    def to_seconds(match: re.Match[str], offset: int) -> float:
        hours = int(match.group(offset) or 0)
        minutes = int(match.group(offset + 1))
        seconds = int(match.group(offset + 2))
        millis = int(match.group(offset + 3).ljust(3, "0")[:3])
        return hours * 3600 + minutes * 60 + seconds + millis / 1000

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0].startswith("WEBVTT"):
            continue
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if time_index < 0:
            continue
        match = time_re.search(lines[time_index])
        if not match:
            continue
        text_lines = [
            re.sub(r"<[^>]+>", "", line)
            for line in lines[time_index + 1:]
        ]
        caption = " ".join(line for line in text_lines if line).strip()
        if not caption:
            continue
        subtitles.append({
            "start": round(to_seconds(match, 1), 2),
            "end": round(to_seconds(match, 5) + 0.1, 2),
            "text": caption,
        })
    return subtitles


def _fetch_youtube_captions(video_url: str, language: str, task_id: str) -> tuple[list[dict[str, Any]], str]:
    if not _is_youtube_url(video_url):
        return [], ""
    import urllib.request
    import yt_dlp

    _update_task(task_id, "downloading", 6, "正在检查 YouTube 字幕...")
    with yt_dlp.YoutubeDL(_youtube_info_options()) as ydl:
        info = ydl.extract_info(video_url, download=False)
    track, caption_lang = _select_caption_track(info, language)
    if not track:
        return [], ""

    _update_task(task_id, "downloading", 10, f"正在下载 YouTube 字幕 ({caption_lang})...")
    req = urllib.request.Request(
        track["url"],
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Referer": video_url,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    if track.get("ext") == "json3":
        subtitles = _parse_youtube_json3(json.loads(raw))
    else:
        subtitles = _parse_youtube_vtt(raw)
    return subtitles, caption_lang if subtitles else ""


def _download_audio(video_url: str, task_id: str) -> Path:
    _raise_if_cancelled(task_id)
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
    cmd += _extra_ytdlp_args()
    cmd.append(video_url)

    try:
        _run_ytdlp(cmd, task_id)
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 yt-dlp，请运行 pip install -r requirements.txt") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-800:]
        classified = _classify_ytdlp_error(exc.stderr or exc.stdout or "")
        if classified:
            raise RuntimeError(classified) from exc
        raise RuntimeError(f"yt-dlp 下载音频失败: {detail}") from exc

    media_path = _existing_download(base_name, audio_only=True)
    if not media_path:
        raise RuntimeError("下载完成但未找到音频文件")
    return media_path


def _download_video(video_url: str, task_id: str, quality: str = "best") -> Path:
    _raise_if_cancelled(task_id)
    _update_task(task_id, "downloading", 8, "正在下载视频...")
    base_name = _safe_name(task_id)
    output_template = str(TEMP_DIR / f"{base_name}.%(ext)s")

    existing = _existing_download(base_name, video_only=True)
    if existing:
        return _ensure_client_video_alias(existing, video_url, task_id)

    common = ["yt-dlp", "--no-playlist", "-o", output_template]
    common += _extra_ytdlp_args()

    cmd = [
        *common,
        "-f",
        _video_format_selector(quality),
        "--merge-output-format",
        "mp4",
        video_url,
    ]

    try:
        _run_ytdlp(cmd, task_id)
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 yt-dlp，请运行 pip install -r requirements.txt") from exc
    except subprocess.CalledProcessError as first_exc:
        fallback_cmd = [*common, "-f", "b[ext=mp4]/best", video_url]
        try:
            _run_ytdlp(fallback_cmd, task_id)
        except subprocess.CalledProcessError as second_exc:
            stderr_combined = (second_exc.stderr or "") + (first_exc.stderr or "")
            detail = (second_exc.stderr or second_exc.stdout or first_exc.stderr or first_exc.stdout or str(second_exc))[-800:]
            classified = _classify_ytdlp_error(stderr_combined)
            if classified:
                raise RuntimeError(classified) from second_exc
            raise RuntimeError(f"yt-dlp 下载视频失败: {detail}") from second_exc

    media_path = _existing_download(base_name, video_only=False)
    if not media_path:
        raise RuntimeError("下载完成但未找到音视频文件")
    if media_path.suffix.lower() not in VIDEO_EXTS:
        raise RuntimeError(f"下载完成但未得到视频文件: {media_path.name}")
    return _ensure_client_video_alias(media_path, video_url, task_id)


def _download_media(video_url: str, task_id: str, mode: str = "video", quality: str = "best") -> Path:
    _raise_if_cancelled(task_id)
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


def _unload_model() -> bool:
    global MODEL, MODEL_KEY
    with MODEL_LOCK:
        had_model = MODEL is not None
        MODEL = None
        MODEL_KEY = None
    if had_model:
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    return had_model


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


def _initial_prompt_for_language(language: str) -> Optional[str]:
    """Return a safe Whisper prompt for explicit source languages only.

    The old implementation always used a Simplified Chinese prompt.  That is
    harmful for Korean/Japanese/English news: Whisper is nudged to hallucinate
    Chinese-looking text, which later gets dubbed by TTS.  For ``auto`` we avoid
    any prompt and let language detection do its job.
    """
    lang = str(language or "auto").strip().lower()
    if lang in {"zh", "zh-cn", "zh-hans", "cmn", "yue"}:
        return "以下是中文普通话音频，请准确转写为简体中文，不要添加原文没有的内容。"
    return None


def _transcribe_file(path: Path, task_id: str, language: str = "auto") -> tuple[list[dict[str, Any]], str]:
    _raise_if_cancelled(task_id)
    _update_task(task_id, "transcribing", 20, "正在加载 Whisper 模型...")
    model = _get_model()
    _raise_if_cancelled(task_id)
    _update_task(task_id, "transcribing", 30, "正在本地识别...")

    prompt = _initial_prompt_for_language(language)
    transcribe_kwargs = {
        "language": None if not language or language == "auto" else language,
        "beam_size": 1,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 700},
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.6,
    }
    if prompt:
        transcribe_kwargs["initial_prompt"] = prompt

    segments, info = model.transcribe(str(path), **transcribe_kwargs)

    detected_lang = getattr(info, "language", None) or language or "unknown"
    duration = float(getattr(info, "duration", 0) or 0)
    subtitles: list[dict[str, Any]] = []

    for segment in segments:
        _raise_if_cancelled(task_id)
        text = (getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(segment, "start", 0) or 0)
        end = float(getattr(segment, "end", start) or start)
        split_parts = _split_text(text)
        if len(split_parts) <= 1:
            subtitles.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        else:
            total_chars = sum(len(p) for p in split_parts) or 1
            cursor = start
            seg_duration = max(0.1, end - start)
            for part in split_parts:
                part_duration = seg_duration * (len(part) / total_chars)
                part_end = cursor + part_duration
                subtitles.append({"start": round(cursor, 3), "end": round(part_end, 3), "text": part})
                cursor = part_end
        subtitles = normalize_subtitle_timeline(subtitles)
        if duration > 0:
            progress = min(95, 30 + int((end / duration) * 65))
            if progress % 5 == 0:
                _update_task(task_id, "transcribing", progress, f"正在识别 {int(end)}s / {int(duration)}s...", subtitles=subtitles, detected_language=detected_lang)

    subtitles = normalize_subtitle_timeline(subtitles)
    repeated_runs = find_repeated_subtitle_runs(subtitles)
    if repeated_runs:
        logger.warning("Suspicious repeated subtitle runs detected: %s", repeated_runs[:5])

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
        language = _clean_language(language)
        download_mode = _clean_download_mode(download_mode)
        download_quality = _clean_download_quality(download_quality)
        _raise_if_cancelled(task_id)
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
        _raise_if_cancelled(task_id)
        if video_url:
            media_path = _download_media(video_url, task_id, download_mode, download_quality)
        _raise_if_cancelled(task_id)
        if not media_path or not media_path.exists():
            raise RuntimeError("找不到可转写的音视频文件")
        if media_path.suffix.lower() not in MEDIA_EXTS:
            raise RuntimeError(
                f"不支持的文件格式 '{media_path.suffix}'，仅支持音视频文件: {', '.join(sorted(MEDIA_EXTS))}"
            )

        subtitles: list[dict[str, Any]] = []
        detected_lang = ""
        caption_error: Optional[Exception] = None
        if video_url:
            try:
                _raise_if_cancelled(task_id)
                subtitles, detected_lang = _fetch_youtube_captions(video_url, language, task_id)
            except Exception as exc:
                if isinstance(exc, TaskCancelled):
                    raise
                caption_error = exc
                subtitles, detected_lang = [], ""
        if not subtitles:
            if caption_error is not None and _caption_language_candidates(language):
                raise RuntimeError(
                    f"YouTube {language} 字幕下载失败，未回退为其他语言字幕: {caption_error}"
                ) from caption_error
            _raise_if_cancelled(task_id)
            subtitles, detected_lang = _transcribe_file(media_path, task_id, language)
        _raise_if_cancelled(task_id)
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
        _raise_if_cancelled(task_id)
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
    except TaskCancelled:
        _mark_task_cancelled(task_id)
    except Exception as exc:
        if _is_task_cancelled(task_id):
            _mark_task_cancelled(task_id)
        else:
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
        "model_loaded": MODEL is not None,
        "download_modes": sorted(SUPPORTED_DOWNLOAD_MODES),
    }


@app.post("/model/unload")
@app.post("/models/unload")
def unload_model(auth: str = Depends(verify_api_key)):
    had_model = _unload_model()
    return {"status": "unloaded", "had_model": had_model}


@app.post("/transcribe")
def transcribe(request: TranscribeRequest, auth: str = Depends(verify_api_key)):
    if not request.video_url:
        raise HTTPException(status_code=400, detail="video_url is required")
    language = _clean_language(request.language)
    task_id = _task_id_from_url(request.video_url, language)
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
    _start_background(task_id, None, request.video_url, language, download_mode, download_quality, keep_video)
    return {"task_id": task_id, "status": "pending"}


def _raise_if_unsupported_media(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in MEDIA_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 {suffix}，仅支持音视频文件: {', '.join(sorted(MEDIA_EXTS))}",
        )


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
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{suffix}'，仅支持音视频文件: {', '.join(sorted(MEDIA_EXTS))}",
        )
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


@app.post("/cancel/{task_id}")
@app.post("/task/{task_id}/cancel")
@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, auth: str = Depends(verify_api_key)):
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task.get("status") in {"completed", "error", "cancelled"}:
        return {"task_id": task_id, "status": task.get("status"), "message": task.get("message", "")}
    _mark_task_cancelled(task_id, "取消请求已发送")
    return {"task_id": task_id, "status": "cancelled", "message": "取消请求已发送"}


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
