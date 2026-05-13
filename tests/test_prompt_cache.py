"""Comprehensive test suite for Prompt Caching.

Tests:
  - PromptCache: get_or_build_prompt, get_or_build_tools, invalidation
  - Anthropic cache_control: system blocks, tool breakpoints, anthropic-beta header
  - Provider-specific message building
  - CacheStats tracking
  - AgenticLoop integration with PromptCache
  - MultiProviderClient Anthropic payload formation
  - Eviction under max_entries
  - Hash stability
"""
from __future__ import annotations

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompt_cache import (
    APPROX_CHARS_PER_TOKEN,
    CacheStats,
    PromptCache,
    _content_hash,
    _list_hash,
)


# ─── Hash helpers ───────────────────────────────────────────────────


def test_content_hash_deterministic():
    h1 = _content_hash("hello world")
    h2 = _content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 16


def test_content_hash_different_inputs():
    h1 = _content_hash("hello")
    h2 = _content_hash("world")
    assert h1 != h2


def test_list_hash_deterministic():
    items = [{"name": "tool_a"}, {"name": "tool_b"}]
    h1 = _list_hash(items)
    h2 = _list_hash(items)
    assert h1 == h2


def test_list_hash_different_inputs():
    h1 = _list_hash([{"a": 1}])
    h2 = _list_hash([{"b": 2}])
    assert h1 != h2


# ─── CacheStats ─────────────────────────────────────────────────────


def test_cache_stats_to_dict():
    stats = CacheStats()
    stats.prompt_hits = 5
    stats.prompt_misses = 1
    stats.tools_hits = 3
    stats.tools_misses = 1
    d = stats.to_dict()
    assert d["total_hits"] == 8
    assert d["total_misses"] == 2
    assert d["hit_rate"] == 0.8


def test_cache_stats_zero_division():
    stats = CacheStats()
    d = stats.to_dict()
    assert d["hit_rate"] == 0.0
    assert d["total_hits"] == 0


# ─── PromptCache.get_or_build_prompt ────────────────────────────────


def test_prompt_cache_miss_calls_builder():
    cache = PromptCache()
    call_count = {"n": 0}

    def builder():
        call_count["n"] += 1
        return "System prompt text"

    result = cache.get_or_build_prompt(builder)
    assert result == "System prompt text"
    assert call_count["n"] == 1
    assert cache.stats.prompt_misses == 1
    assert cache.stats.prompt_hits == 0


def test_prompt_cache_hit_reuses():
    cache = PromptCache()
    call_count = {"n": 0}

    def builder():
        call_count["n"] += 1
        return "System prompt text"

    cache.get_or_build_prompt(builder)
    result2 = cache.get_or_build_prompt(builder)
    assert result2 == "System prompt text"
    assert call_count["n"] == 1
    assert cache.stats.prompt_hits == 1
    assert cache.stats.prompt_misses == 1


