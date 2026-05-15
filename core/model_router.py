# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Model routing by request complexity — fast/cheap models for trivial work, full models for complex tasks."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Fast tier vs full tier per provider (must match provider_registry where possible).
MODEL_POOL: Dict[str, Dict[str, str]] = {
    "anthropic": {
        "fast": "claude-haiku-4-5-20251001",
        "full": "claude-sonnet-4-6",
        "premium": "claude-opus-4-7",
    },
    "openai": {
        "fast": "gpt-4o-mini",
        "full": "gpt-4.1",
        "premium": "gpt-4.1",
    },
    "gemini": {
        "fast": "gemini-2.0-flash",
        "full": "gemini-2.5-pro",
        "premium": "gemini-2.5-pro",
    },
    "openrouter": {
        "fast": "openai/gpt-4o-mini",
        "full": "anthropic/claude-3.5-sonnet",
        "premium": "anthropic/claude-3.5-sonnet",
    },
    "groq": {
        "fast": "llama-3.1-8b-instant",
        "full": "llama-3.3-70b-versatile",
        "premium": "openai/gpt-oss-120b",
    },
    "openai_compatible": {
        "fast": "gpt-4o-mini",
        "full": "gpt-4o",
        "premium": "gpt-4o",
    },
}

FULL_TIER_MODEL_HINTS = frozenset({
    "sonnet", "opus", "gpt-4.1", "gpt-4o", "pro", "turbo", "o3", "o4",
})
FAST_TIER_MODEL_HINTS = frozenset({
    "haiku", "mini", "nano", "flash", "gpt-4o-mini", "4.1-mini", "instant", "8b",
})

TRIVIAL_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|yes|no|test|oi|olá|ola|bom dia|boa tarde|boa noite)\s*[!?.]*$",
    re.I,
)

MULTI_STEP_RES = [
    re.compile(r"(?:^|\n)\s*\d+[\.\)]\s+\S", re.M),
    re.compile(r"\b(?:first|primeiro|depois|then|em seguida|after that|and then|e depois)\b", re.I),
    re.compile(r"\bstep\s+\d+\b", re.I),
    re.compile(r"\b(?:finally|por fim|por último|lastly)\b", re.I),
]

COMPLEX_KEYWORDS = frozenset({
    "install", "uninstall", "configure", "migration", "migrate", "registry",
    "driver", "troubleshoot", "debug", "deploy", "kubernetes", "docker",
    "firewall", "permissions", "administrator", "powershell", "automation",
    "refactor", "architecture", "database", "excel", "outlook", "network",
    "printer", "selenium", "com", "uia", "sandbox", "recovery", "rollback",
    "instalar", "configurar", "depurar", "automatizar", "impressora", "rede",
})

# Read from an app (Outlook, email) and produce a file — needs full model + office/filesystem tools.
_INTEGRATION_SOURCE_RE = re.compile(
    r"\b(?:outlook|e-?mail|emails|correio)\b",
    re.I,
)
_INTEGRATION_DELIVERABLE_RE = re.compile(
    r"\b(?:arquivo|file|ficheiro|pasta|folder|download|salvar|save|coloque|guardar|"
    r"escrever|write|export|exportar|gerar|create|criar)\b",
    re.I,
)


class ComplexityLevel(str, Enum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    COMPLEX = "complex"
    MULTI_STEP = "multi_step"


@dataclass
class ComplexityClassification:
    level: ComplexityLevel
    score: float
    signals: List[str] = field(default_factory=list)
    char_count: int = 0
    sentence_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "score": round(self.score, 2),
            "signals": self.signals,
            "char_count": self.char_count,
            "sentence_count": self.sentence_count,
        }


@dataclass
class ModelRoutingDecision:
    provider: str
    requested_model: Optional[str]
    selected_model: str
    tier: str  # fast | full | premium | user_locked
    complexity: ComplexityClassification
    routing_applied: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "selected_model": self.selected_model,
            "tier": self.tier,
            "complexity": self.complexity.to_dict(),
            "routing_applied": self.routing_applied,
            "reason": self.reason,
        }


def routing_enabled() -> bool:
    return os.getenv("AGENTE_MODEL_ROUTING", "1").strip().lower() not in ("0", "false", "no", "off")


def fallback_escalation_enabled() -> bool:
    return os.getenv("AGENTE_MODEL_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")


def is_fast_tier_model(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in FAST_TIER_MODEL_HINTS)


def escalate_model(
    provider: str,
    current_model: str,
    *,
    available_models: Optional[List[str]] = None,
    target_tier: str = "full",
) -> Optional[str]:
    """Return full-tier model if current is fast-tier and a different model exists."""
    if not is_fast_tier_model(current_model):
        return None
    target = _resolve_pool_model(provider, target_tier, available_models)
    if not target or target == current_model:
        return None
    return target


