# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from intent_profile_registry import IntentProfileRegistry
from schema_domain_registry import (
    SchemaDomainRegistry,
    load_raw_tables,
    load_schema_domain_registry,
    normalize_tables_dict,
    reload_schema_domain_registry,
)
from tool_schema_optimizer import build_optimize_context, optimize_tool_schema, reload_domain_tables

from canonical_tools import tool_schema_by_name


@pytest.fixture(autouse=True)
def _fresh_registry():
    reload_schema_domain_registry()
    reload_domain_tables()
    IntentProfileRegistry.reload_default()
    yield
    reload_schema_domain_registry()
    reload_domain_tables()
    IntentProfileRegistry.reload_default()


def test_v1_tools_normalized_to_domains():
    raw = {
        "version": 1,
        "tools": {
            "office": {
                "variants": {
                    "default": {"actions": ["outlook_read_latest"], "properties": ["action"]}
                }
            }
        },
    }
    normalized = normalize_tables_dict(raw)
    assert normalized["version"] == 2
    assert "office" in normalized["domains"]
    assert normalized["domains"]["office"]["tool"] == "office"


def test_registry_lists_domains():
    registry = load_schema_domain_registry()
    domains = registry.list_domains()
    assert "office" in domains
    assert "filesystem" in domains
    assert "shell" in domains
    assert "driver" in domains


def test_resolve_by_schema_variant_not_user_phrases():
    registry = load_schema_domain_registry()
    variant_id, variant = registry.resolve_variant(
        "office",
        domain="office",
        schema_variant="outlook_read",
    )
    assert variant_id == "outlook_read"
    assert "outlook_read_latest" in variant["actions"]


def test_resolve_tool_variants_for_secondary_tool():
    profile = IntentProfileRegistry.default().require("outlook_export_emails_file")
    registry = load_schema_domain_registry()
    variant_id, variant = registry.resolve_variant(
        "filesystem",
        domain="office",
        schema_variant=profile.schema_variant,
        tool_variants=profile.tool_variants,
    )
    assert variant_id == "write"
    assert variant["actions"] == ["write", "copy", "move", "delete", "mkdir"]


def test_user_overlay_merges_domain_variant(tmp_path, monkeypatch):
    user_file = tmp_path / "schema_domain_tables.json"
    user_file.write_text(
        json.dumps(
            {
                "version": 2,
                "domains": {
                    "office": {
                        "variants": {
                            "outlook_read": {
                                "actions": ["outlook_read_latest"],
                                "properties": ["action", "limit"],
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTE_SCHEMA_DOMAIN_TABLES", str(user_file))
    reload_schema_domain_registry()
    registry = load_schema_domain_registry()
    variant_id, variant = registry.resolve_variant(
        "office",
        domain="office",
        schema_variant="outlook_read",
    )
    assert variant_id == "outlook_read"
    assert variant["actions"] == ["outlook_read_latest"]
    assert variant["properties"] == ["action", "limit"]


def test_validate_against_canonical_passes_for_bundled():
    registry = load_schema_domain_registry()
    errors = registry.validate_against_canonical()
    assert errors == []


def test_profile_schema_variant_drives_optimizer():
    profile = IntentProfileRegistry.default().require("filesystem_list_downloads")
    ctx = build_optimize_context(profile=profile)
    result = optimize_tool_schema(tool_schema_by_name("filesystem"), ctx)
    assert result.variant_id == "profile:list"
    assert result.schema["function"]["parameters"]["properties"]["action"]["enum"] == ["list"]


def test_driver_domain_resolves_driver_manager_tool():
    registry = load_schema_domain_registry()
    spec = registry.get_domain("driver")
    assert spec is not None
    assert spec.tool == "driver_manager"
    variant_id, _ = registry.resolve_variant(
        "driver_manager",
        domain="driver",
        action="find_driver_candidates",
    )
    assert variant_id == "driver_search"
