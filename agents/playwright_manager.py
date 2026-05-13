# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Playwright manager: handles multi-browser launches, isolated contexts and safe teardown."""
import asyncio
import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserType, BrowserContext, Page
from fs_utils import ensure_quarantine, ensure_quarantine as _eq
from fs_config import AGENT_WORKSPACE
from pathlib import Path

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = AGENT_WORKSPACE / 'screenshots'


class PlaywrightManager:
    def __init__(self):
        self._pw = None
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()

    async def start(self):
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                logger.info('Playwright started')

    async def stop(self):
        async with self._lock:
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    logger.exception('error stopping playwright')
                self._pw = None
                logger.info('Playwright stopped')

    def browser_type(self, name: str) -> BrowserType:
        if self._pw is None:
            raise RuntimeError('Playwright not started')
        if name == 'chromium':
            return self._pw.chromium
        if name == 'firefox':
            return self._pw.firefox
        if name == 'webkit':
            return self._pw.webkit
        raise ValueError('unsupported browser')

    async def new_context(self, browser_name: str = 'chromium', headless: bool = True, **kwargs) -> BrowserContext:
        bt = self.browser_type(browser_name)
        browser: Browser = await bt.launch(headless=headless)
        ctx = await browser.new_context(**kwargs)
        return ctx

    async def new_page(self, browser_name: str = 'chromium', headless: bool = True, **kwargs) -> Page:
        ctx = await self.new_context(browser_name=browser_name, headless=headless, **kwargs)
        page = await ctx.new_page()
        # attach basic listeners
        page.on('console', lambda msg: logger.info('console: %s', msg.text))
        page.on('pageerror', lambda err: logger.error('pageerror: %s', err))
        return page

    async def screenshot_path(self, name: str) -> Path:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        return SCREENSHOT_DIR / f"{name}.png"
