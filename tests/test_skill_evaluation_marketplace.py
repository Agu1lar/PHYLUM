# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for skill evaluation gate and offline marketplace packages."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from skill_evaluation import (
    EVALUATION_FAILED,
    EVALUATION_PASSED,
    EVALUATION_PENDING,
    SkillEvaluator,
    SkillTestCase,
    save_skill_tests,
)
from skill_manifest import PermissionKind, SkillManifest
from skill_marketplace import MARKETPLACE_MANIFEST, PACKAGE_EXTENSION, export_marketplace_directory
from skill_registry import SkillRegistry
from skill_runner import SkillRunner

SAMPLE_CODE = """
def run(params):
    name = params.get("name", "world")
    return {"greeting": f"Hello, {name}!"}
"""


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="agente_eval_mkp_"))


def _manifest(name: str = "eval.greet") -> SkillManifest:
    return SkillManifest(
        name=name,
        version="1.0.0",
        display_name="Eval Greeter",
        description="Greeting skill for evaluation tests",
        permissions=[PermissionKind.SANDBOX_PYTHON],
    )


class TestSkillEvaluation:
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.registry = SkillRegistry(skills_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    @pytest.mark.asyncio
    async def test_fails_without_minimum_tests(self):
        self.registry.register(_manifest(), SAMPLE_CODE)
        report = await SkillEvaluator(self.registry, use_subprocess=False).evaluate("eval.greet")
        assert report.status == EVALUATION_FAILED
        assert not self.registry.is_agent_available("eval.greet")

    @pytest.mark.asyncio
    async def test_passes_with_valid_tests(self):
        self.registry.register(_manifest(), SAMPLE_CODE)
        save_skill_tests(
            self.tmpdir / "eval.greet",
            [SkillTestCase("greet", {"name": "Test"}, {"ok": True, "output": {"greeting": "Hello, Test!"}})],
            min_tests=1,
        )
        report = await SkillEvaluator(self.registry, use_subprocess=False).evaluate("eval.greet")
        assert report.status == EVALUATION_PASSED
        assert self.registry.is_agent_available("eval.greet")

    @pytest.mark.asyncio
    async def test_execute_blocked_until_evaluated(self):
        self.registry.register(_manifest("blocked.greet"), SAMPLE_CODE)
        runner = SkillRunner(
            self.registry,
            granted_capabilities={PermissionKind.SANDBOX_PYTHON},
            use_subprocess=False,
        )
        result = await runner.execute("blocked.greet", {"name": "X"})
        assert not result.ok
        assert "agent-available" in result.error.lower()

    @pytest.mark.asyncio
    async def test_discover_excludes_unevaluated(self):
        self.registry.register(_manifest("discover.greet"), SAMPLE_CODE)
        assert self.registry.discover_by_objective("greeting hello") == []
        self.registry.ensure_agent_ready("discover.greet", smoke_params={"name": "A"})
        matches = self.registry.discover_by_objective("greeting hello")
        assert len(matches) >= 1
        assert matches[0]["name"] == "discover.greet"

    def test_pending_status_on_register(self):
        self.registry.register(_manifest("fresh.skill"), SAMPLE_CODE)
        assert self.registry.get_evaluation_status("fresh.skill") == EVALUATION_PENDING


class TestSkillMarketplace:
    def setup_method(self):
        self.tmpdir = _tmp_dir()
        self.registry = SkillRegistry(skills_dir=self.tmpdir / "registry")

    def teardown_method(self):
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def test_export_import_offline_package(self):
        self.registry.register(_manifest("pack.greet"), SAMPLE_CODE)
        self.registry.ensure_agent_ready("pack.greet", smoke_params={"name": "Pkg"})

        export_dir = self.tmpdir / "export"
        export_marketplace_directory(self.registry, export_dir, ["pack.greet"])
        manifest = json.loads((export_dir / MARKETPLACE_MANIFEST).read_text(encoding="utf-8"))
        assert manifest["telemetry"] is False
        assert manifest["offline_only"] is True
        assert "pack.greet" in manifest["skills"]

        dest = SkillRegistry(skills_dir=self.tmpdir / "imported")
        result = dest.import_marketplace_package(export_dir, overwrite=True, run_evaluation=False)
        assert "pack.greet" in result.imported
        assert dest.get("pack.greet") is not None

    def test_export_zip_package(self):
        self.registry.register(_manifest("zip.greet"), SAMPLE_CODE)
        self.registry.ensure_agent_ready("zip.greet")
        zip_path = self.registry.export_marketplace_package(
            self.tmpdir / "bundle",
            ["zip.greet"],
            as_zip=True,
        )
        assert zip_path.suffix == PACKAGE_EXTENSION or str(zip_path).endswith(PACKAGE_EXTENSION)
