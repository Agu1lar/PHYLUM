import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from browser_agent import BrowserAgent
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class BrowserInput(BaseModel):
    action: str = Field(..., pattern='^(open_page|search|download|scrape_structured|interact_dom|upload_file)$')
    url: Optional[str] = None
    base_url: Optional[str] = None
    query_selector: Optional[str] = None
    query: Optional[str] = None
    result_selector: Optional[str] = None
    extractors: Optional[Dict[str, str]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    selector: Optional[str] = None
    file_path: Optional[str] = None
    headless: bool = True
    browser: str = Field('chromium')
    timeout: Optional[int] = Field(None, gt=0)


class BrowserOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class BrowserTool(BaseTool):
    InputModel = BrowserInput
    OutputModel = BrowserOutput

    def __init__(self, *, default_timeout: int = 60, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = BrowserAgent()

    async def validate(self, payload: BrowserInput) -> None:
        if payload.action in {"open_page", "download", "scrape_structured"} and not payload.url:
            raise ValueError("url is required")
        if payload.action == "search":
            if not payload.base_url or not payload.query or not payload.query_selector:
                raise ValueError("search requires base_url, query and query_selector")
        if payload.action == "scrape_structured" and not payload.extractors:
            raise ValueError("extractors are required for scrape_structured")
        if payload.action == "interact_dom" and (not payload.url or not payload.actions):
            raise ValueError("interact_dom requires url and actions")
        if payload.action == "upload_file" and (not payload.url or not payload.selector or not payload.file_path):
            raise ValueError("upload_file requires url, selector and file_path")

    async def _run(self, payload: BrowserInput) -> BrowserOutput:
        timeout = payload.timeout or self.default_timeout
        if payload.action == "open_page":
            response = await self.agent.open_page(
                payload.url,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
                retries=self.default_retries,
            )
        elif payload.action == "search":
            response = await self.agent.search(
                payload.base_url,
                payload.query_selector,
                payload.query,
                result_selector=payload.result_selector,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "download":
            response = await self.agent.download(
                payload.url,
                link_selector=payload.selector,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "scrape_structured":
            response = await self.agent.scrape_structured(
                payload.url,
                payload.extractors or {},
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "interact_dom":
            response = await self.agent.interact_dom(
                payload.url,
                payload.actions or [],
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "upload_file":
            response = await self.agent.upload_file(
                payload.url,
                payload.selector,
                payload.file_path,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        else:
            raise ValueError(f"unsupported browser action: {payload.action}")

        details = response.dict()
        success = bool(details.get("ok"))
        message = details.pop("error", None) or payload.action
        return BrowserOutput(success=success, message=message, details=details)
