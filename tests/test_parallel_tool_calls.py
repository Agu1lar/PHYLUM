"""Tests for parallel tool call execution in the agentic loop."""
import asyncio
import json
import time

import pytest

from agentic_loop import AgenticLoop
from multi_provider_client import AgentTurnResult, NormalizedToolCall


# ─── Helpers ──────────────────────────────────────────────────────────

class _FakeClient:
    """Configurable fake provider client that returns pre-set turns."""

    def __init__(self, turns):
        self._turns = list(turns)
        self._idx = 0

    async def complete(self, **kwargs):
        turn = self._turns[self._idx]
        self._idx += 1
        return turn


def _make_loop(client):
    return AgenticLoop(client=client, safety=None, tool_router=None, reflection=None)


def _base_state(text="test"):
    return {
        "request_id": "test-req",
        "inputs": {"text": text},
        "tasks": [],
        "outputs": {},
    }


def _noop_factory(name, args, step):
    import uuid
    tid = f"agentic-{step}-{uuid.uuid4().hex[:6]}"
    from canonical_tools import normalize_agentic_task
    return normalize_agentic_task(name, args, tid)


async def _noop_emit(event_type, payload):
    pass


_cancel = asyncio.Event()


class _TimedExecutor:
    """Executor that records start/end times for each task to verify concurrency."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.timeline = []

    async def __call__(self, state, task):
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        end = time.monotonic()
        self.timeline.append({
            "task_id": task["id"],
            "tool": task["tool"],
            "action": task["action"],
            "start": start,
            "end": end,
        })
        return {"status": "succeeded", "summary": f"done {task['action']}", "data": {}}


# ─── Tests: dependency partitioning ──────────────────────────────────

class TestPartitionByDependency:
    def _make_pair(self, action, tool="memory"):
        tc = NormalizedToolCall(id=f"tc-{action}", name=tool, arguments={"action": action})
        task = {"id": f"task-{action}", "tool": tool, "action": action, "params": {}}
        return (tc, task)

    def test_single_call_is_independent(self):
        planned = [self._make_pair("world_find_app")]
        ind, dep = AgenticLoop._partition_by_dependency(planned)
        assert len(ind) == 1
        assert len(dep) == 0

    def test_two_reads_are_independent(self):
        planned = [
            self._make_pair("world_find_app"),
            self._make_pair("world_find_share"),
        ]
        ind, dep = AgenticLoop._partition_by_dependency(planned)
        assert len(ind) == 2
        assert len(dep) == 0

    def test_mutation_starts_dependent_chain(self):
        planned = [
            self._make_pair("world_find_app"),
            self._make_pair("execute_python", tool="sandbox"),
            self._make_pair("world_find_share"),
        ]
        ind, dep = AgenticLoop._partition_by_dependency(planned)
        assert len(ind) == 1
        assert ind[0][1]["action"] == "world_find_app"
        assert len(dep) == 2

    def test_all_reads_are_parallel(self):
        planned = [
            self._make_pair("outlook_read_latest", tool="office"),
            self._make_pair("list_workbook_sheets", tool="office"),
            self._make_pair("world_find_share"),
        ]
        ind, dep = AgenticLoop._partition_by_dependency(planned)
        assert len(ind) == 3
        assert len(dep) == 0

    def test_write_followed_by_reads(self):
        planned = [
            self._make_pair("word_create_document", tool="office"),
            self._make_pair("world_find_app"),
        ]
        ind, dep = AgenticLoop._partition_by_dependency(planned)
        assert len(ind) == 0
        assert len(dep) == 2

    def test_empty_list(self):
        ind, dep = AgenticLoop._partition_by_dependency([])
        assert len(ind) == 0
        assert len(dep) == 0


# ─── Tests: parallel execution timing ────────────────────────────────

class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_two_reads_execute_concurrently(self):
        """Two independent reads should overlap in time (parallel), not be sequential."""
        client = _FakeClient([
            AgentTurnResult(
                content="Reading two things at once.",
                tool_calls=[
                    NormalizedToolCall(id="tc1", name="memory", arguments={"action": "world_find_app", "key": "word"}),
                    NormalizedToolCall(id="tc2", name="memory", arguments={"action": "world_find_share", "key": "docs"}),
                ],
            ),
            AgentTurnResult(content="Got both results.", tool_calls=[]),
        ])

        executor = _TimedExecutor(delay=0.1)
        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state("find word and docs share"),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=_noop_emit,
            task_factory=_noop_factory,
            execute_task=executor,
            cancel_event=_cancel,
        )

        assert result["status"] == "completed"
        assert len(executor.timeline) == 2

        t1 = executor.timeline[0]
        t2 = executor.timeline[1]
        overlap = min(t1["end"], t2["end"]) - max(t1["start"], t2["start"])
        assert overlap > 0, f"Tasks should overlap in time but got: {executor.timeline}"

    @pytest.mark.asyncio
    async def test_three_parallel_faster_than_sequential(self):
        """Three parallel reads of 0.1s each should complete in ~0.1s, not ~0.3s."""
        client = _FakeClient([
            AgentTurnResult(
                content="",
                tool_calls=[
                    NormalizedToolCall(id="tc1", name="memory", arguments={"action": "world_find_app", "key": "a"}),
                    NormalizedToolCall(id="tc2", name="memory", arguments={"action": "world_find_share", "key": "b"}),
                    NormalizedToolCall(id="tc3", name="memory", arguments={"action": "world_find_path", "key": "c"}),
                ],
            ),
            AgentTurnResult(content="Done.", tool_calls=[]),
        ])

        executor = _TimedExecutor(delay=0.1)
        loop = _make_loop(client)
        start = time.monotonic()
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=_noop_emit,
            task_factory=_noop_factory,
            execute_task=executor,
            cancel_event=_cancel,
        )
        elapsed = time.monotonic() - start

        assert result["status"] == "completed"
        assert len(executor.timeline) == 3
        assert elapsed < 0.25, f"3 parallel tasks of 0.1s should take ~0.1s, took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_mutation_runs_sequentially(self):
        """A read followed by a mutation should NOT run in parallel."""
        client = _FakeClient([
            AgentTurnResult(
                content="",
                tool_calls=[
                    NormalizedToolCall(id="tc1", name="memory", arguments={"action": "world_find_app", "key": "a"}),
                    NormalizedToolCall(id="tc2", name="sandbox", arguments={"action": "execute_python", "code": "print(1)"}),
                ],
            ),
            AgentTurnResult(content="Done.", tool_calls=[]),
        ])

        executor = _TimedExecutor(delay=0.05)
        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=_noop_emit,
            task_factory=_noop_factory,
            execute_task=executor,
            cancel_event=_cancel,
        )

        assert result["status"] == "completed"
        assert len(executor.timeline) == 2
        t_read = next(t for t in executor.timeline if t["action"] != "execute_python")
        t_write = next(t for t in executor.timeline if t["action"] == "execute_python")
        assert t_write["start"] >= t_read["end"] - 0.01, "Mutation should wait for read to finish"

    @pytest.mark.asyncio
    async def test_single_tool_call_still_works(self):
        """Single tool call should work the same as before."""
        client = _FakeClient([
            AgentTurnResult(
                content="One call.",
                tool_calls=[
                    NormalizedToolCall(id="tc1", name="memory", arguments={"action": "world_find_app", "key": "word"}),
                ],
            ),
            AgentTurnResult(content="Found it.", tool_calls=[]),
        ])

        executor = _TimedExecutor(delay=0.01)
        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=_noop_emit,
            task_factory=_noop_factory,
            execute_task=executor,
            cancel_event=_cancel,
        )

        assert result["status"] == "completed"
        assert len(executor.timeline) == 1

    @pytest.mark.asyncio
    async def test_results_appear_in_correct_order(self):
        """Tool results in messages should match the order of tool calls, not execution order."""
        client = _FakeClient([
            AgentTurnResult(
                content="",
                tool_calls=[
                    NormalizedToolCall(id="tc-alpha", name="memory", arguments={"action": "world_find_app", "key": "a"}),
                    NormalizedToolCall(id="tc-beta", name="memory", arguments={"action": "world_find_share", "key": "b"}),
                ],
            ),
            AgentTurnResult(content="Done.", tool_calls=[]),
        ])

        async def ordered_executor(state, task):
            return {"status": "succeeded", "task_id": task["id"]}

        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=_noop_emit,
            task_factory=_noop_factory,
            execute_task=ordered_executor,
            cancel_event=_cancel,
        )

        assert result["status"] == "completed"
        session_messages = result["session"]["messages"]
        tool_msgs = [m for m in session_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["tool_call_id"] == "tc-alpha"
        assert tool_msgs[1]["tool_call_id"] == "tc-beta"

    @pytest.mark.asyncio
    async def test_handoff_prevents_parallel_execution(self):
        """If a handoff (request_user_input) is in the batch, it should take priority."""
        client = _FakeClient([
            AgentTurnResult(
                content="Need info.",
                tool_calls=[
                    NormalizedToolCall(id="tc-handoff", name="request_user_input", arguments={
                        "prompt": "Which printer?",
                        "title": "Printer selection",
                    }),
                    NormalizedToolCall(id="tc-read", name="memory", arguments={"action": "world_find_app", "key": "x"}),
                ],
            ),
        ])

        executed = []

        async def tracking_executor(state, task):
            executed.append(task["id"])
            return {"status": "succeeded"}

        loop = _make_loop(client)
        result = await loop.run(
            state=_base_state(),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=_noop_emit,
            task_factory=_noop_factory,
            execute_task=tracking_executor,
            cancel_event=_cancel,
        )

        assert result["status"] == "awaiting_input"
        assert len(executed) == 0, "No tasks should execute when handoff is present"


# ─── Tests: event emission ───────────────────────────────────────────

class TestParallelEvents:
    @pytest.mark.asyncio
    async def test_parallel_execution_emits_parallel_step_event(self):
        """When multiple tasks run in parallel, an informational event is emitted."""
        client = _FakeClient([
            AgentTurnResult(
                content="",
                tool_calls=[
                    NormalizedToolCall(id="tc1", name="memory", arguments={"action": "world_find_app", "key": "a"}),
                    NormalizedToolCall(id="tc2", name="memory", arguments={"action": "world_find_share", "key": "b"}),
                ],
            ),
            AgentTurnResult(content="Done.", tool_calls=[]),
        ])

        events = []

        async def capture_emit(event_type, payload):
            events.append({"type": event_type, **payload})

        async def simple_executor(state, task):
            return {"status": "succeeded"}

        loop = _make_loop(client)
        await loop.run(
            state=_base_state(),
            provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
            emit=capture_emit,
            task_factory=_noop_factory,
            execute_task=simple_executor,
            cancel_event=_cancel,
        )

        parallel_events = [e for e in events if "parallel" in e.get("summary", "").lower()]
        assert len(parallel_events) == 1
        assert "2" in parallel_events[0]["summary"]
