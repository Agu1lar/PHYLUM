# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for the test diagnostic loop."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionResult
from test_diagnostic_loop import TestDiagnosticLoop, DiagnosticSession
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class TestDiagnosticRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(run_and_diagnose|start_session|iterate|get_session)$",
    )
    workspace: Optional[str] = None
    target: Optional[str] = None
    framework: str = "pytest"
    extra_args: Optional[List[str]] = None
    session_id: Optional[str] = None
    max_iterations: Optional[int] = None
    test_timeout: Optional[int] = None


class TestDiagnosticTool(BaseTool):
    InputModel = TestDiagnosticRequest

    def __init__(self):
        super().__init__(default_timeout=180, default_retries=1)
        self._loops: Dict[str, TestDiagnosticLoop] = {}
        self._sessions: Dict[str, DiagnosticSession] = {}

    def _get_loop(self, workspace: Optional[str], **kwargs) -> TestDiagnosticLoop:
        ws = workspace or os.getcwd()
        key = ws
        if key not in self._loops:
            self._loops[key] = TestDiagnosticLoop(ws, **kwargs)
        return self._loops[key]

    async def _run(self, payload: TestDiagnosticRequest) -> ActionResult:
        action = payload.action
        loop_kwargs: Dict[str, Any] = {}
        if payload.max_iterations:
            loop_kwargs["max_iterations"] = payload.max_iterations
        if payload.test_timeout:
            loop_kwargs["test_timeout"] = payload.test_timeout

        try:
            loop = self._get_loop(payload.workspace, **loop_kwargs)

            if action == "run_and_diagnose":
                it = loop.run_and_diagnose(
                    payload.target or "",
                    framework=payload.framework,
                    extra_args=payload.extra_args,
                )
                status = "all passed" if it.fixed else f"{len(it.diagnoses)} failure(s) diagnosed"
                return ActionResult(
                    status="succeeded",
                    summary=f"Test run: {status}",
                    tool="test_diagnostic", action=action,
                    data=it.to_dict(),
                )

            if action == "start_session":
                session = loop.get_session(payload.target or "")
                self._sessions[session.session_id] = session
                return ActionResult(
                    status="succeeded",
                    summary=f"Started diagnostic session '{session.session_id}' for '{payload.target}'",
                    tool="test_diagnostic", action=action,
                    data={"session_id": session.session_id, "target": session.target},
                )

            if action == "iterate":
                sid = payload.session_id
                if not sid or sid not in self._sessions:
                    return ActionResult(
                        status="failed",
                        summary=f"Session '{sid}' not found. Start a session first.",
                        tool="test_diagnostic", action=action,
                    )
                session = self._sessions[sid]
                it = loop.iterate(
                    session,
                    framework=payload.framework,
                    extra_args=payload.extra_args,
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Iteration {it.iteration} ({it.phase}): "
                            f"{'fixed' if it.fixed else f'{len(it.diagnoses)} failure(s)'}. "
                            f"Session status: {session.status}",
                    tool="test_diagnostic", action=action,
                    data={
                        "iteration": it.to_dict(),
                        "session_status": session.status,
                        "session_id": sid,
                    },
                )

            if action == "get_session":
                sid = payload.session_id
                if not sid or sid not in self._sessions:
                    return ActionResult(
                        status="failed",
                        summary=f"Session '{sid}' not found",
                        tool="test_diagnostic", action=action,
                    )
                return ActionResult(
                    status="succeeded",
                    summary=f"Session '{sid}': {self._sessions[sid].status}",
                    tool="test_diagnostic", action=action,
                    data=self._sessions[sid].to_dict(),
                )

            return ActionResult(
                status="failed", summary=f"Unknown action: {action}",
                tool="test_diagnostic", action=action,
            )

        except Exception as exc:
            return ActionResult(
                status="failed", summary=str(exc),
                tool="test_diagnostic", action=action,
            )
