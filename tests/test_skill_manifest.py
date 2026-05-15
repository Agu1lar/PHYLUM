# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for the skill manifest, registry, and runner."""
from __future__ import annotations

import json
import os
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "safety"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "execution"))

from skill_manifest import (
    IOSchema,
    ParamDescriptor,
    PermissionKind,
    RiskDescriptor,
    RiskLevel,
    RISK_LEVEL_ORDER,
    SkillManifest,
    compute_code_checksum,
    manifest_from_dynamic_tool,
    validate_manifest,
)
from skill_registry import (
    SkillRegistry,
    SkillRegistryError,
    SkillIntegrityError,
    SkillNotFoundError,
)
from skill_runner import (
    SkillRunner,
    SkillResult,
)
from skill_sandbox import (
    CapabilityDeclaration,
    CapabilityGrant,
    SkillSandbox,
)

SAMPLE_CODE = """
def run(params):
    name = params.get("name", "world")
    return {"greeting": f"Hello, {name}!"}
"""

SAMPLE_ASYNC_CODE = """
import asyncio
async def run(params):
    await asyncio.sleep(0.01)
    return {"value": params.get("x", 0) * 2}
"""


def _tmp_dir():
    d = Path(tempfile.mkdtemp(prefix="agente_test_skills_"))
    return d


def _sample_manifest(**overrides) -> SkillManifest:
    defaults = {
        "name": "test.greet",
        "version": "1.0.0",
        "display_name": "Test Greeter",
        "description": "A simple greeting skill for testing",
        "permissions": [PermissionKind.SANDBOX_PYTHON],
        "inputs": IOSchema(params=[
            ParamDescriptor(name="name", type="string", required=False, default="world"),
        ]),
        "outputs": IOSchema(params=[
            ParamDescriptor(name="greeting", type="string", required=True),
        ]),
        "risk": RiskDescriptor(
            level=RiskLevel.LOW,
            rationale="Pure computation, no side effects",
            reversible=True,
        ),
        "tags": ["test", "greeting"],
    }
    defaults.update(overrides)
    return SkillManifest(**defaults)


# ---------------------------------------------------------------------------
# SkillManifest tests
# ---------------------------------------------------------------------------

class TestSkillManifest:
    def test_basic_creation(self):
        m = _sample_manifest()
        assert m.name == "test.greet"
        assert m.version == "1.0.0"
        assert m.effective_risk_level == RiskLevel.LOW

    def test_invalid_name_raises(self):
        with pytest.raises(Exception):
            SkillManifest(name="INVALID NAME!", version="1.0.0")

    def test_invalid_version_raises(self):
        with pytest.raises(Exception):
            SkillManifest(name="test.skill", version="abc")

    def test_effective_risk_from_permissions(self):
        m = _sample_manifest(permissions=[PermissionKind.FILESYSTEM_DELETE])
        assert m.effective_risk_level == RiskLevel.HIGH

    def test_effective_risk_medium_from_shell(self):
        m = _sample_manifest(permissions=[PermissionKind.SHELL_RUN])
        assert m.effective_risk_level == RiskLevel.MEDIUM

    def test_requires_approval_for_medium(self):
        m = _sample_manifest(permissions=[PermissionKind.SHELL_RUN])
        assert m.requires_approval

    def test_no_approval_for_low(self):
        m = _sample_manifest(permissions=[PermissionKind.SANDBOX_PYTHON])
        assert not m.requires_approval

    def test_explicit_requires_approval(self):
        m = _sample_manifest(
            risk=RiskDescriptor(level=RiskLevel.LOW, requires_approval=True),
        )
        assert m.requires_approval

    def test_to_dict_and_from_dict(self):
        m = _sample_manifest()
        d = m.to_dict()
        assert d["name"] == "test.greet"
        assert "effective_risk_level" in d
        m2 = SkillManifest.from_dict(d)
        assert m2.name == m.name
        assert m2.version == m.version

    def test_checksum_computation(self):
        cs = compute_code_checksum("hello world")
        assert len(cs) == 64
        assert cs == compute_code_checksum("hello world")
        assert cs != compute_code_checksum("different code")

    def test_validate_inputs_ok(self):
        m = _sample_manifest()
        errors = m.validate_inputs({"name": "Alice"})
        assert errors == []

    def test_validate_inputs_missing_required(self):
        m = _sample_manifest(inputs=IOSchema(params=[
            ParamDescriptor(name="path", type="string", required=True),
        ]))
        errors = m.validate_inputs({})
        assert len(errors) == 1
        assert "path" in errors[0]

    def test_validate_inputs_enum_violation(self):
        m = _sample_manifest(inputs=IOSchema(params=[
            ParamDescriptor(name="mode", type="string", required=True, enum=["fast", "slow"]),
        ]))
        errors = m.validate_inputs({"mode": "turbo"})
        assert len(errors) == 1

    def test_param_name_validation(self):
        with pytest.raises(Exception):
            ParamDescriptor(name="123invalid", type="string")


