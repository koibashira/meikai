"""Meikai — central paths, constants, and configuration."""
import os

# ── Paths ──────────────────────────────────────────────────────────────────────
CONFIG_DIR        = os.path.expanduser("~/.config/Meikai")
CONFIG_FILE       = os.path.join(CONFIG_DIR, "meikai.conf")

SHARED_KEYS_DIR        = os.path.expanduser("~/.config/gemini-shared")
SHARED_KEYS_FILE       = os.path.join(SHARED_KEYS_DIR, "config.json")
SHARED_COOLDOWNS_FILE  = os.path.join(SHARED_KEYS_DIR, "cooldowns.json")

CACHE_DIR   = os.path.expanduser("~/.cache/meikai")
DB_PATH     = os.path.join(CACHE_DIR, "meikai.db")
IPC_SOCK    = os.path.join(CACHE_DIR, "meikai_ipc.sock")

DEFAULT_CACHE_BUDGET_MB = 1024  # 1 GiB

# ── Workflow & UI defaults ───────────────────────────────────────────────────
DEFAULT_TAB_OVERFLOW_MODE = "dropdown"  # "dropdown" | "scroll" | "both"
DEFAULT_AI_PROMPT_TEMPLATE = (
    "Please provide a detailed step-by-step solution to the following mathematical problem, "
    "showing all intermediate work and the final answer:\n\n{latex}"
)

# ── Gemini model cascade (fastest→capable) ────────────────────────────────────
# Only models confirmed working with these API keys
DEFAULT_MODELS = [
    # Tier 1 — absolute fastest (<2s target)
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    # Tier 2 — slightly heavier but very capable
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.1-flash-image",
    # Tier 3 — heavy fallback
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]

