# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for workspace awareness and refactor guardrails."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionResult
from refactor_guardrails import (
    RefactorScope,
    check_proposed_changes,
    clear_refactor_scope,
    detect_unrelated_touches,
    get_refactor_scope,
    get_session_touches,
    bind_refactor_scope,
    validate_paths,
)
from tool_base import BaseTool
from workspace_awareness import detect_workspace_context

logger = logging.getLogger(__name__)


class WorkspaceDevRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(detect_context|set_scope|get_scope|clear_scope|validate_path|"
                "check_changes|audit_touches)$",
    )
    workspace: Optional[str] = None
    target_files: Optional[List[str]] = None
    allowed_globs: Optional[List[str]] = None
    allowed_directories: Optional[List[str]] = None
    description: Optional[str] = None
    strict: Optional[bool] = None
    allow_tests: Optional[bool] = None
    allow_same_package: Optional[bool] = None
    path: Optional[str] = None
    paths: Optional[List[str]] = None
    changes: Optional[List[Dict[str, Any]]] = None


class WorkspaceDevTool(BaseTool):
    InputModel = WorkspaceDevRequest
    OutputModel = ActionResult

    def __init__(self):
        super().__init__(default_timeout=60, default_retries=1)
        self._scope_token = None

    def _workspace(self, payload: WorkspaceDevRequest) -> str:
        return payload.workspace or os.getcwd()

    async def _run(self, payload: WorkspaceDevRequest) -> ActionResult:
        action = payload.action
        try:
            if action == "detect_context":
                snap = detect_workspace_context(self._workspace(payload))
                summary_parts = []
                if snap.git_branch:
                    summary_parts.append(f"branch={snap.git_branch}")
                if snap.venv_path:
                    summary_parts.append("venv")
                if snap.ides:
                    summary_parts.append(f"{len(snap.ides)} IDE(s)")
                if snap.dev_ports:
                    summary_parts.append(f"ports={','.join(str(p['port']) for p in snap.dev_ports)}")
                summary = "Workspace context: " + (", ".join(summary_parts) or snap.workspace)
                return ActionResult(
                    status="succeeded",
                    summary=summary,
                    tool="workspace_dev",
                    action=action,
                    data=snap.to_dict(),
                )

            if action == "set_scope":
                scope = RefactorScope(
                    workspace=self._workspace(payload),
                    target_files=list(payload.target_files or []),
                    allowed_globs=list(payload.allowed_globs or []),
                    allowed_directories=list(payload.allowed_directories or []),
                    description=payload.description or "",
                    strict=True if payload.strict is None else bool(payload.strict),
                    allow_tests=True if payload.allow_tests is None else bool(payload.allow_tests),
                    allow_same_package=(
                        True if payload.allow_same_package is None else bool(payload.allow_same_package)
                    ),
                )
                if self._scope_token is not None:
                    clear_refactor_scope()
                self._scope_token = bind_refactor_scope(scope)
                return ActionResult(
                    status="succeeded",
                    summary=(
                        f"Refactor scope set: {len(scope.target_files)} target file(s), "
                        f"{len(scope.allowed_globs)} glob(s), strict={scope.strict}"
                    ),
                    tool="workspace_dev",
                    action=action,
                    data=scope.to_dict(),
                )

            if action == "get_scope":
                scope = get_refactor_scope()
                if scope is None:
                    return ActionResult(
                        status="succeeded",
                        summary="No refactor scope active",
                        tool="workspace_dev",
                        action=action,
                        data={"active": False},
                    )
                touches = get_session_touches(scope.workspace)
                return ActionResult(
                    status="succeeded",
                    summary=f"Active scope: {len(scope.target_files)} target(s), {len(touches)} touched file(s)",
                    tool="workspace_dev",
                    action=action,
                    data={"active": True, "scope": scope.to_dict(), "touched_files": touches},
                )

            if action == "clear_scope":
                clear_refactor_scope()
                self._scope_token = None
                return ActionResult(
                    status="succeeded",
                    summary="Refactor scope cleared",
                    tool="workspace_dev",
                    action=action,
                    data={"active": False},
                )

            if action == "validate_path":
                scope = get_refactor_scope()
                if scope is None:
                    return ActionResult(
                        status="failed",
                        summary="No refactor scope — call set_scope first",
                        tool="workspace_dev",
                        action=action,
                    )
                paths = list(payload.paths or [])
                if payload.path:
                    paths.append(payload.path)
                if not paths:
                    return ActionResult(
                        status="failed",
                        summary="'path' or 'paths' required",
                        tool="workspace_dev",
                        action=action,
                    )
                report = validate_paths(paths, scope)
                return ActionResult(
                    status="succeeded" if report.ok else "failed",
                    summary=(
                        f"Validation: {len(report.allowed)} allowed, "
                        f"{len(report.blocked)} blocked, {len(report.warnings)} warning(s)"
                    ),
                    tool="workspace_dev",
                    action=action,
                    data=report.to_dict(),
                )

            if action == "check_changes":
                scope = get_refactor_scope()
                if scope is None and not payload.target_files:
                    return ActionResult(
                        status="failed",
                        summary="Provide active scope (set_scope) or target_files in request",
                        tool="workspace_dev",
                        action=action,
                    )
                if scope is None:
                    scope = RefactorScope(
                        workspace=self._workspace(payload),
                        target_files=list(payload.target_files or []),
                        allowed_globs=list(payload.allowed_globs or []),
                        strict=True if payload.strict is None else bool(payload.strict),
                    )
                if not payload.changes:
                    return ActionResult(
                        status="failed",
                        summary="'changes' list required",
                        tool="workspace_dev",
                        action=action,
                    )
                report = check_proposed_changes(payload.changes, scope)
                return ActionResult(
                    status="succeeded" if report.ok else "failed",
                    summary=(
                        f"Change check: {len(report.blocked)} out-of-scope, "
                        f"{len(report.warnings)} related warning(s)"
                    ),
                    tool="workspace_dev",
                    action=action,
                    data=report.to_dict(),
                )

            if action == "audit_touches":
                scope = get_refactor_scope()
                if scope is None:
                    return ActionResult(
                        status="failed",
                        summary="No refactor scope — call set_scope first",
                        tool="workspace_dev",
                        action=action,
                    )
                report = detect_unrelated_touches(
                    scope,
                    extra_paths=payload.paths,
                )
                return ActionResult(
                    status="succeeded" if report.ok else "failed",
                    summary=(
                        f"Touch audit: {len(get_session_touches(scope.workspace))} file(s) touched, "
                        f"{len(report.blocked)} unrelated"
                    ),
                    tool="workspace_dev",
                    action=action,
                    data={
                        **report.to_dict(),
                        "touched_files": get_session_touches(scope.workspace),
                    },
                )

            return ActionResult(
                status="failed",
                summary=f"Unknown action: {action}",
                tool="workspace_dev",
                action=action,
            )
        except Exception as exc:
            logger.exception("workspace_dev failed")
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="workspace_dev",
                action=action,
            )
