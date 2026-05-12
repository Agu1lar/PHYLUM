import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from tool_base import BaseTool
from tool_schemas import ToolResult
from shell_executor import ShellExecutor

logger = logging.getLogger(__name__)


class PackageInput(BaseModel):
    manager: str = Field('choco')
    action: str = Field(..., pattern='^(install|uninstall|list|search|show|upgrade)$')
    package: Optional[str]
    version: Optional[str] = None
    source: Optional[str] = None
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
        if payload.manager not in ('choco', 'pip', 'winget'):
            raise ValueError('unsupported package manager')
        if payload.action in ('install','uninstall', 'search', 'show', 'upgrade') and payload.action != 'list' and not payload.package:
            raise ValueError('package name required')

    async def _run(self, payload: PackageInput, cancel_event=None) -> PackageOutput:
        # build safe command
        if payload.manager == 'winget':
            source_args = f" --source {payload.source}" if payload.source else ""
            version_args = f" --version {payload.version}" if payload.version else ""
            if payload.action == 'install':
                cmd = f"winget install --id {payload.package}{version_args}{source_args} --accept-package-agreements --accept-source-agreements"
            elif payload.action == 'uninstall':
                cmd = f"winget uninstall --id {payload.package}{source_args}"
            elif payload.action == 'search':
                cmd = f"winget search {payload.package}{source_args}"
            elif payload.action == 'show':
                cmd = f"winget show --id {payload.package}{source_args}"
            elif payload.action == 'upgrade':
                cmd = f"winget upgrade --id {payload.package}{version_args}{source_args} --accept-package-agreements --accept-source-agreements"
            else:
                cmd = "winget list"
            resp = await self.shell.execute(
                cmd,
                shell='powershell',
                timeout=self.default_timeout,
                retries=self.default_retries,
                require_admin=payload.require_admin,
                cancel_event=cancel_event,
            )
        elif payload.manager == 'choco':
            if payload.action == 'install':
                cmd = f"choco install -y {payload.package}"
            elif payload.action == 'uninstall':
                cmd = f"choco uninstall -y {payload.package}"
            elif payload.action == 'search':
                cmd = f"choco search {payload.package}"
            elif payload.action == 'show':
                cmd = f"choco info {payload.package}"
            elif payload.action == 'upgrade':
                cmd = f"choco upgrade -y {payload.package}"
            else:
                cmd = "choco list --local-only"
            resp = await self.shell.execute(
                cmd,
                shell='powershell',
                timeout=self.default_timeout,
                retries=self.default_retries,
                require_admin=payload.require_admin,
                cancel_event=cancel_event,
            )
        else:
            # pip operations run using python -m pip
            if payload.action == 'install':
                version = f"=={payload.version}" if payload.version else ""
                cmd = f"python -m pip install {payload.package}{version}"
            elif payload.action == 'uninstall':
                cmd = f"python -m pip uninstall -y {payload.package}"
            elif payload.action == 'search':
                cmd = f"python -m pip index versions {payload.package}"
            elif payload.action == 'show':
                cmd = f"python -m pip show {payload.package}"
            elif payload.action == 'upgrade':
                cmd = f"python -m pip install --upgrade {payload.package}"
            else:
                cmd = f"python -m pip list --format=json"
            resp = await self.shell.execute(
                cmd,
                shell='cmd',
                timeout=self.default_timeout,
                retries=self.default_retries,
                require_admin=False,
                cancel_event=cancel_event,
            )

        success = resp.ok
        details = {'stdout': resp.result.stdout if resp.result else None, 'stderr': resp.result.stderr if resp.result else None}
        return PackageOutput(success=success, message='done' if success else 'failed', details=details)
