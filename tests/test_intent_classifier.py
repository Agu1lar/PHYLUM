# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from intent_classifier import (
    IntentClassifier,
    build_resolved_tool_arguments,
    classify_user_intent,
    normalize_user_text,
    score_profile,
)
from intent_profile_registry import IntentProfileRegistry, profile_from_dict


def test_normalize_strips_accents():
    assert "nao lidos" in normalize_user_text("Não lidos")


def test_greeting_does_not_match_any_profile():
    result = classify_user_intent("ola")
    assert not result.accepted
    assert result.profile_id is None


def test_outlook_unread_portuguese():
    result = classify_user_intent("retorne os ultimos emails nao lidos do outlook")
    assert result.accepted
    assert result.profile_id == "outlook_read_unread"


def test_outlook_latest_not_unread():
    result = classify_user_intent("listar os ultimos emails do outlook")
    assert result.accepted
    assert result.profile_id == "outlook_read_latest"


def test_outlook_export_with_file_intent():
    result = classify_user_intent(
        "pegue 3 emails do outlook e salve num arquivo na pasta downloads"
    )
    assert result.accepted
    assert result.profile_id == "outlook_export_emails_file"


def test_filesystem_list_downloads():
    result = classify_user_intent("listar arquivos na pasta downloads")
    assert result.accepted
    assert result.profile_id == "filesystem_list_downloads"


def test_filesystem_write_without_outlook():
    result = classify_user_intent("escrever um arquivo de texto na pasta downloads")
    assert result.accepted
    assert result.profile_id == "filesystem_write_downloads"


def test_driver_install_printer():
    result = classify_user_intent("instalar driver da impressora hp")
    assert result.accepted
    assert result.profile_id == "driver_install_printer"


def test_shell_list_processes():
    result = classify_user_intent("listar processos em execucao no windows")
    assert result.accepted
    assert result.profile_id == "shell_list_processes"


def test_vague_text_stays_below_threshold():
    result = classify_user_intent("faz alguma coisa no computador")
    assert not result.accepted


def test_build_resolved_tool_arguments_outlook():
    profile = IntentProfileRegistry.default().require("outlook_read_unread")
    args = build_resolved_tool_arguments(profile)
    assert args["action"] == "outlook_read_latest"
    assert args["unread_only"] is True


def test_custom_profile_scoring(tmp_path):
    registry = IntentProfileRegistry()
    registry.register(
        profile_from_dict(
            {
                "id": "wiki_fetch",
                "domain": "web",
                "required_tools": ["web"],
                "default_action": {"tool": "web", "action": "fetch_readonly", "params": {}},
                "param_defaults": {},
                "confidence_threshold": 0.6,
                "signals": {
                    "require_any": ["wikipedia", "wiki"],
                    "prefer_any": ["ler", "read", "resumo"],
                },
            }
        )
    )
    hit = IntentClassifier(registry).classify("ler artigo da wikipedia sobre python")
    assert hit.accepted
    assert hit.profile_id == "wiki_fetch"
    miss = IntentClassifier(registry).classify("ola")
    assert not miss.accepted


def test_exclude_any_blocks_profile():
    profile = IntentProfileRegistry.load().require("outlook_read_unread")
    blocked = score_profile("outlook emails salvar em arquivo", profile)
    assert blocked.disqualified
    assert blocked.disqualify_reason == "exclude_any"
