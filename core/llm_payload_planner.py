# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Unified LLM tool payload planning (Fase 2.1–2.2).

Single entry point for all providers: given the full tool catalog, user text,
complexity, and provider id, returns a :class:`ToolPayloadPlan` with the tool
subset and disclosure metadata. Tool ranking and domain affinity live here;
``tool_selector`` re-exports for backward compatibility. Provider-specific
limits are configuration lookups only — no ``if provider == "groq"`` branches.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Union

from intent_classifier import IntentClassification, classify_user_intent
from model_router import ComplexityClassification, ComplexityLevel, classify_request_complexity

logger = logging.getLogger(__name__)

ComplexityInput = Union[
    ComplexityClassification,
    ComplexityLevel,
    str,
    Dict[str, Any],
    None,
]


class DisclosureLevel(str, Enum):
    """How many tools/schemas are exposed to the LLM on this turn."""

    MINIMAL = "minimal"
    FOCUSED = "focused"
    STANDARD = "standard"
    FULL = "full"


# (min_tools, max_tools) per disclosure level (Fase 2.3).
_STATIC_DISCLOSURE_BOUNDS: Dict[DisclosureLevel, tuple[int, int]] = {
    DisclosureLevel.MINIMAL: (1, 3),
    DisclosureLevel.FOCUSED: (4, 8),
    DisclosureLevel.FULL: (0, 999),
}

MAX_DISCLOSURE_EXPANSION_STEP = 2

# Upper bound on tools offered per provider (config, not branching logic).
_DEFAULT_PROVIDER_TOOL_CAPS: Dict[str, int] = {
    "groq": 14,
    "anthropic": 18,
    "openai": 18,
    "gemini": 18,
    "google": 18,
}

# --- Tool ranking (formerly tool_selector.py) ---------------------------------

_CORE_TOOL_NAMES = frozenset({
    "shell",
    "desktop",
    "memory",
    "windows_ui",
    "skill",
    "request_user_input",
})

_TOOL_AFFINITY: Dict[str, tuple[str, ...]] = {
    "office": ("filesystem", "sandbox"),
    "filesystem": ("office", "shell"),
    "driver_manager": ("shell", "desktop"),
    "browser": ("web", "desktop"),
}

_ACTION_HINTS = re.compile(
    r"\b("
    r"install|uninstall|configure|open|close|run|execute|create|delete|remove|copy|move|"
    r"find|search|list|read|write|download|upload|print|driver|excel|word|outlook|"
    r"powershell|cmd|script|automate|fix|debug|troubleshoot|deploy|"
    r"instalar|configurar|abrir|fechar|executar|criar|apagar|copiar|mover|"
    r"buscar|procurar|listar|ler|escrever|baixar|imprimir|automatizar|corrigir|coloque|guardar"
    r")\b",
    re.I,
)

_OUTLOOK_CONTEXT_RE = re.compile(r"\b(?:outlook|e-?mail|emails|correio)\b", re.I)
_FILE_OUTPUT_RE = re.compile(
    r"\b(?:arquivo|file|ficheiro|pasta|folder|download|salvar|save|coloque|guardar|escrever|write)\b",
    re.I,
)

_TOKEN_RE = re.compile(r"[a-z0-9_./\\-]+", re.I)


def _tool_name(tool: Dict[str, Any]) -> str:
    return str((tool.get("function") or {}).get("name") or "")


def _tokens(text: str) -> Set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1}


def _is_outlook_mail_task(user_text: str) -> bool:
    return bool(_OUTLOOK_CONTEXT_RE.search(user_text or ""))


def _score_tool(
    tool: Dict[str, Any],
    user_tokens: Set[str],
    *,
    action_intent: bool,
    outlook_task: bool,
) -> float:
    fn = tool.get("function") or {}
    name = str(fn.get("name") or "")
    desc = str(fn.get("description") or "").lower()
    score = 0.0
    if outlook_task and name == "shell":
        score -= 8.0
    if name in _CORE_TOOL_NAMES and not (outlook_task and name == "shell"):
        score += 5.0
    name_parts = set(name.lower().replace("_", " ").split())
    score += len(user_tokens & name_parts) * 4.0
    desc_tokens = _tokens(desc)
    score += len(user_tokens & desc_tokens) * 1.5
    if action_intent:
        score += 1.0
    if name == "office" and _OUTLOOK_CONTEXT_RE.search(" ".join(user_tokens)):
        score += 6.0
    if name == "filesystem" and _FILE_OUTPUT_RE.search(" ".join(user_tokens)):
        score += 4.0
    return score


