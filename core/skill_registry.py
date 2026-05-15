# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Local skill registry — persistent storage and querying of skill manifests.

Skills are stored in a local directory (default ``~/.agente/skills/``).
Each skill is a folder containing:
  - ``manifest.json``  — the validated SkillManifest
  - ``code.py`` or ``code.ps1`` — the actual skill code
  - ``README.md`` (optional) — human-readable docs

The registry provides:
  - Register / update / delete skills with validation
  - Query by name, tag, permission, risk level
  - Version history and integrity verification via checksums
  - Bulk export/import for offline skill exchange
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from skill_discovery import SkillMatch, discover_skills_for_objective
from skill_manifest import (
    RiskLevel,
    RISK_LEVEL_ORDER,
    PermissionKind,
    SkillManifest,
    compute_code_checksum,
    validate_manifest,
)
from skill_evaluation import EVALUATION_PENDING, EVALUATION_PASSED
from skill_signing import (
    SkillTrustStatus,
    build_provenance,
    compute_bundle_checksum,
    sign_bundle,
    verify_bundle_signature,
)

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(os.getenv("AGENTE_SKILLS_DIR", "")) or (Path.home() / ".agente" / "skills")
REGISTRY_INDEX = "registry_index.json"


class SkillRegistryError(Exception):
    pass


class SkillIntegrityError(SkillRegistryError):
    pass


class SkillNotFoundError(SkillRegistryError):
    pass


class SkillTrustError(SkillRegistryError):
    """Skill cannot run until trust is restored (signature/quarantine review)."""


