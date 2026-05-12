import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from shell_executor import ShellExecutor
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class DriverInput(BaseModel):
    action: str = Field(
        ...,
        pattern='^(list_devices|device_status|list_drivers|find_driver_candidates|install_inf|add_driver_package|rollback_driver|scan_hardware_changes|printer_status|printer_driver_info|restart_spooler)$',
    )
    query: Optional[str] = None
    device_id: Optional[str] = None
    path: Optional[str] = None
    printer_name: Optional[str] = None


class DriverOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class DriverManagerTool(BaseTool):
    InputModel = DriverInput
    OutputModel = DriverOutput

    def __init__(self, *, default_timeout: int = 120, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.shell = ShellExecutor(default_retries=default_retries)

    async def validate(self, payload: DriverInput) -> None:
        if payload.action in {"device_status", "find_driver_candidates"} and not (payload.query or payload.device_id or payload.printer_name):
            raise ValueError("query, device_id or printer_name is required")
        if payload.action in {"install_inf", "add_driver_package"} and not payload.path:
            raise ValueError("path is required")

    async def _run(self, payload: DriverInput, cancel_event=None) -> DriverOutput:
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
            return DriverOutput(success=True, message="find_driver_candidates", details={"query": query, "candidates": urls})
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
            cmd = (
                f"Get-Printer | Where-Object {{$_.Name -like '*{query}*'}} "
                "| Select-Object Name, DriverName, PrinterStatus, PortName | ConvertTo-Json -Depth 3"
            )
            shell = "powershell"
            require_admin = False
        elif payload.action == "printer_driver_info":
            query = payload.printer_name or payload.query or ""
            cmd = (
                f"Get-PrinterDriver | Where-Object {{$_.Name -like '*{query}*'}} "
                "| Select-Object Name, MajorVersion, DriverPath, InfPath | ConvertTo-Json -Depth 3"
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
        return DriverOutput(
            success=resp.ok,
            message=payload.action,
            details={
                "stdout": stdout,
                "stderr": resp.result.stderr if resp.result else None,
                "parsed": parsed,
            },
        )
