# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

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

                planned: List[tuple] = []
                for tool_call in executable_calls:
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
                            if isinstance(item, asyncio.CancelledError):
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
            "\n\n"
            "== WORLD MODEL & STRATEGY MEMORY ==\n"
            "Entity types: share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment. "
            "Before discovering from scratch, check if known: memory.world_find_share/app/alias/selector/path. "
            "After discovering, persist it: memory.world_remember_share/app/alias/selector/path. "
            "On successful reuse: memory.world_touch with boost_confidence. "
            "Confidence decays 0.05/day — stale knowledge is naturally deprioritized. "
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
