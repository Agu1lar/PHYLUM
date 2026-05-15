# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Skill signing, bundle checksums and provenance for tamper detection."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SIGNING_KEY_FILE = ".agente_signing_key"
SIGNING_KEY_ENV = "AGENTE_SKILL_SIGNING_KEY"


class SkillTrustStatus(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    QUARANTINED = "quarantined"


class SkillProvenance(BaseModel):
    """Tracks origin and lineage of a skill package."""
    source: str = "local"
    registered_by: str = "agent"
    session_id: str = ""
    parent_skill: str = ""
    registered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    previous_checksum: str = ""
    previous_version: str = ""
    import_path: str = ""
    notes: str = ""


def _canonical_manifest_payload(manifest_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Stable manifest subset used for signing (excludes volatile/derived fields)."""
    excluded = {
        "signature", "manifest_checksum", "updated_at",
        "effective_risk_level", "requires_approval",
    }
    return {k: v for k, v in sorted(manifest_dict.items()) if k not in excluded}


def compute_bundle_checksum(manifest_dict: Dict[str, Any], code_checksum: str) -> str:
    """SHA-256 of canonical manifest + code checksum."""
    payload = {
        "manifest": _canonical_manifest_payload(manifest_dict),
        "code_checksum": code_checksum,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_signing_key(skills_dir: Path) -> bytes:
    """Load or create the local HMAC signing key."""
    env_key = os.getenv(SIGNING_KEY_ENV, "").strip()
    if env_key:
        return env_key.encode("utf-8")

    key_path = skills_dir / SIGNING_KEY_FILE
    if key_path.exists():
        return key_path.read_bytes()

    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    logger.info("Created local skill signing key at %s", key_path)
    return key


def sign_bundle(bundle_checksum: str, *, skills_dir: Path) -> str:
    """HMAC-SHA256 signature for a bundle checksum."""
    key = get_signing_key(skills_dir)
    return hmac.new(key, bundle_checksum.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_bundle_signature(
    bundle_checksum: str,
    signature: str,
    *,
    skills_dir: Path,
) -> bool:
    if not bundle_checksum or not signature:
        return False
    expected = sign_bundle(bundle_checksum, skills_dir=skills_dir)
    return hmac.compare_digest(expected, signature)


def build_provenance(
    *,
    source: str = "local",
    registered_by: str = "agent",
    session_id: str = "",
    parent_skill: str = "",
    previous_checksum: str = "",
    previous_version: str = "",
    import_path: str = "",
    notes: str = "",
) -> SkillProvenance:
    return SkillProvenance(
        source=source,
        registered_by=registered_by,
        session_id=session_id,
        parent_skill=parent_skill,
        previous_checksum=previous_checksum,
        previous_version=previous_version,
        import_path=import_path,
        notes=notes,
    )
