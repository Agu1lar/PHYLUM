import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from tool_base import BaseTool
from models import StructuredResponse
from shell_executor import ShellExecutor

logger = logging.getLogger(__name__)


class ShellInput(BaseModel):
    command: str = Field(..., min_length=1)
    shell: str = Field('powershell')
    timeout: Optional[int] = Field(None, gt=0)
    retries: Optional[int] = Field(None, ge=1)
    require_admin: bool = Field(False)
    allow_protected_paths: bool = Field(False)


class ShellOutput(BaseModel):
    structured: StructuredResponse


class ShellTool(BaseTool):
    InputModel = ShellInput
    OutputModel = ShellOutput

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.executor = ShellExecutor(default_retries=default_retries)

    async def validate(self, payload: ShellInput) -> None:
        if payload.shell not in ('powershell', 'cmd'):
            raise ValueError('unsupported shell')

    async def _run(self, payload: ShellInput, cancel_event=None) -> ShellOutput:
        timeout = payload.timeout or self.default_timeout
        retries = payload.retries if payload.retries is not None else self.default_retries
        # delegate to executor
        resp = await self.executor.execute(
            payload.command,
            shell=payload.shell,
            timeout=timeout,
            retries=retries,
            require_admin=payload.require_admin,
            cancel_event=cancel_event,
            allow_protected_paths=payload.allow_protected_paths,
        )
        logger.info('ShellTool executed command; ok=%s rc=%s', resp.ok, resp.result.returncode if resp.result else None)
        return ShellOutput(structured=resp)
