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
    "anthropic": ProviderDefinition(
        provider="anthropic",
        display_name="Anthropic",
        models=["claude-3-5-sonnet-latest", "claude-3-7-sonnet-latest"],
        default_model="claude-3-5-sonnet-latest",
        base_url="https://api.anthropic.com/v1",
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


def get_provider(provider: str) -> ProviderDefinition:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    return PROVIDERS[normalized]


def list_provider_definitions() -> List[ProviderDefinition]:
    return list(PROVIDERS.values())
