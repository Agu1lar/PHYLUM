# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Helpers to monitor Playwright downloads and track progress."""
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any
from browser_models import DownloadInfo
from fs_config import AGENT_WORKSPACE

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = AGENT_WORKSPACE / 'downloads'
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DownloadWatcher:
    def __init__(self, page):
        self.page = page
        self.downloads = []
        self._lock = asyncio.Lock()

    async def _handle(self, download):
        suggested = download.suggested_filename
        path = DOWNLOAD_DIR / suggested
        logger.info('Starting download %s -> %s', suggested, path)
        await download.save_as(str(path))
        info = DownloadInfo(url=download.url, suggested_filename=suggested, path=str(path))
        async with self._lock:
            self.downloads.append(info)
        logger.info('Download finished: %s', suggested)

    def attach(self):
        self.page.on('download', lambda d: asyncio.create_task(self._handle(d)))

    async def get_downloads(self):
        async with self._lock:
            return list(self.downloads)
