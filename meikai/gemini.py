"""Gemini API client — parallel key dispatch (up to 3), model cascade.

Design:
- For the fastest model tier, fire up to 3 fresh keys simultaneously.
  First response wins. This cuts latency dramatically without hammering quotas.
- Target: <3s. If it takes longer for some reason, we still accept the result.
- If all 3 keys fail (rate-limited / server error), try next model tier.
- HTTP 404 on a model → that model is blacklisted for this process session.
- Per-minute 429 → 35s cooldown on that key (doesn't block other keys).
- Daily quota → cooldown until midnight PT.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import SHARED_KEYS_FILE, SHARED_COOLDOWNS_FILE

# ── JSON escape fixer ──────────────────────────────────────────────────────────
def _fix_json_escapes(s: str) -> str:
    """
    Fix Gemini's invalid JSON: LaTeX backslashes like \\frac, \\beta, \\text,
    \\uparrow, \\nabla must be kept as literal LaTeX commands, NOT evaluated as
    JSON control characters (formfeed, backspace, tab, or invalid \\u escapes).
    Inside JSON string values, double any backslash that does not escape a quote.
    """
    out = []
    in_string = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if not in_string:
            if c == '"':
                in_string = True
            out.append(c)
            i += 1
        else:
            if c == '\\':
                if i + 1 < n:
                    nxt = s[i + 1]
                    if nxt == '"':
                        # Valid escaped quote
                        out.append('\\"')
                        i += 2
                    elif nxt == '\\':
                        # Already doubled backslash
                        out.append('\\\\')
                        i += 2
                    else:
                        # Any single LaTeX backslash (e.g. \frac, \beta, \uparrow)
                        # must be escaped so json.loads keeps the literal backslash!
                        out.append('\\\\')
                        i += 1
                else:
                    out.append('\\\\')
                    i += 1
            elif c == '"':
                in_string = False
                out.append(c)
                i += 1
            elif c == '\n':
                out.append('\\n')
                i += 1
            elif c == '\r':
                out.append('\\r')
                i += 1
            elif c == '\t':
                out.append('\\t')
                i += 1
            else:
                out.append(c)
                i += 1
    return ''.join(out)


def normalize_gemini_latex(s: str) -> str:
    """
    If Gemini (or a pasted snippet) produces pure LaTeX math mode where text is in \\text{...}
    and line breaks are \\\\ without dollar signs, normalize it into standard
    Markdown LaTeX ($...$ and $$...$$).
    Also formats display equations ($$...$$) cleanly on separate lines.
    """
    if not s or not s.strip():
        return ""

    # Decode literal escaped newlines if present
    if "\\n" in s:
        s = s.replace("\\n", "\n")

    # Check if the string uses \text{...} or \\ linebreaks with missing $ delimiters
    if ("\\text{" in s or "\\\\" in s) and s.count("$") < 2:
        # Split by LaTeX linebreaks \\ (accounting for optional [2ex] spacing)
        lines = re.split(r"\s*\\\\(?:\s*\[[^\]]*\])?\s*", s)
        out_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if this line is an options row: (A) ... (B) ... (C) ... (D) ...
            opt_match = re.search(r"(?:\\text\{\s*)?\(A\)(?:\s*\})?", line, re.I)
            if opt_match and ("(B)" in line or "\\text{(B)}" in line):
                opts_cleaned = re.sub(r"\\(?:hspace\{[^}]*\}|quad|qquad)", " ", line)
                pattern = r"(?:\\text\{\s*)?(\([A-D]\))(?:\s*\})?\s*([^(]*?)(?=(?:\\text\{\s*)?\([A-D]\)|$)"
                matches = list(re.finditer(pattern, opts_cleaned))
                if matches:
                    opt_parts = []
                    for m in matches:
                        letter = m.group(1)
                        val = m.group(2).strip()
                        val = re.sub(r"\\text\{([^}]+)\}", r"\1", val).strip()
                        if val and not val.startswith("$"):
                            val = f"${val}$"
                        opt_parts.append(f"{letter} {val}")
                    out_lines.append(" \\quad\\quad ".join(opt_parts))
                    continue

            # If the entire line is a formula with no \text{...}:
            if "\\text{" not in line:
                if not line.startswith("$$") and not line.startswith("\\["):
                    out_lines.append(f"$${line}$$")
                else:
                    out_lines.append(line)
                continue

            # Mixed line: contains \text{...} and math segments
            tokens = []
            last = 0
            for m in re.finditer(r"\\text\{([^}]*)\}", line):
                math_before = line[last:m.start()].strip()
                if math_before:
                    qm = re.match(r"^(Q\.?\s*\d+[:.]?)\s*(.*)", math_before)
                    if qm:
                        tokens.append(qm.group(1))
                        rem = qm.group(2).strip()
                        if rem:
                            tokens.append(f"${rem}$")
                    else:
                        tokens.append(f"${math_before}$")
                text_content = m.group(1)
                tokens.append(text_content)
                last = m.end()

            math_after = line[last:].strip()
            if math_after:
                tokens.append(f"${math_after}$")

            combined = "".join(tokens)
            combined = re.sub(r"\s*(\$[^$]+\$)\s*", r" \1 ", combined)
            combined = re.sub(r"\s+", " ", combined).strip()
            out_lines.append(combined)

        s = "\n\n".join(out_lines)

    # Ensure display math $$...$$ has proper block spacing
    s = re.sub(r"([^\n])\s*\$\$([\s\S]*?)\$\$\s*([^\n])", r"\1\n\n$$\2$$\n\n\3", s)
    return s.strip()


# ── Model cascade: fastest first ──────────────────────────────────────────────
MODEL_CASCADE = [
    # Tier 1 — ultra fast (<2s target)
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-2.0-flash-lite",
    # Tier 2 — balanced & highly capable
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.1-flash-image",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    # Tier 3 — heavier fallback
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]

# How many keys to fire in parallel per model tier
PARALLEL_KEYS = 3

# Per-process dead-model blacklist (confirmed 404 by all tried keys)
_dead_lock = threading.Lock()
_dead_models: set = set()


# ── Helpers ────────────────────────────────────────────────────────────────────
def _mask(k: str) -> str:
    return f"{k[:6]}...{k[-4:]}" if len(k) > 12 else k[:3] + "..."


def _detect_mime(data: bytes) -> str:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# ── Cooldown persistence ───────────────────────────────────────────────────────
_cooldown_lock = threading.Lock()


def _load_cooldowns() -> Dict[str, float]:
    try:
        if os.path.isfile(SHARED_COOLDOWNS_FILE):
            with open(SHARED_COOLDOWNS_FILE) as f:
                data = json.load(f)
            now = time.time()
            return {k: float(v) for k, v in data.items() if float(v) > now}
    except Exception:
        pass
    return {}


def _save_cooldown(key: str, secs: float) -> None:
    with _cooldown_lock:
        try:
            os.makedirs(os.path.dirname(SHARED_COOLDOWNS_FILE), exist_ok=True)
            cd = _load_cooldowns()
            cd[key] = time.time() + secs
            with open(SHARED_COOLDOWNS_FILE, "w") as f:
                json.dump(cd, f)
        except Exception:
            pass


def _clear_cooldown(key: str) -> None:
    with _cooldown_lock:
        try:
            cd = _load_cooldowns()
            if key in cd:
                del cd[key]
                with open(SHARED_COOLDOWNS_FILE, "w") as f:
                    json.dump(cd, f)
        except Exception:
            pass


def _secs_until_reset() -> float:
    try:
        import zoneinfo
        pt = zoneinfo.ZoneInfo("America/Los_Angeles")
        now_pt = datetime.datetime.now(pt)
        tomorrow = (now_pt + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60.0, (tomorrow - now_pt).total_seconds() + 60.0)
    except Exception:
        return 6 * 3600.0


# ── Error parsing based on Google Cloud Error Model & Gemini API specs ─────────
def _parse_http_error(code: int, body: str) -> Tuple[float, bool, bool, str]:
    """
    Returns (cooldown_secs, is_key_banned_forever, is_model_404, reason).
    Follows Google Cloud / Gemini REST API error model:
    - 401 UNAUTHENTICATED: Bad/revoked API key -> perm ban key
    - 403 PERMISSION_DENIED: Check if key blocked/suspended or service disabled
    - 400 INVALID_ARGUMENT: Bad payload/config -> DO NOT ban key
    - 404 NOT_FOUND: Model name not found -> mark model 404
    - 429 RESOURCE_EXHAUSTED: Rate limit (short wait) vs Daily quota (midnight PT)
    - 5xx INTERNAL/UNAVAILABLE/DEADLINE_EXCEEDED: Transient Google server error
    """
    try:
        err = json.loads(body).get("error", {})
    except Exception:
        err = {}

    msg = str(err.get("message", "")).lower()
    status = str(err.get("status", "")).upper()
    body_low = body.lower()

    # Extract ErrorInfo details
    reasons = []
    retry_delay = None
    for d in err.get("details", []):
        if isinstance(d, dict):
            if "reason" in d:
                reasons.append(str(d["reason"]).upper())
            if "retryDelay" in d:
                try:
                    retry_delay = float(str(d["retryDelay"]).rstrip("s"))
                except Exception:
                    pass

    # 1. Permanent Key Errors: 401 Unauthenticated, or 400/403 with API_KEY_INVALID
    is_key_auth_error = (
        code == 401 or
        status == "UNAUTHENTICATED" or
        "API_KEY_INVALID" in reasons or
        "API_KEY_SERVICE_BLOCKED" in reasons or
        "CONSUMER_SUSPENDED" in reasons or
        "api key not valid" in msg or
        "api_key_invalid" in body_low or
        "api key expired" in msg
    )
    if is_key_auth_error:
        return 86400.0 * 30, True, False, "Invalid or revoked API key"

    # 2. Permission Denied (403): Project restricted or API disabled
    if code == 403 or status == "PERMISSION_DENIED":
        if "project" in msg or "billing" in msg or "denied" in msg:
            return 86400.0 * 30, True, False, f"Permission denied: {err.get('message', '403 Forbidden')[:70]}"
        return 3600.0, False, False, f"HTTP 403: {err.get('message', 'Forbidden')[:70]}"

    # 3. Model not found (404):
    if code == 404 or status == "NOT_FOUND":
        return 0.0, False, True, "404 — model not found or unavailable"

    # 4. Quota / Rate limit (429 RESOURCE_EXHAUSTED):
    if code == 429 or status == "RESOURCE_EXHAUSTED" or "quota" in body_low:
        if "per_day" in body_low or "daily" in body_low or "free_tier_requests_per_day" in body_low:
            return _secs_until_reset(), False, False, "Daily quota reached (resets midnight PT)"
        if retry_delay is not None and retry_delay > 0:
            return retry_delay + 2.0, False, False, f"Rate limit (retry in {int(retry_delay)}s)"
        return 35.0, False, False, "Rate limit exceeded"

    # 5. Client Argument Error (400 INVALID_ARGUMENT):
    # This is a payload issue (image format, token limit, etc.), NOT an API key issue!
    if code == 400 or status == "INVALID_ARGUMENT":
        return 0.0, False, False, f"Bad request: {err.get('message', 'Invalid argument')[:80]}"

    # 6. Transient Server Errors (500, 502, 503, 504):
    if code in (500, 502, 503, 504) or status in ("INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED"):
        return 12.0, False, False, f"Google server high traffic ({code or status})"

    return 30.0, False, False, f"HTTP {code}: {err.get('message', '')[:60]}"


# ── Key loading ────────────────────────────────────────────────────────────────
_key_cache: Optional[List[Dict]] = None
_key_cache_mtime: float = 0.0
_key_cache_lock = threading.Lock()


def _load_keys_cached() -> List[Dict[str, str]]:
    global _key_cache, _key_cache_mtime
    with _key_cache_lock:
        try:
            mtime = os.path.getmtime(SHARED_KEYS_FILE) if os.path.isfile(SHARED_KEYS_FILE) else 0.0
        except Exception:
            mtime = 0.0
        if _key_cache is not None and mtime <= _key_cache_mtime:
            return _key_cache
        pool: List[Dict[str, str]] = []
        seen = set()
        for env in ("MEIKAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            v = os.environ.get(env, "").strip()
            if v and v not in seen:
                pool.append({"key": v, "label": f"Env({env})"})
                seen.add(v)
        if os.path.isfile(SHARED_KEYS_FILE):
            try:
                with open(SHARED_KEYS_FILE) as f:
                    data = json.load(f)
                for item in data.get("keys", []):
                    k = item.get("key", "").strip() if isinstance(item, dict) else str(item).strip()
                    lbl = item.get("label", "") if isinstance(item, dict) else ""
                    if k and k not in seen:
                        pool.append({"key": k, "label": lbl or _mask(k)})
                        seen.add(k)
            except Exception:
                pass
        _key_cache = pool
        _key_cache_mtime = mtime
        return pool


def load_all_keys() -> List[Dict[str, str]]:
    return _load_keys_cached()


# ── Core single request ────────────────────────────────────────────────────────
def _single_request(
    model: str,
    api_key: str,
    payload_bytes: bytes,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    One HTTP request to Gemini. No hard timeout rejection — we wait up to
    `timeout` seconds and accept whatever comes back.
    Returns {"ok": True/False, ...}.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url, data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode())

        candidates = raw.get("candidates", [])
        if not candidates:
            # Check for safety filter block on prompt
            feedback = raw.get("promptFeedback", {})
            block_reason = feedback.get("blockReason", "")
            if block_reason:
                return {
                    "ok": False, "code": 200, "cooldown": 0.0, "perm": False,
                    "is_model_404": False,
                    "reason": f"Prompt blocked by Google safety filter ({block_reason})",
                    "key": api_key, "model": model,
                }
            return {
                "ok": False, "code": 200, "cooldown": 0.0, "perm": False,
                "is_model_404": False,
                "reason": "Empty candidates list from Gemini",
                "key": api_key, "model": model,
            }

        cand = candidates[0]
        finish_reason = cand.get("finishReason", "STOP")
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()

        if not text:
            if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
                return {
                    "ok": False, "code": 200, "cooldown": 0.0, "perm": False,
                    "is_model_404": False,
                    "reason": f"Response filtered by Google: {finish_reason}",
                    "key": api_key, "model": model,
                }
            return {
                "ok": False, "code": 200, "cooldown": 0.0, "perm": False,
                "is_model_404": False,
                "reason": f"No text content returned (finishReason: {finish_reason})",
                "key": api_key, "model": model,
            }

        return {"ok": True, "text": text, "key": api_key, "model": model}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        cd, perm, is_404, reason = _parse_http_error(e.code, body)
        return {
            "ok": False, "code": e.code, "cooldown": cd, "perm": perm,
            "is_model_404": is_404, "reason": reason,
            "key": api_key, "model": model,
        }
    except Exception as ex:
        return {
            "ok": False, "code": 0, "cooldown": 10.0, "perm": False,
            "is_model_404": False, "reason": str(ex)[:120],
            "key": api_key, "model": model,
        }


# ── Per-tier parallel dispatch ─────────────────────────────────────────────────
def _dispatch_tier(
    model: str,
    eligible_keys: List[str],
    payload_bytes: bytes,
    cooldowns: Dict[str, float],
    perm_failed: set,
    on_status: Optional[Callable],
    n_parallel: int = PARALLEL_KEYS,
    timeout: int = 15,
) -> Tuple[Optional[Dict], bool, List[str]]:
    """
    Fire `model` with up to `n_parallel` fresh keys simultaneously.
    Returns (winning_result_or_None, all_got_404, list_of_error_reasons).
    - all_got_404=True → model is dead, skip to next tier
    - all_got_404=False → transient failure (rate limit etc.), try next tier
    """
    # Pick the first n_parallel keys that are not cooled down / banned
    now = time.time()
    picked = [
        k for k in eligible_keys
        if k not in perm_failed and now >= cooldowns.get(k, 0.0)
    ][:n_parallel]

    if not picked:
        return None, False, ["All API keys currently on cooldown"]

    if on_status:
        on_status(f"⏳ {model} ({len(picked)} key{'s' if len(picked)>1 else ''})…")

    stop = threading.Event()
    winner: List[Optional[Dict]] = [None]
    lock = threading.Lock()
    n_404 = [0]
    error_reasons: List[str] = []

    def _run(api_key: str) -> None:
        if stop.is_set():
            return
        r = _single_request(model, api_key, payload_bytes, timeout)
        if stop.is_set():
            return
        with lock:
            if r["ok"] and winner[0] is None:
                winner[0] = r
                stop.set()
            elif not r["ok"]:
                reason = r.get("reason", "")
                if reason:
                    error_reasons.append(reason)
                if r.get("is_model_404"):
                    n_404[0] += 1
                else:
                    if r["cooldown"] > 0:
                        _save_cooldown(api_key, r["cooldown"])
                        cooldowns[api_key] = time.time() + r["cooldown"]
                    if r["perm"]:
                        perm_failed.add(api_key)

    ex = ThreadPoolExecutor(max_workers=len(picked))
    try:
        futs = [ex.submit(_run, k) for k in picked]
        for _ in as_completed(futs, timeout=timeout + 3):
            if stop.is_set():
                break
    finally:
        # Non-blocking shutdown cancels remaining pending tasks and does not hang the caller
        ex.shutdown(wait=False, cancel_futures=True)

    if winner[0]:
        _clear_cooldown(winner[0]["key"])
        return winner[0], False, error_reasons

    # All keys we tried returned 404 → model is dead
    all_404 = (n_404[0] == len(picked))
    return None, all_404, error_reasons


# ── Main client ───────────────────────────────────────────────────────────────
class GeminiClient:
    """
    Thread-safe Gemini client.
    - Target <3s response. We never reject a slow response — if it's late, we
      still use it (the student gets the answer, just a bit slower).
    - Up to PARALLEL_KEYS (3) keys fire simultaneously per model tier.
    - If all keys fail on a tier, cascade to the next model.
    - 404 models are blacklisted for this process session.
    """

    def __init__(self):
        self._perm_failed: set = set()
        self._cooldowns: Dict[str, float] = {}

    def _fresh_cooldowns(self) -> Dict[str, float]:
        self._cooldowns = _load_cooldowns()
        return self._cooldowns

    def get_key_count(self) -> int:
        return len(load_all_keys())

    def get_key_labels(self) -> List[str]:
        return [k.get("label", _mask(k.get("key", ""))) for k in load_all_keys()]

    def transcribe(
        self,
        image_bytes: bytes,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Returns {"latex": str, "tags": list, "model": str, "error": str|None}.
        Target: first response in <3s. We ALWAYS return whatever we get — even
        if it took longer than expected.
        """
        b64 = base64.b64encode(image_bytes).decode()
        mime = _detect_mime(image_bytes)

        prompt = (
            "You are an expert mathematical LaTeX transcription engine.\n"
            "Typeset and formulate all mathematical expressions, statements, and questions from the image into clean Markdown + LaTeX format.\n"
            "Do not include problem numbers (like \"10.\") or verbatim exam headers; formulate the mathematical statement and sub-parts clearly using LaTeX notation ($ and $$).\n\n"
            "CRITICAL FORMATTING RULES:\n"
            "1. Sentences and question statements MUST be written as standard text (NEVER wrap sentences in \\text{...} and NEVER use \\\\ for normal line breaks).\n"
            "2. ALL math symbols, variables, Greek letters, and formulas MUST be explicitly wrapped in dollar signs:\n"
            "   - Inline math: $...$ (e.g. $\\alpha$, $\\beta$, $x$, $\\alpha + \\beta$)\n"
            "   - Standalone display equations: $$...$$ on their own line (e.g. $$\\lim_{x \\to 0} \\frac{\\tan 2x - 2 \\sin \\alpha x}{x(1 - \\cos 2x)} = \\beta$$)\n"
            "3. Multiple-choice options or sub-parts MUST use standard notation: (a) ... (b) ...\n"
            "4. topic: Short descriptive title for the problem (<=45 chars).\n"
            "5. If the image has NO text or math at all, set \"latex\": \"\" and use \"topic\" to describe the visual subject.\n\n"
            "Respond ONLY with this JSON structure (no markdown fences, no extra text):\n"
            "{\n"
            '  "topic": "<short descriptive topic, <=45 chars>",\n'
            '  "latex": "<formulated problem in Markdown LaTeX with $ and $$>",\n'
            '  "tags": ["#tag1", "#tag2"]\n'
            "}"
        )

        payload = json.dumps({
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1,
                "maxOutputTokens": 4096,
            },
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in [
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                ]
            ],
        }).encode()

        all_keys = load_all_keys()
        if not all_keys:
            return {"latex": "", "tags": [], "model": "", "error": "No API keys configured."}

        cooldowns = self._fresh_cooldowns()
        perm_failed = set(self._perm_failed)
        key_strings = [ki["key"] for ki in all_keys]

        with _dead_lock:
            live_models = [m for m in MODEL_CASCADE if m not in _dead_models]

        server_overload_count = 0
        recitation_count = 0
        last_reason = ""

        for model in live_models:
            result, all_404, reasons = _dispatch_tier(
                model, key_strings, payload, cooldowns, perm_failed, on_status, timeout=12
            )
            if result and result.get("ok"):
                self._perm_failed = perm_failed
                return self._parse_response(result["text"], result["model"])
            if all_404:
                with _dead_lock:
                    _dead_models.add(model)
                continue

            if reasons:
                last_reason = reasons[0]

            has_503 = any("503" in r or "high traffic" in r or "UNAVAILABLE" in r or "Server error" in r for r in reasons)
            has_recitation = any("RECITATION" in r for r in reasons)

            if has_recitation:
                recitation_count += 1
                if recitation_count >= 2:
                    self._perm_failed = perm_failed
                    return {
                        "latex": "", "tags": [], "model": "",
                        "error": "Google copyright/recitation filter blocked verbatim extraction of this document. Try cropping around just the mathematical formula."
                    }

            if has_503:
                server_overload_count += 1
                if server_overload_count >= 2:
                    self._perm_failed = perm_failed
                    return {
                        "latex": "", "tags": [], "model": "",
                        "error": "Google Gemini servers are currently experiencing high traffic (503 Service Unavailable). Please try again in a minute."
                    }

        self._perm_failed = perm_failed
        err_msg = last_reason if last_reason else "All models exhausted. Please try again in a moment."
        return {
            "latex": "", "tags": [], "model": "",
            "error": err_msg
        }

    @staticmethod
    def _parse_response(text: str, model: str) -> Dict[str, Any]:
        """
        Parse Gemini JSON response robustly.
        Expected format:
          {"topic": "...", "latex": "...", "tags": [...]}
        Handles:
          - Valid JSON with "topic", "latex", "tags"
          - Empty "latex" when image contains no text/math
          - Double-encoded JSON
          - LaTeX backslash escapes (\frac, \beta, etc.)
          - Fallback to plain text only when Gemini didn't return JSON at all
        """
        # Strip markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

        latex = ""
        topic = ""
        tags: list = []
        parsed_json_successfully = False

        def _extract(s: str, depth: int = 0) -> None:
            """Recursively extract topic/latex/tags from s, updating outer vars."""
            nonlocal latex, topic, tags, parsed_json_successfully
            if depth > 4 or not s or not s.strip():
                return
            s = s.strip()

            obj = None
            # Try 1: direct parse
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                pass

            # Try 2: fix LaTeX backslash escapes then parse
            if obj is None:
                try:
                    obj = json.loads(_fix_json_escapes(s))
                except json.JSONDecodeError:
                    pass

            # Try 3: extract JSON object substring, then fix + parse
            if obj is None:
                m = re.search(r'\{.*\}', s, re.DOTALL)
                if m:
                    try:
                        obj = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        try:
                            obj = json.loads(_fix_json_escapes(m.group(0)))
                        except Exception:
                            pass

            if obj is None:
                return

            if isinstance(obj, str):
                # Decoded a JSON string — recurse into it
                _extract(obj, depth + 1)
                return

            if isinstance(obj, dict):
                parsed_json_successfully = True

                # Extract topic if present
                if not topic and "topic" in obj:
                    topic = str(obj.get("topic") or "").strip()

                # Extract tags
                raw_tags = obj.get("tags", [])
                if isinstance(raw_tags, list) and not tags:
                    tags = raw_tags

                # Extract latex: distinguish empty string from missing key
                if "latex" in obj:
                    val = obj.get("latex")
                    if val is None:
                        val = ""
                    val_str = str(val).strip()
                    # If the value itself looks like a nested JSON object, recurse
                    if val_str.startswith('{') and '"latex"' in val_str:
                        _extract(val_str, depth + 1)
                    else:
                        latex = val_str

        _extract(text)

        # Fallback to raw text ONLY if no JSON object was ever decoded
        if not parsed_json_successfully and not latex:
            latex = text

        latex = normalize_gemini_latex(str(latex).strip())
        topic = str(topic).strip()
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if t]

        return {"latex": latex, "topic": topic, "tags": tags, "model": model, "error": None}

    def generate_title(self, latex: str) -> str:
        """
        Ask Gemini for a short, descriptive plain-text title for the given LaTeX.
        Returns ≤50 chars. Uses fast models and tries all available keys if needed.
        """
        if not latex or not latex.strip():
            return ""

        prompt = (
            "Given this mathematical LaTeX content, generate a SHORT, descriptive title "
            "(plain text, no LaTeX markup, no markdown, ≤50 characters) that describes "
            "what math topic or problem it represents.\n"
            "Example outputs: 'Partial derivatives of multivariable function', "
            "'Matrix eigenvalue problem', 'Actuarial life table identity', 'Integration by parts'.\n"
            "Respond with ONLY the title — no quotes, no explanation, no markdown.\n\n"
            f"LaTeX content:\n{latex[:800]}"
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 64,
            },
        }).encode()

        all_keys = load_all_keys()
        if not all_keys:
            return ""

        cooldowns = self._fresh_cooldowns()
        perm_failed = set(self._perm_failed)
        now = time.time()

        models_to_try = [
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
            "gemini-3.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
        ]

        # Iterate eligible keys
        for ki in all_keys:
            k = ki["key"]
            if k in perm_failed or now < cooldowns.get(k, 0.0):
                continue
            for model in models_to_try:
                with _dead_lock:
                    if model in _dead_models:
                        continue
                r = _single_request(model, k, payload, timeout=6)
                if r.get("ok"):
                    title = r["text"].strip()
                    # Strip any markdown fences, bolding, quotes, or prefixes
                    title = re.sub(r'[*_`"]', '', title).strip()
                    title = re.sub(r'^(?:Title|Topic):\s*', '', title, flags=re.I).strip()
                    return title[:50].strip()
                elif r.get("is_model_404"):
                    with _dead_lock:
                        _dead_models.add(model)
                elif r.get("cooldown", 0) > 0:
                    _save_cooldown(k, r["cooldown"])
                    cooldowns[k] = time.time() + r["cooldown"]
                    break  # Try next available key
        return ""


# ── Singleton ─────────────────────────────────────────────────────────────────
_client: Optional[GeminiClient] = None
_client_lock = threading.Lock()


def get_client() -> GeminiClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = GeminiClient()
    return _client


def reload_client() -> GeminiClient:
    global _client
    with _client_lock:
        _client = GeminiClient()
        return _client
