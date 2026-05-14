# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from action_executor import RunPausedError
from canonical_tools import agentic_tool_definitions, to_openai_tool_call
from context_window import ContextWindowManager
from multi_provider_client import MultiProviderClient
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from prompt_cache import PromptCache


TaskFactory = Callable[[str, Dict[str, Any], int], Dict[str, Any]]
EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]
TaskExecuteFn = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]
CheckpointFn = Callable[[Dict[str, Any]], Awaitable[None]]


class AgenticLoop:
    def __init__(
        self,
        *,
        client: MultiProviderClient,
        safety: SafetyNode,
        tool_router: ToolRouterNode,
        reflection: ReflectionNode,
        max_steps: int = 16,
    ):
        self.client = client
        self.safety = safety
        self.tool_router = tool_router
        self.reflection = reflection
        self.max_steps = max_steps
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

        for step in range(start_step + 1, start_step + self.max_steps + 1):
            if cancel_event.is_set():
                raise asyncio.CancelledError()
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
            turn = await self.client.complete(
                provider=provider_id,
                api_key=provider_config["api_key"],
                model=provider_config["model"],
                messages=messages_for_llm,
                tools=tools_for_provider,
                base_url=provider_config.get("base_url"),
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
                                "content": json.dumps(subagent_results[tool_call.id], default=str),
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
                            "content": json.dumps(task_result, default=str),
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
                }
                if turn.thinking:
                    result["thinking"] = turn.thinking
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
                            "content": json.dumps(results_map.get(tc.id, {"status": "failed", "error": "execution skipped"}), default=str),
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
            "You are the agentic runtime for a desktop automation assistant on Windows. "
            "You are a fully autonomous agent that ACTS first and reports results after. "
            "\n\n"
            "== RESPONSE FORMAT ==\n"
            "Write responses as a competent colleague would speak — clean, natural, direct. "
            "NEVER use excessive emojis. NEVER use markdown tables for simple lists. NEVER use headers (##) excessively. "
            "Use plain text with occasional bold for emphasis. Keep responses concise and action-oriented. "
            "Bad example: '## 🔍 Diagnóstico\\n| # | Nome | Driver |\\n|---|------|--------|' "
            "Good example: 'Encontrei 3 impressoras instaladas: Microsoft Print to PDF, OneNote Desktop e RustDesk Printer. Todas são virtuais.' "
            "When presenting results, summarize the key findings in 2-3 sentences. Add details only if the user asks. "
            "\n\n"
            "== PROACTIVE AUTONOMY ==\n"
            "You MUST think associatively like a human assistant. When you complete a step, ask yourself: "
            "'What would the user logically want me to do NEXT based on what I just found?' "
            "Then DO IT — do not stop to ask permission for the obvious next step. "
            "\n"
            "Examples of associative thinking:\n"
            "- User asks 'install drivers from the network' -> you discover only virtual printers exist -> "
            "  NEXT: scan the network for physical printers/devices, check for shared printers, try to discover and configure them. "
            "  Do NOT stop to list virtual printers and ask 'which printer do you want?'. "
            "- User asks 'get my last 3 emails in a Word doc' -> you read the emails -> "
            "  NEXT: immediately create the Word document with the content. Do not ask where to save it, just pick a sensible default. "
            "- User asks 'organize my Downloads folder' -> you list the files -> "
            "  NEXT: immediately create folders by type and move files. Do not ask 'how should I organize?'. "
            "- User asks 'check if the VPN is working' -> you find it's disconnected -> "
            "  NEXT: try to reconnect it. Do not just report 'VPN is disconnected'. "
            "\n"
            "The rule: if the next step is OBVIOUS from context, DO IT. Only use request_user_input when: "
            "(1) there are multiple equally valid choices that depend on user preference, OR "
            "(2) you need information that cannot be discovered with the available tools (like a password or specific model name that doesn't appear anywhere in the system). "
            "Exhaust ALL discovery options before asking the user anything. "
            "\n\n"
            "== AVAILABLE TOOLS ==\n"
            "shell: Windows PowerShell and cmd commands. Do not assume shell is unavailable unless a command actually failed.\n"
            "desktop: list Explorer windows, mapped drives, open apps/files/folders, focus/close/kill windows and processes.\n"
            "windows_ui: inspect windows, find/list controls, invoke controls, type text, select items, send hotkeys, read UI state (via pywinauto, no pixel automation).\n"
            "share_discovery: enumerate mapped drives, inspect share roots, capture Explorer network context.\n"
            "document_intelligence: inspect documents, extract text, search content, list recent documents.\n"
            "office: Word, Excel, Outlook via COM — ALL headlessly in background. "
            "Actions: open_document, export_pdf, save_as_document, list_workbook_sheets, word_find_text, word_create_document, "
            "excel_read_range, outlook_read_latest, outlook_search_messages, draft_email_with_attachment, reveal_active_document_path.\n"
            "sandbox: execute dynamic Python or PowerShell scripts in a controlled sandbox. Use for custom data analysis, ad-hoc automation, or when no existing tool covers the task. "
            "Pass input_files to provide data.\n"
            "artifact: load, read, transform and analyze files internally (TXT, CSV, JSON, PDF, DOCX, XLSX, MSG) without opening desktop apps.\n"
            "dynamic_tool: create, persist, and execute reusable micro-tools (Python functions with a run(params) entry point).\n"
            "subagent: for complex discovery with independent branches, spawn isolated sub-agents in parallel, each with a specific objective and budget. "
            "Use this when branches like network discovery, driver inventory, and web research can run concurrently; merge their findings before deciding the next action.\n"
            "\n\n"
            "== WORLD MODEL & STRATEGY MEMORY ==\n"
            "Entity types: share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment. "
            "Before discovering from scratch, check if known: memory.world_find_share/app/alias/selector/path. "
            "After discovering, persist it: memory.world_remember_share/app/alias/selector/path. "
            "On successful reuse: memory.world_touch with boost_confidence. "
            "Confidence decays 0.05/day — stale knowledge is naturally deprioritized. "
            "For unfamiliar technical procedures, first query memory.world_query or memory.semantic_search_entities for web_resource. "
            "If there is no useful cached answer, use web.search_web as an internal learning tool, then fetch/read the strongest sources as needed and apply the learned procedure. "
            "Prefer official documentation, Microsoft Learn/docs.microsoft.com, StackOverflow/SuperUser/ServerFault, and vendor docs over blogs or SEO pages. "
            "Treat web results as observations for your own reasoning, not just as material to echo to the user. "
            "The web tool caches search results as web_resource entities so future similar tasks should reuse memory before re-searching. "
            "For strategies: check memory.strategy_best before complex tasks. Record success/failure after. "
            "Semantic search available: memory.semantic_search_strategies/entities for similarity-based lookup. "
            "\n\n"
            "== EXECUTION STRATEGY ==\n"
            "1. INTERNAL vs DESKTOP: Prefer internal/headless processing. "
            "office.outlook_read_latest reads emails without Outlook open. "
            "office.word_create_document creates docs without Word open. "
            "artifact.load processes files without opening apps. "
            "ONLY use desktop/windows_ui tools when the user needs to SEE or INTERACT visually. "
            "NEVER ask the user to open any application.\n"
            "2. COMPLEX TASKS: Prefer built-in office tool over sandbox scripts for Office operations. "
            "For custom processing, write sandbox scripts. Include error handling.\n"
            "3. TOOL GAPS: Check dynamic_tool.list first. If nothing exists, write sandbox scripts. "
            "For recurring needs, create dynamic tools.\n"
            "4. FAILURE RECOVERY: When a tool fails, switch approach — do NOT just retry or give up. "
            "Office COM fails -> sandbox scripts (openpyxl, python-docx). "
            "Browser fails -> web tool or sandbox urllib. "
            "Filesystem fails -> sandbox os/shutil. "
            "Always prefer an alternative script over asking the user.\n"
            "\n\n"
            "== PARALLEL SUB-AGENTS ==\n"
            "For complex tasks with independent discovery branches, use subagent.run_parallel_branches. "
            "Give each branch a narrow objective, context, success criteria, and explicit budget. "
            "Sub-agents have isolated context and return merged results to you; use those observations to choose and execute the final plan. "
            "Do not spawn sub-agents for simple linear tasks or branches that depend on each other's outputs. "
            "\n\n"
            "== DISCOVERY-FIRST APPROACH ==\n"
            "When the target is not fully known, discover it by inspecting the system: "
            "Explorer windows, mapped drives, UNC shares, network devices, installed software, web sources. "
            "Translate imprecise names into likely Windows paths/commands and test hypotheses. "
            "Use Get-ChildItem, Get-PSDrive, net use, net view, ping, and other shell commands for discovery. "
            "If a file might be on a network share, use desktop.list_explorer_windows first. "
            "Always verify actions with a follow-up check (list_windows, list_processes, filesystem.stat). "
            "\n\n"
            "== GRAPH-BASED RECOVERY ==\n"
            "The runtime uses a state graph. Recovery determines the exact node to return to: "
            "retry->executor, replan->planner, verify->reflection, ask_user->handoff, script->script_recovery. "
            "\n\n"
            "Never ask for or reveal API keys or secrets. "
            "Call tools with well-formed, complete arguments — especially 'content' and 'code' fields which must contain the full text. "
            "Treat every tool result as an observation. Replan on failure. "
            "When the goal is achieved, respond concisely with what was done and where results are."
        )

    @staticmethod
    def _normalize_subagent_budget(
        budget: Dict[str, Any],
        *,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = dict(defaults or {
            "max_steps": 3,
            "timeout_seconds": 90,
            "max_tool_calls": 8,
            "max_context_chars": 24000,
            "max_estimated_tokens": 6000,
            "max_cost_usd": 0.0,
            "estimated_usd_per_1k_tokens": 0.0,
        })
        limits = {
            "max_steps": (1, 8),
            "timeout_seconds": (5, 300),
            "max_tool_calls": (0, 20),
            "max_context_chars": (1000, 120000),
            "max_estimated_tokens": (250, 30000),
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

    def _truncate(self, text: str, *, limit: int = 160) -> str:
        if len(text) <= limit:
            return text
        return f"{text[:limit - 3]}..."
