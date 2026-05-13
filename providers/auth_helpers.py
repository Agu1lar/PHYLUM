# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Auth helpers for automated logins using selector-driven interactions.
Requires caller to provide stable selectors to avoid brittle flows.
"""
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def do_login(page, creds: Dict[str, Any], steps: Dict[str, Any], timeout: int = 30):
    """Perform login by executing a series of steps.
    steps: list of {'action':'fill'|'click'|'wait','selector':str,'value':optional}
    creds: map of values available for value templates e.g. '{username}' replaced with creds['username']
    """
    for step in steps:
        action = step.get('action')
        selector = step.get('selector')
        value = step.get('value')
        if isinstance(value, str) and value.startswith('{') and value.endswith('}'):
            key = value[1:-1]
            value = creds.get(key)
        try:
            if action == 'fill':
                await page.fill(selector, value, timeout=timeout*1000)
            elif action == 'click':
                await page.click(selector, timeout=timeout*1000)
            elif action == 'wait':
                await page.wait_for_selector(selector, timeout=timeout*1000)
            elif action == 'press':
                await page.press(selector, value, timeout=timeout*1000)
            else:
                logger.warning('unknown auth action %s', action)
        except Exception:
            logger.exception('auth step failed %s', step)
            raise
    return True
