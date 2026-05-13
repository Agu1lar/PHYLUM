from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from canonical_tools import agentic_tool_definitions, to_openai_tool_call
from multi_provider_client import MultiProviderClient
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode


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
        messages: List[Dict[str, Any]] = list((session or {}).get("messages") or [])
        if not messages:
            messages = [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": state["inputs"].get("text") or state["inputs"].get("prompt") or ""},
            ]
        tools = agentic_tool_definitions()
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
                    "summary": f"Calling {provider_config['provider']}:{provider_config['model']}",
                },
            )
            turn = await self.client.complete(
                provider=provider_config["provider"],
                api_key=provider_config["api_key"],
                model=provider_config["model"],
                messages=messages,
                tools=tools,
                base_url=provider_config.get("base_url"),
            )

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
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.content or None,
                        "tool_calls": [to_openai_tool_call(tool_call.id, tool_call.name, tool_call.arguments) for tool_call in turn.tool_calls],
                    }
                )
                if checkpoint is not None:
                    await checkpoint({"messages": messages, "step": step})
                for tool_call in turn.tool_calls:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError()
                    if tool_call.name == "request_user_input":
                        handoff = self._handoff_from_tool_call(state, tool_call)
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
                    task_result = await self._execute_task(
                        state=state,
                        task=task,
                        emit=emit,
                        cancel_event=cancel_event,
                        execute_task=execute_task,
                    )
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
                messages.append({"role": "assistant", "content": turn.content})
                if checkpoint is not None:
                    await checkpoint({"messages": messages, "step": step, "paused_reason": None})
                return {
                    "status": "completed",
                    "final_text": turn.content,
                    "steps": step,
                    "session": {"messages": messages, "step": step},
                }
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
            "You are the agentic runtime for a desktop automation assistant. "
            "Use the available tools to complete the user's request. "
            "The shell tool is available in this environment and can run Windows PowerShell or cmd commands; do not assume shell is unavailable unless a shell command actually failed. "
            "The desktop tool can list Explorer windows, list mapped drives, inspect Explorer selection, open apps, open files and folders, wait for windows, focus windows, close windows, and kill processes. "
            "The windows_ui tool can inspect native windows, list or find controls, wait for elements, invoke controls, type text, select items, send hotkeys and read focused UI state without pixel automation. "
            "The share_discovery tool can enumerate mapped drives, inspect share roots and capture Explorer network context. "
            "The document_intelligence tool can inspect documents, extract text, search content and list recent documents. "
            "The office tool can use Office COM to open documents, export PDFs, inspect workbooks, reveal active document paths and draft Outlook emails. "
            "The sandbox tool can execute dynamic Python or PowerShell scripts in a controlled sandbox for on-demand problem solving. "
            "Use sandbox.execute_python or sandbox.execute_powershell when no existing tool covers the task, or when you need custom data analysis, complex transformations, ad-hoc automation, or to write scripts that solve a specific problem the user described. "
            "You can pass input_files to the sandbox to provide data for the script to work with. "
            "The artifact tool can load, read, transform and analyze files internally (text, CSV, JSON, PDF, DOCX, XLSX, MSG) without opening them on the user's desktop. "
            "Use artifact.load to read a file into memory, artifact.transform to summarize/filter/extract data, and artifact.write_result to save processed output. "
            "If the user asks to read, analyze or transform a file, prefer using the artifact tool to process it internally and return the result directly. "
            "The dynamic_tool tool can create, persist, and execute reusable micro-tools during a run. "
            "Use dynamic_tool.create when you encounter a recurring need that no native tool covers, writing a small Python function with a run(params) entry point. "
            "Use dynamic_tool.execute to invoke a previously created tool, and dynamic_tool.list to discover existing ones. "
            "\n\n"
            "WORLD MODEL: The memory tool now has a typed world model with confidence-scored entities and TTL expiration. "
            "Entity types: share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment. "
            "Before discovering something from scratch, first check if it is already known: "
            "use memory.world_find_share, memory.world_find_app, memory.world_find_alias, memory.world_find_selector, or memory.world_find_path to look up cached knowledge. "
            "After successfully discovering a share, app path, document alias, UI selector, or file path, persist it: "
            "use memory.world_remember_share, memory.world_remember_app, memory.world_remember_alias, memory.world_remember_selector, or memory.world_remember_path. "
            "When a known entity is reused successfully, call memory.world_touch with boost_confidence to reinforce it. "
            "For general typed entities, use memory.world_upsert/world_get/world_query with entity_type, confidence, source, tags, and optional ttl_seconds. "
            "Entities automatically expire based on their TTL; use memory.world_prune to clean up stale entries. "
            "Confidence decays over time (0.05/day) so stale knowledge is naturally deprioritized. "
            "\n\n"
            "STRATEGY MEMORY: Before executing a complex task, check if a proven strategy exists: "
            "use memory.strategy_best with the goal_type (e.g. 'open_document', 'install_software', 'find_file_on_share'). "
            "If a strategy is found, follow its steps and call memory.strategy_reused to reinforce it. "
            "After successfully completing a multi-step task, record the strategy: "
            "use memory.strategy_record_success with goal_type, strategy_id, goal_summary, and the steps taken. "
            "After a strategy fails, record it with memory.strategy_record_failure to avoid repeating the same approach. "
            "Use memory.strategy_find to search for strategies by goal_type and optional query/tags. "
            "\n\n"
            "AUTOMATIC REUSE: When you need a UI selector, path, share, or app location, always check the world model first. "
            "Prefer reusing a high-confidence cached value over re-discovering from scratch. "
            "If a cached selector or path fails at runtime, lower its confidence (or delete it) and re-discover. "
            "\n\n"
            "Plan and generalize from the user's goal instead of relying on hardcoded workflows. "
            "For complex tasks that mix web browsing, file manipulation and data extraction, decompose them into multiple autonomous steps. "
            "Think of multi-step plans: first discover/locate targets, then process data, then produce output. "
            "If a standard tool cannot accomplish a sub-step, create a sandbox script or dynamic tool to fill the gap. "
            "Follow reusable search policies: discoverTarget -> verifyCandidate -> act -> verifyOutcome -> replan. "
            "For Office work, prefer document_intelligence or share_discovery to locate files, office for COM-native actions, and windows_ui only if COM is unavailable or incomplete. "
            "For browser flows that spawn native dialogs, use browser.bridge_native_dialog or windows_ui instead of assuming everything remains inside the DOM. "
            "For ambiguous UI matches, prefer inspect_window/list_elements/find_element with progressively narrower selectors before asking the user. "
            "Prefer direct, minimal actions, but when the target is not fully known, discover it by inspecting the system, open File Explorer windows, mapped drives, UNC shares, available devices, installed software, or web sources before asking the user. "
            "When the user gives an imprecise name, translate it into likely Windows paths, application names, shares, drives, or commands and test those hypotheses one by one. "
            "For files or folders on Windows, prefer practical discovery: inspect open Explorer windows, enumerate mapped drives, inspect network shares, search recent documents, search document content, and use shell-based searches such as Get-ChildItem, Get-PSDrive, net use, or other native commands before giving up. "
            "If a file might be on a network share already open in Explorer, use desktop.list_explorer_windows first to capture the real path instead of guessing the UNC root. "
            "For requests like opening Word, Excel, Explorer, a folder, or a file, prefer desktop.open_app, desktop.open_path, or desktop.open_file, then verify the result with desktop.wait_for_window, desktop.list_windows, or desktop.list_processes. "
            "If a tool returns a partial result because the action still needs verification, do that verification before declaring success. "
            "Do not stop after the first failure when there are still plausible hypotheses to test. Replan aggressively using the real error or tool output. "
            "When a native tool fails or is unavailable, consider writing a sandbox script or creating a dynamic tool to achieve the same goal through an alternative path. "
            "Never ask for or reveal API keys or secrets. "
            "When a tool is necessary, call it with well-formed arguments. "
            "Use request_user_input only when the required information cannot be discovered with the available tools, or when a human decision is required. "
            "Treat every tool result as an observation. If a tool returns blocked, partial, failed, or needs_input, use that observation to replan before giving up. "
            "When the goal is achieved, answer concisely."
        )

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
