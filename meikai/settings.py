"""AppSettings — persistent QSettings wrapper for Meikai."""
import json
from typing import Any, Dict, Optional

from PyQt6.QtCore import QSettings

from .config import (
    DEFAULT_THEME, THEMES, DEFAULT_CACHE_BUDGET_MB,
    DEFAULT_TAB_OVERFLOW_MODE, DEFAULT_AI_PROMPT_TEMPLATE,
)


class AppSettings:
    _KEY_THEME        = "ui/theme"
    _KEY_BUDGET       = "cache/budget_mb"
    _KEY_CUSTOM_CLR   = "ui/custom_colors"
    _KEY_SPLIT_MAIN   = "ui/split_main"
    _KEY_SPLIT_RIGHT  = "ui/split_right"
    _KEY_WIN_GEO      = "ui/window_geometry"
    _KEY_ISLAND_SCR   = "island/screen"
    _KEY_TAB_OVERFLOW = "ui/tab_overflow_mode"
    _KEY_AI_PROMPT    = "ai/prompt_template"

    def __init__(self):
        self._s = QSettings("Meikai", "Meikai")

    # ── Theme ─────────────────────────────────────────────────────────────────
    @property
    def theme_name(self) -> str:
        return self._s.value(self._KEY_THEME, DEFAULT_THEME, type=str)

    @theme_name.setter
    def theme_name(self, name: str):
        self._s.setValue(self._KEY_THEME, name)

    @property
    def theme(self) -> Dict[str, str]:
        base = dict(THEMES.get(self.theme_name, THEMES[DEFAULT_THEME]))
        custom = self.custom_colors
        base.update(custom)
        return base

    # ── Custom color overrides ────────────────────────────────────────────────
    @property
    def custom_colors(self) -> Dict[str, str]:
        raw = self._s.value(self._KEY_CUSTOM_CLR, "{}", type=str)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    @custom_colors.setter
    def custom_colors(self, d: Dict[str, str]):
        self._s.setValue(self._KEY_CUSTOM_CLR, json.dumps(d))

    # ── Cache budget ──────────────────────────────────────────────────────────
    @property
    def cache_budget_mb(self) -> int:
        return self._s.value(self._KEY_BUDGET, DEFAULT_CACHE_BUDGET_MB, type=int)

    @cache_budget_mb.setter
    def cache_budget_mb(self, v: int):
        self._s.setValue(self._KEY_BUDGET, v)

    # ── Tab Overflow & AI Workflow ───────────────────────────────────────────
    @property
    def tab_overflow_mode(self) -> str:
        return self._s.value(self._KEY_TAB_OVERFLOW, DEFAULT_TAB_OVERFLOW_MODE, type=str)

    @tab_overflow_mode.setter
    def tab_overflow_mode(self, mode: str):
        self._s.setValue(self._KEY_TAB_OVERFLOW, mode)

    @property
    def ai_prompt_template(self) -> str:
        return self._s.value(self._KEY_AI_PROMPT, DEFAULT_AI_PROMPT_TEMPLATE, type=str)

    @ai_prompt_template.setter
    def ai_prompt_template(self, tmpl: str):
        self._s.setValue(self._KEY_AI_PROMPT, tmpl)

    # ── Layout persistence ────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        return self._s.value(key, default)

    def set(self, key: str, value: Any) -> None:
        self._s.setValue(key, value)

    def sync(self):
        self._s.sync()


# Singleton
_settings: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    global _settings
    if _settings is None:
        _settings = AppSettings()
    return _settings
