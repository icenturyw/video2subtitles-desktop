"""Output metadata helpers for Video2Subtitles.

Supports both v1 (legacy) and v2 (localization-aware) manifest formats.
v1 fields are preserved when writing v2, and v1 manifests load transparently.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION_V2 = 2


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


# ---------------------------------------------------------------------------
# Manifest v2 functions
# ---------------------------------------------------------------------------

def get_manifest_version(data: Dict[str, Any]) -> int:
    """Return the schema version of a manifest dict (1 if not specified)."""
    return int(data.get("schema_version", 1))


def write_manifest_v2(
    output_dir: Path,
    *,
    job_id: str,
    title: str = "",
    source: str = "",
    source_type: str = "local",
    mode: str = "subtitle",
    status: str = "pending",
    current_stage: str = "prepare",
    source_language: str = "auto",
    detected_language: str = "",
    target_language: Optional[str] = None,
    subtitle_mode: str = "bilingual",
    burn_subtitles: bool = False,
    dubbing_enabled: bool = False,
    asr_engine: str = "faster-whisper",
    translation_provider: str = "",
    translation_model: str = "",
    tts_provider: str = "",
    subtitle_style_preset: str = "default",
    artifacts: Optional[List[Dict[str, Any]]] = None,
    checkpoints: Optional[Dict[str, bool]] = None,
    # v1 backward-compat fields
    srt_path: Optional[Path] = None,
    vtt_path: Optional[Path] = None,
    txt_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
    language: str = "unknown",
    subtitle_count: int = 0,
    download_mode: str = "",
    download_quality: str = "",
    chatgpt_package_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write a v2 manifest, preserving v1 fields for backward compatibility."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
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

    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    payload: Dict[str, Any] = {
        # v2 fields
        "schema_version": SCHEMA_VERSION_V2,
        "job_id": job_id,
        "title": title or existing.get("title", ""),
        "source": {
            "input": source or existing.get("source", ""),
            "type": source_type,
            "video_file": _rel(video_path, output_dir),
            "sha256": "",
            "duration_seconds": 0,
        },
        "pipeline": {
            "mode": mode,
            "status": status,
            "current_stage": current_stage,
            "source_language": source_language,
            "detected_language": detected_language,
            "target_language": target_language or "",
            "subtitle_mode": subtitle_mode,
            "burn_subtitles": burn_subtitles,
            "dubbing_enabled": dubbing_enabled,
        },
        "settings": {
            "asr_engine": asr_engine,
            "translation_provider": translation_provider,
            "translation_model": translation_model,
            "tts_provider": tts_provider,
            "subtitle_style_preset": subtitle_style_preset,
        },
        "artifacts": artifacts if artifacts is not None else existing.get("artifacts", []),
        "checkpoints": checkpoints if checkpoints is not None else existing.get("checkpoints", {}),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        # v1 backward-compat fields
        "source": source or existing.get("source", ""),
        "is_url": source_type == "url",
        "language": language,
        "subtitle_count": subtitle_count or existing.get("subtitle_count", 0),
        "video_file": _rel(video_path, output_dir),
        "srt_file": _rel(srt_path, output_dir),
        "vtt_file": _rel(vtt_path, output_dir),
        "txt_file": _rel(txt_path, output_dir),
        "download_mode": download_mode or existing.get("download_mode", ""),
        "download_quality": download_quality or existing.get("download_quality", ""),
        "chatgpt_package_dir": _rel(chatgpt_package_dir, output_dir) if chatgpt_package_dir else existing.get("chatgpt_package_dir", ""),
    }

    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def load_manifest_v2(output_dir: Path) -> Dict[str, Any]:
    """Load a manifest and normalize to v2 structure.

    If the manifest is v1, it will be returned with v2 fields populated
    from the v1 data (non-destructive, does not modify the file).
    """
    data = load_manifest(output_dir)
    if not data:
        return {}

    version = get_manifest_version(data)
    if version >= SCHEMA_VERSION_V2:
        return data

    # Upgrade v1 data to v2 structure (in memory only)
    return _upgrade_v1_to_v2(data)


def _upgrade_v1_to_v2(v1: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a v1 manifest dict to v2 structure in memory."""
    source_input = v1.get("source", "")
    is_url = v1.get("is_url", False)

    # Build artifacts from v1 file references
    artifacts: List[Dict[str, Any]] = []
    for kind, key in [
        ("source_video", "video_file"),
        ("source_srt", "srt_file"),
    ]:
        path = v1.get(key, "")
        if path:
            artifacts.append({"kind": kind, "path": path, "language": v1.get("language", "")})

    v2: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V2,
        "job_id": "",
        "title": v1.get("title", ""),
        "source": {
            "input": source_input,
            "type": "url" if is_url else "local",
            "video_file": v1.get("video_file", ""),
            "sha256": "",
            "duration_seconds": 0,
        },
        "pipeline": {
            "mode": "subtitle",
            "status": "completed",
            "current_stage": "completed",
            "source_language": "auto",
            "detected_language": v1.get("language", ""),
            "target_language": "",
            "subtitle_mode": "source",
            "burn_subtitles": False,
            "dubbing_enabled": False,
        },
        "settings": {
            "asr_engine": "faster-whisper",
            "translation_provider": "",
            "translation_model": "",
            "tts_provider": "",
            "subtitle_style_preset": "default",
        },
        "artifacts": artifacts,
        "checkpoints": {"transcribe": True},
        "created_at": v1.get("created_at", ""),
        "updated_at": v1.get("updated_at", ""),
        # Preserve all v1 fields
        **v1,
    }
    return v2


def update_manifest_artifacts(
    output_dir: Path, artifacts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Update the artifacts array in a v2 manifest."""
    output_dir = Path(output_dir)
    manifest_path = output_dir / MANIFEST_NAME
    payload = load_manifest(output_dir)
    if not payload:
        return {}
    payload["artifacts"] = artifacts
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def update_manifest_checkpoints(
    output_dir: Path, checkpoints: Dict[str, bool]
) -> Dict[str, Any]:
    """Update the checkpoints in a v2 manifest."""
    output_dir = Path(output_dir)
    manifest_path = output_dir / MANIFEST_NAME
    payload = load_manifest(output_dir)
    if not payload:
        return {}
    existing_checkpoints = payload.get("checkpoints", {})
    existing_checkpoints.update(checkpoints)
    payload["checkpoints"] = existing_checkpoints
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload
