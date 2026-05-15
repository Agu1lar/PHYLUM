# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Schema mirror ↔ canonical task equivalence (Fase 3.5).

Tool calls that only use fields exposed on the pruned LLM schema must normalize to the
same execution task as the full canonical schema via :func:`canonical_tools.normalize_agentic_task`.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple


class SchemaMirrorEquivalenceError(AssertionError):
    """Raised when mirror-accepted arguments do not match canonical normalization."""


def _function_block(schema: Mapping[str, Any]) -> Dict[str, Any]:
    fn = schema.get("function")
    return fn if isinstance(fn, dict) else {}


def mirror_property_names(mirror_schema: Mapping[str, Any]) -> Set[str]:
    """Property keys declared on the LLM mirror schema."""
    params = (_function_block(mirror_schema).get("parameters") or {})
    props = params.get("properties") if isinstance(params, dict) else None
    if not isinstance(props, dict):
        return set()
    return {str(key) for key in props}


def mirror_action_enum(mirror_schema: Mapping[str, Any]) -> Optional[Tuple[str, ...]]:
    """Allowed ``action`` values on the mirror, or ``None`` when there is no action property."""
    props = (_function_block(mirror_schema).get("parameters") or {}).get("properties") or {}
    if not isinstance(props, dict):
        return None
    action_spec = props.get("action")
    if not isinstance(action_spec, dict):
        return None
    enum = action_spec.get("enum")
    if isinstance(enum, list) and enum:
        return tuple(str(value) for value in enum)
    return None


def filter_arguments_to_mirror(
    arguments: Mapping[str, Any],
    mirror_schema: Mapping[str, Any],
) -> Dict[str, Any]:
    """Keep only argument keys present on the mirror schema."""
    allowed = mirror_property_names(mirror_schema)
    return {key: value for key, value in arguments.items() if key in allowed}


def prepare_arguments_for_mirror(
    tool_name: str,
    arguments: Mapping[str, Any],
    mirror_schema: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Filter to mirror properties and drop ``action`` when the mirror has no action field.

    Shell normalizes to ``action=run`` without an action property on the LLM schema.
    """
    filtered = filter_arguments_to_mirror(arguments, mirror_schema)
    if tool_name == "shell" and mirror_action_enum(mirror_schema) is None:
        filtered.pop("action", None)
    return filtered


def arguments_accepted_by_mirror(
    arguments: Mapping[str, Any],
    mirror_schema: Mapping[str, Any],
    *,
    tool_name: str = "",
) -> Tuple[bool, List[str]]:
    """
    Return whether *arguments* conform to the mirror schema surface.

    Checks property keys and ``action`` enum membership when applicable.
    """
    errors: List[str] = []
    allowed_props = mirror_property_names(mirror_schema)
    if not allowed_props and arguments:
        errors.append("mirror schema declares no properties")
        return False, errors

    for key in arguments:
        if key not in allowed_props:
            errors.append(f"unexpected property '{key}'")

    action_enum = mirror_action_enum(mirror_schema)
    action = arguments.get("action")
    if action_enum is not None:
        if action is None:
            params = _function_block(mirror_schema).get("parameters") or {}
            required = params.get("required") if isinstance(params, dict) else []
            if isinstance(required, list) and "action" in required:
                errors.append("missing required property 'action'")
        elif str(action) not in action_enum:
            errors.append(f"action '{action}' not in mirror enum {list(action_enum)}")
    elif tool_name == "shell" and "action" in arguments:
        errors.append("unexpected property 'action' on shell mirror")

    return (len(errors) == 0, errors)


def task_execution_identity(task: Mapping[str, Any]) -> Dict[str, Any]:
    """Stable subset used for equivalence: tool, action, params."""
    return {
        "tool": task.get("tool"),
        "action": task.get("action"),
        "params": deepcopy(task.get("params") or {}),
    }


def task_identity_on_mirror_surface(
    task: Mapping[str, Any],
    mirror_schema: Mapping[str, Any],
) -> Dict[str, Any]:
    """Task identity restricted to parameter keys exposed on the mirror schema."""
    allowed = mirror_property_names(mirror_schema)
    params = task.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    surface_params = {key: params[key] for key in params if key in allowed}
    return {
        "tool": task.get("tool"),
        "action": task.get("action"),
        "params": deepcopy(surface_params),
    }


def normalized_tasks_equivalent(
    tool_name: str,
    arguments: Mapping[str, Any],
    mirror_schema: Mapping[str, Any],
    *,
    task_id: str = "schema-equiv",
) -> bool:
    """
    True when ``normalize_agentic_task`` yields the same identity for *arguments*
    and for arguments prepared for the mirror schema.
    """
    from canonical_tools import normalize_agentic_task

    mirror_args = prepare_arguments_for_mirror(tool_name, arguments, mirror_schema)
    full_task = normalize_agentic_task(tool_name, dict(arguments), task_id)
    mirror_task = normalize_agentic_task(tool_name, mirror_args, task_id)
    return task_identity_on_mirror_surface(full_task, mirror_schema) == task_identity_on_mirror_surface(
        mirror_task, mirror_schema
    )


def assert_mirror_normalization_equivalent(
    tool_name: str,
    arguments: Mapping[str, Any],
    mirror_schema: Mapping[str, Any],
    *,
    task_id: str = "schema-equiv",
) -> Dict[str, Any]:
    """
    Verify mirror-accepted arguments normalize like the canonical schema.

    Returns the canonical normalized task dict.
    """
    from canonical_tools import normalize_agentic_task

    mirror_args = prepare_arguments_for_mirror(tool_name, arguments, mirror_schema)
    ok, errors = arguments_accepted_by_mirror(
        mirror_args, mirror_schema, tool_name=tool_name
    )
    if not ok:
        raise SchemaMirrorEquivalenceError(
            f"mirror-prepared arguments invalid for tool '{tool_name}': {errors}"
        )

    full_task = normalize_agentic_task(tool_name, dict(arguments), task_id)
    mirror_task = normalize_agentic_task(tool_name, mirror_args, task_id)

    full_surface = task_identity_on_mirror_surface(full_task, mirror_schema)
    mirror_surface = task_identity_on_mirror_surface(mirror_task, mirror_schema)
    if full_surface != mirror_surface:
        raise SchemaMirrorEquivalenceError(
            f"normalization mismatch on mirror surface for tool '{tool_name}': "
            f"full={full_surface!r} mirror_prepared={mirror_surface!r}"
        )
    return full_task


def profile_tool_arguments(profile: Any, tool_name: str) -> Dict[str, Any]:
    """
    Build mirror-valid sample arguments for a tool declared on an intent profile.

    Primary tool uses ``default_action`` + ``param_defaults`` (keys not on the mirror
    are dropped during equivalence checks). Secondary tools use ``tool_variants`` and
    small built-in samples — no user phrase heuristics.
    """
    if profile.default_action.tool == tool_name:
        args: Dict[str, Any] = dict(profile.default_action.params)
        if profile.default_action.action and tool_name != "shell":
            args.setdefault("action", profile.default_action.action)
        for key, value in profile.param_defaults.items():
            args.setdefault(key, value)
        if tool_name == "shell":
            args.pop("action", None)
        return args

    variant = (profile.tool_variants or {}).get(tool_name, "")
    if tool_name == "filesystem":
        if variant == "write":
            return {"action": "write", "path": "~/Downloads/export.txt", "content": "export"}
        return {"action": "list", "path": "~/Downloads"}
    if tool_name == "shell":
        return {
            "command": "Get-Date",
            "shell": "powershell",
            "timeout": 30,
        }
    if tool_name == "office":
        return {"action": "outlook_read_latest", "limit": 5, "folder": "inbox"}
    if tool_name == "driver_manager":
        return {"action": profile.default_action.action or "find_driver_candidates"}
    return {}
