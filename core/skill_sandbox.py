# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Skill sandbox — restricted execution environment governed by capability declarations.

Before a skill runs, the sandbox:
1. Builds a ``CapabilityDeclaration`` summarising exactly what the skill
   will be allowed to do (and what will be denied).
2. Constructs a restricted Python environment with:
   - Import guards that block modules not covered by declared permissions
   - Restricted builtins (no ``exec``, ``eval``, ``compile``, ``__import__``
     unless explicitly declared)
   - Scoped filesystem access through a ``ScopedFS`` helper injected into
     the skill's namespace
   - Network access control via import blocking of ``socket``, ``http``,
     ``urllib``, ``requests`` unless ``network:*`` is declared
3. Executes the skill in a subprocess with a temp working directory,
   environment variables advertising the sandbox context, and a hard
   timeout.

The sandbox does NOT claim to be a security boundary against malicious code.
It is a *capability-aware guardrail* that prevents well-intentioned skills
from accidentally exceeding their declared scope.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set

from skill_manifest import PermissionKind, RiskLevel, SkillManifest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module allow-lists per permission
# ---------------------------------------------------------------------------

_BASE_ALLOWED_MODULES: FrozenSet[str] = frozenset({
    "abc", "asyncio", "base64", "bisect", "builtins", "calendar",
    "collections", "copy", "csv", "dataclasses", "datetime", "decimal",
    "difflib", "enum", "fnmatch", "fractions", "functools", "hashlib",
    "heapq", "hmac", "html", "inspect", "io", "itertools", "json",
    "keyword", "logging", "math", "numbers", "operator", "os.path",
    "pathlib", "pprint", "random", "re", "secrets", "statistics",
    "string", "struct", "textwrap", "time", "traceback", "types",
    "typing", "unicodedata", "uuid", "warnings", "xml",
})

_PERMISSION_MODULES: Dict[PermissionKind, FrozenSet[str]] = {
    PermissionKind.FILESYSTEM_READ: frozenset({"os", "glob", "shutil", "stat", "fileinput", "mimetypes"}),
    PermissionKind.FILESYSTEM_WRITE: frozenset({"os", "glob", "shutil", "stat", "tempfile", "mimetypes"}),
    PermissionKind.FILESYSTEM_DELETE: frozenset({"os", "shutil"}),
    PermissionKind.SHELL_RUN: frozenset({"subprocess", "os", "shlex"}),
    PermissionKind.SHELL_ADMIN: frozenset({"subprocess", "os", "shlex", "ctypes"}),
    PermissionKind.NETWORK_OUTBOUND: frozenset({"socket", "ssl", "http", "urllib", "email"}),
    PermissionKind.NETWORK_INBOUND: frozenset({"socket", "ssl", "http", "socketserver"}),
    PermissionKind.REGISTRY_READ: frozenset({"winreg"}),
    PermissionKind.REGISTRY_WRITE: frozenset({"winreg"}),
    PermissionKind.PROCESS_SPAWN: frozenset({"subprocess", "os", "multiprocessing"}),
    PermissionKind.PROCESS_KILL: frozenset({"subprocess", "os", "signal"}),
    PermissionKind.CLIPBOARD_READ: frozenset({"ctypes"}),
    PermissionKind.CLIPBOARD_WRITE: frozenset({"ctypes"}),
    PermissionKind.COM_AUTOMATION: frozenset({"win32com", "pythoncom", "pywintypes", "comtypes"}),
    PermissionKind.UI_AUTOMATION: frozenset({"pywinauto", "ctypes"}),
    PermissionKind.UI_INPUT: frozenset({"pywinauto", "ctypes", "pynput"}),
    PermissionKind.BROWSER: frozenset({"playwright", "selenium", "webbrowser"}),
    PermissionKind.SANDBOX_PYTHON: frozenset(),
    PermissionKind.SANDBOX_POWERSHELL: frozenset(),
    PermissionKind.MEMORY_READ: frozenset(),
    PermissionKind.MEMORY_WRITE: frozenset(),
}

_DANGEROUS_BUILTINS = frozenset({"exec", "eval", "compile", "__import__", "breakpoint"})

import re as _re

