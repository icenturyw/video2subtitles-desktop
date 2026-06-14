from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from engine.cache import TTSCache
from engine.device import detect_device
from engine.model_manager import ModelManager
from engine.schemas import (
    HealthResponse, Capabilities, VoiceInfo,
    InstallModelRequest, LoadModelRequest,
    SynthesizeCustomVoiceRequest, SynthesizeVoiceCloneRequest,
    SynthesizeVoiceDesignRequest,
    VoiceClonePromptRequest, VoiceClonePromptResponse,
    TaskProgress, TaskStatus,
)
from engine.synthesis import Synthesizer
from engine import voice_clone, voice_design

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("qwen3-tts")

CACHE_DIR = Path(
    os.environ.get("QWEN3_TTS_CACHE", tempfile.gettempdir())
) / "qwen3_tts_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

cache = TTSCache(CACHE_DIR)
model_manager = ModelManager()
synthesizer = Synthesizer(cache=cache)
_tasks: Dict[str, TaskProgress] = {}

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Qwen3-TTS sidecar starting (version %s)", APP_VERSION)
    yield
    logger.info("Qwen3-TTS sidecar shutting down")
    model_manager.unload_model()


app = FastAPI(
    title="Video2Subtitles Qwen3-TTS",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health & Info
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    device_info = detect_device()
    loaded = model_manager.loaded_model_id
    caps = Capabilities()
    if loaded:
        from engine.model_manager import MODEL_TIERS
        info = MODEL_TIERS.get(loaded, {})
        cap_list = info.get("capabilities", [])
        caps.custom_voice = "custom_voice" in cap_list
        caps.voice_design = "voice_design" in cap_list
        caps.voice_clone = "voice_clone" in cap_list
    return HealthResponse(
        status="ok",
        device=device_info.get("device", "cpu"),
        dtype=device_info.get("dtype", "float32"),
        loaded_model=loaded,
        flash_attention=device_info.get("flash_attention", False),
        capabilities=caps,
    )


@app.get("/models")
def list_models():
    return {"models": model_manager.list_models()}


@app.get("/voices")
def list_voices(language: Optional[str] = None):
    speakers = model_manager.list_speakers()
    lang = model_manager.list_languages()
    voices = [VoiceInfo(name=s) for s in speakers]
    return {"voices": voices, "languages": lang}


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

@app.post("/models/install")
def install_model(req: InstallModelRequest):
    from huggingface_hub import snapshot_download
    try:
        cache_dir = req.cache_dir
        path = snapshot_download(
            repo_id=req.model_id,
            cache_dir=cache_dir,
            resume_download=True,
        )
        return {"status": "installed", "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/load")
def load_model(req: LoadModelRequest):
    ok, msg = model_manager.load_model(
        model_id=req.model_id,
        device=req.device,
        dtype=req.dtype,
        attn_implementation=req.attn_implementation,
        cache_dir=req.cache_dir,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "loaded", "model_id": req.model_id, "message": msg}


@app.post("/models/unload")
def unload_model():
    ok, msg = model_manager.unload_model()
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "unloaded", "message": msg}


@app.get("/models/loaded")
def get_loaded_model():
    return {"model_id": model_manager.loaded_model_id}


# ---------------------------------------------------------------------------
# Synthesis endpoints
# ---------------------------------------------------------------------------

