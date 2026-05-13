import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from tool_base import BaseTool
from agent_persistence import Persistence
from world_model import WorldModel
from strategy_memory import StrategyMemory

logger = logging.getLogger(__name__)

MEMORY_ACTIONS = (
    'set|get|delete|list|upsert_entity|query_entities|record_observation|'
    'world_upsert|world_get|world_query|world_delete|world_touch|world_prune|world_types|'
    'world_remember_share|world_remember_app|world_remember_alias|world_remember_selector|world_remember_path|'
    'world_find_share|world_find_app|world_find_alias|world_find_selector|world_find_path|'
    'strategy_record_success|strategy_record_failure|strategy_find|strategy_best|strategy_reused|strategy_goal_types|'
    'semantic_search_strategies|semantic_search_entities'
)


class MemoryInput(BaseModel):
    action: str = Field(..., pattern=f'^({MEMORY_ACTIONS})$')
    key: Optional[str] = None
    value: Optional[Dict[str, Any]] = None
    entity_type: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    query: Optional[str] = None
    confidence: Optional[float] = None
    source: Optional[str] = None
    tags: Optional[List[str]] = None
    ttl_seconds: Optional[int] = None
    app_context: Optional[str] = None
    min_confidence: Optional[float] = None
    strategy_id: Optional[str] = None
    goal_type: Optional[str] = None
    goal_summary: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    boost_confidence: Optional[float] = None
    limit: Optional[int] = None


class MemoryOutput(BaseModel):
    success: bool
    value: Optional[Dict[str, Any]] = None
    items: Optional[List[Dict[str, Any]]] = None
    message: Optional[str] = None


