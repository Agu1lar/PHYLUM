# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Prune canonical tool JSON schemas for LLM payloads (Fase 3.1–3.4).

**Schema espelho canônico (Fase 3.3):** this module only produces *LLM mirror* copies.
The cached catalog from ``canonical_tools`` / ``agentic_tool_definitions()`` is never
modified. Execution and ``prevalidate_tool_call`` validate against registry
:class:`~pydantic.BaseModel` input types (full canonical surface), not the mirror.

Fase 3.2 pruning rules (always on the LLM mirror copy):

1. ``action.enum`` — only values in the resolved allowlist (profile action or domain variant).
2. ``parameters.properties`` — drop keys not used by the profile (or variant when no profile).
3. ``description`` — truncated per :class:`DisclosureLevel`; ``minimal`` keeps the first line only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from intent_profile_registry import IntentProfile
from llm_payload_planner import DisclosureLevel

logger = logging.getLogger(__name__)


class CanonicalCatalogMutationError(RuntimeError):
    """Raised when the canonical tool catalog was mutated in place."""


VALIDATION_SCHEMA_SOURCE = "canonical_input_model"

_TABLES_PATH = Path(__file__).resolve().parent / "schema_domain_tables.json"
_DEFAULT_TABLES: Optional[Dict[str, Any]] = None

# Fase 3.2 — description caps (0 = no truncation). Override via env JSON if needed.
_DESCRIPTION_CHAR_LIMITS: Dict[DisclosureLevel, int] = {
    DisclosureLevel.MINIMAL: 120,
    DisclosureLevel.FOCUSED: 280,
    DisclosureLevel.STANDARD: 480,
    DisclosureLevel.FULL: 0,
}

_PROPERTY_DESC_LIMITS: Dict[DisclosureLevel, int] = {
    DisclosureLevel.MINIMAL: 60,
    DisclosureLevel.FOCUSED: 120,
    DisclosureLevel.STANDARD: 200,
    DisclosureLevel.FULL: 0,
}


@dataclass(frozen=True)
class SchemaOptimizeContext:
    """Context for pruning one tool schema."""

    domain: Optional[str] = None
    profile: Optional[IntentProfile] = None
    disclosure_level: DisclosureLevel = DisclosureLevel.FOCUSED
    restrict_to_profile_action: bool = True

    @property
    def profile_id(self) -> Optional[str]:
        return self.profile.id if self.profile else None

    @property
    def effective_domain(self) -> Optional[str]:
        if self.profile is not None:
            return self.profile.domain
        return self.domain


@dataclass(frozen=True)
class OptimizedToolSchema:
    """Result of optimizing a single tool schema."""

    schema: Dict[str, Any]
    tool_name: str
    optimized: bool
    variant_id: Optional[str] = None
    allowed_actions: Tuple[str, ...] = ()
    allowed_properties: Tuple[str, ...] = ()
    actions_pruned: bool = False
    properties_pruned: bool = False
    descriptions_truncated: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "optimized": self.optimized,
            "variant_id": self.variant_id,
            "allowed_actions": list(self.allowed_actions),
            "allowed_properties": list(self.allowed_properties),
            "actions_pruned": self.actions_pruned,
            "properties_pruned": self.properties_pruned,
            "descriptions_truncated": self.descriptions_truncated,
            "reason": self.reason,
        }


