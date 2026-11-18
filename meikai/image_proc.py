"""Image processing: perspective warp, rotation, contrast, and inversion."""
import os
import time
import uuid
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from .config import CACHE_DIR


def _cache_path(prefix: str, ext: str = ".jpg") -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{prefix}_{uuid.uuid4().hex[:8]}{ext}")


def save_original(image_bytes: bytes) -> str:
    """Save raw image bytes to cache; return path."""
    ext = ".png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    path = _cache_path("orig", ext)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def load_as_pil(path: str) -> Image.Image:
    return Image.open(path).convert("RGB")


def load_as_np(path: str) -> np.ndarray:
    img = load_as_pil(path)
    return np.array(img)


def pil_to_bytes(img: Image.Image, fmt: str = "JPEG") -> bytes:
    import io
    if fmt.upper() in ("JPEG", "JPG") and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def apply_perspective_warp(
    img_path: str,
    corners: List[Tuple[float, float]],
    out_w: Optional[int] = None,
    out_h: Optional[int] = None,
) -> Tuple[str, bytes]:
    """
    Apply 4-point perspective warp.
    corners: [(x1,y1),(x2,y2),(x3,y3),(x4,y4)] — TL, TR, BR, BL (normalized 0..1).
    Calculates dynamic output dimensions from corner coordinates to preserve
    the true aspect ratio of mathematical formulas without stretching.
    Returns (cached_path, image_bytes).
    """
    orig = cv2.imread(img_path)
    if orig is None:
        raise ValueError(f"Cannot read image: {img_path}")

    H, W = orig.shape[:2]
    # Denormalize corners
    pts_src = np.array(
        [(x * W, y * H) for x, y in corners], dtype=np.float32
    )

    if out_w is None or out_h is None:
        tl, tr, br, bl = pts_src
        # Width: maximum distance between bottom-right/bottom-left and top-right/top-left
        width_a = np.hypot(br[0] - bl[0], br[1] - bl[1])
        width_b = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
        calc_w = max(int(max(width_a, width_b)), 100)

        # Height: maximum distance between top-right/bottom-right and top-left/bottom-left
        height_a = np.hypot(tr[0] - br[0], tr[1] - br[1])
        height_b = np.hypot(tl[0] - bl[0], tl[1] - bl[1])
        calc_h = max(int(max(height_a, height_b)), 60)

        # Scale to optimal resolution for Gemini Vision (<2048 max, >=800 min)
        max_dim = max(calc_w, calc_h)
        if max_dim > 2048:
            scale = 2048.0 / max_dim
            calc_w = int(calc_w * scale)
            calc_h = int(calc_h * scale)
        elif max_dim < 700:
            scale = 800.0 / max_dim
            calc_w = int(calc_w * scale)
            calc_h = int(calc_h * scale)

        out_w = max(100, calc_w)
        out_h = max(60, calc_h)

    pts_dst = np.array(
        [(0, 0), (out_w - 1, 0), (out_w - 1, out_h - 1), (0, out_h - 1)],
        dtype=np.float32,
    )
    try:
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(orig, M, (out_w, out_h), flags=cv2.INTER_LANCZOS4)
    except cv2.error:
        # Fallback to axis-aligned bounding box crop if crop points are collinear or degenerate
        min_x = max(0, int(min(p[0] for p in pts_src)))
        max_x = min(W, int(max(p[0] for p in pts_src)))
        min_y = max(0, int(min(p[1] for p in pts_src)))
        max_y = min(H, int(max(p[1] for p in pts_src)))
        if max_x - min_x >= 20 and max_y - min_y >= 20:
            warped = cv2.resize(orig[min_y:max_y, min_x:max_x], (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
        else:
            warped = cv2.resize(orig, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

    path = _cache_path("warp", ".jpg")
    cv2.imwrite(path, warped, [cv2.IMWRITE_JPEG_QUALITY, 94])
    with open(path, "rb") as f:
        data = f.read()
    return path, data


def rotate_image(img_path: str, degrees: int) -> Tuple[str, str]:
    """Rotate by degrees (90/-90). Returns (new_path, new_path)."""
    img = load_as_pil(img_path)
    rotated = img.rotate(-degrees, expand=True)
    path = _cache_path("rot", ".jpg")
    rotated.save(path, "JPEG", quality=92)
    return path, path


def enhance_contrast(img_path: str, factor: float = 1.8) -> Tuple[str, str]:
    img = load_as_pil(img_path)
    enhanced = ImageEnhance.Contrast(img).enhance(factor)
    path = _cache_path("enh", ".jpg")
    enhanced.save(path, "JPEG", quality=92)
    return path, path


def invert_image(img_path: str) -> Tuple[str, str]:
    img = load_as_pil(img_path)
    inverted = ImageOps.invert(img)
    path = _cache_path("inv", ".jpg")
    inverted.save(path, "JPEG", quality=92)
    return path, path


def prepare_for_gemini(img_path: str, corners: Optional[List] = None) -> bytes:
    """
    If corners provided and not all-corners (identity), apply warp.
    Then resize to reasonable resolution for Gemini and return bytes.
    """
    if corners and not _is_identity_crop(corners):
        _, warped_bytes = apply_perspective_warp(img_path, corners)
        return warped_bytes
    else:
        img = load_as_pil(img_path)
        # Resize to max 2048px on longest side
        MAX_DIM = 2048
        w, h = img.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return pil_to_bytes(img)


def _is_identity_crop(corners: List) -> bool:
    """True if corners represent the full image (no warp needed)."""
    if not corners or len(corners) != 4:
        return True
    expected = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for c, e in zip(corners, expected):
        if abs(c[0] - e[0]) > 0.01 or abs(c[1] - e[1]) > 0.01:
            return False
    return True


def get_obsidian_vault_paths() -> List[str]:
    """Retrieve all active Obsidian vault root directories from ~/.config/obsidian/obsidian.json."""
    paths = []
    cfg = os.path.expanduser("~/.config/obsidian/obsidian.json")
    if os.path.isfile(cfg):
        try:
            import json
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            for v in data.get("vaults", {}).values():
                vp = v.get("path")
                if vp and os.path.isdir(vp):
                    paths.append(vp)
        except Exception:
            pass
    return paths


def resolve_image_path(txt: str) -> Optional[str]:
    """
    Resolve a file path, relative vault path, or Obsidian markdown wikilink to an absolute existing image path.
    Supports:
      - Absolute paths (/home/user/.../image.png)
      - File or app URIs (file:///..., app://obsidian.md/...)
      - Obsidian markdown links (![[image.png]], ![[folder/img.png|400]], ![alt](path))
      - Relative paths in any known Obsidian vault
      - Direct filename search in known Obsidian vaults
    """
    if not txt or not isinstance(txt, str):
        return None


# ── PDF support ───────────────────────────────────────────────────────────────

def _require_fitz():
    """Import PyMuPDF; raise ImportError with an install hint if missing."""
    try:
        import fitz  # noqa: PLC0415  (lazy import — only pulled in for PDFs)
        return fitz
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF support. "
            "Install it with:  pip install pymupdf"
        )


def is_pdf_path(path: str) -> bool:
    """Return True if *path* points to an existing PDF file."""
    return bool(path) and path.lower().endswith(".pdf") and os.path.isfile(path)


def get_pdf_page_count(path: str) -> int:
    """Return the number of pages in *path*, or 0 on any error."""
    try:
        fitz = _require_fitz()
        doc = fitz.open(path)
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return 0


def pdf_page_to_image_bytes(path: str, page_num: int = 0, dpi: int = 200) -> bytes:
    """
    Render one PDF page to PNG bytes at *dpi* dots-per-inch.

    Raises:
        ImportError   – if PyMuPDF is not installed.
        ValueError    – if *page_num* is out of range.
    """
    fitz = _require_fitz()
    doc = fitz.open(path)
    if page_num < 0 or page_num >= doc.page_count:
        doc.close()
        raise ValueError(
            f"Page {page_num} out of range (PDF has {doc.page_count} page(s))."
        )
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def pdf_page_to_thumbnail(path: str, page_num: int = 0, max_dim: int = 120) -> bytes:
    """
    Render a low-resolution PNG thumbnail for one PDF page.
    *max_dim* is the longest side in pixels.
    Returns b"" on failure (never raises).
    """
    try:
        fitz = _require_fitz()
        doc = fitz.open(path)
        if page_num < 0 or page_num >= doc.page_count:
            doc.close()
            return b""
        page = doc[page_num]
        rect = page.rect
        scale = max_dim / max(rect.width, rect.height, 1.0)
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception:
        return b""


def extract_pdf_path(md) -> Optional[str]:
    """
    Extract a local PDF file path from QMimeData.
    Returns None if the drop does not contain a PDF.
    Checks: file:// URLs, plain-text paths.
    """
    if not md:
        return None
    if md.hasUrls():
        import urllib.parse
        for u in md.urls():
            path = u.toLocalFile()
            if not path and u.path():
                path = urllib.parse.unquote(u.path())
            if path and path.lower().endswith(".pdf") and os.path.isfile(path):
                return path
    if md.hasText():
        for line in md.text().splitlines():
            line = line.strip()
            if line.lower().endswith(".pdf") and os.path.isfile(line):
                return line
    return None


def can_accept_mime(md) -> bool:
    """Check if QMimeData contains an image (directly, or via URL, HTML, or file/vault path)."""
    if not md:
        return False
    if md.hasImage() or md.hasUrls():
        return True
    if md.hasHtml():
        h = md.html().lower()
        if "<img" in h or "data:image" in h or "obsidian.md" in h or any(ext in h for ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff')):
            return True
    if md.hasText():
        t = md.text().lower()
        if "[[" in t or "obsidian" in t or "app://" in t or any(ext in t for ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.pdf')):
            return True
    for fmt in md.formats():
        if "image" in fmt.lower() or "obsidian" in fmt.lower():
            return True
    return False

def extract_image_bytes(md) -> Optional[bytes]:
    """
    Extract raw image bytes from QMimeData.
    Handles:
      - Native bitmap / clipboard image (hasImage / imageData)
      - Desktop file URLs (file://...)
      - Obsidian internal app URLs (app://obsidian.md/...)
      - Dragged HTML from Obsidian / Electron / browsers (<img src="...">, data:image;base64,...)
      - Plain-text paths, relative vault paths, and Obsidian wikilinks (![[...]])
      - Any custom MIME formats carrying image URLs or raw bytes
    """
    if not md:
        return None

    # 1. Native QImage or imageData
    if md.hasImage():
        img = md.imageData()
        if img:
            from PyQt6.QtCore import QBuffer, QIODeviceBase
            buf = QBuffer()
            buf.open(QIODeviceBase.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            bts = bytes(buf.data())
            if bts:
                return bts

    # 2. URLs (including Electron app://obsidian.md/...)
    if md.hasUrls():
        import urllib.parse
        for u in md.urls():
            path = u.toLocalFile()
            if not path and u.path():
                path = urllib.parse.unquote(u.path())
            resolved = resolve_image_path(path) if path else None
            if resolved and os.path.isfile(resolved):
                ext = os.path.splitext(resolved)[1].lower()
                if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff'):
                    try:
                        with open(resolved, "rb") as f:
                            return f.read()
                    except Exception:
                        pass
            url_str = u.toString()
            if url_str.startswith("data:image/") and ";base64," in url_str:
                import base64
                try:
                    return base64.b64decode(url_str.split(";base64,", 1)[1])
                except Exception:
                    pass

    # 3. HTML (e.g. dragging <img> from Obsidian reading view, Chrome, Firefox)
    if md.hasHtml():
        import re, urllib.parse, base64
        html = md.html()
        for m in re.finditer(r'<img[^>]+src=[\"\x27]([^\"\x27]+)[\"\x27]', html, re.I):
            src = m.group(1).strip()
            if src.startswith("data:image/") and ";base64," in src:
                try:
                    return base64.b64decode(src.split(";base64,", 1)[1])
                except Exception:
                    pass
            from PyQt6.QtCore import QUrl
            u = QUrl(src)
            path = u.toLocalFile() or urllib.parse.unquote(u.path())
            resolved = resolve_image_path(path) if path else None
            if resolved and os.path.isfile(resolved):
                ext = os.path.splitext(resolved)[1].lower()
                if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff'):
                    try:
                        with open(resolved, "rb") as f:
                            return f.read()
                    except Exception:
                        pass

    # 4. Plain text (file path, vault relative path, or markdown link)
    if md.hasText():
        for line in md.text().splitlines():
            line = line.strip()
            if not line:
                continue
            resolved = resolve_image_path(line)
            if resolved and os.path.isfile(resolved):
                try:
                    with open(resolved, "rb") as f:
                        return f.read()
                except Exception:
                    pass

    # 5. Check custom MIME formats (e.g. Obsidian internal drag items)
    for fmt in md.formats():
        if "obsidian" in fmt.lower() or "uri" in fmt.lower() or "text" in fmt.lower():
            try:
                import re
                raw_data = bytes(md.data(fmt)).decode("utf-8", errors="ignore").strip()
                if raw_data:
                    for token in re.split(r'[\r\n\"\'<>\s]+', raw_data):
                        resolved = resolve_image_path(token)
                        if resolved and os.path.isfile(resolved):
                            with open(resolved, "rb") as f:
                                return f.read()
            except Exception:
                pass

    return None
