# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Local skill manifest — versioned, auditable, reusable skill descriptors.

A skill is a higher-level abstraction than a dynamic tool: it carries an
explicit manifest that declares *what* the skill does, *what* it needs
(permissions / capabilities), *what* it accepts and produces (typed I/O),
and *what risks* it carries.  The manifest is validated before the skill
is registered and again before every execution.

Manifest format is intentionally JSON-serialisable so skills can be
exported, imported and reviewed offline.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from skill_signing import SkillProvenance, SkillTrustStatus


# ---------------------------------------------------------------------------
# Enums and constants
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PermissionKind(str, Enum):
    FILESYSTEM_READ = "filesystem:read"
    FILESYSTEM_WRITE = "filesystem:write"
    FILESYSTEM_DELETE = "filesystem:delete"
    SHELL_RUN = "shell:run"
    SHELL_ADMIN = "shell:admin"
    NETWORK_OUTBOUND = "network:outbound"
    NETWORK_INBOUND = "network:inbound"
    REGISTRY_READ = "registry:read"
    REGISTRY_WRITE = "registry:write"
    PROCESS_SPAWN = "process:spawn"
    PROCESS_KILL = "process:kill"
    UI_AUTOMATION = "ui:automation"
    UI_INPUT = "ui:input"
    CLIPBOARD_READ = "clipboard:read"
    CLIPBOARD_WRITE = "clipboard:write"
    COM_AUTOMATION = "com:automation"
    BROWSER = "browser"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    SANDBOX_PYTHON = "sandbox:python"
    SANDBOX_POWERSHELL = "sandbox:powershell"


RISK_LEVEL_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

PERMISSION_RISK_FLOOR: Dict[PermissionKind, RiskLevel] = {
    PermissionKind.FILESYSTEM_DELETE: RiskLevel.HIGH,
    PermissionKind.SHELL_ADMIN: RiskLevel.HIGH,
    PermissionKind.REGISTRY_WRITE: RiskLevel.HIGH,
    PermissionKind.PROCESS_KILL: RiskLevel.HIGH,
    PermissionKind.NETWORK_INBOUND: RiskLevel.MEDIUM,
    PermissionKind.NETWORK_OUTBOUND: RiskLevel.MEDIUM,
    PermissionKind.SHELL_RUN: RiskLevel.MEDIUM,
    PermissionKind.FILESYSTEM_WRITE: RiskLevel.MEDIUM,
    PermissionKind.CLIPBOARD_WRITE: RiskLevel.MEDIUM,
    PermissionKind.COM_AUTOMATION: RiskLevel.MEDIUM,
    PermissionKind.UI_INPUT: RiskLevel.MEDIUM,
}

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


# ---------------------------------------------------------------------------
# Input / Output descriptors
# ---------------------------------------------------------------------------

class ParamDescriptor(BaseModel):
    """Describes a single input or output parameter of a skill."""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Optional[Any] = None
    enum: Optional[List[str]] = None
    sensitive: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", v):
            raise ValueError(f"Invalid param name: {v}")
        return v