class SkillRegistry:
    """Persistent local registry for skill manifests and code."""

    def __init__(self, *, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, Dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        manifest: SkillManifest,
        code: str,
        *,
        overwrite: bool = False,
        provenance: Optional[Any] = None,
        trust_on_import: bool = True,
    ) -> SkillManifest:
        """Register a new skill or update an existing one.

        Validates the manifest, computes checksums, persists code and
        manifest to disk, and updates the registry index.
        """
        issues = validate_manifest(manifest, code=code)
        critical = [i for i in issues if "Checksum mismatch" in i]
        if critical:
            raise SkillIntegrityError("; ".join(critical))

        existing = self._index.get(manifest.name)
        if existing and not overwrite:
            raise SkillRegistryError(
                f"Skill '{manifest.name}' v{existing['version']} already exists. "
                f"Use overwrite=True to update."
            )

        previous_checksum = existing.get("manifest_checksum", "") if existing else ""
        previous_version = existing.get("version", "") if existing else ""

        manifest.checksum = compute_code_checksum(code)
        manifest.updated_at = datetime.now(timezone.utc).isoformat()
        if not manifest.skill_id:
            manifest.skill_id = f"skill_{manifest.name}_{manifest.version}"

        if provenance is not None:
            manifest.provenance = provenance
        elif overwrite and previous_checksum:
            manifest.provenance = build_provenance(
                source=manifest.provenance.source or "local",
                registered_by=manifest.provenance.registered_by or "agent",
                session_id=manifest.provenance.session_id,
                parent_skill=manifest.provenance.parent_skill,
                previous_checksum=previous_checksum,
                previous_version=previous_version,
                import_path=manifest.provenance.import_path,
                notes=manifest.provenance.notes or "Updated skill package",
            )

        manifest_dict = manifest.to_dict()
        manifest.manifest_checksum = compute_bundle_checksum(manifest_dict, manifest.checksum)
        manifest.signature = sign_bundle(manifest.manifest_checksum, skills_dir=self.skills_dir)
        if trust_on_import:
            manifest.trust_status = SkillTrustStatus.TRUSTED
        elif manifest.trust_status == SkillTrustStatus.TRUSTED:
            manifest.trust_status = SkillTrustStatus.UNTRUSTED

        skill_dir = self.skills_dir / manifest.name
        skill_dir.mkdir(parents=True, exist_ok=True)

        ext = ".py" if manifest.language == "python" else ".ps1"
        (skill_dir / f"code{ext}").write_text(code, encoding="utf-8")
        (skill_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

        self._index[manifest.name] = {
            "name": manifest.name,
            "version": manifest.version,
            "display_name": manifest.display_name,
            "skill_id": manifest.skill_id,
            "risk_level": manifest.effective_risk_level.value,
            "tags": manifest.tags,
            "permissions": [p.value for p in manifest.permissions],
            "language": manifest.language,
            "checksum": manifest.checksum,
            "manifest_checksum": manifest.manifest_checksum,
            "signature": manifest.signature,
            "trust_status": manifest.trust_status.value,
            "evaluation_status": EVALUATION_PENDING,
            "agent_available": False,
            "updated_at": manifest.updated_at,
        }
        self._save_index()

        logger.info("Registered skill '%s' v%s (risk=%s)", manifest.name, manifest.version, manifest.effective_risk_level.value)
        return manifest

    def update(self, manifest: SkillManifest, code: str) -> SkillManifest:
        """Update an existing skill (convenience wrapper)."""
        return self.register(manifest, code, overwrite=True)

    def unregister(self, name: str) -> bool:
        """Remove a skill from the registry and delete its files."""
        if name not in self._index:
            return False
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            shutil.rmtree(str(skill_dir), ignore_errors=True)
        del self._index[name]
        self._save_index()
        logger.info("Unregistered skill '%s'", name)
        return True

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[SkillManifest]:
        """Load a skill manifest by name."""
        if name not in self._index:
            return None
        manifest_path = self.skills_dir / name / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return SkillManifest.from_dict(data)
        except Exception:
            logger.exception("Failed to load manifest for skill '%s'", name)
            return None

    def get_code(self, name: str) -> Optional[str]:
        """Load the skill code by name."""
        entry = self._index.get(name)
        if not entry:
            return None
        ext = ".py" if entry.get("language", "python") == "python" else ".ps1"
        code_path = self.skills_dir / name / f"code{ext}"
        if not code_path.exists():
            return None
        return code_path.read_text(encoding="utf-8")

    def verify_integrity(self, name: str) -> bool:
        """Check that the stored code matches the manifest checksum."""
        manifest = self.get(name)
        code = self.get_code(name)
        if not manifest or code is None:
            return False
        code_ok = compute_code_checksum(code) == manifest.checksum
        if not code_ok:
            self._set_trust_status(name, SkillTrustStatus.QUARANTINED)
            return False
        return True

    def verify_signature(self, name: str) -> bool:
        """Verify HMAC signature over manifest+code bundle."""
        manifest = self.get(name)
        if manifest is None:
            return False
        if not manifest.manifest_checksum or not manifest.signature:
            return False
        return verify_bundle_signature(
            manifest.manifest_checksum,
            manifest.signature,
            skills_dir=self.skills_dir,
        )

    def verify_trust(self, name: str) -> Dict[str, Any]:
        """Full trust check: code integrity + bundle signature + trust status."""
        manifest = self.get(name)
        if manifest is None:
            return {"ok": False, "reason": "not_found"}

        code = self.get_code(name)
        if code is None:
            return {"ok": False, "reason": "code_missing"}

        code_ok = compute_code_checksum(code) == manifest.checksum
        sig_ok = self.verify_signature(name)
        status = manifest.trust_status
        if isinstance(status, SkillTrustStatus):
            status_val = status.value
        else:
            status_val = str(status)

        if not code_ok:
            self._set_trust_status(name, SkillTrustStatus.QUARANTINED)
            return {
                "ok": False,
                "reason": "code_altered",
                "trust_status": SkillTrustStatus.QUARANTINED.value,
                "code_integrity": False,
                "signature_valid": sig_ok,
            }

        if status_val == SkillTrustStatus.QUARANTINED.value:
            return {
                "ok": False,
                "reason": "quarantined",
                "trust_status": status_val,
                "code_integrity": True,
                "signature_valid": sig_ok,
            }

        if status_val == SkillTrustStatus.UNTRUSTED.value:
            return {
                "ok": False,
                "reason": "untrusted",
                "trust_status": status_val,
                "code_integrity": True,
                "signature_valid": sig_ok,
            }

        if not sig_ok:
            return {
                "ok": False,
                "reason": "invalid_signature",
                "trust_status": status_val,
                "code_integrity": True,
                "signature_valid": False,
            }

        return {
            "ok": True,
            "reason": "trusted",
            "trust_status": status_val,
            "code_integrity": True,
            "signature_valid": True,
        }

    def approve_trust(self, name: str, *, notes: str = "") -> SkillManifest:
        """Re-sign and mark a skill trusted after human review."""
        manifest = self.get(name)
        code = self.get_code(name)
        if manifest is None or code is None:
            raise SkillNotFoundError(f"Skill '{name}' not found")

        prov = manifest.provenance
        prov.notes = notes or prov.notes or "Approved after review"
        return self.register(
            manifest,
            code,
            overwrite=True,
            provenance=prov,
            trust_on_import=True,
        )

    def _set_trust_status(self, name: str, status: SkillTrustStatus) -> None:
        if name not in self._index:
            return
        self._index[name]["trust_status"] = status.value
        self._save_index()
        manifest_path = self.skills_dir / name / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                data["trust_status"] = status.value
                manifest_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            except Exception:
                logger.debug("Failed to persist trust_status for '%s'", name, exc_info=True)

    def get_evaluation_status(self, name: str) -> str:
        entry = self._index.get(name)
        if not entry:
            return EVALUATION_PENDING
        return entry.get("evaluation_status", EVALUATION_PENDING)

    def set_evaluation_status(self, name: str, report) -> None:
        """Persist evaluation outcome from SkillEvaluationReport."""
        if name not in self._index:
            return
        passed = report.status == EVALUATION_PASSED
        trusted = self._index[name].get("trust_status") == SkillTrustStatus.TRUSTED.value
        self._index[name]["evaluation_status"] = report.status
        self._index[name]["evaluation_passed_at"] = report.evaluated_at if passed else ""
        self._index[name]["evaluation_tests"] = report.passed_count + report.failed_count
        self._index[name]["agent_available"] = passed and trusted
        self._save_index()

    def mark_evaluation_passed(self, name: str) -> None:
        """Mark evaluation passed without running tests (when execution itself is under test)."""
        if name not in self._index:
            return
        trusted = self._index[name].get("trust_status") == SkillTrustStatus.TRUSTED.value
        self._index[name]["evaluation_status"] = EVALUATION_PASSED
        self._index[name]["agent_available"] = trusted
        self._index[name]["evaluation_passed_at"] = datetime.now(timezone.utc).isoformat()
        self._save_index()

    def ensure_agent_ready(
        self,
        name: str,
        *,
        smoke_params: Optional[Dict[str, Any]] = None,
        smoke_expect: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write a minimal passing test suite and evaluate (bootstrap/tests)."""
        from skill_evaluation import SkillEvaluator, SkillTestCase, save_skill_tests, run_async_safe

        expect = smoke_expect or {"ok": True}
        save_skill_tests(
            self.skills_dir / name,
            [SkillTestCase("smoke", smoke_params or {}, expect)],
            min_tests=1,
        )
        run_async_safe(SkillEvaluator(self, use_subprocess=False).evaluate(name))

    def is_agent_available(self, name: str, *, require_evaluated: bool = True) -> bool:
        """True when skill passed evaluation and trust checks."""
        entry = self._index.get(name)
        if not entry:
            return False
        if require_evaluated and entry.get("evaluation_status") != EVALUATION_PASSED:
            return False
        if entry.get("trust_status") != SkillTrustStatus.TRUSTED.value:
            return False
        return True

    def list_skills(
        self,
        *,
        tag: Optional[str] = None,
        permission: Optional[PermissionKind] = None,
        max_risk: Optional[RiskLevel] = None,
        language: Optional[str] = None,
        agent_available_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Query registered skills with optional filters."""
        results: List[Dict[str, Any]] = []
        for entry in self._index.values():
            if agent_available_only and not entry.get("agent_available"):
                continue
            if tag and tag not in entry.get("tags", []):
                continue
            if permission and permission.value not in entry.get("permissions", []):
                continue
            if max_risk:
                entry_risk = RiskLevel(entry.get("risk_level", "low"))
                if RISK_LEVEL_ORDER.get(entry_risk, 0) > RISK_LEVEL_ORDER.get(max_risk, 0):
                    continue
            if language and entry.get("language") != language:
                continue
            results.append(dict(entry))
        return results

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search skills by name, display_name or tags."""
        query_lower = query.lower()
        results: List[Dict[str, Any]] = []
        for entry in self._index.values():
            name = entry.get("name", "").lower()
            display = entry.get("display_name", "").lower()
            tags = " ".join(entry.get("tags", [])).lower()
            if query_lower in name or query_lower in display or query_lower in tags:
                results.append(dict(entry))
        return results

    def discover_by_objective(
        self,
        objective: str,
        *,
        limit: int = 5,
        min_score: float = 0.12,
        trusted_only: bool = True,
        evaluated_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Rank installed skills by relevance to a natural-language objective."""
        pairs: List[tuple] = []
        for name in self.names:
            manifest = self.get(name)
            if manifest is None:
                continue
            entry = self._index.get(name)
            if trusted_only and entry and entry.get("trust_status") != SkillTrustStatus.TRUSTED.value:
                continue
            if evaluated_only and not self.is_agent_available(name):
                continue
            pairs.append((manifest, entry))

        matches: List[SkillMatch] = discover_skills_for_objective(
            pairs, objective, limit=limit, min_score=min_score,
        )
        return [m.to_dict() for m in matches]

    @property
    def count(self) -> int:
        return len(self._index)

    @property
    def names(self) -> List[str]:
        return list(self._index.keys())

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def export_skill(self, name: str, dest_dir: Path) -> Path:
        """Export a skill folder to an external directory for sharing."""
        if name not in self._index:
            raise SkillNotFoundError(f"Skill '{name}' not found")
        src = self.skills_dir / name
        dest = dest_dir / name
        if dest.exists():
            shutil.rmtree(str(dest))
        shutil.copytree(str(src), str(dest))
        return dest

    def export_marketplace_package(
        self,
        dest: Path,
        skill_names: Optional[List[str]] = None,
        *,
        as_zip: bool = False,
        author: str = "",
        description: str = "",
    ) -> Path:
        """Export offline marketplace package (folder or .phylum-skillpack zip)."""
        from skill_marketplace import export_marketplace_directory, export_marketplace_zip

        dest = Path(dest)
        if as_zip:
            return export_marketplace_zip(self, dest, skill_names, author=author, description=description)
        return export_marketplace_directory(self, dest, skill_names, author=author, description=description)

    def import_marketplace_package(
        self,
        src: Path,
        *,
        overwrite: bool = False,
        run_evaluation: bool = True,
    ):
        """Import skills from offline marketplace folder or zip."""
        from skill_marketplace import import_marketplace_directory, import_marketplace_zip

        src = Path(src)
        if src.suffix == ".zip" or str(src).endswith(".phylum-skillpack"):
            return import_marketplace_zip(self, src, overwrite=overwrite, run_evaluation=run_evaluation)
        return import_marketplace_directory(self, src, overwrite=overwrite, run_evaluation=run_evaluation)

    def import_skill(self, src_dir: Path, *, overwrite: bool = False) -> SkillManifest:
        """Import a skill from an external directory."""
        manifest_path = src_dir / "manifest.json"
        if not manifest_path.exists():
            raise SkillRegistryError(f"No manifest.json in {src_dir}")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = SkillManifest.from_dict(data)

        ext = ".py" if manifest.language == "python" else ".ps1"
        code_path = src_dir / f"code{ext}"
        if not code_path.exists():
            raise SkillRegistryError(f"No code file (code{ext}) in {src_dir}")
        code = code_path.read_text(encoding="utf-8")

        sig_valid = False
        if manifest.manifest_checksum and manifest.signature:
            sig_valid = verify_bundle_signature(
                manifest.manifest_checksum,
                manifest.signature,
                skills_dir=self.skills_dir,
            )

        provenance = build_provenance(
            source="import",
            registered_by="import",
            import_path=str(src_dir),
            previous_checksum=manifest.manifest_checksum,
            notes="Imported from external package",
        )
        manifest.provenance = provenance
        if not sig_valid:
            manifest.trust_status = SkillTrustStatus.UNTRUSTED

        return self.register(
            manifest,
            code,
            overwrite=overwrite,
            provenance=provenance,
            trust_on_import=sig_valid,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        index_path = self.skills_dir / REGISTRY_INDEX
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                return data.get("skills", {})
            except Exception:
                logger.exception("Failed to load skill registry index")
        return {}

    def _save_index(self) -> None:
        index_path = self.skills_dir / REGISTRY_INDEX
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "skill_count": len(self._index),
            "skills": self._index,
        }
        index_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def clear(self) -> int:
        """Remove all skills (mainly for testing)."""
        count = len(self._index)
        for name in list(self._index.keys()):
            self.unregister(name)
        return count