def _apply_affinity(
    selected: List[Dict[str, Any]],
    catalog: List[Dict[str, Any]],
    seen: Set[str],
    *,
    outlook_task: bool = False,
) -> None:
    by_name = {
        _tool_name(t): t
        for t in catalog
        if _tool_name(t)
    }
    for tool in list(selected):
        name = _tool_name(tool)
        partners = _TOOL_AFFINITY.get(name, ())
        if outlook_task and name == "filesystem":
            partners = ("office",)
        for partner in partners:
            if outlook_task and partner == "shell":
                continue
            if partner not in seen and partner in by_name:
                seen.add(partner)
                selected.append(by_name[partner])


def select_tools_for_request(
    catalog: List[Dict[str, Any]],
    user_text: str,
    *,
    max_tools: int = 14,
    full_catalog_threshold: int = 18,
) -> List[Dict[str, Any]]:
    """
    Rank and return the most relevant tools for a user message.

    When the catalog is small, returns it unchanged. Otherwise scores by token
    overlap, core tools, and domain affinity (no per-greeting hardcoding).
    """
    if len(catalog) <= full_catalog_threshold:
        return list(catalog)

    user_tokens = _tokens(user_text)
    action_intent = bool(_ACTION_HINTS.search(user_text or ""))
    outlook_task = _is_outlook_mail_task(user_text)

    ranked: List[tuple[float, Dict[str, Any]]] = []
    for tool in catalog:
        ranked.append(
            (
                _score_tool(
                    tool,
                    user_tokens,
                    action_intent=action_intent,
                    outlook_task=outlook_task,
                ),
                tool,
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)

    selected: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(tool: Dict[str, Any]) -> None:
        name = _tool_name(tool)
        if outlook_task and name == "shell":
            return
        if name and name not in seen:
            seen.add(name)
            selected.append(tool)

    if outlook_task:
        for tool in catalog:
            if _tool_name(tool) == "office":
                add(tool)

    for tool in catalog:
        name = _tool_name(tool)
        if name in _CORE_TOOL_NAMES and not (outlook_task and name == "shell"):
            add(tool)

    limit = max(len(_CORE_TOOL_NAMES), min(max_tools, len(catalog)))
    for score, tool in ranked:
        if len(selected) >= limit:
            break
        if score <= 0 and not action_intent and len(selected) >= len(_CORE_TOOL_NAMES):
            continue
        add(tool)

    _apply_affinity(selected, catalog, seen, outlook_task=outlook_task)

    return selected if selected else list(catalog)


@dataclass(frozen=True)
class ToolPayloadPlan:
    """Planned tools and metadata for one LLM turn."""

    tools: tuple[Dict[str, Any], ...]
    tool_names: tuple[str, ...]
    disclosure_level: DisclosureLevel
    expansion_step: int
    provider: str
    complexity_level: str
    max_tools: int
    catalog_size: int
    intent_profile_id: Optional[str] = None
    intent_accepted: bool = False
    reason: str = ""
    canonical_catalog_fingerprint: str = ""
    llm_schema_mirror: bool = False

    @property
    def tools_list(self) -> List[Dict[str, Any]]:
        return list(self.tools)

    def estimate_tools_json_chars(self) -> int:
        return len(json.dumps(self.tools_list, ensure_ascii=False))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "disclosure_level": self.disclosure_level.value,
            "expansion_step": self.expansion_step,
            "provider": self.provider,
            "complexity_level": self.complexity_level,
            "max_tools": self.max_tools,
            "catalog_size": self.catalog_size,
            "tools_offered": len(self.tools),
            "tool_names": list(self.tool_names),
            "tools_json_chars": self.estimate_tools_json_chars(),
            "intent_profile_id": self.intent_profile_id,
            "intent_accepted": self.intent_accepted,
            "reason": self.reason,
            "canonical_catalog_fingerprint": self.canonical_catalog_fingerprint,
            "llm_schema_mirror": self.llm_schema_mirror,
        }


def standard_disclosure_max() -> int:
    """Configurable cap for ``standard`` level (``AGENTE_DISCLOSURE_STANDARD_MAX``)."""
    try:
        return max(6, int(os.getenv("AGENTE_DISCLOSURE_STANDARD_MAX", "14")))
    except ValueError:
        return 14


def disclosure_tool_bounds(level: DisclosureLevel) -> tuple[int, int]:
    """Return ``(min_tools, max_tools)`` for a disclosure level."""
    if level == DisclosureLevel.STANDARD:
        cap = standard_disclosure_max()
        return (4, cap)
    return _STATIC_DISCLOSURE_BOUNDS[level]


def payload_full_disclosure_enabled() -> bool:
    """Force full catalog (debug): ``AGENTE_PAYLOAD_FULL=1``."""
    return os.getenv("AGENTE_PAYLOAD_FULL", "0").strip().lower() in ("1", "true", "yes", "on")