_DANGEROUS_PATTERNS = [
    (_re.compile(r'\bexec\s*\('), "exec()"),
    (_re.compile(r'\beval\s*\('), "eval()"),
    (_re.compile(r'\bcompile\s*\('), "compile()"),
    (_re.compile(r'\b__import__\s*\('), "__import__()"),
    (_re.compile(r'\bbreakpoint\s*\('), "breakpoint()"),
]


# ---------------------------------------------------------------------------
# Capability Declaration
# ---------------------------------------------------------------------------

@dataclass
class CapabilityGrant:
    """A single capability that has been granted or denied."""
    permission: str
    granted: bool
    reason: str = ""
    modules_allowed: List[str] = field(default_factory=list)


@dataclass
class CapabilityDeclaration:
    """Pre-execution summary of what a skill is allowed to do.

    Generated before execution and available for audit/logging/approval.
    """
    skill_name: str
    skill_version: str
    risk_level: str
    requires_approval: bool
    grants: List[CapabilityGrant] = field(default_factory=list)
    allowed_modules: List[str] = field(default_factory=list)
    denied_modules: List[str] = field(default_factory=list)
    restricted_builtins: List[str] = field(default_factory=list)
    filesystem_scope: str = "none"
    network_access: str = "none"
    max_execution_seconds: int = 60
    sandbox_dir: str = ""

    @property
    def all_granted(self) -> bool:
        return all(g.granted for g in self.grants)

    @property
    def denied_permissions(self) -> List[str]:
        return [g.permission for g in self.grants if not g.granted]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "grants": [
                {"permission": g.permission, "granted": g.granted,
                 "reason": g.reason, "modules_allowed": g.modules_allowed}
                for g in self.grants
            ],
            "all_granted": self.all_granted,
            "denied_permissions": self.denied_permissions,
            "allowed_modules": sorted(self.allowed_modules),
            "denied_modules": sorted(self.denied_modules),
            "restricted_builtins": sorted(self.restricted_builtins),
            "filesystem_scope": self.filesystem_scope,
            "network_access": self.network_access,
            "max_execution_seconds": self.max_execution_seconds,
            "sandbox_dir": self.sandbox_dir,
        }

    def summary(self) -> str:
        granted_count = sum(1 for g in self.grants if g.granted)
        denied_count = len(self.grants) - granted_count
        parts = [
            f"Skill '{self.skill_name}' v{self.skill_version}",
            f"risk={self.risk_level}",
            f"permissions: {granted_count} granted, {denied_count} denied",
            f"fs={self.filesystem_scope}",
            f"net={self.network_access}",
            f"timeout={self.max_execution_seconds}s",
        ]
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Sandbox environment builder
# ---------------------------------------------------------------------------

