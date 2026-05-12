import logging
from typing import Optional

from desktop_windows_agent import DesktopWindowsAgent
from desktop_windows_models import DesktopRequest, DesktopResponse
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class DesktopTool(BaseTool):
    InputModel = DesktopRequest
    OutputModel = DesktopResponse

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = DesktopWindowsAgent()

    async def validate(self, payload: DesktopRequest) -> None:
        if payload.action in {"open_path", "open_file"} and not payload.path:
            raise ValueError(f"{payload.action} requires path")
        if payload.action == "open_app" and not payload.app_name and not payload.app_path:
            raise ValueError("open_app requires app_name or app_path")
        if payload.action == "get_explorer_selection":
            return
        if payload.action == "wait_for_window" and payload.hwnd is None and not payload.title and not payload.process_name:
            raise ValueError("wait_for_window requires hwnd, title or process_name")
        if payload.action == "focus_window" and payload.hwnd is None and not payload.title:
            raise ValueError("focus_window requires hwnd or title")
        if payload.action == "close_window" and payload.hwnd is None and not payload.title:
            raise ValueError("close_window requires hwnd or title")
        if payload.action == "kill_process" and payload.pid is None and not payload.process_name and not payload.title:
            raise ValueError("kill_process requires pid, process_name or title")
        if payload.action == "clipboard_set" and payload.text is None:
            raise ValueError("clipboard_set requires text")
        if payload.action == "notify" and payload.message is None:
            raise ValueError("notify requires message")
        if payload.action == "service_action":
            if not payload.service_name or not payload.service_action:
                raise ValueError("service_action requires service_name and service_action")

    async def _run(self, payload: DesktopRequest) -> DesktopResponse:
        if payload.action == "list_processes":
            details = await self.agent.list_processes()
        elif payload.action == "list_windows":
            details = await self.agent.list_windows()
        elif payload.action == "list_explorer_windows":
            details = await self.agent.list_explorer_windows()
        elif payload.action == "list_mapped_drives":
            details = await self.agent.list_mapped_drives()
        elif payload.action == "get_explorer_selection":
            details = await self.agent.get_explorer_selection()
        elif payload.action == "open_app":
            details = await self.agent.open_app(
                app_name=payload.app_name,
                app_path=payload.app_path,
                arguments=payload.arguments,
            )
        elif payload.action == "open_path":
            details = await self.agent.open_path(payload.path or "")
        elif payload.action == "open_file":
            details = await self.agent.open_file(payload.path or "")
        elif payload.action == "wait_for_window":
            details = await self.agent.wait_for_window(
                hwnd=payload.hwnd,
                title=payload.title,
                process_name=payload.process_name,
                timeout_seconds=payload.timeout_seconds or 15,
            )
        elif payload.action == "focus_window":
            details = await self.agent.focus_window(hwnd=payload.hwnd, title=payload.title)
        elif payload.action == "close_window":
            details = await self.agent.close_window(hwnd=payload.hwnd, title=payload.title)
        elif payload.action == "kill_process":
            details = await self.agent.kill_process(pid=payload.pid, process_name=payload.process_name, title=payload.title)
        elif payload.action == "clipboard_get":
            details = await self.agent.clipboard_get()
        elif payload.action == "clipboard_set":
            details = await self.agent.clipboard_set(payload.text or "")
        elif payload.action == "notify":
            details = await self.agent.notify(payload.message or "", title=payload.title or "Agente Desktop")
        elif payload.action == "list_services":
            details = await self.agent.list_services()
        elif payload.action == "service_action":
            details = await self.agent.service_action(payload.service_name or "", payload.service_action or "")
        else:
            raise ValueError(f"unsupported desktop action: {payload.action}")
        if payload.action == "open_app":
            message = f"Opened app {payload.app_name or payload.app_path}"
        elif payload.action in {"open_path", "open_file"}:
            message = f"Opened {payload.path}"
        elif payload.action == "wait_for_window":
            message = "Window detected"
        elif payload.action == "list_mapped_drives":
            message = "Mapped drives listed"
        elif payload.action == "get_explorer_selection":
            message = "Explorer selection captured"
        elif payload.action == "list_explorer_windows":
            message = "Explorer windows listed"
        elif payload.action == "close_window":
            message = "Window close requested"
        elif payload.action == "kill_process":
            message = "Process termination requested"
        else:
            message = payload.action
        return DesktopResponse(ok=True, message=message, details=details)
