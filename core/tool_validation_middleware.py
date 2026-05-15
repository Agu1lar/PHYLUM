# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Pre-execution tool validation and automatic validation-error re-injection for the agentic loop.

Validation always uses registry :class:`~pydantic.BaseModel` input types (the canonical
tool surface from ``tools/*``). Pruned JSON schemas sent to the LLM (Fase 3.3 mirror)
are never used here — a tool call valid on the mirror is checked against the full model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    from tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_MAX_REINJECTIONS_PER_STEP = 8
DEFAULT_MAX_REINJECTIONS_PER_TOOL_CALL = 3

_FIELD_HINTS: Dict[str, str] = {
    "content": "file body as a string",
    "path": "absolute or workspace-relative file path",
    "command": "full shell command to run",
    "action": "tool action name from the schema enum",
    "name": "resource name (skill name, file name, etc.)",
    "query": "search query string",
    "objective": "natural-language goal for discovery",
    "params": "execution parameters object",
    "code": "Python source with a run(params) function",
    "url": "full HTTP(S) URL",
    "package_path": "local folder or archive path",
}


@dataclass
class ToolPreValidationResult:
    ok: bool
    tool: str
    action: str = ""
    errors: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    reinjection_message: str = ""
    prevalidated: bool = False
    validation_schema_source: str = "canonical_input_model"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "action": self.action,
            "errors": self.errors,
            "missing_fields": self.missing_fields,
            "reinjection_message": self.reinjection_message,
            "prevalidated": self.prevalidated,
            "validation_schema_source": self.validation_schema_source,
        }


@dataclass
class ReinjectionBudget:
    """Per-step cap to avoid infinite validate→reinject loops."""

    max_per_step: int = DEFAULT_MAX_REINJECTIONS_PER_STEP
    max_per_tool_call: int = DEFAULT_MAX_REINJECTIONS_PER_TOOL_CALL
    step_counts: Dict[int, int] = field(default_factory=dict)
    tool_call_counts: Dict[str, int] = field(default_factory=dict)

    def can_reinject(self, *, step: int, tool_call_id: str) -> bool:
        if self.step_counts.get(step, 0) >= self.max_per_step:
            return False
        if self.tool_call_counts.get(tool_call_id, 0) >= self.max_per_tool_call:
            return False
        return True

    def record(self, *, step: int, tool_call_id: str) -> None:
        self.step_counts[step] = self.step_counts.get(step, 0) + 1
        self.tool_call_counts[tool_call_id] = self.tool_call_counts.get(tool_call_id, 0) + 1


class ToolValidationMetrics:
    """In-memory counters for confusing tool schemas (per process)."""

    def __init__(self) -> None:
        self.prevalidation_blocked: int = 0
        self.prevalidation_passed: int = 0
        self.reinjections: int = 0
        self.by_tool: Dict[str, Dict[str, int]] = {}

    def record_block(self, tool: str, action: str) -> None:
        self.prevalidation_blocked += 1
        bucket = self.by_tool.setdefault(tool, {"blocked": 0, "passed": 0, "reinjections": 0})
        bucket["blocked"] = bucket.get("blocked", 0) + 1
        key = f"{tool}.{action}" if action else tool
        self.by_tool.setdefault(key, {"blocked": 0, "passed": 0})
        self.by_tool[key]["blocked"] = self.by_tool[key].get("blocked", 0) + 1

    def record_pass(self, tool: str) -> None:
        self.prevalidation_passed += 1
        bucket = self.by_tool.setdefault(tool, {"blocked": 0, "passed": 0, "reinjections": 0})
        bucket["passed"] = bucket.get("passed", 0) + 1

    def record_reinjection(self, tool: str) -> None:
        self.reinjections += 1
        bucket = self.by_tool.setdefault(tool, {"blocked": 0, "passed": 0, "reinjections": 0})
        bucket["reinjections"] = bucket.get("reinjections", 0) + 1

    def top_confusing_tools(self, limit: int = 10) -> List[Dict[str, Any]]:
        ranked = sorted(
            (
                {"tool": name, **counts}
                for name, counts in self.by_tool.items()
                if counts.get("blocked", 0) > 0
            ),
            key=lambda x: x.get("blocked", 0),
            reverse=True,
        )
        return ranked[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prevalidation_blocked": self.prevalidation_blocked,
            "prevalidation_passed": self.prevalidation_passed,
            "reinjections": self.reinjections,
            "top_confusing_tools": self.top_confusing_tools(),
        }


_metrics = ToolValidationMetrics()


def get_validation_metrics() -> ToolValidationMetrics:
    return _metrics


def _extract_missing_fields(exc: ValidationError) -> List[str]:
    missing: List[str] = []
    for err in exc.errors():
        if err.get("type") == "missing":
            loc = err.get("loc") or ()
            if loc:
                missing.append(str(loc[-1]))
    return missing


def _format_pydantic_errors(exc: ValidationError) -> List[str]:
    lines: List[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in (err.get("loc") or ()))
        msg = err.get("msg", "invalid value")
        lines.append(f"{loc}: {msg}" if loc else str(msg))
    return lines


def _hint_for_field(field: str) -> str:
    return _FIELD_HINTS.get(field, f"value for '{field}'")


def build_reinjection_message(
    *,
    tool: str,
    action: str,
    errors: List[str],
    missing_fields: List[str],
    arguments: Optional[Dict[str, Any]] = None,
) -> str:
    """Human/LLM-oriented message injected as tool output without executing the tool."""
    parts = [
        f"PRE_VALIDATION_FAILED: {tool}.{action or '?'} was not executed because arguments are invalid.",
    ]
    if missing_fields:
        hints = ", ".join(f"'{f}' ({_hint_for_field(f)})" for f in missing_fields)
        parts.append(f"Missing required field(s): {hints}. Re-generate the tool call including all required fields.")
    if errors:
        parts.append("Validation errors:")
        parts.extend(f"  - {e}" for e in errors[:8])
        parts.append("Re-generate the tool call with corrected arguments.")
    if arguments is not None:
        preview = {k: v for k, v in arguments.items() if k not in ("content", "code", "value")}
        if preview:
            parts.append(f"You sent: {preview}")
    parts.append(
        "Fix the arguments and call the same tool again in this turn. "
        "Do not assume the action ran."
    )
    return "\n".join(parts)


def _validation_result_from_exception(
    *,
    tool: str,
    action: str,
    exc: Exception,
    arguments: Dict[str, Any],
) -> ToolPreValidationResult:
    missing: List[str] = []
    errors: List[str] = []
    if isinstance(exc, ValidationError):
        missing = _extract_missing_fields(exc)
        errors = _format_pydantic_errors(exc)
    else:
        msg = str(exc).strip() or exc.__class__.__name__
        errors = [msg]
        for match in re.finditer(r"requires?\s+'(\w+)'", msg, re.I):
            missing.append(match.group(1))
        for match in re.finditer(r"'(\w+)'\s+is required", msg, re.I):
            missing.append(match.group(1))
        for match in re.finditer(r"(\w+)\s+is required", msg, re.I):
            missing.append(match.group(1))
        for match in re.finditer(r"(\w+)\s+required for", msg, re.I):
            missing.append(match.group(1))

    reinjection = build_reinjection_message(
        tool=tool,
        action=action,
        errors=errors,
        missing_fields=list(dict.fromkeys(missing)),
        arguments=arguments,
    )
    return ToolPreValidationResult(
        ok=False,
        tool=tool,
        action=action,
        errors=errors,
        missing_fields=list(dict.fromkeys(missing)),
        reinjection_message=reinjection,
        prevalidated=True,
    )


def build_validation_failure_task_result(
    *,
    tool: str,
    action: str,
    validation: ToolPreValidationResult,
    task_id: str = "",
) -> Dict[str, Any]:
    """Structured task result matching execute_task shape for message compaction."""
    return {
        "status": "failed",
        "error": validation.reinjection_message,
        "validation_reinjected": True,
        "prevalidation": validation.to_dict(),
        "action_result": {
            "status": "failed",
            "summary": validation.reinjection_message.split("\n", 1)[0],
            "tool": tool,
            "action": action,
            "semantic_type": "inspection",
            "target": {},
            "data": {"reinjection_message": validation.reinjection_message},
            "effects": {"changed": False},
            "issue": {
                "kind": "validation",
                "message": validation.reinjection_message,
                "retryable": True,
            },
        },
        "task_id": task_id,
    }


async def prevalidate_tool_call(
    registry: "ToolRegistry",
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    task_id: str = "",
) -> ToolPreValidationResult:
    """Validate tool arguments before safety/approval/execution (saves a full agent step)."""
    from canonical_tools import normalize_agentic_task

    if not registry.supports(tool_name):
        return ToolPreValidationResult(
            ok=False,
            tool=tool_name,
            errors=[f"unsupported tool '{tool_name}'"],
            reinjection_message=build_reinjection_message(
                tool=tool_name,
                action="",
                errors=[f"Tool '{tool_name}' is not available."],
                missing_fields=[],
                arguments=arguments,
            ),
            prevalidated=True,
        )

    task = normalize_agentic_task(tool_name, arguments or {}, task_id or "prevalidate")
    action = task.get("action", "")
    tool = task.get("tool", tool_name)

    try:
        tool_impl = registry.tools[tool_name]
        payload = registry.build_payload(task, request_id=task.get("request_id"))
        input_model = tool_impl.InputModel(**payload)
        await tool_impl.validate(input_model)
    except Exception as exc:
        get_validation_metrics().record_block(tool, action)
        return _validation_result_from_exception(
            tool=tool,
            action=action,
            exc=exc,
            arguments=arguments or {},
        )

    get_validation_metrics().record_pass(tool)
    return ToolPreValidationResult(
        ok=True,
        tool=tool,
        action=action,
        prevalidated=True,
    )
