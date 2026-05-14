# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import logging

from action_executor import RunPausedError
from canonical_tools import agentic_tool_definitions, to_openai_tool_call
from context_window import ContextWindowManager
from execution_economics import CostTracker
from multi_provider_client import LLMApiError, MultiProviderClient
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from prompt_cache import PromptCache

logger = logging.getLogger(__name__)


TaskFactory = Callable[[str, Dict[str, Any], int], Dict[str, Any]]
EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]
TaskExecuteFn = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]
CheckpointFn = Callable[[Dict[str, Any]], Awaitable[None]]


_DEFAULT_RUN_BUDGET_USD = 0.25
_DEFAULT_RUN_BUDGET_TOKENS = 80_000


class AgenticLoop:
    def __init__(
        self,
        *,
        client: MultiProviderClient,
        safety: SafetyNode,
        tool_router: ToolRouterNode,
        reflection: ReflectionNode,
        max_steps: int = 10,
        budget_usd: float = _DEFAULT_RUN_BUDGET_USD,
        budget_tokens: int = _DEFAULT_RUN_BUDGET_TOKENS,
    ):
        self.client = client
        self.safety = safety
        self.tool_router = tool_router
        self.reflection = reflection
        self.max_steps = max_steps
        self.budget_usd = budget_usd
        self.budget_tokens = budget_tokens
        self.prompt_cache = PromptCache()
        self.context_window = ContextWindowManager()

    async def run(
        self,
        *,
        state: Dict[str, Any],
        provider_config: Dict[str, Any],
        emit: EmitFn,
        task_factory: TaskFactory,
        execute_task: TaskExecuteFn,
        cancel_event,
        session: Optional[Dict[str, Any]] = None,
        checkpoint: Optional[CheckpointFn] = None,
    ) -> Dict[str, Any]:
        provider_id = provider_config["provider"]
        system_prompt = self.prompt_cache.get_or_build_prompt(self._system_prompt)
        tools = self.prompt_cache.get_or_build_tools(agentic_tool_definitions)
        tools_for_provider = self.prompt_cache.get_tools_for_provider(tools, provider=provider_id)

        messages: List[Dict[str, Any]] = list((session or {}).get("messages") or [])
        if not messages:
            system_message = self.prompt_cache.get_system_message(system_prompt, provider=provider_id)
            messages = [
                system_message,
                {"role": "user", "content": state["inputs"].get("text") or state["inputs"].get("prompt") or ""},
            ]
        start_step = int((session or {}).get("step") or 0)

        cost_tracker = CostTracker(
            run_id=state.get("request_id", ""),
            model=provider_config.get("model", ""),
            budget_usd=self.budget_usd,
            budget_tokens=self.budget_tokens,
        )

        for step in range(start_step + 1, start_step + self.max_steps + 1):
            if cancel_event.is_set():
                raise asyncio.CancelledError()

            if cost_tracker.over_budget:
                logger.warning(
                    "Run %s hit budget limit (USD %.4f / %d tokens) at step %d",
                    state.get("request_id"), cost_tracker.total_cost_usd,
                    cost_tracker.total_tokens, step,
                )
                partial_text = self._salvage_budget_stop(state, cost_tracker)
                return {
                    "status": "completed",
                    "final_text": partial_text,
                    "steps": step - 1,
                    "session": {"messages": messages, "step": step - 1},
                    "cost": cost_tracker.summary().to_dict(),
                    "budget_exceeded": True,
                }

            if checkpoint is not None:
                await checkpoint({"step": step, "paused_reason": None})
            await emit(
                "agent_step",
                {
                    "request_id": state["request_id"],
                    "step": step,
                    "summary": f"Calling {provider_id}:{provider_config['model']}",
                },
            )
            messages_for_llm = self.context_window.compress_if_needed(messages)
            try:
                turn = await self.client.complete(
                    provider=provider_id,
                    api_key=provider_config["api_key"],
                    model=provider_config["model"],
                    messages=messages_for_llm,
                    tools=tools_for_provider,
                    base_url=provider_config.get("base_url"),
                )
            except LLMApiError as llm_err:
                logger.error("LLM API failed at step %d: %s", step, llm_err)
                partial_text = self._salvage_partial_response(state, step, llm_err)
                return {
                    "status": "completed",
                    "final_text": partial_text,
                    "steps": step,
                    "session": {"messages": messages, "step": step},
                    "cost": cost_tracker.summary().to_dict(),
                    "llm_error": {
                        "provider": llm_err.provider,
                        "model": llm_err.model,
                        "status_code": llm_err.status_code,
                        "message": str(llm_err),
                    },
                }

            if turn.usage:
                cost_tracker.record_llm_usage(
                    prompt_tokens=turn.usage.get("prompt_tokens", 0),
                    completion_tokens=turn.usage.get("completion_tokens", 0),
                    cache_creation_tokens=turn.usage.get("cache_creation_input_tokens", 0),
                    cache_read_tokens=turn.usage.get("cache_read_input_tokens", 0),
                )
                logger.debug(
                    "Step %d cost: USD %.4f cumulative, %d tokens total",
                    step, cost_tracker.total_cost_usd, cost_tracker.total_tokens,
                )

            if turn.thinking:
                await emit(
                    "agent_thinking",
                    {
                        "request_id": state["request_id"],
                        "step": step,
                        "thinking": self._truncate(turn.thinking, limit=500),
                    },
                )
                state["_last_thinking"] = turn.thinking
                if checkpoint is not None:
                    await checkpoint({"step": step, "thinking": self._truncate(turn.thinking, limit=500)})

            if turn.content:
                await emit(
                    "agent_step",
                    {
                        "request_id": state["request_id"],
                        "step": step,
                        "summary": self._truncate(turn.content),
                    },
                )
                if checkpoint is not None:
                    await checkpoint(
                        {
                            "step": step,
                            "last_model_output": self._truncate(turn.content, limit=500),
                            "strategy": {"step": step, "model_output": self._truncate(turn.content, limit=500)},
                        }
                    )

            if turn.tool_calls:
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": turn.content or None,
                    "tool_calls": [to_openai_tool_call(tool_call.id, tool_call.name, tool_call.arguments) for tool_call in turn.tool_calls],
                }
                if turn.thinking_blocks:
                    assistant_msg["_thinking_blocks"] = turn.thinking_blocks
                messages.append(assistant_msg)
                if checkpoint is not None:
                    await checkpoint({"messages": messages, "step": step})

                handoff_call = next(
                    (tc for tc in turn.tool_calls if tc.name == "request_user_input"), None
                )
                if handoff_call is not None:
                    handoff = self._handoff_from_tool_call(state, handoff_call)
                    if checkpoint is not None:
                        await checkpoint(
                            {
                                "messages": messages,
                                "step": step,
                                "paused_reason": "awaiting_input",
                                "pending_subgoal": handoff.get("prompt"),
                                "observation": {
                                    "kind": "handoff_requested",
                                    "prompt": handoff.get("prompt"),
                                    "reason": handoff.get("reason"),
                                },
                            }
                        )
                    return {
                        "status": "awaiting_input",
                        "handoff": handoff,
                        "session": {"messages": messages, "step": step, "paused_reason": "awaiting_input"},
                    }

                executable_calls = [
                    tc for tc in turn.tool_calls if tc.name != "request_user_input"
                ]

                subagent_calls = [tc for tc in executable_calls if tc.name == "subagent"]
                regular_calls = [tc for tc in executable_calls if tc.name != "subagent"]

                if subagent_calls:
                    await emit(
                        "agent_step",
                        {
                            "request_id": state["request_id"],
                            "step": step,
                            "summary": f"Spawning {len(subagent_calls)} sub-agent branch group(s)",
                        },
                    )
                    subagent_results: Dict[str, Dict[str, Any]] = {}
                    for tool_call in subagent_calls:
                        subagent_results[tool_call.id] = await self._execute_subagent_tool_call(
                            tool_call=tool_call,
                            state=state,
                            provider_config=provider_config,
                            tools_for_provider=tools_for_provider,
                            emit=emit,
                            task_factory=task_factory,
                            execute_task=execute_task,
                            cancel_event=cancel_event,
                        )
                    for tool_call in subagent_calls:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": self._compact_tool_result(subagent_results[tool_call.id]),
                            }
                        )
                        if checkpoint is not None:
                            await checkpoint(
                                {
                                    "messages": messages,
                                    "step": step,
                                    "observation": {
                                        "kind": "subagent_merge",
                                        "tool_call_id": tool_call.id,
                                        "result": subagent_results[tool_call.id],
                                    },
                                }
                            )
                    if not regular_calls:
                        continue

                planned: List[tuple] = []
                for tool_call in regular_calls:
                    task = task_factory(tool_call.name, tool_call.arguments, step)
                    state["tasks"].append(task)
                    await emit("task_planned", {"request_id": state["request_id"], "task": task})
                    await emit(
                        "tool_call_proposed",
                        {
                            "request_id": state["request_id"],
                            "task_id": task["id"],
                            "tool": task["tool"],
                            "action": task["action"],
                            "preview": self._preview_task(task),
                        },
                    )
                    if checkpoint is not None:
                        await checkpoint(
                            {
                                "messages": messages,
                                "step": step,
                                "pending_subgoal": task["title"],
                                "hypothesis": {"tool": task["tool"], "action": task["action"], "params": task.get("params", {})},
                                "strategy": {"step": step, "tool_call": tool_call.name, "task_id": task["id"], "title": task["title"]},
                            }
                        )
                    planned.append((tool_call, task))

                independent, dependent = self._partition_by_dependency(planned)

                if len(independent) > 1:
                    await emit(
                        "agent_step",
                        {
                            "request_id": state["request_id"],
                            "step": step,
                            "summary": f"Executing {len(independent)} tool calls in parallel",
                        },
                    )

                results_map: Dict[str, Dict[str, Any]] = {}

                if independent:
                    async def _exec_one(tc, tsk):
                        return tc, tsk, await self._execute_task(
                            state=state, task=tsk, emit=emit,
                            cancel_event=cancel_event, execute_task=execute_task,
                        )

                    if cancel_event.is_set():
                        raise asyncio.CancelledError()

                    gathered = await asyncio.gather(
                        *[_exec_one(tc, tsk) for tc, tsk in independent],
                        return_exceptions=True,
                    )
                    for item in gathered:
                        if isinstance(item, BaseException):
                            if isinstance(item, (asyncio.CancelledError, RunPausedError)):
                                raise item
                            results_map["__error__"] = {
                                "status": "failed",
                                "error": f"{item.__class__.__name__}: {item}",
                            }
                            continue
                        tc, tsk, result = item
                        results_map[tc.id] = result

                for tool_call, task in dependent:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError()
                    task_result = await self._execute_task(
                        state=state, task=task, emit=emit,
                        cancel_event=cancel_event, execute_task=execute_task,
                    )
                    results_map[tool_call.id] = task_result

                for tool_call, task in planned:
                    task_result = results_map.get(tool_call.id, {"status": "failed", "error": "execution skipped"})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": self._compact_tool_result(task_result),
                        }
                    )
                    if checkpoint is not None:
                        await checkpoint(
                            {
                                "messages": messages,
                                "step": step,
                                "observation": {
                                    "task_id": task["id"],
                                    "tool": task["tool"],
                                    "action": task["action"],
                                    "result": task_result,
                                },
                            }
                        )
                continue

            if turn.content:
                final_msg: Dict[str, Any] = {"role": "assistant", "content": turn.content}
                if turn.thinking_blocks:
                    final_msg["_thinking_blocks"] = turn.thinking_blocks
                messages.append(final_msg)
                if checkpoint is not None:
                    await checkpoint({"messages": messages, "step": step, "paused_reason": None})
                result: Dict[str, Any] = {
                    "status": "completed",
                    "final_text": turn.content,
                    "steps": step,
                    "session": {"messages": messages, "step": step},
                    "cost": cost_tracker.summary().to_dict(),
                }
                if turn.thinking:
                    result["thinking"] = turn.thinking
                logger.info(
                    "Run %s completed in %d steps — USD %.4f, %d tokens",
                    state.get("request_id"), step,
                    cost_tracker.total_cost_usd, cost_tracker.total_tokens,
                )
                return result
            raise RuntimeError("Agent returned neither text nor tool call")

        raise RuntimeError(f"Agentic loop reached max_steps={self.max_steps}")

    async def _execute_task(
        self,
        *,
        state: Dict[str, Any],
        task: Dict[str, Any],
        emit: EmitFn,
        cancel_event,
        execute_task: TaskExecuteFn,
    ) -> Dict[str, Any]:
        if cancel_event.is_set():
            raise asyncio.CancelledError()
        return await execute_task(state, task)

    async def _execute_subagent_tool_call(
        self,
        *,
        tool_call,
        state: Dict[str, Any],
        provider_config: Dict[str, Any],
        tools_for_provider: List[Dict[str, Any]],
        emit: EmitFn,
        task_factory: TaskFactory,
        execute_task: TaskExecuteFn,
        cancel_event,
    ) -> Dict[str, Any]:
        args = tool_call.arguments or {}
        if args.get("action") != "run_parallel_branches":
            return {"status": "failed", "error": "unsupported subagent action"}
        branches = args.get("branches") or []
        if not isinstance(branches, list) or not branches:
            return {"status": "failed", "error": "subagent branches are required"}

        overall_budget = self._normalize_subagent_budget(args.get("budget") or {})
        stop_on_first_success = bool(args.get("stop_on_first_success", False))
        group_cancel = asyncio.Event()
        started_at = time.monotonic()

        async def _watch_parent_cancel():
            await cancel_event.wait()
            group_cancel.set()

        watcher = asyncio.create_task(_watch_parent_cancel())

        async def _run_one(index: int, branch: Dict[str, Any]) -> Dict[str, Any]:
            branch_id = str(branch.get("id") or f"branch-{index + 1}")
            branch_budget = self._normalize_subagent_budget(branch.get("budget") or {}, defaults=overall_budget)
            await emit(
                "subagent_started",
                {
                    "request_id": state["request_id"],
                    "parent_tool_call_id": tool_call.id,
                    "branch_id": branch_id,
                    "objective": branch.get("objective"),
                    "budget": branch_budget,
                },
            )
            try:
                result = await asyncio.wait_for(
                    self._run_subagent_branch(
                        branch_id=branch_id,
                        branch=branch,
                        parent_state=state,
                        provider_config=provider_config,
                        tools_for_provider=self._tools_without_subagent(tools_for_provider),
                        budget=branch_budget,
                        task_factory=task_factory,
                        execute_task=execute_task,
                        parent_cancel_event=cancel_event,
                        group_cancel_event=group_cancel,
                    ),
                    timeout=branch_budget["timeout_seconds"],
                )
                if stop_on_first_success and result.get("objective_satisfied"):
                    group_cancel.set()
                await emit(
                    "subagent_completed",
                    {
                        "request_id": state["request_id"],
                        "parent_tool_call_id": tool_call.id,
                        "branch_id": branch_id,
                        "status": result.get("status"),
                        "objective_satisfied": result.get("objective_satisfied", False),
                    },
                )
                return result
            except asyncio.TimeoutError:
                return {
                    "branch_id": branch_id,
                    "status": "timeout",
                    "objective": branch.get("objective"),
                    "error": f"sub-agent exceeded timeout_seconds={branch_budget['timeout_seconds']}",
                    "budget": branch_budget,
                }
            except asyncio.CancelledError:
                return {
                    "branch_id": branch_id,
                    "status": "cancelled",
                    "objective": branch.get("objective"),
                    "error": "sub-agent cancelled",
                    "budget": branch_budget,
                }
            except Exception as exc:
                return {
                    "branch_id": branch_id,
                    "status": "failed",
                    "objective": branch.get("objective"),
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "budget": branch_budget,
                }

        tasks = [asyncio.create_task(_run_one(index, branch)) for index, branch in enumerate(branches)]
        task_branch_ids = {
            task: str(branch.get("id") or f"branch-{index + 1}")
            for index, (task, branch) in enumerate(zip(tasks, branches))
        }
        try:
            if stop_on_first_success:
                results = []
                pending = set(tasks)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    completed_results = []
                    for completed_task in done:
                        try:
                            completed_results.append(await completed_task)
                        except asyncio.CancelledError:
                            completed_results.append(
                                {
                                    "branch_id": task_branch_ids.get(completed_task, "cancelled"),
                                    "status": "cancelled",
                                    "error": "sub-agent cancelled",
                                }
                            )
                    results.extend(completed_results)
                    if any(result.get("objective_satisfied") for result in completed_results):
                        group_cancel.set()
                        for pending_task in pending:
                            pending_task.cancel()
                        cancelled_results = await asyncio.gather(*pending, return_exceptions=True)
                        for index, item in enumerate(cancelled_results):
                            if isinstance(item, dict):
                                results.append(item)
                            else:
                                pending_task = list(pending)[index]
                                results.append(
                                    {
                                        "branch_id": task_branch_ids.get(pending_task, f"cancelled-{index + 1}"),
                                        "status": "cancelled",
                                        "error": "cancelled after another branch satisfied the objective",
                                    }
                                )
                        seen_branch_ids = {item.get("branch_id") for item in results}
                        for branch_id in task_branch_ids.values():
                            if branch_id not in seen_branch_ids:
                                results.append(
                                    {
                                        "branch_id": branch_id,
                                        "status": "cancelled",
                                        "error": "cancelled after another branch satisfied the objective",
                                    }
                                )
                        pending = set()
                        break
            else:
                results = await asyncio.gather(*tasks)
        finally:
            watcher.cancel()
            for task in tasks:
                if not task.done():
                    task.cancel()

        merged = self._merge_subagent_results(results)
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        return {
            "status": "succeeded",
            "action": "run_parallel_branches",
            "objective": args.get("objective"),
            "branches": results,
            "merged": merged,
            "budget": overall_budget,
            "elapsed_ms": elapsed_ms,
            "cancelled_remaining": group_cancel.is_set(),
        }

    async def _run_subagent_branch(
        self,
        *,
        branch_id: str,
        branch: Dict[str, Any],
        parent_state: Dict[str, Any],
        provider_config: Dict[str, Any],
        tools_for_provider: List[Dict[str, Any]],
        budget: Dict[str, Any],
        task_factory: TaskFactory,
        execute_task: TaskExecuteFn,
        parent_cancel_event,
        group_cancel_event: asyncio.Event,
    ) -> Dict[str, Any]:
        started_at = time.monotonic()
        branch_state: Dict[str, Any] = {
            "request_id": f"{parent_state['request_id']}:{branch_id}",
            "parent_request_id": parent_state["request_id"],
            "inputs": {
                "text": branch.get("objective") or "",
                "context": branch.get("context"),
            },
            "tasks": [],
            "outputs": {},
            "runtime_context": dict(parent_state.get("runtime_context") or {}),
            "subagent": {"branch_id": branch_id, "objective": branch.get("objective")},
        }
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": self._subagent_system_prompt(
                    branch_id=branch_id,
                    success_criteria=branch.get("success_criteria"),
                ),
            },
            {
                "role": "user",
                "content": self._subagent_user_prompt(branch),
            },
        ]
        tool_calls_used = 0

        for step in range(1, budget["max_steps"] + 1):
            if parent_cancel_event.is_set() or group_cancel_event.is_set():
                return self._subagent_cancelled_result(branch_id, branch, budget, started_at, step - 1, tool_calls_used)

            budget_error = self._subagent_budget_error(messages, budget)
            if budget_error:
                return {
                    "branch_id": branch_id,
                    "status": "budget_exceeded",
                    "objective": branch.get("objective"),
                    "error": budget_error,
                    "budget": budget,
                    "budget_used": self._subagent_budget_used(started_at, step - 1, tool_calls_used, messages, budget),
                }

            turn = await self.client.complete(
                provider=provider_config["provider"],
                api_key=provider_config["api_key"],
                model=provider_config["model"],
                messages=self.context_window.compress_if_needed(messages),
                tools=tools_for_provider,
                base_url=provider_config.get("base_url"),
            )

            if turn.tool_calls:
                if tool_calls_used + len(turn.tool_calls) > budget["max_tool_calls"]:
                    return {
                        "branch_id": branch_id,
                        "status": "budget_exceeded",
                        "objective": branch.get("objective"),
                        "error": f"sub-agent exceeded max_tool_calls={budget['max_tool_calls']}",
                        "budget": budget,
                        "budget_used": self._subagent_budget_used(started_at, step, tool_calls_used, messages, budget),
                    }
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": turn.content or None,
                    "tool_calls": [to_openai_tool_call(tc.id, tc.name, tc.arguments) for tc in turn.tool_calls],
                }
                messages.append(assistant_msg)

                planned = []
                for tc in turn.tool_calls:
                    task = task_factory(tc.name, tc.arguments, step)
                    task["subagent_branch_id"] = branch_id
                    branch_state["tasks"].append(task)
                    planned.append((tc, task))
                independent, dependent = self._partition_by_dependency(planned)
                results_map: Dict[str, Dict[str, Any]] = {}

                if independent:
                    async def _exec_one(tc, task):
                        return tc, await self._execute_task(
                            state=branch_state,
                            task=task,
                            emit=lambda *_args, **_kwargs: None,
                            cancel_event=parent_cancel_event,
                            execute_task=execute_task,
                        )

                    gathered = await asyncio.gather(*[_exec_one(tc, task) for tc, task in independent], return_exceptions=True)
                    for item in gathered:
                        if isinstance(item, BaseException):
                            if isinstance(item, (asyncio.CancelledError, RunPausedError)):
                                raise item
                            results_map["__error__"] = {"status": "failed", "error": f"{item.__class__.__name__}: {item}"}
                            continue
                        tc, result = item
                        results_map[tc.id] = result

                for tc, task in dependent:
                    if parent_cancel_event.is_set() or group_cancel_event.is_set():
                        return self._subagent_cancelled_result(branch_id, branch, budget, started_at, step, tool_calls_used)
                    results_map[tc.id] = await self._execute_task(
                        state=branch_state,
                        task=task,
                        emit=lambda *_args, **_kwargs: None,
                        cancel_event=parent_cancel_event,
                        execute_task=execute_task,
                    )

                for tc, _task in planned:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": self._compact_tool_result(results_map.get(tc.id, {"status": "failed", "error": "execution skipped"})),
                        }
                    )
                tool_calls_used += len(turn.tool_calls)
                continue

            final_text = turn.content or ""
            return {
                "branch_id": branch_id,
                "status": "completed",
                "objective": branch.get("objective"),
                "result": final_text,
                "objective_satisfied": self._detect_objective_satisfied(final_text),
                "tasks": branch_state["tasks"],
                "budget": budget,
                "budget_used": self._subagent_budget_used(started_at, step, tool_calls_used, messages, budget),
            }

        return {
            "branch_id": branch_id,
            "status": "budget_exceeded",
            "objective": branch.get("objective"),
            "error": f"sub-agent reached max_steps={budget['max_steps']}",
            "tasks": branch_state["tasks"],
            "budget": budget,
            "budget_used": self._subagent_budget_used(started_at, budget["max_steps"], tool_calls_used, messages, budget),
        }

    def _system_prompt(self) -> str:
        return (
            "You are PHYLUM, an autonomous Windows desktop agent. ACT first, report after.\n\n"
            "RESPONSE: Clean, direct, no emojis/tables/headers. Summarize in 2-3 sentences.\n\n"
            "AUTONOMY: After each step, do the obvious next step. Only ask the user when "
            "choices depend on preference or info is undiscoverable. Exhaust discovery first.\n\n"
            "TOOLS: shell (PowerShell/cmd), desktop (windows/apps/processes), windows_ui (UI controls via pywinauto), "
            "office (Word/Excel/Outlook headless COM), sandbox (Python/PS scripts), artifact (file analysis), "
            "share_discovery (mapped drives/UNC), document_intelligence (doc extract/search), "
            "web (search/fetch), memory (world model/strategy), driver_manager (printers/devices/drivers), "
            "subagent (parallel branches with budget).\n\n"
            "MEMORY: Check memory before discovering. Persist findings. Confidence decays 0.05/day. "
            "Check strategy_best before complex tasks. Record outcomes after.\n\n"
            "STRATEGY: Prefer headless/internal over desktop UI. On failure, switch approach "
            "(COM fails->openpyxl; browser fails->web tool; fs fails->sandbox). "
            "Never retry blindly. Discover targets via shell, Explorer, net commands. "
            "Verify actions with follow-up checks.\n\n"
            "BUDGET: Minimize tool calls. Combine related queries into one command. "
            "Pipe PowerShell output to ConvertTo-Json for structured data. "
            "Avoid Format-Table (wastes tokens); prefer Select-Object | ConvertTo-Json.\n\n"
            "Never reveal API keys. Use complete tool arguments. Replan on failure."
        )

    @staticmethod
    def _normalize_subagent_budget(
        budget: Dict[str, Any],
        *,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = dict(defaults or {
            "max_steps": 3,
            "timeout_seconds": 60,
            "max_tool_calls": 4,
            "max_context_chars": 12000,
            "max_estimated_tokens": 3000,
            "max_cost_usd": 0.05,
            "estimated_usd_per_1k_tokens": 0.003,
        })
        limits = {
            "max_steps": (1, 5),
            "timeout_seconds": (5, 120),
            "max_tool_calls": (1, 8),
            "max_context_chars": (1000, 24000),
            "max_estimated_tokens": (250, 6000),
        }
        for key, (minimum, maximum) in limits.items():
            raw = budget.get(key, base[key]) if isinstance(budget, dict) else base[key]
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = base[key]
            base[key] = max(minimum, min(maximum, value))
        for key in ("max_cost_usd", "estimated_usd_per_1k_tokens"):
            raw = budget.get(key, base[key]) if isinstance(budget, dict) else base[key]
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = float(base[key] or 0.0)
            base[key] = max(0.0, value)
        return base

    @staticmethod
    def _tools_without_subagent(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [tool for tool in tools if (tool.get("function") or {}).get("name") != "subagent"]

    @staticmethod
    def _messages_char_count(messages: List[Dict[str, Any]]) -> int:
        return len(json.dumps(messages, default=str))

    @staticmethod
    def _subagent_budget_error(messages: List[Dict[str, Any]], budget: Dict[str, Any]) -> Optional[str]:
        chars = AgenticLoop._messages_char_count(messages)
        estimated_tokens = max(1, chars // 4)
        if chars > int(budget["max_context_chars"]):
            return f"sub-agent exceeded max_context_chars={budget['max_context_chars']}"
        if estimated_tokens > int(budget["max_estimated_tokens"]):
            return f"sub-agent exceeded max_estimated_tokens={budget['max_estimated_tokens']}"
        max_cost = float(budget.get("max_cost_usd") or 0.0)
        rate = float(budget.get("estimated_usd_per_1k_tokens") or 0.0)
        if max_cost > 0 and rate > 0:
            estimated_cost = (estimated_tokens / 1000.0) * rate
            if estimated_cost > max_cost:
                return f"sub-agent exceeded max_cost_usd={max_cost:.4f}"
        return None

    @staticmethod
    def _subagent_system_prompt(*, branch_id: str, success_criteria: Optional[str]) -> str:
        criteria = success_criteria or "Return concise findings, evidence, blockers, and recommended next action."
        return (
            "You are an isolated sub-agent branch inside a larger Windows desktop automation run. "
            f"Branch id: {branch_id}. "
            "Work only on your assigned objective. Use tools for discovery when needed. "
            "Do not ask the user for input unless discovery is impossible. "
            "Return a concise final answer with findings, evidence, and next action. "
            "If your result fully satisfies the overall objective, include the exact marker OBJECTIVE_SATISFIED. "
            f"Success criteria: {criteria}"
        )

    @staticmethod
    def _subagent_user_prompt(branch: Dict[str, Any]) -> str:
        parts = [f"Objective: {branch.get('objective') or ''}"]
        if branch.get("context"):
            parts.append(f"Context: {branch['context']}")
        if branch.get("success_criteria"):
            parts.append(f"Success criteria: {branch['success_criteria']}")
        return "\n".join(parts)

    @staticmethod
    def _detect_objective_satisfied(text: str) -> bool:
        lowered = (text or "").lower()
        return "objective_satisfied" in lowered or "objective satisfied" in lowered or "objetivo atingido" in lowered

    @staticmethod
    def _merge_subagent_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        completed = [item for item in results if item.get("status") == "completed"]
        failed = [item for item in results if item.get("status") in {"failed", "timeout", "budget_exceeded"}]
        cancelled = [item for item in results if item.get("status") == "cancelled"]
        findings = [
            {
                "branch_id": item.get("branch_id"),
                "objective": item.get("objective"),
                "result": item.get("result"),
                "status": item.get("status"),
            }
            for item in results
        ]
        return {
            "summary": f"{len(completed)} completed, {len(failed)} failed or budget-limited, {len(cancelled)} cancelled",
            "all_completed": len(completed) == len(results),
            "objective_satisfied": any(item.get("objective_satisfied") for item in results),
            "findings": findings,
        }

    @staticmethod
    def _subagent_budget_used(
        started_at: float,
        steps: int,
        tool_calls: int,
        messages: List[Dict[str, Any]],
        budget: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        chars = AgenticLoop._messages_char_count(messages)
        estimated_tokens = max(1, chars // 4)
        rate = float((budget or {}).get("estimated_usd_per_1k_tokens") or 0.0)
        used = {
            "steps": int(steps),
            "tool_calls": int(tool_calls),
            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            "estimated_tokens": estimated_tokens,
            "context_chars": chars,
        }
        if rate > 0:
            used["estimated_cost_usd"] = round((estimated_tokens / 1000.0) * rate, 6)
        return used

    def _subagent_cancelled_result(
        self,
        branch_id: str,
        branch: Dict[str, Any],
        budget: Dict[str, Any],
        started_at: float,
        steps: int,
        tool_calls: int,
    ) -> Dict[str, Any]:
        return {
            "branch_id": branch_id,
            "status": "cancelled",
            "objective": branch.get("objective"),
            "error": "parent or sibling branch cancelled this sub-agent",
            "budget": budget,
            "budget_used": {
                "steps": int(steps),
                "tool_calls": int(tool_calls),
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            },
        }

    @staticmethod
    def _partition_by_dependency(
        planned: List[tuple],
    ) -> tuple:
        """Split planned (tool_call, task) pairs into independent and dependent groups.

        Independent tasks can execute in parallel. Dependent tasks reference outputs
        from earlier tasks in the same batch (detected by matching tool/action patterns
        that typically chain — e.g. artifact.load followed by artifact.transform on the
        loaded result, or reading data then writing it).

        Heuristic: the first N tasks that are all read/inspection-type (no mutation and
        no reference to sibling outputs) are independent. Once a mutation or a likely
        data-flow dependency is detected, all remaining tasks are dependent.
        """
        if len(planned) <= 1:
            return planned, []

        MUTATION_TYPES = {"execute_python", "execute_powershell", "write_result", "create",
                          "word_create_document", "save_as_document", "export_pdf",
                          "draft_email_with_attachment", "delete", "move", "copy"}

        independent = []
        dependent = []
        seen_outputs = set()
        dependency_detected = False

        for tool_call, task in planned:
            action = task.get("action", "")
            params = task.get("params") or {}

            if dependency_detected:
                dependent.append((tool_call, task))
                continue

            param_values = " ".join(str(v) for v in params.values()).lower()
            refs_sibling = any(tid in param_values for tid in seen_outputs if tid)

            if action in MUTATION_TYPES or refs_sibling:
                dependency_detected = True
                dependent.append((tool_call, task))
            else:
                independent.append((tool_call, task))
                seen_outputs.add(task.get("id", ""))

        return independent, dependent

    def _preview_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        preview = dict(task.get("params", {}))
        if "content" in preview and isinstance(preview["content"], str):
            preview["content"] = self._truncate(preview["content"], limit=120)
        if "value" in preview and isinstance(preview["value"], dict):
            preview["value"] = {key: self._truncate(str(value), limit=120) for key, value in preview["value"].items()}
        return {
            "title": task["title"],
            "params": preview,
        }

    def _handoff_from_tool_call(self, state: Dict[str, Any], tool_call) -> Dict[str, Any]:
        args = tool_call.arguments or {}
        options = []
        for raw in args.get("options") or []:
            if isinstance(raw, dict):
                options.append(
                    {
                        "id": raw.get("id") or f"option-{len(options) + 1}",
                        "label": raw.get("label") or str(raw.get("value") or raw.get("id") or f"Option {len(options) + 1}"),
                        "value": raw.get("value"),
                    }
                )
        return {
            "handoff_id": tool_call.id,
            "request_id": state["request_id"],
            "task_id": state.get("current_task_id"),
            "tool_call_id": tool_call.id,
            "kind": "user_input",
            "title": args.get("title") or "Preciso da sua ajuda para continuar",
            "prompt": args.get("prompt") or "Forneca mais contexto para eu continuar.",
            "reason": args.get("reason"),
            "status": "pending",
            "allow_free_text": bool(args.get("allow_free_text", True)),
            "options": options,
            "response": None,
        }

    @staticmethod
    def _salvage_partial_response(
        state: Dict[str, Any], step: int, llm_err: LLMApiError,
    ) -> str:
        """Build a user-facing message from whatever tool results were collected
        before the LLM API failed. This prevents losing work done in earlier steps."""
        completed_tasks = [
            t for t in state.get("tasks", [])
            if t.get("status") in {"completed", "partial"}
        ]
        if not completed_tasks:
            return (
                "A conexao com o modelo de linguagem falhou antes de iniciar. "
                "Tente novamente em alguns segundos."
            )
        parts = [
            f"Executei {len(completed_tasks)} etapa(s), mas a conexao com o modelo caiu "
            f"antes da analise final. Resumo dos resultados:"
        ]
        for t in completed_tasks:
            ar = ((t.get("result") or {}).get("action_result") or {})
            title = t.get("title", "")
            data = ar.get("data") or {}
            stdout = (data.get("stdout") or "").strip()
            clean = AgenticLoop._sanitize_output(stdout, max_len=150)
            if clean:
                parts.append(f"- {title}: {clean}")
            else:
                parts.append(f"- {title}: (sem saida)")
        parts.append("\nTente novamente para eu analisar e continuar.")
        return "\n".join(parts)

    @staticmethod
    def _salvage_budget_stop(
        state: Dict[str, Any], cost_tracker: CostTracker,
    ) -> str:
        """Build a user-facing message when the run is stopped due to budget limits."""
        completed_tasks = [
            t for t in state.get("tasks", [])
            if t.get("status") in {"completed", "partial"}
        ]
        parts = [
            f"Parei a execucao para proteger seu orcamento "
            f"(USD {cost_tracker.total_cost_usd:.4f} / {cost_tracker.total_tokens} tokens)."
        ]
        if completed_tasks:
            parts.append(f"Completei {len(completed_tasks)} etapa(s):")
            for t in completed_tasks:
                ar = ((t.get("result") or {}).get("action_result") or {})
                title = t.get("title", "")
                summary = ar.get("summary", "")
                parts.append(f"- {title}: {summary[:120] if summary else '(ok)'}")
        parts.append(
            "\nPara continuar, reenvie a instrucao. "
            "Considere simplificar a tarefa para reduzir o custo."
        )
        return "\n".join(parts)

    _MAX_TOOL_RESULT_CHARS = 3000

    def _compact_tool_result(self, result: Dict[str, Any]) -> str:
        """Serialize a tool result for the LLM, stripping binary data and
        capping total size to avoid token waste."""
        compact = dict(result)
        ar = compact.get("action_result")
        if isinstance(ar, dict):
            ar = dict(ar)
            compact["action_result"] = ar
            data = ar.get("data")
            if isinstance(data, dict):
                data = dict(data)
                ar["data"] = data
                for field in ("stdout", "stderr"):
                    raw = data.get(field)
                    if isinstance(raw, str) and raw:
                        data[field] = self._sanitize_output(raw, max_len=1500)
            ar.pop("diagnostics", None)
        compact.pop("diagnostics", None)
        tr = compact.get("tool_result")
        if isinstance(tr, dict):
            tr = dict(tr)
            compact["tool_result"] = tr
            tr.pop("raw", None)
            structured = tr.get("structured")
            if isinstance(structured, dict):
                structured = dict(structured)
                tr["structured"] = structured
                structured.pop("raw", None)
                res = structured.get("result")
                if isinstance(res, dict):
                    res = dict(res)
                    structured["result"] = res
                    for field in ("stdout", "stderr"):
                        raw = res.get(field)
                        if isinstance(raw, str) and raw:
                            res[field] = self._sanitize_output(raw, max_len=1500)
        serialized = json.dumps(compact, default=str, ensure_ascii=False)
        if len(serialized) > self._MAX_TOOL_RESULT_CHARS:
            compact_min = {
                "status": (ar or compact).get("status", "unknown"),
                "summary": (ar or compact).get("summary", ""),
            }
            if isinstance(ar, dict) and ar.get("data"):
                compact_min["data"] = ar["data"]
            serialized = json.dumps(compact_min, default=str, ensure_ascii=False)
            if len(serialized) > self._MAX_TOOL_RESULT_CHARS:
                serialized = serialized[:self._MAX_TOOL_RESULT_CHARS - 20] + '..."}'
        return serialized

    @staticmethod
    def _sanitize_output(text: str, *, max_len: int = 150) -> str:
        """Remove binary garbage, collapse whitespace, truncate."""
        if not text:
            return ""
        printable_ratio = sum(1 for c in text[:200] if c.isprintable() or c in "\n\t") / max(len(text[:200]), 1)
        if printable_ratio < 0.7:
            return "(dados binarios)"
        clean = " ".join(text.split())
        if len(clean) <= max_len:
            return clean
        return clean[:max_len] + "..."

    def _truncate(self, text: str, *, limit: int = 160) -> str:
        if len(text) <= limit:
            return text
        return f"{text[:limit - 3]}..."