class MemoryTool(BaseTool):
    InputModel = MemoryInput
    OutputModel = MemoryOutput

    def __init__(self, *, default_timeout: int = 10, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.persistence = Persistence.get()
        self.world_model = WorldModel(self.persistence)
        self.strategy_memory = StrategyMemory(self.persistence)

    async def validate(self, payload: MemoryInput) -> None:
        if payload.action in ('set', 'get', 'delete') and not payload.key:
            raise ValueError('key is required')
        if payload.action in ('upsert_entity', 'record_observation') and (not payload.entity_type or not payload.key and payload.action == 'upsert_entity'):
            raise ValueError('entity_type and key are required for upsert_entity; entity_type is required for record_observation')
        if payload.action == 'query_entities' and not payload.entity_type:
            raise ValueError('entity_type is required for query_entities')
        if payload.action in ('world_upsert',) and (not payload.entity_type or not payload.key):
            raise ValueError('entity_type and key are required for world_upsert')
        if payload.action in ('world_get', 'world_delete', 'world_touch') and (not payload.entity_type or not payload.key):
            raise ValueError('entity_type and key are required')
        if payload.action == 'world_query' and not payload.entity_type:
            raise ValueError('entity_type is required for world_query')
        if payload.action.startswith('world_remember_') and not payload.key:
            raise ValueError('key is required for world_remember_* actions')
        if payload.action.startswith('world_find_') and not payload.query:
            raise ValueError('query is required for world_find_* actions')
        if payload.action == 'strategy_record_success' and (not payload.strategy_id or not payload.goal_type):
            raise ValueError('strategy_id and goal_type are required for strategy_record_success')
        if payload.action == 'strategy_record_failure' and not payload.goal_type:
            raise ValueError('goal_type is required for strategy_record_failure')
        if payload.action in ('strategy_find', 'strategy_best') and not payload.goal_type:
            raise ValueError('goal_type is required')
        if payload.action == 'strategy_reused' and (not payload.goal_type or not payload.strategy_id):
            raise ValueError('goal_type and strategy_id are required')

    async def _list_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        records = await self.persistence.list_kv(prefix)
        return [
            {"key": item["key"], "value": item["value"], "updated_at": item["updated_at"]}
            for item in records
        ]

    async def _run(self, payload: MemoryInput) -> MemoryOutput:
        if payload.action == 'set':
            await self.persistence.save_kv(f"mem:{payload.key}", payload.value)
            return MemoryOutput(success=True, value=payload.value, message='saved')
        if payload.action == 'get':
            v = await self.persistence.get_kv(f"mem:{payload.key}")
            return MemoryOutput(success=True, value=v, message='fetched')
        if payload.action == 'delete':
            await self.persistence.delete_kv(f"mem:{payload.key}")
            return MemoryOutput(success=True, message='deleted')
        if payload.action == 'list':
            prefix = f"world:{payload.entity_type}:" if payload.entity_type else "mem:"
            items = await self._list_prefix(prefix)
            return MemoryOutput(success=True, items=items, message='listed')
        if payload.action == 'upsert_entity':
            entity_key = f"world:{payload.entity_type}:{payload.key}"
            entity_value = {
                "entity_type": payload.entity_type,
                "key": payload.key,
                "attributes": payload.attributes or payload.value or {},
                "updated_at": datetime.utcnow().isoformat(),
            }
            await self.persistence.save_kv(entity_key, entity_value)
            return MemoryOutput(success=True, value=entity_value, message='entity_upserted')
        if payload.action == 'query_entities':
            items = await self._list_prefix(f"world:{payload.entity_type}:")
            if payload.query:
                lowered = payload.query.lower()
                items = [item for item in items if lowered in str(item.get("value", "")).lower() or lowered in item["key"].lower()]
            return MemoryOutput(success=True, items=items, message='entities_queried')
        if payload.action == 'record_observation':
            observation_key = payload.key or uuid.uuid4().hex
            entity_key = f"world:observation:{payload.entity_type}:{observation_key}"
            observation = {
                "entity_type": payload.entity_type,
                "key": observation_key,
                "observation": payload.attributes or payload.value or {},
                "recorded_at": datetime.utcnow().isoformat(),
            }
            await self.persistence.save_kv(entity_key, observation)
            return MemoryOutput(success=True, value=observation, message='observation_recorded')

        # --- World Model actions ---
        if payload.action == 'world_upsert':
            entity = await self.world_model.upsert(
                payload.entity_type, payload.key,
                payload.value or payload.attributes or {},
                confidence=payload.confidence or 0.8,
                source=payload.source,
                tags=payload.tags,
                ttl_seconds=payload.ttl_seconds,
                app_context=payload.app_context,
            )
            return MemoryOutput(success=True, value=entity.to_dict(), message='world_entity_upserted')
        if payload.action == 'world_get':
            entity = await self.world_model.get(payload.entity_type, payload.key)
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='world_entity_found')
        if payload.action == 'world_query':
            entities = await self.world_model.query(
                payload.entity_type,
                query=payload.query,
                min_confidence=payload.min_confidence or 0.0,
                tags=payload.tags,
                app_context=payload.app_context,
            )
            return MemoryOutput(success=True, items=[e.to_dict() for e in entities], message='world_entities_queried')
        if payload.action == 'world_delete':
            deleted = await self.world_model.delete(payload.entity_type, payload.key)
            return MemoryOutput(success=True, message='deleted' if deleted else 'not_found')
        if payload.action == 'world_touch':
            entity = await self.world_model.touch(
                payload.entity_type, payload.key,
                boost_confidence=payload.boost_confidence or 0.0,
            )
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='touched')
        if payload.action == 'world_prune':
            count = await self.world_model.prune_expired(payload.entity_type)
            return MemoryOutput(success=True, value={"pruned_count": count}, message=f'pruned {count} expired entities')
        if payload.action == 'world_types':
            types = await self.world_model.list_entity_types()
            return MemoryOutput(success=True, items=types, message='entity_types_listed')

        # --- Domain-specific world model shortcuts ---
        if payload.action == 'world_remember_share':
            val = payload.value or payload.attributes or {}
            entity = await self.world_model.remember_share(
                payload.key, val.get("remote_path", ""),
                local_path=val.get("local_path"),
                confidence=payload.confidence or 0.9,
                source=payload.source or "discovery",
            )
            return MemoryOutput(success=True, value=entity.to_dict(), message='share_remembered')
        if payload.action == 'world_remember_app':
            val = payload.value or payload.attributes or {}
            entity = await self.world_model.remember_app_path(
                payload.key, val.get("exe_path", ""),
                confidence=payload.confidence or 0.9,
                source=payload.source or "discovery",
            )
            return MemoryOutput(success=True, value=entity.to_dict(), message='app_path_remembered')
        if payload.action == 'world_remember_alias':
            val = payload.value or payload.attributes or {}
            entity = await self.world_model.remember_document_alias(
                payload.key, val.get("real_path", ""),
                confidence=payload.confidence or 0.85,
                source=payload.source or "user",
            )
            return MemoryOutput(success=True, value=entity.to_dict(), message='alias_remembered')
        if payload.action == 'world_remember_selector':
            entity = await self.world_model.remember_selector(
                payload.key, payload.value or payload.attributes or {},
                app_context=payload.app_context,
                confidence=payload.confidence or 0.85,
                source=payload.source or "ui_automation",
            )
            return MemoryOutput(success=True, value=entity.to_dict(), message='selector_remembered')
        if payload.action == 'world_remember_path':
            val = payload.value or payload.attributes or {}
            entity = await self.world_model.remember_path_candidate(
                payload.key, val.get("path", ""),
                confidence=payload.confidence or 0.7,
                source=payload.source or "discovery",
            )
            return MemoryOutput(success=True, value=entity.to_dict(), message='path_remembered')

        if payload.action == 'world_find_share':
            entity = await self.world_model.find_share(payload.query)
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='share_found')
        if payload.action == 'world_find_app':
            entity = await self.world_model.find_app_path(payload.query)
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='app_found')
        if payload.action == 'world_find_alias':
            entity = await self.world_model.find_document_alias(payload.query)
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='alias_found')
        if payload.action == 'world_find_selector':
            entity = await self.world_model.find_selector(payload.query, app_context=payload.app_context)
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='selector_found')
        if payload.action == 'world_find_path':
            entity = await self.world_model.find_path_candidate(payload.query)
            if entity is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=entity.to_dict(), message='path_found')

        # --- Strategy Memory actions ---
        if payload.action == 'strategy_record_success':
            record = await self.strategy_memory.record_success(
                strategy_id=payload.strategy_id,
                goal_type=payload.goal_type,
                goal_summary=payload.goal_summary or "",
                steps=payload.steps or [],
                confidence=payload.confidence or 0.85,
                context_tags=payload.tags,
                duration_ms=payload.duration_ms,
            )
            return MemoryOutput(success=True, value=record.to_dict(), message='strategy_recorded')
        if payload.action == 'strategy_record_failure':
            record = await self.strategy_memory.record_failure(
                goal_type=payload.goal_type,
                approach_summary=payload.goal_summary or "",
                steps=payload.steps or [],
                error=payload.error or "unknown",
                context_tags=payload.tags,
            )
            return MemoryOutput(success=True, value=record, message='failure_recorded')
        if payload.action == 'strategy_find':
            strategies = await self.strategy_memory.find_strategies(
                payload.goal_type,
                query=payload.query,
                min_confidence=payload.min_confidence or 0.0,
                context_tags=payload.tags,
            )
            return MemoryOutput(success=True, items=[s.to_dict() for s in strategies], message='strategies_found')
        if payload.action == 'strategy_best':
            strategy = await self.strategy_memory.best_strategy(payload.goal_type, query=payload.query)
            if strategy is None:
                return MemoryOutput(success=True, value=None, message='no_strategy_found')
            return MemoryOutput(success=True, value=strategy.to_dict(), message='best_strategy_found')
        if payload.action == 'strategy_reused':
            record = await self.strategy_memory.mark_reused(payload.goal_type, payload.strategy_id)
            if record is None:
                return MemoryOutput(success=True, value=None, message='not_found')
            return MemoryOutput(success=True, value=record.to_dict(), message='strategy_reused')
        if payload.action == 'strategy_goal_types':
            types = await self.strategy_memory.list_goal_types()
            return MemoryOutput(success=True, items=types, message='goal_types_listed')

        if payload.action == 'semantic_search_strategies':
            results = await self.strategy_memory.semantic_search(
                payload.query or '',
                goal_type=payload.goal_type if payload.goal_type else None,
                limit=int(payload.limit or 5),
            )
            return MemoryOutput(success=True, items=results, message='semantic_strategies_found')

        if payload.action == 'semantic_search_entities':
            results = await self.world_model.semantic_search(
                payload.query or '',
                entity_type=payload.entity_type if payload.entity_type else None,
                app_context=payload.app_context if payload.app_context else None,
                limit=int(payload.limit or 10),
            )
            return MemoryOutput(success=True, items=results, message='semantic_entities_found')

        return MemoryOutput(success=False, message='unknown')
