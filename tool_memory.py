import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from tool_base import BaseTool
from agent_persistence import Persistence

logger = logging.getLogger(__name__)


class MemoryInput(BaseModel):
    action: str = Field(..., pattern='^(set|get|delete|list|upsert_entity|query_entities|record_observation)$')
    key: Optional[str] = None
    value: Optional[Dict[str, Any]] = None
    entity_type: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    query: Optional[str] = None


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

    async def validate(self, payload: MemoryInput) -> None:
        if payload.action in ('set', 'get', 'delete') and not payload.key:
            raise ValueError('key is required')
        if payload.action in ('upsert_entity', 'record_observation') and (not payload.entity_type or not payload.key and payload.action == 'upsert_entity'):
            raise ValueError('entity_type and key are required for upsert_entity; entity_type is required for record_observation')
        if payload.action == 'query_entities' and not payload.entity_type:
            raise ValueError('entity_type is required for query_entities')

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
        return MemoryOutput(success=False, message='unknown')
