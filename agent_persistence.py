"""Async persistence wrapper using aiosqlite for key-value and approvals."""
import aiosqlite
import json
import logging
from typing import Optional, Any, List, Dict

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
            await db.execute(
                '''CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                request_id TEXT,
                task_id TEXT,
                approver TEXT,
                status TEXT,
                payload TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )'''
            )
            cur = await db.execute("PRAGMA table_info(approvals)")
            columns = {row[1] for row in await cur.fetchall()}
            if "task_id" not in columns:
                await db.execute("ALTER TABLE approvals ADD COLUMN task_id TEXT")
            await db.commit()
        self._ready = True

    async def save_kv(self, k: str, v: Any):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('REPLACE INTO kv (k, v) VALUES (?, ?)', (k, json.dumps(v, default=str)))
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

    async def delete_state(self, request_id: str):
        await self.delete_kv(f"state:{request_id}")

    async def delete_approvals(self, request_id: str):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM approvals WHERE request_id = ?', (request_id,))
            await db.commit()

    async def list_kv(self, prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        await self._ensure()
        query = 'SELECT k, v, updated_at FROM kv'
        params = ()
        if prefix is not None:
            query += ' WHERE k LIKE ?'
            params = (f"{prefix}%",)
        query += ' ORDER BY updated_at DESC'
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            return [
                {
                    "key": row[0],
                    "value": json.loads(row[1]),
                    "updated_at": row[2],
                }
                for row in rows
            ]

    async def list_states(self) -> List[Dict[str, Any]]:
        records = await self.list_kv("state:")
        states: List[Dict[str, Any]] = []
        for record in records:
            value = record["value"]
            if isinstance(value, dict):
                states.append(value)
        return states

    async def create_approval(self, id: str, request_id: str, approver: str, payload: dict, task_id: Optional[str] = None):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'REPLACE INTO approvals (id, request_id, task_id, approver, status, payload) VALUES (?, ?, ?, ?, ?, ?)',
                (id, request_id, task_id, approver, 'pending', json.dumps(payload, default=str)),
            )
            await db.commit()

    async def set_approval(self, id: str, status: str):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('UPDATE approvals SET status = ? WHERE id = ?', (status, id))
            await db.commit()

    async def get_approval(self, id: str):
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute('SELECT request_id, task_id, approver, status, payload FROM approvals WHERE id = ?', (id,))
            row = await cur.fetchone()
            if not row:
                return None
            request_id, task_id, approver, status, payload = row
            return {
                "id": id,
                "request_id": request_id,
                "task_id": task_id,
                "approver": approver,
                "status": status,
                "payload": json.loads(payload),
            }

    async def list_approvals(self, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        await self._ensure()
        query = 'SELECT id, request_id, task_id, approver, status, payload FROM approvals'
        params = ()
        if request_id:
            query += ' WHERE request_id = ?'
            params = (request_id,)
        query += ' ORDER BY created_at ASC'
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            return [
                {
                    "id": row[0],
                    "request_id": row[1],
                    "task_id": row[2],
                    "approver": row[3],
                    "status": row[4],
                    "payload": json.loads(row[5]),
                }
                for row in rows
            ]
