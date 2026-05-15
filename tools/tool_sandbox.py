# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool wrapper for sandbox execution (Python/PowerShell scripts)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from sandbox_executor import SandboxExecutor
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class SandboxInput(BaseModel):
    action: str = Field(..., description="Action: execute_python, execute_powershell")
    code: str = Field(..., min_length=1, description="Script source code to execute")
    timeout: Optional[int] = Field(None, gt=0, le=300, description="Timeout in seconds")
    work_dir: Optional[str] = Field(None, description="Working directory override")
    input_files: Optional[Dict[str, str]] = Field(None, description="Files to create in sandbox before execution (name -> content)")
    capabilities: Optional[list] = Field(
        None,
        description="Declared capabilities (e.g. filesystem:read, network:outbound, shell:run)",
    )
    request_id: Optional[str] = Field(None, description="Run id — use run filesystem scope as work_dir when work_dir omitted")


class SandboxOutput(BaseModel):
    ok: bool
    success: bool
    message: str
    details: Dict[str, Any]


class SandboxTool(BaseTool):
    InputModel = SandboxInput
    OutputModel = SandboxOutput

    def __init__(self, *, default_timeout: int = 60, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.executor = SandboxExecutor(default_timeout=default_timeout)

    async def validate(self, payload: SandboxInput) -> None:
        if payload.action not in ("execute_python", "execute_powershell"):
            raise ValueError(f"Unsupported sandbox action: {payload.action}")

    async def _run(self, payload: SandboxInput, cancel_event=None) -> SandboxOutput:
        work_dir = payload.work_dir
        if not work_dir and payload.request_id:
            try:
                from filesystem_scope import get_run_filesystem_scope, scope_from_state
                from agent_persistence import Persistence

                scope = get_run_filesystem_scope()
                if scope is None:
                    raw = await Persistence.get().get_kv(f"state:{payload.request_id}")
                    if isinstance(raw, dict):
                        scope = scope_from_state(raw)
                if scope is not None:
                    work_dir = scope.sandbox_dir
            except Exception:
                logger.debug("Could not resolve run filesystem scope for sandbox", exc_info=True)

        kwargs = {
            "timeout": payload.timeout,
            "work_dir": work_dir,
            "cancel_event": cancel_event,
            "input_files": payload.input_files,
            "capabilities": payload.capabilities,
            "script_id": payload.request_id,
        }

        if payload.action == "execute_python":
            result = await self.executor.execute_python(payload.code, **kwargs)
        else:
            result = await self.executor.execute_powershell(payload.code, **kwargs)

        result_dict = result.to_dict()
        success = result.ok
        if success:
            message = f"Script executed successfully. Output: {result.stdout[:500]}" if result.stdout else "Script executed successfully with no output."
        else:
            message = result.error or result.stderr or "Script execution failed"

        return SandboxOutput(
            ok=success,
            success=success,
            message=message,
            details=result_dict,
        )
