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
        if payload.action == "focus_window" and payload.hwnd is None and not payload.title:
            raise ValueError("focus_window requires hwnd or title")
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
        elif payload.action == "focus_window":
            details = await self.agent.focus_window(hwnd=payload.hwnd, title=payload.title)
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
        return DesktopResponse(ok=True, message=payload.action, details=details)