def canonical_catalog_fingerprint(catalog: Sequence[Mapping[str, Any]]) -> str:
    """Stable hash of the canonical tool catalog (detect in-place mutation)."""
    payload = json.dumps(list(catalog), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_canonical_catalog_unchanged(
    catalog: Sequence[Mapping[str, Any]],
    *,
    fingerprint: str,
) -> None:
    """Raise :class:`CanonicalCatalogMutationError` if *catalog* changed since fingerprint."""
    if canonical_catalog_fingerprint(catalog) != fingerprint:
        raise CanonicalCatalogMutationError(
            "Canonical tool catalog was mutated; only detached LLM mirror copies may change."
        )


def detach_tool_definitions(tools: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deep-copy tool schemas so planner/optimizer never alias the canonical catalog."""
    return [copy.deepcopy(tool) for tool in tools]


def build_llm_tool_mirror(
    canonical_tools: Sequence[Dict[str, Any]],
    context: SchemaOptimizeContext,
    *,
    domain_tables: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Build pruned schemas for the LLM only (Fase 3.3).

    *canonical_tools* may be references into the cached catalog; they are not modified.
    """
    return optimize_tools_for_llm(
        detach_tool_definitions(canonical_tools),
        context,
        domain_tables=domain_tables,
    )


def schema_optimizer_enabled() -> bool:
    """``AGENTE_SCHEMA_OPTIMIZER=1`` enables pruning in the payload planner."""
    return os.getenv("AGENTE_SCHEMA_OPTIMIZER", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def schema_domain_tables_path() -> Path:
    custom = os.getenv("AGENTE_SCHEMA_DOMAIN_TABLES", "").strip()
    if custom:
        return Path(custom)
    return _TABLES_PATH


def description_char_limits() -> Dict[DisclosureLevel, int]:
    """Per-level char caps for tool ``function.description`` (Fase 3.2)."""
    return dict(_DESCRIPTION_CHAR_LIMITS)


def property_description_char_limits() -> Dict[DisclosureLevel, int]:
    """Per-level char caps for parameter property descriptions."""
    return dict(_PROPERTY_DESC_LIMITS)


def load_domain_tables(*, path: Optional[Path] = None) -> Dict[str, Any]:
    """Load merged domain tables (bundled + user overlay) as legacy dict shape."""
    global _DEFAULT_TABLES
    if path is not None:
        from schema_domain_registry import SchemaDomainRegistry, normalize_tables_dict

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return SchemaDomainRegistry.from_raw(normalize_tables_dict(raw)).to_tables_dict()
    if _DEFAULT_TABLES is not None:
        return _DEFAULT_TABLES
    from schema_domain_registry import load_schema_domain_registry

    _DEFAULT_TABLES = load_schema_domain_registry().to_tables_dict()
    return _DEFAULT_TABLES


def reload_domain_tables() -> Dict[str, Any]:
    """Clear cache and reload tables (tests)."""
    global _DEFAULT_TABLES
    _DEFAULT_TABLES = None
    from schema_domain_registry import reload_schema_domain_registry

    reload_schema_domain_registry()
    return load_domain_tables()


def build_optimize_context(
    *,
    domain: Optional[str] = None,
    profile: Optional[IntentProfile] = None,
    disclosure_level: DisclosureLevel = DisclosureLevel.FOCUSED,
    restrict_to_profile_action: bool = True,
) -> SchemaOptimizeContext:
    """Build context from an :class:`IntentProfile` and/or detected domain."""
    return SchemaOptimizeContext(
        domain=domain,
        profile=profile,
        disclosure_level=disclosure_level,
        restrict_to_profile_action=restrict_to_profile_action,
    )


def _tool_name(tool: Mapping[str, Any]) -> str:
    return str((tool.get("function") or {}).get("name") or "")


def _first_line(text: str) -> str:
    """Return the first line only (Fase 3.2: ``minimal`` = one line)."""
    return (text.split("\n")[0] or "").strip()


def truncate_description(text: str, level: DisclosureLevel, *, for_property: bool = False) -> str:
    """
    Truncate a description string for the given disclosure level.

    ``minimal`` always keeps a single line; longer levels allow more characters.
    """
    if not text or level == DisclosureLevel.FULL:
        return text
    line = _first_line(text) if level == DisclosureLevel.MINIMAL else text.strip()
    cap = (
        property_description_char_limits().get(level, 0)
        if for_property
        else description_char_limits().get(level, 0)
    )
    if cap <= 0:
        return line if level == DisclosureLevel.MINIMAL else text.strip()
    if len(line) <= cap:
        return line
    return line[: cap - 3].rstrip() + "..."


def profile_property_allowlist(profile: IntentProfile, tool_name: str) -> List[str]:
    """
    Property names allowed for *tool_name* from the intent profile (Fase 3.2).

    Only keys present in ``param_defaults`` and ``default_action.params`` when the
    profile's default tool matches *tool_name*.
    """
    if profile.default_action.tool != tool_name:
        return []
    names: List[str] = ["action"]
    for key in profile.param_defaults:
        if key and key not in names:
            names.append(str(key))
    for key in profile.default_action.params:
        if key and key not in names:
            names.append(str(key))
    return names


def prune_action_enum(
    action_spec: Dict[str, Any],
    allowed_actions: Sequence[str],
) -> Tuple[Dict[str, Any], bool]:
    """
    Restrict ``action.enum`` to *allowed_actions* only (Fase 3.2).

    Returns ``(new_spec, pruned)``. If *allowed_actions* is empty, *action_spec* is unchanged.
    """
    if not allowed_actions:
        return action_spec, False
    spec = copy.deepcopy(action_spec)
    allowed = list(allowed_actions)
    enum = spec.get("enum")
    if isinstance(enum, list):
        filtered = [value for value in enum if value in allowed]
        spec["enum"] = filtered if filtered else allowed
    else:
        spec["enum"] = allowed
    pruned = spec.get("enum") != enum if isinstance(enum, list) else True
    return spec, pruned


def prune_parameter_properties(
    parameters: Dict[str, Any],
    allowed_properties: Sequence[str],
    *,
    allowed_actions: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Any], bool, bool]:
    """
    Drop parameter properties not in *allowed_properties*; optionally prune ``action.enum``.

    Returns ``(parameters, properties_pruned, actions_pruned)``.
    """
    props = parameters.get("properties")
    if not isinstance(props, dict):
        return parameters, False, False

    allow_set = set(allowed_properties)
    if "action" in props:
        allow_set.add("action")

    new_props: Dict[str, Any] = {}
    properties_pruned = False
    for key, spec in props.items():
        if key in allow_set:
            new_props[key] = copy.deepcopy(spec)
        else:
            properties_pruned = True

    actions_pruned = False
    if allowed_actions and "action" in new_props:
        pruned_spec, actions_pruned = prune_action_enum(new_props["action"], allowed_actions)
        new_props["action"] = pruned_spec

    out = copy.deepcopy(parameters)
    out["properties"] = new_props
    required = out.get("required")
    if isinstance(required, list):
        out["required"] = [name for name in required if name in new_props]

    return out, properties_pruned, actions_pruned


def _truncate_descriptions_in_schema(
    schema: Dict[str, Any],
    *,
    level: DisclosureLevel,
) -> bool:
    """Truncate function and property descriptions in-place; return whether anything changed."""
    if level == DisclosureLevel.FULL:
        return False

    changed = False
    fn = schema.get("function")
    if not isinstance(fn, dict):
        return False

    desc = fn.get("description")
    if isinstance(desc, str):
        truncated = truncate_description(desc, level, for_property=False)
        if truncated != desc:
            fn["description"] = truncated
            changed = True

    params = fn.get("parameters")
    if not isinstance(params, dict):
        return changed

    props = params.get("properties")
    if not isinstance(props, dict):
        return changed

    for spec in props.values():
        if not isinstance(spec, dict):
            continue
        prop_desc = spec.get("description")
        if isinstance(prop_desc, str):
            truncated = truncate_description(prop_desc, level, for_property=True)
            if truncated != prop_desc:
                spec["description"] = truncated
                changed = True

    return changed


def _resolve_variant(
    tool_name: str,
    *,
    context: SchemaOptimizeContext,
    tables: Mapping[str, Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Resolve variant via :mod:`schema_domain_registry` (Fase 3.4 tables)."""
    profile = context.profile
    if profile is not None and profile.default_action.tool == tool_name:
        action = profile.default_action.action
        if context.restrict_to_profile_action and action:
            return f"profile:{action}", {
                "actions": [action],
                "properties": profile_property_allowlist(profile, tool_name),
            }

    try:
        from schema_domain_registry import load_schema_domain_registry

        registry = load_schema_domain_registry()
        domain = context.effective_domain or context.domain
        action = profile.default_action.action if profile and profile.default_action.tool == tool_name else None
        schema_variant = profile.schema_variant if profile else None
        tool_variants = profile.tool_variants if profile else None
        variant_id, variant = registry.resolve_variant(
            tool_name,
            domain=domain,
            action=action,
            schema_variant=schema_variant or None,
            tool_variants=tool_variants,
        )
        if variant_id and variant:
            if profile is not None and profile.default_action.tool == tool_name:
                variant = dict(variant)
                variant["properties"] = _merge_variant_properties(
                    variant,
                    profile,
                    tool_name,
                )
            return variant_id, variant
    except Exception as exc:
        logger.debug("schema_domain_registry resolve failed, using legacy tables: %s", exc)

    tools_cfg = (tables.get("tools") or {}).get(tool_name)
    if not isinstance(tools_cfg, dict):
        return None, {}

    variants = tools_cfg.get("variants") or {}
    if not isinstance(variants, dict) or not variants:
        return None, {}

    action_map = tools_cfg.get("profile_action_map") or {}
    if profile is not None and profile.default_action.tool == tool_name:
        action = profile.default_action.action
        if action and isinstance(action_map, dict):
            variant_id = action_map.get(action)
            if variant_id and variant_id in variants:
                variant = dict(variants[variant_id])
                variant["properties"] = _merge_variant_properties(variant, profile, tool_name)
                return str(variant_id), variant

    domain = context.effective_domain
    if domain:
        default_variant = variants.get("default")
        if isinstance(default_variant, dict):
            return "default", dict(default_variant)

    return None, {}


def _merge_variant_properties(
    variant: Mapping[str, Any],
    profile: IntentProfile,
    tool_name: str,
) -> List[str]:
    """Variant property list; profile keys merged only when profile targets this tool."""
    props = list(variant.get("properties") or [])
    if profile.default_action.tool == tool_name:
        props.extend(profile_property_allowlist(profile, tool_name))
    seen: List[str] = []
    for name in props:
        if name and name not in seen:
            seen.append(str(name))
    if "action" in (variant.get("properties") or []) and "action" not in seen:
        seen.insert(0, "action")
    return seen


def _merge_property_allowlist(
    variant: Mapping[str, Any],
    profile: Optional[IntentProfile],
    tool_name: str,
) -> List[str]:
    props = list(variant.get("properties") or [])
    if profile is not None and profile.default_action.tool == tool_name:
        for name in profile_property_allowlist(profile, tool_name):
            if name not in props:
                props.append(name)
    seen: List[str] = []
    for name in props:
        if name and name not in seen:
            seen.append(str(name))
    if "action" not in seen and isinstance(variant.get("actions"), list) and variant.get("actions"):
        seen.insert(0, "action")
    return seen


def optimize_tool_schema(
    canonical_tool: Dict[str, Any],
    context: SchemaOptimizeContext,
    *,
    domain_tables: Optional[Mapping[str, Any]] = None,
) -> OptimizedToolSchema:
    """
    Return a pruned copy of *canonical_tool* for LLM disclosure.

    The input *canonical_tool* is never modified (Fase 3.3).
    """
    name = _tool_name(canonical_tool)
    tables = domain_tables if domain_tables is not None else load_domain_tables()
    variant_id, variant = _resolve_variant(name, context=context, tables=tables)

    out = copy.deepcopy(canonical_tool)
    descriptions_truncated = _truncate_descriptions_in_schema(
        out, level=context.disclosure_level
    )

    if not variant_id or not variant:
        return OptimizedToolSchema(
            schema=out,
            tool_name=name,
            optimized=False,
            descriptions_truncated=descriptions_truncated,
            reason="no_matching_variant",
        )

    fn = out.get("function")
    if not isinstance(fn, dict):
        return OptimizedToolSchema(
            schema=out,
            tool_name=name,
            optimized=False,
            descriptions_truncated=descriptions_truncated,
            reason="invalid_tool_shape",
        )

    params = fn.get("parameters")
    if not isinstance(params, dict):
        return OptimizedToolSchema(
            schema=out,
            tool_name=name,
            optimized=False,
            variant_id=variant_id,
            descriptions_truncated=descriptions_truncated,
            reason="no_parameters_block",
        )

    allowed_actions = tuple(variant.get("actions") or ())
    allowed_properties = tuple(
        variant.get("properties")
        if isinstance(variant.get("properties"), list)
        else _merge_property_allowlist(variant, context.profile, name)
    )

    pruned_params, properties_pruned, actions_pruned = prune_parameter_properties(
        params,
        allowed_properties,
        allowed_actions=allowed_actions if allowed_actions else None,
    )
    fn["parameters"] = pruned_params
    descriptions_truncated = (
        _truncate_descriptions_in_schema(out, level=context.disclosure_level)
        or descriptions_truncated
    )

    return OptimizedToolSchema(
        schema=out,
        tool_name=name,
        optimized=True,
        variant_id=variant_id,
        allowed_actions=allowed_actions,
        allowed_properties=allowed_properties,
        actions_pruned=actions_pruned,
        properties_pruned=properties_pruned,
        descriptions_truncated=descriptions_truncated,
        reason="variant_applied",
    )


def optimize_tools_for_llm(
    tools: Sequence[Dict[str, Any]],
    context: SchemaOptimizeContext,
    *,
    domain_tables: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Optimize each tool in *tools* for LLM disclosure.

    Each entry in *tools* is deep-copied before pruning; inputs are never aliased to
    the returned mirror list (Fase 3.3).
    """
    optimized: List[Dict[str, Any]] = []
    for tool in tools:
        result = optimize_tool_schema(tool, context, domain_tables=domain_tables)
        optimized.append(result.schema)
        if tool is result.schema:
            raise CanonicalCatalogMutationError(
                f"optimize_tool_schema returned the same object for {_tool_name(tool)}"
            )
    return optimized


def estimate_schema_json_chars(tools: Sequence[Dict[str, Any]]) -> int:
    return len(json.dumps(list(tools), ensure_ascii=False))
