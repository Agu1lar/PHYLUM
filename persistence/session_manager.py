# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Session Manager: durable sessions per objective and per operational workspace.

Provides persistent sessions that:
- Group related runs under the same objective/workspace
- Survive backend restarts with full checkpoint state
- Support resume from the last checkpoint after crash/restart
- Track session-level context, goals, and progress across multiple runs
- Enable long-running multi-phase objectives that span multiple interactions
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

SESSION_STATUSES = {"active", "paused", "completed", "failed", "expired"}


class SessionManager:
    TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS agent_sessions (
        session_id TEXT PRIMARY KEY,
        workspace TEXT NOT NULL DEFAULT 'default',
        objective TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        context TEXT NOT NULL DEFAULT '{}',
        checkpoint TEXT NOT NULL DEFAULT '{}',
        run_ids TEXT NOT NULL DEFAULT '[]',
        goal_ids TEXT NOT NULL DEFAULT '[]',
        phases TEXT NOT NULL DEFAULT '[]',
        current_phase INTEGER NOT NULL DEFAULT 0,
        total_runs INTEGER NOT NULL DEFAULT 0,
        total_steps INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        last_activity_at TEXT
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

    async def create_session(
        self,
        *,
        workspace: str = "default",
        objective: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        phases: Optional[List[Dict[str, Any]]] = None,
        ttl_hours: int = 168,
    ) -> Dict[str, Any]:
        await self._ensure()
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()
        expires_at = (datetime.utcnow() + timedelta(hours=ttl_hours)).isoformat()
        session = {
            "session_id": session_id,
            "workspace": workspace,
            "objective": objective,
            "status": "active",
            "context": context or {},
            "checkpoint": {},
            "run_ids": [],
            "goal_ids": [],
            "phases": phases or [],
            "current_phase": 0,
            "total_runs": 0,
            "total_steps": 0,
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
            "last_activity_at": now,
        }
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(
                """INSERT INTO agent_sessions
                (session_id, workspace, objective, status, context, checkpoint, run_ids, goal_ids,
                 phases, current_phase, total_runs, total_steps, created_at, updated_at, expires_at, last_activity_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, workspace, objective, "active",
                 json.dumps(context or {}, default=str), json.dumps({}, default=str),
                 json.dumps([]), json.dumps([]),
                 json.dumps(phases or [], default=str), 0, 0, 0,
                 now, now, expires_at, now),
            )
            await db.commit()
        return session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,))
            row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    async def update_session(self, session_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        await self._ensure()
        now = datetime.utcnow().isoformat()
        set_parts: List[str] = ["updated_at = ?", "last_activity_at = ?"]
        params: list = [now, now]
        field_map = {
            "objective": "objective",
            "status": "status",
            "current_phase": "current_phase",
            "total_runs": "total_runs",
            "total_steps": "total_steps",
            "expires_at": "expires_at",
        }
        json_fields = {"context", "checkpoint", "run_ids", "goal_ids", "phases"}
        for key, col in field_map.items():
            if key in updates:
                set_parts.append(f"{col} = ?")
                params.append(updates[key])
        for key in json_fields:
            if key in updates:
                set_parts.append(f"{key} = ?")
                params.append(json.dumps(updates[key], default=str))
        params.append(session_id)
        query = f"UPDATE agent_sessions SET {', '.join(set_parts)} WHERE session_id = ?"
        async with aiosqlite.connect(self.persistence.db_path) as db:
            await db.execute(query, params)
            await db.commit()
        return await self.get_session(session_id)

    async def checkpoint(self, session_id: str, checkpoint_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = await self.get_session(session_id)
        if not session:
            return None
        existing_checkpoint = session.get("checkpoint") or {}
        existing_checkpoint.update(checkpoint_data)
        return await self.update_session(session_id, {"checkpoint": existing_checkpoint})

    async def add_run(self, session_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        session = await self.get_session(session_id)
        if not session:
            return None
        run_ids = session.get("run_ids") or []
        if request_id not in run_ids:
            run_ids.append(request_id)
        return await self.update_session(session_id, {
            "run_ids": run_ids,
            "total_runs": len(run_ids),
        })

    async def add_goal(self, session_id: str, goal_id: str) -> Optional[Dict[str, Any]]:
        session = await self.get_session(session_id)
        if not session:
            return None
        goal_ids = session.get("goal_ids") or []
        if goal_id not in goal_ids:
            goal_ids.append(goal_id)
        return await self.update_session(session_id, {"goal_ids": goal_ids})

    async def advance_phase(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = await self.get_session(session_id)
        if not session:
            return None
        phases = session.get("phases") or []
        current = session.get("current_phase", 0)
        if current < len(phases):
            phases_copy = list(phases)
            if current < len(phases_copy):
                phases_copy[current]["status"] = "completed"
                phases_copy[current]["completed_at"] = datetime.utcnow().isoformat()
            next_phase = current + 1
            if next_phase < len(phases_copy):
                phases_copy[next_phase]["status"] = "active"
                phases_copy[next_phase]["started_at"] = datetime.utcnow().isoformat()
            updates: Dict[str, Any] = {"phases": phases_copy, "current_phase": next_phase}
            if next_phase >= len(phases_copy):
                updates["status"] = "completed"
            return await self.update_session(session_id, updates)
        return session

    async def merge_context(self, session_id: str, new_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = await self.get_session(session_id)
        if not session:
            return None
        ctx = session.get("context") or {}
        ctx.update(new_context)
        return await self.update_session(session_id, {"context": ctx})

    async def list_sessions(
        self,
        *,
        workspace: Optional[str] = None,
        status: Optional[str] = None,
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
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(limit)
        query = f"SELECT * FROM agent_sessions {where} ORDER BY last_activity_at DESC LIMIT ?"
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def find_active_session(self, *, workspace: str = "default", objective: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Find an existing active session for a workspace, optionally matching objective."""
        await self._ensure()
        now = datetime.utcnow().isoformat()
        if objective:
            async with aiosqlite.connect(self.persistence.db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM agent_sessions WHERE workspace = ? AND objective = ? AND status = 'active' AND (expires_at IS NULL OR expires_at > ?) ORDER BY last_activity_at DESC LIMIT 1",
                    (workspace, objective, now),
                )
                row = await cur.fetchone()
                if row:
                    return self._row_to_dict(row)
        async with aiosqlite.connect(self.persistence.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM agent_sessions WHERE workspace = ? AND status = 'active' AND (expires_at IS NULL OR expires_at > ?) ORDER BY last_activity_at DESC LIMIT 1",
                (workspace, now),
            )
            row = await cur.fetchone()
            if row:
                return self._row_to_dict(row)
        return None

    async def expire_stale(self, *, inactive_hours: int = 168) -> int:
        await self._ensure()
        cutoff = (datetime.utcnow() - timedelta(hours=inactive_hours)).isoformat()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            cur = await db.execute(
                "UPDATE agent_sessions SET status = 'expired', updated_at = ? WHERE status = 'active' AND last_activity_at < ?",
                (now, cutoff),
            )
            await db.commit()
            return cur.rowcount

    async def cleanup_old(self, *, days: int = 90) -> int:
        await self._ensure()
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self.persistence.db_path) as db:
            cur = await db.execute(
                "DELETE FROM agent_sessions WHERE status IN ('completed', 'failed', 'expired') AND updated_at < ?",
                (cutoff,),
            )
            await db.commit()
            return cur.rowcount

    def _row_to_dict(self, row) -> Dict[str, Any]:
        d = dict(row)
        for field in ("context", "checkpoint", "run_ids", "goal_ids", "phases"):
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
