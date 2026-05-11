"""Async persistence wrapper using aiosqlite for key-value and approvals.
"""
import aiosqlite
import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

DB_PATH = "C:\\Users\\User\\Documents\\AgenteDesktop\\agent_state.db"

class Persistence:
    _instance = None

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ready = False

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = Persistence()
        return cls._instance

    async def _ensure(self):
        if self._ready:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, request_id TEXT, approver TEXT, status TEXT, payload TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            await db.commit()
        self._ready = True

    async def save_kv(self, k: str, v: Any):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('REPLACE INTO kv (k, v) VALUES (?, ?)', (k, json.dumps(v)))
            await db.commit()

    async def get_kv(self, k: str) -> Optional[Any]:
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute('SELECT v FROM kv WHERE k = ?', (k,))
            row = await cur.fetchone()
            if not row:
                return None
            return json.loads(row[0])

    async def delete_kv(self, k: str):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM kv WHERE k = ?', (k,))
            await db.commit()

    async def create_approval(self, id: str, request_id: str, approver: str, payload: dict):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('REPLACE INTO approvals (id, request_id, approver, status, payload) VALUES (?, ?, ?, ?, ?)', (id, request_id, approver, 'pending', json.dumps(payload)))
            await db.commit()

    async def set_approval(self, id: str, status: str):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('UPDATE approvals SET status = ? WHERE id = ?', (status, id))
            await db.commit()

    async def get_approval(self, id: str):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute('SELECT request_id, approver, status, payload FROM approvals WHERE id = ?', (id,))
            row = await cur.fetchone()
            if not row:
                return None
            request_id, approver, status, payload = row
            return {"request_id": request_id, "approver": approver, "status": status, "payload": json.loads(payload)}
