# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Declarative intent profiles for direct routing (Fase 1.1)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).resolve().parent / "intent_profiles"
_DEFAULT_REGISTRY: Optional["IntentProfileRegistry"] = None


def user_intent_profiles_dir() -> Path:
    custom = os.getenv("AGENTE_INTENT_PROFILES_DIR", "").strip()
    if custom:
        return Path(custom)
    return Path.home() / ".agente" / "intent_profiles"
_REQUIRED_FIELDS = frozenset({"domain", "required_tools", "default_action", "param_defaults", "confidence_threshold"})


@dataclass(frozen=True)
class IntentDefaultAction:
    tool: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "action": self.action, "params": dict(self.params)}


@dataclass(frozen=True)
class IntentProfile:
    """Declarative profile: domain, tools, default tool call, defaults, confidence gate."""

    id: str
    domain: str
    required_tools: tuple[str, ...]
    default_action: IntentDefaultAction
    param_defaults: Dict[str, Any]
    confidence_threshold: float
    description: str = ""
    version: str = "1"
    signals: Dict[str, Any] = field(default_factory=dict)
    schema_variant: str = ""
    tool_variants: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "id": self.id,
            "domain": self.domain,
            "description": self.description,
            "version": self.version,
            "required_tools": list(self.required_tools),
            "default_action": self.default_action.to_dict(),
            "param_defaults": dict(self.param_defaults),
            "confidence_threshold": self.confidence_threshold,
            "signals": dict(self.signals),
        }
        if self.schema_variant:
            out["schema_variant"] = self.schema_variant
        if self.tool_variants:
            out["tool_variants"] = dict(self.tool_variants)
        return out


def _parse_default_action(raw: Any, *, profile_id: str) -> IntentDefaultAction:
    if not isinstance(raw, dict):
        raise ValueError(f"Profile {profile_id}: default_action must be an object")
    tool = str(raw.get("tool") or "").strip()
    action = str(raw.get("action") or "").strip()
    if not tool or not action:
        raise ValueError(f"Profile {profile_id}: default_action requires tool and action")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"Profile {profile_id}: default_action.params must be an object")
    return IntentDefaultAction(tool=tool, action=action, params=dict(params))


def _normalize_profile_dict(data: Dict[str, Any], *, source: Path) -> Dict[str, Any]:
    profile_id = str(data.get("id") or source.stem)
    merged = dict(data)
    merged["id"] = profile_id
    missing = _REQUIRED_FIELDS - set(merged.keys())
    if missing:
        raise ValueError(f"Profile {profile_id} ({source}) missing fields: {sorted(missing)}")
    return merged


def profile_from_dict(data: Dict[str, Any], *, source: Optional[Path] = None) -> IntentProfile:
    raw = _normalize_profile_dict(data, source=source or Path(data.get("id", "unknown")))
    profile_id = raw["id"]
    required = raw.get("required_tools")
    if not isinstance(required, list) or not required:
        raise ValueError(f"Profile {profile_id}: required_tools must be a non-empty list")

    param_defaults = raw.get("param_defaults")
    if not isinstance(param_defaults, dict):
        raise ValueError(f"Profile {profile_id}: param_defaults must be an object")

    threshold = float(raw["confidence_threshold"])
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"Profile {profile_id}: confidence_threshold must be in (0, 1]")

    signals = raw.get("signals") or {}
    if not isinstance(signals, dict):
        raise ValueError(f"Profile {profile_id}: signals must be an object")

    schema_variant = str(raw.get("schema_variant") or "").strip()
    tool_variants_raw = raw.get("tool_variants") or {}
    tool_variants: Dict[str, str] = {}
    if tool_variants_raw:
        if not isinstance(tool_variants_raw, dict):
            raise ValueError(f"Profile {profile_id}: tool_variants must be an object")
        tool_variants = {str(k): str(v) for k, v in tool_variants_raw.items() if k and v}

    return IntentProfile(
        id=profile_id,
        domain=str(raw["domain"]),
        description=str(raw.get("description") or ""),
        version=str(raw.get("version") or "1"),
        required_tools=tuple(str(t) for t in required),
        default_action=_parse_default_action(raw["default_action"], profile_id=profile_id),
        param_defaults=dict(param_defaults),
        confidence_threshold=threshold,
        signals=dict(signals),
        schema_variant=schema_variant,
        tool_variants=tool_variants,
    )


def _load_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ImportError(
                f"PyYAML is required to load {path.name}; install pyyaml or use .json profiles"
            ) from exc
        parsed = yaml.safe_load(text)
    else:
        parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"Profile file {path} must contain a single object")
    return parsed


def load_profiles_from_directory(directory: Optional[Union[str, Path]] = None) -> List[IntentProfile]:
    root = Path(directory) if directory is not None else _PROFILES_DIR
    if not root.is_dir():
        logger.warning("Intent profiles directory missing: %s", root)
        return []

    profiles: List[IntentProfile] = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            continue
        if path.name in {"manifest.json", "_learn_index.json"}:
            continue
        if path.stem.startswith("_"):
            continue
        try:
            data = _load_file(path)
            profiles.append(profile_from_dict(data, source=path))
        except Exception:
            logger.exception("Failed to load intent profile %s", path)
            raise
    return profiles


class IntentProfileRegistry:
    """In-memory registry of declarative intent profiles."""

    def __init__(self, profiles: Optional[List[IntentProfile]] = None):
        self._profiles: Dict[str, IntentProfile] = {}
        if profiles:
            for profile in profiles:
                self.register(profile)

    @classmethod
    def load(cls, directory: Optional[Union[str, Path]] = None) -> "IntentProfileRegistry":
        return cls(load_profiles_from_directory(directory))

    @classmethod
    def load_merged(cls) -> "IntentProfileRegistry":
        registry = cls(load_profiles_from_directory(_PROFILES_DIR))
        for profile in load_profiles_from_directory(user_intent_profiles_dir()):
            registry.register(profile, replace=True)
        return registry

    @classmethod
    def default(cls) -> "IntentProfileRegistry":
        global _DEFAULT_REGISTRY
        if _DEFAULT_REGISTRY is None:
            _DEFAULT_REGISTRY = cls.load_merged()
        return _DEFAULT_REGISTRY

    @classmethod
    def reload_default(cls) -> "IntentProfileRegistry":
        global _DEFAULT_REGISTRY
        _DEFAULT_REGISTRY = cls.load_merged()
        return _DEFAULT_REGISTRY

    def register(self, profile: IntentProfile, *, replace: bool = False) -> None:
        if profile.id in self._profiles and not replace:
            raise ValueError(f"Duplicate intent profile id: {profile.id}")
        self._profiles[profile.id] = profile

    def get(self, profile_id: str) -> Optional[IntentProfile]:
        return self._profiles.get(profile_id)

    def require(self, profile_id: str) -> IntentProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(f"Unknown intent profile: {profile_id}")
        return profile

    def list_profiles(self) -> List[IntentProfile]:
        return list(self._profiles.values())

    def list_by_domain(self, domain: str) -> List[IntentProfile]:
        return [p for p in self._profiles.values() if p.domain == domain]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self._profiles),
            "profiles": [profile.to_dict() for profile in self.list_profiles()],
        }
