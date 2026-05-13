# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool wrapper for dynamic micro-tool creation and execution."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from dynamic_tool_creator import DynamicToolCreator
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class DynamicToolInput(BaseModel):
    action: str = Field(..., description="Action: create, list, execute, delete, get")
    name: Optional[str] = Field(None, description="Tool name (for create)")
    description: Optional[str] = Field(None, description="Tool description (for create)")
    code: Optional[str] = Field(None, description="Tool source code (for create)")
    language: Optional[str] = Field("python", description="Language: python or powershell")
    tool_id: Optional[str] = Field(None, description="Tool ID (for execute, delete, get)")
    params: Optional[Dict[str, Any]] = Field(None, description="Execution params (for execute)")
    tags: Optional[List[str]] = Field(None, description="Tags (for create, list)")
    tag: Optional[str] = Field(None, description="Filter tag (for list)")
    timeout: Optional[int] = Field(None, gt=0, le=300, description="Execution timeout (for execute)")


class DynamicToolOutput(BaseModel):
    ok: bool
    success: bool
    message: str
    details: Dict[str, Any]


class DynamicToolTool(BaseTool):
    InputModel = DynamicToolInput
    OutputModel = DynamicToolOutput

    def __init__(self, *, default_timeout: int = 60, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.creator = DynamicToolCreator()

    async def validate(self, payload: DynamicToolInput) -> None:
        if payload.action not in ("create", "list", "execute", "delete", "get"):
            raise ValueError(f"Unsupported dynamic_tool action: {payload.action}")
        if payload.action == "create" and (not payload.name or not payload.code):
            raise ValueError("name and code are required for create action")
        if payload.action in ("execute", "delete", "get") and not payload.tool_id:
            raise ValueError("tool_id is required for execute, delete and get actions")

    async def _run(self, payload: DynamicToolInput, cancel_event=None) -> DynamicToolOutput:
        if payload.action == "create":
            try:
                spec = await self.creator.create_tool(
                    name=payload.name,
                    description=payload.description or "",
                    code=payload.code,
                    language=payload.language or "python",
                    tags=payload.tags,
                )
                return DynamicToolOutput(
                    ok=True,
                    success=True,
                    message=f"Created dynamic tool '{spec.name}' ({spec.tool_id})",
                    details=spec.to_dict(),
                )
            except Exception as exc:
                return DynamicToolOutput(ok=False, success=False, message=str(exc), details={})

        if payload.action == "list":
            tools = await self.creator.list_tools(tag=payload.tag)
            return DynamicToolOutput(
                ok=True,
                success=True,
                message=f"Found {len(tools)} dynamic tool(s)",
                details={"tools": [t.to_dict() for t in tools]},
            )

        if payload.action == "get":
            spec = await self.creator.get_tool(payload.tool_id)
            if spec is None:
                return DynamicToolOutput(ok=False, success=False, message=f"Tool {payload.tool_id} not found", details={})
            return DynamicToolOutput(ok=True, success=True, message=f"Tool: {spec.name}", details=spec.to_dict())

        if payload.action == "execute":
            result = await self.creator.execute_tool(
                payload.tool_id,
                params=payload.params,
                timeout=payload.timeout or 60,
            )
            result_dict = result.to_dict()
            return DynamicToolOutput(
                ok=result.ok,
                success=result.ok,
                message=f"Executed '{result.tool_name}'" if result.ok else (result.error or "Execution failed"),
                details=result_dict,
            )

        if payload.action == "delete":
            deleted = await self.creator.delete_tool(payload.tool_id)
            return DynamicToolOutput(
                ok=deleted,
                success=deleted,
                message=f"Deleted tool {payload.tool_id}" if deleted else f"Tool {payload.tool_id} not found",
                details={"tool_id": payload.tool_id, "deleted": deleted},
            )

        return DynamicToolOutput(ok=False, success=False, message=f"Unknown action: {payload.action}", details={})
