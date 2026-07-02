"""OpenAI-compatible LLM text normalization for OmniVoice TTS.

Reads/writes a small INI config in the script's CWD (omnivoice.ini),
and exposes a `normalize_text` helper used by the Gradio demo.
"""

import configparser
import json
import logging
import os
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_INI_NAME = "omnivoice.ini"

# Default endpoint & model — override via INI / UI.
DEFAULTS = {
    "enabled": "false",
    "base_url": "https://opencode.ai/zen/v1",
    "api_key": "public",
    "model": "deepseek-v4-flash-free",
    "extra_headers": "x-opencode-client: desktop",
    "system_prompt": (
        "You are a text normalizer for a high-quality text-to-speech system.\n"
        "Your job is to LIGHTLY format the text — DO NOT rewrite, paraphrase,\n"
        "shorten, or merge sentences. Keep the speaker's exact words.\n"
        "Rules:\n"
        "1. Output the same language as the input — never translate.\n"
        "2. Expand symbols to spoken form (e.g. 2024 -> two thousand twenty four,\n"
        "    $5 -> five dollars, 50% -> fifty percent, @ -> at).\n"
        "3. Insert punctuation that improves prosody: commas for short pauses,\n"
        "    periods for sentence breaks, ellipsis for hesitation.\n"
        "4. Keep proper nouns verbatim. Do NOT add commentary.\n"
        "5. Keep the output roughly the same length as the input. If unsure,\n"
        "    return the input unchanged.\n"
        "6. Return ONLY the normalized text, no quotes, no prefix."
    ),
    "timeout": "60",
}

_lock = threading.Lock()
_cache: Optional[configparser.ConfigParser] = None
_cached_path: Optional[str] = None


def _ini_path() -> str:
    return os.path.join(os.getcwd(), DEFAULT_INI_NAME)


def _make_cfg() -> configparser.ConfigParser:
    # Use RawConfigParser so '%' in prompts is literal, not interpolation.
    return configparser.RawConfigParser()


def load_config(
    path: Optional[str] = None, force_reload: bool = False
) -> configparser.ConfigParser:
    """Load INI config. Creates one with defaults if missing."""
    global _cache, _cached_path
    p = path or _ini_path()
    with _lock:
        if _cache is not None and _cached_path == p and not force_reload:
            return _cache
        cfg = _make_cfg()
        file_exists = os.path.exists(p)
        if file_exists:
            try:
                cfg.read(p, encoding="utf-8")
            except configparser.ParsingError as e:
                logger.warning(
                    "INI %s is malformed (%s); recreating with defaults.",
                    p, e,
                )
                file_exists = False
                try:
                    os.remove(p)
                except OSError:
                    pass
        if "llm" not in cfg:
            cfg["llm"] = {}
        for k, v in DEFAULTS.items():
            cfg["llm"].setdefault(k, v)
        # Persist the INI on first load so the file always exists and the
        # user can edit it directly.
        if not file_exists:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    cfg.write(f)
            except OSError as e:
                logger.warning("Could not write default INI %s: %s", p, e)
        _cache = cfg
        _cached_path = p
        return cfg


def save_config(
    enabled: bool,
    base_url: str,
    api_key: str,
    model: str,
    extra_headers: str,
    system_prompt: str,
    timeout: float,
    path: Optional[str] = None,
) -> str:
    global _cache, _cached_path
    p = path or _ini_path()
    cfg = _make_cfg()
    if os.path.exists(p):
        cfg.read(p, encoding="utf-8")
    if "llm" not in cfg:
        cfg["llm"] = {}
    cfg["llm"]["enabled"] = "true" if enabled else "false"
    cfg["llm"]["base_url"] = base_url
    cfg["llm"]["api_key"] = api_key
    cfg["llm"]["model"] = model
    cfg["llm"]["extra_headers"] = extra_headers
    # configparser has no real multi-line value support; collapse newlines
    # so a long prompt stays on one logical line and re-reads correctly.
    cfg["llm"]["system_prompt"] = " ".join(
        line.strip() for line in system_prompt.splitlines() if line.strip()
    )
    cfg["llm"]["timeout"] = str(timeout)
    with open(p, "w", encoding="utf-8") as f:
        cfg.write(f)
    with _lock:
        _cache = cfg
        _cached_path = p
    return p


