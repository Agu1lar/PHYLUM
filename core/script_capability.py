# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Capability isolation for dynamic sandbox scripts (Python / PowerShell)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set

from skill_manifest import PermissionKind
from skill_sandbox import SkillSandbox, _BASE_ALLOWED_MODULES, _DANGEROUS_PATTERNS, _PERMISSION_MODULES

_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
_SUBPROCESS_RE = re.compile(r"\bsubprocess\b|\bos\.system\b|\bos\.popen\b")
_NETWORK_RE = re.compile(r"\b(requests|urllib|httpx|socket|aiohttp)\b")
_REGISTRY_RE = re.compile(r"\bwinreg\b")


@dataclass
class ScriptCapabilityProfile:
    """Declared and inferred capabilities for one script execution."""

    script_id: str
    granted: Set[PermissionKind] = field(default_factory=set)
    inferred: Set[PermissionKind] = field(default_factory=set)
    denied_modules: List[str] = field(default_factory=list)
    allowed_modules: List[str] = field(default_factory=list)
    filesystem_scope: str = "sandbox_only"
    violations: List[str] = field(default_factory=list)

    @property
    def effective(self) -> Set[PermissionKind]:
        base = {PermissionKind.SANDBOX_PYTHON}
        return base | self.granted | self.inferred

    def to_dict(self) -> Dict[str, object]:
        return {
            "script_id": self.script_id,
            "granted": sorted(p.value for p in self.granted),
            "inferred": sorted(p.value for p in self.inferred),
            "effective": sorted(p.value for p in self.effective),
            "allowed_modules": self.allowed_modules,
            "denied_modules": self.denied_modules,
            "filesystem_scope": self.filesystem_scope,
            "violations": self.violations,
            "isolated": not self.violations,
        }


def parse_capability_strings(values: Optional[List[str]]) -> Set[PermissionKind]:
    granted: Set[PermissionKind] = set()
    if not values:
        return granted
    by_value = {p.value: p for p in PermissionKind}
    for raw in values:
        key = (raw or "").strip().lower()
        if key in by_value:
            granted.add(by_value[key])
    return granted


def infer_capabilities_from_code(code: str, *, language: str = "python") -> Set[PermissionKind]:
    if language != "python":
        if _SUBPROCESS_RE.search(code):
            return {PermissionKind.SHELL_RUN}
        return set()

    inferred: Set[PermissionKind] = set()
    modules: Set[str] = set()
    for match in _IMPORT_RE.finditer(code):
        modules.add(match.group(1))

    fs_modules = {"os", "pathlib", "shutil", "glob", "open"}
    if modules & fs_modules or re.search(r"\bopen\s*\(", code):
        inferred.add(PermissionKind.FILESYSTEM_READ)
        if re.search(r"\.write_|write_text|write_bytes|shutil\.(move|copy|rmtree)|unlink|remove", code):
            inferred.add(PermissionKind.FILESYSTEM_WRITE)

    if modules & {"subprocess"} or _SUBPROCESS_RE.search(code):
        inferred.add(PermissionKind.SHELL_RUN)
    if modules & {"winreg"} or _REGISTRY_RE.search(code):
        inferred.add(PermissionKind.REGISTRY_READ)
    if modules & {"socket", "urllib", "http"} or _NETWORK_RE.search(code):
        inferred.add(PermissionKind.NETWORK_OUTBOUND)
    if modules & {"win32com", "pythoncom"}:
        inferred.add(PermissionKind.COM_AUTOMATION)

    return inferred


def build_script_profile(
    code: str,
    *,
    script_id: str,
    capabilities: Optional[List[str]] = None,
    language: str = "python",
) -> ScriptCapabilityProfile:
    granted = parse_capability_strings(capabilities)
    inferred = infer_capabilities_from_code(code, language=language)
    effective = {PermissionKind.SANDBOX_PYTHON} | granted | inferred

    allowed: Set[str] = set(_BASE_ALLOWED_MODULES)
    for perm in effective:
        allowed |= _PERMISSION_MODULES.get(perm, frozenset())

    known_sensitive: Set[str] = set()
    for modules in _PERMISSION_MODULES.values():
        known_sensitive |= set(modules)
    denied = sorted(known_sensitive - allowed)

    default_granted = {
        PermissionKind.SANDBOX_PYTHON,
        PermissionKind.FILESYSTEM_READ,
        PermissionKind.FILESYSTEM_WRITE,
    }
    violations: List[str] = []
    for perm in inferred:
        if perm in default_granted or perm in granted:
            continue
        violations.append(f"Code requires {perm.value} but it was not declared in capabilities")

    sandbox = SkillSandbox()
    for warning in sandbox.scan_dangerous_patterns(code):
        if PermissionKind.SANDBOX_PYTHON in effective:
            violations.append(warning)

    fs_scope = SkillSandbox._filesystem_scope(list(effective), effective)

    return ScriptCapabilityProfile(
        script_id=script_id,
        granted=granted,
        inferred=inferred,
        denied_modules=denied,
        allowed_modules=sorted(allowed),
        filesystem_scope=fs_scope,
        violations=violations,
    )


def wrap_python_script(
    code: str,
    profile: ScriptCapabilityProfile,
    *,
    sandbox_dir: str,
) -> str:
    """Wrap user script with import guards and optional ScopedFS."""
    if profile.violations:
        raise ValueError("; ".join(profile.violations))

    from skill_sandbox import CapabilityDeclaration

    declaration = CapabilityDeclaration(
        skill_name=f"script:{profile.script_id}",
        skill_version="0",
        risk_level="medium",
        requires_approval=False,
        allowed_modules=profile.allowed_modules,
        denied_modules=profile.denied_modules,
        filesystem_scope=profile.filesystem_scope,
        sandbox_dir=sandbox_dir,
    )
    sandbox = SkillSandbox()
    parts = [
        "# --- Script capability isolation preamble ---\n",
        sandbox.build_import_guard_code(declaration),
        "\n",
    ]
    if profile.filesystem_scope != "none":
        parts.append(sandbox.build_scoped_fs_code(declaration))
        parts.append("\n")
    parts.append("# --- User script ---\n")
    parts.append(code)
    parts.append("\n")
    return "".join(parts)