# ── 9 built-in themes ─────────────────────────────────────────────────────────
THEMES = {
    "Dark (GitHub/Obsidian)": {
        "bg":        "#0d1117", "surface":   "#161b22", "border":    "#30363d",
        "text":      "#e6edf3", "text_dim":  "#8b949e", "accent":    "#58a6ff",
        "accent2":   "#1f6feb", "editor_bg": "#0d1117", "tab_active":"#161b22",
        "tab_hover": "#1c2128", "button_bg": "#21262d", "button_hover":"#30363d",
        "success":   "#3fb950", "warning":   "#d29922", "error":     "#f85149",
        "island_bg": "#010409", "island_text":"#58a6ff", "island_border":"#30363d",
    },
    "Light (GitHub Clean)": {
        "bg":        "#ffffff", "surface":   "#f6f8fa", "border":    "#d0d7de",
        "text":      "#1f2328", "text_dim":  "#656d76", "accent":    "#0969da",
        "accent2":   "#0550ae", "editor_bg": "#ffffff", "tab_active":"#ffffff",
        "tab_hover": "#eaf5ff", "button_bg": "#f6f8fa", "button_hover":"#eaeef2",
        "success":   "#1a7f37", "warning":   "#9a6700", "error":     "#cf222e",
        "island_bg": "#24292f", "island_text":"#58a6ff", "island_border":"#444c56",
    },
    "Nord Frost": {
        "bg":        "#2e3440", "surface":   "#3b4252", "border":    "#4c566a",
        "text":      "#eceff4", "text_dim":  "#d8dee9", "accent":    "#88c0d0",
        "accent2":   "#5e81ac", "editor_bg": "#2e3440", "tab_active":"#3b4252",
        "tab_hover": "#434c5e", "button_bg": "#3b4252", "button_hover":"#434c5e",
        "success":   "#a3be8c", "warning":   "#ebcb8b", "error":     "#bf616a",
        "island_bg": "#191d26", "island_text":"#88c0d0", "island_border":"#4c566a",
    },
    "Tokyo Night": {
        "bg":        "#1a1b26", "surface":   "#16161e", "border":    "#292e42",
        "text":      "#c0caf5", "text_dim":  "#565f89", "accent":    "#7aa2f7",
        "accent2":   "#2ac3de", "editor_bg": "#1a1b26", "tab_active":"#16161e",
        "tab_hover": "#1e2030", "button_bg": "#1e2030", "button_hover":"#292e42",
        "success":   "#9ece6a", "warning":   "#e0af68", "error":     "#f7768e",
        "island_bg": "#0f0f14", "island_text":"#7aa2f7", "island_border":"#292e42",
    },
    "Dracula": {
        "bg":        "#282a36", "surface":   "#1e1f29", "border":    "#44475a",
        "text":      "#f8f8f2", "text_dim":  "#6272a4", "accent":    "#bd93f9",
        "accent2":   "#ff79c6", "editor_bg": "#282a36", "tab_active":"#1e1f29",
        "tab_hover": "#353746", "button_bg": "#44475a", "button_hover":"#6272a4",
        "success":   "#50fa7b", "warning":   "#f1fa8c", "error":     "#ff5555",
        "island_bg": "#191a21", "island_text":"#bd93f9", "island_border":"#44475a",
    },
    "Catppuccin Mocha": {
        "bg":        "#1e1e2e", "surface":   "#181825", "border":    "#313244",
        "text":      "#cdd6f4", "text_dim":  "#6c7086", "accent":    "#cba6f7",
        "accent2":   "#89b4fa", "editor_bg": "#1e1e2e", "tab_active":"#181825",
        "tab_hover": "#252538", "button_bg": "#313244", "button_hover":"#45475a",
        "success":   "#a6e3a1", "warning":   "#f9e2af", "error":     "#f38ba8",
        "island_bg": "#11111b", "island_text":"#cba6f7", "island_border":"#313244",
    },
    "Solarized Dark": {
        "bg":        "#002b36", "surface":   "#073642", "border":    "#586e75",
        "text":      "#839496", "text_dim":  "#657b83", "accent":    "#268bd2",
        "accent2":   "#2aa198", "editor_bg": "#002b36", "tab_active":"#073642",
        "tab_hover": "#0d3c48", "button_bg": "#073642", "button_hover":"#0d3c48",
        "success":   "#859900", "warning":   "#b58900", "error":     "#dc322f",
        "island_bg": "#001a21", "island_text":"#268bd2", "island_border":"#586e75",
    },
    "High Contrast Dark": {
        "bg":        "#000000", "surface":   "#0a0a0a", "border":    "#555555",
        "text":      "#ffffff", "text_dim":  "#cccccc", "accent":    "#ffff00",
        "accent2":   "#00ffff", "editor_bg": "#000000", "tab_active":"#1a1a1a",
        "tab_hover": "#222222", "button_bg": "#1a1a1a", "button_hover":"#333333",
        "success":   "#00ff00", "warning":   "#ffaa00", "error":     "#ff0000",
        "island_bg": "#000000", "island_text":"#ffff00", "island_border":"#ffffff",
    },
    "High Contrast Light": {
        "bg":        "#ffffff", "surface":   "#f0f0f0", "border":    "#000000",
        "text":      "#000000", "text_dim":  "#333333", "accent":    "#0000cc",
        "accent2":   "#cc0000", "editor_bg": "#ffffff", "tab_active":"#ffffff",
        "tab_hover": "#e0e0ff", "button_bg": "#e0e0e0", "button_hover":"#c0c0c0",
        "success":   "#006600", "warning":   "#996600", "error":     "#cc0000",
        "island_bg": "#000000", "island_text":"#ffffff", "island_border":"#ffffff",
    },
}

DEFAULT_THEME = "Dark (GitHub/Obsidian)"


def save_api_key(key: str, label: str = "") -> bool:
    """Save an API key to the shared Gemini config file ~/.config/gemini-shared/config.json."""
    key = key.strip()
    if not key:
        return False
    import json
    os.makedirs(SHARED_KEYS_DIR, exist_ok=True)
    data = {"keys": []}
    if os.path.isfile(SHARED_KEYS_FILE):
        try:
            with open(SHARED_KEYS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"keys": []}
    keys_list = data.get("keys", [])
    updated = False
    for item in keys_list:
        if isinstance(item, dict) and item.get("key") == key:
            if label:
                item["label"] = label
            updated = True
            break
    if not updated:
        lbl = label if label else (key[:6] + "..." + key[-4:] if len(key) > 12 else key)
        keys_list.append({"key": key, "label": lbl})
    data["keys"] = keys_list
    try:
        with open(SHARED_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def delete_api_key(key_or_label: str) -> bool:
    """Delete an API key by exact key value or label from ~/.config/gemini-shared/config.json."""
    target = key_or_label.strip()
    if not target or not os.path.isfile(SHARED_KEYS_FILE):
        return False
    import json
    try:
        with open(SHARED_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys_list = data.get("keys", [])
        new_list = []
        for item in keys_list:
            if isinstance(item, dict):
                k = item.get("key", "").strip()
                lbl = item.get("label", "").strip()
                if k == target or lbl == target:
                    continue
            elif str(item).strip() == target:
                continue
            new_list.append(item)
        data["keys"] = new_list
        with open(SHARED_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False

