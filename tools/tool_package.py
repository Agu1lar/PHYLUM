# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import logging
import json
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from shell_executor import ShellExecutor
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class PackageInput(BaseModel):
    manager: str = Field('choco')
    action: str = Field(..., pattern='^(install|uninstall|list|search|show|upgrade)$')
    package: Optional[str]
    version: Optional[str] = None
    source: Optional[str] = None
    require_admin: bool = Field(True)


class PackageManagerTool(BaseTool):
    InputModel = PackageInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 120, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.shell = ShellExecutor(default_retries=default_retries)

    async def validate(self, payload: PackageInput) -> None:
        if payload.manager not in ('choco', 'pip', 'winget'):
            raise ValueError('unsupported package manager')
        if payload.action in ('install','uninstall', 'search', 'show', 'upgrade') and payload.action != 'list' and not payload.package:
            raise ValueError('package name required')

    async def _run(self, payload: PackageInput, cancel_event=None) -> ActionResult:
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

        stdout = resp.result.stdout if resp.result else ""
        stderr = resp.result.stderr if resp.result else ""
        diagnostics = {
            "command": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "error": resp.error,
            "manager": payload.manager,
        }
        target = {"manager": payload.manager}
        if payload.package:
            target["package"] = payload.package

        parsed_stdout = None
        if payload.manager == "pip" and payload.action == "list" and stdout:
            try:
                parsed_stdout = json.loads(stdout)
            except Exception:
                parsed_stdout = None

        if resp.ok:
            if payload.action in {"list", "search", "show"}:
                summary = f"Consulta ao gerenciador {payload.manager} concluida."
                data: Dict[str, Any] = {"stdout": stdout}
                if parsed_stdout is not None:
                    data["packages"] = parsed_stdout
                    summary = f"Listei {len(parsed_stdout)} pacote(s) com o gerenciador {payload.manager}."
                return ActionResult(
                    status="succeeded",
                    summary=summary,
                    tool="package_manager",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data=data,
                    effects=ActionEffects(changed=False),
                    diagnostics=diagnostics,
                )

            return ActionResult(
                status="succeeded",
                summary=f"A acao {payload.action} em {payload.package or payload.manager} foi concluida com sucesso.",
                tool="package_manager",
                action=payload.action,
                semantic_type="mutation",
                target=target,
                data={"stdout": stdout},
                effects=ActionEffects(changed=True),
                diagnostics=diagnostics,
            )

        issue = ActionIssue(
            kind="command_failed",
            code=resp.error,
            message=resp.error or stderr or f"A acao {payload.action} falhou no gerenciador {payload.manager}.",
            retryable=resp.error == "timeout",
            details=diagnostics,
        )
        return ActionResult(
            status="failed",
            summary=issue.message,
            tool="package_manager",
            action=payload.action,
            semantic_type="mutation" if payload.action in {"install", "uninstall", "upgrade"} else "inspection",
            target=target,
            data={"stdout": stdout} if stdout else {},
            effects=ActionEffects(changed=False),
            issue=issue,
            diagnostics=diagnostics,
        )
