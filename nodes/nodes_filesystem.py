# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from nodes_base import BaseNode
from typing import Dict, Any
import logging
from agent_persistence import Persistence

logger = logging.getLogger(__name__)

class FileSystemNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        return True

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        persistence = Persistence.get()
        key = f"fs:{state.get('request_id')}:snapshot"
        await persistence.save_kv(key, {"fs": "ok"})
        return {"fs_saved": True}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("fs_saved", False)

    async def rollback(self, state: Dict[str, Any], result: Dict[str, Any]) -> None:
        persistence = Persistence.get()
        key = f"fs:{state.get('request_id')}:snapshot"
        await persistence.delete_kv(key)
