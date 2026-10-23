"""A small client for any OpenAI-compatible chat endpoint (OpenAI, OpenRouter, a local server).

    llm = LLM.from_env()                      # LLM_API_KEY / OPENAI_API_KEY, LLM_BASE_URL, LLM_MODEL
    llm.json("Classify this email ...", schema_hint={"priority": "urgent|important|normal|low|noise"})
    llm.text("Summarise ...")

`json()` asks for a JSON object and parses it, retrying once when the model returns something else. No SDK is needed: the
dependency is `httpx`, which also makes the client easy to test with `httpx.MockTransport`.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.4-mini"


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE,
        model: str = DEFAULT_MODEL,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls, **options: Any) -> LLM:
        key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("Set LLM_API_KEY (or OPENAI_API_KEY) to use a model")
        return cls(
            key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            **options,
        )

    def chat(self, system: str, user: str, *, as_json: bool = False) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if as_json:
            body["response_format"] = {"type": "json_object"}
        try:
            response = self.http.post("/chat/completions", json=body)
        except httpx.TransportError as error:
            raise LLMError(f"The model endpoint is unreachable: {error}") from None
        if response.status_code >= 400:
            raise LLMError(f"The model answered {response.status_code}: {response.text[:300]}")
        return response.json()["choices"][0]["message"]["content"] or ""

    def text(self, system: str, user: str) -> str:
        return self.chat(system, user).strip()

    def json(self, system: str, user: str) -> dict[str, Any]:
        for _ in range(2):
            raw = self.chat(
                system + "\nAnswer with one JSON object and nothing else.", user, as_json=True
            )
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise LLMError("The model did not return a JSON object")
