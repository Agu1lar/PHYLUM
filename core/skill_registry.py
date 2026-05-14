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

from skill_manifest import (
    RiskLevel,
    RISK_LEVEL_ORDER,
    PermissionKind,
    SkillManifest,
    compute_code_checksum,
    validate_manifest,
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

        manifest.checksum = compute_code_checksum(code)
        manifest.updated_at = datetime.now(timezone.utc).isoformat()
        if not manifest.skill_id:
            manifest.skill_id = f"skill_{manifest.name}_{manifest.version}"

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
        return compute_code_checksum(code) == manifest.checksum

    def list_skills(
        self,
        *,
        tag: Optional[str] = None,
        permission: Optional[PermissionKind] = None,
        max_risk: Optional[RiskLevel] = None,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query registered skills with optional filters."""
        results: List[Dict[str, Any]] = []
        for entry in self._index.values():
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

        return self.register(manifest, code, overwrite=overwrite)

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
