# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from tool_selector import select_tools_for_request


def _tool(name: str, desc: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": parameters
            or {
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
            },
        },
    }


def _fake_tools() -> List[Dict[str, Any]]:
    office_params = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "open_document",
                    "outlook_read_latest",
                    "outlook_search_messages",
                    "word_create_document",
                    "draft_email_with_attachment",
                ],
            },
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    }
    return [
        _tool("shell", "run powershell commands"),
        _tool("desktop", "windows desktop control"),
        _tool("memory", "world model memory"),
        _tool("windows_ui", "ui automation"),
        _tool("skill", "discover skills"),
        _tool("request_user_input", "ask user"),
        _tool("web", "search the web"),
        _tool("office", "word excel outlook", office_params),
        _tool("driver_manager", "printer drivers"),
        _tool("sandbox", "run python scripts"),
        _tool("artifact", "analyze files"),
        _tool("share_discovery", "network shares"),
        _tool("document_intelligence", "document search"),
        _tool("filesystem", "read write files"),
        _tool("browser", "browser automation"),
        _tool("dynamic_tool", "custom tools"),
        _tool("subagent", "parallel branches"),
        _tool("world", "world model"),
        _tool("quality_dashboard", "metrics"),
    ]


def test_greeting_still_includes_core_tools_not_empty():
    tools = _fake_tools()
    selected = select_tools_for_request(tools, "ola")
    names = {(t["function"]["name"]) for t in selected}
    assert "shell" in names
    assert "memory" in names
    assert len(selected) < len(tools)


def test_action_request_includes_relevant_tools():
    tools = _fake_tools()
    selected = select_tools_for_request(tools, "instale o driver da impressora hp")
    names = {(t["function"]["name"]) for t in selected}
    assert "driver_manager" in names or "shell" in names


def test_small_catalog_unchanged():
    tools = _fake_tools()[:5]
    selected = select_tools_for_request(tools, "ola")
    assert len(selected) == 5


def test_outlook_read_prefers_office_over_shell():
    tools = _fake_tools()
    msg = "retornar meus ultimos emails do outlook nao visualizados"
    selected = select_tools_for_request(tools, msg)
    names = {(t["function"]["name"]) for t in selected}
    assert "office" in names
    assert "shell" not in names


def test_outlook_request_includes_office_and_filesystem():
    tools = _fake_tools()
    msg = "gostaria de um arquivo com meus ultimos 3 emails do outlook na pasta downloads"
    selected = select_tools_for_request(tools, msg)
    names = {(t["function"]["name"]) for t in selected}
    assert "office" in names
    assert "filesystem" in names