def is_groq_tool_schema_validation_error(response_body: str = "") -> bool:
    """Groq rejected the model's tool call against JSON schema before returning a turn."""
    body = (response_body or "").lower()
    return "tool call validation failed" in body or "did not match schema" in body


def is_groq_failed_generation_error(response_body: str = "") -> bool:
    """Model produced a malformed tool call (Groq failed_generation / function call errors)."""
    body = (response_body or "").lower()
    return (
        "failed_generation" in body
        or "failed to call a function" in body
        or "failed to call a tool" in body
    )


def is_groq_retryable_request_error(status_code: int, response_body: str = "") -> bool:
    """Errors where a lighter Groq request (compact tools or text-only) may succeed."""
    if is_groq_tpm_limit_error(status_code, response_body):
        return True
    if is_groq_tool_schema_validation_error(response_body):
        return True
    if is_groq_failed_generation_error(response_body):
        return True
    return False


def is_groq_tpm_limit_error(status_code: int, response_body: str = "") -> bool:
    """HTTP 413/429 from Groq when the request exceeds tokens-per-minute (common on free tier)."""
    if status_code not in {413, 429}:
        return False
    body = (response_body or "").lower()
    return "tokens per minute" in body or "rate_limit" in body or "rate limit" in body


def should_escalate_after_failure(
    provider_config: Dict[str, Any],
    *,
    status_code: int = 0,
    response_body: str = "",
) -> bool:
    """True when a fast-tier call failed and we have not escalated yet."""
    if not fallback_escalation_enabled():
        return False
    if provider_config.get("escalation_used"):
        return False
    provider = str(provider_config.get("provider") or "")
    if provider == "groq" and is_groq_tpm_limit_error(status_code, response_body):
        routing = provider_config.get("model_routing") or {}
        tier = str(routing.get("tier") or "")
        if tier != "fast" and not is_fast_tier_model(provider_config.get("model", "")):
            return False
    routing = provider_config.get("model_routing") or {}
    tier = str(routing.get("tier") or "")
    if tier == "fast" or is_fast_tier_model(provider_config.get("model", "")):
        return True
    return False


