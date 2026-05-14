from __future__ import annotations

import pytest


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeAsyncClient:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        _FakeAsyncClient.calls += 1
        return _FakeResponse(
            """
            <html>
              <a href="https://random-blog.example/powershell-printers">Blog result</a>
              <a href="https://stackoverflow.com/questions/123/powershell-printers">StackOverflow answer</a>
              <a href="https://learn.microsoft.com/powershell/module/printmanagement/get-printer">Get-Printer docs</a>
            </html>
            """
        )


class _FailingAsyncClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("network should not be used on cache hit")


@pytest.fixture
def web_tool(tmp_path):
    from agent_persistence import Persistence
    from tool_web import WebTool
    from world_model import WorldModel

    persistence = Persistence(db_path=str(tmp_path / "web-cache.db"))
    return WebTool(world_model=WorldModel(persistence=persistence))


@pytest.mark.asyncio
async def test_search_web_ranks_quality_sources_and_caches(monkeypatch, web_tool):
    import tool_web

    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(tool_web.httpx, "AsyncClient", _FakeAsyncClient)

    result = await web_tool.run({"action": "search_web", "query": "discover printers powershell"}, retries=1)

    assert result.success is True
    assert result.details["cache_hit"] is False
    assert result.details["candidates"][0]["hostname"] == "learn.microsoft.com"
    assert result.details["candidates"][1]["hostname"] == "stackoverflow.com"
    assert _FakeAsyncClient.calls == 1

    cached = await web_tool.world_model.get(
        "web_resource",
        web_tool._search_cache_key("discover printers powershell"),
        record_hit=False,
    )
    assert cached is not None
    assert cached.value["purpose"] == "autonomous_discovery_web_search"


@pytest.mark.asyncio
async def test_search_web_uses_world_model_cache_before_network(monkeypatch, web_tool):
    import tool_web

    await web_tool._cache_search(
        "how to discover printers powershell",
        {
            "query": "how to discover printers powershell",
            "candidates": [
                {"url": "https://learn.microsoft.com/powershell/module/printmanagement/get-printer", "text": "Get-Printer docs"}
            ],
        },
    )
    monkeypatch.setattr(tool_web.httpx, "AsyncClient", _FailingAsyncClient)

    result = await web_tool.run({"action": "search_web", "query": "how to discover printers powershell"}, retries=1)

    assert result.success is True
    assert result.details["cache_hit"] is True
    assert result.details["candidates"][0]["hostname"] == "learn.microsoft.com"


def test_system_prompt_instructs_web_learning():
    from core.agentic_loop import AgenticLoop

    prompt = AgenticLoop._system_prompt(None)

    assert "web" in prompt.lower()
    assert "search" in prompt.lower() or "fetch" in prompt.lower()
