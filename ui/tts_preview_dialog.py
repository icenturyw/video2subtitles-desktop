"""Capability-driven TTS preview player and voice preset editor."""
from __future__ import annotations

import uuid
from pathlib import Path

from PyQt5.QtCore import QThread, QUrl, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

try:
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
except ImportError:  # pragma: no cover - depends on optional Qt multimedia runtime
    QMediaContent = None
    QMediaPlayer = None

from localization_client import LocalizationClient


class _PreviewWorker(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, client: LocalizationClient, payload: dict) -> None:
        super().__init__()
        self.client = client
        self.payload = payload

    def run(self) -> None:
        self.completed.emit(self.client.preview_tts(self.payload))


class TTSPreviewDialog(QDialog):
    def __init__(self, parent=None, client: LocalizationClient | None = None) -> None:
        super().__init__(parent)
        self.client = client or LocalizationClient()
        self.worker: _PreviewWorker | None = None
        self.preview_id = ""
        self.player = QMediaPlayer(self) if QMediaPlayer else None
        self.setWindowTitle("TTS 语音试听与预设")
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Provider", self.provider)
        self.language = QLineEdit("zh-CN")
        self.language.editingFinished.connect(self._load_voices)
        form.addRow("语言", self.language)
        self.voice = QComboBox()
        self.voice.setEditable(True)
        form.addRow("声音", self.voice)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 4.0)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(1.0)
        self.speed_label = QLabel("语速")
        form.addRow(self.speed_label, self.speed)
        self.pitch = QDoubleSpinBox()
        self.pitch.setRange(-100, 100)
        self.pitch.setSuffix(" Hz")
        self.pitch_label = QLabel("音调")
        form.addRow(self.pitch_label, self.pitch)
        self.emotion = QLineEdit()
        self.emotion.setPlaceholderText("例如：happy / calm")
        self.emotion_label = QLabel("情感")
        form.addRow(self.emotion_label, self.emotion)
        layout.addLayout(form)

        self.text = QPlainTextEdit("这是一段语音试听文本。")
        self.text.textChanged.connect(self._update_count)
        layout.addWidget(self.text, 1)
        self.count = QLabel()
        layout.addWidget(self.count)

        preset_row = QHBoxLayout()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("预设名称")
        preset_row.addWidget(self.preset_name, 1)
        save_preset = QPushButton("保存预设")
        save_preset.clicked.connect(self._save_preset)
        preset_row.addWidget(save_preset)
        self.presets = QComboBox()
        self.presets.currentIndexChanged.connect(self._apply_preset)
        preset_row.addWidget(self.presets, 1)
        layout.addLayout(preset_row)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("生成试听")
        self.preview_button.clicked.connect(self._start_preview)
        actions.addWidget(self.preview_button)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_preview)
        actions.addWidget(self.cancel_button)
        self.play_button = QPushButton("播放 / 暂停")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self._toggle_playback)
        actions.addWidget(self.play_button)
        actions.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.status = QLabel("试听不会写入正式任务历史。")
        layout.addWidget(self.status)

        self._load_providers()
        self._load_presets()

    def _load_providers(self) -> None:
        data = self.client.get_tts_providers()
        self.provider.clear()
        for item in data.get("providers") or []:
            label = item.get("name", "")
            if not item.get("available", True):
                label += "（不可用）"
            self.provider.addItem(label, item)
        self._provider_changed()

    def _provider_changed(self) -> None:
        item = self.provider.currentData() or {}
        capabilities = item.get("capabilities") or {}
        self._show_parameter(self.speed_label, self.speed, bool(capabilities.get("speed")))
        self._show_parameter(self.pitch_label, self.pitch, bool(capabilities.get("pitch")))
        self._show_parameter(self.emotion_label, self.emotion, bool(capabilities.get("emotion")))
        self._update_count()
        self._load_voices()

    @staticmethod
    def _show_parameter(label, widget, visible: bool) -> None:
        label.setVisible(visible)
        widget.setVisible(visible)

    def _load_voices(self) -> None:
        item = self.provider.currentData() or {}
        provider = item.get("name")
        if not provider:
            return
        current = self.voice.currentText()
        data = self.client.get_tts_voices(provider, self.language.text().strip())
        self.voice.blockSignals(True)
        self.voice.clear()
        for voice in data.get("voices") or []:
            self.voice.addItem(str(voice.get("name") or voice.get("id") or ""), voice)
        if current:
            self.voice.setEditText(current)
        self.voice.blockSignals(False)

    def _capabilities(self) -> dict:
        return (self.provider.currentData() or {}).get("capabilities") or {}

    def _update_count(self) -> None:
        limit = int(self._capabilities().get("preview_character_limit") or 0)
        count = len(self.text.toPlainText())
        self.count.setText(f"{count} / {limit or '—'} 字符")
        self.preview_button.setEnabled(bool(count) and (not limit or count <= limit) and self.worker is None)

    def _options(self) -> dict:
        name = str((self.provider.currentData() or {}).get("name") or "")
        caps = self._capabilities()
        options = {}
        if caps.get("speed"):
            if name == "edge-tts":
                options["rate"] = f"{round((self.speed.value() - 1) * 100):+d}%"
            elif name == "sapi":
                options["sapi_rate"] = round((self.speed.value() - 1) * 5)
            elif name == "volcengine-doubao":
                options["volcengine_speech_rate"] = round((self.speed.value() - 1) * 100)
            else:
                options["speed"] = self.speed.value()
        if caps.get("pitch") and name == "edge-tts":
            options["pitch"] = f"{round(self.pitch.value()):+d}Hz"
        if caps.get("emotion") and self.emotion.text().strip():
            key = "volcengine_emotion" if name == "volcengine-doubao" else "fish_audio_emotion"
            options[key] = self.emotion.text().strip()
        return options

    def _start_preview(self) -> None:
        if self.worker is not None:
            return
        self.preview_id = uuid.uuid4().hex
        payload = {
            "preview_id": self.preview_id,
            "provider": (self.provider.currentData() or {}).get("name", ""),
            "voice": self.voice.currentText().strip(),
            "language": self.language.text().strip(),
            "text": self.text.toPlainText(),
            "options": self._options(),
        }
        self.worker = _PreviewWorker(self.client, payload)
        self.worker.completed.connect(self._preview_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("正在生成试听…")
        self.worker.start()

    def _cancel_preview(self) -> None:
        if self.preview_id:
            self.client.cancel_tts_preview(self.preview_id)
        self.status.setText("已请求取消试听。")

    def _preview_finished(self, result: dict) -> None:
        self.worker = None
        self.cancel_button.setEnabled(False)
        self._update_count()
        if result.get("error"):
            self.status.setText(f"试听失败 [{result.get('error_code', '')}]：{result['error']}")
            return
        path = Path(result["path"])
        self.status.setText("试听已就绪（缓存命中）" if result.get("cached") else "试听已就绪")
        self.play_button.setEnabled(bool(self.player and path.is_file()))
        if self.player and QMediaContent:
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path.resolve()))))
            self.player.play()

    def _toggle_playback(self) -> None:
        if not self.player:
            return
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _load_presets(self) -> None:
        data = self.client.list_voice_presets()
        self.presets.blockSignals(True)
        self.presets.clear()
        self.presets.addItem("选择已有预设…", None)
        for preset in data.get("presets") or []:
            label = ("★ " if preset.get("is_default") else "") + preset.get("name", "")
            self.presets.addItem(label, preset)
        self.presets.blockSignals(False)

    def _apply_preset(self) -> None:
        preset = self.presets.currentData()
        if not preset:
            return
        for row in range(self.provider.count()):
            if (self.provider.itemData(row) or {}).get("name") == preset.get("provider"):
                self.provider.setCurrentIndex(row)
                break
        self.language.setText(preset.get("language", ""))
        self.voice.setEditText(preset.get("voice_id", ""))
        self.preset_name.setText(preset.get("name", ""))

    def _save_preset(self) -> None:
        name = self.preset_name.text().strip()
        if not name:
            QMessageBox.warning(self, "语音预设", "请输入预设名称。")
            return
        result = self.client.create_voice_preset({
            "name": name,
            "provider": (self.provider.currentData() or {}).get("name", ""),
            "voice_id": self.voice.currentText().strip(),
            "language": self.language.text().strip(),
            "parameters": self._options(),
        })
        if result.get("error"):
            QMessageBox.warning(self, "语音预设", result["error"])
            return
        self.status.setText("语音预设已保存；正式任务会保存参数快照。")
        self._load_presets()

    def closeEvent(self, event) -> None:
        if self.worker and self.preview_id:
            self.client.cancel_tts_preview(self.preview_id)
        if self.player:
            self.player.stop()
        super().closeEvent(event)
