"""Prompt Cache: avoids rebuilding and re-serializing large static payloads on every LLM call.

The system prompt (~3000+ tokens) and tool definitions (~35+ tools) are identical
across every step of an agentic loop run.  This module caches:

1. The built system prompt string (hash-keyed, invalidated if tools change)
2. The serialized tool definitions list
3. Provider-specific formatted payloads (Anthropic cache_control blocks, Gemini system_instruction)
4. Metrics: cache hits/misses, estimated token savings

The cache is entirely in-process (no disk I/O) and thread-safe for asyncio usage.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _list_hash(items: List[Dict[str, Any]]) -> str:
    raw = json.dumps(items, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


APPROX_CHARS_PER_TOKEN = 4


@dataclass
class CacheStats:
    prompt_hits: int = 0
    prompt_misses: int = 0
    tools_hits: int = 0
    tools_misses: int = 0
    anthropic_hits: int = 0
    anthropic_misses: int = 0
    estimated_prompt_tokens_saved: int = 0
    estimated_tools_tokens_saved: int = 0

    def to_dict(self) -> Dict[str, Any]:
        total_hits = self.prompt_hits + self.tools_hits + self.anthropic_hits
        total_misses = self.prompt_misses + self.tools_misses + self.anthropic_misses
        return {
            "prompt_hits": self.prompt_hits,
            "prompt_misses": self.prompt_misses,
            "tools_hits": self.tools_hits,
            "tools_misses": self.tools_misses,
            "anthropic_hits": self.anthropic_hits,
            "anthropic_misses": self.anthropic_misses,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "hit_rate": round(total_hits / max(total_hits + total_misses, 1), 4),
            "estimated_prompt_tokens_saved": self.estimated_prompt_tokens_saved,
            "estimated_tools_tokens_saved": self.estimated_tools_tokens_saved,
            "estimated_total_tokens_saved": self.estimated_prompt_tokens_saved + self.estimated_tools_tokens_saved,
        }


@dataclass
class _CacheEntry:
    value: Any
    content_hash: str
    created_at: float = field(default_factory=time.monotonic)
    access_count: int = 0
    char_count: int = 0


class PromptCache:
    """In-process cache for system prompts, tool definitions, and provider payloads.

    Usage:
        cache = PromptCache()

        # Cache the system prompt (built once, reused across steps)
        system_prompt = cache.get_or_build_prompt(builder_fn)

        # Cache tool definitions
        tools = cache.get_or_build_tools(builder_fn)

        # Get Anthropic-formatted system with cache_control
        anthropic_system = cache.get_anthropic_system(system_prompt)

        # Get stats
        stats = cache.stats.to_dict()
    """

    def __init__(self, *, max_entries: int = 32):
        self._prompt_cache: Dict[str, _CacheEntry] = {}
        self._tools_cache: Dict[str, _CacheEntry] = {}
        self._anthropic_cache: Dict[str, _CacheEntry] = {}
        self._provider_payload_cache: Dict[str, _CacheEntry] = {}
        self._max_entries = max_entries
        self.stats = CacheStats()

    def get_or_build_prompt(self, builder_fn) -> str:
        """Cache the system prompt string. builder_fn() is called only on miss."""
        cache_key = "system_prompt"
        entry = self._prompt_cache.get(cache_key)
        if entry is not None:
            entry.access_count += 1
            self.stats.prompt_hits += 1
            self.stats.estimated_prompt_tokens_saved += entry.char_count // APPROX_CHARS_PER_TOKEN
            return entry.value

        self.stats.prompt_misses += 1
        prompt = builder_fn()
        self._prompt_cache[cache_key] = _CacheEntry(
            value=prompt,
            content_hash=_content_hash(prompt),
            char_count=len(prompt),
        )
        return prompt

    def invalidate_prompt(self) -> None:
        self._prompt_cache.pop("system_prompt", None)
        self._anthropic_cache.clear()

    def get_or_build_tools(self, builder_fn) -> List[Dict[str, Any]]:
        """Cache the tool definitions list. builder_fn() is called only on miss."""
        cache_key = "tool_definitions"
        entry = self._tools_cache.get(cache_key)
        if entry is not None:
            entry.access_count += 1
            self.stats.tools_hits += 1
            serialized_len = entry.char_count
            self.stats.estimated_tools_tokens_saved += serialized_len // APPROX_CHARS_PER_TOKEN
            return entry.value

        self.stats.tools_misses += 1
        tools = builder_fn()
        serialized = json.dumps(tools, sort_keys=True, default=str)
        self._tools_cache[cache_key] = _CacheEntry(
            value=tools,
            content_hash=_list_hash(tools),
            char_count=len(serialized),
        )
        return tools

    def invalidate_tools(self) -> None:
        self._tools_cache.pop("tool_definitions", None)
        self._anthropic_cache.clear()

    def get_anthropic_system(self, system_prompt: str) -> Any:
        """Build Anthropic system payload with cache_control breakpoints.

        Anthropic's prompt caching uses cache_control blocks to mark content
        that should be cached on their servers. The system prompt is the ideal
        candidate since it's large and identical across all turns.

        Returns a list of content blocks suitable for the Anthropic 'system' field.
        """
        h = _content_hash(system_prompt)
        entry = self._anthropic_cache.get(h)
        if entry is not None:
            entry.access_count += 1
            self.stats.anthropic_hits += 1
            return entry.value

        self.stats.anthropic_misses += 1
        payload = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self._anthropic_cache[h] = _CacheEntry(
            value=payload,
            content_hash=h,
            char_count=len(system_prompt),
        )
        self._evict_if_needed(self._anthropic_cache)
        return payload

    def get_anthropic_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add cache_control to the last tool definition for Anthropic caching.

        Anthropic caches everything up to and including the last cache_control
        breakpoint. By marking the last tool, we cache both system + tools.
        """
        if not tools:
            return tools
        cache_key = f"anthropic_tools:{_list_hash(tools)}"
        entry = self._provider_payload_cache.get(cache_key)
        if entry is not None:
            entry.access_count += 1
            return entry.value

        cached_tools = []
        for i, tool in enumerate(tools):
            if i == len(tools) - 1:
                tool_copy = json.loads(json.dumps(tool))
                tool_copy["cache_control"] = {"type": "ephemeral"}
                cached_tools.append(tool_copy)
            else:
                cached_tools.append(tool)

        self._provider_payload_cache[cache_key] = _CacheEntry(
            value=cached_tools,
            content_hash=cache_key,
            char_count=len(json.dumps(cached_tools, default=str)),
        )
        self._evict_if_needed(self._provider_payload_cache)
        return cached_tools

    def get_system_message(self, system_prompt: str, *, provider: str) -> Dict[str, Any]:
        """Build a provider-appropriate system message, potentially with cache hints."""
        if provider == "anthropic":
            return {"role": "system", "content": system_prompt, "_anthropic_system": self.get_anthropic_system(system_prompt)}
        return {"role": "system", "content": system_prompt}

    def get_tools_for_provider(self, tools: List[Dict[str, Any]], *, provider: str) -> List[Dict[str, Any]]:
        """Return tools with provider-specific cache hints if applicable."""
        if provider == "anthropic":
            return self.get_anthropic_tools(tools)
        return tools

    def clear(self) -> None:
        self._prompt_cache.clear()
        self._tools_cache.clear()
        self._anthropic_cache.clear()
        self._provider_payload_cache.clear()

    def _evict_if_needed(self, cache: Dict[str, _CacheEntry]) -> None:
        if len(cache) <= self._max_entries:
            return
        entries = sorted(cache.items(), key=lambda kv: kv[1].access_count)
        to_remove = len(cache) - self._max_entries
        for key, _ in entries[:to_remove]:
            cache.pop(key, None)
