"""Provider-agnostic LLM wrapper for classifying ONE unknown item into a signal.

Privacy: the payload is built ONLY from the item's title, domain, YouTube channel name (and app name for
non-browser apps). No URLs, paths, ids, query strings or other activity are ever included; `build_payload`
enforces this and tests check it.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

_URLISH = re.compile(r"https?://|www\.|[?&#=]|/", re.I)

SYSTEM_PROMPT = """You label ONE item of a person's computer activity with exactly one coarse signal.
Allowed signals (pick exactly one name from this list):
{signals}

Rules:
- Judge only from the given title / site / channel / app name.
- YouTube: decide from the title and channel whether the video teaches something (education) or entertains.
- If you cannot tell, use "other" with low confidence.
Reply with ONLY a JSON object: {{"signal": "<one of the allowed names>", "confidence": <0..1>, "reason": "<max 15 words>"}}"""


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class Answer:
    signal: str
    confidence: float
    reason: str


def build_payload(title: str | None, domain: str | None, channel: str | None, app: str | None) -> dict:
    """Minimal, URL-free payload. Anything URL-like is dropped rather than sent."""
    out = {}
    if title:
        t = re.sub(r"https?://\S+|www\.\S+", "", title)
        out["title"] = " ".join(t.split())[:200]
    if domain and not _URLISH.search(domain):
        out["site"] = domain
    if channel:
        out["channel"] = " ".join(channel.split())[:100]
    if app and not domain:
        out["app"] = app[:100]
    return out


def validate_answer(obj, allowed: list[str]) -> Answer:
    if not isinstance(obj, dict):
        raise LLMError("answer is not a JSON object")
    sig = obj.get("signal")
    if not isinstance(sig, str) or sig.strip().lower() not in allowed:
        raise LLMError(f"signal {sig!r} is not in the allowed list")
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        raise LLMError("confidence is not a number")
    if not 0.0 <= conf <= 1.0:
        raise LLMError("confidence out of range")
    reason = str(obj.get("reason") or "")[:200]
    return Answer(sig.strip().lower(), conf, reason)


class Provider(Protocol):
    name: str
    model: str

    def classify(self, payload: dict, allowed: list[str], descriptions: dict[str, str]) -> dict: ...


def _signal_list(allowed: list[str], descriptions: dict[str, str]) -> str:
    return "\n".join(f"- {s}: {descriptions.get(s, '')}".rstrip(": ") for s in allowed)


class OpenAICompatibleProvider:
    """Works with Groq, OpenAI, OpenRouter, a local Ollama/LM Studio server, ... (chat/completions + JSON mode)."""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 20.0,
                 extra_params: dict | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self._timeout = timeout
        self._extra = dict(extra_params or {})
        self._transport = transport

    def request_body(self, payload: dict, allowed: list[str], descriptions: dict[str, str]) -> dict:
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(signals=_signal_list(allowed, descriptions))},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            **self._extra,
        }

    def classify(self, payload: dict, allowed: list[str], descriptions: dict[str, str]) -> dict:
        body = self.request_body(payload, allowed, descriptions)
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as c:
                r = c.post(f"{self.base_url}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:
            raise LLMError(f"network error: {e.__class__.__name__}") from e
        if r.status_code == 429:
            raise LLMError("rate limited (429)")
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise LLMError("response was not valid JSON") from e


def provider_from_config(llm_cfg: dict) -> Provider | None:
    """None when no API key is configured: unknown items then wait in the queue."""
    key = os.environ.get(llm_cfg.get("api_key_env", "GROQ_API_KEY"), "").strip()
    if not key:
        return None
    return OpenAICompatibleProvider(
        base_url=llm_cfg.get("base_url", "https://api.groq.com/openai/v1"),
        model=llm_cfg.get("model", "openai/gpt-oss-20b"),
        api_key=key,
        timeout=float(llm_cfg.get("timeout_seconds", 20)),
        extra_params=llm_cfg.get("extra_params") or {},
    )


def now() -> float:
    return time.time()
