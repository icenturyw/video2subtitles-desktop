"""Patch output workflow: manifest, multi-format subtitles and richer history actions."""
from __future__ import annotations

import os
import shutil
import time
import webbrowser
from pathlib import Path

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtWidgets import (
    QAction,
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import main_window as mw
from api_client import WhisperApiClient
from client_settings import get_effective_settings
from output_manifest import load_manifest, update_chatgpt_package, write_manifest


THEME = mw.THEME
OriginalMainWindowInit = mw.MainWindow.__init__
OriginalSaveOutput = mw.MainWindow._save_output
OriginalPackageDone = mw.MainWindow._on_chatgpt_package_done


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv"}


def _safe_open_path(path: Path):
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif os.sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(path)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _app_dir() -> Path:
    return Path(mw.__file__).resolve().parent


def _stable_history_path() -> Path:
    return _app_dir() / ".cache" / "history.json"


def _ensure_stable_history(window):
    """Keep history independent from the current output directory.

    The original app stored history under the startup output directory. If users later
    changed the output directory, files were written to the new place while history
    still lived in the old place, making "open output" and "ChatGPT package" lose
    the current item. Store history in .cache/history.json and opportunistically
    migrate any entries that were already loaded by the original manager.
    """
    target = _stable_history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    old_manager = getattr(window, "history", None)
    if old_manager and getattr(old_manager, "history_path", None) == target:
        return old_manager

    old_entries = {}
    try:
        if old_manager:
            old_entries = old_manager.all_entries()
    except Exception:
        old_entries = {}

    new_manager = mw.HistoryManager(target)
    try:
        merged = new_manager.all_entries()
        for key, entry in old_entries.items():
            if key not in merged:
                new_manager.put(key, entry)
    except Exception as exc:
        print(f"Migrate history failed: {exc}")
    window.history = new_manager
    return new_manager


def _patched_init(self, *args, **kwargs):
    OriginalMainWindowInit(self, *args, **kwargs)
    _ensure_stable_history(self)


def _copy_downloaded_video(window, key: str, sub_dir: Path, base: str) -> Path | None:
    video_id = window._get_video_id(key)
    whisper_temp = mw.WHISPER_SERVER / "temp"
    if not whisper_temp.exists():
        return None
    candidates = []
    for pattern in (f"{video_id}.*", f"{video_id[:80]}.*"):
        candidates.extend(whisper_temp.glob(pattern))
    # Also search recent video files when aliases are not exact, e.g. Bilibili hashes.
    candidates.extend(p for p in whisper_temp.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    candidates = [p for p in candidates if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stat().st_size > 0]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for src in candidates:
        dst = sub_dir / f"{base}{src.suffix.lower()}"
        try:
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(str(src), str(dst))
            return dst
        except Exception as exc:
            print(f"Copy downloaded video failed: {exc}")
    return None


def _patched_save_output(self, key, subtitles, is_url=False, language="unknown"):
    try:
        _ensure_stable_history(self)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        client = WhisperApiClient()
        settings = get_effective_settings()

        if is_url:
            video_id = self._get_video_id(key)
            title = self.video_items.get(key, {}).get("title") or ""
            display_title = title or key
            base = self._sanitize_filename(title) if title else video_id
            base = (base or "video")[:80]
            sub_dir = self.output_dir / base
            sub_dir.mkdir(parents=True, exist_ok=True)
            video_path = _copy_downloaded_video(self, key, sub_dir, base)
        else:
            src = Path(key)
            base = src.stem
            display_title = src.name
            sub_dir = self.output_dir / base
            sub_dir.mkdir(parents=True, exist_ok=True)
            video_path = sub_dir / src.name
            try:
                if src.exists() and (not video_path.exists() or video_path.stat().st_size != src.stat().st_size):
                    shutil.copy2(str(src), str(video_path))
            except Exception as exc:
                print(f"Copy video failed: {exc}")
                video_path = src if src.exists() else None

        srt_path = sub_dir / f"{base}.srt"
        vtt_path = sub_dir / f"{base}.vtt"
        txt_path = sub_dir / f"{base}.txt"
        client.save_srt(subtitles, str(srt_path))
        client.save_vtt(subtitles, str(vtt_path))
        client.save_txt(subtitles, str(txt_path))

        manifest = write_manifest(
            sub_dir,
            source=key,
            title=display_title,
            is_url=is_url,
            language=language,
            subtitles=subtitles,
            srt_path=srt_path,
            vtt_path=vtt_path,
            txt_path=txt_path,
            video_path=video_path,
            download_mode=settings.get("download_mode", "video"),
            download_quality=settings.get("download_quality", "best"),
        )

        entry = self.history.make_entry(subtitles, language, srt_path, sub_dir, is_url, display_title)
        entry.update({
            "vtt_path": str(vtt_path),
            "txt_path": str(txt_path),
            "video_path": str(video_path) if video_path else "",
            "manifest_path": str(sub_dir / "manifest.json"),
            "download_mode": manifest.get("download_mode", ""),
            "download_quality": manifest.get("download_quality", ""),
        })
        self.history.put(key, entry)
        return srt_path
    except Exception as exc:
        print(f"Patched save output failed: {exc}")
        return OriginalSaveOutput(self, key, subtitles, is_url, language)


def _resolve_output_path(out_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = out_dir / path
    return path


def _first_existing_file(out_dir: Path, suffixes: set[str], prefer: str = "") -> Path | None:
    if not out_dir.exists():
        return None
    files = [p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in suffixes]
    if prefer:
        preferred = [p for p in files if p.name == prefer or p.stem == Path(prefer).stem]
        if preferred:
            return preferred[0]
    return sorted(files)[0] if files else None


def _candidate_dir_names(window, key: str) -> list[str]:
    names = []
    is_url = str(key).startswith(("http://", "https://"))
    if is_url:
        try:
            title = window.video_items.get(key, {}).get("title") or ""
            if title:
                names.append(window._sanitize_filename(title)[:80])
            names.append(window._get_video_id(key))
        except Exception:
            pass
    else:
        try:
            names.append(Path(key).stem)
        except Exception:
            pass
    return [name for name in dict.fromkeys(names) if name]


def _candidate_output_roots(window) -> list[Path]:
    roots = []
    for root in (
        getattr(window, "output_dir", None),
        mw.WHISPER_SERVER / "output" if getattr(mw, "WHISPER_SERVER", None) else None,
        _app_dir() / "output",
    ):
        if root:
            path = Path(root)
            if path not in roots:
                roots.append(path)
    return roots


def _iter_candidate_output_dirs(window, key: str):
    seen: set[Path] = set()
    entry = None
    try:
        _ensure_stable_history(window)
        entry = window.history.get(key)
    except Exception:
        entry = None

    if entry and entry.get("output_dir"):
        path = Path(entry["output_dir"])
        if path not in seen:
            seen.add(path)
            yield path

    names = _candidate_dir_names(window, key)
    for root in _candidate_output_roots(window):
        for name in names:
            path = root / name
            if path not in seen:
                seen.add(path)
                yield path
        if root.exists():
            try:
                for child in root.iterdir():
                    if child.is_dir() and (child / "manifest.json").exists() and child not in seen:
                        seen.add(child)
                        yield child
            except Exception:
                pass


def _manifest_matches_key(manifest: dict, key: str, out_dir: Path, window) -> bool:
    if not manifest:
        return False
    source = str(manifest.get("source") or "")
    if source == str(key):
        return True
    is_url = str(key).startswith(("http://", "https://"))
    if not is_url:
        try:
            return out_dir.name == Path(key).stem or manifest.get("title") == Path(key).name
        except Exception:
            return False
    try:
        return out_dir.name in _candidate_dir_names(window, key)
    except Exception:
        return False


def _entry_from_manifest(window, key: str, out_dir: Path, manifest: dict) -> dict | None:
    srt_path = _resolve_output_path(out_dir, manifest.get("srt_file")) or _first_existing_file(out_dir, {".srt"})
    if not srt_path or not srt_path.exists():
        return None
    vtt_path = _resolve_output_path(out_dir, manifest.get("vtt_file")) or _first_existing_file(out_dir, {".vtt"})
    txt_path = _resolve_output_path(out_dir, manifest.get("txt_file")) or _first_existing_file(out_dir, {".txt"})
    video_path = _resolve_output_path(out_dir, manifest.get("video_file")) or _first_existing_file(out_dir, VIDEO_EXTS)

    try:
        subtitles = mw.HistoryManager._parse_srt(srt_path)
    except Exception:
        subtitles = []
    is_url = bool(manifest.get("is_url", str(key).startswith(("http://", "https://"))))
    title = manifest.get("title") or (str(key) if is_url else Path(key).name)
    language = manifest.get("language") or "unknown"
    entry = window.history.make_entry(subtitles, language, srt_path, out_dir, is_url, title)
    entry.update({
        "vtt_path": str(vtt_path) if vtt_path else "",
        "txt_path": str(txt_path) if txt_path else "",
        "video_path": str(video_path) if video_path else "",
        "manifest_path": str(out_dir / "manifest.json"),
        "download_mode": manifest.get("download_mode", ""),
        "download_quality": manifest.get("download_quality", ""),
    })
    return entry


def _recover_history_entry(window, key: str) -> dict | None:
    _ensure_stable_history(window)
    entry = window.history.get(key)
    if entry:
        out_dir = entry.get("output_dir") or ""
        srt_path = entry.get("srt_path") or ""
        if out_dir and Path(out_dir).exists() and (not srt_path or Path(srt_path).exists()):
            return entry

    # If the item is currently loaded and has subtitles, re-save it to rebuild history.
    try:
        if key in window.video_items:
            widget = window.video_items[key]["widget"]
            if getattr(widget, "subtitles", None):
                srt_path = window._save_output(key, widget.subtitles, getattr(widget, "is_url", False), "unknown")
                if srt_path:
                    rebuilt = window.history.get(key)
                    if rebuilt:
                        return rebuilt
    except Exception as exc:
        print(f"Recover history by re-saving failed: {exc}")

    # Recover from manifest.json when history.json was moved, deleted or created in another output directory.
    for out_dir in _iter_candidate_output_dirs(window, key):
        manifest = load_manifest(out_dir)
        if not _manifest_matches_key(manifest, key, out_dir, window):
            continue
        recovered = _entry_from_manifest(window, key, out_dir, manifest)
        if recovered:
            window.history.put(key, recovered)
            return recovered
    return None


def _patched_open_output_dir(self, key):
    entry = _recover_history_entry(self, key)
    if entry and entry.get("output_dir") and Path(entry["output_dir"]).exists():
        _safe_open_path(Path(entry["output_dir"]))
        return

    for out_dir in _iter_candidate_output_dirs(self, key):
        if out_dir.exists():
            _safe_open_path(out_dir)
            return

    _safe_open_path(Path(self.output_dir))
    if hasattr(self, "status_label"):
        self.status_label.setText("未找到该任务的独立输出目录，已打开总输出目录。")


def _patched_find_output_video(self, key, out_dir):
    candidates = []
    entry = None
    try:
        entry = self.history.get(key)
    except Exception:
        entry = None
    if entry and entry.get("video_path"):
        path = Path(entry["video_path"])
        if path.exists():
            candidates.append(path)

    manifest = load_manifest(out_dir) if out_dir else {}
    video_from_manifest = _resolve_output_path(Path(out_dir), manifest.get("video_file")) if out_dir else None
    if video_from_manifest and video_from_manifest.exists():
        candidates.append(video_from_manifest)

    out_path = Path(out_dir)
    if out_path.exists():
        candidates.extend(
            p for p in out_path.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and "_proxy_" not in p.stem
        )
    if key in self.video_items:
        widget = self.video_items[key]["widget"]
        if not widget.is_url:
            src = Path(key)
            if src.exists():
                candidates.insert(0, src)
    return candidates[0] if candidates else None


def _patched_generate_chatgpt_package(self, key):
    if self.package_worker and self.package_worker.isRunning():
        QMessageBox.information(self, "正在生成", "已有一个 ChatGPT 分析包正在生成，请稍后再试。")
        return

    entry = _recover_history_entry(self, key)
    if not entry:
        searched = "\n".join(str(p) for p in _candidate_output_roots(self))
        QMessageBox.warning(
            self,
            "无法生成",
            "找不到该项目的历史记录，也未能从 manifest.json 恢复。\n\n"
            f"已搜索目录:\n{searched}",
        )
        return

    out_dir = entry.get("output_dir") or ""
    srt_path = entry.get("srt_path") or ""
    if not out_dir or not Path(out_dir).exists():
        QMessageBox.warning(self, "无法生成", "找不到输出目录。")
        return
    if not srt_path or not Path(srt_path).exists():
        srt = _first_existing_file(Path(out_dir), {".srt"})
        if srt:
            srt_path = str(srt)
            entry["srt_path"] = srt_path
            self.history.put(key, entry)
        else:
            QMessageBox.warning(self, "无法生成", "找不到 SRT 字幕文件。")
            return

    source_video = _patched_find_output_video(self, key, out_dir)
    if not source_video:
        mode = entry.get("download_mode") or load_manifest(out_dir).get("download_mode") or "未知"
        QMessageBox.warning(
            self,
            "无法生成",
            "找不到可用于压缩的原视频文件。\n\n"
            f"当前下载模式: {mode}\n"
            "如果使用在线视频，请在设置中选择“保存 MP4 视频（推荐）”，重新处理后再生成完整 ChatGPT 包。",
        )
        return

    title = entry.get("title") or Path(source_video).name
    self.package_worker = mw.ChatGPTPackageWorker(source_video, srt_path, out_dir, title, self)
    if hasattr(self, "_begin_chatgpt_package_progress"):
        self._begin_chatgpt_package_progress(source_video, out_dir)
    if hasattr(self, "_on_chatgpt_package_progress"):
        self.package_worker.progress.connect(lambda message: self._on_chatgpt_package_progress(message, None))
        if hasattr(self.package_worker, "progress_detail"):
            self.package_worker.progress_detail.connect(self._on_chatgpt_package_progress)
    else:
        self.package_worker.progress.connect(self.status_label.setText)
    self.package_worker.completed.connect(self._on_chatgpt_package_done)
    self.package_worker.failed.connect(self._on_chatgpt_package_failed)
    self.status_label.setText("正在生成 ChatGPT 分析包...")
    self.package_worker.start()


def _patched_package_done(self, package_dir):
    try:
        package_path = Path(package_dir)
        output_dir = package_path.parent
        update_chatgpt_package(output_dir, package_path)
    except Exception as exc:
        print(f"Update manifest package info failed: {exc}")
    OriginalPackageDone(self, package_dir)


def _history_entry_for_item(item):
    return item.data(Qt.UserRole) or (None, None)


def _patched_show_history_dialog(self, entries):
    _ensure_stable_history(self)
    dialog = QDialog(self)
    dialog.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setWindowTitle("历史记录")
    dialog.setMinimumSize(760, 520)
    dialog.resize(860, 580)
    dialog.setStyleSheet(f"QDialog {{ background-color: {THEME['bg_dark']}; }}")

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)

    title = QLabel(f"历史记录（共 {len(entries)} 条）")
    title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {THEME['text_primary']}; padding-bottom: 4px;")
    layout.addWidget(title)

    list_widget = QListWidget()
    list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    list_widget.setSpacing(6)
    list_widget.setStyleSheet(f"""
        QListWidget {{ background: transparent; border: none; }}
        QListWidget::item {{
            background-color: {THEME['bg_card']};
            border: none;
            border-radius: 8px;
            padding: 10px 14px;
            margin: 1px 0px;
        }}
        QListWidget::item:selected {{ background-color: {THEME['accent_dark']}; }}
        QListWidget::item:hover {{ background-color: {THEME['bg_hover']}; }}
    """)

    for key, entry in sorted(entries.items(), key=lambda x: x[1].get("timestamp", 0), reverse=True):
        out_dir = entry.get("output_dir") or ""
        manifest = load_manifest(out_dir) if out_dir else {}
        lang = entry.get("language", "?")
        count = entry.get("subtitle_count", 0)
        is_url = entry.get("is_url", False)
        name = entry.get("title") or manifest.get("title") or (Path(key).name if not is_url else key)
        mode = manifest.get("download_mode") or entry.get("download_mode") or ""
        package_hint = " · 已有 ChatGPT 包" if manifest.get("chatgpt_package_dir") else ""
        detail = f"{lang} · {count} 条 · {time.strftime('%m-%d %H:%M', time.localtime(entry.get('timestamp', 0)))}"
        if mode:
            detail += f" · {mode}"
        detail += package_hint
        item = QListWidgetItem(f"{str(name)[:120]}\n{detail}")
        item.setToolTip(key)
        item.setData(Qt.UserRole, (key, entry))
        item.setSizeHint(QSize(0, 62))
        list_widget.addItem(item)
    layout.addWidget(list_widget)

    def selected():
        item = list_widget.currentItem()
        if not item:
            return None, None
        return _history_entry_for_item(item)

    def open_selected_output():
        key, entry = selected()
        if key:
            _patched_open_output_dir(self, key)
        else:
            out_dir = entry.get("output_dir") if entry else ""
            if out_dir and Path(out_dir).exists():
                _safe_open_path(Path(out_dir))

    def add_back_to_list():
        key, entry = selected()
        if not key or not entry:
            return
        if entry.get("is_url"):
            if key not in self.video_items:
                self.url_input.setText(key)
                self._add_url()
        else:
            if Path(key).exists():
                self._add_videos([key])
            else:
                QMessageBox.warning(dialog, "源文件不存在", "源视频文件已不存在，只能打开输出目录或使用历史字幕。")

    def regenerate_package():
        key, entry = selected()
        if not key or not entry:
            return
        # Ensure a loaded widget exists so package logic can locate state and recover subtitles if needed.
        if key not in self.video_items:
            if entry.get("is_url"):
                self.url_input.setText(key)
                self._add_url()
            elif Path(key).exists():
                self._add_videos([key])
        self._generate_chatgpt_package(key)

    def copy_source():
        key, _ = selected()
        if not key:
            return
        try:
            mw.QApplication.clipboard().setText(key)
            self.status_label.setText("已复制来源路径/链接")
        except Exception:
            pass

    def open_source():
        key, entry = selected()
        if not key:
            return
        if entry and entry.get("is_url"):
            webbrowser.open(key)
        elif Path(key).exists():
            _safe_open_path(Path(key).parent)

    list_widget.itemDoubleClicked.connect(lambda _item: open_selected_output())

    buttons = QHBoxLayout()
    button_defs = [
        ("打开输出目录", open_selected_output),
        ("加回任务列表", add_back_to_list),
        ("重新生成 ChatGPT 包", regenerate_package),
        ("复制来源", copy_source),
        ("打开来源", open_source),
    ]
    for text, fn in button_defs:
        btn = QPushButton(text)
        btn.setObjectName("btn_secondary")
        btn.setFixedHeight(34)
        btn.clicked.connect(fn)
        buttons.addWidget(btn)
    buttons.addStretch()
    close_btn = QPushButton("关闭")
    close_btn.setObjectName("btn_secondary")
    close_btn.setFixedHeight(34)
    close_btn.clicked.connect(dialog.accept)
    buttons.addWidget(close_btn)
    layout.addLayout(buttons)

    if self.window():
        qr = dialog.frameGeometry()
        cp = self.window().frameGeometry().center()
        qr.moveCenter(cp)
        dialog.move(qr.topLeft())
    try:
        dialog.exec_()
    finally:
        self.history_btn.setEnabled(True)


def install():
    mw.MainWindow.__init__ = _patched_init
    mw.MainWindow._save_output = _patched_save_output
    mw.MainWindow._on_chatgpt_package_done = _patched_package_done
    mw.MainWindow._show_history_dialog = _patched_show_history_dialog
    mw.MainWindow._open_output_dir = _patched_open_output_dir
    mw.MainWindow._find_output_video = _patched_find_output_video
    mw.MainWindow._generate_chatgpt_package = _patched_generate_chatgpt_package
