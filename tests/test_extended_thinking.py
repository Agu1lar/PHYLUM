"""Tests for extended thinking (adaptive) integration."""
import asyncio
import json

import pytest

from agentic_loop import AgenticLoop
from multi_provider_client import AgentTurnResult, MultiProviderClient, NormalizedToolCall
from nodes_reflection import ReflectionNode


class _FakeClient:
    def __init__(self, turns):
        self._turns = list(turns)
        self._idx = 0

    async def complete(self, **kwargs):
        turn = self._turns[self._idx]
        self._idx += 1
        return turn


def _make_loop(client, reflection=None):
    return AgenticLoop(client=client, safety=None, tool_router=None, reflection=reflection)


def _base_state():
    return {
        "request_id": "test-req",
        "inputs": {"text": "test prompt"},
        "tasks": [],
    }


async def _noop_emit(event, data):
    pass


def _task_factory(name, args, step):
    return {"id": f"task-{step}-{name}", "tool": name, "action": args.get("action", "run"), "title": f"Task {name}", "params": args}


async def _exec_task(state, task):
    return {"status": "ok", "tool": task["tool"]}


class TestThinkingInTurnResult:
    def test_thinking_fields_default_empty(self):
        result = AgentTurnResult(content="hello")
        assert result.thinking == ""
        assert result.thinking_blocks == []

    def test_thinking_fields_populated(self):
        blocks = [
            {"type": "thinking", "thinking": "Let me analyze...", "signature": "sig123"},
            {"type": "redacted_thinking", "data": "encrypted"},
        ]
        result = AgentTurnResult(
            content="The answer is 42",
            thinking="Let me analyze...",
            thinking_blocks=blocks,
        )
        assert result.thinking == "Let me analyze..."
        assert len(result.thinking_blocks) == 2
        assert result.thinking_blocks[0]["type"] == "thinking"
        assert result.thinking_blocks[1]["type"] == "redacted_thinking"


class TestModelSupportsThinking:
    def test_sonnet_46(self):
        client = MultiProviderClient()
        assert client._model_supports_thinking("claude-sonnet-4-6") is True

    def test_opus_46(self):
        client = MultiProviderClient()
        assert client._model_supports_thinking("claude-opus-4-6") is True

    def test_opus_47(self):
        client = MultiProviderClient()
        assert client._model_supports_thinking("claude-opus-4-7") is True

    def test_haiku(self):
        client = MultiProviderClient()
        assert client._model_supports_thinking("claude-haiku-4-5-20251001") is False

    def test_gpt(self):
        client = MultiProviderClient()
        assert client._model_supports_thinking("gpt-4.1-mini") is False

    def test_gemini(self):
        client = MultiProviderClient()
        assert client._model_supports_thinking("gemini-2.5-flash") is False


class TestThinkingEmittedInLoop:
    @pytest.mark.asyncio
    async def test_thinking_emitted_as_event(self):
        turn = AgentTurnResult(
            content="Final answer",
            thinking="Step 1: analyze. Step 2: decide.",
            thinking_blocks=[{"type": "thinking", "thinking": "Step 1...", "signature": "s1"}],
        )
        client = _FakeClient([turn])
        loop = _make_loop(client)
        events = []

        async def capture_emit(event, data):
            events.append((event, data))

        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "anthropic", "api_key": "test", "model": "claude-sonnet-4-6"},
            emit=capture_emit,
            task_factory=_task_factory,
            execute_task=_exec_task,
            cancel_event=asyncio.Event(),
        )

        thinking_events = [e for e in events if e[0] == "agent_thinking"]
        assert len(thinking_events) == 1
        assert "Step 1" in thinking_events[0][1]["thinking"]
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_thinking_returned_in_final_result(self):
        turn = AgentTurnResult(
            content="Done",
            thinking="My reasoning process here",
        )
        client = _FakeClient([turn])
        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "anthropic", "api_key": "test", "model": "claude-sonnet-4-6"},
            emit=_noop_emit,
            task_factory=_task_factory,
            execute_task=_exec_task,
            cancel_event=asyncio.Event(),
        )
        assert result["thinking"] == "My reasoning process here"

    @pytest.mark.asyncio
    async def test_no_thinking_field_when_empty(self):
        turn = AgentTurnResult(content="Done")
        client = _FakeClient([turn])
        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "anthropic", "api_key": "test", "model": "claude-sonnet-4-6"},
            emit=_noop_emit,
            task_factory=_task_factory,
            execute_task=_exec_task,
            cancel_event=asyncio.Event(),
        )
        assert "thinking" not in result


