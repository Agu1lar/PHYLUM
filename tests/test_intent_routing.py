# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from intent_classifier import classify_user_intent
from intent_profile_learner import (
    build_learned_profile_draft,
    maybe_promote_learned_profile,
    propose_learned_profile,
)
from intent_profile_registry import IntentProfileRegistry, profile_from_dict
from intent_routing import intent_fast_path_enabled, resolve_intent_routing


def test_resolve_fast_path_when_accepted(monkeypatch):
    monkeypatch.setenv("AGENTE_INTENT_FAST_PATH", "1")
    classification = classify_user_intent("ultimos emails nao lidos do outlook")
    routing = resolve_intent_routing(classification)
    assert routing["mode"] == "fast_path"
    assert routing["profile_id"] == "outlook_read_unread"


def test_resolve_agentic_when_no_match():
    classification = classify_user_intent("ola")
    routing = resolve_intent_routing(classification)
    assert routing["mode"] == "agentic"
    assert routing["fallback_reason"] == "no_profile_signals_matched"


def test_fast_path_disabled(monkeypatch):
    monkeypatch.setenv("AGENTE_INTENT_FAST_PATH", "0")
    classification = classify_user_intent("ultimos emails nao lidos do outlook")
    routing = resolve_intent_routing(classification)
    assert routing["mode"] == "agentic"
    assert routing["fallback_reason"] == "fast_path_disabled"


def test_learned_profile_promotion_after_two_successes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTE_INTENT_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTE_INTENT_LEARN", "1")
    user_text = "consultar relatorio vendas no sistema interno"
    tasks = [
        {
            "id": "t1",
            "tool": "shell",
            "action": "run",
            "status": "completed",
            "params": {"command": "Get-Report"},
        }
    ]
    first = maybe_promote_learned_profile(
        user_text=user_text,
        tasks=tasks,
        intent_accepted=False,
        execution_mode="agentic",
    )
    assert first["status"] == "recorded"
    assert first["count"] == 1
    second = maybe_promote_learned_profile(
        user_text=user_text,
        tasks=tasks,
        intent_accepted=False,
        execution_mode="agentic",
    )
    assert second["status"] == "promoted"
    profile_file = tmp_path / f"{second['profile_id']}.json"
    assert profile_file.is_file()
    data = json.loads(profile_file.read_text(encoding="utf-8"))
    assert data["source"] == "learned"
    registry = IntentProfileRegistry.load_merged()
    assert registry.get(second["profile_id"]) is not None


def test_no_learning_when_intent_already_matched(monkeypatch):
    monkeypatch.setenv("AGENTE_INTENT_LEARN", "1")
    draft = propose_learned_profile(
        user_text="ultimos emails nao lidos do outlook",
        tasks=[{"tool": "office", "action": "outlook_read_latest", "status": "completed"}],
        intent_accepted=True,
    )
    assert draft is None


def test_user_overlay_overrides_builtin(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTE_INTENT_PROFILES_DIR", str(tmp_path))
    (tmp_path / "outlook_read_unread.json").write_text(
        json.dumps(
            {
                "id": "outlook_read_unread",
                "domain": "office",
                "required_tools": ["office"],
                "default_action": {
                    "tool": "office",
                    "action": "outlook_read_latest",
                    "params": {"unread_only": True, "limit": 99},
                },
                "param_defaults": {"limit": 99, "unread_only": True, "folder": "inbox"},
                "confidence_threshold": 0.5,
                "signals": {"require_any": ["outlook"]},
            }
        ),
        encoding="utf-8",
    )
    registry = IntentProfileRegistry.load_merged()
    profile = registry.require("outlook_read_unread")
    assert profile.default_action.params.get("limit") == 99
