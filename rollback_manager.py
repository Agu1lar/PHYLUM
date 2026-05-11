from typing import List, Dict, Any
from pathlib import Path
import asyncio
import logging
from pydantic import BaseModel
from agent_persistence import Persistence
import json

logger = logging.getLogger(__name__)


class OperationRecord(BaseModel):
    id: str
    type: str  # move, delete, write
    src: str
    dst: str = None
    meta: Dict[str, Any] = {}


class RollbackManager:
    def __init__(self):
        self.persistence = Persistence.get()

    async def record(self, request_id: str, op: OperationRecord):
        key = f"fs:history:{request_id}"
        hist = await self.persistence.get_kv(key) or []
        hist.append(json.loads(op.json()))
        await self.persistence.save_kv(key, hist)

    async def get_history(self, request_id: str):
        key = f"fs:history:{request_id}"
        return await self.persistence.get_kv(key) or []

    async def undo_last(self, request_id: str, n: int = 1):
        hist = await self.get_history(request_id)
        if not hist:
            return []
        undone = []
        for _ in range(min(n, len(hist))):
            op = hist.pop()
            try:
                await self._undo(op)
                undone.append(op)
            except Exception:
                logger.exception('undo failed for op %s', op)
        # save trimmed history
        key = f"fs:history:{request_id}"
        await self.persistence.save_kv(key, hist)
        return undone

    async def _undo(self, op: Dict):
        t = op.get('type')
        if t == 'move':
            src = Path(op.get('dst'))
            dst = Path(op.get('src'))
            await asyncio.to_thread(src.replace, dst)
        elif t == 'delete':
            # delete was quarantine - restore
            q = Path(op.get('dst'))
            original = Path(op.get('src'))
            await asyncio.to_thread(q.replace, original)
        elif t == 'write':
            # write backups may be stored in meta
            backup = op.get('meta', {}).get('backup_path')
            if backup:
                await asyncio.to_thread(Path(backup).replace, Path(op.get('src')))
        else:
            raise NotImplementedError('undo for type %s' % t)