def apply_model_escalation(provider_config: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate provider_config to full-tier model; returns escalation metadata."""
    provider = provider_config.get("provider", "")
    current = provider_config.get("model", "")
    available = provider_config.get("available_models")
    target = escalate_model(provider, current, available_models=available)
    if not target:
        return {"escalated": False, "reason": "no_escalation_target"}
    provider_config["model"] = target
    provider_config["escalation_used"] = True
    provider_config["escalated_from"] = current
    routing = dict(provider_config.get("model_routing") or {})
    routing["tier"] = "full"
    routing["escalated_from"] = current
    routing["selected_model"] = target
    provider_config["model_routing"] = routing
    return {
        "escalated": True,
        "from_model": current,
        "to_model": target,
        "reason": "fast_model_failed_escalate_to_full",
    }


def _sentence_count(text: str) -> int:
    parts = re.split(r"[.!?]+\s+|\n+", text.strip())
    return max(1, len([p for p in parts if p.strip()]))


def classify_request_complexity(
    text: str,
    *,
    extra_signals: Optional[List[str]] = None,
    agent_step: int = 0,
    tool_calls_in_turn: int = 0,
) -> ComplexityClassification:
    """Classify user request complexity: trivial, simple, complex, or multi_step."""
    raw = (text or "").strip()
    signals: List[str] = list(extra_signals or [])
    char_count = len(raw)
    sentence_count = _sentence_count(raw) if raw else 0
    lowered = raw.lower()

    if not raw:
        return ComplexityClassification(
            level=ComplexityLevel.SIMPLE,
            score=1.0,
            signals=["empty_prompt"],
            char_count=0,
            sentence_count=0,
        )

    if TRIVIAL_RE.match(raw):
        return ComplexityClassification(
            level=ComplexityLevel.TRIVIAL,
            score=0.0,
            signals=["greeting_or_ack"],
            char_count=char_count,
            sentence_count=sentence_count,
        )

    score = 0.0

    if char_count < 45:
        score += 0.5
        signals.append("short_prompt")
    elif char_count < 120:
        score += 1.5
    elif char_count < 280:
        score += 3.0
    else:
        score += 5.0
        signals.append("long_prompt")

    if sentence_count >= 4:
        score += 2.5
        signals.append("many_sentences")
    elif sentence_count >= 2:
        score += 1.0

    keyword_hits = [kw for kw in COMPLEX_KEYWORDS if kw in lowered]
    if keyword_hits:
        score += min(4.0, len(keyword_hits) * 1.2)
        signals.append(f"keywords:{','.join(keyword_hits[:5])}")

    if _INTEGRATION_SOURCE_RE.search(lowered):
        score += 2.5
        signals.append("outlook_integration")
    if _INTEGRATION_SOURCE_RE.search(lowered) and _INTEGRATION_DELIVERABLE_RE.search(lowered):
        score += 3.5
        signals.append("integration_deliverable")

    numbered_steps = len(re.findall(r"(?:^|\n)\s*\d+[\.\)]\s+\S", raw, re.M))
    multi_step_hits = sum(1 for pat in MULTI_STEP_RES if pat.search(raw))
    if numbered_steps >= 3:
        score += 5.0
        signals.append(f"numbered_steps:{numbered_steps}")
    elif multi_step_hits >= 2 or (multi_step_hits >= 1 and sentence_count >= 3):
        score += 4.0
        signals.append("multi_step_structure")

    if re.search(r"\b(?:all|every|each|todos|todas|cada)\b", lowered) and keyword_hits:
        score += 1.5
        signals.append("breadth_request")

    if agent_step >= 4:
        score += 1.0
        signals.append("late_agent_step")
    if tool_calls_in_turn >= 3:
        score += 2.0
        signals.append("many_parallel_tools")

    if score <= 1.0:
        level = ComplexityLevel.TRIVIAL
    elif score <= 3.5:
        level = ComplexityLevel.SIMPLE
    elif numbered_steps >= 3 or score > 6.5:
        level = ComplexityLevel.MULTI_STEP
    else:
        level = ComplexityLevel.COMPLEX

    return ComplexityClassification(
        level=level,
        score=score,
        signals=signals,
        char_count=char_count,
        sentence_count=sentence_count,
    )


def _tier_for_complexity(level: ComplexityLevel) -> str:
    if level == ComplexityLevel.TRIVIAL:
        return "fast"
    if level == ComplexityLevel.SIMPLE:
        return "fast"
    if level == ComplexityLevel.COMPLEX:
        return "full"
    return "full"


def _user_locked_tier(model: str) -> Optional[str]:
    m = model.lower()
    if any(h in m for h in FAST_TIER_MODEL_HINTS):
        return "fast"
    if any(h in m for h in FULL_TIER_MODEL_HINTS):
        return "full"
    return None


def _resolve_pool_model(provider: str, tier: str, available_models: Optional[List[str]] = None) -> str:
    pool = MODEL_POOL.get(provider, MODEL_POOL.get("openai", {}))
    candidate = pool.get(tier) or pool.get("full") or ""
    if not available_models:
        return candidate
    if candidate in available_models:
        return candidate
    for tier_name in (tier, "full", "fast", "premium"):
        m = pool.get(tier_name)
        if m and m in available_models:
            return m
    return available_models[0] if available_models else candidate


def route_model_for_request(
    provider: str,
    *,
    user_text: str,
    requested_model: Optional[str] = None,
    available_models: Optional[List[str]] = None,
    agent_step: int = 0,
    tool_calls_in_turn: int = 0,
    force_routing: bool = False,
) -> ModelRoutingDecision:
    """Pick runtime model from pool based on complexity (unless user locked a tier)."""
    provider = provider.strip().lower()
    classification = classify_request_complexity(
        user_text,
        agent_step=agent_step,
        tool_calls_in_turn=tool_calls_in_turn,
    )

    if requested_model and _user_locked_tier(requested_model):
        tier = _user_locked_tier(requested_model) or "full"
        return ModelRoutingDecision(
            provider=provider,
            requested_model=requested_model,
            selected_model=requested_model,
            tier=f"user_locked_{tier}",
            complexity=classification,
            routing_applied=False,
            reason="User-selected model tier preserved",
        )

    if not force_routing and not routing_enabled():
        model = requested_model or _resolve_pool_model(provider, "full", available_models)
        return ModelRoutingDecision(
            provider=provider,
            requested_model=requested_model,
            selected_model=model,
            tier="full",
            complexity=classification,
            routing_applied=False,
            reason="Model routing disabled (AGENTE_MODEL_ROUTING=0)",
        )

    tier = _tier_for_complexity(classification.level)
    selected = _resolve_pool_model(provider, tier, available_models)
    if requested_model and tier == "fast" and classification.level == ComplexityLevel.TRIVIAL:
        pass
    elif requested_model and not routing_enabled():
        selected = requested_model

    return ModelRoutingDecision(
        provider=provider,
        requested_model=requested_model,
        selected_model=selected,
        tier=tier,
        complexity=classification,
        routing_applied=True,
        reason=f"Routed to {tier} tier for {classification.level.value} request",
    )
