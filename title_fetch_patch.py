"""Safer online-title fetching for the main window.

The normal transcription flow already goes through the local Whisper service.
This patch only improves the lightweight title prefetch that runs after a URL is
added, so yt-dlp warnings are never shown as if they were video titles.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import getproxies

from PyQt5.QtCore import QProcessEnvironment


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url)).netloc or "").lower()
    return any(
        item in host
        for item in ("youtube.com", "youtu.be", "youtube-nocookie.com")
    )


def _noise_line(line: str) -> bool:
    text = (line or "").strip()
    lower = text.lower()
    if not text:
        return True
    if lower.startswith(("warning:", "error:", "debug:", "deprecated feature:", "usage:", "yt-dlp:")):
        return True
    if "no supported javascript runtime" in lower:
        return True
    if "unable to extract" in lower or "signature extraction failed" in lower:
        return True
    if text.startswith("[") and "]" in text and any(tag in lower for tag in ("youtube", "download", "info")):
        return True
    return False


def _find_ytdlp():
    """Return (executable, extra_args_prefix) for launching yt-dlp.

    Prefer the yt-dlp command directly; fall back to python -m yt_dlp.
    """
    import shutil
    yt_dlp_exe = shutil.which("yt-dlp")
    if not yt_dlp_exe:
        user_scripts = Path(os.environ.get("APPDATA", "")) / "Python" / "Python313" / "Scripts" / "yt-dlp.exe"
        if user_scripts.exists():
            yt_dlp_exe = str(user_scripts)
    if yt_dlp_exe:
        return yt_dlp_exe, []
    return sys.executable, ["-m", "yt_dlp"]


def _clean_title(raw: str) -> str:
    candidates = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not _noise_line(line):
            candidates.append(line)
    for title in reversed(candidates):
        if not title.startswith(("http://", "https://")):
            return title[:100]
    return ""


def _title_fetch_args(url: str, mw) -> list[str]:
    args = [
        "--no-playlist",
        "--skip-download",
        "--no-warnings",
        "--extractor-retries", "2",
        "--print", "%(title)s",
    ]

    # Avoid asking every YouTube client variant for a lightweight title preview.
    # The real download/transcription path can still use its broader server-side args.
    if _is_youtube_url(url):
        args += ["--extractor-args", "youtube:player_client=default"]

    proxy = os.environ.get("V2S_PROXY", "").strip()
    if not proxy:
        system_proxies = getproxies()
        proxy = system_proxies.get("http") or system_proxies.get("https") or ""
    if proxy:
        args += ["--proxy", proxy]

    whisper_server = Path(str(getattr(mw, "WHISPER_SERVER", "")))
    cookie_file = whisper_server / "cookies.txt"
    if cookie_file.exists() and cookie_file.stat().st_size > 0:
        args += ["--cookies", str(cookie_file)]

    args.append(str(url))
    return args


def install() -> None:
    import main_window as mw

    if getattr(mw, "_title_fetch_patch_installed", False):
        return

    def patched_start(self):
        self._proc = mw.QProcess(self)
        self._proc.setProcessChannelMode(mw.QProcess.SeparateChannels)
        self._proc.finished.connect(self._on_finished)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self._proc.setProcessEnvironment(env)
        args = _title_fetch_args(self.url, mw)
        exe, prefix = _find_ytdlp()
        if prefix:
            self._proc.start(exe, prefix + args)
        else:
            self._proc.start(exe, args)

    def patched_on_finished(self, *args):
        if not self._proc:
            return
        try:
            raw_out = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
            title = _clean_title(raw_out)
            if title:
                self.title_ready.emit(self.url, title)
                return

            raw_all = raw_out + "\n" + bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
            title = _clean_title(raw_all)
            if title:
                self.title_ready.emit(self.url, title)
        except Exception as exc:
            print(f"TitleFetcher failed: {exc}")

    mw.TitleFetcher.start = patched_start
    mw.TitleFetcher._on_finished = patched_on_finished
    mw._title_fetch_patch_installed = True
