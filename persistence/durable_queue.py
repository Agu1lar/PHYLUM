# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Durable Goal Queue: persistent, prioritized queue of objectives with retry and scheduling.

Provides a SQLite-backed queue that survives backend restarts. Goals can be:
- enqueued with priority, max_retries, delay, and optional scheduling
- dequeued in priority order (lower number = higher priority)
- marked as running, completed, failed, or cancelled
- automatically retried up to max_retries on failure
- deferred for later execution via scheduled_at
- queried by status or workspace
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiosqlite

from agent_persistence import Persistence

logger = logging.getLogger(__name__)

GOAL_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "deferred", "retrying"}


class DurableQueue:
    TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS goal_queue (
        goal_id TEXT PRIMARY KEY,
        workspace TEXT NOT NULL DEFAULT 'default',
        priority INTEGER NOT NULL DEFAULT 50,
        status TEXT NOT NULL DEFAULT 'queued',
        inputs TEXT NOT NULL,
        runtime_mode TEXT NOT NULL DEFAULT 'agentic',
        provider TEXT,
        model TEXT,
        parent_goal_id TEXT,
        request_id TEXT,
        attempt INTEGER NOT NULL DEFAULT 0,
        max_retries INTEGER NOT NULL DEFAULT 2,
        retry_delay_seconds INTEGER NOT NULL DEFAULT 30,
        error TEXT,
        result_summary TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        scheduled_at TEXT,
        started_at TEXT,
        completed_at TEXT
    )
    """

    def __init__(self, persistence: Optional[Persistence] = None):
        self.persistence = persistence or Persistence.get()
        self._ready = False

    async def _ensure(self) -> None:
        if self._ready:
            return
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(self.TABLE_DDL)
            await db.commit()
        self._ready = True

    async def enqueue(
        self,
        inputs: Dict[str, Any],
        *,
        workspace: str = "default",
        priority: int = 50,
        runtime_mode: str = "agentic",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        max_retries: int = 2,
        retry_delay_seconds: int = 30,
        scheduled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure()
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()
        status = "deferred" if scheduled_at else "queued"
        goal = {
            "goal_id": goal_id,
            "workspace": workspace,
            "priority": priority,
            "status": status,
            "inputs": inputs,
            "runtime_mode": runtime_mode,
            "provider": provider,
            "model": model,
            "parent_goal_id": parent_goal_id,
            "attempt": 0,
            "max_retries": max_retries,
            "retry_delay_seconds": retry_delay_seconds,
            "error": None,
            "result_summary": None,
            "created_at": now,
            "updated_at": now,
            "scheduled_at": scheduled_at,
            "started_at": None,
            "completed_at": None,
            "request_id": None,
        }
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(
                """INSERT INTO goal_queue
                (goal_id, workspace, priority, status, inputs, runtime_mode, provider, model,
                 parent_goal_id, attempt, max_retries, retry_delay_seconds, created_at, updated_at, scheduled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (goal_id, workspace, priority, status, json.dumps(inputs, default=str),
                 runtime_mode, provider, model, parent_goal_id, 0, max_retries,
                 retry_delay_seconds, now, now, scheduled_at),
            )
            await db.commit()
        return goal

    async def dequeue(self, *, workspace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        await self._ensure()
        now = datetime.utcnow().isoformat()
        where = "WHERE status IN ('queued', 'retrying')"
        params: list = []
        if workspace:
            where += " AND workspace = ?"
            params.append(workspace)
        where += " AND (scheduled_at IS NULL OR scheduled_at <= ?)"
        params.append(now)
        query = f"SELECT * FROM goal_queue {where} ORDER BY priority ASC, created_at ASC LIMIT 1"
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            row = await cur.fetchone()
            if not row:
                return None
            goal = self._row_to_dict(row)
            await db.execute(
                "UPDATE goal_queue SET status = 'running', started_at = ?, updated_at = ?, attempt = attempt + 1 WHERE goal_id = ?",
                (now, now, goal["goal_id"]),
            )
            await db.commit()
        goal["status"] = "running"
        goal["started_at"] = now
        goal["attempt"] += 1
        return goal

    async def mark_running(self, goal_id: str, *, request_id: Optional[str] = None) -> None:
        await self._ensure()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(
                "UPDATE goal_queue SET status = 'running', request_id = ?, started_at = COALESCE(started_at, ?), updated_at = ? WHERE goal_id = ?",
                (request_id, now, now, goal_id),
            )
            await db.commit()

    async def mark_completed(self, goal_id: str, *, result_summary: Optional[str] = None) -> None:
        await self._ensure()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(
                "UPDATE goal_queue SET status = 'completed', result_summary = ?, completed_at = ?, updated_at = ? WHERE goal_id = ?",
                (result_summary, now, now, goal_id),
            )
            await db.commit()

    async def mark_failed(self, goal_id: str, error: str) -> Optional[Dict[str, Any]]:
        await self._ensure()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM goal_queue WHERE goal_id = ?", (goal_id,))
            row = await cur.fetchone()
            if not row:
                return None
            goal = self._row_to_dict(row)
            if goal["attempt"] < goal["max_retries"]:
                retry_at = (datetime.utcnow() + timedelta(seconds=goal["retry_delay_seconds"])).isoformat()
                await db.execute(
                    "UPDATE goal_queue SET status = 'retrying', error = ?, scheduled_at = ?, updated_at = ? WHERE goal_id = ?",
                    (error, retry_at, now, goal_id),
                )
                goal["status"] = "retrying"
                goal["scheduled_at"] = retry_at
            else:
                await db.execute(
                    "UPDATE goal_queue SET status = 'failed', error = ?, completed_at = ?, updated_at = ? WHERE goal_id = ?",
                    (error, now, now, goal_id),
                )
                goal["status"] = "failed"
                goal["completed_at"] = now
            goal["error"] = error
            goal["updated_at"] = now
            await db.commit()
        return goal

    async def mark_cancelled(self, goal_id: str) -> None:
        await self._ensure()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(
                "UPDATE goal_queue SET status = 'cancelled', completed_at = ?, updated_at = ? WHERE goal_id = ?",
                (now, now, goal_id),
            )
            await db.commit()

    async def get_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM goal_queue WHERE goal_id = ?", (goal_id,))
            row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    async def list_goals(
        self,
        *,
        workspace: Optional[str] = None,
        status: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        await self._ensure()
        where_parts: List[str] = []
        params: list = []
        if workspace:
            where_parts.append("workspace = ?")
            params.append(workspace)
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if parent_goal_id:
            where_parts.append("parent_goal_id = ?")
            params.append(parent_goal_id)
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(limit)
        query = f"SELECT * FROM goal_queue {where} ORDER BY priority ASC, created_at ASC LIMIT ?"
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def pending_count(self, *, workspace: Optional[str] = None) -> int:
        await self._ensure()
        where = "WHERE status IN ('queued', 'retrying', 'deferred')"
        params: list = []
        if workspace:
            where += " AND workspace = ?"
            params.append(workspace)
        async with aiosqlite.connect(self.persistence.db_path) as db:
            cur = await db.execute(f"SELECT COUNT(*) FROM goal_queue {where}", params)
            row = await cur.fetchone()
            return row[0] if row else 0

    async def promote_deferred(self) -> int:
        """Move deferred goals whose scheduled_at has passed to queued status."""
        await self._ensure()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            cur = await db.execute(
                "UPDATE goal_queue SET status = 'queued', updated_at = ? WHERE status IN ('deferred', 'retrying') AND scheduled_at IS NOT NULL AND scheduled_at <= ?",
                (now, now),
            )
            await db.commit()
            return cur.rowcount

    async def recover_stale_running(self, *, stale_seconds: int = 600) -> int:
        """Mark goals stuck in 'running' beyond stale_seconds as retrying or failed."""
        await self._ensure()
        cutoff = (datetime.utcnow() - timedelta(seconds=stale_seconds)).isoformat()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM goal_queue WHERE status = 'running' AND started_at IS NOT NULL AND started_at < ?",
                (cutoff,),
            )
            rows = await cur.fetchall()
            recovered = 0
            for row in rows:
                goal = self._row_to_dict(row)
                if goal["attempt"] < goal["max_retries"]:
                    retry_at = (datetime.utcnow() + timedelta(seconds=goal["retry_delay_seconds"])).isoformat()
                    await db.execute(
                        "UPDATE goal_queue SET status = 'retrying', error = 'stale_running_recovered', scheduled_at = ?, updated_at = ? WHERE goal_id = ?",
                        (retry_at, now, goal["goal_id"]),
                    )
                else:
                    await db.execute(
                        "UPDATE goal_queue SET status = 'failed', error = 'exceeded_max_retries_after_stale', completed_at = ?, updated_at = ? WHERE goal_id = ?",
                        (now, now, goal["goal_id"]),
                    )
                recovered += 1
            await db.commit()
        return recovered

    async def cleanup_old(self, *, days: int = 30) -> int:
        await self._ensure()
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            cur = await db.execute(
                "DELETE FROM goal_queue WHERE status IN ('completed', 'failed', 'cancelled') AND completed_at < ?",
                (cutoff,),
            )
            await db.commit()
            return cur.rowcount

    def _row_to_dict(self, row) -> Dict[str, Any]:
        d = dict(row)
        if "inputs" in d and isinstance(d["inputs"], str):
            try:
                d["inputs"] = json.loads(d["inputs"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d
