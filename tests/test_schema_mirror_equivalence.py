# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Fase 3.5 — mirror schema arguments normalize to the same task as canonical."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from canonical_tools import normalize_agentic_task, tool_schema_by_name
from intent_profile_registry import IntentProfileRegistry
from llm_payload_planner import DisclosureLevel
from schema_domain_registry import reload_schema_domain_registry
from schema_equivalence import (
    assert_mirror_normalization_equivalent,
    arguments_accepted_by_mirror,
    filter_arguments_to_mirror,
    normalized_tasks_equivalent,
    prepare_arguments_for_mirror,
    profile_tool_arguments,
)
from tool_schema_optimizer import (
    build_optimize_context,
    estimate_schema_json_chars,
    optimize_tool_schema,
    reload_domain_tables,
)


@pytest.fixture(autouse=True)
def _fresh_tables():
    reload_schema_domain_registry()
    reload_domain_tables()
    IntentProfileRegistry.reload_default()
    yield
    reload_schema_domain_registry()
    reload_domain_tables()
    IntentProfileRegistry.reload_default()


def _mirror_for_profile_tool(profile_id: str, tool_name: str):
    profile = IntentProfileRegistry.default().require(profile_id)
    ctx = build_optimize_context(profile=profile, disclosure_level=DisclosureLevel.FOCUSED)
    canonical = tool_schema_by_name(tool_name)
    result = optimize_tool_schema(canonical, ctx)
    return profile, result.schema


PROFILE_TOOL_CASES = [
    ("outlook_read_latest", "office"),
    ("outlook_read_unread", "office"),
    ("outlook_export_emails_file", "office"),
    ("outlook_export_emails_file", "filesystem"),
    ("filesystem_list_downloads", "filesystem"),
    ("filesystem_write_downloads", "filesystem"),
    ("shell_list_processes", "shell"),
    ("driver_install_printer", "driver_manager"),
    ("driver_install_printer", "shell"),
]


@pytest.mark.parametrize("profile_id,tool_name", PROFILE_TOOL_CASES)
def test_profile_default_args_equivalent_on_mirror(profile_id: str, tool_name: str):
    profile, mirror = _mirror_for_profile_tool(profile_id, tool_name)
    arguments = profile_tool_arguments(profile, tool_name)
    assert arguments, f"no sample arguments for {profile_id}/{tool_name}"

    task = assert_mirror_normalization_equivalent(tool_name, arguments, mirror)
    assert task["tool"] == tool_name
    assert normalized_tasks_equivalent(tool_name, arguments, mirror)


@pytest.mark.parametrize("profile_id,tool_name", PROFILE_TOOL_CASES)
def test_mirror_accepts_profile_arguments(profile_id: str, tool_name: str):
    profile, mirror = _mirror_for_profile_tool(profile_id, tool_name)
    arguments = profile_tool_arguments(profile, tool_name)
    mirror_args = prepare_arguments_for_mirror(tool_name, arguments, mirror)
    ok, errors = arguments_accepted_by_mirror(mirror_args, mirror, tool_name=tool_name)
    assert ok, errors


def test_filter_drops_keys_not_on_mirror():
    profile, mirror = _mirror_for_profile_tool("outlook_read_latest", "office")
    arguments = {
        **profile_tool_arguments(profile, "office"),
        "output_path": r"C:\temp\out.pdf",
        "app_name": "Word",
    }
    prepared = prepare_arguments_for_mirror("office", arguments, mirror)
    assert "output_path" not in prepared
    assert "app_name" not in prepared
    assert prepared["action"] == "outlook_read_latest"
    assert_mirror_normalization_equivalent("office", arguments, mirror)


def test_extra_canonical_fields_equivalent_on_mirror_surface_only():
    """Extras on the full schema do not change tool/action or mirror-visible params."""
    profile, mirror = _mirror_for_profile_tool("outlook_read_latest", "office")
    base = profile_tool_arguments(profile, "office")
    base["limit"] = 5
    base["folder"] = "inbox"
    base["unread_only"] = True

    with_extra = {**base, "to": "user@example.com", "subject": "ignored"}
    assert_mirror_normalization_equivalent("office", with_extra, mirror)

    task_full = normalize_agentic_task("office", with_extra, "t1")
    task_base = normalize_agentic_task("office", base, "t2")
    assert task_full["action"] == task_base["action"]
    assert task_full["params"]["limit"] == task_base["params"]["limit"]
    assert "to" in task_full["params"]
    assert "to" not in task_base["params"]


def test_office_outlook_read_mirror_parameters_smaller_than_canonical():
    profile = IntentProfileRegistry.default().require("outlook_read_latest")
    ctx = build_optimize_context(profile=profile, disclosure_level=DisclosureLevel.MINIMAL)
    canonical = tool_schema_by_name("office")
    mirror = optimize_tool_schema(canonical, ctx).schema
    full_props = canonical["function"]["parameters"]["properties"]
    mirror_props = mirror["function"]["parameters"]["properties"]
    full_actions = full_props["action"]["enum"]
    mirror_actions = mirror_props["action"]["enum"]
    assert len(mirror_actions) < len(full_actions) * 0.3
    assert len(mirror_props) < len(full_props) * 0.5


def test_equivalence_matrix_all_bundled_profiles():
    """Every bundled intent profile: each required tool has a mirror-equivalent sample."""
    manifest = Path(__file__).resolve().parent.parent / "core" / "intent_profiles" / "manifest.json"
    profile_ids = json.loads(manifest.read_text(encoding="utf-8"))["profiles"]
    registry = IntentProfileRegistry.default()
    failures: list[str] = []

    for profile_id in profile_ids:
        profile = registry.require(profile_id)
        for tool_name in profile.required_tools:
            try:
                _, mirror = _mirror_for_profile_tool(profile_id, tool_name)
                args = profile_tool_arguments(profile, tool_name)
                if not args:
                    failures.append(f"{profile_id}/{tool_name}: empty arguments")
                    continue
                assert_mirror_normalization_equivalent(tool_name, args, mirror)
            except Exception as exc:
                failures.append(f"{profile_id}/{tool_name}: {exc}")

    assert not failures, "equivalence failures:\n" + "\n".join(failures)