class TestRiskDescriptor:
    def test_effective_level_no_permissions(self):
        r = RiskDescriptor(level=RiskLevel.LOW)
        assert r.effective_level([]) == RiskLevel.LOW

    def test_effective_level_elevated_by_perm(self):
        r = RiskDescriptor(level=RiskLevel.LOW)
        assert r.effective_level([PermissionKind.SHELL_ADMIN]) == RiskLevel.HIGH

    def test_effective_level_keeps_higher_declared(self):
        r = RiskDescriptor(level=RiskLevel.CRITICAL)
        assert r.effective_level([PermissionKind.SANDBOX_PYTHON]) == RiskLevel.CRITICAL

    def test_side_effects_list(self):
        r = RiskDescriptor(side_effects=["writes to temp dir", "spawns subprocess"])
        assert len(r.side_effects) == 2


class TestIOSchema:
    def test_required_params(self):
        schema = IOSchema(params=[
            ParamDescriptor(name="a", required=True),
            ParamDescriptor(name="b", required=False),
            ParamDescriptor(name="c", required=True),
        ])
        assert len(schema.required_params) == 2

    def test_validate_empty_schema(self):
        schema = IOSchema()
        assert schema.validate_input({"anything": True}) == []

    def test_sensitive_param(self):
        schema = IOSchema(params=[
            ParamDescriptor(name="api_key", type="string", sensitive=True),
        ])
        assert schema.params[0].sensitive


class TestValidateManifest:
    def test_valid_manifest(self):
        m = _sample_manifest()
        issues = validate_manifest(m, code=SAMPLE_CODE)
        assert not any("Checksum mismatch" in i for i in issues)

    def test_missing_description_warning(self):
        m = _sample_manifest(description="")
        issues = validate_manifest(m)
        assert any("description" in i.lower() for i in issues)

    def test_high_risk_no_rationale(self):
        m = _sample_manifest(
            permissions=[PermissionKind.FILESYSTEM_DELETE],
            risk=RiskDescriptor(level=RiskLevel.HIGH, rationale=""),
        )
        issues = validate_manifest(m)
        assert any("rationale" in i.lower() for i in issues)

    def test_checksum_mismatch(self):
        m = _sample_manifest()
        m.checksum = "deadbeef" * 8
        issues = validate_manifest(m, code=SAMPLE_CODE)
        assert any("Checksum mismatch" in i for i in issues)

    def test_sensitive_input_no_exposure(self):
        m = _sample_manifest(
            inputs=IOSchema(params=[
                ParamDescriptor(name="secret", type="string", sensitive=True),
            ]),
            risk=RiskDescriptor(data_exposure="none"),
        )
        issues = validate_manifest(m)
        assert any("data_exposure" in i.lower() for i in issues)


class TestManifestFromDynamicTool:
    def test_upgrade_python_tool(self):
        spec = {
            "tool_id": "dyn_abc123",
            "name": "My Cool Tool",
            "description": "Does cool stuff",
            "code": "def run(p): return 42",
            "language": "python",
            "tags": ["utility"],
        }
        m = manifest_from_dynamic_tool(spec)
        assert m.name == "my_cool_tool"
        assert PermissionKind.SANDBOX_PYTHON in m.permissions
        assert m.skill_id == "dyn_abc123"
        assert m.checksum == compute_code_checksum(spec["code"])

    def test_upgrade_powershell_tool(self):
        spec = {
            "tool_id": "dyn_ps1",
            "name": "ps-helper",
            "description": "PowerShell helper",
            "code": "Write-Output 'hello'",
            "language": "powershell",
            "tags": [],
        }
        m = manifest_from_dynamic_tool(spec)
        assert PermissionKind.SANDBOX_POWERSHELL in m.permissions
        assert PermissionKind.SHELL_RUN in m.permissions


