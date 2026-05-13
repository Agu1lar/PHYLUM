# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from shell_executor import ShellExecutor
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class DriverInput(BaseModel):
    action: str = Field(
        ...,
        pattern='^(list_devices|device_status|list_drivers|find_driver_candidates|install_inf|add_driver_package|rollback_driver|scan_hardware_changes|printer_status|printer_driver_info|printer_diagnostics|restart_spooler)$',
    )
    query: Optional[str] = None
    device_id: Optional[str] = None
    path: Optional[str] = None
    printer_name: Optional[str] = None


class DriverManagerTool(BaseTool):
    InputModel = DriverInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 120, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.shell = ShellExecutor(default_retries=default_retries)

    async def validate(self, payload: DriverInput) -> None:
        if payload.action in {"device_status", "find_driver_candidates"} and not (payload.query or payload.device_id or payload.printer_name):
            raise ValueError("query, device_id or printer_name is required")
        if payload.action in {"install_inf", "add_driver_package"} and not payload.path:
            raise ValueError("path is required")

    async def _run(self, payload: DriverInput, cancel_event=None) -> ActionResult:
        if payload.action == "list_devices":
            cmd = "Get-PnpDevice | Select-Object Class, FriendlyName, InstanceId, Status | ConvertTo-Json -Depth 3"
            shell = "powershell"
            require_admin = False
        elif payload.action == "device_status":
            query = payload.device_id or payload.query or payload.printer_name or ""
            cmd = (
                f"Get-PnpDevice | Where-Object {{$_.FriendlyName -like '*{query}*' -or $_.InstanceId -like '*{query}*'}} "
                "| Select-Object Class, FriendlyName, InstanceId, Status | ConvertTo-Json -Depth 3"
            )
            shell = "powershell"
            require_admin = False
        elif payload.action == "list_drivers":
            cmd = "Get-WindowsDriver -Online | Select-Object Driver, OriginalFileName, ProviderName, Version | ConvertTo-Json -Depth 3"
            shell = "powershell"
            require_admin = True
        elif payload.action == "find_driver_candidates":
            query = payload.query or payload.device_id or payload.printer_name or ""
            urls = [
                f"https://www.google.com/search?q={query.replace(' ', '+')}+official+driver+download",
                f"https://support.microsoft.com/search/results?query={query.replace(' ', '%20')}",
            ]
            return ActionResult(
                status="succeeded",
                summary=f"Encontrei {len(urls)} fontes candidatas para buscar drivers." if urls else "Nao encontrei fontes candidatas.",
                tool="driver_manager",
                action=payload.action,
                semantic_type="inspection",
                target={"query": query} if query else {},
                data={"query": query, "candidates": urls},
                effects=ActionEffects(changed=False),
            )
        elif payload.action == "install_inf":
            cmd = f'pnputil /add-driver "{payload.path}" /install'
            shell = "cmd"
            require_admin = True
        elif payload.action == "add_driver_package":
            cmd = f'pnputil /add-driver "{payload.path}"'
            shell = "cmd"
            require_admin = True
        elif payload.action == "rollback_driver":
            target = payload.device_id or payload.query or ""
            cmd = (
                f"Get-PnpDevice | Where-Object {{$_.InstanceId -like '*{target}*'}} "
                "| ForEach-Object {{ pnputil /enum-drivers }}"
            )
            shell = "powershell"
            require_admin = True
        elif payload.action == "scan_hardware_changes":
            cmd = "pnputil /scan-devices"
            shell = "cmd"
            require_admin = True
        elif payload.action == "printer_status":
            query = payload.printer_name or payload.query or ""
            if query:
                cmd = (
                    f"Get-Printer | Where-Object {{$_.Name -like '*{query}*'}} "
                    "| Select-Object Name, DriverName, PrinterStatus, PortName | ConvertTo-Json -Depth 3"
                )
            else:
                cmd = "Get-Printer | Select-Object Name, DriverName, PrinterStatus, PortName | ConvertTo-Json -Depth 3"
            shell = "powershell"
            require_admin = False
        elif payload.action == "printer_driver_info":
            query = payload.printer_name or payload.query or ""
            if query:
                cmd = (
                    f"Get-PrinterDriver | Where-Object {{$_.Name -like '*{query}*'}} "
                    "| Select-Object Name, MajorVersion, DriverPath, InfPath | ConvertTo-Json -Depth 3"
                )
            else:
                cmd = "Get-PrinterDriver | Select-Object Name, MajorVersion, DriverPath, InfPath | ConvertTo-Json -Depth 3"
            shell = "powershell"
            require_admin = False
        elif payload.action == "printer_diagnostics":
            query = payload.printer_name or payload.query or ""
            filter_script = f"| Where-Object {{$_.Name -like '*{query}*'}}" if query else ""
            cmd = (
                "$spooler = Get-Service -Name Spooler | Select-Object Name, Status, StartType; "
                f"$printers = Get-Printer {filter_script} | Select-Object Name, DriverName, PrinterStatus, PortName, Shared, ShareName; "
                "$ports = Get-PrinterPort | Select-Object Name, PrinterHostAddress, PortNumber, Protocol; "
                "$drivers = Get-PrinterDriver | Select-Object Name, MajorVersion, DriverPath, InfPath; "
                "[pscustomobject]@{spooler=$spooler; printers=$printers; ports=$ports; drivers=$drivers} | ConvertTo-Json -Depth 5"
            )
            shell = "powershell"
            require_admin = False
        elif payload.action == "restart_spooler":
            cmd = "Restart-Service -Name Spooler"
            shell = "powershell"
            require_admin = True
        else:
            raise ValueError(f"unsupported driver_manager action: {payload.action}")

        resp = await self.shell.execute(
            cmd,
            shell=shell,
            timeout=self.default_timeout,
            retries=self.default_retries,
            require_admin=require_admin,
            cancel_event=cancel_event,
        )
        stdout = resp.result.stdout if resp.result else ""
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except Exception:
                parsed = None
        diagnostics = {
            "command": cmd,
            "stdout": stdout,
            "stderr": resp.result.stderr if resp.result else None,
            "error": resp.error,
            "cancelled": resp.cancelled,
            "raw": resp.raw,
        }
        raw_exception_message = None
        if isinstance(resp.raw, dict):
            raw_exception_message = (
                resp.raw.get("exception_message")
                or resp.raw.get("exception_repr")
                or resp.raw.get("exception_type")
            )
        target = {
            key: value
            for key, value in {
                "query": payload.query,
                "device_id": payload.device_id,
                "path": payload.path,
                "printer_name": payload.printer_name,
            }.items()
            if value is not None
        }
        if resp.ok:
            if payload.action == "printer_status":
                printers = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
                summary = (
                    f"Encontrei {len(printers)} impressora(s) visivel(is) no sistema."
                    if printers
                    else "Nao encontrei impressoras correspondentes no sistema neste momento."
                )
                return ActionResult(
                    status="succeeded",
                    summary=summary,
                    tool="driver_manager",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data={"printers": printers, "raw_stdout": stdout},
                    effects=ActionEffects(changed=False),
                    diagnostics=diagnostics,
                )
            if payload.action == "printer_driver_info":
                drivers = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
                return ActionResult(
                    status="succeeded",
                    summary=f"Consultei {len(drivers)} driver(s) de impressora." if drivers else "Nao encontrei drivers de impressora correspondentes.",
                    tool="driver_manager",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data={"drivers": drivers, "raw_stdout": stdout},
                    effects=ActionEffects(changed=False),
                    diagnostics=diagnostics,
                )
            if payload.action == "printer_diagnostics":
                return ActionResult(
                    status="succeeded",
                    summary="Coletei diagnosticos de impressoras, spooler, portas e drivers.",
                    tool="driver_manager",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data={"diagnostics": parsed, "raw_stdout": stdout},
                    effects=ActionEffects(changed=False),
                    diagnostics=diagnostics,
                )
            if payload.action in {"list_devices", "device_status", "list_drivers"}:
                items = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
                label = "dispositivo(s)" if payload.action != "list_drivers" else "driver(s)"
                return ActionResult(
                    status="succeeded",
                    summary=f"Consultei {len(items)} {label}.",
                    tool="driver_manager",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data={"items": items, "raw_stdout": stdout},
                    effects=ActionEffects(changed=False),
                    diagnostics=diagnostics,
                )
            return ActionResult(
                status="succeeded",
                summary=f"A acao {payload.action} foi executada com sucesso.",
                tool="driver_manager",
                action=payload.action,
                semantic_type="mutation",
                target=target,
                data={"parsed": parsed, "raw_stdout": stdout},
                effects=ActionEffects(changed=True),
                diagnostics=diagnostics,
            )

        issue = ActionIssue(
            kind="command_failed",
            code=resp.error,
            message=resp.error or diagnostics["stderr"] or raw_exception_message or f"A acao {payload.action} falhou.",
            retryable=resp.error == "timeout",
            details=diagnostics,
        )
        return ActionResult(
            status="failed",
            summary=issue.message,
            tool="driver_manager",
            action=payload.action,
            semantic_type="mutation" if payload.action in {"install_inf", "add_driver_package", "rollback_driver", "scan_hardware_changes", "restart_spooler"} else "inspection",
            target=target,
            data={"parsed": parsed} if parsed is not None else {},
            effects=ActionEffects(changed=False),
            issue=issue,
            diagnostics=diagnostics,
        )