def _parse_headers(raw: str) -> list:
    """Parse 'k1: v1, k2: v2' into a list of (k, v) tuples."""
    out = []
    if not raw:
        return out
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        out.append((k.strip(), v.strip()))
    return out


def _build_messages(text: str, system_prompt: str):
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]


def normalize_text(text: str, path: Optional[str] = None) -> str:
    """Normalize text via the configured LLM. Returns input on any failure."""
    if not text or not text.strip():
        return text
    try:
        import requests  # local import: optional dep
    except ImportError:
        logger.warning("requests not installed; skipping LLM normalize")
        return text

    cfg = load_config(path)
    if cfg["llm"].get("enabled", "false").lower() != "true":
        return text

    base_url = cfg["llm"].get("base_url", DEFAULTS["base_url"]).rstrip("/")
    api_key = cfg["llm"].get("api_key", DEFAULTS["api_key"])
    model = cfg["llm"].get("model", DEFAULTS["model"])
    system_prompt = cfg["llm"].get("system_prompt", DEFAULTS["system_prompt"])
    rules = cfg["llm"].get("rules", "").strip()
    if rules:
        system_prompt = f"{system_prompt}\n\n{rules}"
    try:
        timeout = float(cfg["llm"].get("timeout", DEFAULTS["timeout"]))
    except ValueError:
        timeout = 60.0
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    raw_headers = cfg["llm"].get("extra_headers", "")
    # Decide whether to attach extra_headers:
    #  - If user customized the field, send it.
    #  - Else, auto-add opencode.ai defaults if the base_url is opencode.
    if raw_headers and raw_headers != DEFAULTS["extra_headers"]:
        for k, v in _parse_headers(raw_headers):
            headers[k] = v
    elif "opencode.ai" in base_url:
        for k, v in _parse_headers(DEFAULTS["extra_headers"]):
            headers[k] = v

    payload = {
        "model": model,
        "messages": _build_messages(text, system_prompt),
        "stream": False,
        "temperature": 0.2,
    }
    url = f"{base_url}/chat/completions"
    r = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            break
        except Exception as e:
            last_exc = e
            wait = 0.5 * (2**attempt)
            logger.warning(
                "LLM normalize attempt %d/3 failed (%s); retrying in %.1fs",
                attempt + 1, e, wait,
            )
            import time as _time
            _time.sleep(wait)
    if r is None:
        logger.warning("LLM normalize giving up after 3 attempts: %s", last_exc)
        return text
    try:
        if r.status_code != 200:
            logger.warning("LLM normalize HTTP %s: %s", r.status_code, r.text[:200])
            return text
        data = r.json()
        out = data["choices"][0]["message"]["content"].strip()
        return out or text
    except Exception as e:
        logger.warning("LLM normalize failed (%s); passing through", e)
        return text