def provider_tool_caps() -> Dict[str, int]:
    """Optional override via ``AGENTE_PROVIDER_TOOL_CAPS`` JSON object."""
    caps = dict(_DEFAULT_PROVIDER_TOOL_CAPS)
    raw = os.getenv("AGENTE_PROVIDER_TOOL_CAPS", "").strip()
    if not raw:
        return caps
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                caps[str(key).lower()] = int(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return caps


def _normalize_complexity(
    complexity: ComplexityInput,
    user_text: str,
) -> ComplexityClassification:
    if isinstance(complexity, ComplexityClassification):
        return complexity
    if isinstance(complexity, ComplexityLevel):
        return ComplexityClassification(level=complexity, score=0.0)
    if isinstance(complexity, dict):
        level_raw = complexity.get("level") or complexity.get("complexity_level")
        if level_raw:
            try:
                level = ComplexityLevel(str(level_raw))
            except ValueError:
                level = ComplexityLevel.SIMPLE
            return ComplexityClassification(
                level=level,
                score=float(complexity.get("score") or 0.0),
                signals=list(complexity.get("signals") or []),
                char_count=int(complexity.get("char_count") or 0),
                sentence_count=int(complexity.get("sentence_count") or 0),
            )
    if isinstance(complexity, str):
        try:
            return ComplexityClassification(level=ComplexityLevel(complexity), score=0.0)
        except ValueError:
            pass
    return classify_request_complexity(user_text)


def resolve_disclosure_level(
    complexity: ComplexityClassification,
    *,
    expansion_step: int = 0,
    force_full: bool = False,
    override: Optional[DisclosureLevel] = None,
) -> DisclosureLevel:
    """
    Map complexity + expansion step to disclosure level.

    Turn 0 (expansion_step=0): trivial → minimal; all actionable requests → focused (2.4).
    Expansion 1 → standard; expansion 2+ → full.
    """
    if override is not None:
        return override
    if force_full or payload_full_disclosure_enabled():
        return DisclosureLevel.FULL
    if expansion_step >= 2:
        return DisclosureLevel.FULL
    if expansion_step == 1:
        return DisclosureLevel.STANDARD
    if complexity.level == ComplexityLevel.TRIVIAL:
        return DisclosureLevel.MINIMAL
    return DisclosureLevel.FOCUSED


def disclosure_level_for_expansion_step(expansion_step: int) -> DisclosureLevel:
    if expansion_step >= 2:
        return DisclosureLevel.FULL
    if expansion_step == 1:
        return DisclosureLevel.STANDARD
    return DisclosureLevel.FOCUSED


def can_expand_disclosure(expansion_step: int) -> bool:
    return expansion_step < MAX_DISCLOSURE_EXPANSION_STEP


def next_disclosure_expansion(
    current_step: int,
    *,
    reason: str,
) -> tuple[int, DisclosureLevel, bool]:
    """
    Advance expansion step after tool/schema/execution failure.

    Returns ``(new_step, new_level, did_expand)``.
    """
    if not can_expand_disclosure(current_step):
        return current_step, disclosure_level_for_expansion_step(current_step), False
    new_step = current_step + 1
    return new_step, disclosure_level_for_expansion_step(new_step), True


def max_tools_for_disclosure(
    disclosure: DisclosureLevel,
    *,
    provider: str,
    provider_caps: Optional[Mapping[str, int]] = None,
) -> int:
    _, disclosure_max = disclosure_tool_bounds(disclosure)
    caps = provider_caps or provider_tool_caps()
    provider_max = int(caps.get((provider or "").lower(), caps.get(provider, 18)))
    if disclosure == DisclosureLevel.FULL:
        return provider_max if provider_max < 999 else 999
    return min(disclosure_max, provider_max)


def _ensure_tools_present(
    selected: List[Dict[str, Any]],
    catalog: List[Dict[str, Any]],
    required_names: Sequence[str],
) -> List[Dict[str, Any]]:
    if not required_names:
        return selected
    by_name = {_tool_name(t): t for t in catalog if _tool_name(t)}
    seen = {_tool_name(t) for t in selected}
    out = list(selected)
    for name in required_names:
        if name and name not in seen and name in by_name:
            seen.add(name)
            out.append(by_name[name])
    return out


def plan_llm_payload(
    catalog: List[Dict[str, Any]],
    user_text: str,
    complexity: ComplexityInput = None,
    provider: str = "",
    *,
    expansion_step: int = 0,
    disclosure_level: Optional[DisclosureLevel] = None,
    force_full: bool = False,
    intent: Optional[IntentClassification] = None,
    max_tools_override: Optional[int] = None,
) -> ToolPayloadPlan:
    """
    Plan which tools to send to the LLM for one turn.

    Parameters
    ----------
    catalog:
        Full OpenAI-style tool definitions (e.g. from ``agentic_tool_definitions()``).
    user_text:
        Current user message.
    complexity:
        Pre-computed complexity or None to classify from ``user_text``.
    provider:
        Provider id (``groq``, ``anthropic``, ``openai``, …) — used only for cap lookup.
    expansion_step:
        0 = first turn; higher values widen disclosure (Fase 2.4).
    disclosure_level:
        Optional override of automatic disclosure resolution.
    force_full:
        Send entire catalog (debug / post-expansion).
    intent:
        Optional pre-computed intent classification; classified when omitted.
    """
    text = user_text or ""
    provider_id = (provider or "unknown").lower()
    catalog_fingerprint = ""
    try:
        from tool_schema_optimizer import (
            assert_canonical_catalog_unchanged,
            build_llm_tool_mirror,
            canonical_catalog_fingerprint,
            detach_tool_definitions,
            schema_optimizer_enabled,
        )

        catalog_fingerprint = canonical_catalog_fingerprint(catalog)
    except Exception:
        detach_tool_definitions = None  # type: ignore[assignment,misc]
        build_llm_tool_mirror = None  # type: ignore[assignment,misc]
        assert_canonical_catalog_unchanged = None  # type: ignore[assignment,misc]
        schema_optimizer_enabled = lambda: False  # type: ignore[assignment,misc]

    classification = _normalize_complexity(complexity, text)
    intent_result = intent if intent is not None else classify_user_intent(text)
    force_full = force_full or payload_full_disclosure_enabled()

    disclosure = resolve_disclosure_level(
        classification,
        expansion_step=expansion_step,
        force_full=force_full,
        override=disclosure_level,
    )
    max_tools = max_tools_for_disclosure(disclosure, provider=provider_id)
    if max_tools_override is not None:
        max_tools = max(1, int(max_tools_override))

    if disclosure == DisclosureLevel.FULL or len(catalog) <= max_tools:
        selected = list(catalog)
        reason = "full_catalog" if disclosure == DisclosureLevel.FULL else "catalog_within_cap"
    else:
        min_tools, _ = disclosure_tool_bounds(disclosure)
        effective_max = max(max_tools, min_tools) if min_tools > 0 else max_tools
        selected = select_tools_for_request(
            catalog,
            text,
            max_tools=effective_max,
            full_catalog_threshold=max_tools + 1,
        )
        reason = f"ranked_subset_{disclosure.value}"

    required_tools: List[str] = []
    if intent_result.accepted and intent_result.profile is not None:
        required_tools = list(intent_result.profile.required_tools)
        selected = _ensure_tools_present(selected, catalog, required_tools)
        reason = f"{reason}+intent_profile"

    if len(selected) > max_tools and disclosure != DisclosureLevel.FULL:
        required_set = set(required_tools)
        required_part = [t for t in selected if _tool_name(t) in required_set]
        rest = [t for t in selected if _tool_name(t) not in required_set]
        selected = required_part + rest[: max(0, max_tools - len(required_part))]

    tool_names = tuple(_tool_name(t) for t in selected if _tool_name(t))

    llm_schema_mirror = False
    if detach_tool_definitions is not None:
        selected = detach_tool_definitions(selected)

    if (
        intent_result.accepted
        and intent_result.profile is not None
        and schema_optimizer_enabled()
        and build_llm_tool_mirror is not None
    ):
        try:
            from tool_schema_optimizer import build_optimize_context

            schema_ctx = build_optimize_context(
                profile=intent_result.profile,
                domain=intent_result.profile.domain,
                disclosure_level=disclosure,
            )
            selected = build_llm_tool_mirror(selected, schema_ctx)
            llm_schema_mirror = True
            reason = f"{reason}+schema_mirror"
        except Exception as exc:
            logger.warning("tool_schema_optimizer skipped: %s", exc)

    if catalog_fingerprint and assert_canonical_catalog_unchanged is not None:
        assert_canonical_catalog_unchanged(catalog, fingerprint=catalog_fingerprint)

    return ToolPayloadPlan(
        tools=tuple(selected),
        tool_names=tool_names,
        disclosure_level=disclosure,
        expansion_step=expansion_step,
        provider=provider_id,
        complexity_level=classification.level.value,
        max_tools=max_tools,
        catalog_size=len(catalog),
        intent_profile_id=intent_result.profile_id,
        intent_accepted=intent_result.accepted,
        reason=reason,
        canonical_catalog_fingerprint=catalog_fingerprint,
        llm_schema_mirror=llm_schema_mirror,
    )
