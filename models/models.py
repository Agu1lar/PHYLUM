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
