import pytest

from multi_provider_client import MultiProviderClient
from provider_registry import get_provider


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http error {self.status_code}")


@pytest.mark.asyncio
async def test_openrouter_uses_openai_compatible_transport(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse({"choices": [{"message": {"content": "ok from openrouter"}}]})

    monkeypatch.setattr("multi_provider_client.httpx.AsyncClient", FakeClient)

    client = MultiProviderClient()
    result = await client.complete(
        provider="openrouter",
        api_key="or-key",
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        base_url=get_provider("openrouter").base_url,
    )

    assert result.content == "ok from openrouter"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer or-key"
    assert captured["headers"]["HTTP-Referer"] == "http://127.0.0.1:5173"
    assert captured["headers"]["X-Title"] == "PHYLUM"


@pytest.mark.asyncio
async def test_gemini_generate_content_supports_tools(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "preciso de mais contexto"},
                                    {
                                        "functionCall": {
                                            "id": "gemini-call-1",
                                            "name": "request_user_input",
                                            "args": {"prompt": "Qual opcao devo seguir?"},
                                        }
                                    },
                                ]
                            }
                        }
                    ]
                }
            )

    monkeypatch.setattr("multi_provider_client.httpx.AsyncClient", FakeClient)

    client = MultiProviderClient()
    result = await client.complete(
        provider="gemini",
        api_key="gem-key",
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "help me choose"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "request_user_input",
                    "description": "Ask the user for clarification.",
                    "parameters": {
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"],
                    },
                },
            }
        ],
        base_url=get_provider("gemini").base_url,
    )

    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert captured["headers"]["x-goog-api-key"] == "gem-key"
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "You are helpful."
    assert captured["json"]["tools"][0]["functionDeclarations"][0]["name"] == "request_user_input"
    assert result.content == "preciso de mais contexto"
    assert result.tool_calls[0].id == "gemini-call-1"
    assert result.tool_calls[0].name == "request_user_input"
    assert result.tool_calls[0].arguments["prompt"] == "Qual opcao devo seguir?"
