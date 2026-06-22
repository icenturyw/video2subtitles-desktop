"""Helpers for launching background subprocesses without flashing consoles."""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Mapping


def hidden_subprocess_kwargs(existing: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Return subprocess kwargs that hide child console windows on Windows.

    GUI launches through pythonw/start.bat can otherwise briefly flash an empty
    console when starting helpers such as ffmpeg, ffprobe, powershell, or python.
    """
    kwargs: Dict[str, Any] = dict(existing or {})
    if os.name != "nt":
        return kwargs

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | create_no_window
    except (TypeError, ValueError):
        kwargs["creationflags"] = create_no_window

    startupinfo = kwargs.get("startupinfo")
    if startupinfo is None:
        startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo
    return kwargs
