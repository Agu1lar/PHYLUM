# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool wrapper for internal artifact processing."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from artifact_processor import ArtifactProcessor
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class ArtifactInput(BaseModel):
    action: str = Field(..., description="Action: load, transform, write_result")
    path: Optional[str] = Field(None, description="Path to the artifact file")
    operation: Optional[str] = Field(None, description="Transform operation: summarize, extract_table, filter_lines, convert_json, stats")
    params: Optional[Dict[str, Any]] = Field(None, description="Operation parameters")
    content: Optional[str] = Field(None, description="Content to write (for write_result)")
    output_path: Optional[str] = Field(None, description="Output path (for write_result)")


class ArtifactOutput(BaseModel):
    ok: bool
    success: bool
    message: str
    details: Dict[str, Any]


class ArtifactTool(BaseTool):
    InputModel = ArtifactInput
    OutputModel = ArtifactOutput

    def __init__(self, *, default_timeout: int = 60, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.processor = ArtifactProcessor()

    async def validate(self, payload: ArtifactInput) -> None:
        if payload.action not in ("load", "transform", "write_result"):
            raise ValueError(f"Unsupported artifact action: {payload.action}")
        if payload.action in ("load", "transform") and not payload.path:
            raise ValueError("path is required for load and transform actions")
        if payload.action == "transform" and not payload.operation:
            raise ValueError("operation is required for transform action")
        if payload.action == "write_result" and (not payload.content or not payload.output_path):
            raise ValueError("content and output_path are required for write_result action")

    async def _run(self, payload: ArtifactInput, cancel_event=None) -> ArtifactOutput:
        if payload.action == "load":
            result = await self.processor.load_and_read(payload.path)
        elif payload.action == "transform":
            result = await self.processor.transform(payload.path, payload.operation, payload.params)
        elif payload.action == "write_result":
            result = await self.processor.write_result(payload.content, payload.output_path)
        else:
            return ArtifactOutput(ok=False, success=False, message=f"Unknown action: {payload.action}", details={})

        result_dict = result.to_dict()
        return ArtifactOutput(
            ok=result.ok,
            success=result.ok,
            message=result.summary if result.ok else (result.error or "Processing failed"),
            details=result_dict,
        )
