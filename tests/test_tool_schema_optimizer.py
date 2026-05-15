# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from canonical_tools import tool_schema_by_name
from intent_profile_registry import IntentProfileRegistry
from llm_payload_planner import DisclosureLevel
from canonical_tools import agentic_tool_definitions
from llm_payload_planner import plan_llm_payload
from model_router import ComplexityLevel
from tool_schema_optimizer import (
    CanonicalCatalogMutationError,
    assert_canonical_catalog_unchanged,
    build_llm_tool_mirror,
    build_optimize_context,
    canonical_catalog_fingerprint,
    detach_tool_definitions,
    estimate_schema_json_chars,
    load_domain_tables,
    optimize_tool_schema,
    optimize_tools_for_llm,
    prune_action_enum,
    prune_parameter_properties,
    reload_domain_tables,
    schema_optimizer_enabled,
    truncate_description,
)


@pytest.fixture(autouse=True)
def _fresh_domain_tables():
    from intent_profile_registry import IntentProfileRegistry
    from schema_domain_registry import reload_schema_domain_registry

    reload_schema_domain_registry()
    reload_domain_tables()
    IntentProfileRegistry.reload_default()
    yield
    reload_schema_domain_registry()
    reload_domain_tables()
    IntentProfileRegistry.reload_default()


def _office_schema():
    return tool_schema_by_name("office")


def test_canonical_schema_unchanged_after_optimize():
    canonical = _office_schema()
    before = json.dumps(canonical, sort_keys=True)
    profile = IntentProfileRegistry.default().require("outlook_read_latest")
    ctx = build_optimize_context(profile=profile, disclosure_level=DisclosureLevel.FOCUSED)
    result = optimize_tool_schema(canonical, ctx)
    after_canonical = json.dumps(canonical, sort_keys=True)
    assert before == after_canonical
    assert result.optimized is True
    assert result.tool_name == "office"


def test_profile_restricts_office_actions_and_properties():
    profile = IntentProfileRegistry.default().require("outlook_read_latest")
    ctx = build_optimize_context(profile=profile, disclosure_level=DisclosureLevel.MINIMAL)
    result = optimize_tool_schema(_office_schema(), ctx)
    params = result.schema["function"]["parameters"]
    actions = params["properties"]["action"]["enum"]
    assert actions == ["outlook_read_latest"]
    assert set(params["properties"].keys()) == {"action", "limit", "folder", "unread_only"}
    full_size = estimate_schema_json_chars([_office_schema()])
    opt_size = estimate_schema_json_chars([result.schema])
    assert opt_size < full_size * 0.7


def test_domain_only_uses_domain_table_default_variant():
    ctx = build_optimize_context(domain="office", disclosure_level=DisclosureLevel.FOCUSED)
    result = optimize_tool_schema(_office_schema(), ctx)
    assert result.optimized is True
    assert result.variant_id == "outlook_read"
    actions = result.schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert "outlook_read_latest" in actions


def test_filesystem_list_profile():
    profile = IntentProfileRegistry.default().require("filesystem_list_downloads")
    ctx = build_optimize_context(profile=profile)
    result = optimize_tool_schema(tool_schema_by_name("filesystem"), ctx)
    actions = result.schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert actions == ["list"]
    assert "path" in result.schema["function"]["parameters"]["properties"]


def test_shell_profile_keeps_command_properties():
    profile = IntentProfileRegistry.default().require("shell_list_processes")
    ctx = build_optimize_context(profile=profile)
    result = optimize_tool_schema(tool_schema_by_name("shell"), ctx)
    props = result.schema["function"]["parameters"]["properties"]
    assert "command" in props
    assert "shell" in props


def test_no_variant_returns_copy_with_optional_truncation():
    ctx = build_optimize_context(domain="unknown_domain_xyz")
    memory_tool = tool_schema_by_name("memory")
    result = optimize_tool_schema(memory_tool, ctx)
    assert result.optimized is False
    assert result.schema["function"]["name"] == "memory"


def test_optimize_tools_batch():
    profile = IntentProfileRegistry.default().require("outlook_export_emails_file")
    ctx = build_optimize_context(profile=profile)
    tools = [tool_schema_by_name("office"), tool_schema_by_name("filesystem")]
    optimized = optimize_tools_for_llm(tools, ctx)
    assert len(optimized) == 2
    office_actions = optimized[0]["function"]["parameters"]["properties"]["action"]["enum"]
    assert office_actions == ["outlook_read_latest"]


def test_domain_tables_load():
    tables = load_domain_tables()
    assert tables.get("version") == 2
    assert "office" in tables.get("domains", {})
    assert "office" in tables.get("tools", {})


