import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from tool_base import BaseTool
from tool_schemas import ToolResult
from shell_executor import ShellExecutor

logger = logging.getLogger(__name__)


class PackageInput(BaseModel):
    manager: str = Field('choco')
    action: str = Field(..., pattern='^(install|uninstall|list)$')
    package: Optional[str]
    require_admin: bool = Field(True)


class PackageOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class PackageManagerTool(BaseTool):
    InputModel = PackageInput
    OutputModel = PackageOutput

    def __init__(self, *, default_timeout: int = 120, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.shell = ShellExecutor(default_retries=default_retries)

    async def validate(self, payload: PackageInput) -> None:
        if payload.manager not in ('choco', 'pip'):
            raise ValueError('unsupported package manager')
        if payload.action in ('install','uninstall') and not payload.package:
            raise ValueError('package name required')

    async def _run(self, payload: PackageInput) -> PackageOutput:
        # build safe command
        if payload.manager == 'choco':
            if payload.action == 'install':
                cmd = f"choco install -y {payload.package}"
            elif payload.action == 'uninstall':
                cmd = f"choco uninstall -y {payload.package}"
            else:
                cmd = "choco list --local-only"
            resp = await self.shell.execute(cmd, shell='powershell', timeout=self.default_timeout, retries=self.default_retries, require_admin=payload.require_admin)
        else:
            # pip operations run using python -m pip
            if payload.action == 'install':
                cmd = f"python -m pip install {payload.package}"
            elif payload.action == 'uninstall':
                cmd = f"python -m pip uninstall -y {payload.package}"
            else:
                cmd = f"python -m pip list --format=json"
            resp = await self.shell.execute(cmd, shell='cmd', timeout=self.default_timeout, retries=self.default_retries, require_admin=False)

        success = resp.ok
        details = {'stdout': resp.result.stdout if resp.result else None, 'stderr': resp.result.stderr if resp.result else None}
        return PackageOutput(success=success, message='done' if success else 'failed', details=details)
