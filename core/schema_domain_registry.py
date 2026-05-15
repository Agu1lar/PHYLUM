# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Editable domain → action/property tables for schema optimization (Fase 3.4).

Tables are data-driven (JSON), not derived from user phrases. Bundled defaults live in
``schema_domain_tables.json``; operators can overlay ``~/.agente/schema_domain_tables.json``
or set ``AGENTE_SCHEMA_DOMAIN_TABLES``.

Intent profiles may declare ``schema_variant`` / ``tool_variants`` to pick a row in the
table without adding Python branches.
"""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_BUNDLED_PATH = Path(__file__).resolve().parent / "schema_domain_tables.json"
_DEFAULT_REGISTRY: Optional["SchemaDomainRegistry"] = None


def user_schema_domain_tables_path() -> Path:
    custom = os.getenv("AGENTE_SCHEMA_DOMAIN_TABLES", "").strip()
    if custom:
        return Path(custom)
    custom_dir = os.getenv("AGENTE_SCHEMA_DOMAIN_TABLES_DIR", "").strip()
    if custom_dir:
        return Path(custom_dir) / "schema_domain_tables.json"
    return Path.home() / ".agente" / "schema_domain_tables.json"


def bundled_schema_domain_tables_path() -> Path:
    return _BUNDLED_PATH


@dataclass(frozen=True)
class DomainVariantSpec:
    """Allowed actions and properties for one domain variant row."""

    actions: Tuple[str, ...] = ()
    properties: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DomainVariantSpec":
        actions = tuple(str(a) for a in (raw.get("actions") or ()))
        properties = tuple(str(p) for p in (raw.get("properties") or ()))
        return cls(actions=actions, properties=properties)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.actions:
            out["actions"] = list(self.actions)
        if self.properties:
            out["properties"] = list(self.properties)
        return out


@dataclass(frozen=True)
class DomainTableSpec:
    """Configuration for a logical domain (e.g. office, filesystem, shell)."""

    domain: str
    tool: str
    default_variant: str
    profile_action_map: Dict[str, str] = field(default_factory=dict)
    variants: Dict[str, DomainVariantSpec] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "default_variant": self.default_variant,
            "profile_action_map": dict(self.profile_action_map),
            "variants": {key: spec.to_dict() for key, spec in self.variants.items()},
        }


class SchemaDomainRegistry:
    """Merged domain tables (bundled + user overlay)."""

    def __init__(self, domains: Dict[str, DomainTableSpec], *, version: int = 2):
        self.version = version
        self._domains = domains

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "SchemaDomainRegistry":
        normalized = normalize_tables_dict(raw)
        domains: Dict[str, DomainTableSpec] = {}
        for domain_id, cfg in (normalized.get("domains") or {}).items():
            if not isinstance(cfg, dict):
                continue
            tool = str(cfg.get("tool") or domain_id)
            default_variant = str(cfg.get("default_variant") or "default")
            action_map = {
                str(k): str(v)
                for k, v in (cfg.get("profile_action_map") or {}).items()
            }
            variants_raw = cfg.get("variants") or {}
            variants: Dict[str, DomainVariantSpec] = {}
            if isinstance(variants_raw, dict):
                for variant_id, variant_cfg in variants_raw.items():
                    if isinstance(variant_cfg, dict):
                        variants[str(variant_id)] = DomainVariantSpec.from_dict(variant_cfg)
            domains[str(domain_id)] = DomainTableSpec(
                domain=str(domain_id),
                tool=tool,
                default_variant=default_variant,
                profile_action_map=action_map,
                variants=variants,
            )
        return cls(domains, version=int(normalized.get("version") or 2))

    def list_domains(self) -> List[str]:
        return sorted(self._domains.keys())

    def get_domain(self, domain: str) -> Optional[DomainTableSpec]:
        return self._domains.get(domain)

    def get_tool_config(self, tool_name: str) -> Optional[DomainTableSpec]:
        for spec in self._domains.values():
            if spec.tool == tool_name:
                return spec
        return None

    def resolve_variant(
        self,
        tool_name: str,
        *,
        domain: Optional[str] = None,
        action: Optional[str] = None,
        schema_variant: Optional[str] = None,
        tool_variants: Optional[Mapping[str, str]] = None,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Resolve a variant id and raw config dict for *tool_name*.

        Priority: explicit ``tool_variants[tool]`` → ``schema_variant`` (primary tool) →
        ``profile_action_map[action]`` → domain ``default_variant``.
        """
        spec = self._pick_domain_spec(tool_name, domain=domain)
        if spec is None:
            return None, {}

        variant_id: Optional[str] = None
        if tool_variants and tool_name in tool_variants:
            variant_id = str(tool_variants[tool_name])
        elif schema_variant and spec.tool == tool_name:
            variant_id = str(schema_variant)
        elif action and action in spec.profile_action_map:
            variant_id = spec.profile_action_map[action]
        else:
            variant_id = spec.default_variant

        if not variant_id or variant_id not in spec.variants:
            if spec.default_variant in spec.variants:
                variant_id = spec.default_variant
            else:
                return None, {}

        variant = spec.variants[variant_id]
        return variant_id, variant.to_dict()

    def _pick_domain_spec(
        self,
        tool_name: str,
        *,
        domain: Optional[str],
    ) -> Optional[DomainTableSpec]:
        if domain and domain in self._domains:
            spec = self._domains[domain]
            if spec.tool == tool_name:
                return spec
        return self.get_tool_config(tool_name)

    def to_tables_dict(self) -> Dict[str, Any]:
        """Legacy shape consumed by ``tool_schema_optimizer`` (tools keyed by tool name)."""
        tools: Dict[str, Any] = {}
        for domain_id, spec in self._domains.items():
            tools[spec.tool] = {
                "domain": domain_id,
                "default_variant": spec.default_variant,
                "profile_action_map": dict(spec.profile_action_map),
                "variants": {vid: v.to_dict() for vid, v in spec.variants.items()},
            }
        return {"version": self.version, "domains": {d: s.to_dict() for d, s in self._domains.items()}, "tools": tools}

    def validate_against_canonical(self) -> List[str]:
        """Return human-readable errors for actions not present in canonical schemas."""
        errors: List[str] = []
        try:
            from canonical_tools import tool_schema_by_name
        except ImportError:
            return errors

        for spec in self._domains.values():
            try:
                schema = tool_schema_by_name(spec.tool)
            except ValueError:
                errors.append(f"domain {spec.domain}: unknown tool '{spec.tool}'")
                continue
            props = (
                (schema.get("function") or {}).get("parameters") or {}
            ).get("properties") or {}
            action_spec = props.get("action") if isinstance(props, dict) else None
            canonical_actions: set[str] = set()
            if isinstance(action_spec, dict):
                enum = action_spec.get("enum")
                if isinstance(enum, list):
                    canonical_actions = {str(a) for a in enum}

            for variant_id, variant in spec.variants.items():
                for action in variant.actions:
                    if canonical_actions and action not in canonical_actions:
                        errors.append(
                            f"domain {spec.domain} variant {variant_id}: "
                            f"unknown action '{action}' for tool '{spec.tool}'"
                        )
        return errors


