import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from tool_base import BaseTool
import asyncio

logger = logging.getLogger(__name__)

class BrowserInput(BaseModel):
    url: str = Field(..., min_length=3)
    headless: bool = Field(True)
    wait_for: Optional[float] = Field(5.0)
    actions: Optional[list] = Field(None)


class BrowserOutput(BaseModel):
    success: bool
    title: Optional[str] = None
    content_snippet: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class BrowserTool(BaseTool):
    InputModel = BrowserInput
    OutputModel = BrowserOutput

    async def _run(self, payload: BrowserInput) -> BrowserOutput:
        # Use Playwright in an isolated context
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            logger.exception('Playwright not available')
            raise

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=payload.headless)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(payload.url, timeout=int(self.default_timeout*1000))
                await asyncio.sleep(payload.wait_for or 0)
                title = await page.title()
                content = await page.content()
                snippet = content[:200]
                # optional actions placeholder
                return BrowserOutput(success=True, title=title, content_snippet=snippet)
            finally:
                await context.close()
                await browser.close()
