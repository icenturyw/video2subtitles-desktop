from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("engine.voice_design")

_DESIGNS_FILE = "voice_design_profiles.json"


def _get_designs_path() -> Path:
    import os
    data_dir = Path(os.environ.get("QWEN3_TTS_DATA_DIR", "."))
    return data_dir / _DESIGNS_FILE


def save_design(profile: Dict) -> Dict:
    path = _get_designs_path()
    designs = []
    if path.exists():
        designs = json.loads(path.read_text("utf-8"))
    profile_id = str(len(designs) + 1)
    profile["id"] = profile_id
    designs.append(profile)
    path.write_text(
        json.dumps(designs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"id": profile_id, **profile}


def list_designs() -> List[Dict]:
    path = _get_designs_path()
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return []


def delete_design(design_id: str) -> bool:
    path = _get_designs_path()
    if not path.exists():
        return False
    designs = json.loads(path.read_text("utf-8"))
    filtered = [d for d in designs if d.get("id") != design_id]
    if len(filtered) == len(designs):
        return False
    path.write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True
