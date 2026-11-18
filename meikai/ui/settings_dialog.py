"""Settings dialog — API keys, cache, theme selection, custom colors."""
from typing import Dict

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import (
    THEMES, DEFAULT_CACHE_BUDGET_MB,
    DEFAULT_TAB_OVERFLOW_MODE, DEFAULT_AI_PROMPT_TEMPLATE,
    save_api_key, delete_api_key,
)
from ..settings import get_settings
from ..gemini import load_all_keys, reload_client


class _ColorButton(QPushButton):
    color_changed = pyqtSignal(str)

    def __init__(self, color: str, label: str, theme: dict, parent=None):
        super().__init__(parent)
        self._color = color
        self._label = label
        self._theme = theme
        self.setFixedSize(100, 28)
        self._refresh()

    def _refresh(self):
        self.setStyleSheet(f"""
            QPushButton {{
                background: {self._color};
                border: 2px solid {self._theme.get('border','#30363d')};
                border-radius: 5px;
                color: {'#000' if QColor(self._color).lightness() > 128 else '#fff'};
                font-size: 10px;
            }}
        """)
        self.setText(self._color)

    def mousePressEvent(self, ev):
        col = QColorDialog.getColor(QColor(self._color), self, f"Pick {self._label}")
        if col.isValid():
            self._color = col.name()
            self._refresh()
            self.color_changed.emit(self._color)


