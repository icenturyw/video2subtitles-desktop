"""Subtitle style configuration dialog."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from job_models import SubtitleStyle

_THEME = {
    "bg_dark": "#1a1b2e",
    "bg_medium": "#232540",
    "bg_light": "#2d2f54",
    "bg_card": "#363870",
    "accent": "#7c6ff0",
    "text_primary": "#e8eaff",
    "text_secondary": "#9a9cc0",
    "text_muted": "#6b6d92",
    "border": "#3d3f6b",
    "success": "#4ade80",
    "error": "#f87171",
}


_STYLE_SHEET = f"""
QDialog {{ background-color: {_THEME["bg_dark"]}; }}
QGroupBox {{
    border: 1px solid {_THEME["border"]};
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px 12px 10px 12px;
    font-weight: 600;
    color: {_THEME["text_primary"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: {_THEME["accent"]};
}}
QLabel {{ color: {_THEME["text_secondary"]}; font-size: 12px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {_THEME["bg_light"]};
    border: 1px solid {_THEME["border"]};
    border-radius: 6px;
    padding: 6px 10px;
    color: {_THEME["text_primary"]};
    font-size: 12px;
}}
"""


class SubtitleStyleDialog(QDialog):
    """Dialog for configuring subtitle appearance."""

    def __init__(self, parent=None, style: SubtitleStyle = None):
        super().__init__(parent)
        self._style = style or SubtitleStyle()
        self.setWindowTitle("字幕样式")
        self.setMinimumSize(480, 520)
        self.setStyleSheet(_STYLE_SHEET)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("字幕样式设置")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {_THEME['text_primary']};")
        layout.addWidget(title)

        # Presets
        preset_group = QGroupBox("样式预设")
        preset_layout = QHBoxLayout(preset_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(SubtitleStyle.presets().keys()))
        self.preset_combo.setCurrentText(self._style.preset)
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_layout.addWidget(self.preset_combo)
        preset_layout.addStretch()
        layout.addWidget(preset_group)

        # Font
        font_group = QGroupBox("字体")
        font_form = QFormLayout(font_group)
        font_form.setSpacing(8)

        self.font_family = QLineEdit(self._style.font_family)
        font_form.addRow("字体:", self.font_family)

        self.font_size = QSpinBox()
        self.font_size.setRange(12, 120)
        self.font_size.setValue(self._style.font_size)
        font_form.addRow("字号:", self.font_size)

        self.bold = QCheckBox()
        self.bold.setChecked(self._style.bold)
        font_form.addRow("加粗:", self.bold)

        layout.addWidget(font_group)

        # Outline & Shadow
        effect_group = QGroupBox("描边与阴影")
        effect_form = QFormLayout(effect_group)
        effect_form.setSpacing(8)

        self.outline = QDoubleSpinBox()
        self.outline.setRange(0, 10)
        self.outline.setSingleStep(0.5)
        self.outline.setValue(self._style.outline)
        effect_form.addRow("描边:", self.outline)

        self.shadow = QDoubleSpinBox()
        self.shadow.setRange(0, 10)
        self.shadow.setSingleStep(0.5)
        self.shadow.setValue(self._style.shadow)
        effect_form.addRow("阴影:", self.shadow)

        layout.addWidget(effect_group)

        # Position
        pos_group = QGroupBox("位置")
        pos_form = QFormLayout(pos_group)
        pos_form.setSpacing(8)

        self.margin_v = QSpinBox()
        self.margin_v.setRange(0, 300)
        self.margin_v.setValue(self._style.margin_v)
        pos_form.addRow("底部边距:", self.margin_v)

        layout.addWidget(pos_group)

        # Bilingual scale
        bilingual_group = QGroupBox("双语比例")
        bilingual_form = QFormLayout(bilingual_group)
        bilingual_form.setSpacing(8)

        self.source_scale = QDoubleSpinBox()
        self.source_scale.setRange(0.3, 2.0)
        self.source_scale.setSingleStep(0.05)
        self.source_scale.setValue(self._style.bilingual_source_scale)
        bilingual_form.addRow("原文字号比例:", self.source_scale)

        self.trans_scale = QDoubleSpinBox()
        self.trans_scale.setRange(0.3, 2.0)
        self.trans_scale.setSingleStep(0.05)
        self.trans_scale.setValue(self._style.bilingual_translation_scale)
        bilingual_form.addRow("译文字号比例:", self.trans_scale)

        layout.addWidget(bilingual_group)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_preset_changed(self, preset_name: str):
        presets = SubtitleStyle.presets()
        if preset_name in presets:
            p = presets[preset_name]
            self.font_family.setText(p.font_family)
            self.font_size.setValue(p.font_size)
            self.outline.setValue(p.outline)
            self.shadow.setValue(p.shadow)
            self.margin_v.setValue(p.margin_v)
            self.bold.setChecked(p.bold)
            self.source_scale.setValue(p.bilingual_source_scale)
            self.trans_scale.setValue(p.bilingual_translation_scale)

    @property
    def style(self) -> SubtitleStyle:
        return SubtitleStyle(
            preset=self.preset_combo.currentText(),
            font_family=self.font_family.text(),
            font_size=self.font_size.value(),
            outline=self.outline.value(),
            shadow=self.shadow.value(),
            margin_v=self.margin_v.value(),
            bold=self.bold.isChecked(),
            bilingual_source_scale=self.source_scale.value(),
            bilingual_translation_scale=self.trans_scale.value(),
        )
