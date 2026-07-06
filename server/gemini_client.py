"""Gemini Flash client with a universal mock fallback.

OpenEvolve reaches Gemini through the OpenAI-compatible endpoint; we mirror that
here using plain ``requests`` (no heavy SDK). EVERY call site passes a ``mock``
value/callable that is returned when MOCK_MODE is on, no key is present, or the
API errors — so the app never breaks without a key.
"""
from __future__ import annotations

import json
import re
from typing import Callable, List, Dict, Optional, Union

import config

try:
    import requests  # optional; only needed for live mode
except Exception:  # pragma: no cover
    requests = None

MockLike = Union[str, Callable[[], str], None]


def _resolve_mock(mock: MockLike) -> str:
    if callable(mock):
        return mock()
    return mock if mock is not None else ""


def chat(messages: List[Dict[str, str]], mock: MockLike = None,
         temperature: float = 0.7, max_tokens: int = 1400) -> str:
    """Send a chat completion to Gemini Flash, or return the mock fallback.

    ``messages`` is an OpenAI-style list of {role, content} dicts.
    """
    if not config.LIVE_ENABLED or requests is None:
        return _resolve_mock(mock)
    try:
        payload = {
            "model": config.GEMINI_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Disable Gemini 2.5 "thinking" so long code/JSON outputs aren't truncated.
        if config.GEMINI_REASONING_EFFORT:
            payload["reasoning_effort"] = config.GEMINI_REASONING_EFFORT
        resp = requests.post(
            f"{config.GEMINI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.GEMINI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # transparent fallback keeps the demo alive
        print(f"[gemini] live call failed, using mock: {exc}")
        return _resolve_mock(mock)


def complete(system: str, user: str, mock: MockLike = None,
             temperature: float = 0.7, max_tokens: int = 1400) -> str:
    return chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        mock=mock, temperature=temperature, max_tokens=max_tokens,
    )


def extract_code_block(text: str, lang: str = "python") -> str:
    """Return the first fenced code block (```lang ... ```), else "" .

    Robust fallback for pulling program source out of an LLM response when the
    model returns code instead of (or alongside) JSON.
    """
    if not text:
        return ""
    m = re.search(r"```(?:" + re.escape(lang) + r"|py)?\s*\n(.*?)```", text,
                  re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def extract_all_code_blocks(text: str) -> list:
    if not text:
        return []
    return [b.strip() for b in
            re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)]


def extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a possibly fenced/annotated response."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except Exception:
        return None