class TestThinkingBlocksPreserved:
    @pytest.mark.asyncio
    async def test_thinking_blocks_in_tool_call_messages(self):
        thinking_blocks = [
            {"type": "thinking", "thinking": "reasoning...", "signature": "sig1"},
        ]
        tool_turn = AgentTurnResult(
            content="Let me check",
            tool_calls=[NormalizedToolCall(id="tc1", name="shell", arguments={"action": "run", "command": "ls"})],
            thinking="reasoning...",
            thinking_blocks=thinking_blocks,
        )
        final_turn = AgentTurnResult(content="Done")

        client = _FakeClient([tool_turn, final_turn])
        loop = _make_loop(client)

        checkpoint_data = []

        async def _checkpoint(data):
            checkpoint_data.append(data)

        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "anthropic", "api_key": "test", "model": "claude-sonnet-4-6"},
            emit=_noop_emit,
            task_factory=_task_factory,
            execute_task=_exec_task,
            cancel_event=asyncio.Event(),
            checkpoint=_checkpoint,
        )

        msgs_with_thinking = [
            cp for cp in checkpoint_data
            if "messages" in cp
            for m in cp["messages"]
            if m.get("_thinking_blocks")
        ]
        assert len(msgs_with_thinking) > 0


class TestAnthropicMessageConversion:
    def test_thinking_blocks_prepended_in_conversion(self):
        client = MultiProviderClient()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": "Let me check",
                "_thinking_blocks": [
                    {"type": "thinking", "thinking": "reasoning...", "signature": "sig1"},
                ],
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "shell", "arguments": '{"command": "ls"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": "file1.txt"},
        ]
        _, converted = client._to_anthropic_messages(messages)

        assistant_msg = converted[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"][0]["type"] == "thinking"
        assert assistant_msg["content"][0]["signature"] == "sig1"
        assert assistant_msg["content"][1]["type"] == "text"
        assert assistant_msg["content"][2]["type"] == "tool_use"

    def test_no_thinking_blocks_works_normally(self):
        client = MultiProviderClient()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        _, converted = client._to_anthropic_messages(messages)
        assert converted[1]["content"][0]["type"] == "text"


class TestReflectionWithThinking:
    @pytest.mark.asyncio
    async def test_reflection_uses_thinking_when_available(self):
        node = ReflectionNode("reflection_test")
        state = {
            "_last_thinking": "I analyzed the task and it completed successfully. The file was created.",
            "current_task": {"id": "t1"},
            "current_task_result": {"status": "ok"},
            "current_task_error": None,
        }
        result = await node.execute(state)
        assert result["reflection"]["verdict"] == "success"
        assert result["reflection"]["details"]["source"] == "extended_thinking"

    @pytest.mark.asyncio
    async def test_reflection_detects_failure_in_thinking(self):
        node = ReflectionNode("reflection_test")
        state = {
            "_last_thinking": "I tried but the command failed with an error. The file was not found.",
            "current_task": {"id": "t1", "recovery": {"action": "retry"}},
            "current_task_result": None,
            "current_task_error": None,
        }
        result = await node.execute(state)
        assert result["reflection"]["verdict"] == "failed"
        assert result["reflection"]["recommended_action"]["action"] == "retry"

    @pytest.mark.asyncio
    async def test_reflection_falls_back_without_thinking(self):
        node = ReflectionNode("reflection_test")
        state = {
            "current_task": {"id": "t1"},
            "current_task_result": {
                "action_result": {"status": "succeeded", "summary": "done"}
            },
            "current_task_error": None,
        }
        result = await node.execute(state)
        assert result["reflection"]["verdict"] == "success"
        assert result["reflection"]["details"].get("source") != "extended_thinking"
