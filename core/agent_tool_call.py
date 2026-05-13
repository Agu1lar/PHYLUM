# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool calling utilities: safe shell, playwright helpers.
"""
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def run_shell(cmd: str, timeout: int = 30, retries: int = 2) -> Dict[str, Any]:
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise
            return {"returncode": proc.returncode, "stdout": stdout.decode(errors="ignore"), "stderr": stderr.decode(errors="ignore"), "attempt": attempt}
        except Exception as exc:
            last_exc = exc
            logger.exception("Shell attempt %s failed: %s", attempt, exc)
            await asyncio.sleep(min(2 ** attempt, 10))
    return {"returncode": 1, "stdout": "", "stderr": str(last_exc), "attempts": retries}
