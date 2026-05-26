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
OriginalSaveOutput = mw.MainWindow._save_output
OriginalPackageDone = mw.MainWindow._on_chatgpt_package_done


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


def _copy_downloaded_video(window, key: str, sub_dir: Path, base: str) -> Path | None:
    video_id = window._get_video_id(key)
    whisper_temp = mw.WHISPER_SERVER / "temp"
    video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv"}
    if not whisper_temp.exists():
        return None
    candidates = []
    for pattern in (f"{video_id}.*", f"{video_id[:80]}.*"):
        candidates.extend(whisper_temp.glob(pattern))
    # Also search recent video files when aliases are not exact, e.g. Bilibili hashes.
    candidates.extend(p for p in whisper_temp.iterdir() if p.is_file() and p.suffix.lower() in video_exts)
    candidates = [p for p in candidates if p.is_file() and p.suffix.lower() in video_exts and p.stat().st_size > 0]
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
        _, entry = selected()
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
        # Ensure a loaded widget exists so old package logic can locate state.
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
    mw.MainWindow._save_output = _patched_save_output
    mw.MainWindow._on_chatgpt_package_done = _patched_package_done
    mw.MainWindow._show_history_dialog = _patched_show_history_dialog