def test_prune_action_enum_only_allowed():
    office = _office_schema()
    action_spec = office["function"]["parameters"]["properties"]["action"]
    pruned, did_prune = prune_action_enum(action_spec, ["outlook_read_latest"])
    assert did_prune is True
    assert pruned["enum"] == ["outlook_read_latest"]


def test_prune_parameter_properties_drops_unused():
    office = _office_schema()
    params = office["function"]["parameters"]
    pruned, props_pruned, actions_pruned = prune_parameter_properties(
        params,
        ["action", "limit", "folder", "unread_only"],
        allowed_actions=["outlook_read_latest"],
    )
    assert props_pruned is True
    assert actions_pruned is True
    assert set(pruned["properties"].keys()) == {"action", "limit", "folder", "unread_only"}
    assert "path" not in pruned["properties"]
    assert pruned["properties"]["action"]["enum"] == ["outlook_read_latest"]


def test_minimal_description_is_single_line():
    long_desc = "Line one.\nLine two with extra detail.\nLine three."
    minimal = truncate_description(long_desc, DisclosureLevel.MINIMAL)
    assert "\n" not in minimal
    assert minimal == "Line one."

    profile = IntentProfileRegistry.default().require("outlook_read_latest")
    ctx = build_optimize_context(profile=profile, disclosure_level=DisclosureLevel.MINIMAL)
    result = optimize_tool_schema(_office_schema(), ctx)
    fn_desc = result.schema["function"]["description"]
    assert "\n" not in fn_desc
    assert result.descriptions_truncated is True


def test_focused_truncates_but_longer_than_minimal():
    long_desc = "A" * 400
    minimal = truncate_description(long_desc, DisclosureLevel.MINIMAL)
    focused = truncate_description(long_desc, DisclosureLevel.FOCUSED)
    assert len(focused) > len(minimal)


def test_detach_never_aliases_canonical_catalog():
    catalog = agentic_tool_definitions()
    fp = canonical_catalog_fingerprint(catalog)
    office_canonical = tool_schema_by_name("office")
    detached = detach_tool_definitions([office_canonical])[0]
    detached["function"]["description"] = "MUTATED"
    assert canonical_catalog_fingerprint(catalog) == fp
    assert office_canonical["function"]["description"] != "MUTATED"
    assert_canonical_catalog_unchanged(catalog, fingerprint=fp)


def test_build_llm_tool_mirror_is_not_canonical_reference():
    profile = IntentProfileRegistry.default().require("outlook_read_latest")
    ctx = build_optimize_context(profile=profile)
    canonical = tool_schema_by_name("office")
    mirror = build_llm_tool_mirror([canonical], ctx)[0]
    assert mirror is not canonical
    mirror["function"]["parameters"]["properties"]["action"]["enum"] = ["mutated"]
    assert canonical["function"]["parameters"]["properties"]["action"]["enum"] != ["mutated"]


def test_plan_llm_payload_preserves_canonical_catalog_with_optimizer(monkeypatch):
    monkeypatch.setenv("AGENTE_SCHEMA_OPTIMIZER", "1")
    catalog = agentic_tool_definitions()
    fp_before = canonical_catalog_fingerprint(catalog)
    plan = plan_llm_payload(
        catalog,
        "listar emails do outlook",
        ComplexityLevel.SIMPLE,
        "anthropic",
    )
    assert canonical_catalog_fingerprint(catalog) == fp_before
    assert plan.llm_schema_mirror is True
    assert plan.canonical_catalog_fingerprint == fp_before
    office_mirror = next(t for t in plan.tools if t["function"]["name"] == "office")
    office_canonical = tool_schema_by_name("office")
    assert office_mirror is not office_canonical
    full_enum = office_canonical["function"]["parameters"]["properties"]["action"]["enum"]
    mirror_enum = office_mirror["function"]["parameters"]["properties"]["action"]["enum"]
    assert len(mirror_enum) < len(full_enum)


def test_mutating_catalog_after_fingerprint_raises():
    catalog = agentic_tool_definitions()
    fp = canonical_catalog_fingerprint(catalog)
    catalog[0]["function"]["description"] = "mutated canonical entry"
    with pytest.raises(CanonicalCatalogMutationError):
        assert_canonical_catalog_unchanged(catalog, fingerprint=fp)


def test_variant_without_profile_restrict_prunes_enum_to_variant():
    profile = IntentProfileRegistry.default().require("outlook_read_latest")
    ctx = build_optimize_context(
        profile=profile,
        disclosure_level=DisclosureLevel.FOCUSED,
        restrict_to_profile_action=False,
    )
    result = optimize_tool_schema(_office_schema(), ctx)
    actions = result.schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert set(actions) == {
        "outlook_read_latest",
        "outlook_search_messages",
        "reveal_active_document_path",
    }
    assert "open_document" not in actions
    assert result.actions_pruned is True
