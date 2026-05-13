# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""World Model: typed entities with confidence, expiration and domain-specific stores.

Provides a persistent, queryable model of the operational world:
- Typed entities (share, app_path, document_alias, selector, path_candidate, device, etc.)
- Confidence scores (0.0-1.0) that decay over time
- TTL/expiration so stale knowledge is automatically pruned
- Domain-specific stores: discovered shares, document aliases, app paths, selectors
- Automatic reuse of valid candidates from previous discoveries
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_persistence import Persistence

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 86400 * 7  # 7 days
CONFIDENCE_DECAY_RATE = 0.05  # per day
MIN_CONFIDENCE = 0.1

ENTITY_TYPES = {
    "share": {"default_ttl": 86400 * 30, "description": "Discovered network share or mapped drive"},
    "app_path": {"default_ttl": 86400 * 90, "description": "Known application executable path"},
    "document_alias": {"default_ttl": 86400 * 14, "description": "Alias or shortcut name for a document path"},
    "selector": {"default_ttl": 86400 * 30, "description": "UI automation selector for a window/control"},
    "path_candidate": {"default_ttl": 86400 * 7, "description": "Validated filesystem path candidate"},
    "device": {"default_ttl": 86400 * 30, "description": "Known hardware device or printer"},
    "web_resource": {"default_ttl": 86400 * 3, "description": "Web URL or API endpoint"},
    "user_preference": {"default_ttl": 86400 * 365, "description": "Learned user preference or default"},
    "environment": {"default_ttl": 86400 * 30, "description": "Environment variable or system config"},
}


class WorldEntity:
    __slots__ = (
        "entity_type", "key", "value", "confidence", "created_at",
        "updated_at", "expires_at", "source", "tags", "hit_count",
        "last_used_at", "app_context",
    )

    def __init__(
        self,
        *,
        entity_type: str,
        key: str,
        value: Any,
        confidence: float = 0.8,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
        hit_count: int = 0,
        last_used_at: Optional[str] = None,
        app_context: Optional[str] = None,
    ):
        now = datetime.utcnow().isoformat()
        self.entity_type = entity_type
        self.key = key
        self.value = value
        self.confidence = max(0.0, min(1.0, confidence))
        self.created_at = created_at or now
        self.updated_at = updated_at or now
        self.expires_at = expires_at or self._default_expiry(entity_type)
        self.source = source
        self.tags = tags or []
        self.hit_count = hit_count
        self.last_used_at = last_used_at
        self.app_context = app_context

    @staticmethod
    def _default_expiry(entity_type: str) -> str:
        ttl = ENTITY_TYPES.get(entity_type, {}).get("default_ttl", DEFAULT_TTL_SECONDS)
        return (datetime.utcnow() + timedelta(seconds=ttl)).isoformat()

    @property
    def is_expired(self) -> bool:
        try:
            return datetime.fromisoformat(self.expires_at) < datetime.utcnow()
        except (ValueError, TypeError):
            return False

    @property
    def effective_confidence(self) -> float:
        try:
            updated = datetime.fromisoformat(self.updated_at)
            days_old = (datetime.utcnow() - updated).total_seconds() / 86400
            decayed = self.confidence - (days_old * CONFIDENCE_DECAY_RATE)
            return max(MIN_CONFIDENCE, decayed)
        except (ValueError, TypeError):
            return self.confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "effective_confidence": round(self.effective_confidence, 3),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "is_expired": self.is_expired,
            "source": self.source,
            "tags": self.tags,
            "hit_count": self.hit_count,
            "last_used_at": self.last_used_at,
            "app_context": self.app_context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorldEntity":
        return cls(
            entity_type=data["entity_type"],
            key=data["key"],
            value=data.get("value"),
            confidence=data.get("confidence", 0.8),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            expires_at=data.get("expires_at"),
            source=data.get("source"),
            tags=data.get("tags", []),
            hit_count=data.get("hit_count", 0),
            last_used_at=data.get("last_used_at"),
            app_context=data.get("app_context"),
        )


