# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Skill runner — sandbox execution with capability declaration before execution.

Before executing a skill, the runner:
1. Loads and validates the manifest
2. Verifies code integrity (checksum)
3. Builds a CapabilityDeclaration describing what will be allowed/denied
4. Checks that every declared permission is within the allowed capability set
5. Validates input parameters against the manifest's IOSchema
6. Constructs a sandboxed execution environment (import guards, builtin
   restrictions, scoped filesystem, env vars)
7. Executes the skill code in a subprocess with a hard timeout
8. Parses structured output and returns a SkillResult

The runner enforces capability boundaries: a skill declaring ``filesystem:write``
will only run if the caller explicitly grants that capability.  The full
capability declaration is available *before* execution for audit/approval.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from skill_manifest import (
    PermissionKind,
    RiskLevel,
    RISK_LEVEL_ORDER,
    SkillManifest,
    compute_code_checksum,
)
from skill_registry import SkillRegistry
from skill_sandbox import (
    CapabilityDeclaration,
    SkillSandbox,
)

logger = logging.getLogger(__name__)


class SkillExecutionError(Exception):
    pass


class SkillPermissionError(SkillExecutionError):
    pass


class SkillValidationError(SkillExecutionError):
    pass


class SkillResult:
    """Structured result from a skill execution."""
    __slots__ = ("ok", "output", "error", "skill_name", "skill_version",
                 "execution_time_ms", "permissions_used", "risk_level",
                 "capability_declaration", "sandbox_dir")

    def __init__(
        self,
        *,
        ok: bool,
        output: Any = None,
        error: Optional[str] = None,
        skill_name: str = "",
        skill_version: str = "",
        execution_time_ms: int = 0,
        permissions_used: Optional[List[str]] = None,
        risk_level: str = "low",
        capability_declaration: Optional[Dict[str, Any]] = None,
        sandbox_dir: str = "",
    ):
        self.ok = ok
        self.output = output
        self.error = error
        self.skill_name = skill_name
        self.skill_version = skill_version
        self.execution_time_ms = execution_time_ms
        self.permissions_used = permissions_used or []
        self.risk_level = risk_level
        self.capability_declaration = capability_declaration
        self.sandbox_dir = sandbox_dir

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "execution_time_ms": self.execution_time_ms,
            "permissions_used": self.permissions_used,
            "risk_level": self.risk_level,
            "capability_declaration": self.capability_declaration,
            "sandbox_dir": self.sandbox_dir,
        }


