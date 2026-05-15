# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for skill discovery by objective and signing/provenance."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from skill_discovery import discover_skills_for_objective, score_skill_for_objective
from skill_manifest import IOSchema, ParamDescriptor, PermissionKind, SkillManifest
from skill_registry import SkillRegistry
from skill_runner import SkillRunner
from skill_signing import (
    SkillTrustStatus,
    compute_bundle_checksum,
    sign_bundle,
    verify_bundle_signature,
)

SAMPLE_CODE = """
def run(params):
    return {"rows": params.get("path", "")}
"""


def _tmp_dir():
    return Path(tempfile.mkdtemp(prefix="agente_skill_ds_"))


def _excel_manifest(name: str = "office.read_excel") -> SkillManifest:
    return SkillManifest(
        name=name,
        version="1.0.0",
        display_name="Read Excel",
        description="Read Excel spreadsheet ranges and export JSON",
        permissions=[PermissionKind.SANDBOX_PYTHON],
        tags=["office", "excel", "spreadsheet"],
        inputs=IOSchema(params=[
            ParamDescriptor(name="path", type="string", required=True, description="xlsx file path"),
        ]),
    )


def _printer_manifest() -> SkillManifest:
    return SkillManifest(
        name="network.list_printers",
        version="1.0.0",
        display_name="List Printers",
        description="Discover network printers via PowerShell",
        permissions=[PermissionKind.SHELL_RUN],
        tags=["printer", "network"],
    )


class TestSkillDiscovery:
    def test_scores_excel_skill_for_spreadsheet_objective(self):
        manifest = _excel_manifest()
        match = score_skill_for_objective(manifest, "read data from an Excel spreadsheet file")
        assert match.score > 0.2
        assert "office.read_excel" == match.name

    def test_discover_ranks_best_match_first(self):
        registry_dir = _tmp_dir()
        reg = SkillRegistry(skills_dir=registry_dir)
        reg.register(_excel_manifest(), SAMPLE_CODE)
        reg.register(_printer_manifest(), "def run(p): return []")
        reg.ensure_agent_ready("office.read_excel", smoke_params={"path": "book.xlsx"})
        reg.ensure_agent_ready("network.list_printers")

        matches = reg.discover_by_objective("open excel file and read the sales sheet")
        assert len(matches) >= 1
        assert matches[0]["name"] == "office.read_excel"
        assert matches[0]["score"] > matches[-1]["score"] or len(matches) == 1

    def test_discover_empty_registry(self):
        reg = SkillRegistry(skills_dir=_tmp_dir())
        assert reg.discover_by_objective("anything") == []


class TestSkillSigning:
    def test_sign_and_verify_bundle(self):
        skills_dir = _tmp_dir()
        manifest = {"name": "test.skill", "version": "1.0.0", "checksum": "abc123"}
        bundle = compute_bundle_checksum(manifest, "abc123")
        sig = sign_bundle(bundle, skills_dir=skills_dir)
        assert verify_bundle_signature(bundle, sig, skills_dir=skills_dir)

    def test_tampered_signature_fails(self):
        skills_dir = _tmp_dir()
        bundle = compute_bundle_checksum({"name": "x"}, "code")
        sig = sign_bundle(bundle, skills_dir=skills_dir)
        assert not verify_bundle_signature(bundle, "deadbeef", skills_dir=skills_dir)

    def test_register_signs_skill(self):
        reg = SkillRegistry(skills_dir=_tmp_dir())
        m = _excel_manifest("signed.skill")
        result = reg.register(m, SAMPLE_CODE)
        assert result.signature
        assert result.manifest_checksum
        assert reg.verify_signature("signed.skill")

    def test_altered_code_quarantines_on_verify(self):
        reg = SkillRegistry(skills_dir=_tmp_dir())
        m = _excel_manifest("tampered.skill")
        reg.register(m, SAMPLE_CODE)
        code_path = reg.skills_dir / "tampered.skill" / "code.py"
        code_path.write_text(SAMPLE_CODE + "\n# injected\n", encoding="utf-8")
        assert not reg.verify_integrity("tampered.skill")
        trust = reg.verify_trust("tampered.skill")
        assert not trust["ok"]
        assert trust["reason"] == "code_altered"

    def test_import_without_valid_signature_is_untrusted(self):
        import_dir = _tmp_dir() / "imported"
        import_dir.mkdir()
        manifest = _excel_manifest("imported.skill")
        manifest.manifest_checksum = "fake"
        manifest.signature = "fake"
        (import_dir / "manifest.json").write_text(
            __import__("json").dumps(manifest.to_dict(), indent=2),
            encoding="utf-8",
        )
        (import_dir / "code.py").write_text(SAMPLE_CODE, encoding="utf-8")

        reg = SkillRegistry(skills_dir=_tmp_dir())
        imported = reg.import_skill(import_dir)
        assert imported.trust_status == SkillTrustStatus.UNTRUSTED
        trust = reg.verify_trust("imported.skill")
        assert not trust["ok"]
        assert trust["reason"] == "untrusted"

    def test_approve_trust_restores_execution(self):
        reg = SkillRegistry(skills_dir=_tmp_dir())
        m = _excel_manifest("review.skill")
        reg.register(m, SAMPLE_CODE)
        reg._set_trust_status("review.skill", SkillTrustStatus.UNTRUSTED)
        approved = reg.approve_trust("review.skill", notes="reviewed OK")
        assert approved.trust_status == SkillTrustStatus.TRUSTED
        assert reg.verify_trust("review.skill")["ok"]

    @pytest.mark.asyncio
    async def test_runner_blocks_untrusted_skill(self):
        reg = SkillRegistry(skills_dir=_tmp_dir())
        m = _excel_manifest("blocked.skill")
        reg.register(m, SAMPLE_CODE)
        reg._set_trust_status("blocked.skill", SkillTrustStatus.UNTRUSTED)
        runner = SkillRunner(reg, granted_capabilities={PermissionKind.SANDBOX_PYTHON})
        result = await runner.execute("blocked.skill", params={"path": "x.xlsx"})
        assert not result.ok
        assert "signature" in result.error.lower() or "trust" in result.error.lower() or "review" in result.error.lower()