# ---------------------------------------------------------------------------
# SkillRegistry tests
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.registry = SkillRegistry(skills_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def test_register_and_get(self):
        m = _sample_manifest()
        result = self.registry.register(m, SAMPLE_CODE)
        assert result.name == "test.greet"
        assert result.checksum == compute_code_checksum(SAMPLE_CODE)

        loaded = self.registry.get("test.greet")
        assert loaded is not None
        assert loaded.name == "test.greet"
        assert loaded.version == "1.0.0"

    def test_register_duplicate_raises(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        with pytest.raises(SkillRegistryError, match="already exists"):
            self.registry.register(m, SAMPLE_CODE)

    def test_register_overwrite(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        m2 = _sample_manifest(version="2.0.0")
        result = self.registry.register(m2, SAMPLE_CODE, overwrite=True)
        assert result.version == "2.0.0"

    def test_update(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        m2 = _sample_manifest(version="1.1.0")
        result = self.registry.update(m2, SAMPLE_CODE)
        assert result.version == "1.1.0"

    def test_unregister(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        assert self.registry.unregister("test.greet")
        assert self.registry.get("test.greet") is None
        assert not self.registry.unregister("test.greet")

    def test_get_code(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        code = self.registry.get_code("test.greet")
        assert code == SAMPLE_CODE

    def test_get_nonexistent(self):
        assert self.registry.get("nonexistent") is None
        assert self.registry.get_code("nonexistent") is None

    def test_verify_integrity_pass(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        assert self.registry.verify_integrity("test.greet")

    def test_verify_integrity_fail(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        code_path = self.tmpdir / "test.greet" / "code.py"
        code_path.write_text("# tampered code", encoding="utf-8")
        assert not self.registry.verify_integrity("test.greet")

    def test_list_skills(self):
        self.registry.register(_sample_manifest(name="skill.a", tags=["alpha"]), SAMPLE_CODE)
        self.registry.register(_sample_manifest(name="skill.b", tags=["beta"]), SAMPLE_CODE)
        self.registry.register(_sample_manifest(name="skill.c", tags=["alpha", "gamma"]), SAMPLE_CODE)

        all_skills = self.registry.list_skills()
        assert len(all_skills) == 3

        alpha = self.registry.list_skills(tag="alpha")
        assert len(alpha) == 2

        beta = self.registry.list_skills(tag="beta")
        assert len(beta) == 1

    def test_list_by_risk(self):
        self.registry.register(
            _sample_manifest(name="skill.safe", permissions=[PermissionKind.SANDBOX_PYTHON]),
            SAMPLE_CODE,
        )
        self.registry.register(
            _sample_manifest(name="skill.risky", permissions=[PermissionKind.FILESYSTEM_DELETE]),
            SAMPLE_CODE,
        )
        low_only = self.registry.list_skills(max_risk=RiskLevel.LOW)
        assert len(low_only) == 1
        assert low_only[0]["name"] == "skill.safe"

    def test_list_by_permission(self):
        self.registry.register(
            _sample_manifest(name="skill.x", permissions=[PermissionKind.SANDBOX_PYTHON]),
            SAMPLE_CODE,
        )
        self.registry.register(
            _sample_manifest(name="skill.y", permissions=[PermissionKind.SHELL_RUN]),
            SAMPLE_CODE,
        )
        result = self.registry.list_skills(permission=PermissionKind.SHELL_RUN)
        assert len(result) == 1
        assert result[0]["name"] == "skill.y"

    def test_search(self):
        self.registry.register(_sample_manifest(name="file.organizer", display_name="File Organizer"), SAMPLE_CODE)
        self.registry.register(_sample_manifest(name="email.sender", display_name="Email Sender"), SAMPLE_CODE)
        results = self.registry.search("file")
        assert len(results) == 1
        assert results[0]["name"] == "file.organizer"

    def test_search_by_tag(self):
        self.registry.register(_sample_manifest(name="skill.t", tags=["automation", "office"]), SAMPLE_CODE)
        results = self.registry.search("office")
        assert len(results) == 1

    def test_count_and_names(self):
        self.registry.register(_sample_manifest(name="a.skill"), SAMPLE_CODE)
        self.registry.register(_sample_manifest(name="b.skill"), SAMPLE_CODE)
        assert self.registry.count == 2
        assert set(self.registry.names) == {"a.skill", "b.skill"}

    def test_export_and_import(self):
        self.registry.register(_sample_manifest(name="exportable"), SAMPLE_CODE)

        export_dir = _tmp_dir()
        try:
            self.registry.export_skill("exportable", export_dir)
            assert (export_dir / "exportable" / "manifest.json").exists()

            registry2 = SkillRegistry(skills_dir=_tmp_dir())
            imported = registry2.import_skill(export_dir / "exportable")
            assert imported.name == "exportable"
            assert registry2.count == 1
            shutil.rmtree(str(registry2.skills_dir), ignore_errors=True)
        finally:
            shutil.rmtree(str(export_dir), ignore_errors=True)

    def test_export_nonexistent_raises(self):
        with pytest.raises(SkillNotFoundError):
            self.registry.export_skill("nope", _tmp_dir())

    def test_clear(self):
        self.registry.register(_sample_manifest(name="a.skill"), SAMPLE_CODE)
        self.registry.register(_sample_manifest(name="b.skill"), SAMPLE_CODE)
        count = self.registry.clear()
        assert count == 2
        assert self.registry.count == 0

    def test_persistence_across_instances(self):
        self.registry.register(_sample_manifest(name="persistent"), SAMPLE_CODE)
        registry2 = SkillRegistry(skills_dir=self.tmpdir)
        assert registry2.get("persistent") is not None


# ---------------------------------------------------------------------------
# SkillRunner tests
# ---------------------------------------------------------------------------

class TestSkillRunner:
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.registry = SkillRegistry(skills_dir=self.tmpdir)
        self.runner = SkillRunner(
            self.registry,
            granted_capabilities={PermissionKind.SANDBOX_PYTHON},
            use_subprocess=False,
        )

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def _reg(self, manifest: SkillManifest, code: str = SAMPLE_CODE, **ready_kw) -> SkillManifest:
        result = self.registry.register(manifest, code)
        self.registry.ensure_agent_ready(manifest.name, **ready_kw)
        return result

    @pytest.mark.asyncio
    async def test_execute_simple_skill(self):
        m = _sample_manifest()
        self._reg(m, SAMPLE_CODE)
        result = await self.runner.execute("test.greet", {"name": "Alice"})
        assert result.ok
        assert result.output == {"greeting": "Hello, Alice!"}
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_execute_async_skill(self):
        m = _sample_manifest(name="test.async")
        self._reg(
            m, SAMPLE_ASYNC_CODE,
            smoke_params={"x": 21},
            smoke_expect={"ok": True, "output": {"value": 42}},
        )
        result = await self.runner.execute("test.async", {"x": 21})
        assert result.ok
        assert result.output == {"value": 42}

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        result = await self.runner.execute("nonexistent")
        assert not result.ok
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_missing_capability(self):
        m = _sample_manifest(name="test.risky", permissions=[PermissionKind.SHELL_RUN])
        self.registry.register(m, SAMPLE_CODE)
        result = await self.runner.execute("test.risky")
        assert not result.ok
        assert "Missing capabilities" in result.error

    @pytest.mark.asyncio
    async def test_execute_after_granting_capability(self):
        m = _sample_manifest(name="test.shell", permissions=[PermissionKind.SHELL_RUN])
        self._reg(m, SAMPLE_CODE)
        self.runner.grant(PermissionKind.SHELL_RUN)
        result = await self.runner.execute("test.shell", {"name": "admin"})
        assert result.ok

    @pytest.mark.asyncio
    async def test_execute_risk_too_high(self):
        runner = SkillRunner(
            self.registry,
            granted_capabilities={PermissionKind.SANDBOX_PYTHON, PermissionKind.FILESYSTEM_DELETE},
            max_risk_level=RiskLevel.LOW,
        )
        m = _sample_manifest(name="test.deleter", permissions=[PermissionKind.FILESYSTEM_DELETE])
        self.registry.register(m, SAMPLE_CODE)
        result = await runner.execute("test.deleter")
        assert not result.ok
        assert "risk level" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_input_validation_fails(self):
        m = _sample_manifest(
            name="test.strict",
            inputs=IOSchema(params=[
                ParamDescriptor(name="path", type="string", required=True),
            ]),
        )
        self.registry.register(m, SAMPLE_CODE)
        result = await self.runner.execute("test.strict", {})
        assert not result.ok
        assert "validation failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_integrity_check_fails(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        code_path = self.tmpdir / "test.greet" / "code.py"
        code_path.write_text("# tampered", encoding="utf-8")
        result = await self.runner.execute("test.greet")
        assert not result.ok
        assert "altered" in result.error.lower() or "integrity" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_skip_integrity(self):
        m = _sample_manifest()
        self._reg(m, SAMPLE_CODE)
        code_path = self.tmpdir / "test.greet" / "code.py"
        code_path.write_text(SAMPLE_CODE + "\n# extra", encoding="utf-8")
        result = await self.runner.execute("test.greet", skip_integrity_check=True)
        assert result.ok

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        slow_code = "import time\ndef run(p): time.sleep(10)"
        m = _sample_manifest(
            name="test.slow",
            risk=RiskDescriptor(max_execution_time_seconds=1),
        )
        self.registry.register(m, slow_code)
        self.registry.mark_evaluation_passed("test.slow")
        result = await self.runner.execute("test.slow", timeout=1)
        assert not result.ok
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_runtime_error(self):
        bad_code = "def run(p): raise ValueError('intentional')"
        m = _sample_manifest(name="test.bad")
        self.registry.register(m, bad_code)
        self.registry.mark_evaluation_passed("test.bad")
        result = await self.runner.execute("test.bad")
        assert not result.ok
        assert "intentional" in result.error

    def test_can_execute_check(self):
        m = _sample_manifest(permissions=[PermissionKind.SHELL_ADMIN])
        issues = self.runner.can_execute(m)
        assert len(issues) >= 1

    def test_can_execute_all_granted(self):
        m = _sample_manifest()
        issues = self.runner.can_execute(m)
        assert issues == []

    def test_grant_and_revoke(self):
        self.runner.grant(PermissionKind.NETWORK_OUTBOUND)
        assert PermissionKind.NETWORK_OUTBOUND in self.runner.granted_capabilities
        self.runner.revoke(PermissionKind.NETWORK_OUTBOUND)
        assert PermissionKind.NETWORK_OUTBOUND not in self.runner.granted_capabilities


class TestSkillResult:
    def test_to_dict(self):
        r = SkillResult(
            ok=True, output={"key": "value"},
            skill_name="test", skill_version="1.0.0",
            execution_time_ms=42,
            permissions_used=["sandbox:python"],
            risk_level="low",
        )
        d = r.to_dict()
        assert d["ok"] is True
        assert d["execution_time_ms"] == 42


# ---------------------------------------------------------------------------
# Integration: canonical_tools recognizes "skill"
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SkillSandbox tests
# ---------------------------------------------------------------------------

class TestSkillSandbox:
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.sandbox = SkillSandbox(sandbox_root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def test_declare_all_granted(self):
        m = _sample_manifest(permissions=[PermissionKind.SANDBOX_PYTHON])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        assert decl.all_granted
        assert len(decl.denied_permissions) == 0
        assert decl.skill_name == "test.greet"

    def test_declare_missing_capability(self):
        m = _sample_manifest(permissions=[PermissionKind.SHELL_RUN, PermissionKind.SANDBOX_PYTHON])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        assert not decl.all_granted
        assert "shell:run" in decl.denied_permissions

    def test_declare_filesystem_scope_read_only(self):
        m = _sample_manifest(permissions=[PermissionKind.FILESYSTEM_READ])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.FILESYSTEM_READ})
        assert decl.filesystem_scope == "read_only"

    def test_declare_filesystem_scope_read_write(self):
        m = _sample_manifest(permissions=[PermissionKind.FILESYSTEM_READ, PermissionKind.FILESYSTEM_WRITE])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.FILESYSTEM_READ, PermissionKind.FILESYSTEM_WRITE})
        assert decl.filesystem_scope == "read_write"

    def test_declare_filesystem_scope_full(self):
        m = _sample_manifest(permissions=[PermissionKind.FILESYSTEM_DELETE])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.FILESYSTEM_DELETE})
        assert decl.filesystem_scope == "read_write_delete"

    def test_declare_filesystem_scope_sandbox_only(self):
        m = _sample_manifest(permissions=[PermissionKind.SANDBOX_PYTHON])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        assert decl.filesystem_scope == "sandbox_only"

    def test_declare_network_none(self):
        m = _sample_manifest(permissions=[PermissionKind.SANDBOX_PYTHON])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        assert decl.network_access == "none"

    def test_declare_network_outbound(self):
        m = _sample_manifest(permissions=[PermissionKind.NETWORK_OUTBOUND])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.NETWORK_OUTBOUND})
        assert decl.network_access == "outbound"

    def test_declare_network_full(self):
        m = _sample_manifest(permissions=[PermissionKind.NETWORK_OUTBOUND, PermissionKind.NETWORK_INBOUND])
        decl = self.sandbox.declare_capabilities(
            m, {PermissionKind.NETWORK_OUTBOUND, PermissionKind.NETWORK_INBOUND},
        )
        assert decl.network_access == "full"

    def test_declare_modules_allowed_for_shell(self):
        m = _sample_manifest(permissions=[PermissionKind.SHELL_RUN])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SHELL_RUN})
        assert "subprocess" in decl.allowed_modules
        assert "os" in decl.allowed_modules

    def test_declare_modules_denied_without_permission(self):
        m = _sample_manifest(permissions=[PermissionKind.SANDBOX_PYTHON])
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        assert "subprocess" in decl.denied_modules
        assert "socket" in decl.denied_modules

    def test_declare_restricted_builtins(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        assert "exec" in decl.restricted_builtins
        assert "eval" in decl.restricted_builtins
        assert "__import__" in decl.restricted_builtins

    def test_to_dict(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        d = decl.to_dict()
        assert d["skill_name"] == "test.greet"
        assert "all_granted" in d
        assert "allowed_modules" in d
        assert "denied_modules" in d
        assert isinstance(d["grants"], list)

    def test_summary_string(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        s = decl.summary()
        assert "test.greet" in s
        assert "risk=" in s
        assert "permissions:" in s

    def test_build_environment(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        env = self.sandbox.build_environment(m, decl)
        assert env["AGENTE_SKILL_SANDBOX"] == "1"
        assert env["AGENTE_SKILL_NAME"] == "test.greet"
        assert env["AGENTE_SKILL_VERSION"] == "1.0.0"
        assert "AGENTE_SKILL_PERMISSIONS" in env
        assert "AGENTE_SKILL_ALLOWED_MODULES" in env
        assert "AGENTE_SKILL_DENIED_MODULES" in env
        assert "AGENTE_SKILL_FS_SCOPE" in env
        assert "AGENTE_SKILL_NET_ACCESS" in env

    def test_build_import_guard_code(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        code = self.sandbox.build_import_guard_code(decl)
        assert "_SkillImportGuard" in code
        assert "meta_path" in code
        assert "ImportError" in code

    def test_scan_dangerous_patterns_clean(self):
        warnings = self.sandbox.scan_dangerous_patterns("def run(p): return p")
        assert warnings == []

    def test_scan_dangerous_patterns_exec(self):
        warnings = self.sandbox.scan_dangerous_patterns("exec('print(1)')")
        assert any("exec()" in w for w in warnings)

    def test_scan_dangerous_patterns_eval(self):
        warnings = self.sandbox.scan_dangerous_patterns("x = eval('1+1')")
        assert any("eval()" in w for w in warnings)

    def test_scan_dangerous_patterns_import(self):
        warnings = self.sandbox.scan_dangerous_patterns("m = __import__('os')")
        assert any("__import__()" in w for w in warnings)

    def test_build_scoped_fs_code(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        decl.filesystem_scope = "read_write"
        code = self.sandbox.build_scoped_fs_code(decl)
        assert "ScopedFS" in code
        assert "resolve" in code
        assert "PermissionError" in code

    def test_wrap_skill_code(self):
        m = _sample_manifest()
        decl = self.sandbox.declare_capabilities(m, {PermissionKind.SANDBOX_PYTHON})
        wrapped = self.sandbox.wrap_skill_code(m, SAMPLE_CODE, decl, '{"name": "test"}')
        assert "_SkillImportGuard" in wrapped
        assert "run(_params)" in wrapped
        assert "Skill Sandbox Preamble" in wrapped

    def test_create_sandbox_dir(self):
        m = _sample_manifest()
        sandbox_dir = self.sandbox.create_sandbox_dir(m)
        assert sandbox_dir.exists()
        assert sandbox_dir.is_dir()

    def test_cleanup(self):
        m = _sample_manifest()
        sandbox_dir = self.sandbox.create_sandbox_dir(m)
        assert sandbox_dir.exists()
        self.sandbox.cleanup(sandbox_dir)
        assert not sandbox_dir.exists()


class TestCapabilityDeclaration:
    def test_all_granted_true(self):
        decl = CapabilityDeclaration(
            skill_name="test", skill_version="1.0.0",
            risk_level="low", requires_approval=False,
            grants=[
                CapabilityGrant(permission="sandbox:python", granted=True),
            ],
        )
        assert decl.all_granted

    def test_all_granted_false(self):
        decl = CapabilityDeclaration(
            skill_name="test", skill_version="1.0.0",
            risk_level="low", requires_approval=False,
            grants=[
                CapabilityGrant(permission="sandbox:python", granted=True),
                CapabilityGrant(permission="shell:run", granted=False, reason="Not granted"),
            ],
        )
        assert not decl.all_granted
        assert decl.denied_permissions == ["shell:run"]


# ---------------------------------------------------------------------------
# SkillRunner with sandbox tests
# ---------------------------------------------------------------------------

class TestSkillRunnerSandbox:
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.registry = SkillRegistry(skills_dir=self.tmpdir / "skills")
        self.sandbox = SkillSandbox(sandbox_root=self.tmpdir / "sandbox")
        self.runner = SkillRunner(
            self.registry,
            granted_capabilities={PermissionKind.SANDBOX_PYTHON},
            sandbox=self.sandbox,
            use_subprocess=False,
        )

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def _reg(self, manifest: SkillManifest, code: str = SAMPLE_CODE, **ready_kw) -> SkillManifest:
        result = self.registry.register(manifest, code)
        self.registry.ensure_agent_ready(manifest.name, **ready_kw)
        return result

    def test_declare_returns_declaration(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        decl = self.runner.declare("test.greet")
        assert decl is not None
        assert decl.skill_name == "test.greet"
        assert decl.all_granted

    def test_declare_nonexistent_returns_none(self):
        assert self.runner.declare("nonexistent") is None

    def test_declare_shows_denied_capabilities(self):
        m = _sample_manifest(name="test.net", permissions=[PermissionKind.NETWORK_OUTBOUND])
        self.registry.register(m, SAMPLE_CODE)
        decl = self.runner.declare("test.net")
        assert decl is not None
        assert not decl.all_granted
        assert "network:outbound" in decl.denied_permissions

    @pytest.mark.asyncio
    async def test_execute_includes_capability_declaration(self):
        m = _sample_manifest()
        self._reg(m, SAMPLE_CODE)
        result = await self.runner.execute("test.greet", {"name": "World"})
        assert result.ok
        assert result.capability_declaration is not None
        assert result.capability_declaration["all_granted"] is True

    @pytest.mark.asyncio
    async def test_execute_blocked_by_denied_capabilities(self):
        m = _sample_manifest(name="test.shell", permissions=[PermissionKind.SHELL_RUN])
        self.registry.register(m, SAMPLE_CODE)
        result = await self.runner.execute("test.shell")
        assert not result.ok
        assert "Missing capabilities" in result.error
        assert result.capability_declaration is not None

    @pytest.mark.asyncio
    async def test_on_declaration_callback(self):
        declarations = []
        runner = SkillRunner(
            self.registry,
            granted_capabilities={PermissionKind.SANDBOX_PYTHON},
            sandbox=self.sandbox,
            use_subprocess=False,
            on_declaration=lambda d: declarations.append(d),
        )
        m = _sample_manifest()
        self._reg(m, SAMPLE_CODE)
        await runner.execute("test.greet", {"name": "CB"})
        assert len(declarations) == 1
        assert declarations[0].skill_name == "test.greet"

    @pytest.mark.asyncio
    async def test_execute_simple(self):
        m = _sample_manifest()
        self._reg(m, SAMPLE_CODE)
        result = await self.runner.execute("test.greet", {"name": "Alice"})
        assert result.ok
        assert result.output == {"greeting": "Hello, Alice!"}
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_execute_async_skill(self):
        m = _sample_manifest(name="test.async")
        self._reg(
            m, SAMPLE_ASYNC_CODE,
            smoke_params={"x": 21},
            smoke_expect={"ok": True, "output": {"value": 42}},
        )
        result = await self.runner.execute("test.async", {"x": 21})
        assert result.ok
        assert result.output == {"value": 42}

    @pytest.mark.asyncio
    async def test_execute_integrity_check_fails(self):
        m = _sample_manifest()
        self.registry.register(m, SAMPLE_CODE)
        code_path = self.tmpdir / "skills" / "test.greet" / "code.py"
        code_path.write_text("# tampered", encoding="utf-8")
        result = await self.runner.execute("test.greet")
        assert not result.ok
        assert "altered" in result.error.lower() or "integrity" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        result = await self.runner.execute("nonexistent")
        assert not result.ok
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_input_validation_fails(self):
        m = _sample_manifest(
            name="test.strict",
            inputs=IOSchema(params=[
                ParamDescriptor(name="path", type="string", required=True),
            ]),
        )
        self.registry.register(m, SAMPLE_CODE)
        result = await self.runner.execute("test.strict", {})
        assert not result.ok
        assert "validation failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        slow_code = "import time\ndef run(p): time.sleep(10)"
        m = _sample_manifest(
            name="test.slow",
            risk=RiskDescriptor(max_execution_time_seconds=1),
        )
        self.registry.register(m, slow_code)
        self.registry.mark_evaluation_passed("test.slow")
        result = await self.runner.execute("test.slow", timeout=1)
        assert not result.ok
        assert "timed out" in result.error.lower()


class TestSkillRunnerSubprocess:
    """Test subprocess-based sandbox execution."""
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.registry = SkillRegistry(skills_dir=self.tmpdir / "skills")
        self.sandbox = SkillSandbox(sandbox_root=self.tmpdir / "sandbox")
        self.runner = SkillRunner(
            self.registry,
            granted_capabilities={PermissionKind.SANDBOX_PYTHON},
            sandbox=self.sandbox,
            use_subprocess=True,
        )

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def _reg(self, manifest: SkillManifest, code: str = SAMPLE_CODE, **ready_kw) -> SkillManifest:
        result = self.registry.register(manifest, code)
        self.registry.ensure_agent_ready(manifest.name, **ready_kw)
        return result

    @pytest.mark.asyncio
    async def test_subprocess_execution(self):
        simple_code = "def run(params):\n    return {'result': params.get('x', 0) + 1}\n"
        m = _sample_manifest(name="test.sub")
        self._reg(m, simple_code)
        result = await self.runner.execute("test.sub", {"x": 41})
        assert result.ok
        assert result.output == {"result": 42}
        assert result.capability_declaration is not None
        assert result.sandbox_dir != ""

    @pytest.mark.asyncio
    async def test_subprocess_error_handling(self):
        bad_code = "def run(p):\n    raise ValueError('sandbox test error')\n"
        m = _sample_manifest(name="test.bad")
        self.registry.register(m, bad_code)
        self.registry.mark_evaluation_passed("test.bad")
        result = await self.runner.execute("test.bad")
        assert not result.ok
        assert "sandbox test error" in result.error

    @pytest.mark.asyncio
    async def test_subprocess_with_declaration(self):
        simple_code = "def run(params):\n    return 'ok'\n"
        m = _sample_manifest(name="test.decl")
        self._reg(m, simple_code)

        decl = self.runner.declare("test.decl")
        assert decl is not None
        assert decl.all_granted

        result = await self.runner.execute("test.decl")
        assert result.ok


class TestSkillResult:
    def test_to_dict(self):
        r = SkillResult(
            ok=True, output={"key": "value"},
            skill_name="test", skill_version="1.0.0",
            execution_time_ms=42,
            permissions_used=["sandbox:python"],
            risk_level="low",
            capability_declaration={"all_granted": True},
            sandbox_dir="/tmp/sandbox",
        )
        d = r.to_dict()
        assert d["ok"] is True
        assert d["execution_time_ms"] == 42
        assert d["capability_declaration"] == {"all_granted": True}
        assert d["sandbox_dir"] == "/tmp/sandbox"


# ---------------------------------------------------------------------------
# Integration: canonical_tools recognizes "skill"
# ---------------------------------------------------------------------------

class TestSkillCanonicalIntegration:
    def test_skill_in_supported_tools(self):
        from canonical_tools import supported_tools
        assert "skill" in supported_tools()

    def test_skill_action_metadata(self):
        from canonical_tools import action_metadata
        meta = action_metadata("skill", "register")
        assert meta["semantic_type"] == "mutation"
        assert meta["mutates_state"] is True

    def test_skill_action_metadata_get(self):
        from canonical_tools import action_metadata
        meta = action_metadata("skill", "get")
        assert meta["semantic_type"] == "inspection"
        assert meta["mutates_state"] is False

    def test_skill_task_title(self):
        from canonical_tools import task_title
        title = task_title("skill", "register", {"name": "my.skill"})
        assert "my.skill" in title
        assert "Skill" in title

    def test_normalize_agentic_task_skill(self):
        from canonical_tools import normalize_agentic_task
        task = normalize_agentic_task(
            "skill",
            {"action": "execute", "name": "test.greet", "params": {"name": "Bob"}, "timeout": 30},
            "task-sk-1",
        )
        assert task["tool"] == "skill"
        assert task["action"] == "execute"
        assert task["params"]["name"] == "test.greet"
        assert task["params"]["params"] == {"name": "Bob"}
        assert task["params"]["timeout"] == 30