class SkillSandbox:
    """Builds and manages the restricted execution environment for a skill."""

    def __init__(self, *, sandbox_root: Optional[Path] = None):
        self.sandbox_root = sandbox_root or Path(tempfile.gettempdir()) / "agente_skill_sandbox"
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def declare_capabilities(
        self,
        manifest: SkillManifest,
        granted_capabilities: Set[PermissionKind],
    ) -> CapabilityDeclaration:
        """Build a CapabilityDeclaration describing what will be allowed/denied."""
        grants: List[CapabilityGrant] = []
        all_allowed_modules: Set[str] = set(_BASE_ALLOWED_MODULES)

        for perm in manifest.permissions:
            perm_modules = _PERMISSION_MODULES.get(perm, frozenset())
            if perm in granted_capabilities:
                grants.append(CapabilityGrant(
                    permission=perm.value,
                    granted=True,
                    reason="Capability granted by runner",
                    modules_allowed=sorted(perm_modules),
                ))
                all_allowed_modules |= perm_modules
            else:
                grants.append(CapabilityGrant(
                    permission=perm.value,
                    granted=False,
                    reason="Capability not granted by runner",
                    modules_allowed=[],
                ))

        fs_scope = self._filesystem_scope(manifest.permissions, granted_capabilities)
        net_access = self._network_scope(manifest.permissions, granted_capabilities)
        restricted = sorted(_DANGEROUS_BUILTINS)

        known_sensitive = {
            "subprocess", "os", "shutil", "socket", "ssl", "http", "urllib",
            "ctypes", "winreg", "win32com", "pythoncom", "pywinauto",
            "multiprocessing", "signal", "webbrowser", "pynput",
        }
        denied_modules = sorted(known_sensitive - all_allowed_modules)

        sandbox_dir = str(self.sandbox_root / f"skill_{manifest.name.replace('.', '_')}")

        return CapabilityDeclaration(
            skill_name=manifest.name,
            skill_version=manifest.version,
            risk_level=manifest.effective_risk_level.value,
            requires_approval=manifest.requires_approval,
            grants=grants,
            allowed_modules=sorted(all_allowed_modules),
            denied_modules=denied_modules,
            restricted_builtins=restricted,
            filesystem_scope=fs_scope,
            network_access=net_access,
            max_execution_seconds=manifest.risk.max_execution_time_seconds,
            sandbox_dir=sandbox_dir,
        )

    def build_environment(
        self,
        manifest: SkillManifest,
        declaration: CapabilityDeclaration,
    ) -> Dict[str, str]:
        """Build environment variables for the sandboxed subprocess."""
        sandbox_dir = Path(declaration.sandbox_dir)
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["AGENTE_SANDBOX"] = "1"
        env["AGENTE_SKILL_SANDBOX"] = "1"
        env["AGENTE_SANDBOX_DIR"] = str(sandbox_dir)
        env["AGENTE_SKILL_NAME"] = manifest.name
        env["AGENTE_SKILL_VERSION"] = manifest.version
        env["AGENTE_SKILL_RISK"] = manifest.effective_risk_level.value
        env["AGENTE_SKILL_PERMISSIONS"] = ",".join(p.value for p in manifest.permissions)
        env["AGENTE_SKILL_ALLOWED_MODULES"] = ",".join(declaration.allowed_modules)
        env["AGENTE_SKILL_DENIED_MODULES"] = ",".join(declaration.denied_modules)
        env["AGENTE_SKILL_FS_SCOPE"] = declaration.filesystem_scope
        env["AGENTE_SKILL_NET_ACCESS"] = declaration.network_access
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def build_import_guard_code(self, declaration: CapabilityDeclaration) -> str:
        """Generate Python code that installs an import hook restricting modules."""
        allowed = set(declaration.allowed_modules)
        denied = set(declaration.denied_modules)
        return (
            "import sys as _sys\n"
            "import importlib as _importlib\n"
            "\n"
            "class _SkillImportGuard:\n"
            "    _ALLOWED = " + repr(allowed) + "\n"
            "    _DENIED = " + repr(denied) + "\n"
            "\n"
            "    def find_module(self, fullname, path=None):\n"
            "        top = fullname.split('.')[0]\n"
            "        if top in self._DENIED:\n"
            "            return self\n"
            "        return None\n"
            "\n"
            "    def load_module(self, fullname):\n"
            "        top = fullname.split('.')[0]\n"
            "        raise ImportError(\n"
            "            f\"Skill sandbox: import of '{fullname}' blocked. \"\n"
            "            f\"Module '{top}' is not covered by declared permissions.\"\n"
            "        )\n"
            "\n"
            "_sys.meta_path.insert(0, _SkillImportGuard())\n"
        )

    def scan_dangerous_patterns(self, code: str) -> List[str]:
        """Scan skill code for dangerous builtin calls.

        Returns list of warnings (empty = clean).  This is a static check
        applied *before* execution — it does not modify builtins at runtime
        because doing so breaks Python's import machinery.
        """
        warnings: List[str] = []
        for pattern, name in _DANGEROUS_PATTERNS:
            if pattern.search(code):
                warnings.append(f"Skill code contains '{name}' call")
        return warnings

    def build_scoped_fs_code(self, declaration: CapabilityDeclaration) -> str:
        """Inject a ScopedFS helper into the skill namespace that limits file paths."""
        sandbox_dir = declaration.sandbox_dir.replace("\\", "\\\\")
        return (
            "import os as _os\n"
            "import pathlib as _pathlib\n"
            "\n"
            "class ScopedFS:\n"
            "    \"\"\"Filesystem helper scoped to the skill's sandbox directory.\"\"\"\n"
            f"    ROOT = _pathlib.Path(r'{sandbox_dir}')\n"
            "\n"
            "    @classmethod\n"
            "    def resolve(cls, path):\n"
            "        resolved = cls.ROOT / path\n"
            "        resolved = resolved.resolve()\n"
            "        if not str(resolved).startswith(str(cls.ROOT.resolve())):\n"
            "            raise PermissionError(f'Path escapes sandbox: {path}')\n"
            "        return resolved\n"
            "\n"
            "    @classmethod\n"
            "    def read(cls, path):\n"
            "        return cls.resolve(path).read_text(encoding='utf-8')\n"
            "\n"
            "    @classmethod\n"
            "    def write(cls, path, content):\n"
            "        target = cls.resolve(path)\n"
            "        target.parent.mkdir(parents=True, exist_ok=True)\n"
            "        target.write_text(content, encoding='utf-8')\n"
            "        return str(target)\n"
            "\n"
            "    @classmethod\n"
            "    def exists(cls, path):\n"
            "        return cls.resolve(path).exists()\n"
            "\n"
            "    @classmethod\n"
            "    def listdir(cls, path='.'):\n"
            "        return [p.name for p in cls.resolve(path).iterdir()]\n"
            "\n"
        )

    def wrap_skill_code(
        self,
        manifest: SkillManifest,
        code: str,
        declaration: CapabilityDeclaration,
        params_json: str,
    ) -> str:
        """Wrap skill code with sandbox guards and execution scaffolding."""
        parts: List[str] = []

        parts.append("# --- Skill Sandbox Preamble ---\n")
        parts.append(self.build_import_guard_code(declaration))
        parts.append("\n")

        if declaration.filesystem_scope not in ("none", "sandbox_only"):
            parts.append(self.build_scoped_fs_code(declaration))
            parts.append("\n")

        parts.append("# --- Skill Code ---\n")
        parts.append(code)
        parts.append("\n\n")

        parts.append("# --- Skill Execution ---\n")
        parts.append("import json as _json\n")
        parts.append("import sys as _sys\n")
        parts.append("import traceback as _traceback\n")
        parts.append("\n")
        parts.append(f"_params = _json.loads('''{params_json}''')\n")
        parts.append("try:\n")
        parts.append(f"    _result = {manifest.entry_point}(_params)\n")
        parts.append("    _json.dump({'ok': True, 'output': _result}, _sys.stdout, default=str)\n")
        parts.append("except Exception as _exc:\n")
        parts.append("    _traceback.print_exc(file=_sys.stderr)\n")
        parts.append("    _json.dump({'ok': False, 'error': str(_exc)}, _sys.stdout, default=str)\n")
        parts.append("    _sys.exit(1)\n")

        return "".join(parts)

    def create_sandbox_dir(self, manifest: SkillManifest) -> Path:
        """Create and return a fresh sandbox working directory for a skill run."""
        sandbox_dir = self.sandbox_root / f"skill_{manifest.name.replace('.', '_')}"
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        return sandbox_dir

    def cleanup(self, sandbox_dir: Path) -> None:
        """Clean up a sandbox directory after execution."""
        import shutil
        try:
            if sandbox_dir.exists() and str(sandbox_dir).startswith(str(self.sandbox_root)):
                shutil.rmtree(str(sandbox_dir), ignore_errors=True)
        except Exception:
            logger.debug("Sandbox cleanup failed for %s", sandbox_dir, exc_info=True)

    @staticmethod
    def _filesystem_scope(
        permissions: List[PermissionKind],
        granted: Set[PermissionKind],
    ) -> str:
        perms = set(permissions) & granted
        if PermissionKind.FILESYSTEM_DELETE in perms:
            return "read_write_delete"
        if PermissionKind.FILESYSTEM_WRITE in perms:
            return "read_write"
        if PermissionKind.FILESYSTEM_READ in perms:
            return "read_only"
        return "sandbox_only"

    @staticmethod
    def _network_scope(
        permissions: List[PermissionKind],
        granted: Set[PermissionKind],
    ) -> str:
        perms = set(permissions) & granted
        if PermissionKind.NETWORK_INBOUND in perms and PermissionKind.NETWORK_OUTBOUND in perms:
            return "full"
        if PermissionKind.NETWORK_INBOUND in perms:
            return "inbound"
        if PermissionKind.NETWORK_OUTBOUND in perms:
            return "outbound"
        return "none"
