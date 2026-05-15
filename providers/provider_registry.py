# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ProviderDefinition(BaseModel):
    provider: str
    display_name: str
    models: List[str] = Field(default_factory=list)
    default_model: Optional[str] = None
    base_url: Optional[str] = None
    requires_base_url: bool = False


PROVIDERS: Dict[str, ProviderDefinition] = {
    "openai": ProviderDefinition(
        provider="openai",
        display_name="OpenAI",
        models=["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"],
        default_model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
    ),
    "gemini": ProviderDefinition(
        provider="gemini",
        display_name="Google Gemini",
        models=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        default_model="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    ),
    "anthropic": ProviderDefinition(
        provider="anthropic",
        display_name="Anthropic",
        models=["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
        default_model="claude-sonnet-4-6",
        base_url="https://api.anthropic.com/v1",
    ),
    "openrouter": ProviderDefinition(
        provider="openrouter",
        display_name="OpenRouter",
        models=["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash-001"],
        default_model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
    ),
    "groq": ProviderDefinition(
        provider="groq",
        display_name="Groq",
        models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
        default_model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
    ),
    "openai_compatible": ProviderDefinition(
        provider="openai_compatible",
        display_name="OpenAI Compatible",
        models=[],
        default_model=None,
        base_url=None,
        requires_base_url=True,
    ),
}


MODEL_ALIASES: Dict[str, Dict[str, str]] = {
    "anthropic": {
        "claude": "claude-sonnet-4-6",
        "claude sonnet": "claude-sonnet-4-6",
        "claude-sonnet": "claude-sonnet-4-6",
        "claude opus": "claude-opus-4-7",
        "claude-opus": "claude-opus-4-7",
        "claude haiku": "claude-haiku-4-5-20251001",
        "claude-haiku": "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-latest": "claude-sonnet-4-6",
        "claude-3-7-sonnet-latest": "claude-sonnet-4-6",
    }
}


def get_provider(provider: str) -> ProviderDefinition:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    return PROVIDERS[normalized]


def list_provider_definitions() -> List[ProviderDefinition]:
    return list(PROVIDERS.values())


def normalize_model(provider: str, model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    normalized_model = model.strip()
    if not normalized_model:
        return None
    provider_id = get_provider(provider).provider
    alias_map = MODEL_ALIASES.get(provider_id, {})
    return alias_map.get(normalized_model.lower(), normalized_model)
