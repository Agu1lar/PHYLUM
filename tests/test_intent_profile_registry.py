# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from intent_profile_registry import (
    IntentProfileRegistry,
    load_profiles_from_directory,
    profile_from_dict,
)


def test_default_registry_loads_all_manifest_profiles():
    registry = IntentProfileRegistry.default()
    manifest_path = Path(__file__).resolve().parent.parent / "core" / "intent_profiles" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for profile_id in manifest["profiles"]:
        profile = registry.get(profile_id)
        assert profile is not None, profile_id
        assert profile.id == profile_id
        assert profile.domain
        assert profile.required_tools
        assert 0.0 < profile.confidence_threshold <= 1.0
        assert profile.default_action.tool
        assert profile.default_action.action


def test_outlook_read_unread_profile():
    profile = IntentProfileRegistry.default().require("outlook_read_unread")
    assert profile.domain == "office"
    assert profile.required_tools == ("office",)
    assert profile.default_action.tool == "office"
    assert profile.default_action.action == "outlook_read_latest"
    assert profile.default_action.params["unread_only"] is True
    assert profile.param_defaults["unread_only"] is True


def test_profile_from_dict_validates_required_fields():
    with pytest.raises(ValueError, match="missing fields"):
        profile_from_dict({"id": "incomplete"})


def test_profile_from_dict_rejects_bad_threshold():
    with pytest.raises(ValueError, match="confidence_threshold"):
        profile_from_dict(
            {
                "id": "bad",
                "domain": "x",
                "required_tools": ["shell"],
                "default_action": {"tool": "shell", "action": "run"},
                "param_defaults": {},
                "confidence_threshold": 1.5,
            }
        )


def test_registry_rejects_duplicate_ids():
    profile = IntentProfileRegistry.default().require("outlook_read_unread")
    registry = IntentProfileRegistry()
    registry.register(profile)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(profile)


def test_list_by_domain_office():
    office_profiles = IntentProfileRegistry.default().list_by_domain("office")
    ids = {p.id for p in office_profiles}
    assert "outlook_read_unread" in ids
    assert "outlook_read_latest" in ids


def test_load_from_custom_directory(tmp_path: Path):
    profile_path = tmp_path / "custom_task.json"
    profile_path.write_text(
        json.dumps(
            {
                "id": "custom_task",
                "domain": "shell",
                "required_tools": ["shell"],
                "default_action": {"tool": "shell", "action": "run", "params": {"command": "echo hi"}},
                "param_defaults": {"shell": "powershell"},
                "confidence_threshold": 0.8,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_profiles_from_directory(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "custom_task"
