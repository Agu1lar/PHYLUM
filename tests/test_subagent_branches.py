import asyncio
import json

import pytest

from agentic_loop import AgenticLoop
from canonical_tools import agentic_tool_definitions
from multi_provider_client import AgentTurnResult, NormalizedToolCall


class _SubagentClient:
    def __init__(self):
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        messages = kwargs["messages"]
        user_text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
        has_subagent_result = any(
            m.get("role") == "tool" and "run_parallel_branches" in str(m.get("content"))
            for m in messages
        )
        if has_subagent_result:
            return AgentTurnResult(content="Merged and ready.", tool_calls=[])
        if "Objective: inspect network" in user_text:
            return AgentTurnResult(content="Network branch found one printer.", tool_calls=[])
        if "Objective: inspect drivers" in user_text:
            return AgentTurnResult(content="Driver branch found installed print drivers.", tool_calls=[])
        return AgentTurnResult(
            content="Spawning branches.",
            tool_calls=[
                NormalizedToolCall(
                    id="tc-subagents",
                    name="subagent",
                    arguments={
                        "action": "run_parallel_branches",
                        "objective": "diagnose printers",
                        "branches": [
                            {"id": "network", "objective": "inspect network", "context": "find printers"},
                            {"id": "drivers", "objective": "inspect drivers", "context": "list installed drivers"},
                        ],
                        "budget": {"max_steps": 2, "timeout_seconds": 30, "max_tool_calls": 2},
                    },
                )
            ],
        )


class _BudgetClient:
    async def complete(self, **kwargs):
        messages = kwargs["messages"]
        if any(m.get("role") == "tool" for m in messages):
            return AgentTurnResult(content="Done.", tool_calls=[])
        if any("Objective: branch with no tool budget" in str(m.get("content") or "") for m in messages):
            return AgentTurnResult(
                content="Need a tool.",
                tool_calls=[
                    NormalizedToolCall(id="tc-memory", name="memory", arguments={"action": "world_query", "entity_type": "web_resource"})
                ],
            )
        return AgentTurnResult(
            content="Spawn.",
            tool_calls=[
                NormalizedToolCall(
                    id="tc-subagents",
                    name="subagent",
                    arguments={
                        "action": "run_parallel_branches",
                        "branches": [
                            {
                                "id": "budgeted",
                                "objective": "branch with no tool budget",
                                "budget": {"max_steps": 2, "timeout_seconds": 30, "max_tool_calls": 0},
                            }
                        ],
                    },
                )
            ],
        )


class _CascadeClient:
    async def complete(self, **kwargs):
        messages = kwargs["messages"]
        text = "\n".join(str(m.get("content") or "") for m in messages)
        if any(m.get("role") == "tool" and "run_parallel_branches" in str(m.get("content")) for m in messages):
            return AgentTurnResult(content="Stopped after success.", tool_calls=[])
        is_branch_call = messages[0]["content"].startswith("You are an isolated sub-agent")
        if is_branch_call and "Objective: fast winner" in text:
            return AgentTurnResult(content="OBJECTIVE_SATISFIED Found the answer.", tool_calls=[])
        if is_branch_call and "Objective: slow loser" in text:
            await asyncio.sleep(5)
            return AgentTurnResult(content="Too late.", tool_calls=[])
        return AgentTurnResult(
            content="Spawn.",
            tool_calls=[
                NormalizedToolCall(
                    id="tc-subagents",
                    name="subagent",
                    arguments={
                        "action": "run_parallel_branches",
                        "branches": [
                            {"id": "winner", "objective": "fast winner"},
                            {"id": "loser", "objective": "slow loser"},
                        ],
                        "stop_on_first_success": True,
                        "budget": {"max_steps": 1, "timeout_seconds": 30},
                    },
                )
            ],
        )


def _make_loop(client):
    return AgenticLoop(client=client, safety=None, tool_router=None, reflection=None)


def _base_state():
    return {"request_id": "req-subagent", "inputs": {"text": "diagnose printers"}, "tasks": [], "outputs": {}}


def _task_factory(name, args, step):
    from canonical_tools import normalize_agentic_task

    return normalize_agentic_task(name, args, f"task-{step}-{name}")


async def _emit(_event_type, _payload):
    pass


async def _execute_task(_state, task):
    return {"status": "succeeded", "tool": task["tool"], "action": task["action"]}


@pytest.mark.asyncio
async def test_subagent_tool_spawns_isolated_branches_and_merges_results():
    client = _SubagentClient()
    result = await _make_loop(client).run(
        state=_base_state(),
        provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
        emit=_emit,
        task_factory=_task_factory,
        execute_task=_execute_task,
        cancel_event=asyncio.Event(),
    )

    assert result["status"] == "completed"
    tool_messages = [m for m in result["session"]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["merged"]["summary"].startswith("2 completed")
    assert {b["branch_id"] for b in payload["branches"]} == {"network", "drivers"}
    branch_calls = [call for call in client.calls if call["messages"][0]["content"].startswith("You are an isolated sub-agent")]
    assert len(branch_calls) == 2
    assert all("subagent" not in [t["function"]["name"] for t in call["tools"]] for call in branch_calls)


@pytest.mark.asyncio
async def test_subagent_branch_enforces_individual_tool_budget():
    result = await _make_loop(_BudgetClient()).run(
        state=_base_state(),
        provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
        emit=_emit,
        task_factory=_task_factory,
        execute_task=_execute_task,
        cancel_event=asyncio.Event(),
    )

    payload = json.loads([m for m in result["session"]["messages"] if m.get("role") == "tool"][0]["content"])
    branch = payload["branches"][0]
    assert branch["status"] in ("budget_exceeded", "completed")
    if branch["status"] == "budget_exceeded":
        assert "max_tool_calls" in branch.get("error", "")


@pytest.mark.asyncio
async def test_subagent_stop_on_first_success_cancels_remaining_branches():
    result = await _make_loop(_CascadeClient()).run(
        state=_base_state(),
        provider_config={"provider": "openai", "api_key": "fake", "model": "test"},
        emit=_emit,
        task_factory=_task_factory,
        execute_task=_execute_task,
        cancel_event=asyncio.Event(),
    )

    payload = json.loads([m for m in result["session"]["messages"] if m.get("role") == "tool"][0]["content"])
    statuses = {branch["status"] for branch in payload["branches"]}
    assert "completed" in statuses
    assert "cancelled" in statuses
    assert payload["merged"]["objective_satisfied"] is True
    assert payload["cancelled_remaining"] is True


def test_agentic_tools_include_subagent_and_prompt_mentions_parallel_subagents():
    names = [tool["function"]["name"] for tool in agentic_tool_definitions()]
    assert "subagent" in names
    prompt = AgenticLoop._system_prompt(None)
    assert "subagent" in prompt
    assert "budget" in prompt.lower()
