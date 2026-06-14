from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from engine.synthesis import Synthesizer

logger = logging.getLogger("engine.voice_clone")

_ACTIVE_PROMPTS: Dict[str, list] = {}


def create_prompt(
    synthesizer: Synthesizer,
    ref_audio: str,
    ref_text: Optional[str] = None,
    x_vector_only_mode: bool = False,
) -> Dict:
    prompt_items = synthesizer.create_voice_clone_prompt(
        ref_audio=ref_audio,
        ref_text=ref_text,
        x_vector_only_mode=x_vector_only_mode,
    )
    prompt_id = str(uuid.uuid4())[:8]
    _ACTIVE_PROMPTS[prompt_id] = prompt_items
    return {"prompt_id": prompt_id, "item_count": len(prompt_items)}


def get_prompt(prompt_id: str) -> Optional[list]:
    return _ACTIVE_PROMPTS.get(prompt_id)


def delete_prompt(prompt_id: str) -> bool:
    if prompt_id in _ACTIVE_PROMPTS:
        del _ACTIVE_PROMPTS[prompt_id]
        return True
    return False


def list_prompts() -> List[Dict]:
    return [
        {"prompt_id": pid, "item_count": len(items)}
        for pid, items in _ACTIVE_PROMPTS.items()
    ]