def normalize_batch(texts: List[str], path: Optional[str] = None) -> List[str]:
    """Normalize a list of strings in 1 LLM round-trip.

    Asks the model to return a JSON array of normalized strings, one per
    input line (same order, same length). On any failure, returns the
    original list unchanged.
    """
    if not texts:
        return texts
    # Fast path: feature off or empty.
    try:
        import requests
    except ImportError:
        return texts
    cfg = load_config(path, force_reload=True)
    if cfg["llm"].get("enabled", "false").lower() != "true":
        return texts

    base_url = cfg["llm"].get("base_url", DEFAULTS["base_url"]).rstrip("/")
    api_key = cfg["llm"].get("api_key", DEFAULTS["api_key"])
    model = cfg["llm"].get("model", DEFAULTS["model"])
    base_system = cfg["llm"].get("system_prompt", DEFAULTS["system_prompt"])
    try:
        timeout = float(cfg["llm"].get("timeout", DEFAULTS["timeout"]))
    except ValueError:
        timeout = 60.0
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    # Decide whether to attach extra_headers:
    #  - If user customized the field, send it.
    #  - Else, auto-add opencode.ai defaults if the base_url is opencode.
    raw_headers = cfg["llm"].get("extra_headers", "")
    if raw_headers and raw_headers != DEFAULTS["extra_headers"]:
        for k, v in _parse_headers(raw_headers):
            headers[k] = v
    elif "opencode.ai" in base_url:
        for k, v in _parse_headers(DEFAULTS["extra_headers"]):
            headers[k] = v

    system_prompt = (
        base_system
        + "\n\nYou will receive a JSON array of input strings. Apply the same"
        " rules to each and return ONLY a JSON array of the normalized"
        " strings, in the same order, with the same length. Do not add"
        " commentary. Do not wrap in markdown fences unless explicitly told."
    )
    user_payload = json.dumps(texts, ensure_ascii=False)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "stream": False,
        "temperature": 0.2,
    }
    url = f"{base_url}/chat/completions"
    # Retry transient network errors (ConnectionResetError, ChunkedEncodingError,
    # RemoteDisconnected, timeouts) with a short backoff. After retries are
    # exhausted, fall back to the original texts.
    last_exc: Optional[Exception] = None
    r = None
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            break
        except Exception as e:
            last_exc = e
            wait = 0.5 * (2**attempt)
            logger.warning(
                "LLM batch attempt %d/3 failed (%s); retrying in %.1fs",
                attempt + 1, e, wait,
            )
            import time as _time
            _time.sleep(wait)
    if r is None:
        logger.warning("LLM batch giving up after 3 attempts: %s", last_exc)
        return texts
    try:
        if r.status_code != 200:
            logger.warning("LLM batch HTTP %s: %s", r.status_code, r.text[:200])
            return texts
        data = r.json()
        out = data["choices"][0]["message"]["content"].strip()
        parsed = _extract_json_array(out)
        if not parsed or len(parsed) != len(texts):
            logger.warning(
                "LLM batch returned %d items, expected %d; passing through",
                0 if not parsed else len(parsed), len(texts),
            )
            return texts
        # Coerce to str, fallback to original on any None/empty.
        result = []
        for src, got in zip(texts, parsed):
            s = got if isinstance(got, str) else src
            result.append(s.strip() or src)
        return result
    except Exception as e:
        logger.warning("LLM batch normalize failed (%s); passing through", e)
        return texts


def _extract_json_array(text: str) -> Optional[List]:
    """Best-effort JSON array extraction from a model response.

    Tries strict parse first, then strips markdown fences, then finds the
    first '[' ... ']' substring.
    """
    import json as _json
    try:
        v = _json.loads(text)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    # Strip ```json ... ``` fences.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        v = _json.loads(cleaned)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    # Find first '[' and matching ']'.
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            v = _json.loads(text[start : end + 1])
            if isinstance(v, list):
                return v
        except Exception:
            return None
    return None


def describe_config(path: Optional[str] = None) -> dict:
    """Return current config as a dict (for the UI)."""
    cfg = load_config(path)
    s = cfg["llm"]
    return {
        "enabled": s.get("enabled", DEFAULTS["enabled"]).lower() == "true",
        "base_url": s.get("base_url", DEFAULTS["base_url"]),
        "api_key": s.get("api_key", DEFAULTS["api_key"]),
        "model": s.get("model", DEFAULTS["model"]),
        "extra_headers": s.get("extra_headers", DEFAULTS["extra_headers"]),
        "system_prompt": s.get("system_prompt", DEFAULTS["system_prompt"]),
        "timeout": float(s.get("timeout", DEFAULTS["timeout"])),
        "path": path or _ini_path(),
    }


def is_llm_settings_visible(path: Optional[str] = None) -> bool:
    """Return True if the LLM Settings tab should be rendered.

    Controlled by the `show_llm_settings` key in [llm]. Defaults to False
    so the tab is hidden out of the box; users can flip it in omnivoice.ini.
    """
    cfg = load_config(path)
    return cfg["llm"].get("show_llm_settings", "false").lower() == "true"