class WorldModel:
    KV_PREFIX = "world2:"

    def __init__(self, persistence: Optional[Persistence] = None, *, semantic_index=None):
        self.persistence = persistence or Persistence.get()
        self._semantic_index = semantic_index

    def set_semantic_index(self, index) -> None:
        self._semantic_index = index

    def _storage_key(self, entity_type: str, key: str) -> str:
        return f"{self.KV_PREFIX}{entity_type}:{key}"

    async def upsert(
        self,
        entity_type: str,
        key: str,
        value: Any,
        *,
        confidence: float = 0.8,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
        ttl_seconds: Optional[int] = None,
        app_context: Optional[str] = None,
    ) -> WorldEntity:
        storage_key = self._storage_key(entity_type, key)
        existing_raw = await self.persistence.get_kv(storage_key)
        now = datetime.utcnow().isoformat()

        if existing_raw and isinstance(existing_raw, dict):
            existing = WorldEntity.from_dict(existing_raw)
            existing.value = value
            existing.confidence = max(existing.confidence, confidence)
            existing.updated_at = now
            existing.source = source or existing.source
            if tags:
                existing.tags = list(set(existing.tags + tags))
            if ttl_seconds:
                existing.expires_at = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
            else:
                existing.expires_at = WorldEntity._default_expiry(entity_type)
            if app_context:
                existing.app_context = app_context
            existing.hit_count += 1
            await self.persistence.save_kv(storage_key, existing.to_dict())
            await self._index_entity(existing)
            return existing

        if ttl_seconds:
            expires_at = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
        else:
            expires_at = WorldEntity._default_expiry(entity_type)

        entity = WorldEntity(
            entity_type=entity_type,
            key=key,
            value=value,
            confidence=confidence,
            source=source,
            tags=tags,
            expires_at=expires_at,
            app_context=app_context,
        )
        await self.persistence.save_kv(storage_key, entity.to_dict())
        await self._index_entity(entity)
        return entity

    async def get(self, entity_type: str, key: str, *, record_hit: bool = True) -> Optional[WorldEntity]:
        storage_key = self._storage_key(entity_type, key)
        raw = await self.persistence.get_kv(storage_key)
        if not raw or not isinstance(raw, dict):
            return None
        entity = WorldEntity.from_dict(raw)
        if entity.is_expired:
            await self.persistence.delete_kv(storage_key)
            return None
        if record_hit:
            entity.hit_count += 1
            entity.last_used_at = datetime.utcnow().isoformat()
            await self.persistence.save_kv(storage_key, entity.to_dict())
        return entity

    async def query(
        self,
        entity_type: str,
        *,
        query: Optional[str] = None,
        min_confidence: float = 0.0,
        tags: Optional[List[str]] = None,
        app_context: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 50,
    ) -> List[WorldEntity]:
        prefix = f"{self.KV_PREFIX}{entity_type}:"
        records = await self.persistence.list_kv(prefix)
        entities: List[WorldEntity] = []

        for record in records:
            raw = record.get("value")
            if not isinstance(raw, dict):
                continue
            entity = WorldEntity.from_dict(raw)
            if entity.is_expired and not include_expired:
                continue
            if entity.effective_confidence < min_confidence:
                continue
            if tags and not any(t in entity.tags for t in tags):
                continue
            if app_context and entity.app_context != app_context:
                continue
            if query:
                lowered = query.lower()
                searchable = f"{entity.key} {json.dumps(entity.value, default=str)}".lower()
                if lowered not in searchable:
                    continue
            entities.append(entity)

        entities.sort(key=lambda e: (-e.effective_confidence, -e.hit_count))
        return entities[:limit]

    async def delete(self, entity_type: str, key: str) -> bool:
        storage_key = self._storage_key(entity_type, key)
        existing = await self.persistence.get_kv(storage_key)
        if existing is None:
            return False
        await self.persistence.delete_kv(storage_key)
        return True

    async def touch(self, entity_type: str, key: str, *, boost_confidence: float = 0.0) -> Optional[WorldEntity]:
        entity = await self.get(entity_type, key, record_hit=True)
        if entity is None:
            return None
        if boost_confidence > 0:
            entity.confidence = min(1.0, entity.confidence + boost_confidence)
            storage_key = self._storage_key(entity_type, key)
            await self.persistence.save_kv(storage_key, entity.to_dict())
        return entity

    async def prune_expired(self, entity_type: Optional[str] = None) -> int:
        prefix = f"{self.KV_PREFIX}{entity_type}:" if entity_type else self.KV_PREFIX
        records = await self.persistence.list_kv(prefix)
        pruned = 0
        for record in records:
            raw = record.get("value")
            if not isinstance(raw, dict):
                continue
            entity = WorldEntity.from_dict(raw)
            if entity.is_expired:
                await self.persistence.delete_kv(record["key"])
                pruned += 1
        return pruned

    async def best_candidate(
        self,
        entity_type: str,
        *,
        query: Optional[str] = None,
        min_confidence: float = 0.3,
        app_context: Optional[str] = None,
    ) -> Optional[WorldEntity]:
        results = await self.query(
            entity_type,
            query=query,
            min_confidence=min_confidence,
            app_context=app_context,
            limit=1,
        )
        return results[0] if results else None

    async def _index_entity(self, entity: WorldEntity) -> None:
        if self._semantic_index is None:
            return
        try:
            await self._semantic_index.upsert_entity(
                entity.entity_type,
                entity.key,
                entity.value,
                confidence=entity.confidence,
                tags=entity.tags,
                app_context=entity.app_context,
            )
        except Exception:
            logger.debug("Failed to index entity %s:%s", entity.entity_type, entity.key, exc_info=True)

    async def semantic_search(
        self,
        query: str,
        *,
        entity_type: Optional[str] = None,
        app_context: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.1,
    ) -> List[Dict[str, Any]]:
        """Semantic vector search across entities. Falls back to typed query if no index."""
        if self._semantic_index is not None:
            try:
                return await self._semantic_index.search_entities(
                    query,
                    entity_type=entity_type,
                    app_context=app_context,
                    limit=limit,
                    min_score=min_score,
                )
            except Exception:
                logger.debug("Semantic search failed, falling back to typed query", exc_info=True)

        entities = await self.query(
            entity_type or "share",
            query=query,
            app_context=app_context,
            limit=limit,
        )
        return [e.to_dict() for e in entities]

    # --- Domain-specific convenience methods ---

    async def remember_share(
        self, name: str, remote_path: str, *, local_path: Optional[str] = None,
        confidence: float = 0.9, source: str = "discovery",
    ) -> WorldEntity:
        return await self.upsert(
            "share", name,
            {"remote_path": remote_path, "local_path": local_path},
            confidence=confidence, source=source, tags=["network"],
        )

    async def remember_app_path(
        self, app_name: str, exe_path: str, *, confidence: float = 0.9, source: str = "discovery",
    ) -> WorldEntity:
        return await self.upsert(
            "app_path", app_name.lower(),
            {"exe_path": exe_path, "app_name": app_name},
            confidence=confidence, source=source, tags=["app"],
        )

    async def remember_document_alias(
        self, alias: str, real_path: str, *, confidence: float = 0.85, source: str = "user",
    ) -> WorldEntity:
        return await self.upsert(
            "document_alias", alias.lower(),
            {"real_path": real_path, "alias": alias},
            confidence=confidence, source=source, tags=["document"],
        )

    async def remember_selector(
        self,
        selector_key: str,
        selector_data: Dict[str, Any],
        *,
        app_context: Optional[str] = None,
        confidence: float = 0.85,
        source: str = "ui_automation",
    ) -> WorldEntity:
        return await self.upsert(
            "selector", selector_key,
            selector_data,
            confidence=confidence, source=source,
            tags=["ui", "selector"], app_context=app_context,
        )

    async def remember_path_candidate(
        self, label: str, path: str, *, confidence: float = 0.7, source: str = "discovery",
    ) -> WorldEntity:
        return await self.upsert(
            "path_candidate", label.lower(),
            {"path": path, "label": label},
            confidence=confidence, source=source, tags=["path"],
        )

    async def find_share(self, query: str) -> Optional[WorldEntity]:
        return await self.best_candidate("share", query=query, min_confidence=0.3)

    async def find_app_path(self, app_name: str) -> Optional[WorldEntity]:
        return await self.best_candidate("app_path", query=app_name.lower(), min_confidence=0.3)

    async def find_document_alias(self, alias: str) -> Optional[WorldEntity]:
        return await self.best_candidate("document_alias", query=alias.lower(), min_confidence=0.3)

    async def find_selector(self, selector_key: str, *, app_context: Optional[str] = None) -> Optional[WorldEntity]:
        return await self.best_candidate("selector", query=selector_key, app_context=app_context, min_confidence=0.3)

    async def query_similar_selectors(
        self,
        *,
        app_context: Optional[str] = None,
        query: Optional[str] = None,
        min_confidence: float = 0.2,
        limit: int = 20,
    ) -> List[WorldEntity]:
        """Broader selector search for healing: returns multiple candidates sorted by confidence."""
        return await self.query(
            "selector",
            query=query,
            app_context=app_context,
            min_confidence=min_confidence,
            limit=limit,
        )

    async def find_path_candidate(self, label: str) -> Optional[WorldEntity]:
        return await self.best_candidate("path_candidate", query=label.lower(), min_confidence=0.3)

    async def list_entity_types(self) -> List[Dict[str, Any]]:
        result = []
        for etype, info in ENTITY_TYPES.items():
            entities = await self.query(etype, limit=1)
            result.append({
                "entity_type": etype,
                "description": info["description"],
                "default_ttl_days": info["default_ttl"] // 86400,
                "count_sample": len(entities),
            })
        return result
