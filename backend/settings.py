"""User settings: the list of AI 'brains' JARVIS may use, in priority order.

If the first brain fails (quota, offline, bad key) JARVIS tries the next one.
Stored in data/settings.json on this PC only. API keys are never sent back to the browser.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "settings.json"

# Free limits change often and every model has its own daily quota, so model names are only suggestions.
PRESETS: list[dict[str, Any]] = [
    {"key": "ollama", "label": "Ollama (on this PC)", "kind": "ollama", "base_url": "http://127.0.0.1:11434",
     "model": "qwen2.5:7b", "needs_key": False,
     "models": ["qwen2.5-coder:7b", "qwen2.5:7b", "deepseek-r1:7b", "qwen2.5:14b", "llama3.1:8b"],
     "help": "Free, private, offline. For ultimate Python and coding: run 'ollama pull qwen2.5-coder:7b'"},
    {"key": "groq", "label": "Groq (free cloud)", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
     "model": "llama-3.3-70b-versatile", "needs_key": True,
     "models": ["llama-3.3-70b-versatile", "qwen-2.5-coder-32b", "deepseek-r1-distill-llama-70b", "llama-3.1-8b-instant"],
     "help": "Ultra-fast cloud AI. Sign in at console.groq.com, create a key. No card needed."},
    {"key": "gemini", "label": "Google Gemini (free)", "kind": "openai",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash",
     "needs_key": True, "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.5-flash-lite"],
     "help": "Get a free key at aistudio.google.com. Exceptional Python and multi-task reasoning."},
    {"key": "openrouter", "label": "OpenRouter (free models)", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
     "model": "meta-llama/llama-3.3-70b-instruct:free", "needs_key": True,
     "models": ["meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen-2.5-coder-32b-instruct:free", "deepseek/deepseek-r1:free"],
     "help": "Key at openrouter.ai/keys. Top free coding models with :free suffix."},
    {"key": "custom", "label": "Other (OpenAI-compatible)", "kind": "openai", "base_url": "", "model": "",
     "needs_key": True, "models": [], "help": "Any service that supports the OpenAI chat API."},
]


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _from_env() -> dict[str, Any]:
    """Backwards compatible: build the first brain from .env if settings.json does not exist yet."""
    kind = os.getenv("JARVIS_PROVIDER", "ollama").strip().lower()
    base = os.getenv("JARVIS_BASE_URL", "http://127.0.0.1:11434" if kind == "ollama" else "").rstrip("/")
    if kind == "ollama" and base.endswith("/v1"):
        base = base[:-3]
    return {"id": _new_id(), "label": "Main brain", "kind": kind, "base_url": base,
            "model": os.getenv("JARVIS_MODEL", "qwen2.5:7b"), "api_key": os.getenv("JARVIS_API_KEY", "").strip()}


def load() -> dict[str, Any]:
    if PATH.is_file():
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
            if isinstance(data.get("brains"), list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"brains": [_from_env()], "voice_lang": os.getenv("JARVIS_VOICE_LANG", "en-US")}


def save(data: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def brains() -> list[dict[str, Any]]:
    return [b for b in load()["brains"] if b.get("model") and (b["kind"] == "ollama" or b.get("base_url"))]


def public() -> dict[str, Any]:
    d = load()
    phone_cfg = d.get("phone_access", {"enabled": True, "password": "1234", "name": "JARVIS Link"})
    return {"voice_lang": d.get("voice_lang", "en-US"),
            "phone_access": phone_cfg,
            "brains": [{**{k: v for k, v in b.items() if k != "api_key"}, "has_key": bool(b.get("api_key"))}
                       for b in d["brains"]]}


def update(brains_in: list[dict[str, Any]], voice_lang: str | None = None, phone_access: dict[str, Any] | None = None) -> None:
    """Replace the brain list. A blank api_key keeps the stored key for the same id."""
    old = {b["id"]: b for b in load()["brains"]}
    clean = []
    for b in brains_in[:6]:
        bid = str(b.get("id") or _new_id())
        kind = "ollama" if b.get("kind") == "ollama" else "openai"
        key = str(b.get("api_key") or "").strip() or old.get(bid, {}).get("api_key", "")
        base = str(b.get("base_url") or "").strip().rstrip("/")
        if kind == "ollama" and base.endswith("/v1"):
            base = base[:-3]
        clean.append({"id": bid, "label": str(b.get("label") or "Brain")[:40], "kind": kind, "base_url": base,
                      "model": str(b.get("model") or "").strip(), "api_key": key})
    data = load()
    data["brains"] = clean
    if voice_lang:
        data["voice_lang"] = voice_lang
    if phone_access is not None:
        data["phone_access"] = phone_access
    save(data)


def get_brain(brain_id: str) -> dict[str, Any] | None:
    return next((b for b in load()["brains"] if b["id"] == brain_id), None)
