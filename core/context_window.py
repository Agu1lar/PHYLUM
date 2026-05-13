# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


class ContextWindowManager:
    """Manages LLM context window by compressing older messages to stay within
    a token budget.  Recent tool results are kept intact; older ones are
    selectively summarised, preserving paths, numbers and key identifiers
    while stripping narrative text.
    """

    CHARS_PER_TOKEN = 4  # conservative estimate for English/mixed text

    def __init__(
        self,
        *,
        max_context_tokens: int = 100_000,
        reserve_for_response: int = 8_000,
        recency_window: int = 4,
    ):
        self.max_context_tokens = max_context_tokens
        self.reserve_for_response = reserve_for_response
        self.recency_window = recency_window

    @property
    def token_budget(self) -> int:
        return self.max_context_tokens - self.reserve_for_response

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    def estimate_message_tokens(self, message: Dict[str, Any]) -> int:
        content = message.get("content") or ""
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        tokens = self.estimate_tokens(str(content))
        for tc in message.get("tool_calls") or []:
            args = tc.get("function", {}).get("arguments", "")
            tokens += self.estimate_tokens(str(args))
        for tb in message.get("_thinking_blocks") or []:
            tokens += self.estimate_tokens(str(tb.get("thinking", "")))
        return tokens + 4  # per-message overhead

    def total_tokens(self, messages: List[Dict[str, Any]]) -> int:
        return sum(self.estimate_message_tokens(m) for m in messages)

    # ------------------------------------------------------------------
    # Main entry point: compress messages to fit within budget
    # ------------------------------------------------------------------

    def compress_if_needed(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Return a (possibly compressed) copy of *messages* that fits the
        token budget.  The system message, user messages and the most recent
        *recency_window* tool-result messages are never compressed.
        """
        current = self.total_tokens(messages)
        if current <= self.token_budget:
            return messages

        compressed = list(messages)
        tool_indices = self._compressible_tool_indices(compressed)

        for idx in tool_indices:
            if self.total_tokens(compressed) <= self.token_budget:
                break
            compressed[idx] = self._compress_tool_message(compressed[idx])

        if self.total_tokens(compressed) > self.token_budget:
            compressed = self._drop_oldest_tool_results(compressed)

        return compressed

    # ------------------------------------------------------------------
    # Identify which tool-result messages are eligible for compression
    # ------------------------------------------------------------------

    def _compressible_tool_indices(
        self, messages: List[Dict[str, Any]]
    ) -> List[int]:
        """Return indices of tool-result messages oldest-first, excluding
        the *recency_window* most recent ones."""
        tool_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "tool"
        ]
        if len(tool_indices) <= self.recency_window:
            return []
        return tool_indices[: len(tool_indices) - self.recency_window]

    # ------------------------------------------------------------------
    # Selective compression of a single tool message
    # ------------------------------------------------------------------

    def _compress_tool_message(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        raw = message.get("content") or ""
        data = self._try_parse_json(raw)

        if data is not None:
            summary = self._summarize_tool_result(data)
        else:
            summary = self._compress_text(raw)

        return {**message, "content": summary}

    def _summarize_tool_result(self, data: Any) -> str:
        """Build a compact summary of a structured tool result, keeping
        status, paths, numbers and short strings; dropping long text."""
        if isinstance(data, dict):
            preserved = {}
            for key, value in data.items():
                preserved[key] = self._compact_value(key, value)
            return json.dumps(preserved, default=str, ensure_ascii=False)
        if isinstance(data, list):
            if len(data) <= 3:
                return json.dumps(
                    [self._compact_value("", item) for item in data],
                    default=str,
                    ensure_ascii=False,
                )
            head = [self._compact_value("", item) for item in data[:2]]
            return json.dumps(
                head, default=str, ensure_ascii=False
            ) + f" ... (+{len(data) - 2} more items)"
        return self._compress_text(str(data))

    def _compact_value(self, key: str, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if self._is_preservable(key, value):
                return value
            if len(value) <= 200:
                return value
            return self._compress_text(value)
        if isinstance(value, dict):
            return {k: self._compact_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            if len(value) <= 5:
                return [self._compact_value("", item) for item in value]
            head = [self._compact_value("", item) for item in value[:3]]
            return head + [f"(+{len(value) - 3} more)"]
        return str(value)[:200]

    # ------------------------------------------------------------------
    # Selective text compression — preserve structured data, drop prose
    # ------------------------------------------------------------------

    _PATH_RE = re.compile(
        r"[A-Za-z]:\\[^\s\"']+|\\\\[^\s\"']+|/[^\s\"']{4,}"
    )
    _NUM_RE = re.compile(r"\b\d[\d.,]+\b")
    _KEY_VALUE_RE = re.compile(r"[\w_]+\s*[:=]\s*\S+")

    def _compress_text(self, text: str, max_len: int = 300) -> str:
        if len(text) <= max_len:
            return text

        preserved: List[str] = []

        for match in self._PATH_RE.finditer(text):
            preserved.append(match.group())
        for match in self._NUM_RE.finditer(text):
            preserved.append(match.group())
        for match in self._KEY_VALUE_RE.finditer(text):
            if len(match.group()) < 100:
                preserved.append(match.group())

        seen = set()
        unique: List[str] = []
        for item in preserved:
            if item not in seen:
                seen.add(item)
                unique.append(item)

        extracted = " | ".join(unique[:20])

        prefix = text[:120].rstrip()
        if extracted:
            summary = f"{prefix}... [compressed] key data: {extracted}"
        else:
            summary = f"{prefix}... [compressed, {len(text)} chars original]"

        return summary[:max_len + 100]

    def _is_preservable(self, key: str, value: str) -> bool:
        """Values that should never be compressed."""
        preserve_keys = {
            "path", "file", "filename", "filepath", "output_path",
            "url", "href", "src", "command", "cmd", "status",
            "error", "stderr", "name", "id", "task_id", "tool",
            "action", "pid", "port", "host", "ip", "address",
        }
        if key.lower() in preserve_keys:
            return True
        if self._PATH_RE.search(value):
            return True
        if value.startswith(("http://", "https://", "\\\\")):
            return True
        return False

    # ------------------------------------------------------------------
    # Emergency: drop oldest tool results to fit budget
    # ------------------------------------------------------------------

    def _drop_oldest_tool_results(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """As a last resort, replace the oldest tool-result messages with a
        minimal placeholder until the budget is met."""
        tool_indices = self._compressible_tool_indices(messages)
        result = list(messages)

        for idx in tool_indices:
            if self.total_tokens(result) <= self.token_budget:
                break
            original_id = result[idx].get("tool_call_id", "")
            result[idx] = {
                "role": "tool",
                "tool_call_id": original_id,
                "content": '{"_summarized": true, "note": "result dropped to fit context window"}',
            }
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Any]:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
