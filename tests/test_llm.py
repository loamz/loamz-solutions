import json

import httpx
import pytest

from loamz.solutions.kit.llm import LLM, LLMError


def llm(*answers):
    queue = list(answers)
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = queue.pop(0)
        if isinstance(content, int):
            return httpx.Response(content, text="nope")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return LLM("k", transport=httpx.MockTransport(handle)), seen


def test_json_parses_the_models_object_and_retries_once_on_junk():
    model, seen = llm("not json", '{"priority": "urgent"}')
    assert model.json("classify", "mail") == {"priority": "urgent"}
    assert len(seen) == 2 and seen[0]["response_format"] == {"type": "json_object"}
    assert "JSON object" in seen[0]["messages"][0]["content"]


def test_two_bad_answers_and_http_errors_raise():
    with pytest.raises(LLMError):
        llm("x", "[1]")[0].json("s", "u")
    with pytest.raises(LLMError, match="500"):
        llm(500)[0].text("s", "u")


def test_from_env_needs_a_key(monkeypatch):
    for name in ("LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(LLMError):
        LLM.from_env()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "m")
    assert LLM.from_env().model == "m"
