# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""High level scraping primitives built on top of Playwright contexts/pages.
Provides retry/timeout/screenshot on error and structured extraction helpers.
"""
import asyncio
import logging
from typing import Optional, Dict, Any, List
from playwright.async_api import Page
from browser_models import BrowserResponse
from download_monitor import DownloadWatcher
from playwright_manager import PlaywrightManager
from fs_config import AGENT_WORKSPACE
from pathlib import Path

logger = logging.getLogger(__name__)


async def with_page(browser: PlaywrightManager, browser_name: str = 'chromium', headless: bool = True):
    await browser.start()
    page = await browser.new_page(browser_name=browser_name, headless=headless)
    return page


async def open_and_extract(url: str, selector: Optional[str] = None, browser_name: str = 'chromium', headless: bool = True, timeout: int = 30) -> BrowserResponse:
    pm = PlaywrightManager()
    await pm.start()
    page = await pm.new_page(browser_name=browser_name, headless=headless)
    dw = DownloadWatcher(page)
    dw.attach()
    console_msgs: List[str] = []
    page.on('console', lambda msg: console_msgs.append(msg.text))
    try:
        await page.goto(url, timeout=timeout * 1000)
        await asyncio.sleep(0.2)
        title = await page.title()
        content = await page.content()
        snippet = content[:1000]
        extracted = None
        if selector:
            try:
                el = await page.query_selector(selector)
                if el:
                    extracted = await el.inner_text()
            except Exception:
                logger.exception('selector extraction failed')
        downloads = await dw.get_downloads()
        return BrowserResponse(ok=True, url=url, title=title, content_snippet=snippet, console=console_msgs, downloads=downloads)
    except Exception as exc:
        logger.exception('open_and_extract failed')
        # save screenshot
        path = AGENT_WORKSPACE / 'screenshots'
        path.mkdir(parents=True, exist_ok=True)
        ss = path / f"error_{int(asyncio.get_event_loop().time()*1000)}.png"
        try:
            await page.screenshot(path=str(ss), full_page=True)
            ss_path = str(ss)
        except Exception:
            logger.exception('screenshot failed')
            ss_path = None
        return BrowserResponse(ok=False, url=url, title=None, content_snippet=None, console=console_msgs, downloads=[], screenshot_path=ss_path, error=str(exc))
    finally:
        try:
            await page.context.close()
            await page.context.browser.close()
            await pm.stop()
        except Exception:
            pass