class IOSchema(BaseModel):
    """Typed input/output contract for a skill."""
    params: List[ParamDescriptor] = Field(default_factory=list)
    description: str = ""

    @property
    def required_params(self) -> List[ParamDescriptor]:
        return [p for p in self.params if p.required]

    def validate_input(self, data: Dict[str, Any]) -> List[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: List[str] = []
        for p in self.params:
            if p.required and p.name not in data:
                if p.default is None:
                    errors.append(f"Missing required parameter: {p.name}")
            if p.name in data and p.enum and data[p.name] not in p.enum:
                errors.append(f"Parameter '{p.name}' must be one of {p.enum}, got '{data[p.name]}'")
        return errors


# ---------------------------------------------------------------------------
# Risk descriptor
# ---------------------------------------------------------------------------

class RiskDescriptor(BaseModel):
    """Declares the risk profile of a skill."""
    level: RiskLevel = RiskLevel.LOW
    tags: List[str] = Field(default_factory=list)
    rationale: str = ""
    reversible: bool = False
    side_effects: List[str] = Field(default_factory=list)
    data_exposure: Literal["none", "local", "network"] = "none"
    requires_approval: bool = False
    max_execution_time_seconds: int = Field(default=60, ge=1, le=3600)

    def effective_level(self, permissions: List[PermissionKind]) -> RiskLevel:
        """Compute the effective risk level from declared risk + permissions."""
        max_risk = self.level
        for perm in permissions:
            floor = PERMISSION_RISK_FLOOR.get(perm)
            if floor and RISK_LEVEL_ORDER.get(floor, 0) > RISK_LEVEL_ORDER.get(max_risk, 0):
                max_risk = floor
        return max_risk


# ---------------------------------------------------------------------------
# Skill Manifest
# ---------------------------------------------------------------------------

class SkillManifest(BaseModel):
    """Complete manifest for a local skill.

    Fields:
    - name: unique identifier (lowercase, alphanumeric + dots/dashes/underscores)
    - version: semver string (e.g. "1.0.0")
    - display_name: human-readable name
    - description: what the skill does
    - author: who wrote it
    - license: SPDX identifier (default GPL-3.0-or-later)
    - language: python or powershell
    - permissions: list of capabilities the skill requires
    - inputs: schema describing expected inputs
    - outputs: schema describing produced outputs
    - risk: risk profile
    - tags: domain/category tags
    - entry_point: the function name to call (default "run")
    - dependencies: Python packages required
    - checksum: SHA-256 of the skill code at registration time
    - created_at: ISO timestamp
    - updated_at: ISO timestamp
    - skill_id: unique internal identifier
    """
    name: str
    version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    author: str = ""
    license: str = "GPL-3.0-or-later"
    language: Literal["python", "powershell"] = "python"
    permissions: List[PermissionKind] = Field(default_factory=list)
    inputs: IOSchema = Field(default_factory=IOSchema)
    outputs: IOSchema = Field(default_factory=IOSchema)
    risk: RiskDescriptor = Field(default_factory=RiskDescriptor)
    tags: List[str] = Field(default_factory=list)
    entry_point: str = "run"
    dependencies: List[str] = Field(default_factory=list)
    checksum: str = ""
    manifest_checksum: str = ""
    signature: str = ""
    trust_status: SkillTrustStatus = SkillTrustStatus.TRUSTED
    provenance: SkillProvenance = Field(default_factory=SkillProvenance)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    skill_id: str = ""

    @field_validator("name")
    @classmethod
    def _valid_skill_name(cls, v: str) -> str:
        if not SKILL_NAME_RE.match(v):
            raise ValueError(
                f"Skill name must be 2-64 lowercase chars (a-z, 0-9, _, ., -), "
                f"starting with a letter. Got: '{v}'"
            )
        return v

    @field_validator("version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"Version must be semver (X.Y.Z). Got: '{v}'")
        return v

    @property
    def effective_risk_level(self) -> RiskLevel:
        return self.risk.effective_level(self.permissions)

    @property
    def requires_approval(self) -> bool:
        eff = self.effective_risk_level
        if self.risk.requires_approval:
            return True
        return RISK_LEVEL_ORDER.get(eff, 0) >= RISK_LEVEL_ORDER[RiskLevel.MEDIUM]

    def to_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        d["permissions"] = [p.value for p in self.permissions]
        d["risk"]["level"] = self.risk.level.value
        d["effective_risk_level"] = self.effective_risk_level.value
        d["requires_approval"] = self.requires_approval
        d["trust_status"] = self.trust_status.value if isinstance(self.trust_status, SkillTrustStatus) else self.trust_status
        if isinstance(self.provenance, SkillProvenance):
            d["provenance"] = self.provenance.model_dump()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillManifest":
        data = dict(data)
        if "permissions" in data:
            data["permissions"] = [
                PermissionKind(p) if isinstance(p, str) else p
                for p in data["permissions"]
            ]
        if "risk" in data and isinstance(data["risk"], dict):
            risk = dict(data["risk"])
            if isinstance(risk.get("level"), str):
                risk["level"] = RiskLevel(risk["level"])
            data["risk"] = risk
        if "provenance" in data and isinstance(data["provenance"], dict):
            data["provenance"] = SkillProvenance(**data["provenance"])
        if "trust_status" in data and isinstance(data["trust_status"], str):
            data["trust_status"] = SkillTrustStatus(data["trust_status"])
        return cls(**data)

    def validate_inputs(self, data: Dict[str, Any]) -> List[str]:
        return self.inputs.validate_input(data)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def compute_code_checksum(code: str) -> str:
    """SHA-256 hex digest of the skill code."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def validate_manifest(manifest: SkillManifest, *, code: Optional[str] = None) -> List[str]:
    """Run structural validation on a manifest. Returns list of issues (empty = valid)."""
    issues: List[str] = []

    if not manifest.description:
        issues.append("Manifest should have a description")

    if not manifest.permissions:
        issues.append("Manifest declares no permissions — verify the skill truly needs none")

    eff = manifest.effective_risk_level
    if RISK_LEVEL_ORDER.get(eff, 0) >= RISK_LEVEL_ORDER[RiskLevel.HIGH] and not manifest.risk.rationale:
        issues.append("High/critical-risk skills must include a risk rationale")

    if code and manifest.checksum:
        actual = compute_code_checksum(code)
        if actual != manifest.checksum:
            issues.append(f"Checksum mismatch: manifest={manifest.checksum[:16]}… actual={actual[:16]}…")

    if manifest.risk.max_execution_time_seconds > 300 and eff in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        issues.append("High-risk skills should not request >300s execution time")

    has_sensitive = any(p.sensitive for p in manifest.inputs.params)
    if has_sensitive and manifest.risk.data_exposure == "none":
        issues.append("Skill has sensitive inputs but declares no data exposure — review risk.data_exposure")

    return issues


def manifest_from_dynamic_tool(spec_dict: Dict[str, Any]) -> SkillManifest:
    """Upgrade a DynamicToolSpec dict to a SkillManifest with sensible defaults."""
    perms = [PermissionKind.SANDBOX_PYTHON]
    if spec_dict.get("language") == "powershell":
        perms = [PermissionKind.SANDBOX_POWERSHELL, PermissionKind.SHELL_RUN]

    code = spec_dict.get("code", "")
    return SkillManifest(
        name=re.sub(r"[^a-z0-9_.-]", "_", (spec_dict.get("name") or "unnamed").lower().strip())[:64] or "unnamed",
        version="1.0.0",
        display_name=spec_dict.get("name", ""),
        description=spec_dict.get("description", ""),
        language=spec_dict.get("language", "python"),
        permissions=perms,
        tags=spec_dict.get("tags", []),
        risk=RiskDescriptor(
            level=RiskLevel.MEDIUM,
            rationale="Auto-generated from dynamic tool — review before production use",
            reversible=False,
        ),
        checksum=compute_code_checksum(code),
        skill_id=spec_dict.get("tool_id", ""),
        created_at=spec_dict.get("created_at", datetime.now(timezone.utc).isoformat()),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
