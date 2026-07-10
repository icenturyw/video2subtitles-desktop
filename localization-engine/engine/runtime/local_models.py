"""Adapters that lease models hosted by local loopback sidecars."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from .model_resources import ModelDefinition, ModelResourcePolicy


QWEN_MODEL_VRAM_MB = {
    "0.6B": 2048,
    "1.7B": 4096,
}
QWEN_DEFAULT_MODEL_BY_MODE = {
    "auto": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "custom_voice": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "voice_clone": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "voice_design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}


def qwen3_tts_definition(options: dict[str, Any], *, immediate: bool = False) -> ModelDefinition:
    mode = str(options.get("qwen_mode") or options.get("mode") or "auto").lower()
    model_id = str(
        options.get("qwen_model")
        or options.get("model")
        or QWEN_DEFAULT_MODEL_BY_MODE.get(mode, QWEN_DEFAULT_MODEL_BY_MODE["auto"])
    )
    device = str(options.get("device") or "auto")
    minimum_vram = int(options.get("min_vram_mb") or _qwen_vram(model_id))
    base_url = str(options.get("service_url") or "http://127.0.0.1:8767").rstrip("/")

    def load() -> dict:
        payload = {"model_id": model_id}
        if device != "auto":
            payload["device"] = device
        data = _json_request(base_url, "/models/load", payload, timeout=1800)
        return {"service": "qwen3-tts", "model_id": model_id, "response": data}

    def unload(_value: Any) -> None:
        _json_request(base_url, "/models/unload", {}, timeout=60)

    return ModelDefinition(
        model_id=f"qwen3-tts:{model_id}",
        kind="tts",
        loader=load,
        unloader=unload,
        device=device,
        estimated_vram_mb=minimum_vram if device.lower().startswith("cuda") else 0,
        policy=ModelResourcePolicy.IMMEDIATE if immediate else ModelResourcePolicy.IDLE,
        idle_timeout_seconds=float(options.get("idle_timeout_seconds") or 120),
        load_timeout_seconds=float(options.get("load_timeout_seconds") or 1800),
        resource_group="qwen3-tts-sidecar",
        metadata={"provider": "qwen3-tts", "remote_model_id": model_id},
    )


def whisper_definition(options: dict[str, Any]) -> ModelDefinition:
    model_id = str(options.get("model") or options.get("model_id") or "base")
    base_url = str(options.get("service_url") or "http://127.0.0.1:8765").rstrip("/")
    device = str(options.get("device") or "auto")

    def load() -> dict:
        data = _json_request(base_url, "/models/load", {"model_id": model_id}, timeout=900)
        return {"service": "whisper", "model_id": model_id, "response": data}

    def unload(_value: Any) -> None:
        _json_request(base_url, "/models/unload", {}, timeout=60)

    return ModelDefinition(
        model_id=f"whisper:{model_id}",
        kind="transcription",
        loader=load,
        unloader=unload,
        device=device,
        estimated_vram_mb=int(options.get("min_vram_mb") or 0),
        policy=ModelResourcePolicy(str(options.get("unload_policy") or "idle")),
        idle_timeout_seconds=float(options.get("idle_timeout_seconds") or 120),
        load_timeout_seconds=float(options.get("load_timeout_seconds") or 900),
        resource_group="whisper-sidecar",
        metadata={"provider": "faster-whisper", "remote_model_id": model_id},
    )


def local_translation_definition(
    model_id: str,
    loader,
    unloader,
    *,
    device: str = "auto",
    estimated_vram_mb: int = 0,
    policy: ModelResourcePolicy = ModelResourcePolicy.IDLE,
) -> ModelDefinition:
    """Create a definition for a future/local translation provider.

    The current project has no local translation provider. This adapter keeps
    lifecycle integration provider-neutral without pretending cloud APIs own a
    local model.
    """
    return ModelDefinition(
        model_id=f"translation:{model_id}",
        kind="translation",
        loader=loader,
        unloader=unloader,
        device=device,
        estimated_vram_mb=estimated_vram_mb,
        policy=policy,
    )


def _qwen_vram(model_id: str) -> int:
    return next((value for marker, value in QWEN_MODEL_VRAM_MB.items() if marker in model_id), 2048)


def _json_request(base_url: str, path: str, payload: dict, *, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}
