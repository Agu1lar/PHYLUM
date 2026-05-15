# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Local/offline skill marketplace — import/export packages without telemetry."""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MARKETPLACE_FORMAT_VERSION = 1
MARKETPLACE_MANIFEST = "marketplace.json"
PACKAGE_EXTENSION = ".phylum-skillpack"


@dataclass
class MarketplaceImportResult:
    imported: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": self.errors,
            "imported_count": len(self.imported),
        }


def build_marketplace_manifest(
    *,
    skill_names: List[str],
    author: str = "",
    description: str = "",
) -> Dict[str, Any]:
    return {
        "format_version": MARKETPLACE_FORMAT_VERSION,
        "kind": "phylum_skill_marketplace",
        "telemetry": False,
        "offline_only": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "author": author,
        "description": description or "Offline skill package — no network calls, no telemetry.",
        "skill_count": len(skill_names),
        "skills": skill_names,
    }


def export_marketplace_directory(
    registry,
    dest_dir: Path,
    skill_names: Optional[List[str]] = None,
    *,
    author: str = "",
    description: str = "",
) -> Path:
    """Export skills into a marketplace folder (offline shareable)."""
    names = skill_names or registry.names
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    skills_root = dest_dir / "skills"
    skills_root.mkdir(exist_ok=True)

    exported: List[str] = []
    for name in names:
        if name not in registry.names:
            continue
        registry.export_skill(name, skills_root)
        exported.append(name)

    manifest = build_marketplace_manifest(
        skill_names=exported,
        author=author,
        description=description,
    )
    (dest_dir / MARKETPLACE_MANIFEST).write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return dest_dir


def export_marketplace_zip(
    registry,
    zip_path: Path,
    skill_names: Optional[List[str]] = None,
    *,
    author: str = "",
    description: str = "",
) -> Path:
    """Export skills as a .phylum-skillpack zip archive."""
    import tempfile

    zip_path = Path(zip_path)
    if zip_path.suffix != PACKAGE_EXTENSION:
        zip_path = zip_path.with_suffix(PACKAGE_EXTENSION)

    with tempfile.TemporaryDirectory(prefix="phylum_export_") as tmp:
        folder = Path(tmp) / "package"
        export_marketplace_directory(
            registry,
            folder,
            skill_names,
            author=author,
            description=description,
        )
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", str(folder))
    return zip_path


def import_marketplace_directory(
    registry,
    src_dir: Path,
    *,
    overwrite: bool = False,
    run_evaluation: bool = True,
) -> MarketplaceImportResult:
    """Import all skills from a marketplace folder."""
    src_dir = Path(src_dir)
    manifest_path = src_dir / MARKETPLACE_MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"Missing {MARKETPLACE_MANIFEST} in {src_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("telemetry"):
        logger.warning("Marketplace manifest claims telemetry=true; import proceeds offline anyway")

    skills_dir = src_dir / "skills"
    if not skills_dir.is_dir():
        raise ValueError(f"Missing skills/ directory in {src_dir}")

    result = MarketplaceImportResult()

    for skill_name in manifest.get("skills") or []:
        skill_path = skills_dir / skill_name
        if not skill_path.is_dir():
            result.errors.append(f"{skill_name}: folder missing")
            continue
        if skill_name in registry.names and not overwrite:
            result.skipped.append(skill_name)
            continue
        try:
            registry.import_skill(skill_path, overwrite=overwrite)
            result.imported.append(skill_name)
        except Exception as exc:
            result.errors.append(f"{skill_name}: {exc}")

    return result


def import_marketplace_zip(
    registry,
    zip_path: Path,
    *,
    overwrite: bool = False,
    run_evaluation: bool = True,
) -> MarketplaceImportResult:
    """Import skills from a .phylum-skillpack zip."""
    import tempfile

    zip_path = Path(zip_path)
    with tempfile.TemporaryDirectory(prefix="phylum_import_") as tmp:
        extract_dir = Path(tmp) / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        return import_marketplace_directory(
            registry,
            extract_dir,
            overwrite=overwrite,
            run_evaluation=run_evaluation,
        )
