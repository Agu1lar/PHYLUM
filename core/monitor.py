# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from threading import Thread
from typing import Callable, List
import time

logger = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, cb: Callable[[FileSystemEvent], None]):
        super().__init__()
        self.cb = cb

    def on_any_event(self, event: FileSystemEvent):
        try:
            self.cb(event)
        except Exception:
            logger.exception('monitor callback error')


class DirectoryMonitor:
    def __init__(self):
        self.observer = Observer()
        self.threads: List[Thread] = []

    def start(self, paths: List[str], callback: Callable[[FileSystemEvent], None], recursive: bool = True):
        handler = _Handler(callback)
        for p in paths:
            logger.info('Starting monitor for %s', p)
            self.observer.schedule(handler, p, recursive=recursive)
        t = Thread(target=self.observer.start, daemon=True)
        t.start()
        self.threads.append(t)
        # small sleep to let observer start
        time.sleep(0.1)

    def stop(self):
        self.observer.stop()
        self.observer.join()
        for t in self.threads:
            if t.is_alive():
                t.join(timeout=1)