@app.post("/synthesize/custom-voice")
def synthesize_custom_voice(req: SynthesizeCustomVoiceRequest):
    if not model_manager.is_loaded:
        raise HTTPException(status_code=400, detail="No model loaded")

    try:
        out_dir = CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{uuid.uuid4().hex[:16]}.wav"

        model_type = model_manager.model_type
        if model_type == "VoiceDesign":
            # VoiceDesign model uses generate_voice_design, not custom voice
            instruct = req.instruct or (
                f"A natural voice speaking {req.language or 'the given language'}"
            )
            duration, path = synthesizer.synthesize_voice_design(
                text=req.text,
                instruct=instruct,
                language=req.language,
                output_path=output_path,
                max_new_tokens=req.max_new_tokens,
                top_p=req.top_p,
                temperature=req.temperature,
                seed=req.seed,
            )
        else:
            duration, path = synthesizer.synthesize_custom_voice(
                text=req.text,
                speaker=req.speaker,
                language=req.language,
                instruct=req.instruct,
                output_path=output_path,
                max_new_tokens=req.max_new_tokens,
                top_p=req.top_p,
                temperature=req.temperature,
                seed=req.seed,
            )
        return FileResponse(
            str(path),
            media_type="audio/wav",
            filename=path.name,
            headers={
                "X-Duration": str(duration),
                "X-Sample-Rate": "24000",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/synthesize/voice-design")
def synthesize_voice_design(req: SynthesizeVoiceDesignRequest):
    if not model_manager.is_loaded:
        raise HTTPException(status_code=400, detail="No model loaded")

    try:
        out_dir = CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{uuid.uuid4().hex[:16]}.wav"
        duration, path = synthesizer.synthesize_voice_design(
            text=req.text,
            instruct=req.instruct,
            language=req.language,
            output_path=output_path,
            max_new_tokens=req.max_new_tokens,
            top_p=req.top_p,
            temperature=req.temperature,
            seed=req.seed,
        )
        return FileResponse(
            str(path),
            media_type="audio/wav",
            filename=path.name,
            headers={
                "X-Duration": str(duration),
                "X-Sample-Rate": "24000",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/synthesize/voice-clone")
def synthesize_voice_clone(req: SynthesizeVoiceCloneRequest):
    if not model_manager.is_loaded:
        raise HTTPException(status_code=400, detail="No model loaded")

    try:
        prompt_items = None
        if req.voice_clone_prompt_id:
            prompt_items = voice_clone.get_prompt(req.voice_clone_prompt_id)
            if prompt_items is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Prompt {req.voice_clone_prompt_id} not found",
                )
        out_dir = CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{uuid.uuid4().hex[:16]}.wav"
        duration, path = synthesizer.synthesize_voice_clone(
            text=req.text,
            language=req.language,
            ref_audio=req.ref_audio,
            ref_text=req.ref_text,
            x_vector_only_mode=req.x_vector_only_mode,
            voice_clone_prompt=prompt_items,
            output_path=output_path,
            max_new_tokens=req.max_new_tokens,
            top_p=req.top_p,
            temperature=req.temperature,
            seed=req.seed,
        )
        return FileResponse(
            str(path),
            media_type="audio/wav",
            filename=path.name,
            headers={
                "X-Duration": str(duration),
                "X-Sample-Rate": "24000",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Voice clone prompts
# ---------------------------------------------------------------------------

@app.post("/voice-clone/prompts")
def create_voice_clone_prompt(req: VoiceClonePromptRequest):
    if not model_manager.is_loaded:
        raise HTTPException(status_code=400, detail="No model loaded")

    try:
        result = voice_clone.create_prompt(
            synthesizer,
            ref_audio=req.ref_audio,
            ref_text=req.ref_text,
            x_vector_only_mode=req.x_vector_only_mode,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voice-clone/prompts")
def list_voice_clone_prompts():
    return {"prompts": voice_clone.list_prompts()}


@app.delete("/voice-clone/prompts/{prompt_id}")
def delete_voice_clone_prompt(prompt_id: str):
    ok = voice_clone.delete_prompt(prompt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Voice design profile management
# ---------------------------------------------------------------------------

@app.get("/voice-design/profiles")
def list_voice_design_profiles():
    return {"profiles": voice_design.list_designs()}


@app.post("/voice-design/profiles")
def save_voice_design_profile(profile: dict):
    result = voice_design.save_design(profile)
    return result


@app.delete("/voice-design/profiles/{design_id}")
def delete_voice_design_profile(design_id: str):
    ok = voice_design.delete_design(design_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Tasks (batch synthesis)
# ---------------------------------------------------------------------------

@app.post("/tasks")
def create_task(segments: List[dict]):
    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = TaskProgress(
        task_id=task_id,
        status=TaskStatus.PENDING,
    )
    return {"task_id": task_id, "status": "created"}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = TaskStatus.CANCELLED
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@app.post("/shutdown")
def shutdown():
    import os
    logger.info("Shutdown requested")
    os._exit(0)


def main():
    port = int(os.environ.get("QWEN3_TTS_PORT", "8767"))
    host = os.environ.get("QWEN3_TTS_HOST", "127.0.0.1")
    logger.info("Starting Qwen3-TTS sidecar on %s:%s", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
