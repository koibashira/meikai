<p align="center">
  <img src="meikai.png" alt="Meikai" width="160"/>
</p>

# Meikai — Mathematical LaTeX Transcription Studio

> **The problem:** Free tiers of AI assistants (ChatGPT, Claude, Gemini) limit how many images you can upload per day. If you're solving math problems and want AI help, uploading a photo of each step burns through that quota fast.
>
> **The solution:** Snap or paste any math image → Meikai converts it to LaTeX instantly → paste the text into any AI, completely bypassing image limits.

Meikai is a lightweight Linux desktop tool powered by **Gemini Vision** that turns images and PDFs of mathematical content into clean LaTeX code — in seconds.

> [!NOTE]
> **What does *Meikai* mean?**  
> In Japanese, **Meikai** (**明快** / **明解**, *めいかい*) translates to *clear, lucid, straightforward,* or *a clear explanation/solution*. The name reflects the app's core mission: taking complex, blurry, or handwritten math problems and turning them into crystal-clear, unambiguous LaTeX code.

---

## Features

- **Instant LaTeX extraction** from images and PDFs via Gemini Vision API
- **Dynamic Island** — a compact always-on-top widget so you never have to open the full app to get a quick transcription
- **Multi-tab Studio** — open multiple images/pages side-by-side for comparison
- **PDF support** — pick individual pages from multi-page PDFs, each opens as its own tab
- **Paste from clipboard** (`Ctrl+V`) or **drag-and-drop** — both work in the Studio and the Dynamic Island
- **Rendered preview** — LaTeX is rendered via KaTeX/MathJax so you can verify it before copying
- **Copy for AI** — wraps LaTeX in a clean prompt block ready to paste into ChatGPT, Claude, Gemini, etc.
- **Multiple API keys** — round-robins across keys to avoid rate limits
- **Tab search** (`Ctrl+P`) — fuzzy-search across all open tabs

---

## Screenshots

> *TODO: add screenshot/GIF here*

---

## Requirements

**System:**
- Linux (X11 or XCB — Wayland users are run through XWayland automatically)
- Python 3.10+
- Node.js 18+ and npm (for the MathJax/KaTeX renderer)
- `xprop` (`sudo apt install x11-utils` / `sudo dnf install xorg-x11-utils`)

**Python packages** (see `requirements.txt`):
```
PyQt6, opencv-python, Pillow, numpy, pymupdf
```

**API:** A free [Google Gemini API key](https://aistudio.google.com/app/apikey) (the free tier is generous enough for daily use)

---

## Installation

```bash
# 1. Clone
git clone https://github.com/koibashira/meikai.git
cd meikai

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Node renderer (one-time)
cd meikai/math_renderer
npm install
cd ../..

# 4. Add your Gemini API key
python3 main.py --add-key YOUR_GEMINI_API_KEY
# Optional: label it
python3 main.py --add-key YOUR_KEY --label "personal"

# 5. Install desktop integration (launcher, Dolphin menu, icons)
bash install_desktop.sh
```

`install_desktop.sh` automatically detects where you cloned the repo and:
- Adds a **Meikai** entry to your KDE/app launcher
- Adds an **"Open with Meikai"** right-click option in Dolphin file manager
- Installs the icon at all standard sizes
- Registers a KWin rule so the Dynamic Island never appears in the taskbar

> [!NOTE]
> `install_desktop.sh` is KDE/Plasma-centric. On GNOME or other desktops you can still run Meikai directly with `python3 main.py`; the desktop integration steps just won't apply.

---

## Usage

### Launch

```bash
python3 main.py          # Opens the Studio (or restores it if already running)
python3 main.py --island # Starts directly in Dynamic Island mode (default if already open)
```

Or use the installed desktop launcher / app menu entry.

### Workflow

1. **Get your math image** — screenshot, photo, or PDF scan
2. **Drop it** into the Dynamic Island or the Studio, or press `Ctrl+V` to paste from clipboard
3. Press **`Ctrl+Enter`** (or the ▶ button) to transcribe
4. **Copy the LaTeX** (`Ctrl+Shift+C`) or **Copy for AI** (`Ctrl+Alt+A`) and paste into your AI assistant

### Dynamic Island

The Dynamic Island is a small bar that sits at the edge of your screen. It:
- Accepts drops and pastes like the main Studio
- Shows a quick preview and status
- Has a "→ Studio" button to open the full app for a chosen tab
- Stays out of the way until you need it

### API Key Management

```bash
python3 main.py --list-keys          # Show all configured keys
python3 main.py --add-key NEW_KEY    # Add another key (round-robin load balancing)
python3 main.py --add-key NEW_KEY --label "work"
```

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Open image or PDF file |
| `Ctrl+V` | Paste image/PDF from clipboard |
| `Ctrl+Enter` | Transcribe current tab |
| `Ctrl+Shift+C` | Copy LaTeX |
| `Ctrl+Alt+A` | Copy for AI (formatted prompt) |
| `Ctrl+Alt+C` | Copy rendered PNG |
| `Ctrl+T` | New tab |
| `Ctrl+W` | Close tab |
| `Ctrl+Shift+T` | Reopen last closed tab |
| `Ctrl+Tab` | Next tab |
| `Ctrl+Shift+Tab` | Previous tab |
| `Ctrl+P` / `Ctrl+K` | Search tabs |
| `Ctrl+,` | Settings |
| `Ctrl+H` | Hide to Dynamic Island |
| `Ctrl+Q` | Quit |

---

## Platform

**Linux only** — by design. The Dynamic Island relies on X11 (`xprop`, `libX11`) for window-level positioning and always-on-top behaviour that has no equivalent portable abstraction. Wayland users are automatically handled via XWayland.

---

## Project Structure

```
meikai/
├── main.py                  # Entry point, CLI args, IPC
├── meikai/
│   ├── __init__.py          # Version
│   ├── config.py            # API key storage, Gemini model config
│   ├── gemini.py            # Gemini Vision API client
│   ├── image_proc.py        # Image & PDF processing utilities
│   ├── ipc.py               # Unix socket IPC (single-instance)
│   └── ui/
│       ├── main_window.py   # Studio (multi-tab main window)
│       ├── dynamic_island.py# Always-on-top compact widget
│       ├── tab_widget.py    # Per-tab canvas + LaTeX editor
│       ├── pdf_page_picker.py # PDF page selection dialog
│       ├── settings_dialog.py # API key & model settings UI
│       └── search_dialog.py # Tab fuzzy-search
├── meikai/math_renderer/    # Node.js KaTeX/MathJax renderer
│   ├── index.js
│   └── package.json
├── meikai.desktop           # Desktop file (app launcher)
├── meikai-dolphin.desktop   # Dolphin file-manager "Open with" entry
├── install_desktop.sh       # Installs desktop files + icon
├── requirements.txt
└── LICENSE
```

---

## License

MIT — see [LICENSE](LICENSE).
