"""Configurable LLM client supporting cloud and local providers.

All providers are reached over plain HTTP via httpx so the app carries no heavy
vendor SDKs and stays self-contained. Local runtimes (Ollama, LM Studio) expose
OpenAI-compatible endpoints and are handled by the same code path with a
different base_url.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from ..models import LLMSettings, LLMProvider

DEFAULT_BASE = {
    LLMProvider.ANTHROPIC: "https://api.anthropic.com",
    LLMProvider.OPENAI: "https://api.openai.com",
    LLMProvider.GOOGLE: "https://generativelanguage.googleapis.com",
    LLMProvider.OLLAMA: "http://localhost:11434",
    LLMProvider.LMSTUDIO: "http://localhost:1234",
}

# Suggestions surfaced in the UI; users may type any model id.
SUGGESTED_MODELS = {
    LLMProvider.ANTHROPIC: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    LLMProvider.OPENAI: ["gpt-4o", "gpt-4.1", "o3", "gpt-4o-mini"],
    LLMProvider.GOOGLE: ["gemini-2.5-pro", "gemini-2.5-flash"],
    LLMProvider.OLLAMA: ["qwen2.5-coder:7b", "llama3.1:8b", "qwen2.5:14b", "gpt-oss:20b"],
    LLMProvider.LMSTUDIO: ["local-model"],
}


class LLMError(Exception):
    pass


def _base_url(s: LLMSettings) -> str:
    return (s.base_url or DEFAULT_BASE[s.provider]).rstrip("/")


def list_local_models(provider: LLMProvider, base_url: str = "") -> list[str]:
    """Query a local runtime for installed models (best-effort)."""
    base = (base_url or DEFAULT_BASE.get(provider, "")).rstrip("/")
    try:
        with httpx.Client(timeout=10.0) as c:
            if provider == LLMProvider.OLLAMA:
                data = c.get(f"{base}/api/tags").json()
                return [m["name"] for m in data.get("models", [])]
            # LM Studio / OpenAI-compatible
            data = c.get(f"{base}/v1/models").json()
            return [m["id"] for m in data.get("data", [])]
    except Exception:
        return []


def complete(system: str, user: str, s: LLMSettings) -> str:
    """Return the model's text completion as a string."""
    if s.provider == LLMProvider.ANTHROPIC:
        return _anthropic(system, user, s)
    if s.provider == LLMProvider.GOOGLE:
        return _google(system, user, s)
    # OpenAI, Ollama, LM Studio all speak the OpenAI chat API.
    return _openai_compatible(system, user, s)


def _anthropic(system: str, user: str, s: LLMSettings) -> str:
    url = f"{_base_url(s)}/v1/messages"
    headers = {
        "x-api-key": s.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": s.model,
        "max_tokens": s.max_output_tokens,
        "temperature": s.temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    with httpx.Client(timeout=300.0) as c:
        r = c.post(url, headers=headers, json=body)
    if r.status_code != 200:
        raise LLMError(f"Anthropic error {r.status_code}: {r.text[:400]}")
    data = r.json()
    return "".join(block.get("text", "") for block in data.get("content", []))


def _openai_compatible(system: str, user: str, s: LLMSettings) -> str:
    url = f"{_base_url(s)}/v1/chat/completions"
    headers = {"content-type": "application/json"}
    if s.api_key:
        headers["Authorization"] = f"Bearer {s.api_key}"
    body = {
        "model": s.model,
        "temperature": s.temperature,
        "max_tokens": s.max_output_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    with httpx.Client(timeout=600.0) as c:
        r = c.post(url, headers=headers, json=body)
    if r.status_code != 200:
        raise LLMError(f"LLM error {r.status_code}: {r.text[:400]}")
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _google(system: str, user: str, s: LLMSettings) -> str:
    url = f"{_base_url(s)}/v1beta/models/{s.model}:generateContent?key={s.api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": s.temperature,
            "maxOutputTokens": s.max_output_tokens,
        },
    }
    with httpx.Client(timeout=300.0) as c:
        r = c.post(url, json=body)
    if r.status_code != 200:
        raise LLMError(f"Google error {r.status_code}: {r.text[:400]}")
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)