class SkillRunner:
    """Executes skills with manifest validation, capability enforcement and sandbox isolation.

    Usage::

        runner = SkillRunner(registry, granted_capabilities={PermissionKind.SANDBOX_PYTHON})

        # Pre-flight: inspect what the skill will be allowed to do
        declaration = runner.declare(skill_name)
        if declaration and declaration.all_granted:
            result = await runner.execute(skill_name, params)

    The ``declare()`` → ``execute()`` two-step is the capability declaration
    protocol.  ``execute()`` also calls ``declare()`` internally, so a
    single call to ``execute()`` is valid when no pre-approval is needed.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        granted_capabilities: Optional[Set[PermissionKind]] = None,
        max_risk_level: RiskLevel = RiskLevel.HIGH,
        sandbox: Optional[SkillSandbox] = None,
        use_subprocess: bool = True,
        on_declaration: Optional[Callable[[CapabilityDeclaration], None]] = None,
    ):
        self.registry = registry
        self.granted_capabilities = granted_capabilities or set()
        self.max_risk_level = max_risk_level
        self.sandbox = sandbox or SkillSandbox()
        self.use_subprocess = use_subprocess
        self.on_declaration = on_declaration
        self._modules: Dict[str, types.ModuleType] = {}

    def grant(self, *permissions: PermissionKind) -> None:
        """Dynamically grant capabilities to this runner."""
        self.granted_capabilities.update(permissions)

    def revoke(self, *permissions: PermissionKind) -> None:
        """Revoke capabilities from this runner."""
        self.granted_capabilities -= set(permissions)

    def can_execute(self, manifest: SkillManifest) -> List[str]:
        """Pre-flight check. Returns list of blocking issues (empty = can run)."""
        issues: List[str] = []

        eff_risk = manifest.effective_risk_level
        if RISK_LEVEL_ORDER.get(eff_risk, 0) > RISK_LEVEL_ORDER.get(self.max_risk_level, 0):
            issues.append(
                f"Skill risk level ({eff_risk.value}) exceeds runner limit ({self.max_risk_level.value})"
            )

        missing = []
        for perm in manifest.permissions:
            if perm not in self.granted_capabilities:
                missing.append(perm.value)
        if missing:
            issues.append(f"Missing capabilities: {', '.join(missing)}")

        return issues

    def declare(self, skill_name: str) -> Optional[CapabilityDeclaration]:
        """Build a capability declaration for a skill without executing it.

        Returns None if the skill is not found.  The declaration shows
        exactly which permissions are granted/denied, which modules are
        allowed, and what sandbox restrictions will be applied.
        """
        manifest = self.registry.get(skill_name)
        if manifest is None:
            return None
        return self.sandbox.declare_capabilities(manifest, self.granted_capabilities)

    async def execute(
        self,
        skill_name: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[int] = None,
        skip_integrity_check: bool = False,
    ) -> SkillResult:
        """Execute a registered skill by name with full sandbox enforcement."""
        params = params or {}

        manifest = self.registry.get(skill_name)
        if manifest is None:
            return SkillResult(
                ok=False, error=f"Skill '{skill_name}' not found",
                skill_name=skill_name,
            )

        # --- Step 1: Build capability declaration ---
        declaration = self.sandbox.declare_capabilities(manifest, self.granted_capabilities)

        if self.on_declaration:
            try:
                self.on_declaration(declaration)
            except Exception:
                logger.debug("on_declaration callback failed", exc_info=True)

        logger.info("Capability declaration: %s", declaration.summary())

        # --- Step 2: Check capabilities ---
        blocking = self.can_execute(manifest)
        if blocking:
            return SkillResult(
                ok=False,
                error=f"Execution blocked: {'; '.join(blocking)}",
                skill_name=manifest.name,
                skill_version=manifest.version,
                risk_level=manifest.effective_risk_level.value,
                permissions_used=[p.value for p in manifest.permissions],
                capability_declaration=declaration.to_dict(),
            )

        if not declaration.all_granted:
            denied = declaration.denied_permissions
            return SkillResult(
                ok=False,
                error=f"Capability declaration has denied permissions: {', '.join(denied)}",
                skill_name=manifest.name,
                skill_version=manifest.version,
                risk_level=manifest.effective_risk_level.value,
                capability_declaration=declaration.to_dict(),
            )

        # --- Step 3: Validate inputs ---
        input_errors = manifest.validate_inputs(params)
        if input_errors:
            return SkillResult(
                ok=False,
                error=f"Input validation failed: {'; '.join(input_errors)}",
                skill_name=manifest.name,
                skill_version=manifest.version,
                capability_declaration=declaration.to_dict(),
            )

        # --- Step 4: Load and verify code ---
        code = self.registry.get_code(skill_name)
        if code is None:
            return SkillResult(
                ok=False, error=f"Skill code not found for '{skill_name}'",
                skill_name=manifest.name,
                skill_version=manifest.version,
                capability_declaration=declaration.to_dict(),
            )

        if not skip_integrity_check:
            actual_checksum = compute_code_checksum(code)
            if manifest.checksum and actual_checksum != manifest.checksum:
                return SkillResult(
                    ok=False,
                    error="Integrity check failed: code has been modified since registration",
                    skill_name=manifest.name,
                    skill_version=manifest.version,
                    risk_level=manifest.effective_risk_level.value,
                    capability_declaration=declaration.to_dict(),
                )

        # --- Step 4b: Static code analysis ---
        code_warnings = self.sandbox.scan_dangerous_patterns(code)
        if code_warnings:
            logger.warning(
                "Skill '%s' code warnings: %s", manifest.name, "; ".join(code_warnings),
            )

        effective_timeout = timeout or manifest.risk.max_execution_time_seconds

        # --- Step 5: Execute in sandbox ---
        start = time.monotonic()
        try:
            if manifest.language == "python":
                if self.use_subprocess:
                    output = await self._run_python_sandboxed(
                        manifest, code, params, effective_timeout, declaration,
                    )
                else:
                    output = await self._run_python_inprocess(
                        manifest, code, params, effective_timeout,
                    )
            elif manifest.language == "powershell":
                output = await self._run_powershell(manifest, code, params, effective_timeout)
            else:
                return SkillResult(
                    ok=False, error=f"Unsupported language: {manifest.language}",
                    skill_name=manifest.name, skill_version=manifest.version,
                    capability_declaration=declaration.to_dict(),
                )
            elapsed = int((time.monotonic() - start) * 1000)

            return SkillResult(
                ok=True,
                output=output,
                skill_name=manifest.name,
                skill_version=manifest.version,
                execution_time_ms=elapsed,
                permissions_used=[p.value for p in manifest.permissions],
                risk_level=manifest.effective_risk_level.value,
                capability_declaration=declaration.to_dict(),
                sandbox_dir=declaration.sandbox_dir,
            )

        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return SkillResult(
                ok=False,
                error=f"Execution timed out after {effective_timeout}s",
                skill_name=manifest.name,
                skill_version=manifest.version,
                execution_time_ms=elapsed,
                risk_level=manifest.effective_risk_level.value,
                capability_declaration=declaration.to_dict(),
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return SkillResult(
                ok=False,
                error=str(exc),
                skill_name=manifest.name,
                skill_version=manifest.version,
                execution_time_ms=elapsed,
                risk_level=manifest.effective_risk_level.value,
                capability_declaration=declaration.to_dict(),
            )

    # ------------------------------------------------------------------
    # Python execution strategies
    # ------------------------------------------------------------------

    async def _run_python_sandboxed(
        self,
        manifest: SkillManifest,
        code: str,
        params: Dict[str, Any],
        timeout: int,
        declaration: CapabilityDeclaration,
    ) -> Any:
        """Execute Python skill in a subprocess with sandbox guards."""
        params_json = json.dumps(params, default=str).replace("\\", "\\\\").replace("'", "\\'")
        wrapped = self.sandbox.wrap_skill_code(manifest, code, declaration, params_json)
        env = self.sandbox.build_environment(manifest, declaration)

        sandbox_dir = Path(declaration.sandbox_dir)
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        script_path = sandbox_dir / "skill_script.py"
        script_path.write_text(wrapped, encoding="utf-8")

        python_exe = sys.executable or "python"
        kwargs: Dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(sandbox_dir),
            "env": env,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        process = await asyncio.create_subprocess_exec(
            python_exe, str(script_path), **kwargs,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if process.returncode != 0:
            error_msg = stderr.strip()[:500] if stderr.strip() else f"Process exited with code {process.returncode}"
            raise SkillExecutionError(error_msg)

        if stdout.strip():
            try:
                result_data = json.loads(stdout)
                if isinstance(result_data, dict):
                    if result_data.get("ok"):
                        return result_data.get("output")
                    raise SkillExecutionError(result_data.get("error", "Unknown error"))
            except json.JSONDecodeError:
                return stdout.strip()

        return None

    async def _run_python_inprocess(
        self,
        manifest: SkillManifest,
        code: str,
        params: Dict[str, Any],
        timeout: int,
    ) -> Any:
        """Execute Python skill in-process (for testing or trusted skills)."""
        module = self._load_module(manifest, code)
        run_fn = getattr(module, manifest.entry_point, None)
        if run_fn is None:
            raise SkillExecutionError(
                f"Skill '{manifest.name}' has no '{manifest.entry_point}' function"
            )

        if asyncio.iscoroutinefunction(run_fn):
            return await asyncio.wait_for(run_fn(params), timeout=timeout)
        return await asyncio.wait_for(
            asyncio.to_thread(run_fn, params), timeout=timeout,
        )

    async def _run_powershell(
        self,
        manifest: SkillManifest,
        code: str,
        params: Dict[str, Any],
        timeout: int,
    ) -> Any:
        from sandbox_executor import SandboxExecutor

        executor = SandboxExecutor(default_timeout=timeout)
        params_json = json.dumps(params, default=str)
        wrapped = f"$params = '{params_json}' | ConvertFrom-Json\n{code}"
        result = await executor.execute_powershell(wrapped, timeout=timeout)
        if not result.ok:
            raise SkillExecutionError(result.error or result.stderr or "PowerShell execution failed")
        return result.stdout

    def _load_module(self, manifest: SkillManifest, code: str) -> types.ModuleType:
        cache_key = f"{manifest.name}:{manifest.checksum}"
        if cache_key in self._modules:
            return self._modules[cache_key]

        skill_dir = self.registry.skills_dir / manifest.name
        code_path = skill_dir / "code.py"

        module_name = f"agente_skill_{manifest.name.replace('.', '_').replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, str(code_path))
        if spec is None or spec.loader is None:
            raise SkillExecutionError(f"Cannot load module from {code_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._modules[cache_key] = module
        return module

    def clear_cache(self) -> None:
        """Clear the loaded module cache."""
        for key in list(self._modules):
            mod_name = self._modules[key].__name__
            sys.modules.pop(mod_name, None)
        self._modules.clear()
