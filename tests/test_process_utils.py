from __future__ import annotations

import os
import subprocess

from process_utils import hidden_subprocess_kwargs


def test_hidden_subprocess_kwargs_preserves_existing_options():
    kwargs = hidden_subprocess_kwargs({"timeout": 3, "text": True})

    assert kwargs["timeout"] == 3
    assert kwargs["text"] is True


def test_hidden_subprocess_kwargs_hides_windows_console():
    kwargs = hidden_subprocess_kwargs()

    if os.name != "nt":
        assert kwargs == {}
        return

    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].wShowWindow == 0
