# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import pytest

from script_capability import (
    build_script_profile,
    infer_capabilities_from_code,
    parse_capability_strings,
    wrap_python_script,
)


def test_infer_network_from_imports():
    code = "import requests\nrequests.get('https://example.com')"
    inferred = infer_capabilities_from_code(code)
    assert any(p.value == "network:outbound" for p in inferred)


def test_build_profile_blocks_undeclared_network():
    code = "import requests\nprint(requests.get('https://x.com').status_code)"
    profile = build_script_profile(code, script_id="s1", capabilities=["filesystem:read"])
    assert profile.violations
    assert any("network:outbound" in v for v in profile.violations)


def test_build_profile_allows_declared_network():
    code = "import requests\nprint(1)"
    profile = build_script_profile(
        code,
        script_id="s2",
        capabilities=["filesystem:read", "filesystem:write", "network:outbound"],
    )
    assert not profile.violations


def test_wrap_adds_import_guard(tmp_path):
    code = "print('hello')"
    profile = build_script_profile(code, script_id="s3", capabilities=[])
    wrapped = wrap_python_script(code, profile, sandbox_dir=str(tmp_path))
    assert "SkillImportGuard" in wrapped or "_SkillImportGuard" in wrapped
    assert "print('hello')" in wrapped


@pytest.mark.asyncio
async def test_sandbox_executor_blocks_subprocess_without_capability(tmp_path):
    from sandbox_executor import SandboxExecutor

    executor = SandboxExecutor(root=tmp_path / "sb")
    code = "import subprocess\nsubprocess.run(['echo','hi'])"
    result = await executor.execute_python(
        code,
        capabilities=["filesystem:read", "filesystem:write"],
        work_dir=str(tmp_path / "work"),
    )
    assert not result.ok
    assert "Capability" in (result.error or "")


@pytest.mark.asyncio
async def test_sandbox_executor_runs_safe_script(tmp_path):
    from sandbox_executor import SandboxExecutor

    executor = SandboxExecutor(root=tmp_path / "sb2")
    code = "print('ok')"
    result = await executor.execute_python(
        code,
        capabilities=["filesystem:read", "filesystem:write"],
        work_dir=str(tmp_path / "work2"),
        timeout=30,
    )
    assert result.ok
    assert "ok" in result.stdout
