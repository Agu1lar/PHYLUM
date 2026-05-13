# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import asyncio
import shlex
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


async def run_powershell(cmd: str, timeout: int = 30) -> Dict[str, Any]:
    """Run a PowerShell command using asyncio.create_subprocess_exec and return structured output."""
    # Build argument list for powershell.exe
    # Use -NoProfile -NonInteractive -Command <cmd>
    exe = 'powershell'
    args = [exe, '-NoProfile', '-NonInteractive', '-Command', cmd]
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {'returncode': -1, 'stdout': '', 'stderr': 'timeout'}
        return {'returncode': proc.returncode, 'stdout': stdout.decode(errors='ignore'), 'stderr': stderr.decode(errors='ignore')}
    except FileNotFoundError:
        logger.exception('PowerShell not found')
        return {'returncode': -2, 'stdout': '', 'stderr': 'pwsh not found'}
    except Exception as exc:
        logger.exception('pwsh run failed')
        return {'returncode': -3, 'stdout': '', 'stderr': str(exc)}
