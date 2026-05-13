# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import BaseModel, Field

from agent_persistence import Persistence
from provider_registry import get_provider, list_provider_definitions, normalize_model


def _now() -> str:
    return datetime.utcnow().isoformat()


class CredentialPayload(BaseModel):
    api_key: str = Field(..., min_length=1)
    default_model: Optional[str] = None
    base_url: Optional[str] = None


class ProviderSettings(BaseModel):
    provider: str
    display_name: str
    configured: bool = False
    last4: Optional[str] = None
    updated_at: Optional[str] = None
    default_model: Optional[str] = None
    base_url: Optional[str] = None
    requires_base_url: bool = False
    models: List[str] = Field(default_factory=list)


class CredentialStore:
    SERVICE_NAME = "PHYLUM.providers"

    def __init__(self, persistence: Optional[Persistence] = None):
        self.persistence = persistence or Persistence.get()

    async def list_provider_settings(self) -> List[Dict[str, Any]]:
        settings: List[Dict[str, Any]] = []
        for definition in list_provider_definitions():
            metadata = await self._get_metadata(definition.provider)
            settings.append(
                ProviderSettings(
                    provider=definition.provider,
                    display_name=definition.display_name,
                    configured=bool(metadata),
                    last4=metadata.get("last4") if metadata else None,
                    updated_at=metadata.get("updated_at") if metadata else None,
                    default_model=normalize_model(definition.provider, (metadata or {}).get("default_model") or definition.default_model),
                    base_url=(metadata or {}).get("base_url") or definition.base_url,
                    requires_base_url=definition.requires_base_url,
                    models=definition.models,
                ).dict()
            )
        return settings

    async def save_credential(self, provider: str, payload: CredentialPayload) -> Dict[str, Any]:
        definition = get_provider(provider)
        if definition.requires_base_url and not payload.base_url:
            raise ValueError("base_url is required for this provider")
        secret = payload.api_key.strip()
        await asyncio.to_thread(self._set_password, definition.provider, secret)
        metadata = {
            "provider": definition.provider,
            "last4": secret[-4:] if len(secret) >= 4 else secret,
            "updated_at": _now(),
            "default_model": normalize_model(definition.provider, payload.default_model or definition.default_model),
            "base_url": payload.base_url or definition.base_url,
        }
        await self.persistence.save_kv(self._metadata_key(definition.provider), metadata)
        return await self.get_provider_settings(definition.provider)

    async def delete_credential(self, provider: str) -> None:
        definition = get_provider(provider)
        await asyncio.to_thread(self._delete_password, definition.provider)
        await self.persistence.delete_kv(self._metadata_key(definition.provider))

    async def get_provider_settings(self, provider: str) -> Dict[str, Any]:
        provider = get_provider(provider).provider
        settings = await self.list_provider_settings()
        for item in settings:
            if item["provider"] == provider:
                return item
        raise ValueError(f"unsupported provider: {provider}")

    async def resolve_runtime_config(self, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        definition = get_provider(provider)
        secret = await asyncio.to_thread(keyring.get_password, self.SERVICE_NAME, definition.provider)
        if not secret:
            raise ValueError(f"provider '{definition.provider}' is not configured")
        metadata = await self._get_metadata(definition.provider)
        resolved_model = normalize_model(
            definition.provider,
            model or (metadata or {}).get("default_model") or definition.default_model,
        )
        if not resolved_model:
            raise ValueError(f"provider '{definition.provider}' requires a model")
        return {
            "provider": definition.provider,
            "api_key": secret,
            "model": resolved_model,
            "base_url": (metadata or {}).get("base_url") or definition.base_url,
        }

    async def is_configured(self, provider: str) -> bool:
        metadata = await self._get_metadata(provider)
        return bool(metadata)

    async def _get_metadata(self, provider: str) -> Optional[Dict[str, Any]]:
        return await self.persistence.get_kv(self._metadata_key(provider))

    def _metadata_key(self, provider: str) -> str:
        return f"provider_settings:{provider}"

    def _set_password(self, provider: str, secret: str) -> None:
        try:
            keyring.set_password(self.SERVICE_NAME, provider, secret)
        except KeyringError as exc:
            raise RuntimeError("unable to store credential securely") from exc

    def _delete_password(self, provider: str) -> None:
        try:
            keyring.delete_password(self.SERVICE_NAME, provider)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise RuntimeError("unable to delete credential securely") from exc
