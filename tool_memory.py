import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from tool_base import BaseTool
from agent_persistence import Persistence

logger = logging.getLogger(__name__)


class MemoryInput(BaseModel):
    action: str = Field(..., pattern='^(set|get|delete|list)$')
    key: Optional[str]
    value: Optional[Dict[str, Any]]


class MemoryOutput(BaseModel):
    success: bool
    value: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class MemoryTool(BaseTool):
    InputModel = MemoryInput
    OutputModel = MemoryOutput

    def __init__(self, *, default_timeout: int = 10, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.persistence = Persistence.get()

    async def validate(self, payload: MemoryInput) -> None:
        if payload.action in ('set','get','delete') and not payload.key:
            raise ValueError('key is required')

    async def _run(self, payload: MemoryInput) -> MemoryOutput:
        if payload.action == 'set':
            await self.persistence.save_kv(f"mem:{payload.key}", payload.value)
            return MemoryOutput(success=True, value=payload.value, message='saved')
        if payload.action == 'get':
            v = await self.persistence.get_kv(f"mem:{payload.key}")
            return MemoryOutput(success=True, value=v, message='fetched')
        if payload.action == 'delete':
            await self.persistence.delete_kv(f"mem:{payload.key}")
            return MemoryOutput(success=True, message='deleted')
        if payload.action == 'list':
            # no direct list support in persistence; return not implemented
            return MemoryOutput(success=False, message='list not implemented')
        return MemoryOutput(success=False, message='unknown')