class SettingsDialog(QDialog):
    settings_changed = pyqtSignal()

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._s = get_settings()
        self.setWindowTitle("⚙ Meikai Settings")
        self.setModal(True)
        self.setMinimumSize(540, 480)
        self._setup_ui()
        self._apply_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._make_api_tab(), "🔑 API Keys")
        self._tabs.addTab(self._make_workflow_tab(), "⚙ Workflow & AI")
        self._tabs.addTab(self._make_theme_tab(), "🎨 Appearance")
        self._tabs.addTab(self._make_cache_tab(), "💾 Storage")

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── API Keys tab ──────────────────────────────────────────────────────────
    def _make_api_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        info = QLabel(
            "API keys are shared via <b>~/.config/gemini-shared/config.json</b>.<br>"
            "Meikai dispatches requests across active keys in parallel for instant (<2s) math transcription."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        # Configured Keys list
        layout.addWidget(QLabel("Configured Keys:"))
        self._key_list = QListWidget()
        self._key_list.setMaximumHeight(140)
        layout.addWidget(self._key_list)

        # Buttons under list
        btn_row = QHBoxLayout()
        btn_del = QPushButton("✕ Remove Selected")
        btn_del.clicked.connect(self._delete_selected_key)
        btn_row.addWidget(btn_del)

        btn_refresh = QPushButton("↻ Refresh")
        btn_refresh.clicked.connect(self._refresh_keys)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Add Key group
        grp = QGroupBox("Add New Gemini API Key")
        grp_layout = QVBoxLayout(grp)
        grp_layout.setSpacing(6)

        add_form = QFormLayout()
        add_form.setSpacing(6)
        self._new_key_input = QLineEdit()
        self._new_key_input.setPlaceholderText("Paste Gemini API Key (starts with AIzaSy...)")
        self._new_key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        add_form.addRow("API Key:", self._new_key_input)

        self._new_label_input = QLineEdit()
        self._new_label_input.setPlaceholderText("Optional label, e.g. 'Project 1'")
        add_form.addRow("Label:", self._new_label_input)
        grp_layout.addLayout(add_form)

        add_btn_row = QHBoxLayout()
        btn_add = QPushButton("✚ Add Key")
        btn_add.clicked.connect(self._add_key)
        add_btn_row.addWidget(btn_add)
        self._key_status = QLabel("")
        self._key_status.setStyleSheet("font-size: 11px;")
        add_btn_row.addWidget(self._key_status)
        add_btn_row.addStretch()
        grp_layout.addLayout(add_btn_row)

        layout.addWidget(grp)
        self._refresh_keys()

        layout.addStretch()
        return w

    def _add_key(self):
        key = self._new_key_input.text().strip()
        label = self._new_label_input.text().strip()
        if not key:
            self._key_status.setText("⚠ Please paste an API key.")
            self._key_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        if save_api_key(key, label):
            reload_client()
            self._new_key_input.clear()
            self._new_label_input.clear()
            self._refresh_keys()
            self._key_status.setText("✓ Key saved successfully!")
            self._key_status.setStyleSheet("color: #3fb950; font-size: 11px;")
        else:
            self._key_status.setText("✗ Failed to save API key.")
            self._key_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _delete_selected_key(self):
        item = self._key_list.currentItem()
        if not item:
            return
        raw_key = item.data(Qt.ItemDataRole.UserRole)
        if raw_key:
            delete_api_key(raw_key)
            reload_client()
            self._refresh_keys()
            self._key_status.setText("✓ Key removed.")
            self._key_status.setStyleSheet("color: #3fb950; font-size: 11px;")

    def _refresh_keys(self):
        self._key_list.clear()
        keys = load_all_keys()
        for k in keys:
            lbl = k.get("label", "")
            key = k.get("key", "")
            masked = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else key
            display = f"  {lbl}  ({masked})" if lbl else f"  {masked}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._key_list.addItem(item)
        if not keys:
            item = QListWidgetItem("  (No keys configured — add one below)")
            self._key_list.addItem(item)

    # ── Theme tab ─────────────────────────────────────────────────────────────
    def _make_theme_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Theme:"))
        self._theme_combo = QComboBox()
        for name in THEMES:
            self._theme_combo.addItem(name)
        idx = self._theme_combo.findText(self._s.theme_name)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        layout.addWidget(self._theme_combo)

        layout.addWidget(QLabel("Custom Color Overrides:"))
        self._color_btns: Dict[str, _ColorButton] = {}
        form = QFormLayout()
        form.setSpacing(8)
        custom = self._s.custom_colors
        t = self._theme
        for key, label in [
            ("bg", "Window Background"),
            ("surface", "Panel/Surface"),
            ("border", "Borders"),
            ("text", "Primary Text"),
            ("accent", "Accent Color"),
        ]:
            color = custom.get(key, t.get(key, "#000000"))
            btn = _ColorButton(color, label, t)
            self._color_btns[key] = btn
            form.addRow(label + ":", btn)
        layout.addLayout(form)

        btn_reset = QPushButton("Reset custom colors")
        btn_reset.clicked.connect(self._reset_colors)
        layout.addWidget(btn_reset)

        layout.addStretch()
        return w

    def _reset_colors(self):
        theme_name = self._theme_combo.currentText()
        base = THEMES.get(theme_name, THEMES[list(THEMES.keys())[0]])
        for key, btn in self._color_btns.items():
            btn._color = base.get(key, "#000000")
            btn._refresh()

    # ── Cache tab ─────────────────────────────────────────────────────────────
    def _make_cache_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        form = QFormLayout()
        self._budget_spin = QSpinBox()
        self._budget_spin.setRange(100, 10240)
        self._budget_spin.setSuffix(" MB")
        self._budget_spin.setValue(self._s.cache_budget_mb)
        form.addRow("Cache Budget:", self._budget_spin)
        layout.addLayout(form)

        btn_prune = QPushButton("🗑 Prune Cache Now")
        btn_prune.clicked.connect(self._prune_cache)
        layout.addWidget(btn_prune)

        self._cache_info = QLabel("")
        layout.addWidget(self._cache_info)
        self._refresh_cache_info()

        layout.addStretch()
        return w

    def _refresh_cache_info(self):
        from ..config import CACHE_DIR
        import os
        try:
            total = sum(
                os.path.getsize(os.path.join(CACHE_DIR, f))
                for f in os.listdir(CACHE_DIR)
                if os.path.isfile(os.path.join(CACHE_DIR, f))
            )
            self._cache_info.setText(f"Current cache size: {total / 1024 / 1024:.1f} MB")
        except Exception:
            self._cache_info.setText("Cache directory not found")

    def _prune_cache(self):
        from ..database import get_db
        pruned = get_db().prune_cache(self._budget_spin.value())
        self._refresh_cache_info()
        self._cache_info.setText(f"Pruned {pruned} files. " + self._cache_info.text())

    # ── Workflow & AI tab ─────────────────────────────────────────────────────
    def _make_workflow_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(14)

        # Tab Overflow Style Group
        tab_grp = QGroupBox("Tab Bar Overflow Style")
        tab_layout = QVBoxLayout(tab_grp)
        tab_layout.setSpacing(6)

        tab_info = QLabel("Choose how to handle the tab strip when multiple tabs exceed window width:")
        tab_info.setWordWrap(True)
        tab_layout.addWidget(tab_info)

        self._overflow_combo = QComboBox()
        self._overflow_combo.addItem("Dropdown Menu (Default)", "dropdown")
        self._overflow_combo.addItem("Horizontal Scroll Arrows", "scroll")
        self._overflow_combo.addItem("Both (Scroll Arrows & Dropdown)", "both")

        curr_mode = self._s.tab_overflow_mode
        idx = self._overflow_combo.findData(curr_mode)
        if idx >= 0:
            self._overflow_combo.setCurrentIndex(idx)
        tab_layout.addWidget(self._overflow_combo)
        layout.addWidget(tab_grp)

        # Copy for AI Prompt Template Group
        ai_grp = QGroupBox("🤖 'Copy for AI' Prompt Template")
        ai_layout = QVBoxLayout(ai_grp)
        ai_layout.setSpacing(6)

        ai_info = QLabel(
            "Customize the prompt format copied to your clipboard when using <b>Copy for AI</b> (Ctrl+Alt+A).<br>"
            "Use <code>{latex}</code> as the placeholder where the transcribed formula or question will be placed."
        )
        ai_info.setTextFormat(Qt.TextFormat.RichText)
        ai_info.setWordWrap(True)
        ai_layout.addWidget(ai_info)

        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlainText(self._s.ai_prompt_template)
        self._prompt_edit.setMinimumHeight(110)
        ai_layout.addWidget(self._prompt_edit)

        btn_row = QHBoxLayout()
        btn_reset_prompt = QPushButton("↺ Reset to Default Prompt")
        btn_reset_prompt.clicked.connect(lambda: self._prompt_edit.setPlainText(DEFAULT_AI_PROMPT_TEMPLATE))
        btn_row.addWidget(btn_reset_prompt)
        btn_row.addStretch()
        ai_layout.addLayout(btn_row)

        layout.addWidget(ai_grp)
        layout.addStretch()
        return w

    # ── Save ──────────────────────────────────────────────────────────────────
    def _save(self):
        self._s.theme_name = self._theme_combo.currentText()
        custom = {k: btn._color for k, btn in self._color_btns.items()}
        self._s.custom_colors = custom
        self._s.cache_budget_mb = self._budget_spin.value()
        self._s.tab_overflow_mode = self._overflow_combo.currentData()
        self._s.ai_prompt_template = self._prompt_edit.toPlainText().strip() or DEFAULT_AI_PROMPT_TEMPLATE
        self._s.sync()
        reload_client()
        self.settings_changed.emit()
        self.accept()

    def _apply_style(self):
        t = self._theme
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background: {t.get('bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
            }}
            QTabWidget::pane {{
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
                background: {t.get('surface','#161b22')};
            }}
            QTabBar::tab {{
                background: {t.get('bg','#0d1117')};
                color: {t.get('text_dim','#8b949e')};
                padding: 8px 16px;
                border: 1px solid {t.get('border','#30363d')};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background: {t.get('surface','#161b22')};
                color: {t.get('text','#e6edf3')};
            }}
            QLabel {{ color: {t.get('text','#e6edf3')}; }}
            QLineEdit, QSpinBox, QComboBox, QListWidget {{
                background: {t.get('editor_bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 5px;
                padding: 5px 8px;
                font-size: 12px;
            }}
            QPushButton {{
                background: {t.get('button_bg','#21262d')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {t.get('button_hover','#30363d')};
            }}
            QDialogButtonBox QPushButton {{
                min-width: 80px;
            }}
        """)
