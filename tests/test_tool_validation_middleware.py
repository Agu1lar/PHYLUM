# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for pre-execution tool validation middleware."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from tool_validation_middleware import (
    ReinjectionBudget,
    build_reinjection_message,
    get_validation_metrics,
    prevalidate_tool_call,
)
from tool_registry import ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.mark.asyncio
async def test_prevalidate_filesystem_write_missing_content(registry):
    result = await prevalidate_tool_call(
        registry,
        "filesystem",
        {"action": "write", "path": "C:/Temp/test.txt"},
        task_id="t1",
    )
    assert not result.ok
    assert "content" in result.missing_fields or any("content" in e.lower() for e in result.errors)
    assert "Re-generate" in result.reinjection_message
    assert "content" in result.reinjection_message.lower()


@pytest.mark.asyncio
async def test_prevalidate_filesystem_read_ok(registry):
    result = await prevalidate_tool_call(
        registry,
        "filesystem",
        {"action": "read", "path": "C:/Users/Public"},
        task_id="t2",
    )
    assert result.ok
    assert result.validation_schema_source == "canonical_input_model"


@pytest.mark.asyncio
async def test_prevalidate_office_uses_canonical_not_llm_mirror(registry):
    """Actions allowed on the pruned LLM mirror must still pass full OfficeInput."""
    result = await prevalidate_tool_call(
        registry,
        "office",
        {"action": "outlook_read_latest", "limit": 5, "folder": "inbox"},
        task_id="t-office",
    )
    assert result.ok
    assert result.validation_schema_source == "canonical_input_model"


@pytest.mark.asyncio
async def test_build_reinjection_message_explicit():
    msg = build_reinjection_message(
        tool="filesystem",
        action="write",
        errors=[],
        missing_fields=["content"],
    )
    assert "content" in msg
    assert "Re-generate" in msg
    assert "not executed" in msg.lower() or "PRE_VALIDATION" in msg


def test_reinjection_budget_caps_per_step():
    budget = ReinjectionBudget(max_per_step=2, max_per_tool_call=5)
    assert budget.can_reinject(step=1, tool_call_id="a")
    budget.record(step=1, tool_call_id="a")
    budget.record(step=1, tool_call_id="b")
    assert not budget.can_reinject(step=1, tool_call_id="c")


@pytest.mark.asyncio
async def test_metrics_record_block(registry):
    metrics = get_validation_metrics()
    before = metrics.prevalidation_blocked
    await prevalidate_tool_call(registry, "filesystem", {"action": "write", "path": "x"})
    assert metrics.prevalidation_blocked >= before
