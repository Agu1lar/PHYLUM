# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for ContextWindowManager — token budgeting, selective compression,
sliding window with recency priority, and structured data preservation."""

from __future__ import annotations

import json
import pytest

from context_window import ContextWindowManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _system_msg(text: str = "You are a helpful assistant.") -> dict:
    return {"role": "system", "content": text}


def _user_msg(text: str = "Do something") -> dict:
    return {"role": "user", "content": text}


def _assistant_msg(text: str = "Sure.", tool_calls=None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_msg(tool_call_id: str, result: dict | str) -> dict:
    content = json.dumps(result, default=str) if isinstance(result, dict) else result
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _large_tool_result(n_chars: int = 5000) -> dict:
    return {
        "success": True,
        "stdout": "x" * n_chars,
        "stderr": "",
        "path": r"C:\Users\User\Documents\report.docx",
        "returncode": 0,
    }


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

class TestTokenEstimation:
    def test_estimate_tokens_basic(self):
        mgr = ContextWindowManager()
        assert mgr.estimate_tokens("abcd") == 1
        assert mgr.estimate_tokens("a" * 400) == 100

    def test_estimate_message_tokens_includes_tool_calls(self):
        mgr = ContextWindowManager()
        msg = {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [
                {"function": {"arguments": json.dumps({"path": "C:\\x"})}}
            ],
        }
        tokens = mgr.estimate_message_tokens(msg)
        assert tokens > mgr.estimate_tokens("ok")

    def test_total_tokens_sums_correctly(self):
        mgr = ContextWindowManager()
        msgs = [_system_msg(), _user_msg(), _assistant_msg()]
        total = mgr.total_tokens(msgs)
        individual = sum(mgr.estimate_message_tokens(m) for m in msgs)
        assert total == individual


# ---------------------------------------------------------------------------
# No compression when within budget
# ---------------------------------------------------------------------------

class TestNoCompression:
    def test_small_conversation_untouched(self):
        mgr = ContextWindowManager(max_context_tokens=100_000)
        messages = [_system_msg(), _user_msg(), _assistant_msg()]
        result = mgr.compress_if_needed(messages)
        assert result == messages

    def test_returns_same_list_when_within_budget(self):
        mgr = ContextWindowManager(max_context_tokens=100_000)
        messages = [_system_msg(), _user_msg()]
        assert mgr.compress_if_needed(messages) is messages


# ---------------------------------------------------------------------------
# Recency window — recent tool results are never compressed
# ---------------------------------------------------------------------------

class TestRecencyWindow:
    def test_recent_tool_messages_preserved(self):
        mgr = ContextWindowManager(
            max_context_tokens=500, reserve_for_response=100, recency_window=2
        )
        messages = [
            _system_msg("sys"),
            _user_msg("go"),
            _assistant_msg("ok", tool_calls=[{"id": "tc1", "function": {"name": "a", "arguments": "{}"}}]),
            _tool_msg("tc1", _large_tool_result(2000)),
            _assistant_msg("ok", tool_calls=[{"id": "tc2", "function": {"name": "b", "arguments": "{}"}}]),
            _tool_msg("tc2", _large_tool_result(2000)),
            _assistant_msg("ok", tool_calls=[{"id": "tc3", "function": {"name": "c", "arguments": "{}"}}]),
            _tool_msg("tc3", {"success": True, "note": "small"}),
        ]
        result = mgr.compress_if_needed(messages)
        recent_tool = next(m for m in result if m.get("tool_call_id") == "tc3")
        assert '"small"' in recent_tool["content"]

    def test_old_tool_messages_compressed(self):
        mgr = ContextWindowManager(
            max_context_tokens=500, reserve_for_response=100, recency_window=1
        )
        old_result = _large_tool_result(3000)
        messages = [
            _system_msg("sys"),
            _user_msg("go"),
            _assistant_msg("ok", tool_calls=[{"id": "tc-old", "function": {"name": "a", "arguments": "{}"}}]),
            _tool_msg("tc-old", old_result),
            _assistant_msg("ok", tool_calls=[{"id": "tc-new", "function": {"name": "b", "arguments": "{}"}}]),
            _tool_msg("tc-new", {"ok": True}),
        ]
        result = mgr.compress_if_needed(messages)
        old_msg = next(m for m in result if m.get("tool_call_id") == "tc-old")
        assert len(old_msg["content"]) < len(json.dumps(old_result))


# ---------------------------------------------------------------------------
# Selective compression — paths & numbers survive, prose is cut
# ---------------------------------------------------------------------------

class TestSelectiveCompression:
    def test_paths_preserved_in_compressed_text(self):
        mgr = ContextWindowManager()
        text = (
            "This is a very long narrative about what happened during execution. " * 20
            + r"The output was saved to C:\Users\User\Documents\output.xlsx and also to \\SERVER\share\data.csv."
        )
        compressed = mgr._compress_text(text, max_len=300)
        assert r"C:\Users\User\Documents\output.xlsx" in compressed
        assert r"\\SERVER\share\data.csv" in compressed

    def test_numbers_preserved(self):
        mgr = ContextWindowManager()
        text = "The process completed after 3,456 iterations with a result of 99.7% accuracy. " * 20
        compressed = mgr._compress_text(text, max_len=300)
        assert "3,456" in compressed

    def test_short_text_not_compressed(self):
        mgr = ContextWindowManager()
        short = "Command succeeded with exit code 0."
        assert mgr._compress_text(short) == short

    def test_structured_result_preserves_path(self):
        mgr = ContextWindowManager()
        data = {
            "success": True,
            "path": r"C:\Users\User\report.pdf",
            "stdout": "x" * 1000,
            "returncode": 0,
        }
        summary = mgr._summarize_tool_result(data)
        parsed = json.loads(summary)
        assert parsed["path"] == r"C:\Users\User\report.pdf"
        assert len(summary) < len(json.dumps(data))

    def test_preservable_keys_never_compressed(self):
        mgr = ContextWindowManager()
        for key in ("path", "url", "error", "command", "status", "id"):
            assert mgr._is_preservable(key, "any_value")

    def test_url_values_preserved_regardless_of_key(self):
        mgr = ContextWindowManager()
        assert mgr._is_preservable("random_key", "https://example.com/api/v1")


# ---------------------------------------------------------------------------
# Token budgeting — total stays within limits
# ---------------------------------------------------------------------------

class TestTokenBudgeting:
    def test_compressed_result_smaller_than_original(self):
        mgr = ContextWindowManager(
            max_context_tokens=500, reserve_for_response=100, recency_window=1
        )
        messages = [
            _system_msg("sys"),
            _user_msg("go"),
        ]
        for i in range(6):
            tc_id = f"tc-{i}"
            messages.append(
                _assistant_msg("ok", tool_calls=[{"id": tc_id, "function": {"name": "x", "arguments": "{}"}}])
            )
            messages.append(_tool_msg(tc_id, _large_tool_result(800)))

        original_tokens = mgr.total_tokens(messages)
        result = mgr.compress_if_needed(messages)
        compressed_tokens = mgr.total_tokens(result)
        assert compressed_tokens < original_tokens, (
            f"Compression should reduce tokens: {compressed_tokens} < {original_tokens}"
        )

    def test_budget_property(self):
        mgr = ContextWindowManager(max_context_tokens=100_000, reserve_for_response=8_000)
        assert mgr.token_budget == 92_000


# ---------------------------------------------------------------------------
# Emergency drop — when compression is not enough
# ---------------------------------------------------------------------------

class TestEmergencyDrop:
    def test_drop_replaces_with_placeholder(self):
        mgr = ContextWindowManager(
            max_context_tokens=100, reserve_for_response=20, recency_window=0
        )
        messages = [
            _system_msg("s"),
            _user_msg("u"),
            _assistant_msg("a", tool_calls=[{"id": "t1", "function": {"name": "x", "arguments": "{}"}}]),
            _tool_msg("t1", _large_tool_result(2000)),
        ]
        result = mgr.compress_if_needed(messages)
        dropped = next(m for m in result if m.get("tool_call_id") == "t1")
        assert "_summarized" in dropped["content"]

    def test_tool_call_id_preserved_after_drop(self):
        mgr = ContextWindowManager(
            max_context_tokens=100, reserve_for_response=20, recency_window=0
        )
        messages = [
            _system_msg("s"),
            _user_msg("u"),
            _assistant_msg("a", tool_calls=[{"id": "keep-id", "function": {"name": "x", "arguments": "{}"}}]),
            _tool_msg("keep-id", _large_tool_result(2000)),
        ]
        result = mgr.compress_if_needed(messages)
        dropped = next(m for m in result if m.get("tool_call_id") == "keep-id")
        assert dropped["tool_call_id"] == "keep-id"


# ---------------------------------------------------------------------------
# Compact value logic
# ---------------------------------------------------------------------------

class TestCompactValue:
    def test_none_and_primitives_pass_through(self):
        mgr = ContextWindowManager()
        assert mgr._compact_value("k", None) is None
        assert mgr._compact_value("k", True) is True
        assert mgr._compact_value("k", 42) == 42
        assert mgr._compact_value("k", 3.14) == 3.14

    def test_short_strings_pass_through(self):
        mgr = ContextWindowManager()
        assert mgr._compact_value("k", "hello") == "hello"

    def test_long_strings_compressed(self):
        mgr = ContextWindowManager()
        long_text = "word " * 200
        result = mgr._compact_value("description", long_text)
        assert len(result) < len(long_text)

    def test_long_list_truncated(self):
        mgr = ContextWindowManager()
        big_list = list(range(20))
        result = mgr._compact_value("items", big_list)
        assert isinstance(result, list)
        assert len(result) == 4  # 3 items + summary string
        assert "more" in result[-1]

    def test_nested_dict_recursion(self):
        mgr = ContextWindowManager()
        data = {"inner": {"path": r"C:\test", "text": "x" * 500}}
        result = mgr._compact_value("outer", data)
        assert result["inner"]["path"] == r"C:\test"
        assert len(result["inner"]["text"]) < 500


# ---------------------------------------------------------------------------
# Summarize tool result
# ---------------------------------------------------------------------------

class TestSummarizeToolResult:
    def test_small_list_kept_intact(self):
        mgr = ContextWindowManager()
        data = [{"id": 1}, {"id": 2}]
        result = mgr._summarize_tool_result(data)
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_large_list_truncated_with_count(self):
        mgr = ContextWindowManager()
        data = [{"n": i} for i in range(10)]
        result = mgr._summarize_tool_result(data)
        assert "+8 more items" in result

    def test_dict_result_produces_valid_json(self):
        mgr = ContextWindowManager()
        data = {"status": "ok", "count": 5, "detail": "x" * 500}
        result = mgr._summarize_tool_result(data)
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["count"] == 5


# ---------------------------------------------------------------------------
# Integration-style: agentic_loop uses compress_if_needed
# ---------------------------------------------------------------------------

class TestAgenticLoopIntegration:
    def test_agentic_loop_has_context_window(self):
        from agentic_loop import AgenticLoop
        from unittest.mock import MagicMock

        loop = AgenticLoop(
            client=MagicMock(),
            safety=MagicMock(),
            tool_router=MagicMock(),
            reflection=MagicMock(),
        )
        assert isinstance(loop.context_window, ContextWindowManager)
