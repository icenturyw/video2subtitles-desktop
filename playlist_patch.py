"""YouTube playlist add-mode selection for the main window.

When a pasted YouTube URL contains both a video id and a playlist id, the user can
choose whether to add only the current video or expand the whole playlist into
individual queue items. Each expanded item remains a normal single-video URL so
the existing processing, history, retry, and output code paths stay unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _hidden_subprocess_kwargs():
    """Hide helper console windows on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _find_ytdlp():
    """Return (executable, extra_args_prefix) for launching yt-dlp."""
    import shutil

    yt_dlp_exe = shutil.which("yt-dlp")
    if not yt_dlp_exe:
        user_scripts = Path(os.environ.get("APPDATA", "")) / "Python" / "Python313" / "Scripts" / "yt-dlp.exe"
        if user_scripts.exists():
            yt_dlp_exe = str(user_scripts)
    if yt_dlp_exe:
        return yt_dlp_exe, []
    return sys.executable, ["-m", "yt_dlp"]


def _parse(url: str):
    try:
        return urlsplit(str(url))
    except Exception:
        return None


def _youtube_host(host: str) -> bool:
    host = (host or "").lower()
    return any(item in host for item in ("youtube.com", "youtu.be", "youtube-nocookie.com"))


def _query_value(url: str, name: str) -> str:
    parsed = _parse(url)
    if not parsed:
        return ""
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == name:
            return value
    return ""


def _has_video_id(url: str) -> bool:
    parsed = _parse(url)
    if not parsed:
        return False
    if "youtu.be" in (parsed.netloc or "").lower() and parsed.path.strip("/"):
        return True
    return bool(_query_value(url, "v"))


def _is_youtube_playlist_url(url: str) -> bool:
    parsed = _parse(url)
    if not parsed or not _youtube_host(parsed.netloc):
        return False
    return bool(_query_value(url, "list"))


def _strip_playlist_params(url: str) -> str:
    """Return a single-video URL by removing playlist-only query parameters."""
    parsed = _parse(url)
    if not parsed:
        return url
    filtered = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"list", "index", "start_radio", "pp"}
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(filtered, doseq=True), parsed.fragment))


def _playlist_url(url: str) -> str:
    playlist_id = _query_value(url, "list")
    return f"https://www.youtube.com/playlist?list={playlist_id}" if playlist_id else url


def _extra_ytdlp_args(mw) -> list[str]:
    args = [
        "--extractor-retries", "2",
    ]
    proxy = os.environ.get("V2S_PROXY", "").strip()
    if proxy:
        args += ["--proxy", proxy]

    whisper_server = Path(str(getattr(mw, "WHISPER_SERVER", "")))
    cookie_file = whisper_server / "cookies.txt"
    if cookie_file.exists() and cookie_file.stat().st_size > 0:
        args += ["--cookies", str(cookie_file)]
    return args


def _run_ytdlp_json(url: str, mw) -> dict:
    exe, prefix = _find_ytdlp()
    args = [
        *prefix,
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
    ]
    args += _extra_ytdlp_args(mw)
    args.append(_playlist_url(url))

    run_kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 180,
    }
    run_kwargs.update(_hidden_subprocess_kwargs())
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    run_kwargs["env"] = env

    proc = subprocess.run([exe, *args], **run_kwargs)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail[-500:] or f"yt-dlp 退出码 {proc.returncode}")

    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("yt-dlp 未返回播放列表信息")
    return json.loads(raw.splitlines()[-1])


def _entry_to_url(entry: dict) -> str:
    webpage_url = str(entry.get("webpage_url") or "").strip()
    if webpage_url.startswith(("http://", "https://")):
        return webpage_url

    video_id = str(entry.get("id") or entry.get("url") or "").strip()
    if video_id and not video_id.startswith(("http://", "https://")):
        return f"https://www.youtube.com/watch?v={video_id}"

    raw_url = str(entry.get("url") or "").strip()
    if raw_url.startswith(("http://", "https://")):
        return raw_url
    return ""


