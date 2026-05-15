import httpx
import pytest

from multi_provider_client import MultiProviderClient, _parse_tool_arguments
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
async def test_groq_uses_openai_compatible_transport(monkeypatch):
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
            return FakeResponse({"choices": [{"message": {"content": "ok from groq"}}]})

    monkeypatch.setattr("multi_provider_client.httpx.AsyncClient", FakeClient)

    client = MultiProviderClient()
    sample_tools = [
        {"type": "function", "function": {"name": "shell", "description": "run shell", "parameters": {"type": "object"}}}
    ]
    result = await client.complete(
        provider="groq",
        api_key="gsk-test",
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "hello"}],
        tools=sample_tools,
        base_url=get_provider("groq").base_url,
    )

    assert result.content == "ok from groq"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer gsk-test"
    assert captured["json"]["max_completion_tokens"] == 2048
    assert "max_tokens" not in captured["json"]
    assert captured["json"]["disable_tool_validation"] is True


@pytest.mark.asyncio
async def test_groq_test_connection_lists_models(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse({"data": []})

    monkeypatch.setattr("multi_provider_client.httpx.AsyncClient", FakeClient)

    client = MultiProviderClient()
    result = await client.test_connection(
        provider="groq",
        api_key="gsk-test",
        base_url=get_provider("groq").base_url,
    )

    assert result["ok"] is True
    assert captured["url"] == "https://api.groq.com/openai/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer gsk-test"


@pytest.mark.asyncio
async def test_groq_retries_compact_on_failed_generation(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            calls.append(json)
            if len(calls) == 1:
                raise httpx.HTTPStatusError(
                    "failed_generation",
                    request=httpx.Request("POST", url),
                    response=httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": "Failed to call a function. See failed_generation",
                                "failed_generation": "<malformed>",
                            }
                        },
                    ),
                )
            return FakeResponse({"choices": [{"message": {"content": "Ola! Como posso ajudar?"}}]})

    monkeypatch.setattr("multi_provider_client.httpx.AsyncClient", FakeClient)

    client = MultiProviderClient()
    result = await client.complete(
        provider="groq",
        api_key="gsk-test",
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "ola"}],
        tools=[{"type": "function", "function": {"name": "shell", "description": "x", "parameters": {"type": "object"}}}],
        base_url=get_provider("groq").base_url,
    )

    assert "ajudar" in result.content.lower() or "ola" in result.content.lower()
    assert len(calls) == 2
    assert "tools" in calls[0]
    assert "tools" in calls[1]


@pytest.mark.asyncio
async def test_groq_retries_with_compact_tools_on_tpm_limit(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            calls.append(json)
            if len(calls) == 1:
                raise httpx.HTTPStatusError(
                    "tpm",
                    request=httpx.Request("POST", url),
                    response=httpx.Response(
                        413,
                        json={
                            "error": {
                                "message": "tokens per minute Limit 6000",
                                "type": "tokens",
                            }
                        },
                    ),
                )
            return FakeResponse({"choices": [{"message": {"content": "ok after compact"}}]})

    monkeypatch.setattr("multi_provider_client.httpx.AsyncClient", FakeClient)

    client = MultiProviderClient()
    result = await client.complete(
        provider="groq",
        api_key="gsk-test",
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": "ola"}],
        tools=[{"type": "function", "function": {"name": "shell", "description": "x" * 300, "parameters": {"type": "object"}}}],
        base_url=get_provider("groq").base_url,
    )

    assert result.content == "ok after compact"
    assert len(calls) == 2
    assert len(calls[0]["tools"][0]["function"]["description"]) > len(calls[1]["tools"][0]["function"]["description"])


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


def test_parse_tool_arguments_coerces_null_json():
    assert _parse_tool_arguments(None) == {}
    assert _parse_tool_arguments("null") == {}
    assert _parse_tool_arguments('{"action":"outlook_read_latest","unread_only":true}') == {
        "action": "outlook_read_latest",
        "unread_only": True,
    }
