"""Output metadata helpers for Video2Subtitles."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


MANIFEST_NAME = "manifest.json"


def _rel(path: Optional[Path], base_dir: Path) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(base_dir.resolve()))
    except Exception:
        return str(path)


def detect_video_file(output_dir: Path) -> str:
    video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv"}
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return ""
    for item in sorted(output_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in video_exts and "_proxy_" not in item.stem:
            return item.name
    return ""


def write_manifest(
    output_dir: Path,
    *,
    source: str,
    title: str = "",
    is_url: bool = False,
    language: str = "unknown",
    subtitles: Optional[Iterable[Dict[str, Any]]] = None,
    srt_path: Optional[Path] = None,
    vtt_path: Optional[Path] = None,
    txt_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
    download_mode: str = "",
    download_quality: str = "",
    chatgpt_package_dir: Optional[Path] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    subtitles_list = list(subtitles or [])
    manifest_path = output_dir / MANIFEST_NAME
    existing: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    if not video_path:
        detected = detect_video_file(output_dir)
        video_path = output_dir / detected if detected else None

    payload: Dict[str, Any] = {
        **existing,
        "title": title or existing.get("title", ""),
        "source": source,
        "is_url": bool(is_url),
        "language": language,
        "subtitle_count": len(subtitles_list) if subtitles_list else existing.get("subtitle_count", 0),
        "created_at": existing.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "video_file": _rel(video_path, output_dir),
        "srt_file": _rel(srt_path, output_dir),
        "vtt_file": _rel(vtt_path, output_dir),
        "txt_file": _rel(txt_path, output_dir),
        "download_mode": download_mode or existing.get("download_mode", ""),
        "download_quality": download_quality or existing.get("download_quality", ""),
        "chatgpt_package_dir": _rel(chatgpt_package_dir, output_dir) if chatgpt_package_dir else existing.get("chatgpt_package_dir", ""),
    }
    if extra:
        payload.update(extra)

    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def update_chatgpt_package(output_dir: Path, package_dir: Path) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    package_dir = Path(package_dir)
    manifest_path = output_dir / MANIFEST_NAME
    payload: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload["chatgpt_package_dir"] = _rel(package_dir, output_dir)
    payload["chatgpt_light_zip"] = _rel(package_dir / "chatgpt_upload_light.zip", output_dir)
    payload["chatgpt_full_zip"] = _rel(package_dir / "chatgpt_upload_full.zip", output_dir)
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_manifest(output_dir: Path) -> Dict[str, Any]:
    manifest_path = Path(output_dir) / MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
