# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Direct tool execution for high-confidence intent profiles (Fase 1.3)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from intent_classifier import IntentClassification, build_resolved_tool_arguments
from intent_profile_registry import IntentProfile

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]
TaskFactory = Callable[[str, Dict[str, Any], int], Dict[str, Any]]
TaskExecuteFn = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]

_SUCCESS_STATUSES = frozenset({"completed", "partial", "succeeded"})

_SUMMARIZE_SYSTEM = (
    "You are PHYLUM. The user's request was handled by a native tool (fast path). "
    "Summarize the tool result in clear, direct language (2–4 sentences). "
    "Do not mention internal tools, profiles, or fast path. "
    "If the result is empty or failed, say so plainly and suggest one next step."
)


def _task_succeeded(result: Dict[str, Any]) -> bool:
    status = str(result.get("status") or "").lower()
    if status in _SUCCESS_STATUSES:
        return True
    action_result = result.get("action_result") or {}
    if isinstance(action_result, dict) and action_result.get("success") is True:
        return True
    return False


async def run_intent_fast_path(
    *,
    classification: IntentClassification,
    profile: IntentProfile,
    user_text: str,
    state: Dict[str, Any],
    provider_id: str,
    provider_config: Dict[str, Any],
    client: Any,
    emit: EmitFn,
    task_factory: TaskFactory,
    execute_task: TaskExecuteFn,
    compact_tool_result: Callable[[Dict[str, Any]], str],
    cost_tracker: Any,
) -> Optional[Dict[str, Any]]:
    """
    Execute profile default_action once, then one LLM turn (no tools) to reply.
    Returns a completed run dict on success, or None to fall back to full agentic loop.
    """
    tool_name = profile.default_action.tool
    arguments = build_resolved_tool_arguments(profile)
    request_id = state.get("request_id", "")

    await emit(
        "intent_fast_path_started",
        {
            "request_id": request_id,
            "profile_id": profile.id,
            "domain": profile.domain,
            "tool": tool_name,
            "action": profile.default_action.action,
        },
    )

    task = task_factory(tool_name, arguments, 0)
    task["execution_mode"] = "intent_fast_path"
    task["intent_profile_id"] = profile.id
    state.setdefault("tasks", []).append(task)
    await emit("task_planned", {"request_id": request_id, "task": task})

    try:
        task_result = await execute_task(state, task)
    except Exception as exc:
        logger.warning("Intent fast path execution failed: %s", exc)
        await emit(
            "intent_fast_path_failed",
            {
                "request_id": request_id,
                "profile_id": profile.id,
                "phase": "execute",
                "error": f"{exc.__class__.__name__}: {exc}",
            },
        )
        return None

    if not _task_succeeded(task_result):
        await emit(
            "intent_fast_path_failed",
            {
                "request_id": request_id,
                "profile_id": profile.id,
                "phase": "execute",
                "error": str(task_result.get("error") or task_result.get("status") or "task_failed"),
            },
        )
        return None

    tool_excerpt = compact_tool_result(task_result)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {
            "role": "user",
            "content": f"User request:\n{user_text}\n\nTool result:\n{tool_excerpt}",
        },
    ]

    try:
        turn = await client.complete(
            provider=provider_id,
            api_key=provider_config["api_key"],
            model=provider_config["model"],
            messages=messages,
            tools=[],
            base_url=provider_config.get("base_url"),
        )
        usage = turn.usage or {}
        cost_tracker.record_llm_turn(
            step=1,
            provider=provider_id,
            model=provider_config.get("model"),
            messages=messages,
            tools=[],
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )
    except Exception as exc:
        logger.warning("Intent fast path summarize failed: %s", exc)
        await emit(
            "intent_fast_path_failed",
            {
                "request_id": request_id,
                "profile_id": profile.id,
                "phase": "summarize",
                "error": f"{exc.__class__.__name__}: {exc}",
            },
        )
        return None

    final_text = (turn.content or "").strip()
    if not final_text:
        summary = (task_result.get("action_result") or {}).get("summary") if isinstance(
            task_result.get("action_result"), dict
        ) else None
        final_text = str(summary or "Tarefa concluída.")

    await emit(
        "intent_fast_path_completed",
        {
            "request_id": request_id,
            "profile_id": profile.id,
            "task_id": task.get("id"),
        },
    )

    session_messages: List[Dict[str, Any]] = [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": final_text},
    ]

    return {
        "status": "completed",
        "final_text": final_text,
        "steps": 1,
        "session": {"messages": session_messages, "step": 1},
        "execution_mode": "intent_fast_path",
        "intent_profile_id": profile.id,
    }
