from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import qwen_tts  # noqa: ensure qwen_tts model registry is initialized
except ImportError:
    pass

logger = logging.getLogger("engine.model_manager")

DEFAULT_MODELS = {
    "0.6B-CustomVoice": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "0.6B-Base": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "1.7B-CustomVoice": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "1.7B-Base": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "1.7B-VoiceDesign": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}

MODEL_TIERS = {
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice": {
        "tier": "standard",
        "capabilities": ["custom_voice"],
        "size_gb": 2.5,
        "min_vram_mb": 2048,
    },
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base": {
        "tier": "standard",
        "capabilities": ["voice_clone"],
        "size_gb": 2.5,
        "min_vram_mb": 2048,
    },
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice": {
        "tier": "premium",
        "capabilities": ["custom_voice"],
        "size_gb": 5.0,
        "min_vram_mb": 4096,
    },
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base": {
        "tier": "premium",
        "capabilities": ["voice_clone"],
        "size_gb": 5.0,
        "min_vram_mb": 4096,
    },
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign": {
        "tier": "premium",
        "capabilities": ["custom_voice", "voice_design"],
        "size_gb": 5.0,
        "min_vram_mb": 4096,
    },
}

_LANGUAGES = [
    "zh", "en", "ja", "ko", "de", "fr", "ru",
    "pt", "es", "it",
]

_PRESET_SPEAKERS = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
]


class ModelManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._model = None
        self._model_id: Optional[str] = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()

    @property
    def loaded_model_id(self) -> Optional[str]:
        return self._model_id

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_type(self) -> str:
        """Return the type suffix from the loaded model ID, e.g. 'VoiceDesign', 'CustomVoice', 'Base'."""
        if not self._model_id:
            return ""
        parts = self._model_id.split("-")
        return parts[-1] if parts else ""

    def list_models(self) -> List[Dict]:
        result = []
        for short, model_id in DEFAULT_MODELS.items():
            info = dict(MODEL_TIERS.get(model_id, {}))
            info["model_id"] = model_id
            info["short"] = short
            result.append(info)
        return result

    def list_speakers(self) -> List[str]:
        return list(_PRESET_SPEAKERS)

    def list_languages(self) -> List[str]:
        return list(_LANGUAGES)

    def load_model(
        self,
        model_id: str,
        device: Optional[str] = None,
        dtype: Optional[str] = None,
        attn_implementation: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ) -> Tuple[bool, str]:
        with self._load_lock:
            if self._model is not None and self._model_id == model_id:
                return True, "Model already loaded"
            self._unload_model()

            if device is None:
                from engine.device import get_optimal_device
                device, dtype = get_optimal_device()

            import torch
            torch_dtype = torch.bfloat16 if dtype == "bfloat16" else (
                torch.float16 if dtype == "float16" else torch.float32
            )

            try:
                logger.info(
                    "Loading model %s on %s (dtype=%s)",
                    model_id, device, dtype,
                )
                from qwen_tts import Qwen3TTSModel, Qwen3TTSTokenizer

                load_kwargs = {
                    "torch_dtype": torch_dtype,
                }
                if device.startswith("cuda"):
                    load_kwargs["device_map"] = device if ":" in device else "cuda:0"
                if cache_dir:
                    load_kwargs["cache_dir"] = cache_dir
                self._model = Qwen3TTSModel.from_pretrained(
                    model_id, **load_kwargs,
                )
                # Tokenizer loading may fail for VoiceDesign models which
                # lack a feature extractor; that's fine, synthesis works
                # without explicit tokenizer for generate_voice_design.
                try:
                    self._tokenizer = Qwen3TTSTokenizer.from_pretrained(
                        model_id,
                        cache_dir=cache_dir,
                    )
                except Exception as tok_err:
                    logger.warning(
                        "Tokenizer not loaded (non-fatal for VoiceDesign): %s",
                        tok_err,
                    )
                    self._tokenizer = None
                self._model_id = model_id
                logger.info("Model loaded successfully: %s", model_id)
                return True, f"Loaded {model_id} on {device}"
            except Exception as e:
                logger.error("Failed to load model %s: %s", model_id, e)
                self._model = None
                self._tokenizer = None
                self._model_id = None
                return False, str(e)

    def unload_model(self) -> Tuple[bool, str]:
        with self._load_lock:
            return self._unload_model()

    def _unload_model(self) -> Tuple[bool, str]:
        if self._model is None:
            return True, "No model loaded"

        model_id = self._model_id
        try:
            del self._model
            del self._tokenizer
            self._model = None
            self._tokenizer = None
            self._model_id = None

            import gc
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

            logger.info("Model unloaded: %s", model_id)
            return True, f"Unloaded {model_id}"
        except Exception as e:
            logger.error("Failed to unload model: %s", e)
            return False, str(e)

    def get_model(self):
        return self._model

    def get_tokenizer(self):
        return self._tokenizer


def _has_flash_attn() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("flash_attn") is not None
    except Exception:
        return False