def normalize_tables_dict(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize v1 ``tools`` layout into v2 ``domains`` (backward compatible)."""
    version = int(raw.get("version") or 1)
    if version >= 2 and isinstance(raw.get("domains"), dict):
        merged = deepcopy(dict(raw))
        if "tools" not in merged:
            merged["tools"] = _domains_to_tools(merged["domains"])
        return merged

    tools = raw.get("tools") or {}
    domains: Dict[str, Any] = {}
    if isinstance(tools, dict):
        for tool_name, cfg in tools.items():
            if not isinstance(cfg, dict):
                continue
            domain_id = str(cfg.get("domain") or tool_name)
            entry = deepcopy(cfg)
            entry["tool"] = str(entry.get("tool") or tool_name)
            if "default_variant" not in entry:
                entry["default_variant"] = "default"
            domains[domain_id] = entry
    return {"version": 2, "domains": domains, "tools": tools}


def _domains_to_tools(domains: Mapping[str, Any]) -> Dict[str, Any]:
    tools: Dict[str, Any] = {}
    for domain_id, cfg in domains.items():
        if not isinstance(cfg, dict):
            continue
        tool = str(cfg.get("tool") or domain_id)
        tools[tool] = {
            "domain": domain_id,
            "default_variant": cfg.get("default_variant", "default"),
            "profile_action_map": cfg.get("profile_action_map") or {},
            "variants": cfg.get("variants") or {},
        }
    return tools


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for key, value in overlay.items():
        if key == "domains" and isinstance(value, dict):
            domains = out.setdefault("domains", {})
            if not isinstance(domains, dict):
                domains = {}
                out["domains"] = domains
            for domain_id, domain_cfg in value.items():
                if domain_id in domains and isinstance(domains[domain_id], dict) and isinstance(domain_cfg, dict):
                    merged_domain = deepcopy(domains[domain_id])
                    for dk, dv in domain_cfg.items():
                        if dk == "variants" and isinstance(dv, dict):
                            variants = merged_domain.setdefault("variants", {})
                            if isinstance(variants, dict):
                                variants.update(dv)
                        elif dk == "profile_action_map" and isinstance(dv, dict):
                            action_map = merged_domain.setdefault("profile_action_map", {})
                            if isinstance(action_map, dict):
                                action_map.update(dv)
                        else:
                            merged_domain[dk] = dv
                    domains[domain_id] = merged_domain
                else:
                    domains[domain_id] = deepcopy(domain_cfg)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            nested = deepcopy(out[key])
            nested.update(value)
            out[key] = nested
        else:
            out[key] = deepcopy(value)
    return out


def load_raw_tables(*, bundled_path: Optional[Path] = None, user_path: Optional[Path] = None) -> Dict[str, Any]:
    bundled = bundled_path or bundled_schema_domain_tables_path()
    raw: Dict[str, Any] = {"version": 2, "domains": {}}
    try:
        raw = json.loads(bundled.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("bundled schema_domain_tables load failed: %s", exc)

    user = user_path or user_schema_domain_tables_path()
    if user.is_file():
        try:
            overlay = json.loads(user.read_text(encoding="utf-8"))
            if isinstance(overlay, dict):
                raw = _deep_merge_dict(normalize_tables_dict(raw), normalize_tables_dict(overlay))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("user schema_domain_tables overlay failed (%s): %s", user, exc)

    return normalize_tables_dict(raw)


def load_schema_domain_registry(*, reload: bool = False) -> SchemaDomainRegistry:
    global _DEFAULT_REGISTRY
    if not reload and _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY
    registry = SchemaDomainRegistry.from_raw(load_raw_tables())
    errors = registry.validate_against_canonical()
    for message in errors:
        logger.warning("schema_domain_tables: %s", message)
    _DEFAULT_REGISTRY = registry
    return registry


def reload_schema_domain_registry() -> SchemaDomainRegistry:
    return load_schema_domain_registry(reload=True)
