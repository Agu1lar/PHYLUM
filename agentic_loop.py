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


class AgenticLoop:
    def __init__(
        self,
        *,
        client: MultiProviderClient,
        safety: SafetyNode,
        tool_router: ToolRouterNode,
        reflection: ReflectionNode,
        max_steps: int = 8,
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

            if turn.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.content or None,
                        "tool_calls": [to_openai_tool_call(tool_call.id, tool_call.name, tool_call.arguments) for tool_call in turn.tool_calls],
                    }
                )
                for tool_call in turn.tool_calls:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError()
                    if tool_call.name == "request_user_input":
                        handoff = self._handoff_from_tool_call(state, tool_call)
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
                continue

            if turn.content:
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
            "Prefer direct, minimal actions. "
            "Never ask for or reveal API keys or secrets. "
            "When a tool is necessary, call it with well-formed arguments. "
            "If you need clarification, choices, credentials already stored by the app, or a decision from the user, call request_user_input instead of failing. "
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