def _fetch_playlist_video_urls(url: str, mw) -> list[str]:
    data = _run_ytdlp_json(url, mw)
    entries = data.get("entries") or []
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        video_url = _strip_playlist_params(_entry_to_url(entry))
        if not video_url or video_url in seen:
            continue
        seen.add(video_url)
        result.append(video_url)
    return result


def _ask_playlist_mode(parent, url: str):
    """Return playlist/current/cancel/None."""
    if not _is_youtube_playlist_url(url):
        return None

    import main_window as mw

    box = mw.QMessageBox(parent)
    box.setIcon(mw.QMessageBox.Question)
    box.setWindowTitle("检测到 YouTube 播放列表")
    box.setText("这个链接属于 YouTube 播放列表。")
    if _has_video_id(url):
        box.setInformativeText("请选择要添加整个列表，还是只添加当前粘贴的这个视频。")
        playlist_btn = box.addButton("添加整个列表", mw.QMessageBox.AcceptRole)
        current_btn = box.addButton("只添加当前视频", mw.QMessageBox.ActionRole)
        cancel_btn = box.addButton("取消", mw.QMessageBox.RejectRole)
        box.setDefaultButton(current_btn)
        box.exec_()
        clicked = box.clickedButton()
        if clicked == playlist_btn:
            return "playlist"
        if clicked == current_btn:
            return "current"
        if clicked == cancel_btn:
            return "cancel"
        return "cancel"

    box.setInformativeText("这是播放列表链接，没有指定当前视频。是否展开并添加列表内所有视频？")
    playlist_btn = box.addButton("添加整个列表", mw.QMessageBox.AcceptRole)
    cancel_btn = box.addButton("取消", mw.QMessageBox.RejectRole)
    box.setDefaultButton(playlist_btn)
    box.exec_()
    return "playlist" if box.clickedButton() == playlist_btn else "cancel"


def install() -> None:
    import main_window as mw

    if getattr(mw, "_playlist_patch_installed", False):
        return

    original_add_url = mw.MainWindow._add_url

    def add_single(self, url: str):
        self.url_input.setText(url)
        return original_add_url(self)

    def add_playlist(self, url: str):
        before = len(self.video_items)
        self.url_input.clear()
        self.status_label.setText("正在读取播放列表...")
        mw.QApplication.setOverrideCursor(mw.Qt.WaitCursor)
        try:
            urls = _fetch_playlist_video_urls(url, mw)
        except Exception as exc:
            mw.QMessageBox.warning(
                self,
                "读取播放列表失败",
                f"无法展开播放列表：\n{str(exc)[:500]}\n\n可以选择“只添加当前视频”后继续处理。",
            )
            return
        finally:
            mw.QApplication.restoreOverrideCursor()

        if not urls:
            mw.QMessageBox.information(self, "提示", "播放列表中未找到可添加的视频")
            return

        skipped = 0
        for video_url in urls:
            if video_url in self.video_items:
                skipped += 1
                continue
            add_single(self, video_url)

        added = len(self.video_items) - before
        self.url_input.clear()
        self.status_label.setText(
            f"播放列表已添加 {added} 个视频"
            + (f"，跳过 {skipped} 个已存在视频" if skipped else "")
        )

    def patched_add_url(self):
        raw = self.url_input.text().strip()
        if not raw:
            return None

        url = raw if raw.startswith(("http://", "https://")) else "https://" + raw
        mode = _ask_playlist_mode(self, url)
        if mode == "cancel":
            return None
        if mode == "current":
            return add_single(self, _strip_playlist_params(url))
        if mode == "playlist":
            return add_playlist(self, url)
        return original_add_url(self)

    mw.MainWindow._add_url = patched_add_url
    mw._playlist_patch_installed = True
