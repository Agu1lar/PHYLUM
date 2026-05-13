# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime


class CommandResult(BaseModel):
    stdout: str
    stderr: str
    returncode: int
    duration_seconds: float
    pid: Optional[int]


class ExecutionRisk(BaseModel):
    level: str  # low, medium, high
    tags: List[str]
    reason: str


class ExecutionMeta(BaseModel):
    attempted_at: datetime
    attempt: int
    retries: int
    timeout_seconds: int
    shell: str
    command: str
    allowed: bool
    admin_requested: bool
    admin_granted: bool


class StructuredResponse(BaseModel):
    ok: bool
    meta: ExecutionMeta
    result: Optional[CommandResult]
    risk: ExecutionRisk
    error: Optional[str]
    cancelled: bool = False
    raw: Optional[Dict[str, Any]] = None
