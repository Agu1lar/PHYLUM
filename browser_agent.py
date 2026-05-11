"""High-level Browser Agent exposing robust operations using Playwright.
Supports open, search, login, download, scraping, DOM interaction, uploads, structured extraction.
"""
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from playwright_manager import PlaywrightManager
from download_monitor import DownloadWatcher
from auth_helpers import do_login
from browser_models import BrowserRequest, BrowserResponse, LoginCredentials, DownloadInfo
from fs_config import AGENT_WORKSPACE
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SCREENSHOT_DIR = AGENT_WORKSPACE / 'screenshots'
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class BrowserAgent:
    def __init__(self):
        self.pm = PlaywrightManager()

    async def open_page(self, url: str, browser: str = 'chromium', headless: bool = True, timeout: int = 30, retries: int = 2) -> BrowserResponse:
        last = None
        for attempt in range(1, retries+1):
            try:
                await self.pm.start()
                page = await self.pm.new_page(browser_name=browser, headless=headless)
                dw = DownloadWatcher(page)
                dw.attach()
                console_msgs: List[str] = []
                page.on('console', lambda msg: console_msgs.append(msg.text))
                await page.goto(url, timeout=timeout*1000)
                title = await page.title()
                content = await page.content()
                snippet = content[:2000]
                downloads = await dw.get_downloads()
                # cleanup
                await page.context.close()
                await page.context.browser.close()
                return BrowserResponse(ok=True, url=url, title=title, content_snippet=snippet, console=console_msgs, downloads=downloads)
            except Exception as exc:
                logger.exception('open_page attempt %s failed', attempt)
                last = exc
                await asyncio.sleep(min(2**attempt, 10))
        return BrowserResponse(ok=False, url=url, title=None, content_snippet=None, console=[], downloads=[], error=str(last))

    async def search(self, base_url: str, query_selector: str, query: str, result_selector: Optional[str] = None, **kwargs) -> BrowserResponse:
        # simple search flow: open base_url, fill query_selector, press Enter, wait and extract
        await self.pm.start()
        page = await self.pm.new_page(browser_name=kwargs.get('browser','chromium'), headless=kwargs.get('headless', True))
        dw = DownloadWatcher(page)
        dw.attach()
        console_msgs = []
        page.on('console', lambda msg: console_msgs.append(msg.text))
        try:
            await page.goto(base_url, timeout=kwargs.get('timeout',30)*1000)
            await page.fill(query_selector, query)
            await page.keyboard.press('Enter')
            await asyncio.sleep(kwargs.get('wait',2))
            snippet = None
            if result_selector:
                el = await page.query_selector(result_selector)
                if el:
                    snippet = await el.inner_text()
            downloads = await dw.get_downloads()
            await page.context.close()
            await page.context.browser.close()
            return BrowserResponse(ok=True, url=page.url, title=await page.title(), content_snippet=snippet, console=console_msgs, downloads=downloads)
        except Exception as exc:
            logger.exception('search failed')
            ss = SCREENSHOT_DIR / f"search_error_{int(time.time()*1000)}.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
                ss_path = str(ss)
            except Exception:
                ss_path = None
            return BrowserResponse(ok=False, url=None, title=None, content_snippet=None, console=console_msgs, downloads=[], screenshot_path=ss_path, error=str(exc))
        finally:
            try:
                await page.context.close()
                await page.context.browser.close()
            except Exception:
                pass

    async def login(self, url: str, creds: LoginCredentials, steps: List[Dict[str,Any]], browser: str = 'chromium', headless: bool = True, timeout: int = 30) -> BrowserResponse:
        await self.pm.start()
        page = await self.pm.new_page(browser_name=browser, headless=headless)
        console_msgs = []
        page.on('console', lambda msg: console_msgs.append(msg.text))
        try:
            await page.goto(url, timeout=timeout*1000)
            await do_login(page, creds.dict(), steps, timeout=timeout)
            await asyncio.sleep(1)
            downloads = []
            title = await page.title()
            content = await page.content()
            snippet = content[:1000]
            await page.context.close()
            await page.context.browser.close()
            return BrowserResponse(ok=True, url=page.url, title=title, content_snippet=snippet, console=console_msgs, downloads=downloads)
        except Exception as exc:
            logger.exception('login failed')
            ss = SCREENSHOT_DIR / f"login_error_{int(time.time()*1000)}.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
                ss_path = str(ss)
            except Exception:
                ss_path = None
            return BrowserResponse(ok=False, url=None, title=None, content_snippet=None, console=console_msgs, downloads=[], screenshot_path=ss_path, error=str(exc))
        finally:
            try:
                await page.context.close()
                await page.context.browser.close()
            except Exception:
                pass

    async def download(self, url: str, link_selector: Optional[str] = None, browser: str = 'chromium', headless: bool = True, timeout: int = 60) -> BrowserResponse:
        await self.pm.start()
        page = await self.pm.new_page(browser_name=browser, headless=headless)
        dw = DownloadWatcher(page)
        dw.attach()
        console_msgs = []
        page.on('console', lambda msg: console_msgs.append(msg.text))
        try:
            await page.goto(url, timeout=timeout*1000)
            if link_selector:
                await page.click(link_selector)
            # wait for downloads to complete
            await asyncio.sleep(2)
            downloads = await dw.get_downloads()
            await page.context.close()
            await page.context.browser.close()
            return BrowserResponse(ok=True, url=url, title=await page.title(), content_snippet=None, console=console_msgs, downloads=downloads)
        except Exception as exc:
            logger.exception('download failed')
            ss = SCREENSHOT_DIR / f"download_error_{int(time.time()*1000)}.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
                ss_path = str(ss)
            except Exception:
                ss_path = None
            return BrowserResponse(ok=False, url=url, title=None, content_snippet=None, console=console_msgs, downloads=[], screenshot_path=ss_path, error=str(exc))
        finally:
            try:
                await page.context.close()
                await page.context.browser.close()
            except Exception:
                pass

    async def scrape_structured(self, url: str, extractors: Dict[str,str], browser: str = 'chromium', headless: bool = True, timeout: int = 30) -> BrowserResponse:
        # extractors: field -> css selector or xpath (Playwright supports both)
        await self.pm.start()
        page = await self.pm.new_page(browser_name=browser, headless=headless)
        try:
            await page.goto(url, timeout=timeout*1000)
            data = {}
            for k, sel in extractors.items():
                try:
                    el = await page.query_selector(sel)
                    if el:
                        data[k] = await el.inner_text()
                    else:
                        data[k] = None
                except Exception:
                    logger.exception('extraction failed for %s', k)
                    data[k] = None
            title = await page.title()
            snippet = str(data)[:2000]
            await page.context.close()
            await page.context.browser.close()
            return BrowserResponse(ok=True, url=url, title=title, content_snippet=snippet, console=[], downloads=[], raw={'extracted': data})
        except Exception as exc:
            logger.exception('scrape_structured failed')
            ss = SCREENSHOT_DIR / f"scrape_error_{int(time.time()*1000)}.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
                ss_path = str(ss)
            except Exception:
                ss_path = None
            return BrowserResponse(ok=False, url=url, title=None, content_snippet=None, console=[], downloads=[], screenshot_path=ss_path, error=str(exc))
        finally:
            try:
                await page.context.close()
                await page.context.browser.close()
            except Exception:
                pass

    async def interact_dom(self, url: str, actions: List[Dict[str,Any]], browser: str = 'chromium', headless: bool = True, timeout: int = 30) -> BrowserResponse:
        await self.pm.start()
        page = await self.pm.new_page(browser_name=browser, headless=headless)
        try:
            await page.goto(url, timeout=timeout*1000)
            for a in actions:
                act = a.get('action')
                sel = a.get('selector')
                val = a.get('value')
                if act == 'click':
                    await page.click(sel)
                elif act == 'fill':
                    await page.fill(sel, val)
                elif act == 'press':
                    await page.press(sel, val)
                elif act == 'wait':
                    await page.wait_for_selector(sel, timeout=timeout*1000)
                await asyncio.sleep(a.get('delay',0.1))
            title = await page.title()
            content = await page.content()
            snippet = content[:1000]
            await page.context.close()
            await page.context.browser.close()
            return BrowserResponse(ok=True, url=page.url, title=title, content_snippet=snippet, console=[])
        except Exception as exc:
            logger.exception('interact_dom failed')
            ss = SCREENSHOT_DIR / f"interact_error_{int(time.time()*1000)}.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
                ss_path = str(ss)
            except Exception:
                ss_path = None
            return BrowserResponse(ok=False, url=None, title=None, content_snippet=None, console=[], screenshot_path=ss_path, error=str(exc))
        finally:
            try:
                await page.context.close()
                await page.context.browser.close()
            except Exception:
                pass

    async def upload_file(self, url: str, selector: str, file_path: str, browser: str = 'chromium', headless: bool = True, timeout: int = 60) -> BrowserResponse:
        await self.pm.start()
        page = await self.pm.new_page(browser_name=browser, headless=headless)
        try:
            await page.goto(url, timeout=timeout*1000)
            input_handle = await page.query_selector(selector)
            if not input_handle:
                raise RuntimeError('upload input not found')
            await input_handle.set_input_files(file_path)
            await asyncio.sleep(1)
            title = await page.title()
            await page.context.close()
            await page.context.browser.close()
            return BrowserResponse(ok=True, url=page.url, title=title, content_snippet=None)
        except Exception as exc:
            logger.exception('upload failed')
            ss = SCREENSHOT_DIR / f"upload_error_{int(time.time()*1000)}.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
                ss_path = str(ss)
            except Exception:
                ss_path = None
            return BrowserResponse(ok=False, url=None, title=None, content_snippet=None, screenshot_path=ss_path, error=str(exc))
        finally:
            try:
                await page.context.close()
                await page.context.browser.close()
            except Exception:
                pass