def test_prompt_cache_tracks_tokens_saved():
    cache = PromptCache()
    prompt = "A" * 1000

    cache.get_or_build_prompt(lambda: prompt)
    cache.get_or_build_prompt(lambda: prompt)
    cache.get_or_build_prompt(lambda: prompt)

    assert cache.stats.prompt_hits == 2
    expected_tokens = 2 * (1000 // APPROX_CHARS_PER_TOKEN)
    assert cache.stats.estimated_prompt_tokens_saved == expected_tokens


def test_prompt_cache_invalidate():
    cache = PromptCache()
    cache.get_or_build_prompt(lambda: "v1")
    cache.invalidate_prompt()

    call_count = {"n": 0}
    def builder():
        call_count["n"] += 1
        return "v2"

    result = cache.get_or_build_prompt(builder)
    assert result == "v2"
    assert call_count["n"] == 1


# ─── PromptCache.get_or_build_tools ─────────────────────────────────


def test_tools_cache_miss_calls_builder():
    cache = PromptCache()
    call_count = {"n": 0}

    def builder():
        call_count["n"] += 1
        return [{"type": "function", "function": {"name": "tool_a"}}]

    result = cache.get_or_build_tools(builder)
    assert len(result) == 1
    assert call_count["n"] == 1
    assert cache.stats.tools_misses == 1


def test_tools_cache_hit_reuses():
    cache = PromptCache()
    tools = [{"type": "function", "function": {"name": "tool_a"}}]

    cache.get_or_build_tools(lambda: tools)
    result2 = cache.get_or_build_tools(lambda: tools)
    assert result2 is tools
    assert cache.stats.tools_hits == 1


def test_tools_cache_invalidate():
    cache = PromptCache()
    cache.get_or_build_tools(lambda: [{"name": "v1"}])
    cache.invalidate_tools()

    result = cache.get_or_build_tools(lambda: [{"name": "v2"}])
    assert result == [{"name": "v2"}]


# ─── Anthropic cache_control ────────────────────────────────────────


def test_anthropic_system_has_cache_control():
    cache = PromptCache()
    prompt = "Large system prompt with many tools described"
    result = cache.get_anthropic_system(prompt)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == prompt
    assert result[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_system_cached():
    cache = PromptCache()
    prompt = "System prompt"
    r1 = cache.get_anthropic_system(prompt)
    r2 = cache.get_anthropic_system(prompt)
    assert r1 is r2
    assert cache.stats.anthropic_hits == 1
    assert cache.stats.anthropic_misses == 1


def test_anthropic_tools_last_has_cache_control():
    cache = PromptCache()
    tools = [
        {"type": "function", "function": {"name": "a"}},
        {"type": "function", "function": {"name": "b"}},
        {"type": "function", "function": {"name": "c"}},
    ]
    result = cache.get_anthropic_tools(tools)
    assert len(result) == 3
    assert "cache_control" not in result[0]
    assert "cache_control" not in result[1]
    assert result[2]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_tools_cached():
    cache = PromptCache()
    tools = [{"type": "function", "function": {"name": "a"}}]
    r1 = cache.get_anthropic_tools(tools)
    r2 = cache.get_anthropic_tools(tools)
    assert r1 is r2


def test_anthropic_tools_empty():
    cache = PromptCache()
    assert cache.get_anthropic_tools([]) == []


def test_anthropic_tools_does_not_mutate_original():
    cache = PromptCache()
    tools = [{"type": "function", "function": {"name": "x"}}]
    result = cache.get_anthropic_tools(tools)
    assert "cache_control" not in tools[0]
    assert "cache_control" in result[0]


# ─── Provider-specific messages ─────────────────────────────────────


def test_system_message_anthropic():
    cache = PromptCache()
    msg = cache.get_system_message("prompt", provider="anthropic")
    assert msg["role"] == "system"
    assert msg["content"] == "prompt"
    assert "_anthropic_system" in msg
    assert isinstance(msg["_anthropic_system"], list)


def test_system_message_openai():
    cache = PromptCache()
    msg = cache.get_system_message("prompt", provider="openai")
    assert msg["role"] == "system"
    assert msg["content"] == "prompt"
    assert "_anthropic_system" not in msg


def test_system_message_gemini():
    cache = PromptCache()
    msg = cache.get_system_message("prompt", provider="gemini")
    assert msg == {"role": "system", "content": "prompt"}


def test_tools_for_provider_anthropic():
    cache = PromptCache()
    tools = [{"type": "function", "function": {"name": "a"}}]
    result = cache.get_tools_for_provider(tools, provider="anthropic")
    assert result[-1].get("cache_control") == {"type": "ephemeral"}


def test_tools_for_provider_openai():
    cache = PromptCache()
    tools = [{"type": "function", "function": {"name": "a"}}]
    result = cache.get_tools_for_provider(tools, provider="openai")
    assert result is tools


# ─── Eviction ───────────────────────────────────────────────────────


def test_eviction_under_max_entries():
    cache = PromptCache(max_entries=3)
    for i in range(10):
        cache.get_anthropic_system(f"prompt_{i}")
    assert len(cache._anthropic_cache) <= 3


# ─── Clear ──────────────────────────────────────────────────────────


def test_clear_empties_all():
    cache = PromptCache()
    cache.get_or_build_prompt(lambda: "p")
    cache.get_or_build_tools(lambda: [{"a": 1}])
    cache.get_anthropic_system("s")
    cache.clear()
    assert len(cache._prompt_cache) == 0
    assert len(cache._tools_cache) == 0
    assert len(cache._anthropic_cache) == 0


# ─── MultiProviderClient Anthropic integration ─────────────────────


def test_anthropic_messages_with_cache_control():
    from multi_provider_client import MultiProviderClient
    client = MultiProviderClient()

    cache = PromptCache()
    system_prompt = "You are a test assistant"
    system_msg = cache.get_system_message(system_prompt, provider="anthropic")

    messages = [
        system_msg,
        {"role": "user", "content": "hello"},
    ]

    system, converted = client._to_anthropic_messages(messages)
    assert system == system_prompt
    assert len(converted) == 1
    assert converted[0]["role"] == "user"


def test_anthropic_tool_cache_control_forwarded():
    from multi_provider_client import MultiProviderClient
    client = MultiProviderClient()

    cache = PromptCache()
    tools = [
        {"type": "function", "function": {"name": "shell__run", "description": "Run shell", "parameters": {"type": "object", "properties": {}}}},
    ]
    cached_tools = cache.get_tools_for_provider(tools, provider="anthropic")
    assert cached_tools[0].get("cache_control") == {"type": "ephemeral"}

    anthropic_tools = client._to_anthropic_tools(cached_tools)
    assert len(anthropic_tools) == 1


# ─── AgenticLoop integration ───────────────────────────────────────


def test_agentic_loop_has_prompt_cache():
    from agentic_loop import AgenticLoop
    from multi_provider_client import MultiProviderClient
    from nodes_safety import SafetyNode
    from nodes_tool_router import ToolRouterNode
    from nodes_reflection import ReflectionNode

    loop = AgenticLoop(
        client=MultiProviderClient(),
        safety=SafetyNode("safety"),
        tool_router=ToolRouterNode("tool_router"),
        reflection=ReflectionNode("reflection"),
    )
    assert isinstance(loop.prompt_cache, PromptCache)


def test_agentic_loop_prompt_cache_is_used():
    from agentic_loop import AgenticLoop
    from multi_provider_client import MultiProviderClient
    from nodes_safety import SafetyNode
    from nodes_tool_router import ToolRouterNode
    from nodes_reflection import ReflectionNode

    loop = AgenticLoop(
        client=MultiProviderClient(),
        safety=SafetyNode("safety"),
        tool_router=ToolRouterNode("tool_router"),
        reflection=ReflectionNode("reflection"),
    )

    prompt1 = loop.prompt_cache.get_or_build_prompt(loop._system_prompt)
    prompt2 = loop.prompt_cache.get_or_build_prompt(loop._system_prompt)
    assert prompt1 == prompt2
    assert loop.prompt_cache.stats.prompt_hits == 1
    assert loop.prompt_cache.stats.prompt_misses == 1


def test_prompt_cache_tools_match_canonical():
    from canonical_tools import agentic_tool_definitions

    cache = PromptCache()
    tools1 = cache.get_or_build_tools(agentic_tool_definitions)
    tools2 = cache.get_or_build_tools(agentic_tool_definitions)

    assert tools1 is tools2
    assert len(tools1) > 10
    assert cache.stats.tools_hits == 1


def test_full_cache_lifecycle():
    """Simulate a multi-step run: prompt and tools built once, reused per step."""
    cache = PromptCache()

    prompt = cache.get_or_build_prompt(lambda: "X" * 3000)
    tools = cache.get_or_build_tools(lambda: [{"name": f"tool_{i}"} for i in range(35)])

    for step in range(10):
        p = cache.get_or_build_prompt(lambda: "should not be called")
        t = cache.get_or_build_tools(lambda: "should not be called")
        assert p == prompt
        assert t is tools

    stats = cache.stats.to_dict()
    assert stats["prompt_hits"] == 10
    assert stats["prompt_misses"] == 1
    assert stats["tools_hits"] == 10
    assert stats["tools_misses"] == 1
    assert stats["hit_rate"] > 0.9
    assert stats["estimated_prompt_tokens_saved"] > 0
    assert stats["estimated_tools_tokens_saved"] > 0


def test_cache_stats_across_providers():
    cache = PromptCache()
    prompt = "System prompt"

    cache.get_system_message(prompt, provider="anthropic")
    cache.get_system_message(prompt, provider="anthropic")
    cache.get_system_message(prompt, provider="openai")

    assert cache.stats.anthropic_misses == 1
    assert cache.stats.anthropic_hits == 1
